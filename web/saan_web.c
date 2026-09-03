/* W-0: ブラウザ（WebAssembly）から C99 コアと漢字経路を叩く薄い層。
 *
 * ⚠️ **ここに新しいロジックを書かない。** 経路判定（かな / 辞書 / 拒否）も G2P も
 *    合成も、既に `csrc/` と `esp32/main/` にある実装がやる。この .c の仕事は
 *    「`esp32/main/main.c` の `speak_auto()` → `speak_line()` / `speak_kanji()` →
 *    `synth_once()` と**同じ順序**で呼び、結果を JS が触れる形に置くこと」だけ。
 *    順序が同じであることが、端末と web で同じ列が出ることの根拠になる。
 *
 * ⚠️ **経路判定を JS 側に書かない**（計画 §2）。`saan_g2p_classify()` が唯一の規約で、
 *    ホストとの一致を `uv run python scripts/k1/kb_route_parity.py` が守っている。
 *    JS に「ひらがなっぽいから」を作った瞬間に入力仕様の目的が崩れる。
 *
 * --- JS 側の呼び方（凍結 ABI。11 関数だけ）---------------------------------
 *   1. `_saan_web_alloc(len)` を 2 回（重み用・辞書用）
 *   2. `Module.HEAPU8.set(bytes, ptr)` で中身を書く
 *   3. `_saan_web_init(mPtr, mLen, dPtr, dLen)`
 *   4. `lengthBytesUTF8` + `stringToUTF8` で文字列を入れて `_saan_web_synth(ptr, len)`
 *   5. `Module.HEAPF32.subarray(pcmPtr >> 2, (pcmPtr >> 2) + n)`
 *
 * ⚠️ **`Module.HEAPF32` は毎回 Module から読み直すこと。** メモリ拡張で detach する
 *    （この worktree の実測で `byteLength` が 0 になった）。`saan_web_synth()` は
 *    長い発話で PCM バッファを取り直すので、**拡張は普通に起きる**。
 *
 * ⚠️ **wasm の checksum を ESP32 の checksum と比べない。** float の丸めが違うので
 *    bit 一致は期待できない（CLAUDE.md / `esp32/main/saan_pcm.h`）。同じターゲット上の
 *    2 構成（かな経路 vs 辞書経路、`-msimd128` の有無）を比べるときだけ bit 一致を言える。
 */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <emscripten/emscripten.h>

#include "saanotts.h"
#include "saanotts_stream.h"
#include "g2p.h"
#include "jdict.h"
#include "saan_kanji.h"
#include "saan_pcm.h"
/* ⚠️ **ESP-IDF は要らない。** `saan_console.h` は `ESP_PLATFORM` が無いと
 *    `SAAN_INTERACTIVE 0` になり、driver を 1 本も引かない（stdbool / stddef / stdint だけ）。
 *    ここから取るのは `SAAN_CONSOLE_LINE_MAX` **1 個だけ** — 写しにすると
 *    main.c 側で伸ばしたときに web だけ 512 B のまま黙って短く拒否する。 */
#include "saan_console.h"

/* --- 動作点（ESP32 と同じ値にそろえる）------------------------------------
 *
 * ⚠️ **arena はブラウザなら緩められるが、緩めない。** 同じ 176 KB で動くことが
 *    そのまま「MCU に載る」の証拠になる（計画 §1）。`esp32/main/main.c:129` と同値。
 *
 * ⚠️ **これは写し。** `main.c` は ESP-IDF のヘッダを include するので、この .c から
 *    そのまま `#include` できない（`saan_console.h` だけは IDF を引かないので上で include している）。
 * ⚠️ **ずれると何が起きるか**: web を大きくすると「176 KB で動く = MCU に載る」の証拠が
 *    黙って崩れる（数字は出るし音も出るので気づけない）。小さくすると `SAAN_KANJI_WORKBYTES`
 *    を割った時点で**下の typedef がコンパイルを止める**（こちら側は静かには壊れない）。 */
#define SAAN_ARENA_BYTES (176 * 1024)

/* 受け付ける ids の上限。**arena の限界ではなく学習分布の上限**（`main.c:169` の同名 #define の写し）。
 * ⚠️ **`saan_kanji_to_ids()` は これを強制しない** — 109 文字で 582 ids を OK で返した
 *    （この worktree の実測）。`main.c` と同じ順序で**呼び出し側が**拒否する。
 * ⚠️ **ずれると何が起きるか**: 大きくすると D-017（max_spec_length=700 = 350 ids 相当）の
 *    外の入力が web だけ通り、**分布外の音が黙って出る**（端末は同じ行を拒否するので
 *    「web では喋れたのに実機では喋らない」になる）。 */
#define SAAN_MAX_IDS 350

