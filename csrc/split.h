/* 長文を文末記号で区切る（C99 / 依存なし / I/O なし）— D-070
 *
 * **なぜ在るか。** 端末は 1 発話を形態素 44 個 / 350 ids までしか受けない
 * （`SAAN_KANJI_MAX_INPUT_TOK` / `SAAN_MAX_IDS`。生徒が学習した分布の外を黙って出さないため）。
 * それを超える文章を「喋らない」のではなく、**文ごとに区切って順に喋る**ための部品。
 *
 * ⚠️ **上限は緩めない。** 区切ったあとの各区間にも同じ上限が掛かる。
 * ⚠️ **全か無か。** 1 区間でも変換できない・上限を超えるなら、1 区間も喋らない。
 * ⚠️ **上限内の入力は 1 命令も違う経路を通らない**（`saan_split_say` の手順 1）。
 *
 * 規則（`make -C csrc split` の G-SP1 / G-SP2 が固定する）:
 *   - 区間の終わり = 開始記号（。！？!?）に続く「終端の並び」の最後。終端の並びは
 *     開始記号・．・.・〜(U+301C)・～(U+FF5E)・… と閉じ括弧 」』）)】 が続く限り伸びる
 *     （疑問 EOS の ?! ?. ?~ を区間の末尾に残すため。G33）
 *   - ⚠️ `.` と `．` は**開始記号にしない**（`3.14` を切らないため）
 *   - 区間の先頭の空白（' ' \t \r \n U+3000）は飛ばす
 *   - 中身（空白・終端の並びの文字・閉じ括弧のどれでもない文字）を持たない区間は、
 *     次の区間の頭にくっつける。末尾に残った中身の無い断片は捨てる
 */
#ifndef SAAN_SPLIT_H
#define SAAN_SPLIT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { int32_t off; int32_t len; } saan_span;   /* text からのバイト位置 */

typedef enum {
    SAAN_SPLIT_OK = 0,
    SAAN_SPLIT_END = 1,          /* もう区間が無い */
    SAAN_SPLIT_ERR_UTF8_NEXT = -1 /* *err_byte に位置 */
} saan_split_next_status;

/* text[*pos..nbytes) の次の区間を *out に返し、*pos を区間の後ろへ進める。 */
saan_split_next_status saan_split_next(const char *text, size_t nbytes, size_t *pos,
                                       saan_span *out, int32_t *err_byte);

#ifdef __cplusplus
}
#endif

#endif /* SAAN_SPLIT_H */
