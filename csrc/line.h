/* 端末からの 1 行入力（UTF-8 対応の行編集ステートマシン。C99 / 依存なし）
 *
 * ⚠️ **なぜ「1 バイトずつ食わせる」形なのか。** ESP32 側は UART から 1 バイトずつ
 *    しか取れず、`fgets` も `readline` も使えない。そして行編集をその場で書くと、
 *    **黙って入力を壊す**経路が 4 本できる（全部このプロジェクトの入力仕様に固有）:
 *
 *      1. **矢印キーが `[` を挿入する。** ESC [ A の `[` は中間表現では
 *         **上昇アクセントの記号**。カーソルを動かしただけでアクセントが変わり、
 *         **エラーも出ずに違う音が出る**。これが一番危ない
 *      2. **BS が UTF-8 の途中で切る。** ひらがなは 3 バイト。1 バイト消すと
 *         残りが不正な UTF-8 になり、`saan_g2p` が ERR_UTF8 を返す。
 *         「1 文字消しただけなのに行全体が拒否される」に見える
 *      3. **CRLF が空行を 1 つ余分に作る。** 端末によって CR / LF / CRLF が
 *         混ざるので、素直に書くと発話の後に必ず空行が 1 回入る
 *      4. **溢れたぶんを黙って捨てる。** 長い文を貼り付けると先頭だけ喋る。
 *         入力仕様の主目的（端末とホストで同じ列になる）が崩れる
 *
 *    どれも**音は出る**ので、動かしただけでは気づけない。だから状態機械を
 *    切り出してホストでゲートにかける（`make -C csrc line`）。
 *
 * 使い方:
 *     char buf[512]; saan_line ln;
 *     saan_line_reset(&ln, buf, sizeof buf);
 *     for (;;) {
 *         unsigned char c = read_one_byte();
 *         switch (saan_line_feed(&ln, c)) {
 *         case SAAN_LINE_DONE: handle(ln.buf, ln.len); break;   // 次の feed で自動的に消える
 *         case SAAN_LINE_EDIT: redraw(&ln); break;
 *         case SAAN_LINE_MORE: break;
 *         }
 *     }
 */
#ifndef SAAN_LINE_H
#define SAAN_LINE_H

#include <stddef.h>

typedef enum {
    SAAN_LINE_MORE = 0,  /* 何も起きていない（捨てたバイト・ESC の途中・溢れ） */
    SAAN_LINE_DONE = 1,  /* 行が完成した。buf は NUL 終端、長さは len */
    SAAN_LINE_EDIT = 2,  /* buf が縮んだ。呼び出し側は表示を書き直すこと */
    SAAN_LINE_ECHO = 3   /* 1 バイト受理した。**そのバイトをそのままエコーする** */
} saan_line_event;

/* ⚠️ **エコーの判定を呼び出し側で組み立てないこと。**
 *
 *    かつて `feed の前後で len が増えたか` で判定していたが、DONE の次の
 *    バイトでは中身が自動的に消えるので `before` が前の行の長さになり、
 *    **行の先頭 1 文字がエコーされなかった**（QEMU で実際に踏んだ。
 *    `かな> ��んにちわ` と出るのに、合成される列は正しい = **画面だけが嘘をつく**）。
 *    ⚠️ しかも CRLF を送る端末では `\n` が自動クリアを担うので**再現しない**。
 *    エコーすべきかどうかは状態機械しか正しく知らないので、ここで返す。 */

typedef struct {
    char   *buf;
    size_t  cap;         /* buf の総バイト数。NUL 終端に 1 使うので実効は cap-1 */
    size_t  len;         /* 現在の長さ（NUL を含まない） */
    int     overflow;    /* 1 = 入りきらないバイトを捨てた。**行を拒否する根拠** */
    int     done;        /* 1 = 直前に DONE を返した（次の feed で自動的に消す） */
    unsigned char esc;   /* ESC シーケンスの状態: 0 通常 / 1 ESC 直後 / 2 CSI 中 */
    unsigned char cr;    /* 1 = 直前が CR。次の LF を吸って空行を作らない */
} saan_line;

/* `buf` は呼び出し側が持つ。`cap >= 2` を要求する（NUL に 1 使うため）。 */
void saan_line_reset(saan_line *ln, char *buf, size_t cap);

/* 1 バイト食わせる。**`ln->buf[cap-1]` より先には 1 バイトも書かない。** */
saan_line_event saan_line_feed(saan_line *ln, unsigned char c);

#endif /* SAAN_LINE_H */