/* 1 行の上限（バイト）。**`main.c` と同じ実体を使う**（`main.c:202` の
 * `SAAN_G2P_IDS_CAP (2 * SAAN_CONSOLE_LINE_MAX + 3)` と同じ形）。
 * ⚠️ **切り詰めない。** 超えたら行ごと拒否する（先頭だけ喋ると「ホストと端末で同じ列」が崩れる）。 */
#define SAAN_WEB_TEXT_MAX SAAN_CONSOLE_LINE_MAX

/* `saan_g2p_capacity()` と同じ式（配列サイズには関数を書けない）。実体との突き合わせは init で行う。 */
#define SAAN_WEB_IDS_CAP (2 * SAAN_WEB_TEXT_MAX + 3)

/* ⚠️ **漢字経路の作業領域が arena に収まるか**をコンパイル時に検査する（C99 に _Static_assert が
 *    無いので配列 typedef で潰す。落ちると「負のサイズの配列」で止まる）。
 *
 * ⚠️ **`main.c:158` の検査と式は同じ。** 向こうは
 *    `SAAN_ARENA_BYTES >= SAAN_KANJI_WORKBYTES + SAAN_KANJI_T10_BSS_BYTES` と書いてあるが、
 *    その第 2 項は `main.c:157` で **`0u`** に定義されている（T10(a) で 14,464 B が .bss から
 *    arena へ移り、`SAAN_KANJI_WORKBYTES` の中に入ったので、足すと二重計上になるため 0 にされた）。
 *    **だから「main.c には + 14,464 B が付く」は誤り。** 式は web と 1 文字も違わない。
 *
 * ⚠️ **違うのは式ではなく `SAAN_KANJI_WORKBYTES` の値の方**（実測。下の再現コマンド）:
 *      web   （`LABEL_IDS_EXTERNAL_SCRATCH` 未定義）136,448 B → 176 KB の余り 43,776 B
 *      ESP32 （component の CMakeLists が =1 を PUBLIC）146,688 B → 　　　余り 33,536 B
 *    差 **10,240 B = `LABEL_IDS_SCRATCH_BYTES`**（K-7 のトークン表 640 × 16）。web はこれを
 *    arena へ移さないので `csrc/label_ids.c` の .bss に残る。**arena 側に足すと二重計上。**
 *    ⚠️ 残り 4,224 B（`s_key` / `s_tok` / `s_lab`）は**どちらの構成でも arena**で、
 *       `SAAN_KANJI_WORKBYTES` に既に入っている。14,464 = 10,240 + 4,224 を丸ごと
 *       「web では .bss」と読むと 4,224 B ぶん間違える。
 *
 *    再現（この worktree で実測）:
 *      printf '#include <stdio.h>\n#include "saan_kanji.h"\nint main(void){printf("%%zu\\n",(size_t)SAAN_KANJI_WORKBYTES);}\n' > /tmp/wb.c
 *      cc -I csrc -I esp32/main /tmp/wb.c -o /tmp/wb && /tmp/wb                        # → 136448（web）
 *      cc -I csrc -I esp32/main -DLABEL_IDS_EXTERNAL_SCRATCH=1 /tmp/wb.c -o /tmp/wb && /tmp/wb   # → 146688（ESP32） */
typedef char saan_web_arena_holds_kanji[(SAAN_ARENA_BYTES >= SAAN_KANJI_WORKBYTES) ? 1 : -1];
#if defined(LABEL_IDS_EXTERNAL_SCRATCH) && LABEL_IDS_EXTERNAL_SCRATCH
#error "web ビルドは LABEL_IDS_EXTERNAL_SCRATCH を定義しない前提（arena の勘定が上の typedef とずれる）"
#endif

/* --- 戻り値 ---------------------------------------------------------------
 * 0 = OK / 負 = エラー。**理由は必ず `saan_web_message()` に日本語で入る。**
 * ⚠️ 番号は JS の分岐用であって、人に見せるのは message の方。 */
#define SAAN_WEB_OK              0
#define SAAN_WEB_ERR_ARG        (-1)   /* 引数が NULL / 長さが負 */
#define SAAN_WEB_ERR_ALIGN      (-2)   /* 16 バイト境界に無い（saan_web_alloc を通していない） */
#define SAAN_WEB_ERR_MODEL      (-3)   /* 重み blob が開けない */
#define SAAN_WEB_ERR_LANE       (-4)   /* fp32 blob を W8A8 レーンに渡した */
#define SAAN_WEB_ERR_DICT       (-5)   /* 辞書 blob が開けない */
#define SAAN_WEB_ERR_NOINIT     (-6)   /* init していない */
#define SAAN_WEB_ERR_EMPTY      (-7)   /* 空行 */
#define SAAN_WEB_ERR_TOO_LONG   (-8)   /* 入力が長すぎる / ids が上限超え */
#define SAAN_WEB_ERR_REJECT     (-9)   /* 経路判定が拒否 */
#define SAAN_WEB_ERR_G2P       (-10)   /* かな G2P が失敗 */
#define SAAN_WEB_ERR_KANJI     (-11)   /* 漢字 G2P が失敗 */
#define SAAN_WEB_ERR_NODICT    (-12)   /* 辞書経路だが辞書が無い */
#define SAAN_WEB_ERR_SYNTH     (-13)   /* 合成が失敗 */
#define SAAN_WEB_ERR_ARENA     (-14)   /* a.used が期待値と違う（黙って確保に失敗した） */
#define SAAN_WEB_ERR_OOM       (-15)   /* PCM バッファが取れない */
#define SAAN_WEB_ERR_NUL       (-16)   /* 入力に NUL バイトが混じっている */

