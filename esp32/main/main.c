/* sanoTTS-jp — ESP32-S3 のファーム（c'-4 + 対話入力）
 *
 * ⚠️⚠️ **実機では一度も動かしていない。速度は一度も測っていない。** ⚠️⚠️
 *    ビルドは通り（ESP-IDF v5.5 / M-54）、**QEMU で起動から合成完了まで
 *    通した**（M-62）。ただし QEMU は**サイクル精度ではない**ので速度は測れない。
 *    実機で未検証なのは: 実 SRAM / 実レイテンシ / I2S の実サンプルレート /
 *    D-cache と flash mmap の相互作用 / **UART からの対話入力**。
 *
 * 流れ:
 *   flash の model パーティションを mmap
 *     → **起動セルフテスト**: 組み込みのかな中間表現を saan_g2p() に通し、
 *        demo_ids.h の錨と ids が完全一致するか（**表と実装のずれの検出**）
 *     → その 1 文を合成（ホスト / QEMU と checksum を突き合わせるための基準）
 *     → **対話ループ**: シリアルから かな中間表現を 1 行読んで合成、を繰り返す
 *
 * 1 発話の中身:
 *   静的 arena で saan_stream_init
 *     → 数チャンク先に計算してプリロール（初回 pull だけ約 6 倍重いため）
 *     → saan_audio_start → pull → int16 → saan_audio_write_f32 を繰り返す
 *        （音声出力の実装は saan_audio.h の後ろ。DevKit の I2S か M5.Speaker か）
 *
 * ⚠️ **「喋った = 実時間で喋れた」ではない。** M-43 の外挿では
 *    移植可能 C / fp32 は 2.47 × RT で、音声 92.88 ms を作るのに約 229 ms かかる。
 *    **アンダーランが出るのが期待どおり**。このファームの役目は
 *    「それを正しく観測してログに出すこと」。
 */
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "saanotts.h"
#include "saanotts_stream.h"
#include "saan_prof.h"

#include "g2p.h"

#include "demo_ids.h"
#include "saan_audio.h"
#include "saan_console.h"
#include "saan_model.h"
#include "saan_pcm.h"
#include "saan_ui.h"

static const char *TAG = "saanotts";

#if SAAN_KANJI
#include "saan_dict.h"
#include "saan_kanji.h"
/* ⚠️ **辞書は flash に mmap したまま使う。** RAM には読まない（12 MB ある）。 */
static k1_dict_t g_dict;
static bool g_dict_ok;
/* ⚠️ **Viterbi は合成用の g_arena を borrow する。** G2P と合成は同時に
 *    走らないので、別に 192 KB 持つと DRAM が足りない（G19 で実測）。 */
#endif

/* --- 起動時に 1 文喋るか --------------------------------------------------
 *
 * **既定は「喋らない」。** 対話入力が入ったので、同じことは
 * `きょ][おわよ][いて][んきです°ね` と 1 行打てばできる。**しかも bit 単位で同じ列になる**
 * （M-63 の T1 で確認済み）。起動のたびに 1.2 秒の音声を作るのは、
 * 実機では 2.47 × RT の外挿でおよそ 3 秒の待ちになる。
 *
 * ⚠️ **ただし非対話ビルドでは唯一の出力経路。** ホスト stub
 * （`scripts/check_esp32_template.sh` のゲート 8）は `app_main()` を同期実行して
 * I2S に出た int16 を golden と突き合わせるので、ここで喋らないと比較対象が 0 sample になる。
 * **そのときゲートは「27136 sample vs 0 sample」で落ちる**（-DSAAN_BOOT_SPEAK=0 で実測）
 * ので空虚にはならないが、**ゲートが常に赤になって役に立たない**。
 * だから SAAN_INTERACTIVE=0 のときは既定で喋る。
 *
 * 実機でも「打たずに測りたい」ときは `-DSAAN_BOOT_SPEAK=1`
 * （コンソールに触れない環境・`idf.py qemu` をそのまま流す回帰確認など）。
 *
 * ⚠️ **錨との照合（`boot_selftest`）は喋るかどうかに関係なく必ず走る。**
 *    あれが「テーブルと実装がずれていない」唯一の機械的な証拠で、G2P だけで済む。 */
#ifndef SAAN_BOOT_SPEAK
#  if SAAN_INTERACTIVE
#    define SAAN_BOOT_SPEAK 0
#  else
#    define SAAN_BOOT_SPEAK 1
#  endif
#endif

/* --- 再生方式 -------------------------------------------------------------
 *
 *   0 = **ストリーミング**（既定）。プリロール後に計算しながら鳴らす。xRT が 1.0 を
 *       切らないと途切れる。**速度（xRT / アンダーラン）を測るのはこちら。**
 *   1 = 1 発話ぶんを全部計算して貯めてから鳴らす。**途切れない**が、発話開始までの
 *       待ちが合成時間そのもの（CoreS3 の報告値: 1.2 秒の文に約 2.7 秒）。
 *       貯め先は saan_audio_begin_utterance() が確保する（PSRAM 優先。取れなければ止まる）。
 *
 *     idf.py -DSAAN_BUFFERED=1 build */
#ifndef SAAN_BUFFERED
#define SAAN_BUFFERED 0
#endif

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
/* ⚠️ **漢字対応ビルドでは 204 KB に落とす。** PSRAM を有効にすると
 *    IDF 側が DRAM を数 KB 追加で使い、208 KB では **3,776 B 溢れる**（実測）。
 *    204 KB でも 350 ids に必要な 197,632 B に対し 11,264 B の余裕がある。 */
#if SAAN_KANJI
#define SAAN_ARENA_BYTES (204 * 1024)
#else
#define SAAN_ARENA_BYTES (208 * 1024)
#endif

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

/* 受け付ける ids の上限。**arena の限界 (520) ではなく学習分布の上限を採る。**
 *
 * ⚠️ **「入るか」と「まともに喋れるか」は別。** arena は 520 ids まで持つが、
 *    生徒が学習したのは D-017 の `max_spec_length=700`（= 350 ids 相当）までで、
 *    それを超える入力は**分布の外**になる。しかも clip[1,80] の上限に張り付くと
 *    350 ids で最大 28,000 frames = 325 秒の音声になりうる。
 *    **入力を拒否するほうが、分布外の音を黙って出すより良い。** */
