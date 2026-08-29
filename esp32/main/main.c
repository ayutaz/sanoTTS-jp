/* sanoTTS-jp — ESP32-S3 の雛形（c'-4）
 *
 * ⚠️⚠️ **実機では一度も動かしていない。** ⚠️⚠️
 *    ビルドは通る（2026-08-28 / ESP-IDF v5.5 / M-54。`saanotts_jp.bin` 267,968 B）。
 *    QEMU も導入済みだが**サイクル精度ではないので速度は測れない**。
 *    ⚠️ **この雛形は PIE を有効にしていない**（`components/saanotts_core/CMakeLists.txt`
 *    が `SAAN_PIE` / `SAAN_INT8_ACT` を定義していない）。W8A8 を採る決定が要る。
 *    ビルドできるようになる前に確認していたのは
 *      (1) CMake の構文、
 *      (2) esp32/host_stub の偽ヘッダを噛ませたホストビルドと、I2S に書いたはずの
 *          int16 が **csrc の一括版 saan_synthesize の出力と bit 完全一致**すること
 *          （fp32 blob / int8 blob の両方で 27,136 sample。
 *           ⚠️ Python 参照の golden.bin とは bit 一致しない — fp32 で max 1 LSB、
 *           int8 は量子化ぶん違う。判定基準が違うので README を読むこと）、
 *      (3) csrc のコアにホスト専用 API（malloc / fopen / mmap …）が無いこと、
 *      (4) arena のサイズと「足りないときの壊れ方」、
 *      (5) `saan_g2p()` が中間表現 44 B から demo_ids.h の錨 53 ids を再現すること
 *          （錨を 1 要素・中間表現を 1 文字だけ変えると exit 1 で落ちることも確認）
 *    の 5 つだけ。**idf.py build / flash / 実 SRAM / 実レイテンシは全部未検証。**
 *    詳細は esp32/README.md。
 *
 * 流れ:
 *   flash の model パーティションを mmap
 *     → **中間表現の文字列を saan_g2p() で生徒インデックス列に変換**
 *     → 静的 arena で saan_stream_init
 *     → 数チャンク先に計算してプリロール（初回 pull だけ約 6 倍重いため）
 *     → I2S を enable
 *     → pull → int16 → i2s_channel_write を繰り返す
 *
 * ⚠️ **「雛形が動いた = 実時間で喋れた」ではない。** M-43 の外挿では
 *    移植可能 C / fp32 は 2.47 × RT で、音声 92.88 ms を作るのに約 229 ms かかる。
 *    **アンダーランが出るのが期待どおり**。この雛形の役目は
 *    「それを正しく観測してログに出すこと」。
 */
#include <inttypes.h>
#include <stdint.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "saanotts.h"
#include "saanotts_stream.h"

#include "g2p.h"

#include "demo_ids.h"
#include "saan_i2s.h"
#include "saan_model.h"

static const char *TAG = "saanotts";

/* --- arena ---------------------------------------------------------------
 *
 * ⚠️ **`saan_stream_arena_needed()` の戻り値を使わないこと。** あれは緩い上限で、
 *    n_ids=350 に対し **340,016 B (332 KB)** を返す。512 KB の SRAM に対して
 *    65% を占め、IDF / FreeRTOS / I2S DMA と合わせて破綻する。
 *
 * ⚠️ **CLAUDE.md / M-42 の「197 KB」もそのまま確保量にしないこと。** あれは
 *    高水位（`st.peak_used` = 197,424 B）で、**init が通る最小 arena は
 *    197,632 B**。ALIGN16 の切り上げと確保順の差でわずかに上回る。
 *
 * 208 KB (212,992 B) の根拠は実測（`make -C csrc arena`、ホスト）:
 *   - n_ids=350（D-017 の max_spec_length=700 相当）の最小 arena  197,632 B
 *   - 208 KB 固定で n_ids 1〜520 は init も pull も成功、560 以上は
 *     SAAN_ERR_ARENA で**きれいに失敗**（n_ids 1〜1000 の 23 点でクラッシュ 0）
 *   - 設計上限 350 ids に対し 15,360 B の余裕、かつ 520 ids まで伸びる
 */
#define SAAN_ARENA_BYTES (208 * 1024)

