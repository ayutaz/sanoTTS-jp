#include "saan_i2s.h"

#include <inttypes.h>
#include <math.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "saan_i2s";

/* ⚠️ **自分のボードの配線に合わせて変えること。** 既定値は根拠のない仮置き。
 *    多くの I2S DAC ボード（MAX98357A / PCM5102 など）は BCLK / WS / DOUT の
 *    3 本で足りる。MCLK は使わない。 */
#ifndef SAAN_I2S_GPIO_BCLK
#define SAAN_I2S_GPIO_BCLK 5
#endif
#ifndef SAAN_I2S_GPIO_WS
#define SAAN_I2S_GPIO_WS 6
#endif
#ifndef SAAN_I2S_GPIO_DOUT
#define SAAN_I2S_GPIO_DOUT 7
#endif

/* DMA バッファ。16 bit mono なので 1 バッファ = dma_frame_num * 2 B。
 * 6 × 512 = 3,072 フレーム ≒ 139 ms ぶん（1 バッファ 1,024 B は上限 4,092 B 以内）。
 *
 * ⚠️ **これでスループット不足は埋まらない。** M-43 の外挿では移植可能 C /
 *    fp32 は 2.47 × RT で、1 チャンク（音声 92.88 ms）の計算に約 229 ms かかる。
 *    DMA を何段積んでも足りない。int8 + PIE が要る、が M-43 の結論。 */
#define SAAN_I2S_DMA_DESC  6
#define SAAN_I2S_DMA_FRAME 512

static i2s_chan_handle_t s_tx;
static uint32_t s_clips;

/* 変換バッファとプリロール。**static にしてスタックから外す**
 * （FreeRTOS タスクのスタックに置くと saan_irfft_1024 の自動変数 4,128 B と
 * 合わせてすぐ溢れる。M-42 で arena だけ見て見落としたのと同じ間違いを
 * タスクスタックで繰り返さない） */
#define SAAN_I2S_MAXBUF 2048
static int16_t s_i16[SAAN_I2S_MAXBUF];
static int16_t s_preroll[SAAN_I2S_PREROLL_SAMPLES];
static size_t  s_preroll_fill;

/* 出力 PCM の FNV-1a（リトルエンディアンの 2 バイトを順に食う）。
 * ⚠️ ホストと**同じ順序・同じ幅**で食うこと。片方だけ変えると比較が無意味になる */
static uint64_t s_pcm_fnv = 1469598103934665603ull;
static uint32_t s_pcm_n;
/* ⚠️ **checksum だけでは「1 LSB 違い」と「全部でたらめ」を区別できない。**
 * ホストとターゲットで float の丸めが違えば checksum は必ず変わるので、
 * **大きさも併せて出す**（これが一致していれば丸め差、外れていればバグ）。 */
static int32_t  s_pcm_absmax;
static uint64_t s_pcm_sqsum;

int16_t saan_f32_to_i16(float x) {
    long v = lrintf(x * 32767.0f);
    if (v > 32767) { v = 32767; ++s_clips; }
    else if (v < -32768) { v = -32768; ++s_clips; }
    uint16_t u = (uint16_t)(int16_t)v;
    s_pcm_fnv = (s_pcm_fnv ^ (uint8_t)(u & 0xff)) * 1099511628211ull;
    s_pcm_fnv = (s_pcm_fnv ^ (uint8_t)(u >> 8)) * 1099511628211ull;
    { int32_t av = (int32_t)(v < 0 ? -v : v);
      if (av > s_pcm_absmax) s_pcm_absmax = av;
      s_pcm_sqsum += (uint64_t)((int64_t)v * (int64_t)v); }
    ++s_pcm_n;
    return (int16_t)v;
}

void saan_i2s_pcm_reset(void) {
    s_pcm_fnv = 1469598103934665603ull;   /* FNV-1a 64 bit のオフセット基底 */
    s_pcm_n = 0;
    s_pcm_absmax = 0;
    s_pcm_sqsum = 0;
    s_clips = 0;
    s_preroll_fill = 0;   /* ⚠️ 前の発話の残りを次に混ぜない */
}

uint32_t saan_i2s_clip_count(void) { return s_clips; }
uint64_t saan_i2s_pcm_checksum(void) { return s_pcm_fnv; }
uint32_t saan_i2s_pcm_samples(void) { return s_pcm_n; }
int32_t  saan_i2s_pcm_absmax(void) { return s_pcm_absmax; }
uint64_t saan_i2s_pcm_sqsum(void) { return s_pcm_sqsum; }