#define SAAN_MAX_IDS 350

/* 既定は .bss に静的確保する。**malloc しない**（断片化させない・失敗しない）。
 * 16 バイト境界にそろえるのは PIE（SOC_SIMD_PREFERRED_DATA_ALIGNMENT = 16）のため。
 *
 * ⚠️ **ESP32（S3 でない）は dram0_0_seg が小さく、静的 208 KB が入らない**
 *    （M5Stack Core2 で 65,368 B 溢れた）。`-DSAAN_ARENA_HEAP=1` で起動時に
 *    heap_caps_malloc（PSRAM 優先 → 内部 DRAM）から取る。**PSRAM の arena は遅い**
 *    （未測定）ので、速度を測る構成では使わない。取れなければ起動時に止まる。 */
#ifndef SAAN_ARENA_HEAP
#define SAAN_ARENA_HEAP 0
#endif
#if SAAN_ARENA_HEAP
static uint8_t *g_arena;   /* tts_task の先頭で確保。16 B 境界は heap_caps_aligned_alloc が保証 */
#else
static __attribute__((aligned(16))) uint8_t g_arena[SAAN_ARENA_BYTES];
#endif

/* ⚠️ **スタックに置かない。** 8,192 B ある。
 * `saan_irfft_1024` は自動変数 zr[512]+zi[512] (float) だけで **4,096 B** 使う
 * （arm64 clang -O2 の実フレームは 4,224 B。otool -tv で実測。
 * ⚠️ Xtensa では別の値になる）。IDF の小さい既定スタックでは足りない。 */
static float g_chunk[SAAN_CHUNK * SAAN_HOP];

/* --- 端末側 G2P ----------------------------------------------------------
 *
 * 入力は**かな中間表現**。漢字は端末で扱わない（D-010 / D-011）。
 * 表は csrc/g2p_table.h に 913 B。
 *
 * ⚠️ **`saan_g2p_capacity()` と同じ式を使う。** 上限は `2 * バイト数 + 3`。
 *    足りないと SAAN_G2P_ERR_OVERFLOW で**きれいに失敗する**（黙って切り詰めない）。
 * ⚠️ **デモ文ではなく入力バッファの最大長から決める。** 対話入力の方が長い。 */
#define SAAN_G2P_IDS_CAP (2 * SAAN_CONSOLE_LINE_MAX + 3)
static int32_t g_ids[SAAN_G2P_IDS_CAP];

/* C99 には _Static_assert が無いので配列サイズで潰す（IDF は gnu17 だが csrc に合わせる） */
typedef char saan_g2p_cap_check[(SAAN_G2P_IDS_CAP >= SAAN_DEMO_N_IDS) ? 1 : -1];
typedef char saan_max_ids_check[(SAAN_MAX_IDS <= SAAN_G2P_IDS_CAP) ? 1 : -1];

/* タッチで「もう一度」喋るために、最後に合成した列を覚えておく。
 * g_ids は speak_line のたびに上書きされるので、個数と元の文字列だけ別に持つ。
 * ⚠️ **G2P が失敗したら 0 にする。** そのとき g_ids は途中まで書かれた壊れた列で、
 *    前の個数のまま再生すると**それらしい音**が出てしまう（未知語が無音で消えるのと同じ壊れ方）。 */
static int32_t g_last_n_ids;
static char    g_last_text[SAAN_CONSOLE_LINE_MAX];

/* 合成タスクのスタック。saan_irfft_1024 の 4 KB + 呼び出し段 + ログで、
 * IDF の小さい既定スタックでは足りない。**16 KB を明示する**。
 * ⚠️ 実機で `uxTaskGetStackHighWaterMark` を見て詰めること（QEMU では 11,180 B 残り）。 */
#define SAAN_TASK_STACK 16384
#define SAAN_TASK_PRIO  5

/* プリロールするチャンク数。1 チャンク = 2,048 sample = 92.88 ms */
#define SAAN_PREROLL_CHUNKS (SAAN_AUDIO_PREROLL_SAMPLES / (SAAN_CHUNK * SAAN_HOP))

/* シリアル入力を待つ単位。この間隔でタッチも見る（合成中は見ない） */
#define SAAN_POLL_MS 20

/* pull ごとの所要時間と n を控える上限。pull ごとの行は**実時間ループの中では出さず**、done: の後で
 * まとめて出す（UART 115200 の板では 1 行 ≈ 数 ms が pull と I2S write の間に入り、途切れの原因になる。
 * dt の外なので xRT には出ない = 審査 T1）。満チャンク pull の中央値もこの控えから出す。
 * 128 pull × 8 frames = 1,024 frames = 11.9 s の音声で、max_spec_length=700（8.13 s、88 pull）を
 * 超える。溢れたら以降は数えるだけ（行は出ない・中央値にも入らない。その旨を出す）。
 * ⚠️ 内部 DRAM の .bss を 128 × (4 + 1) = 640 B 食う。T1 の「メモリ 0」は arena（csrc）だけの話で、
 *    main.c はここで増える（審査 T1。以前は uint32_t × 256 = 1,024 B だった） */
#define SAAN_MAX_PULL_LOG 128
#if SAAN_CHUNK > 127
#error "pull_n は 7 bit（bit 7 は予算超過の印）。SAAN_CHUNK が 127 を超えるなら uint16_t にすること"
#endif

/* --- 段別プロファイラ（-DSAAN_PROFILE=1 のときだけ。csrc/saan_prof.h）------------
 *
 * 時計は CCOUNT（CPU サイクル。240 MHz なら 1 サイクル = 4.17 ns）。
 * ⚠️ **QEMU ではサイクルではない**（仮想時間ベース。`-icount` なら命令数に比例）。
 *    実機の表と QEMU の表を混ぜて読まないこと。
 * ⚠️ **計測自体のコスト**: 区間の出入りで CCOUNT を読む関数を呼ぶ。`hout` は
 *    出力チャネル 1,539 本なので WCOPY / MAC の区間は 1 チャンクに約 3,000 回入る。
 *    細かい区間ほど過大に出る。速度の報告には SAAN_PROFILE=0 のビルドを使うこと。 */
#if SAAN_PROFILE
#include "esp_cpu.h"
uint32_t saan_prof_now(void) { return esp_cpu_get_cycle_count(); }