/* 音素が 1 つも出なかったときの ids の個数。**枠だけ**（`^` + `_` + `$`）。
 * ⚠️ かな経路（`csrc/g2p.c:330` / `:370`）も辞書経路（`csrc/label_ids.c:224` / `:232`）も
 *    同じ形で囲む。**片方だけ変えると下の検査が静かに効かなくなる。** */
#define SAAN_WEB_IDS_FRAME_ONLY 3

/* --- 状態 ----------------------------------------------------------------- */

/* ⚠️ **16 バイト境界。** wasm に PIE は無いが、`saan_kanji.c` の `layout()` が
 *    「arena は 16 バイト境界で渡される前提」で全部の切り出しを並べる。 */
static __attribute__((aligned(16))) uint8_t g_arena[SAAN_ARENA_BYTES];

/* ⚠️ **arena の外**に置く。合成が arena を上書きするので、ids を arena に置くと
 *    pull の途中で自分の入力が消える。 */
static int32_t g_ids[SAAN_WEB_IDS_CAP];

/* 下流に渡す入力の**写し。必ず NUL 終端してから渡す。**
 *
 * ⚠️ 凍結 ABI は `saan_web_synth(text, nbytes)` =「`nbytes` までを読む」だが、
 *    辞書経路の下流にある `csrc/label_ids.c:50` の `question_type()` は **`strlen()`** で
 *    末尾を見る（EOS が `?` / `?!` / `?.` / `?~` のどれになるかを決めるところ）。
 *    **JS が終端してくれる保証は ABI のどこにも書いていない**ので、終端していない
 *    ポインタを渡されると**後続ヒープを読んで**EOS が入力と無関係に決まる。
 * ⚠️ wasm は境界例外を出さない（線形メモリの中ならただ読める）。**手元では動いてしまう。** */
static char g_text[SAAN_WEB_TEXT_MAX + 1];

/* pull 1 回ぶんの受け皿。`main.c` の `g_chunk` と同じ理由でスタックに置かない
 * （`saan_irfft_1024` が自動変数だけで 4 KB 使う）。 */
static float g_chunk[SAAN_CHUNK * SAAN_HOP];

static saan_weights g_w;
static jdict_t      g_dict;
static int          g_ready;      /* saan_web_init が成功した */
static int          g_dict_ok;    /* 辞書が開けた（無くても かな経路は動く） */

static float      *g_pcm;         /* 直前の発話の PCM（float / -1..1） */
static size_t      g_pcm_cap;     /* g_pcm の容量（サンプル数） */
static int32_t     g_n_samples;
static int32_t     g_n_ids;
static const char *g_route = "";
static int32_t     g_arena_used;

/* ⚠️ **成功時も空とは限らない。** 「黙って落ちたもの」（`ー` / `°`）と int16 クリップは
 *    ここにしか出ない。出さないと打った記号が反映されなかったことに気づけない。 */
static char g_msg[512];

static void msg_clear(void) { g_msg[0] = '\0'; }

static void msg_set(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_msg, sizeof g_msg, fmt, ap);
    va_end(ap);
}

/* 既にある文言の後ろに足す（警告を 2 つ出したいとき）。 */
static void msg_add(const char *fmt, ...) {
    const size_t n = strlen(g_msg);
    if (n + 1 >= sizeof g_msg) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_msg + n, sizeof g_msg - n, fmt, ap);
    va_end(ap);
}

/* 拒否の理由を「どの文字か」まで出す（`main.c:569-579` の `log_reject` と同じ形）。
 * ⚠️ **未知の記号が黙って無音になるのがこの入力仕様の一番危ない壊れ方**なので、
 *    必ず位置と文字を見せる。 */
