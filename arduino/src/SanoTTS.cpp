/* SanoTTS.h の実装。
 *
 * ⚠️ **`esp32/main/main.c` の `speak_auto` / `speak_line` / `speak_kanji` / `synth_once`
 *    からの移植。** 不変条件を落とすと「音は出るが正しくない」形で壊れる。移してあるもの:
 *
 *   1. 経路は `saan_g2p_classify()` の 3 値で決める。**拒否はそのまま拒否する**
 *      （読めない文字を黙って落とすのがこの入力仕様の一番危ない壊れ方）
 *   2. arena は 136 KB の静的確保。`saan_stream_arena_needed()` の戻り値を使わない
 *      （あれは緩い上限で n_ids=350 に 250.7 KB を返す）。見るのは
 *      `saan_stream_arena_peak(350)` = 116,592 B（W8A8・ホスト。C-100 / C-102。⚠️ MEM-7 の前は 149,824 B）
 *   3. `saan_stream_init` が OK でも `a.used` を期待値と突き合わせる
 *      （黙った確保失敗の二重防御。外すと pull の中で NULL 書き込み → ログ無しで再起動）
 *   4. `n_ids > 350` は喋らずに拒否する（分布外の音を黙って出さない）
 *   5. 発話ごとに `saan_pcm_reset()`（忘れると 2 発話目の checksum が「1 + 2 発話目」になる）
 *   6. 作業バッファはスタックに置かない（`saan_irfft_1024` だけで 4 KB 使う）
 *   7. float → int16 は `saan_f32_to_i16()` **だけ**を通す（checksum の唯一の実装）
 *   8. プリロールしてから鳴らす（初回 pull は定常の約 6 倍。省くと必ず途切れる）
 */
#include "SanoTTS.h"

extern "C" {
#include "core/saanotts.h"
#include "core/saanotts_stream.h"
#include "core/g2p.h"
#include "core/saan_pcm.h"
#include "core/saan_model.h"
#if SANOTTS_ENABLE_KANJI
#include "core/jdict.h"
#include "core/saan_dict.h"
#include "core/saan_kanji.h"
#endif
}

#include <string.h>

/* --- arena ----------------------------------------------------------------
 *
 * 136 KB (139,264 B)。⚠️ **この値の根拠は実測**（`make -C csrc arena` の 2 レーン）:
 * MEM-7（duration net の窓分割。M-143 / M-144）で n_ids=350 の
 * `saan_stream_arena_peak` は W8A32 **114,128 B** / W8A8 **116,592 B** まで下がった
 * （どちらもホストの sizeof）。⚠️ **`arena_used()` を下限だと思わないこと** —
 * conv が上に取る activation 作業領域 2,464 B を含まない（C-100 / C-102）。
 * ⚠️ **合成だけなら 116 KB で足りる。136 KB は漢字経路の Viterbi のため**
 * （`SAAN_KANJI_KEY_MAX` の鍵を受けるのに prefix + 64·(KEY_MAX+1) = 138,304 B。C-101）。
 * **このライブラリは漢字を有効にできる**ので、どちらの構成でも安全な方を既定にしてある。
 * ⚠️ **漢字経路（Viterbi と NJD）はこの同じ arena を借りる。** 別に確保しない。 */
#define SANOTTS_ARENA_BYTES (136 * 1024)   /* ⚠️ 下限は漢字 138,304 B（C-101）/ 合成 116,592 B */

#if SANOTTS_ARENA_HEAP
/* ⚠️ **16 バイト境界が要る**（PIE の SOC_SIMD_PREFERRED_DATA_ALIGNMENT）。
 *    `heap_caps_aligned_alloc` が保証する。取れなければ `begin()` が失敗する。 */
#include <esp_heap_caps.h>
static uint8_t* g_arena;
#else
static __attribute__((aligned(16))) uint8_t g_arena[SANOTTS_ARENA_BYTES];
#endif

#if SANOTTS_ENABLE_KANJI
/* ⚠️ 漢字経路の作業領域が arena に収まらないと、`layout()` が NULL を返して
 *    G2P が黙って失敗する。**コンパイル時に止める。** */
static_assert(SANOTTS_ARENA_BYTES >= SAAN_KANJI_WORKBYTES,
              "arena が漢字経路の作業領域より小さい。SANOTTS_ARENA_BYTES を上げるか "
              "SANOTTS_ENABLE_KANJI=0 にすること");
#endif

/* ⚠️ **スタックに置かない。** `saan_irfft_1024` は自動変数だけで 4 KB 使う。
 *
 * ⚠️ **`g_chunk`（float・8,192 B）は消えた**（MEM-8。[M-145](../../docs/measurements.md#m-145)）。
 *    `saan_stream_pull_ptr` が出力リングの中を指して返すので、写し先が要らない。
 *    ⚠️ **返ったポインタは次に pull を呼ぶまでだけ有効。** 下の 3 か所はどれも
 *    **その場で `g_i16` へ変換**してから次に進むので問題ない。
 *    **保持するコードを足すならコピー版 `saan_stream_pull` に戻すこと。** */