static void prof_report(void) {
    const uint32_t steps = saan_prof_cnt[SAAN_PROF_STEP];
    if (steps == 0) return;
    const double step = (double)saan_prof_acc[SAAN_PROF_STEP] / (double)steps;
    ESP_LOGI(TAG, "----- 段別プロファイル（step_chunk %u 回の平均。単位 = CCOUNT）-----", (unsigned)steps);
    ESP_LOGI(TAG, "%-8s %10s %12s %7s %12s %10s", "区間", "回数/step", "cyc/step", "%%STEP", "要素/step", "cyc/要素");
    for (int id = 0; id < SAAN_PROF_N; ++id) {
        if (id == SAAN_PROF_INIT || id == SAAN_PROF_LOOKUP) continue;   /* 発話側に出す（下） */
        const double cnt = (double)saan_prof_cnt[id] / steps;
        const double acc = (double)saan_prof_acc[id] / steps;
        const double n   = (double)saan_prof_n[id] / steps;
        ESP_LOGI(TAG, "%-8s %10.2f %12.0f %6.1f%% %12.0f %10.2f", saan_prof_name(id), cnt, acc,
                 step > 0 ? 100.0 * acc / step : 0.0, n,
                 saan_prof_n[id] ? (double)saan_prof_acc[id] / (double)saan_prof_n[id] : 0.0);
    }
    /* ⚠️ DW 行には入れ子の QUANT（dw 入力の量子化 = saan_quantize_act_i8p）が含まれる。
     *    カーネル行（QUANT / WCOPY / MAC / DW / GELU / LN / PIPE …）を単純に足すと
     *    その分が二重計上になる。 */
    ESP_LOGI(TAG, "  ⚠️ DW の cyc/step は入れ子の QUANT（dw 入力の量子化）を含む。カーネル行の合算は二重計上");
    /* ⚠️ LOOKUP は S1 で init 側に移した（pull の中では 0 回）。step で割ると
     *    「0.7%/step」のように見えて、pull の中で走っていると誤読する（PROF-1）。 */
    ESP_LOGI(TAG, "----- INIT 側（発話あたり。step で割らない）-----");
    ESP_LOGI(TAG, "INIT  : %.0f cyc / 回 (%u 回)", saan_prof_cnt[SAAN_PROF_INIT]
             ? (double)saan_prof_acc[SAAN_PROF_INIT] / saan_prof_cnt[SAAN_PROF_INIT] : 0.0,
             (unsigned)saan_prof_cnt[SAAN_PROF_INIT]);
    ESP_LOGI(TAG, "LOOKUP: %u 回 / %llu cyc（init の resolve_weights。pull の中では 0 回が期待値）",
             (unsigned)saan_prof_cnt[SAAN_PROF_LOOKUP],
             (unsigned long long)saan_prof_acc[SAAN_PROF_LOOKUP]);
    ESP_LOGI(TAG, "1 step = %.0f cyc（240 MHz なら %.2f ms）。⚠️ QEMU ではサイクルではない",
             step, step / 240000.0);
}
#endif