static void msg_add_reject_char(const char *text, size_t nbytes, int32_t err_byte) {
    if (err_byte < 0 || (size_t)err_byte >= nbytes) return;
    /* err_byte から先の 1 文字（最大 4 B）。**何を消せばよいか分かるように。** */
    char ch[8] = {0};
    size_t k = 0;
    for (size_t i = (size_t)err_byte; i < nbytes && k < 4; ++i, ++k) {
        ch[k] = text[i];
        if (k > 0 && ((unsigned char)text[i] & 0xC0u) != 0x80u) { ch[k] = '\0'; break; }
    }
    msg_add("  受け付けない文字: \"%s\"（%d バイト目）", ch, (int)err_byte);
}

/* --- 1. 16 バイト境界のバッファ -------------------------------------------
 *
 * ⚠️ **`malloc` を使ってはいけない。** emcc 6.0.9 の `malloc` は 8 バイト境界しか
 *    返さない（この worktree で 6 サイズすべて `mod16 == 8` を実測）。
 *    `jdict` の `matrix` は `const int16_t *` として、`saan_arena` は 16 境界前提で読まれる。
 *    **wasm は境界例外を出さないので、ずれていても手元では動いてしまう。**
 * ⚠️ C11 の `aligned_alloc` は「size が alignment の倍数」を要求するので切り上げる。 */
EMSCRIPTEN_KEEPALIVE
void *saan_web_alloc(int nbytes) {
    if (nbytes <= 0) return NULL;
    const size_t n = ((size_t)nbytes + 15u) & ~(size_t)15u;
    void *p = aligned_alloc(16, n);
    if (!p) return NULL;
    /* `esp32/main/saan_dict.c:127-130` と同じ検査。**取れた後に必ず確かめる。** */
    if (((uintptr_t)p & 15u) != 0u) { free(p); return NULL; }
    return p;
}

/* --- 2. 起動時に 1 回 ------------------------------------------------------
 *
 * `dict` に NULL / 長さ 0 を渡すと**かな経路だけ**の構成になる（漢字文は拒否される）。
 * ⚠️ そのとき `saan_web_message()` に警告が残る。**成功 = 空文字ではない。** */
EMSCRIPTEN_KEEPALIVE
int saan_web_init(const void *model, int model_len,
                  const void *dict, int dict_len) {
    msg_clear();
    g_ready = 0; g_dict_ok = 0;
    g_n_samples = 0; g_n_ids = 0; g_arena_used = 0; g_route = "";

    if (!model || model_len <= 0) {
        msg_set("重み blob が渡されていない（model=%p / %d B）", model, model_len);
        return SAAN_WEB_ERR_ARG;
    }
    if (((uintptr_t)model & 15u) != 0u) {
        msg_set("重み blob が 16 バイト境界に無い。**saan_web_alloc() で取ること**"
                "（emcc の malloc は 8 バイト境界しか返さない）");
        return SAAN_WEB_ERR_ALIGN;
    }
    saan_status s = saan_weights_open(&g_w, model, (size_t)model_len);
    if (s != SAAN_OK) {
        msg_set("重み blob を開けない: %s（%d B）。"
                "⚠️ int8 blob は **v2**（S4 以降）でなければ SAAN_ERR_VERSION で拒否される",
                saan_strerror(s), model_len);
        return SAAN_WEB_ERR_MODEL;
    }

#if SAAN_INT8_ACT
    /* ⚠️ **W8A8 で fp32 blob を渡しても黙って動く**（W8A32 と同じ出力が出るだけで、
     *    速度が変わらない理由が分からないという最悪の壊れ方になる）。`main.c:808` と同じ検査。
     *    int8 blob だけが `<name>.scale` を持つ。 */
    {
        uint32_t dt = 0, d[4] = {0};
        uint64_t nb = 0;
        if (!saan_tensor(&g_w, "duration.blocks.0.c1.weight.scale", &dt, d, &nb)) {
            msg_set("W8A8 レーンなのに **fp32 blob** が渡された。この構成では int8 経路が"
                    "1 度も走らない。int8 blob（saanotts-jp-v3-int8.bin / 654,032 B）を渡すこと");
            return SAAN_WEB_ERR_LANE;
        }
    }
#endif

    /* ⚠️ **2 か所にある式は必ずずれる**（`main.c` の `boot_selftest` と同じ理由）。
     *    配列サイズのマクロ `SAAN_WEB_IDS_CAP` を実体 `saan_g2p_capacity()` と突き合わせる。 */
    if (SAAN_WEB_IDS_CAP < saan_g2p_capacity(SAAN_WEB_TEXT_MAX)) {
        msg_set("SAAN_WEB_IDS_CAP (%d) が saan_g2p_capacity() (%d) より小さい。"
                "saan_web.c の式が csrc/g2p.c とずれている",
                (int)SAAN_WEB_IDS_CAP, (int)saan_g2p_capacity(SAAN_WEB_TEXT_MAX));
        return SAAN_WEB_ERR_ARG;
    }

    if (dict && dict_len > 0) {
        if (((uintptr_t)dict & 15u) != 0u) {
            msg_set("辞書が 16 バイト境界に無い。**saan_web_alloc() で取ること**"
                    "（jdict の matrix を int16_t* として読むので黙って壊れる）");
            return SAAN_WEB_ERR_ALIGN;
        }
        if (jdict_open(&g_dict, (const uint8_t *)dict, (size_t)dict_len) != 0) {
            msg_set("辞書 blob を開けない（%d B）。k1-dict-438750.bin / 13,702,320 B を渡すこと",
                    dict_len);
            return SAAN_WEB_ERR_DICT;
        }
        if (!saan_kanji_init()) {
            msg_set("漢字経路の初期化に失敗した");
            return SAAN_WEB_ERR_DICT;
        }
        g_dict_ok = 1;
    } else {
        /* ⚠️ **失敗ではない**が、黙ると「漢字が拒否される理由」が分からなくなる。 */
        msg_set("⚠️ 辞書が無い構成。かな中間表現だけ喋れる（漢字・カタカナ・句読点は拒否される）");
    }

    g_ready = 1;
    return SAAN_WEB_OK;
}

