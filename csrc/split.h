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

/* --- 手順（saan_split_say）------------------------------------------------------
 *
 * 1. to_ids(全体)。0 かつ n ≤ max_ids → speak(0, 1)。**上限内はここで終わる = 今と同じ経路**
 * 2. 0 かつ n > max_ids、または SAAN_SPLIT_CB_TOO_LONG → 区切る。それ以外の負値 → ERR_TO_IDS
 *    （⚠️ 長さ以外の失敗では区切らない。未知の文字などを区切ってごまかさない）
 * 3. 区間を全部数える（> SAAN_SPLIT_MAX_SPANS なら ERR_SPANS）。各区間に to_ids を 1 回ずつ呼んで
 *    検査する。失敗 or n > max_ids → ERR_SEGMENT（**何も喋っていない**）
 * 4. 全部通ったら、区間 k ごとに to_ids(区間 k) → speak(k, N)。speak が失敗したら ERR_SPEAK で止める
 *    （⚠️ to_ids を 2 回呼ぶのは、ids のバッファが 1 本しか無い呼び出し側に合わせるため）
 */
#define SAAN_SPLIT_MAX_SPANS 64
#define SAAN_SPLIT_CB_TOO_LONG (-1000)      /* to_ids が「長すぎて変換できない」ときに返す */
#define SAAN_SPLIT_CB_TOO_MANY_IDS (-1001)  /* saan_split_say が bad_code に入れる（n_ids > max_ids） */

/* 0 = OK（*n_ids に書く）/ SAAN_SPLIT_CB_TOO_LONG / それ以外の負値 = 変換できない */
typedef int (*saan_split_to_ids_fn)(void *ctx, const char *seg, size_t len, int32_t *n_ids);
/* 直前の to_ids が作った列を合成して鳴らす。0 = OK。index は 0 始まり */
typedef int (*saan_split_speak_fn)(void *ctx, int32_t index, int32_t n_spans);

typedef enum {
    SAAN_SPLIT_SAID = 0,
    SAAN_SPLIT_ERR_UTF8 = -1,      /* 区切る途中で不正な UTF-8。何も喋っていない */
    SAAN_SPLIT_ERR_TO_IDS = -2,    /* 全体が長さ以外で失敗。bad_code にそのコード */
    SAAN_SPLIT_ERR_SEGMENT = -3,   /* 区間 bad_index が変換できない / 上限超え。何も喋っていない */
    SAAN_SPLIT_ERR_SPANS = -4,     /* 区間が SAAN_SPLIT_MAX_SPANS を超えた。何も喋っていない */
    SAAN_SPLIT_ERR_SPEAK = -5      /* 区間 bad_index の speak が失敗。それより前は喋った */
} saan_split_status;

typedef struct {
    int32_t n_spans;     /* 喋った（喋ろうとした）区間の数。上限内なら 1 */
    int32_t bad_index;   /* 失敗した区間（0 始まり）。無ければ -1 */
    int bad_code;        /* to_ids / speak の戻り値、または SAAN_SPLIT_CB_TOO_MANY_IDS */
    saan_span bad_span;  /* 失敗した区間の位置（ERR_SEGMENT / ERR_SPEAK） */
} saan_split_result;

saan_split_status saan_split_say(const char *text, size_t nbytes, int32_t max_ids,
                                 saan_split_to_ids_fn to_ids, saan_split_speak_fn speak,
                                 void *ctx, saan_split_result *res);
const char *saan_split_strerror(saan_split_status s);

#ifdef __cplusplus
}
#endif

#endif /* SAAN_SPLIT_H */