static void log_heap(const char *when) {
    ESP_LOGI(TAG, "%s: 内部 DRAM free %u B / 最大ブロック %u B", when,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
}

/* --- 1 発話 ---------------------------------------------------------------
 *
 * ⚠️ **arena も PCM 統計も発話ごとに巻き戻す。** 巻き戻さないと 2 発話目の
 *    checksum が「1 + 2 発話目」になり、しかも値は出るので気づけない。 */
static bool synth_once(const saan_weights *w, const int32_t *ids, int32_t n_ids) {
    saan_pcm_reset();
#if SAAN_PROFILE
    saan_prof_reset();
#endif
    saan_ui_status("合成中…");

    saan_arena a;
    saan_arena_init(&a, g_arena, SAAN_ARENA_BYTES);

    saan_stream st;
    int64_t t_init = esp_timer_get_time();
    saan_status s = saan_stream_init(&st, w, &a, ids, n_ids, SAAN_S_V);
    t_init = esp_timer_get_time() - t_init;
    if (s != SAAN_OK) {
        ESP_LOGE(TAG, "saan_stream_init: %s", saan_strerror(s));
        return false;
    }

    /* ⚠️ 上に書いた二重防御。init が OK でも黙って確保に失敗していることがある */
    if (a.used < SAAN_ARENA_USED_FLOOR) {
        ESP_LOGE(TAG, "saan_stream_init は OK を返したが a.used が %u B しかない "
                      "(下限 %u B)。**確保が黙って失敗している** — "
                      "このまま pull すると NULL 書き込みで再起動する",
                 (unsigned)a.used, (unsigned)SAAN_ARENA_USED_FLOOR);
        ESP_LOGE(TAG, "arena を増やすか、csrc の確保順が変わったなら "
                      "`make -C csrc arena` で下限を測り直すこと");
        return false;
    }

    const double audio_s = (double)st.n_frames * SAAN_HOP / SAAN_SR;
    ESP_LOGI(TAG, "init %.2f ms / %d ids / %d frames / %d sample / 音声 %.3f s",
             (double)t_init / 1000.0, (int)n_ids, (int)st.n_frames,
             (int)st.n_frames * SAAN_HOP, audio_s);
    ESP_LOGI(TAG, "arena used %u B / peak %u B / 確保 %u B",
             (unsigned)a.used, (unsigned)a.peak, (unsigned)SAAN_ARENA_BYTES);

    /* --- プリロール ------------------------------------------------------
     * ⚠️ **鳴らし始める前に数チャンク計算しておく。** 最初の pull だけ
     * 定常の約 6 倍かかる（ホスト実測 12.2 ms vs 2.04 ms）。受容野 38 フレームの
     * warmup で内部の step_chunk が複数回走るため。 */
#if SAAN_BUFFERED
    /* 発話の総サンプル数は init の時点で決まる。そのぶん貯めて全部計算してから鳴らす */
    if (!saan_audio_begin_utterance((size_t)st.n_frames * SAAN_HOP)) return false;
    const int preroll_chunks = INT_MAX;   /* = 最後まで */
#else
    if (!saan_audio_begin_utterance(SAAN_AUDIO_PREROLL_SAMPLES)) return false;
    const int preroll_chunks = SAAN_PREROLL_CHUNKS;
#endif
    const int64_t t_begin = esp_timer_get_time();
    int32_t n = 0;
    int chunks = 0;
    int64_t t_first = 0, t_rest = 0;
    int32_t total_frames = 0;
    int underruns = 0, short_pulls = 0;
    bool eos = false;
    bool ok = true;
    /* T1: pull ごとの dt と n を控え、done: の後で行を出す + 満チャンク pull（n == SAAN_CHUNK、
     * 初回を除く）の中央値 / 平均を出す。「定常 xRT」の定義はその中央値。
     * ⚠️ 末尾の端数 pull（n < SAAN_CHUNK）と初回 pull（warmup で step_chunk が複数回走る）は混ぜない。
     *    以前の `mean_rest`（2 回目以降の全 pull の平均）は末尾 pull を含んでいて、
     *    そこが 1 sample も出さない step を 3 回回していたため xRT が膨らんでいた。
     * ⚠️ **M-82 / M-84 の「定常 xRT」はその旧定義**で、この firmware の値とは直接比べられない
     *    （旧定義の行も T1 後は末尾 pull の step が消えているので M-82 の再現にはならない）。
     *    T1 をまたいで比べてよいのは、定義に依らない「合成合計 ms」（全 pull の dt の和）と
     *    `-DSAAN_PROFILE=1` の STEP 行（1 step のサイクル）だけ。
     * pull_n の bit 7 は「その pull が予算を超えた」印（定常ループだけ立てる） */
    static uint32_t pull_us[SAAN_MAX_PULL_LOG];
    static uint8_t  pull_n[SAAN_MAX_PULL_LOG];

    for (int i = 0; i < preroll_chunks && !eos; ++i) {
        int64_t t0 = esp_timer_get_time();
        s = saan_stream_pull(&st, g_chunk, &n);
        int64_t dt = esp_timer_get_time() - t0;
        if (s != SAAN_OK) { ESP_LOGE(TAG, "pull: %s", saan_strerror(s)); ok = false; goto done; }
        if (n <= 0) { eos = true; break; }
        if (chunks == 0) t_first = dt; else t_rest += dt;
        if (chunks < SAAN_MAX_PULL_LOG) { pull_us[chunks] = (uint32_t)dt; pull_n[chunks] = (uint8_t)n; }
        /* ⚠️ `n` は**フレーム数**。サンプル数は n * SAAN_HOP */
        if (!saan_audio_preroll_push(g_chunk, (size_t)n * SAAN_HOP)) {
            ESP_LOGE(TAG, "プリロール容量の計算が合っていない。"
                          "SAAN_AUDIO_PREROLL_SAMPLES を見直すこと");
            ok = false; goto done;
        }
        total_frames += n; ++chunks;
        if (n < SAAN_CHUNK) ++short_pulls;
    }
    const double t_ready_ms = (double)(esp_timer_get_time() - t_begin) / 1000.0;
#if SAAN_BUFFERED
    ESP_LOGI(TAG, "全 %d チャンクを貯めた。発話開始まで %.0f ms（音声 %.3f s）",
             chunks, t_ready_ms, audio_s);
#else
    ESP_LOGI(TAG, "プリロール %d チャンク完了（初回 pull %.2f ms / 鳴らし始めまで %.0f ms）",
             chunks, (double)t_first / 1000.0, t_ready_ms);
#endif

    if (!saan_audio_start()) { ok = false; goto done; }

    /* --- 定常ループ（ストリーミングのみ。貯める方式では eos 済みで入らない）------ */
    while (!eos) {
        int64_t t0 = esp_timer_get_time();
        s = saan_stream_pull(&st, g_chunk, &n);
        int64_t dt = esp_timer_get_time() - t0;
        if (s != SAAN_OK) { ESP_LOGE(TAG, "pull: %s", saan_strerror(s)); ok = false; break; }
        if (n <= 0) break;

        /* ⚠️ **これが実機で最初に見るべき数値。** 1 チャンクの計算に、
         * そのチャンクが表す音声より長くかかったらアンダーラン。 */
        int64_t budget_us = (int64_t)n * SAAN_HOP * 1000000 / SAAN_SR;
        const bool over = dt > budget_us;
        if (over) ++underruns;
        /* ⚠️ ここでは ESP_LOGI しない（pull と I2S write の間に UART の時間が入る）。行は done: の後 */
        if (chunks == 0) t_first = dt; else t_rest += dt;
        if (chunks < SAAN_MAX_PULL_LOG) {
            pull_us[chunks] = (uint32_t)dt;
            pull_n[chunks] = (uint8_t)n | (over ? 0x80 : 0);
        }
        if (n < SAAN_CHUNK) ++short_pulls;
        total_frames += n; ++chunks;

        if (!saan_audio_write_f32(g_chunk, (size_t)n * SAAN_HOP)) { ok = false; break; }
    }

done:
    saan_audio_stop();
    {
        double total_audio = (double)total_frames * SAAN_HOP / SAAN_SR;
        double mean_rest = chunks > 1 ? (double)t_rest / (chunks - 1) / 1000.0 : 0.0;
        double chunk_ms = (double)SAAN_CHUNK * SAAN_HOP * 1000.0 / SAAN_SR;
        const int logged = chunks < SAAN_MAX_PULL_LOG ? chunks : SAAN_MAX_PULL_LOG;
        /* pull ごとの行（実時間ループの外で出す） */
        for (int i = 0; i < logged; ++i)
            ESP_LOGI(TAG, "pull %d: n=%d %.2f ms%s", i + 1, (int)(pull_n[i] & 0x7f),
                     (double)pull_us[i] / 1000.0, (pull_n[i] & 0x80) ? "  ← 予算超過" : "");
        if (chunks > logged)
            ESP_LOGW(TAG, "pull %d 回目以降の %d 回は控えていない（SAAN_MAX_PULL_LOG）。"
                          "中央値もそこまでの満チャンク pull だけ", logged + 1, chunks - logged);
        /* 定常 = 初回を除く満チャンク pull。控えの先頭に詰め直して中央値（挿入ソート）と平均 */
        uint32_t *full_us = pull_us;
        int n_full = 0;
        for (int i = 1; i < logged; ++i)
            if ((pull_n[i] & 0x7f) == SAAN_CHUNK) full_us[n_full++] = pull_us[i];
        double full_mean = 0.0, full_med = 0.0;
        if (n_full > 0) {
            uint64_t sum = 0;
            for (int i = 1; i < n_full; ++i) {
                uint32_t v = full_us[i]; int j = i - 1;
                while (j >= 0 && full_us[j] > v) { full_us[j + 1] = full_us[j]; --j; }
                full_us[j + 1] = v;
            }
            for (int i = 0; i < n_full; ++i) sum += full_us[i];
            full_mean = (double)sum / n_full / 1000.0;
            full_med = (n_full & 1) ? full_us[n_full / 2] / 1000.0
                                    : (full_us[n_full / 2 - 1] + full_us[n_full / 2]) / 2000.0;
        }
        /* ⚠️ 満チャンク pull が 0 回（n_frames ≤ SAAN_CHUNK の短い発話）なら xRT は**未測定**。
         *    0.000 と出すと「速い」に見えるので n/a にする（審査 T1） */
        const bool have_xrt = n_full > 0 && chunk_ms > 0;
        const double xrt = have_xrt ? full_med / chunk_ms : 0.0;
        char xrt_s[16];
        if (have_xrt) snprintf(xrt_s, sizeof xrt_s, "%.2f", xrt);
        else          snprintf(xrt_s, sizeof xrt_s, "n/a");
        const double total_ms = (double)(t_first + t_rest) / 1000.0;
        ESP_LOGI(TAG, "----- 結果 -----");
        ESP_LOGI(TAG, "pull %d 回 / %d frames / 音声 %.3f s（端数チャンク %d 回）",
                 chunks, (int)total_frames, total_audio, short_pulls);
        ESP_LOGI(TAG, "合成合計 %.2f ms（全 pull の dt の和。定義に依らない量 — T1 前後の比較はこれか "
                      "SAAN_PROFILE=1 の STEP 行で）/ 音声 %.3f s → 合成/音声 %.3f",
                 total_ms, total_audio, total_audio > 0 ? total_ms / 1000.0 / total_audio : 0.0);
        ESP_LOGI(TAG, "初回 pull %.2f ms / 2 回目以降 mean %.2f ms（⚠️ 末尾の端数 pull を含む。参考値）"
                      "(満チャンク 1 個 = %.2f ms の音声)",
                 (double)t_first / 1000.0, mean_rest, chunk_ms);
        ESP_LOGI(TAG, "満チャンク pull（n=%d、初回を除く %d 回）: 中央値 %.2f ms / 平均 %.2f ms",
                 (int)SAAN_CHUNK, n_full, full_med, full_mean);
        if (have_xrt)
            ESP_LOGI(TAG, "定常 xRT = %.3f（満チャンク pull の中央値 / %.2f ms。平均なら %.3f）"
                          "  ← **1.0 を超えたら実時間に間に合っていない**",
                     xrt, chunk_ms, full_mean / chunk_ms);
        else
            ESP_LOGW(TAG, "定常 xRT = n/a（満チャンク pull が 0 回。n_frames=%d ≤ %d の短い発話では測れない）",
                     (int)st.n_frames, (int)SAAN_CHUNK);
        ESP_LOGW(TAG, "⚠️ 「定常 xRT」の定義は T1 で変わった（旧: 2 回目以降の全 pull の平均 = 末尾 pull の"
                      "出力に寄与しない step 3 回込み）。M-82 / M-84 の値とは直接比べないこと。"
                      "上の「2 回目以降 mean」も T1 後は末尾の step が消えているので旧値の再現ではない");
#if SAAN_BUFFERED
        ESP_LOGI(TAG, "再生方式: 全部貯めてから再生（途切れ 0）/ 発話開始まで %.0f ms", t_ready_ms);
        saan_ui_status("合成 %.1f s → 音声 %.1f s (xRT %s)",
                       t_ready_ms / 1000.0, total_audio, xrt_s);
#else
        /* ⚠️ 判定は pull ごとの `dt > その pull の音声長`。**末尾の端数 pull を含む**
         *    （端数 pull は音声長が短いので予算も短い）。実際に途切れたかは測っていない */
        ESP_LOGI(TAG, "アンダーラン %d / %d チャンク（判定は pull ごと。末尾の端数 pull を含む）",
                 underruns, chunks);
        saan_ui_status("xRT %s  途切れ %d/%d", xrt_s, underruns, chunks);
        if (underruns > 0)
            ESP_LOGW(TAG, "途切れている。途切れない再生が要るなら -DSAAN_BUFFERED=1 "
                          "（1 発話ぶんを貯めてから鳴らす。待ちは合成時間）");
#endif
        ESP_LOGI(TAG, "int16 クリップ %u sample", (unsigned)saan_pcm_clip_count());
        /* ⚠️ **移植が正しいことの唯一の機械的な証拠。** 「音が鳴った」ではなく
         *    この 2 つがホスト（esp32/host_stub）と一致するかで判定する。 */
        ESP_LOGI(TAG, "出力 PCM: %u sample / FNV-1a 0x%016llx",
                 (unsigned)saan_pcm_samples(),
                 (unsigned long long)saan_pcm_checksum());
        /* ⚠️ **checksum が違っても、ここが合っていれば丸め差。**
         *    ホストとターゲットで bit 一致は**期待できない**（float の丸めが違う）。
         *    bit 一致を主張してよいのは**同じターゲット上の 2 構成**を比べたときだけ。 */
        ESP_LOGI(TAG, "        |max| %d / Σx² %llu",
                 (int)saan_pcm_absmax(),
                 (unsigned long long)saan_pcm_sqsum());
        ESP_LOGI(TAG, "タスクスタック残り %u B（%d B 中）",
                 (unsigned)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
                 (int)SAAN_TASK_STACK);
        if (underruns > 0)
            ESP_LOGW(TAG, "アンダーラン %d 回。実機の内訳は -DSAAN_PROFILE=1 で（M-82: CoreS3 / W8A8+PIE は "
                          "xRT 0.926 で 1 回残る）。途切れない再生が要るなら -DSAAN_BUFFERED=1", underruns);
#if SAAN_PROFILE
        prof_report();
#endif
    }
    return ok;
}

#if SAAN_KANJI
/* --- 漢字かな交じり文 1 行 → 合成 ----------------------------------------
 *
 * ⚠️ **ホスト（フル辞書）とは一致しない。** 枝刈りの分だけ食い違う
 *    （実測 17.79% の文。M-74 / C-050）。**既知の代償**であって欠陥ではない。
 * ⚠️ **音は一度も聞いていない。** */
static bool speak_kanji(const saan_weights *w, const char *text, size_t nbytes) {
    if (nbytes == 0) {
        ESP_LOGW(TAG, "空行。漢字かな交じり文を入力すること（例: 今日は良い天気ですね。）");
        return false;
    }
    if (!g_dict_ok) {
        ESP_LOGE(TAG, "辞書が開けていない。漢字モードは使えない");
        return false;
    }
    int32_t n_ids = 0;
    int n_tok = 0;
    /* G28（K-8）: 漢字文 1 行の G2P レイテンシを実測で出す（Viterbi + NJD 4 段 + ラベル → ids） */
    const int64_t t_kg2p = esp_timer_get_time();
    saan_kanji_status ks = saan_kanji_to_ids(&g_dict, text, nbytes,
                                            g_arena, SAAN_ARENA_BYTES,
                                            g_ids, SAAN_G2P_IDS_CAP,
                                            &n_ids, &n_tok);
    const double kg2p_ms = (double)(esp_timer_get_time() - t_kg2p) / 1000.0;
    if (ks != SAAN_KANJI_OK) {
        ESP_LOGE(TAG, "漢字 G2P 失敗: %s", saan_kanji_strerror(ks));
        return false;
    }
    ESP_LOGI(TAG, "漢字 G2P: %u B -> 形態素 %d 個 / ids %d 個 / %.2f ms", (unsigned)nbytes, n_tok,
             (int)n_ids, kg2p_ms);
    if (n_ids > SAAN_MAX_IDS) {
        ESP_LOGE(TAG, "ids が %d 個で上限 %d を超えた。**喋らない**（短く区切ること）",
                 (int)n_ids, (int)SAAN_MAX_IDS);
        return false;
    }
    g_last_n_ids = n_ids;
    snprintf(g_last_text, sizeof g_last_text, "!%.*s", (int)nbytes, text);
    saan_ui_show(NULL, g_last_text);
    return synth_once(w, g_ids, n_ids);
}
#endif

/* --- 入力 1 行 → 合成 -----------------------------------------------------
 *
 * ⚠️ **拒否する理由を必ず「どの文字か」まで出す。** 未知語や記号は
 *    「黙って無音になる」のがこの入力仕様の一番危ない壊れ方なので、
 *    端末側では**必ずエラーにして位置を示す**（`err_byte`）。 */
static bool speak_line(const saan_weights *w, const char *text, size_t nbytes) {
    g_last_n_ids = 0;   /* 失敗したら「もう一度」も無効にする（g_last_n_ids の ⚠️） */
    if (nbytes == 0) {
        ESP_LOGW(TAG, "空行。かな中間表現を入力すること（例: きょ][おわよ][いて][んきです°ね）");
        return false;
    }
    if (saan_g2p_capacity(nbytes) > SAAN_G2P_IDS_CAP) {
        ESP_LOGE(TAG, "入力 %u B は長すぎる（ids バッファ %d 分）",
                 (unsigned)nbytes, (int)SAAN_G2P_IDS_CAP);
        return false;
    }

    int32_t n_ids = 0;
    saan_g2p_info gi;
    int64_t t_g2p = esp_timer_get_time();
    saan_g2p_status gs = saan_g2p(text, nbytes, g_ids, SAAN_G2P_IDS_CAP, &n_ids, &gi);
    t_g2p = esp_timer_get_time() - t_g2p;

    if (gs != SAAN_G2P_OK) {
        ESP_LOGE(TAG, "G2P 失敗: %s（%d バイト目）", saan_g2p_strerror(gs), (int)gi.err_byte);
        if (gs == SAAN_G2P_ERR_UNKNOWN && gi.err_byte >= 0
            && (size_t)gi.err_byte < nbytes) {
            /* err_byte から先の 1 文字（最大 4 B）を見せる。**何を消せばよいか分かるように。** */
            char ch[8] = {0};
            size_t k = 0;
            for (size_t i = (size_t)gi.err_byte; i < nbytes && k < 4; ++i, ++k) {
                ch[k] = text[i];
                if (k > 0 && ((unsigned char)text[i] & 0xC0u) != 0x80u) { ch[k] = '\0'; break; }
            }
            ESP_LOGE(TAG, "  受け付けない文字: \"%s\"", ch);
            ESP_LOGE(TAG, "  使えるのは **ひらがな** と [ ] # ° ー っ ん と ? ?! ?. ?~ だけ。"
                          "漢字・カタカナ・句読点 (。、) は端末では扱わない");
            ESP_LOGE(TAG, "  漢字混じり文からの変換は**ホスト側**で: "
                          "uv run python scripts/to_intermediate.py \"文\"");
        }
        return false;
    }

    ESP_LOGI(TAG, "G2P: %u B -> %d ids / %.3f ms（音素 %d 個・うち PAD %d）",
             (unsigned)nbytes, (int)n_ids, (double)t_g2p / 1000.0,
             (int)gi.n_phonemes, (int)gi.n_pad_phonemes);

    /* ⚠️ **黙って落ちたものを必ず出す。** `ー`（直前に平母音が無い）と
     *    `°`（直前が平母音でない）は**例外を出さずに捨てられる**規約なので、
     *    件数を見せないと「打ったのに反映されない」に気づけない。 */
    if (gi.n_dropped_long > 0 || gi.n_dropped_devoice > 0)
        ESP_LOGW(TAG, "  ⚠️ 黙って落ちた: ー %d 個 / ° %d 個"
                      "（直前が平母音でないと効かない規約）",
                 (int)gi.n_dropped_long, (int)gi.n_dropped_devoice);

    if (n_ids > SAAN_MAX_IDS) {
        ESP_LOGE(TAG, "%d ids は上限 %d を超える。**短く区切って入力すること**",
                 (int)n_ids, (int)SAAN_MAX_IDS);
        ESP_LOGE(TAG, "  （arena は %d ids まで持つが、生徒が学習したのは "
                      "D-017 の max_spec_length=700 = %d ids 相当まで。"
                      "その外は分布外で品質を保証できない）", 520, (int)SAAN_MAX_IDS);
        return false;
    }

    /* ここまで来れば g_ids は完成した列。タッチで再生できるようにしてから喋る */
    g_last_n_ids = n_ids;
    memcpy(g_last_text, text, nbytes);
    g_last_text[nbytes] = '\0';
    saan_ui_show(NULL, g_last_text);
    return synth_once(w, g_ids, n_ids);
}

/* --- 起動セルフテスト -----------------------------------------------------
 *
 * ⚠️ **kSaanDemoIds は入力ではなく答え合わせの錨。** 合成に使うのは
 *    saan_g2p() が今その場で作った g_ids の方。錨と食い違ったら**走らせない** —
 *    このファームの目的は「音が出た」ではなく「Python と同じ列になっている」ことの
 *    確認なので、ずれたまま**それらしい音**を出すのが一番悪い
 *    （未知語が無音で消えるのと同じ壊れ方）。 */
static bool boot_selftest(int32_t *n_ids_out) {
    /* ⚠️ **上の #define は saan_g2p_capacity() の式を写したもの**（配列サイズには
     *    関数を書けない）。**2 か所にある式は必ずずれる**ので、実体と突き合わせる。 */
    if (SAAN_G2P_IDS_CAP < saan_g2p_capacity(SAAN_DEMO_INTERMEDIATE_BYTES)) {
        ESP_LOGE(TAG, "SAAN_G2P_IDS_CAP (%d) が saan_g2p_capacity() (%d) より小さい。"
                      "main.c の式が csrc/g2p.c とずれている",
                 (int)SAAN_G2P_IDS_CAP,
                 (int)saan_g2p_capacity(SAAN_DEMO_INTERMEDIATE_BYTES));
        return false;
    }

    int32_t n_ids = 0;
    saan_g2p_info gi;
    int64_t t_g2p = esp_timer_get_time();
    saan_g2p_status gs = saan_g2p(SAAN_DEMO_INTERMEDIATE, SAAN_DEMO_INTERMEDIATE_BYTES,
                                  g_ids, SAAN_G2P_IDS_CAP, &n_ids, &gi);
    t_g2p = esp_timer_get_time() - t_g2p;
    if (gs != SAAN_G2P_OK) {
        ESP_LOGE(TAG, "saan_g2p: %s (err_byte=%d)", saan_g2p_strerror(gs), (int)gi.err_byte);
        return false;
    }
    ESP_LOGI(TAG, "G2P セルフテスト: \"%s\" (%d B) -> %d ids / %.3f ms",
             SAAN_DEMO_INTERMEDIATE, (int)SAAN_DEMO_INTERMEDIATE_BYTES,
             (int)n_ids, (double)t_g2p / 1000.0);
    if (n_ids != SAAN_DEMO_N_IDS
        || memcmp(g_ids, kSaanDemoIds, sizeof kSaanDemoIds) != 0) {
        ESP_LOGE(TAG, "G2P の出力が demo_ids.h の錨と一致しない（%d ids / 期待 %d）。"
                      "**テーブルか実装がずれている** — "
                      "`make -C csrc g2p` と `uv run python scripts/gen_g2p_tables.py` を見ること",
                 (int)n_ids, (int)SAAN_DEMO_N_IDS);
        return false;
    }
    ESP_LOGI(TAG, "     OK  %d ids が demo_ids.h の錨と完全一致", (int)n_ids);
    /* ⚠️ **g_ids には「今その場で G2P した、錨と一致することを確認済みの列」が入っている。**
     *    起動時の 1 発話はこれをそのまま使う。個数を明示して返すのは、
     *    間に別のコードが入って g_ids が上書きされたときに気づけるようにするため。 */
    *n_ids_out = n_ids;
    return true;
}

#if SAAN_INTERACTIVE
static void print_usage(void) {
    /* ⚠️ ESP_LOG ではなく素の行で出す。**貼り付けて使う手順書**なので、
     *    ログレベルで消えたりタイムスタンプが混ざったりすると読みにくい。 */
    ESP_LOGI(TAG, "==================== 対話モード ====================");
    ESP_LOGI(TAG, "かな中間表現を 1 行入力して Enter で喋る。");
    ESP_LOGI(TAG, "  例:  きょ][おわよ][いて][んきです°ね     （今日は良い天気ですね。）");
    ESP_LOGI(TAG, "  例:  こんにちわ");
    ESP_LOGI(TAG, "記号:  [ 上昇 / ] 下降核 / # 句境界 / ° 無声化 / ? ?! ?. ?~ 疑問");
#if SAAN_KANJI
    ESP_LOGI(TAG, "**漢字対応ビルド**: `!` で始めると漢字かな交じり文として扱う。");
    ESP_LOGI(TAG, "  例:  !今日は良い天気ですね。");
    ESP_LOGI(TAG, "⚠️ 端末の辞書は枝刈りしてあるので、**ホストと 17.79%% の文で"
                  "読みが変わる**（M-74）。⚠️ 音は誰も聞いていない。");
#else
    ESP_LOGI(TAG, "⚠️ **漢字・カタカナ・句読点は受け付けない**（端末に辞書が無い）。");
    ESP_LOGI(TAG, "   漢字混じり文からは**ホスト側**で作る:");
    ESP_LOGI(TAG, "     uv run python scripts/to_intermediate.py \"今日は良い天気ですね。\"");
#endif
    ESP_LOGI(TAG, "⚠️ アクセント記号を省くと平板になる。**音は出るが正しい抑揚ではない。**");
    ESP_LOGI(TAG, "編集: BS/DEL 1 文字消す / Ctrl-U 行を消す / 上限 %d ids",
             (int)SAAN_MAX_IDS);
    ESP_LOGI(TAG, "画面があればタッチで直前の文をもう一度喋る（saan_ui の実装次第）。");
    ESP_LOGI(TAG, "====================================================");
}
#endif

static void tts_task(void *arg) {
    (void)arg;
    log_heap("起動直後");
#if SAAN_ARENA_HEAP
    g_arena = (uint8_t *)heap_caps_aligned_alloc(16, SAAN_ARENA_BYTES,
                                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (g_arena) {
        ESP_LOGW(TAG, "arena %d B を **PSRAM** に確保 (%p)。合成は遅くなる（速度の測定には使わない）",
                 (int)SAAN_ARENA_BYTES, (void *)g_arena);
    } else {
        g_arena = (uint8_t *)heap_caps_aligned_alloc(16, SAAN_ARENA_BYTES,
                                                     MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (!g_arena) {
            ESP_LOGE(TAG, "arena %d B を確保できない（PSRAM も内部 DRAM も）", (int)SAAN_ARENA_BYTES);
            vTaskDelete(NULL); return;
        }
        ESP_LOGI(TAG, "arena %d B を内部 DRAM のヒープに確保 (%p)", (int)SAAN_ARENA_BYTES, (void *)g_arena);
    }
#else
    ESP_LOGI(TAG, "arena %d B を .bss に静的確保 (%p) / G2P の ids %d B",
             (int)SAAN_ARENA_BYTES, (void *)g_arena, (int)sizeof g_ids);
#endif

    static saan_weights w;
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

#if SAAN_KANJI
    /* ⚠️ **重みより後に開く。** MMU の窓は flash と PSRAM で共有なので、
     *    先に 13.5 MB を貼ると model の mmap が落ちることがある。 */
    g_dict_ok = saan_dict_open(&g_dict) && saan_kanji_init();
    if (!g_dict_ok)
        ESP_LOGW(TAG, "辞書を開けなかった（または作業領域を取れなかった）。"
                      "**かな入力だけ**で続ける");
    else
        ESP_LOGI(TAG, "漢字経路の作業領域 %u B", (unsigned)saan_kanji_workbytes());
    log_heap("辞書 mmap 後");
#endif

    /* ⚠️ この順番。M5 実装では saan_audio_setup() が M5.begin() を呼び、saan_ui_init() はその後 */
    if (!saan_audio_setup(SAAN_SR)) { vTaskDelete(NULL); return; }
    if (!saan_ui_init()) { vTaskDelete(NULL); return; }
    int32_t demo_n_ids = 0;
    if (!boot_selftest(&demo_n_ids)) { vTaskDelete(NULL); return; }

#if SAAN_BOOT_SPEAK
    /* ⚠️ **ホスト / QEMU / 実機を突き合わせる基準はこの 1 文**（M-62 / M-63 の記録値）。
     *    対話入力は毎回違う列なので突き合わせに使えない。ただし**同じ中間表現を
     *    打てば同じ列になる**ことは確認済みなので、既定では喋らない（上の #define）。 */
    ESP_LOGI(TAG, "起動時の 1 発話: \"%s\"", SAAN_DEMO_TEXT);
    g_last_n_ids = demo_n_ids;
    strncpy(g_last_text, SAAN_DEMO_INTERMEDIATE, sizeof g_last_text - 1);
    saan_ui_show(SAAN_DEMO_TEXT, SAAN_DEMO_INTERMEDIATE);
    (void)synth_once(&w, g_ids, demo_n_ids);
    log_heap("1 発話後");
#else
    (void)demo_n_ids;   /* 錨との照合だけして喋らない */
    saan_ui_show(SAAN_DEMO_TEXT, SAAN_DEMO_INTERMEDIATE);
    saan_ui_status("シリアルの かな> に 1 行入力");
    ESP_LOGI(TAG, "起動時は喋らない。突き合わせ用の 1 文を出すには "
                  "-DSAAN_BOOT_SPEAK=1（同じ中間表現を打っても同じ列になる）");
#endif

#if SAAN_INTERACTIVE
    if (!saan_console_init()) {
        ESP_LOGE(TAG, "コンソールを開けなかった。対話入力は使えない");
        vTaskDelete(NULL); return;
    }
    print_usage();
    saan_console_prompt();
    for (;;) {
        const char *line = NULL;
        int n = saan_console_poll(&line, SAAN_POLL_MS);
        if (n == SAAN_CONSOLE_PENDING) {
            /* 行が完成していない間はタッチを見る。**合成中はここに来ない**ので、
             * 合成中に何度触っても、戻ってきたときの 1 回ぶんにまとまる */
            if (saan_ui_poll_touch() && g_last_n_ids > 0) {
                ESP_LOGI(TAG, "タッチ → もう一度: \"%s\" (%d ids)", g_last_text, (int)g_last_n_ids);
                (void)synth_once(&w, g_ids, g_last_n_ids);
                saan_console_prompt();
            }
            continue;
        }
        if (n == SAAN_CONSOLE_ERROR) {
            ESP_LOGE(TAG, "コンソールの読み取りに失敗した");
            break;
        }
        if (n == SAAN_CONSOLE_TOO_LONG) {
            /* ⚠️ **切り詰めて喋らない。** 先頭だけ喋ると「端末とホストで同じ列」が崩れる */
            ESP_LOGE(TAG, "入力が %d B を超えた。**行ごと捨てた**（切り詰めていない）。"
                          "短く区切ること", (int)SAAN_CONSOLE_LINE_MAX - 1);
            saan_console_prompt();
            continue;
        }
#if SAAN_KANJI
        /* ⚠️ **`!` を前置したときだけ漢字経路。** 既定をかなのままにするのは、
         *    「同じ 1 行が構成で違う音になる」のを避けるため。 */
        if (n > 0 && line[0] == '!') {
            (void)speak_kanji(&w, line + 1, (size_t)n - 1);
            saan_console_prompt();
            continue;
        }
#endif
        (void)speak_line(&w, line, (size_t)n);
        saan_console_prompt();
    }
#else
    /* ⚠️ **未使用警告よけに捨てるのではなく、コンパイル対象に残すために参照する。**
     *    ホスト stub のゲート（scripts/check_esp32_template.sh の 8）は
     *    -Werror で main.c を通すので、#if で囲うと speak_line だけ検査されなくなる。 */
    (void)&speak_line;
    ESP_LOGI(TAG, "SAAN_INTERACTIVE=0 でビルドされている（ホスト stub）。対話ループには入らない");
#endif

    log_heap("終了時");
    vTaskDelete(NULL);
}

void app_main(void) {
    ESP_LOGI(TAG, "sanoTTS-jp ESP32-S3（**実機未検証** — esp32/README.md を読むこと）");
    xTaskCreate(tts_task, "saan_tts", SAAN_TASK_STACK, NULL, SAAN_TASK_PRIO, NULL);
}