/* --- 合成本体（`main.c` の `synth_once()`（:295-521）の順序をそのまま移す）---
 *
 * ⚠️ ESP32 側にある「プリロール / 定常ループ / アンダーラン計数」は**移さない**。
 *    あれは I2S に実時間で流すための構造で、ブラウザは 1 発話ぶんを AudioBuffer に
 *    渡すだけ。**pull の順序と回数は同じ**なので出力は変わらない。 */
static int synth_once(const int32_t *ids, int32_t n_ids) {
    /* ⚠️ **発話ごとに巻き戻す。** 呼ばないと 2 発話目の PCM 統計が「1 + 2 発話目」になり、
     *    しかも値は出るので気づけない（`esp32/main/saan_pcm.h`）。 */
    saan_pcm_reset();
    g_n_samples = 0;

    saan_arena a;
    /* ⚠️ **ここで巻き戻すのが要点。** 漢字経路の作業領域とこの arena は同じメモリなので、
     *    G2P が先、`saan_arena_init` が後。逆にすると合成中に上書きされて
     *    **それらしい音**が出る（例外は出ない）。 */
    saan_arena_init(&a, g_arena, SAAN_ARENA_BYTES);

    saan_stream st;
    saan_status s = saan_stream_init(&st, &g_w, &a, ids, n_ids, SAAN_S_V);
    if (s != SAAN_OK) {
        msg_set("saan_stream_init: %s（%d ids）", saan_strerror(s), (int)n_ids);
        return SAAN_WEB_ERR_SYNTH;
    }

    /* ⚠️ **黙って確保に失敗したのを検出する二重防御**（`main.c` と同じ）。
     *    ⚠️ **ホストで測った定数を写さないこと。** `a.used` は
     *    `sizeof(struct saan_stream_impl)` のポインタ幅で変わる（ホスト 64 bit /
     *    Xtensa 32 bit で 768 B ずれた実績。wasm32 も 32 bit）。
     *    必ず**その発話の n_ids で** `saan_stream_arena_used()` を呼ぶ。 */
    {
        const size_t used_expect = saan_stream_arena_used(n_ids);
        if (a.used != used_expect) {
            msg_set("saan_stream_init は OK を返したが a.used が %u B（期待 %u B）。"
                    "**確保が黙って失敗しているか、コアの確保一覧と "
                    "saan_stream_arena_used() がずれている**",
                    (unsigned)a.used, (unsigned)used_expect);
            return SAAN_WEB_ERR_ARENA;
        }
        g_arena_used = (int32_t)a.used;
    }

    /* PCM は発話ごとに必要量だけ持つ。⚠️ **固定の巨大配列を持たない**
     *    （350 ids × clip 上限 80 frames = 7,168,000 sample = 28.7 MB になる）。 */
    const size_t need = (size_t)st.n_frames * SAAN_HOP;
    if (need > g_pcm_cap) {
        float *p = (float *)saan_web_alloc((int)(need * sizeof(float)));
        if (!p) {
            msg_set("PCM バッファ %u sample を確保できない", (unsigned)need);
            return SAAN_WEB_ERR_OOM;
        }
        free(g_pcm);
        g_pcm = p;
        g_pcm_cap = need;
        /* ⚠️ ここで wasm のメモリが伸びうる。**JS 側の HEAPF32 は detach する。** */
    }

    size_t total = 0;
    for (;;) {
        int32_t n = 0;
        s = saan_stream_pull(&st, g_chunk, &n);
        if (s != SAAN_OK) {
            msg_set("saan_stream_pull: %s（%u sample まで）", saan_strerror(s), (unsigned)total);
            return SAAN_WEB_ERR_SYNTH;
        }
        if (n <= 0) break;
        /* ⚠️ `n` は**フレーム数**。サンプル数は n * SAAN_HOP */
        const size_t k = (size_t)n * SAAN_HOP;
        if (total + k > g_pcm_cap) {
            /* ⚠️ st.n_frames から取った容量を超えるのは、コアの規約が変わった印。 */
            msg_set("PCM が溢れた（%u + %u > 容量 %u sample）。"
                    "st.n_frames から取った容量とコアの出力が合っていない",
                    (unsigned)total, (unsigned)k, (unsigned)g_pcm_cap);
            return SAAN_WEB_ERR_SYNTH;
        }
        memcpy(g_pcm + total, g_chunk, k * sizeof(float));
        /* ⚠️ **統計は `esp32/main/saan_pcm.c` に任せる**（自前の FNV を書かない）。
         *    丸めを 2 か所に書くと checksum の突き合わせが黙って無意味になる
         *    （`saan_pcm.h` が「ここが唯一の実装」と明記している）。 */
        for (size_t i = 0; i < k; ++i) (void)saan_f32_to_i16(g_chunk[i]);
        total += k;
    }
    g_n_samples = (int32_t)total;

    if (saan_pcm_clip_count() > 0)
        msg_add("%s⚠️ int16 に直すと %u sample がクリップする（正規化はしない規約）",
                g_msg[0] ? " / " : "", (unsigned)saan_pcm_clip_count());
    return SAAN_WEB_OK;
}

