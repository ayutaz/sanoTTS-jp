/* K-7: 漢字かな交じり文 → 生徒インデックス列（端末側の全段）。
 *
 *   文 → k1_analyze（K-2/K-3）→ k1_entry_feature → mecab2njd
 *      → 8 段（K-4b）→ k4_apply（K-4）→ njd2jpcommon → make_label
 *      → k7_label2ids
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
#include "k1dict.h"
#include "k4_accent.h"   /* k4_node_t（作業領域の大きさを静的に出すため） */

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
#define SAAN_KANJI_FEAT_MAX   320
#define SAAN_KANJI_VITERBI_N  (48u * 1024u)
#define SAAN_KANJI_WORKBYTES \
    ((size_t)SAAN_KANJI_MAX_TOK * SAAN_KANJI_FEAT_MAX \
     + sizeof(k4_node_t) * SAAN_KANJI_MAX_TOK + SAAN_KANJI_VITERBI_N)

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

/* 確保する作業領域のバイト数（= SAAN_KANJI_WORKBYTES。ログ用）。 */
size_t saan_kanji_workbytes(void);

/* 作業領域は呼び出し側が渡す（Viterbi 用。K-2 の arena）。 */
saan_kanji_status saan_kanji_to_ids(const k1_dict_t *d,
                                    const char *text, size_t nbytes,
                                    void *arena, size_t arena_n,
                                    int32_t *ids, int32_t ids_cap,
                                    int32_t *n_ids, int *n_tokens);

const char *saan_kanji_strerror(saan_kanji_status s);

#endif /* SAAN_KANJI_H */
