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
 * ⚠️ **トークン数の上限は 96。** 端末は ids 350 個までしか喋らない
 *    （`SAAN_MAX_IDS`）ので、1 文あたりの形態素はそれよりずっと少ない
 *    （K-5 の最長文 98 文字で 67 個）。 */
#define SAAN_KANJI_MAX_TOK    96
#define SAAN_KANJI_MAX_LABEL  512
#define SAAN_KANJI_KEY_MAX    1024
#define SAAN_KANJI_FEAT_MAX   320
#define SAAN_KANJI_VITERBI_N  (48u * 1024u)

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
 * （マクロは静的検査用、関数は実行時のログ用。`make -C csrc k7` と check_esp32_template.sh §10 が両方を見る）。 */
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