/* --- 3. 合成 ---------------------------------------------------------------
 *
 * `main.c` の `speak_auto()`（:649-694）と同じ順序:
 *   空行 → classify → かな経路 `speak_line()` / 辞書経路 `speak_kanji()` / 拒否
 * ⚠️ **拒否をそのまま残す。** 「中間表現 + `。`」を黙って辞書経路に回すと
 *    `[` `]` `#` が読み上げられて**それらしい音**が出る（気づけない壊れ方）。 */
EMSCRIPTEN_KEEPALIVE
int saan_web_synth(const char *text, int nbytes) {
    msg_clear();
    g_n_samples = 0; g_n_ids = 0; g_arena_used = 0;
    g_route = "";

    if (!g_ready) {
        msg_set("saan_web_init() が済んでいない");
        return SAAN_WEB_ERR_NOINIT;
    }
    if (!text || nbytes < 0) {
        msg_set("引数が不正（text=%p / %d B）", (const void *)text, nbytes);
        return SAAN_WEB_ERR_ARG;
    }
    if (nbytes == 0) {
        msg_set("空行。かな中間表現か漢字かな交じり文を入力すること"
                "（例: きょ][おわよ][いて][んきです°ね / 今日は良い天気ですね。）");
        return SAAN_WEB_ERR_EMPTY;
    }
    if (nbytes > SAAN_WEB_TEXT_MAX) {
        /* ⚠️ **切り詰めない。** 先頭だけ喋ると「ホストと端末で同じ列」が崩れる。 */
        msg_set("入力が %d B で上限 %d B を超えた。**行ごと拒否した**（切り詰めていない）。"
                "短く区切ること", nbytes, (int)SAAN_WEB_TEXT_MAX);
        return SAAN_WEB_ERR_TOO_LONG;
    }

    const size_t n = (size_t)nbytes;

    /* ⚠️ **NUL が混じった入力は喋らない。** `nbytes` までを読むこの ABI と、下流の
     *    `question_type()` が見る `strlen` とで**末尾の判定が割れる**。
     *    実測（この worktree / W8A32 / 検査を外した版で再現）: `これは何ですか?` + NUL + `.`
     *    は nbytes 側では `?.` なのに question_type には `?` としか見えず、
     *    **同じ 43 ids のまま 25,600 sample になった**（`?.` は 22,784 / `?` は 25,600）。
     *    **EOS トークンが変わると duration が変わる。音は出るので気づけない。**
     * ⚠️ 同じ理由で**終端されていないバッファも危ない**。末尾が `?` の文を終端せずに渡すと
     *    question_type が後続ヒープまで読み、**25,600 → 22,016 sample に変わった**
     *    （同じく検査を外した版で実測）。だから下で必ず写して終端する。
     * ⚠️ **NUL で切り詰めない。** 先頭だけ喋るのは「ホストと端末で同じ列」を崩す
     *    （長すぎる行を切り詰めないのと同じ理由）。 */
    {
        const void *z = memchr(text, 0, n);
        if (z != NULL) {
            msg_set("入力の %d バイト目に NUL がある（全 %d B）。**喋らない**。"
                    "この ABI は nbytes までを読むが、下流の EOS 判定（? / ?! / ?. / ?~）は"
                    " strlen を見るので**2 つが割れる**",
                    (int)((const char *)z - text), nbytes);
            return SAAN_WEB_ERR_NUL;
        }
    }
    /* ⚠️ **ここから先は `text` ではなく `g_text` を渡す。** 終端の有無を呼び出し側に
     *    委ねない（上の NUL 検査で「中に無いこと」は保証したが、「末尾にあること」は
     *    保証できないため）。長さは上で `SAAN_WEB_TEXT_MAX` 以下に絞ってある。 */
    memcpy(g_text, text, n);
    g_text[n] = '\0';

    saan_g2p_status why = SAAN_G2P_OK;
    int32_t err_byte = -1;
    const saan_g2p_route route = saan_g2p_classify(g_text, n, &why, &err_byte);
    g_route = saan_g2p_route_name(route);

    int32_t n_ids = 0;
    /* 辞書経路が切った形態素の数。**−1 = かな経路（切っていない）。**
     * ⚠️ `saan_kanji_to_ids()` は受け取るのにこれまで一度も読んでいなかった。
     *    「入力が丸ごと落ちた」を人に見せられるのはこの数だけ。 */
    int n_tok = -1;

    if (route == SAAN_G2P_ROUTE_KANA) {
        if (saan_g2p_capacity(n) > SAAN_WEB_IDS_CAP) {
            msg_set("入力 %d B は長すぎる（ids バッファ %d 分）", nbytes, (int)SAAN_WEB_IDS_CAP);
            return SAAN_WEB_ERR_TOO_LONG;
        }
        saan_g2p_info gi;
        const saan_g2p_status gs = saan_g2p(g_text, n, g_ids, SAAN_WEB_IDS_CAP, &n_ids, &gi);
        if (gs != SAAN_G2P_OK) {
            /* ⚠️ **ここには来ないはず** — classify が同じトークナイザで「かな経路」と
             *    判定している。来たら 2 つがずれた印。 */
            msg_set("G2P 失敗: %s（%d バイト目）。**classify と saan_g2p がずれている**",
                    saan_g2p_strerror(gs), (int)gi.err_byte);
            if (gs == SAAN_G2P_ERR_UNKNOWN) msg_add_reject_char(g_text, n, gi.err_byte);
            return SAAN_WEB_ERR_G2P;
        }
        /* ⚠️ **黙って落ちたものを必ず出す。** `ー`（直前に平母音が無い）と
         *    `°`（直前が平母音でない）は例外を出さずに捨てられる規約なので、
         *    件数を見せないと「打ったのに反映されない」に気づけない。 */
        if (gi.n_dropped_long > 0 || gi.n_dropped_devoice > 0)
            msg_set("⚠️ 黙って落ちた: ー %d 個 / ° %d 個（直前が平母音でないと効かない規約）",
                    (int)gi.n_dropped_long, (int)gi.n_dropped_devoice);
    } else if (route == SAAN_G2P_ROUTE_DICT) {
        if (!g_dict_ok) {
            msg_set("この構成は辞書を持たないので、漢字・カタカナ・句読点は扱えない。");
            msg_add_reject_char(g_text, n, err_byte);
            return SAAN_WEB_ERR_NODICT;
        }
        n_tok = 0;
        const saan_kanji_status ks =
            saan_kanji_to_ids(&g_dict, g_text, n, g_arena, SAAN_ARENA_BYTES,
                              g_ids, SAAN_WEB_IDS_CAP, &n_ids, &n_tok);
        if (ks != SAAN_KANJI_OK) {
            msg_set("漢字 G2P 失敗: %s", saan_kanji_strerror(ks));
            return SAAN_WEB_ERR_KANJI;
        }
    } else {
        /* 拒否 */
        if (why == SAAN_G2P_ERR_UTF8) {
            msg_set("不正な UTF-8（%d バイト目）。UTF-8 しか受けない", (int)err_byte);
            return SAAN_WEB_ERR_REJECT;
        }
        msg_set("かな中間表現として読めないのに、中間表現の記号"
                "（[ ] # ° _ ^ $ ? ?! ?. ?~）が混じっている。**喋らない**。");
        msg_add_reject_char(g_text, n, err_byte);
        msg_add("  かな中間表現なら **ひらがな** と [ ] # ° ー っ ん と ? ?! ?. ?~ だけ"
                "（句読点 。、 は入れない）。漢字文なら中間表現の記号を消すこと");
        return SAAN_WEB_ERR_REJECT;
    }

    /* ⚠️ **音素ゼロ = 入力が丸ごと落ちた。** ids が `^ _ $` の 3 個だけになると
     *    「ほぼ無音」が **rc = 0 のまま**返る。実測（この worktree / emcc 6.0.9 / node v25.2.0。
     *    `Hello` / `test` / `2026` / `sanoTTS` / `a` / `。` / 空白 3 個 がどれも同じ値）:
     *
     *      レーン   落ちた入力            正常な `今日は良い天気ですね。`
     *      W8A32    3 ids / 2,560 sample / |max| 152    53 ids / 27,136 sample / |max| 9,529
     *      W8A8     3 ids / 2,560 sample / |max| 159    53 ids / 27,136 sample / |max| 9,627
     *
     *    2,560 sample = 22,050 Hz で **0.116 秒**、振幅は正常発話の **約 1/60 = −36 dB**。
     *    **耳では無音。聴いても気づけない壊れ方。**
     *
     *    かな経路には `gi.n_dropped_long` / `n_dropped_devoice` の報告があるのに、
     *    辞書経路には**「落ちた」を伝える口が 1 つも無かった**（`n_tok` は受け取るだけで
     *    読んでいなかった）。`g_msg` の規約「黙って落ちたものはここにしか出ない」を
     *    **両方の経路に**当てはめる。
     *
     * ⚠️ **rc は変えない（`SAAN_WEB_OK` のまま）。** 理由は 3 つ:
     *    (1) **かな経路の音素ゼロは成功扱いという規約が既にある。**
     *        `ー` 1 文字は「直前に平母音が無いので黙って消える」規約で 3 ids を返し、
     *        `saan_g2p()` は `SAAN_G2P_OK`（この worktree で実測）。辞書経路だけ
     *        拒否にすると**同じ「音素ゼロ」が経路で違う rc になる**。
     *    (2) `main.c` の `speak_kanji()`（:543-）も ids 3 個で `synth_once()` へ進む
     *        （形態素数をログに出すだけ）。**端末と web で挙動を分けない。**
     *    (3) `g_msg` は「成功時も空とは限らない」規約で、`web/main.js` の
     *        `setMarkedText(el.msg, msg)` が rc ≥ 0 でも必ず表示する。**警告は人に届く。**
     *        ⚠️ **JS 側でそこを消したら、この壊れ方は誰にも見えなくなる。**
     *    ⚠️ ここを `return SAAN_WEB_ERR_*` にするなら、**かな経路の `ー` も同時に**変えること。
     *       片方だけ直すと「経路によって同じ入力の rc が違う」に戻る。 */
    if (n_ids <= SAAN_WEB_IDS_FRAME_ONLY) {
        msg_add("%s⚠️ 音素が 1 つも出なかった（ids は ^ _ $ の %d 個だけ）。"
                "**入力が丸ごと落ちている。** ほぼ無音が出るが、これは合成結果ではない",
                g_msg[0] ? " / " : "", (int)n_ids);
        if (n_tok >= 0)
            msg_add("。辞書経路は形態素 %d 個に切ったが、読みのある語が 1 つも無かった"
                    "（ローマ字・数字・記号だけの行はここに落ちる）", n_tok);
    }

    /* ⚠️ **`saan_kanji_to_ids()` も `saan_g2p()` も 350 ids を強制しない。**
     *    `main.c` と同じ順序でここで拒否する（arena は 350 ids より先まで持つが、
     *    生徒が学習したのは D-017 の max_spec_length=700 = 350 ids 相当まで。
     *    その外は分布外で、黙って分布外の音を出すより拒否するほうが良い）。 */
    if (n_ids > SAAN_MAX_IDS) {
        msg_set("ids が %d 個で上限 %d を超えた。**喋らない**（短く区切ること）",
                (int)n_ids, (int)SAAN_MAX_IDS);
        return SAAN_WEB_ERR_TOO_LONG;
    }
    g_n_ids = n_ids;

    return synth_once(g_ids, n_ids);
}

