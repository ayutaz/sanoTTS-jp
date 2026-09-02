/* saan_audio.h の DevKit 実装 — ESP-IDF の driver/i2s_std を直叩き。
 *
 * float → int16 と checksum は saan_pcm.c（唯一の実装）。ここは I2S に流すだけ。 */
#include "saan_audio.h"
#include "saan_pcm.h"

#include <inttypes.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
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

/* I2S ペリフェラルへの書き込みだけを外す（**変換は通す**）。
 * ⚠️ **QEMU 専用の逃げ道。既定 0。** 1 にすると音は一切出ない。
 * QEMU の esp32s3 は I2S DMA を捌かず `i2s_channel_write` が返らないため、
 * これが無いと合成ループを 1 回も回せない（実測）。 */
#ifndef SAAN_SKIP_I2S
#define SAAN_SKIP_I2S 0
#endif

/* DMA バッファ。16 bit mono なので 1 バッファ = dma_frame_num * 2 B。
 * 6 × 512 = 3,072 フレーム ≒ 139 ms ぶん（1 バッファ 1,024 B は上限 4,092 B 以内）。
 *
 * ⚠️ **これでスループット不足は埋まらない。** 実機（CoreS3 の報告値）は W8A8+PIE でも
 *    1 チャンク（音声 92.88 ms）に 144 ms かかる。DMA を何段積んでも足りない。
 *    直す順序は docs/research/s1-m5-cores3-speed.md §5。 */
#define SAAN_I2S_DMA_DESC  6
#define SAAN_I2S_DMA_FRAME 512

static i2s_chan_handle_t s_tx;

/* 変換バッファとプリロール。**static にしてスタックから外す**
 * （FreeRTOS タスクのスタックに置くと saan_irfft_1024 の自動変数 4,128 B と
 * 合わせてすぐ溢れる。M-42 で arena だけ見て見落としたのと同じ間違いを
 * タスクスタックで繰り返さない） */
#define SAAN_I2S_MAXBUF 2048
static int16_t s_i16[SAAN_I2S_MAXBUF];
static int16_t s_preroll_static[SAAN_AUDIO_PREROLL_SAMPLES];

/* begin_utterance で決まる貯め先。静的バッファか、超えるときだけヒープ。 */
static int16_t *s_preroll;
static int16_t *s_preroll_heap;    /* ヒープから取ったときだけ非 NULL（stop で返す） */
static size_t   s_preroll_cap;
static size_t   s_preroll_fill;

bool saan_audio_setup(uint32_t sample_rate) {
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
             (int)SAAN_AUDIO_PREROLL_SAMPLES);
    ESP_LOGW(TAG, "S3 に APLL は無い。実サンプルレートの誤差は**未測定**");
    return true;
}

bool saan_audio_begin_utterance(size_t n_samples) {
    if (n_samples == 0) { ESP_LOGE(TAG, "0 sample の発話"); return false; }
    if (s_preroll != NULL) {
        /* stop() を呼ばずに次の発話に来た。前の再生が終わっているとは限らない */
        ESP_LOGW(TAG, "前の発話のバッファが残っている。stop してから続ける");
        saan_audio_stop();
    }
    if (n_samples <= (size_t)SAAN_AUDIO_PREROLL_SAMPLES) {
        s_preroll = s_preroll_static;
    } else {
        /* 貯めてから鳴らす方式。1 秒 44,100 B。350 ids の上限では数百 KB になるので
         * 内部 DRAM には入らないことがある。**取れなければ false で止める**（切り詰めない） */
        const size_t nb = n_samples * sizeof(int16_t);
        void *p = heap_caps_malloc(nb, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (p) {
            ESP_LOGI(TAG, "発話バッファ %u B を PSRAM に確保", (unsigned)nb);
        } else {
            p = heap_caps_malloc(nb, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
            if (!p) {
                ESP_LOGE(TAG, "発話バッファ %u B を確保できない（PSRAM も内部 DRAM も）。"
                              "-DSAAN_BUFFERED=0（ストリーミング）にするか文を短くすること",
                         (unsigned)nb);
                return false;
            }
            ESP_LOGW(TAG, "発話バッファ %u B を**内部 DRAM**から確保した（PSRAM が無い構成）",
                     (unsigned)nb);
        }
        s_preroll_heap = (int16_t *)p;
        s_preroll = s_preroll_heap;
    }
    s_preroll_cap  = n_samples;
    s_preroll_fill = 0;
    return true;
}

bool saan_audio_preroll_push(const float *pcm, size_t n_samples) {
    if (s_preroll == NULL) {
        ESP_LOGE(TAG, "saan_audio_begin_utterance が済んでいない");
        return false;
    }
    if (s_preroll_fill + n_samples > s_preroll_cap) return false;
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
 * `saan_pcm_checksum()` はホストと突き合わせられる。 */
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

/* 貯めたぶんを送出する。I2S の 1 回の書き込みは SAAN_I2S_MAXBUF ずつに割る
 * （貯めてから鳴らす方式では数十万 sample になる） */
static bool flush_preroll(void) {
    size_t off = 0;
    while (off < s_preroll_fill) {
        size_t n = s_preroll_fill - off;
        if (n > (size_t)SAAN_I2S_MAXBUF) n = SAAN_I2S_MAXBUF;
        if (!write_i16(s_preroll + off, n)) return false;
        off += n;
    }
    s_preroll_fill = 0;
    return true;
}

bool saan_audio_start(void) {
#if SAAN_SKIP_I2S
    ESP_LOGW(TAG, "SAAN_SKIP_I2S: I2S を鳴らさない（QEMU 用。音は出ない）");
    return flush_preroll();
#else
    esp_err_t err = i2s_channel_enable(s_tx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_enable: %s", esp_err_to_name(err));
        return false;
    }
    if (s_preroll_fill > 0)
        ESP_LOGI(TAG, "貯めた %u sample (%.3f s) を送出", (unsigned)s_preroll_fill,
                 (double)s_preroll_fill / 22050.0);
    return flush_preroll();
#endif
}

void saan_audio_stop(void) {
#if !SAAN_SKIP_I2S
    /* ⚠️ i2s_channel_write は DMA に渡し終えた時点で返る。最後の DMA バッファ
     *    （139 ms ぶん）が鳴り切るまで待ってから disable する。待たないと語尾が切れる */
    if (s_tx) {
        vTaskDelay(pdMS_TO_TICKS(SAAN_I2S_DMA_DESC * SAAN_I2S_DMA_FRAME * 1000 / 22050 + 10));
        i2s_channel_disable(s_tx);
    }
#endif
    if (s_preroll_heap) {
        heap_caps_free(s_preroll_heap);
        s_preroll_heap = NULL;
    }
    s_preroll = NULL;
    s_preroll_cap = 0;
    s_preroll_fill = 0;
}

bool saan_audio_write_f32(const float *pcm, size_t n_samples) {
    while (n_samples > 0) {
        size_t n = n_samples > SAAN_I2S_MAXBUF ? SAAN_I2S_MAXBUF : n_samples;
        for (size_t i = 0; i < n; ++i) s_i16[i] = saan_f32_to_i16(pcm[i]);
        if (!write_i16(s_i16, n)) return false;
        pcm += n; n_samples -= n;
    }
    return true;
}
