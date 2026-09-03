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

/* 確保する作業領域のバイト数（**最低限これだけ要る**）。 */
size_t saan_kanji_workbytes(void);

/* arena が `arena_n` バイトのとき、実際に Viterbi へ渡るバイト数（ログ用）。
 * ⚠️ **workbytes() と違って「余りも全部渡す」実際の値**。T10(a) で
 *    固定長の配列を arena へ移したぶんここが減るので、起動ログに出して
 *    「減りすぎていないか」を人が見られるようにしてある。 */
size_t saan_kanji_vitbytes(size_t arena_n);

/* 作業領域は呼び出し側が渡す（Viterbi 用。K-2 の arena）。 */
saan_kanji_status saan_kanji_to_ids(const k1_dict_t *d,
                                    const char *text, size_t nbytes,
                                    void *arena, size_t arena_n,
                                    int32_t *ids, int32_t ids_cap,
                                    int32_t *n_ids, int *n_tokens);

const char *saan_kanji_strerror(saan_kanji_status s);

#endif /* SAAN_KANJI_H */
