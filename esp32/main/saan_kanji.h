/* K-7: 漢字かな交じり文 → 生徒インデックス列（端末側の全段）。
 *
 *   文 → jdict_analyze（K-2/K-3）→ jdict_entry_feature → mecab2njd
 *      → 8 段（K-4b）→ accent_apply（K-4）→ njd2jpcommon → make_label
 *      → label_ids_convert
 *
 * ⚠️ **ホスト（フル辞書）とは一致しない。** 枝刈りの分だけ必ず食い違う
 *    （実測 17.79% の文。M-74 / C-050）。**それは既知の代償**であって欠陥ではない。
 * ⚠️ **malloc を使う。** 取り込んだ Open JTalk が NJD / JPCommon を
 *    calloc で組むので避けられない。1 文あたりのピークは K-5 で実測（104,589 B）。
 */
#ifndef SAAN_KANJI_H
#define SAAN_KANJI_H

#include <stddef.h>
#include <stdint.h>
#include "jdict.h"
#include "accent.h"     /* accent_node_t（作業領域の大きさを静的に出すため） */
#include "label_ids.h" /* LABEL_IDS_SCRATCH_BYTES（T10(a) で arena へ移した分） */

/* 作業領域の寸法。⚠️ **saan_kanji_workbytes() と 1:1**（関数はこの式をそのまま返す）。
 * マクロで持つのは、雛形（esp32/main/main.c）が `SAAN_ARENA_BYTES ≥ SAAN_KANJI_WORKBYTES + 14,464`
 * をコンパイル時に検査し、scripts/check_esp32_template.sh がホストで同じ式を評価するため（計画 T4）。
 *
 * ⚠️ **Viterbi の作業領域は 32 KB あれば held-out 298 文すべてで足りる**
 *    （16 KB だと 1 文落ちる。ホストで実測）。余裕を見て 48 KB 取る。
 * ⚠️ **トークン数の上限は 96。** ただしこれは**配列の寸法**であって
 *    入力の上限ではない（NJD 段はノードを増やすので下げられない）。
 *    **入力の上限は `SAAN_KANJI_MAX_INPUT_TOK`（44）**で、そちらが
 *    Open JTalk の一時ヒープを縛る（下の節。M-98）。 */
#define SAAN_KANJI_MAX_TOK    96
#define SAAN_KANJI_MAX_LABEL  512
#define SAAN_KANJI_KEY_MAX    1024
#define SAAN_KANJI_FEAT_MAX   320
#define SAAN_KANJI_VITERBI_N  (48u * 1024u)

/* --- Open JTalk の一時ヒープを縛る（M-98）---------------------------------
 *
 * ⚠️ **これは arena ではない。** `mecab2njd` 以降が calloc / strdup で取る分で、
 *    PSRAM がある板では PSRAM に落ちるが（oj_heap_psram.c）、
 *    **PSRAM の無い板では内部 DRAM から来る**。
 *
 * QEMU（PSRAM 無し）で文長を伸ばして低水位を測ると、**ids に比例する**:
 *
 *     OJ ヒープ = 197.6 B × ids + 924 B      （n=6 / R² = 0.99905。M-98）
 *
 * そして held-out 2,328 文で **ids ≤ 8 × 形態素 + 54** が例外なく成り立つ
 * （包絡。中央値は 8.07 ids/形態素）。この 2 つを繋ぐと、
 * **NJD 段に入る前に形態素数で縛れば OJ ヒープが縛れる**。
 *
 * ⚠️ **縛る場所はここしかない。** `jdict_analyze` は arena しか使わないので
 *    形態素数が分かった時点ではまだ 1 バイトも malloc していない。
 * ⚠️ **`SAAN_KANJI_MAX_TOK` を下げてはいけない。** あれは NJD 段の配列の寸法も
 *    兼ねていて、`njd_set_digit` などが**ノードを増やす**ので、下げると
 *    アクセント段が黙って切れる。入力トークンの上限は**別に持つ**。 */
#define SAAN_KANJI_IDS_PER_TOK   8      /* 包絡の傾き（実測 8.07 中央 / 8.84 最大） */
#define SAAN_KANJI_IDS_CONST     54     /* 包絡の切片（held-out 2,328 文で最小） */
#define SAAN_KANJI_OJ_B_PER_IDS  198    /* 197.6 を切り上げ */
#define SAAN_KANJI_OJ_B_CONST    1024   /* 924 を切り上げ */

/* 入力 1 文の形態素の上限。**44 で held-out の「喋れる文」を 6/2,314 = 0.26% 落とす**
 * （落ちるのは形態素 45/45/45/46/46/54 の 6 文。実測であって内挿ではない）。
 * ⚠️ 96 なら 0% だが、96 は OJ ヒープ 163,780 B を許してしまい、PSRAM 無しの
 *    実測空き 103,140 B を超える = **M-98 で踏んだ穴**。
 * ⚠️ 44 は `SAAN_KANJI_OJ_BUDGET_BYTES` 80 KB から逆算した上限でもある
 *    （45 にすると SAAN_KANJI_OJ_MAX_BYTES が予算を超え、下の _Static_assert で止まる）。 */