/* --- 4. 直前の合成の結果 --------------------------------------------------- */

EMSCRIPTEN_KEEPALIVE const float *saan_web_pcm(void)         { return g_pcm; }
EMSCRIPTEN_KEEPALIVE int          saan_web_n_samples(void)   { return (int)g_n_samples; }
EMSCRIPTEN_KEEPALIVE int          saan_web_n_ids(void)       { return (int)g_n_ids; }
EMSCRIPTEN_KEEPALIVE const char  *saan_web_route(void)       { return g_route; }
EMSCRIPTEN_KEEPALIVE const char  *saan_web_message(void)     { return g_msg; }
EMSCRIPTEN_KEEPALIVE int          saan_web_arena_used(void)  { return (int)g_arena_used; }
EMSCRIPTEN_KEEPALIVE int          saan_web_sample_rate(void) { return SAAN_SR; }

/* 0 = W8A32（重みだけ int8）/ 1 = W8A8（活性化も int8）。
 * ⚠️ **ビルド時に決まる。** 同じ blob でも 2 本の wasm で PCM は違う
 *    （W8A8 は fp32 経路に対し held-out 24 文で最小 22.00 dB。M-55 の既知の劣化）。 */
EMSCRIPTEN_KEEPALIVE
int saan_web_lane(void) {
#if SAAN_INT8_ACT
    return 1;
#else
    return 0;
#endif
}