static int16_t g_i16[SAAN_CHUNK * SAAN_HOP];

/* ids の容量は `saan_g2p_capacity()` と同じ式（2 * バイト数 + 3）。 */
static const int32_t kIdsCap = 2 * SANOTTS_MAX_INPUT_BYTES + 3;
static int32_t g_ids[kIdsCap];

static saan_weights g_w;
#if SANOTTS_ENABLE_KANJI
static jdict_t g_dict;
#endif

/* --- 起動 ------------------------------------------------------------------ */
bool SanoTTS::begin() {
    if (m_ready) return true;

#if SANOTTS_ARENA_HEAP
    if (!g_arena) {
        /* ⚠️ **PSRAM を先に試す。** 内部 DRAM に 136 KB 取れる板ばかりではない。
         *    ⚠️ ただし PSRAM の arena は遅い（未測定）ので、速度を測るなら
         *    SANOTTS_ARENA_HEAP=0（静的）にすること。 */
        g_arena = (uint8_t*)heap_caps_aligned_alloc(
            16, SANOTTS_ARENA_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!g_arena)
            g_arena = (uint8_t*)heap_caps_aligned_alloc(
                16, SANOTTS_ARENA_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (!g_arena) {
            m_err = "arena 139,264 B を確保できない（PSRAM も内部 DRAM も）";
            return false;
        }
    }
#endif

    if (!saan_model_open(&g_w)) {
        m_err = "重みを開けない。重みライブラリ（SanoTTS-jp-voice-tsukuyomi-v4）を"
                "入れるか、SANOTTS_MODEL_FROM_PARTITION=1 で model パーティションを焼くこと";
        return false;
    }

#if SANOTTS_ENABLE_KANJI
    /* ⚠️ **辞書が無くても起動は止めない**（D-063）。漢字経路だけ無効にして
     *    かな専用で続く。`saan_dict.c` が SHA-256 の不一致も同じ扱いにする。 */
    m_kanjiReady = saan_dict_open(&g_dict) && (saan_kanji_init() != 0);
#else
    m_kanjiReady = false;
#endif

    if (m_spk && !m_spk->begin(kSampleRate)) {
        m_err = "スピーカーの begin() が失敗した";
        return false;
    }

    m_ready = true;
    return true;
}

/* --- 文 → 生徒インデックス列 ---------------------------------------------- */
bool SanoTTS::toIds(const char* text, size_t nbytes, int32_t* nIds) {
    *nIds = 0;
    if (nbytes == 0) {
        m_err = "空行。漢字かな交じり文かかな中間表現を入れること";
        return false;
    }
    if (nbytes > SANOTTS_MAX_INPUT_BYTES) {
        m_err = "入力が長すぎる（SANOTTS_MAX_INPUT_BYTES を超えた）。短く区切ること";
        return false;
    }

    saan_g2p_status why = SAAN_G2P_OK;
    int32_t err_byte = -1;
    const saan_g2p_route route = saan_g2p_classify(text, nbytes, &why, &err_byte);

    if (route == SAAN_G2P_ROUTE_KANA) {
        saan_g2p_info gi;
        const saan_g2p_status gs =
            saan_g2p(text, nbytes, g_ids, kIdsCap, nIds, &gi);
        if (gs != SAAN_G2P_OK) {
            /* ⚠️ **ここには来ないはず** — classify が同じトークナイザで KANA と
             *    判定している。来たら 2 つがずれた印。 */
            m_err = saan_g2p_strerror(gs);
            return false;
        }
        return true;
    }

    if (route == SAAN_G2P_ROUTE_DICT) {
#if SANOTTS_ENABLE_KANJI
        if (!m_kanjiReady) {
            m_err = "辞書が開けていないので漢字・カタカナ・句読点は読めない"
                    "（dict パーティションを焼くか、かな中間表現で入れること）";
            return false;
        }
        int n_tok = 0;
        const saan_kanji_status ks =
            saan_kanji_to_ids(&g_dict, text, nbytes, g_arena, SANOTTS_ARENA_BYTES,
                              g_ids, kIdsCap, nIds, &n_tok);
        if (ks != SAAN_KANJI_OK) {
            m_err = saan_kanji_strerror(ks);
            return false;
        }
        return true;
#else
        /* ⚠️ **喋らずに理由を出す。** かな経路に無理やり通すと読めない文字が黙って落ちる。 */
        m_err = "この構成は辞書を持たないので漢字・カタカナ・句読点は扱えない"
                "（SANOTTS_ENABLE_KANJI=1 でビルドすること）";
        return false;
#endif
    }

    m_err = (why == SAAN_G2P_ERR_UTF8)
        ? "不正な UTF-8。端末は UTF-8 しか受けない"
        : "かな中間表現として読めないのに中間表現の記号（[ ] # ° _ ^ $ ?）が混じっている";
    return false;
}

/* --- 1 発話ぶんの stream を開く（arena の二重防御つき）--------------------- */
static saan_stream g_st;
static saan_arena  g_a;

bool SanoTTS::openStream(int32_t nIds) {
    if (nIds > kMaxIds) {
        m_err = "入力が長すぎる（350 ids 超）。**喋らない** — 生徒が学習した分布の外になる";
        return false;
    }
    saan_pcm_reset();
    saan_arena_init(&g_a, g_arena, SANOTTS_ARENA_BYTES);
    if (saan_stream_init(&g_st, &g_w, &g_a, g_ids, nIds, m_sv) != SAAN_OK) {
        m_err = "saan_stream_init が失敗した";
        return false;
    }
    /* ⚠️ **init が OK でも黙って確保に失敗していることがある。**
     *    期待値はコアが同じ確保一覧から計算する（ポインタ幅の差も吸収される）。 */
    if (g_a.used != saan_stream_arena_used(nIds)) {
        m_err = "arena の確保が黙って失敗している（このまま pull すると再起動しうる）";
        return false;
    }
    return true;
}

/* --- PCM を返すだけ -------------------------------------------------------- */
bool SanoTTS::synthesize(const char* text, PcmCallback cb, void* user) {
    return synthesize(text, text ? strlen(text) : 0, cb, user);
}

bool SanoTTS::synthesize(const char* text, size_t nbytes, PcmCallback cb, void* user) {
    if (!m_ready) { m_err = "begin() を呼んでいない"; return false; }
    int32_t n_ids = 0;
    if (!toIds(text, nbytes, &n_ids)) return false;
    if (!openStream(n_ids)) return false;

    for (;;) {
        int32_t n = 0;
        const float* chunk = NULL;
        if (saan_stream_pull_ptr(&g_st, &chunk, &n) != SAAN_OK) {
            m_err = "saan_stream_pull_ptr が失敗した";
            return false;
        }
        if (n <= 0) break;
        const size_t ns = (size_t)n * SAAN_HOP;
        for (size_t i = 0; i < ns; ++i) g_i16[i] = saan_f32_to_i16(chunk[i]);
        if (cb) cb(g_i16, ns, user);
    }
    return true;
}

/* --- 鳴らす ---------------------------------------------------------------- */
bool SanoTTS::say(const char* text) {
    return say(text, text ? strlen(text) : 0);
}

bool SanoTTS::say(const char* text, size_t nbytes) {
    if (!m_ready) { m_err = "begin() を呼んでいない"; return false; }
    if (!m_spk)   { m_err = "setSpeaker() を呼んでいない"; return false; }

    int32_t n_ids = 0;
    if (!toIds(text, nbytes, &n_ids)) return false;
    if (!openStream(n_ids)) return false;

    const size_t preroll = m_spk->prerollSamples();
    if (!m_spk->beginUtterance(preroll)) {
        m_err = "スピーカーの beginUtterance() が失敗した";
        return false;
    }

    /* --- プリロール -------------------------------------------------------
     * ⚠️ **鳴らし始める前に貯める。** 初回 pull は受容野 38 フレームの warmup で
     *    定常の約 6 倍かかる。ここを省くと最初の 1 チャンクで必ず途切れる。 */
    bool eos = false;
    size_t filled = 0;
    while (!eos && filled + (size_t)SAAN_CHUNK * SAAN_HOP <= preroll) {
        int32_t n = 0;
        const float* chunk = NULL;
        if (saan_stream_pull_ptr(&g_st, &chunk, &n) != SAAN_OK) {
            m_err = "saan_stream_pull_ptr が失敗した"; m_spk->stop(); return false;
        }
        if (n <= 0) { eos = true; break; }
        const size_t ns = (size_t)n * SAAN_HOP;
        for (size_t i = 0; i < ns; ++i) g_i16[i] = saan_f32_to_i16(chunk[i]);
        if (!m_spk->prerollPush(g_i16, ns)) {
            m_err = "プリロールの容量が足りない（prerollSamples() を見直すこと）";
            m_spk->stop(); return false;
        }
        filled += ns;
    }

    if (!m_spk->start()) { m_err = "スピーカーの start() が失敗した"; m_spk->stop(); return false; }

    /* --- 定常 ------------------------------------------------------------- */
    while (!eos) {
        int32_t n = 0;
        const float* chunk = NULL;
        if (saan_stream_pull_ptr(&g_st, &chunk, &n) != SAAN_OK) {
            m_err = "saan_stream_pull_ptr が失敗した"; break;
        }
        if (n <= 0) break;
        const size_t ns = (size_t)n * SAAN_HOP;
        for (size_t i = 0; i < ns; ++i) g_i16[i] = saan_f32_to_i16(chunk[i]);
        if (!m_spk->write(g_i16, ns)) { m_err = "スピーカーの write() が失敗した"; break; }
    }

    m_spk->stop();
    return true;
}

/* --- 統計 ------------------------------------------------------------------ */
uint64_t SanoTTS::checksum() const { return saan_pcm_checksum(); }
uint32_t SanoTTS::samples()  const { return saan_pcm_samples(); }
int32_t  SanoTTS::absmax()   const { return saan_pcm_absmax(); }