bool saan_i2s_setup(uint32_t sample_rate) {
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    cc.dma_desc_num  = SAAN_I2S_DMA_DESC;
    cc.dma_frame_num = SAAN_I2S_DMA_FRAME;
    cc.auto_clear    = true;   /* アンダーラン時に前のデータを繰り返さない */

    esp_err_t err = i2s_new_channel(&cc, &s_tx, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_new_channel: %s", esp_err_to_name(err));
        return false;
    }

    i2s_std_config_t sc = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(sample_rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SAAN_I2S_GPIO_BCLK,
            .ws   = SAAN_I2S_GPIO_WS,
            .dout = SAAN_I2S_GPIO_DOUT,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    err = i2s_channel_init_std_mode(s_tx, &sc);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_init_std_mode: %s", esp_err_to_name(err));
        return false;
    }
    ESP_LOGI(TAG, "I2S 設定 %" PRIu32 " Hz / 16bit / mono / DMA %d x %d frames / "
                  "preroll %d sample",
             sample_rate, SAAN_I2S_DMA_DESC, SAAN_I2S_DMA_FRAME,
             (int)SAAN_I2S_PREROLL_SAMPLES);
    ESP_LOGW(TAG, "S3 に APLL は無い。実サンプルレートの誤差は**未測定**");
    return true;
}

bool saan_i2s_preroll_push(const float *pcm, size_t n_samples) {
    if (s_preroll_fill + n_samples > (size_t)SAAN_I2S_PREROLL_SAMPLES) return false;
    for (size_t i = 0; i < n_samples; ++i)
        s_preroll[s_preroll_fill + i] = saan_f32_to_i16(pcm[i]);
    s_preroll_fill += n_samples;
    return true;
}

#if SAAN_SKIP_I2S
/* ⚠️ **QEMU 用の逃げ道であって、実機の構成ではない。**
 * QEMU の esp32s3 マシンは I2S の DMA を捌かないので、`i2s_channel_write` が
 * 永久にブロックして合成ループへ入れない（実測: プリロール完了後に停止）。
 * ペリフェラルへの書き込みだけを外し、**float→int16 変換は必ず通す**ので
 * `saan_i2s_pcm_checksum()` はホストと突き合わせられる。 */
static bool write_i16(const int16_t *p, size_t n) { (void)p; (void)n; return true; }
#else
static bool write_i16(const int16_t *p, size_t n) {
    size_t wrote = 0;
    esp_err_t err = i2s_channel_write(s_tx, p, n * sizeof(int16_t), &wrote,
                                      portMAX_DELAY);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_write: %s", esp_err_to_name(err));
        return false;
    }
    if (wrote != n * sizeof(int16_t)) {
        ESP_LOGE(TAG, "i2s_channel_write が %u/%u B しか書かなかった",
                 (unsigned)wrote, (unsigned)(n * sizeof(int16_t)));
        return false;
    }
    return true;
}
#endif /* SAAN_SKIP_I2S */

bool saan_i2s_start(void) {
#if SAAN_SKIP_I2S
    ESP_LOGW(TAG, "SAAN_SKIP_I2S: I2S を鳴らさない（QEMU 用。音は出ない）");
    if (s_preroll_fill > 0) { write_i16(s_preroll, s_preroll_fill); s_preroll_fill = 0; }
    return true;
#else
    esp_err_t err = i2s_channel_enable(s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_enable: %s", esp_err_to_name(err));
        return false;
    }
    if (s_preroll_fill > 0) {
        ESP_LOGI(TAG, "プリロール %u sample を送出", (unsigned)s_preroll_fill);
        if (!write_i16(s_preroll, s_preroll_fill)) return false;
        s_preroll_fill = 0;
    }
    return true;
#endif
}

void saan_i2s_stop(void) {
#if !SAAN_SKIP_I2S
    if (s_tx) i2s_channel_disable(s_tx);
#endif
}

bool saan_i2s_write_f32(const float *pcm, size_t n_samples) {
    while (n_samples > 0) {
        size_t n = n_samples > SAAN_I2S_MAXBUF ? SAAN_I2S_MAXBUF : n_samples;
        for (size_t i = 0; i < n; ++i) s_i16[i] = saan_f32_to_i16(pcm[i]);
        if (!write_i16(s_i16, n)) return false;
        pcm += n; n_samples -= n;
    }
    return true;
}