#ifndef SAAN_KANJI_MAX_INPUT_TOK
#define SAAN_KANJI_MAX_INPUT_TOK 44
#endif

/* その上限で OJ ヒープが最大いくつになるか（**上の包絡から算出**）。 */
#define SAAN_KANJI_OJ_MAX_BYTES \
    ((size_t)SAAN_KANJI_OJ_B_PER_IDS \
     * ((size_t)SAAN_KANJI_IDS_PER_TOK * SAAN_KANJI_MAX_INPUT_TOK \
        + SAAN_KANJI_IDS_CONST) \
     + SAAN_KANJI_OJ_B_CONST)

/* 内部 DRAM から OJ ヒープに回してよい予算。**PSRAM 無しの板向け。**
 * 既定 80 KB は QEMU 実測の空き 103,140 B（M-98）に 21 KB の余裕を残す値。 */
#ifndef SAAN_KANJI_OJ_BUDGET_BYTES
#define SAAN_KANJI_OJ_BUDGET_BYTES (80u * 1024u)
#endif

/* 16 B 境界への切り上げ（saan_kanji.c の KJ_ALIGN16 と同じ）。 */
#define SAAN_KANJI_A16(x) ((((size_t)(x)) + 15u) & ~(size_t)15u)

/* K-7 のトークン表を arena から渡す構成（K-A / T10(a)）では、その分もここに入る。
 * ⚠️ **component の CMakeLists が LABEL_IDS_EXTERNAL_SCRATCH=1 を PUBLIC で定義する。**
 *    定義されていないビルドでは label_ids.c が自分の .bss を使うので 0。 */
#if defined(LABEL_IDS_EXTERNAL_SCRATCH) && LABEL_IDS_EXTERNAL_SCRATCH
#define SAAN_KANJI_K7_SCRATCH LABEL_IDS_SCRATCH_BYTES
#else
#define SAAN_KANJI_K7_SCRATCH ((size_t)0)
#endif

#define SAAN_KANJI_WORKBYTES \
    (SAAN_KANJI_A16((size_t)SAAN_KANJI_MAX_TOK * SAAN_KANJI_FEAT_MAX) \
     + SAAN_KANJI_A16(sizeof(accent_node_t) * SAAN_KANJI_MAX_TOK) \
     + SAAN_KANJI_A16((size_t)SAAN_KANJI_KEY_MAX) \
     + SAAN_KANJI_A16(sizeof(jdict_token_t) * SAAN_KANJI_MAX_TOK) \
     + SAAN_KANJI_A16(sizeof(const char *) * SAAN_KANJI_MAX_LABEL) \
     + SAAN_KANJI_A16(SAAN_KANJI_K7_SCRATCH) \
     + SAAN_KANJI_VITERBI_N)

typedef enum {
    SAAN_KANJI_OK = 0,
    SAAN_KANJI_ERR_KEY      = -1,  /* 鍵に符号化できない */
    SAAN_KANJI_ERR_ANALYZE  = -2,  /* 経路が張れない */
    SAAN_KANJI_ERR_FEATURE  = -3,  /* 素性を復元できない */
    SAAN_KANJI_ERR_IDS      = -4,  /* ids の生成に失敗 */
    SAAN_KANJI_ERR_TOO_LONG = -5   /* 内部バッファを超えた */
} saan_kanji_status;

/* 起動時に 1 回。作業領域を確保する（PSRAM 優先）。0 なら失敗。
 * ⚠️ **.bss には置けない**（DRAM が 419 KB 足りない。G19 で実測）。 */
int saan_kanji_init(void);

/* 確保する作業領域のバイト数（**最低限これだけ要る**）。SAAN_KANJI_WORKBYTES と同じ値を返す
 * （マクロは静的検査用、関数は実行時のログ用。`make -C csrc label-ids` と check_esp32_template.sh §10 が両方を見る）。 */
size_t saan_kanji_workbytes(void);

/* arena が `arena_n` バイトのとき、実際に Viterbi へ渡るバイト数（ログ用）。
 * ⚠️ **workbytes() と違って「余りも全部渡す」実際の値**。T10(a) で
 *    固定長の配列を arena へ移したぶんここが減るので、起動ログに出して
 *    「減りすぎていないか」を人が見られるようにしてある。 */
size_t saan_kanji_vitbytes(size_t arena_n);

/* 作業領域は呼び出し側が渡す（Viterbi 用。K-2 の arena）。 */
saan_kanji_status saan_kanji_to_ids(const jdict_t *d,
                                    const char *text, size_t nbytes,
                                    void *arena, size_t arena_n,
                                    int32_t *ids, int32_t ids_cap,
                                    int32_t *n_ids, int *n_tokens);

const char *saan_kanji_strerror(saan_kanji_status s);

#endif /* SAAN_KANJI_H */