/* ⚠️ **黙って確保に失敗したのを検出するための下限。**
 *
 * `saan_alloc` は失敗しても `used` を進めずに NULL を返す。`saan_stream_init` は
 * 25 回の確保のうち各グループの**最後の 1 個しか NULL 検査していない**ので、
 * 途中の大きい確保だけが落ちると **init が SAAN_OK を返したまま壊れた状態**になり、
 * その後 `saan_stream_pull` の中で NULL 書き込みになる。
 * 手元では SEGV、ESP32 では StoreProhibited パニック = **ログも出ずに再起動**。
 *
 * 実測（`make -C csrc arena` / /tmp の probe、n_ids=350）:
 *   正しく init できたときの `a.used`  194,640 B (n_ids=1) 〜 198,768 B (n_ids=520)
 *   黙って失敗したときの `a.used`      178,992 / 185,136 / 191,280 B（最大 191,280）
 * → 191,280 < 閾値 <= 194,640 なら誤検知も見逃しも無い。中点を採る。
 *
 * ⚠️ **arena を 208 KB にしていればそもそも踏まない**（クラッシュ帯は 175〜191 KB）。
 *    これは二重防御。**コアの確保順が変わったら再測すること** —
 *    `make -C csrc arena` が正しい値を出す。 */
#define SAAN_ARENA_USED_FLOOR 192960u

/* .bss に静的確保する。**malloc しない**（断片化させない・失敗しない）。
 * 16 バイト境界にそろえるのは将来の PIE
 * （SOC_SIMD_PREFERRED_DATA_ALIGNMENT = 16）のため。 */
static __attribute__((aligned(16))) uint8_t g_arena[SAAN_ARENA_BYTES];

/* ⚠️ **スタックに置かない。** 8,192 B ある。
 * `saan_irfft_1024` は自動変数 zr[512]+zi[512] (float) だけで **4,096 B** 使う
 * （arm64 clang -O2 の実フレームは 4,224 B。otool -tv で実測。
 * ⚠️ Xtensa では別の値になる）。IDF の小さい既定スタックでは足りない。 */
static float g_chunk[SAAN_CHUNK * SAAN_HOP];

/* --- 端末側 G2P ----------------------------------------------------------
 *
 * 入力は**かな中間表現**（`SAAN_DEMO_INTERMEDIATE`）。漢字は端末で扱わない
 * （D-010 / D-011）。表は csrc/g2p_table.h に 913 B。
 *
 * ⚠️ **`saan_g2p_capacity()` と同じ式を使う。** 上限は `2 * バイト数 + 3`。
 *    足りないと SAAN_G2P_ERR_OVERFLOW で**きれいに失敗する**（黙って切り詰めない）。 */
#define SAAN_G2P_IDS_CAP (2 * SAAN_DEMO_INTERMEDIATE_BYTES + 3)
static int32_t g_ids[SAAN_G2P_IDS_CAP];

/* C99 には _Static_assert が無いので配列サイズで潰す（IDF は gnu17 だが csrc に合わせる） */
typedef char saan_g2p_cap_check[(SAAN_G2P_IDS_CAP >= SAAN_DEMO_N_IDS) ? 1 : -1];

/* 合成タスクのスタック。saan_irfft_1024 の 4 KB + 呼び出し段 + ログで、
 * IDF の小さい既定スタックでは足りない。**16 KB を明示する**。
 * ⚠️ 実機で `uxTaskGetStackHighWaterMark` を見て詰めること（未測定）。 */
#define SAAN_TASK_STACK 16384
#define SAAN_TASK_PRIO  5

/* プリロールするチャンク数。1 チャンク = 2,048 sample = 92.88 ms */
#define SAAN_PREROLL_CHUNKS (SAAN_I2S_PREROLL_SAMPLES / (SAAN_CHUNK * SAAN_HOP))

