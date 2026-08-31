/* K-7: フルコンテキストラベル列 → 生徒インデックス列。
 *
 * K-6 までで「漢字文 → ラベル」は通った。ここはその先で、
 * `piper_plus_g2p/japanese.py` の `_phonemize_core()` を写したもの。
 *
 *   ラベル列 → 音素 + アクセント記号（`[` `]` `#` `_`）→ `ん` の異音
 *   → 生徒インデックス（**トークン間に PAD を挟む**）
 *
 * ⚠️ **PAD の挟み方を canonical と同じにする。** 外すと発話が約 2.4 倍速に
 *    なるが例外は出ない（C-007）。規則は `csrc/g2p.c` の `emit()` と同一:
 *    「その音素自身が PAD なら後ろに挟まない」。
 *
 * ⚠️ **疑問符の種類は元テキストから決まる**（`?` `?!` `?.` `?~` の 4 種）。
 *    ラベルには入っていないので、テキストも渡す。
 */
#ifndef SAAN_K7_LABEL2IDS_H
#define SAAN_K7_LABEL2IDS_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
    K7_OK = 0,
    K7_ERR_OVERFLOW = -1,   /* ids バッファが足りない */
    K7_ERR_TOKEN    = -2,   /* 語彙に無い音素が出た */
    K7_ERR_ARG      = -3
} k7_status;

/* labels[0..n_labels) と元テキストから ids を作る。
 * `n_ids` には必要な総数が返る（cap を超えても数える）。 */
k7_status k7_label2ids(const char *const *labels, int n_labels,
                       const char *text,
                       int32_t *ids, int32_t ids_cap, int32_t *n_ids);

/* 音素名 → 生徒インデックス。無ければ -1。 */
int32_t k7_token_id(const char *name);

const char *k7_strerror(k7_status s);

/* 語彙のトークン数（`k7_table.h` の K7_N_TOKENS と同じ値）。 */
extern const int k7_n_tokens;

/* ⚠️ **ゲートの陰性対照専用。** 1 にすると上昇記号 `[` を出さなくなる。
 *    出荷経路では 0 のまま。これが無いと「一致した」を
 *    「検査が効いていない」と区別できない。 */
extern int k7_debug_drop_rise;

#endif /* SAAN_K7_LABEL2IDS_H */