static void log_heap(const char *when) {
    ESP_LOGI(TAG, "%s: 内部 DRAM free %u B / 最大ブロック %u B", when,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
}

static void tts_task(void *arg) {
    (void)arg;
    log_heap("起動直後");
    ESP_LOGI(TAG, "arena %d B を .bss に静的確保 (%p) / G2P の ids %d B",
             (int)SAAN_ARENA_BYTES, (void *)g_arena, (int)sizeof g_ids);

    saan_weights w;
    if (!saan_model_open(&w)) { vTaskDelete(NULL); return; }

#if SAAN_INT8_ACT
    /* ⚠️ **W8A8/PIE を有効にしても、blob が fp32 なら 1 命令も効かない。**
     *    `saan_conv1d_w` は `W.f32` があればそこで return するので、
     *    **速度が変わらないのに理由が分からない**という最悪の壊れ方をする。
     *    int8 blob だけが `<name>.scale` を持つ（fp32 blob は 0 個）ので、それで判る。 */
    {
        uint32_t dt = 0, d[4] = {0};
        uint64_t nb = 0;
        if (!saan_tensor(&w, "duration.blocks.0.c1.weight.scale", &dt, d, &nb)) {
            ESP_LOGE(TAG, "W8A8/PIE 有効でビルドしたのに **fp32 blob** が焼かれている。"
                          "この構成では PIE は 1 命令も効かない。"
                          "int8 blob を焼くこと: -DSAAN_MODEL_BLOB=<...>/student_i8.bin");
            vTaskDelete(NULL); return;
        }
        ESP_LOGI(TAG, "W8A8 + PIE 有効 / int8 blob を確認");
    }
#endif

    if (!saan_i2s_setup(SAAN_SR)) { vTaskDelete(NULL); return; }

    /* --- 端末側 G2P --------------------------------------------------------
     *
     * ⚠️ **kSaanDemoIds は入力ではなく答え合わせの錨。** 合成に使うのは
     *    saan_g2p() が今その場で作った g_ids の方。錨と食い違ったら
     *    **走らせない** — この雛形の目的は「音が出た」ではなく
     *    「Python と同じ列になっている」ことの確認なので、ずれたまま
     *    それらしい音を出すのが一番悪い（未知語が無音で消えるのと同じ壊れ方）。 */
    /* ⚠️ **上の #define は saan_g2p_capacity() の式を写したもの**（配列サイズには
     *    関数を書けない）。**2 か所にある式は必ずずれる**ので、実体と突き合わせる。 */
    if (SAAN_G2P_IDS_CAP < saan_g2p_capacity(SAAN_DEMO_INTERMEDIATE_BYTES)) {
        ESP_LOGE(TAG, "SAAN_G2P_IDS_CAP (%d) が saan_g2p_capacity() (%d) より小さい。"
                      "main.c の式が csrc/g2p.c とずれている",
                 (int)SAAN_G2P_IDS_CAP,
                 (int)saan_g2p_capacity(SAAN_DEMO_INTERMEDIATE_BYTES));
        vTaskDelete(NULL); return;
    }

    int32_t n_ids = 0;
    saan_g2p_info gi;
    int64_t t_g2p = esp_timer_get_time();
    saan_g2p_status gs = saan_g2p(SAAN_DEMO_INTERMEDIATE, SAAN_DEMO_INTERMEDIATE_BYTES,
                                  g_ids, SAAN_G2P_IDS_CAP, &n_ids, &gi);
    t_g2p = esp_timer_get_time() - t_g2p;
    if (gs != SAAN_G2P_OK) {
        ESP_LOGE(TAG, "saan_g2p: %s (err_byte=%d)", saan_g2p_strerror(gs), (int)gi.err_byte);
        vTaskDelete(NULL); return;
    }
    ESP_LOGI(TAG, "G2P: \"%s\" (%d B) -> %d ids / %.3f ms",
             SAAN_DEMO_INTERMEDIATE, (int)SAAN_DEMO_INTERMEDIATE_BYTES,
             (int)n_ids, (double)t_g2p / 1000.0);
    ESP_LOGI(TAG, "     音素 %d 個（うち PAD %d）/ 黙って落ちた ー %d ・ ° %d",
             (int)gi.n_phonemes, (int)gi.n_pad_phonemes,
             (int)gi.n_dropped_long, (int)gi.n_dropped_devoice);
    if (n_ids != SAAN_DEMO_N_IDS
        || memcmp(g_ids, kSaanDemoIds, sizeof kSaanDemoIds) != 0) {
        ESP_LOGE(TAG, "G2P の出力が demo_ids.h の錨と一致しない（%d ids / 期待 %d）。"
                      "**テーブルか実装がずれている** — "
                      "`make -C csrc g2p` と `uv run python scripts/gen_g2p_tables.py` を見ること",
                 (int)n_ids, (int)SAAN_DEMO_N_IDS);
        vTaskDelete(NULL); return;
    }
    ESP_LOGI(TAG, "     OK  %d ids が demo_ids.h の錨と完全一致", (int)n_ids);

    saan_arena a;
    saan_arena_init(&a, g_arena, sizeof g_arena);

    saan_stream st;
    int64_t t_init = esp_timer_get_time();
    saan_status s = saan_stream_init(&st, &w, &a, g_ids, n_ids, SAAN_S_V);
    t_init = esp_timer_get_time() - t_init;
    if (s != SAAN_OK) {
        ESP_LOGE(TAG, "saan_stream_init: %s", saan_strerror(s));
        vTaskDelete(NULL); return;
    }

    /* ⚠️ 上に書いた二重防御。init が OK でも黙って確保に失敗していることがある */
    if (a.used < SAAN_ARENA_USED_FLOOR) {
        ESP_LOGE(TAG, "saan_stream_init は OK を返したが a.used が %u B しかない "
                      "(下限 %u B)。**確保が黙って失敗している** — "
                      "このまま pull すると NULL 書き込みで再起動する",
                 (unsigned)a.used, (unsigned)SAAN_ARENA_USED_FLOOR);
        ESP_LOGE(TAG, "arena を増やすか、csrc の確保順が変わったなら "
                      "`make -C csrc arena` で下限を測り直すこと");
        vTaskDelete(NULL); return;
    }

    const double audio_s = (double)st.n_frames * SAAN_HOP / SAAN_SR;
    ESP_LOGI(TAG, "入力: \"%s\" (%d ids)", SAAN_DEMO_TEXT, (int)n_ids);
    ESP_LOGI(TAG, "init %.2f ms / %d frames / %d sample / 音声 %.3f s",
             (double)t_init / 1000.0, (int)st.n_frames,
             (int)st.n_frames * SAAN_HOP, audio_s);
    ESP_LOGI(TAG, "arena used %u B / peak %u B / 確保 %u B",
             (unsigned)a.used, (unsigned)a.peak, (unsigned)sizeof g_arena);
    ESP_LOGI(TAG, "算法遅延 %d frames = %.3f s（受容野 %d + iSTFT 2）",
             SAAN_LATENCY + 2, (double)(SAAN_LATENCY + 2) * SAAN_HOP / SAAN_SR,
             SAAN_LATENCY);
    log_heap("init 後");

    /* --- プリロール ------------------------------------------------------
     * ⚠️ **I2S を enable する前に数チャンク計算しておく。** 最初の pull だけ
     * 定常の約 6 倍かかる（ホスト実測 12.2 ms vs 2.04 ms）。受容野 38 フレームの
     * warmup で内部の step_chunk が複数回走るため。 */
    int32_t n = 0;
    int chunks = 0;
    int64_t t_first = 0, t_rest = 0;
    int64_t audio_us_done = 0;
    int32_t total_frames = 0;
    int underruns = 0, short_pulls = 0;
    bool eos = false;

    for (int i = 0; i < SAAN_PREROLL_CHUNKS && !eos; ++i) {
        int64_t t0 = esp_timer_get_time();
        s = saan_stream_pull(&st, g_chunk, &n);
        int64_t dt = esp_timer_get_time() - t0;
        if (s != SAAN_OK) { ESP_LOGE(TAG, "pull: %s", saan_strerror(s)); goto done; }
        if (n <= 0) { eos = true; break; }
        if (chunks == 0) t_first = dt; else t_rest += dt;
        /* ⚠️ `n` は**フレーム数**。サンプル数は n * SAAN_HOP */
        if (!saan_i2s_preroll_push(g_chunk, (size_t)n * SAAN_HOP)) {
            ESP_LOGW(TAG, "プリロールが一杯（%d チャンクで打ち切り）", chunks);
            /* 貯めきれなかったぶんは start 後に普通に書く。ここでは捨てない */
            ESP_LOGE(TAG, "プリロール容量の計算が合っていない。"
                          "SAAN_I2S_PREROLL_SAMPLES を見直すこと");
            goto done;
        }
        total_frames += n; ++chunks;
        if (n < SAAN_CHUNK) ++short_pulls;
    }
    ESP_LOGI(TAG, "プリロール %d チャンク完了（初回 pull %.2f ms）",
             chunks, (double)t_first / 1000.0);

    if (!saan_i2s_start()) goto done;
    audio_us_done = (int64_t)total_frames * SAAN_HOP * 1000000 / SAAN_SR;

    /* --- 定常ループ ------------------------------------------------------ */
    while (!eos) {
        int64_t t0 = esp_timer_get_time();
        s = saan_stream_pull(&st, g_chunk, &n);
        int64_t dt = esp_timer_get_time() - t0;
        if (s != SAAN_OK) { ESP_LOGE(TAG, "pull: %s", saan_strerror(s)); break; }
        if (n <= 0) break;

        /* ⚠️ **これが実機で最初に見るべき数値。** 1 チャンクの計算に、
         * そのチャンクが表す音声より長くかかったらアンダーラン。 */
        int64_t budget_us = (int64_t)n * SAAN_HOP * 1000000 / SAAN_SR;
        if (dt > budget_us) ++underruns;

        if (chunks == 0) t_first = dt; else t_rest += dt;
        if (n < SAAN_CHUNK) ++short_pulls;
        total_frames += n; ++chunks;

        if (!saan_i2s_write_f32(g_chunk, (size_t)n * SAAN_HOP)) break;
    }

done:
    saan_i2s_stop();
    {
        double total_audio = (double)total_frames * SAAN_HOP / SAAN_SR;
        double mean_rest = chunks > 1 ? (double)t_rest / (chunks - 1) / 1000.0 : 0.0;
        double chunk_ms = (double)SAAN_CHUNK * SAAN_HOP * 1000.0 / SAAN_SR;
        ESP_LOGI(TAG, "----- 結果 -----");
        ESP_LOGI(TAG, "pull %d 回 / %d frames / 音声 %.3f s（端数チャンク %d 回）",
                 chunks, (int)total_frames, total_audio, short_pulls);
        ESP_LOGI(TAG, "初回 pull %.2f ms / 2 回目以降 mean %.2f ms "
                      "(満チャンク 1 個 = %.2f ms の音声)",
                 (double)t_first / 1000.0, mean_rest, chunk_ms);
        ESP_LOGI(TAG, "定常 xRT = %.3f  ← **1.0 を超えたら実時間に間に合っていない**",
                 chunk_ms > 0 ? mean_rest / chunk_ms : 0.0);
        ESP_LOGI(TAG, "アンダーラン %d / %d チャンク", underruns, chunks);
        ESP_LOGI(TAG, "int16 クリップ %u sample", (unsigned)saan_i2s_clip_count());
        /* ⚠️ **移植が正しいことの唯一の機械的な証拠。** 「音が鳴った」ではなく
         *    この 2 つがホスト（esp32/host_stub）と一致するかで判定する。 */
        ESP_LOGI(TAG, "出力 PCM: %u sample / FNV-1a 0x%016llx",
                 (unsigned)saan_i2s_pcm_samples(),
                 (unsigned long long)saan_i2s_pcm_checksum());
        /* ⚠️ **checksum が違っても、ここが合っていれば丸め差。**
         *    ホストとターゲットで bit 一致は**期待できない**（float の丸めが違う）。
         *    bit 一致を主張してよいのは**同じターゲット上の 2 構成**を比べたときだけ。 */
        ESP_LOGI(TAG, "        |max| %d / Σx² %llu",
                 (int)saan_i2s_pcm_absmax(),
                 (unsigned long long)saan_i2s_pcm_sqsum());
        ESP_LOGI(TAG, "タスクスタック残り %u B（%d B 中）",
                 (unsigned)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
                 (int)SAAN_TASK_STACK);
        log_heap("終了時");
        if (underruns > 0)
            ESP_LOGW(TAG, "アンダーランは**想定どおり**。M-43 の外挿では "
                          "移植可能 C / fp32 は 2.47 x RT。int8 + PIE が要る");
        (void)audio_us_done;
    }
    vTaskDelete(NULL);
}

void app_main(void) {
    ESP_LOGI(TAG, "sanoTTS-jp ESP32-S3 雛形（**未検証** — esp32/README.md を読むこと）");
    xTaskCreate(tts_task, "saan_tts", SAAN_TASK_STACK, NULL, SAAN_TASK_PRIO, NULL);
}
