/* 行編集ステートマシンの受け入れゲート（csrc/line.c）。
 *
 *   ./line_test
 *
 * ⚠️ **「0 件でした」は「安全」ではない。** このゲートは、わざと素直に書いた
 *    実装 `naive_feed()` を同居させ、**同じベクタでそれが落ちること**を検査する。
 *    落ちなければベクタが空虚なので、たとえ本物が全部通っても NG を返す。
 *
 *    `naive_feed()` は「普通に書くとこうなる」を再現したもの:
 *      - BS を 1 バイトだけ消す        → UTF-8 が壊れる
 *      - ESC を素通しする              → 矢印キーが `[` を挿入する
 *      - CRLF を 2 行として扱う        → 発話のたびに空行が 1 回入る
 *      - 溢れたら黙って切り詰める      → 長文の貼り付けで先頭だけ喋る
 *
 * ⚠️ **見ていないもの**: UART から本当に 1 バイトずつ取れるか（IDF の API）、
 *    端末のエコーが読める見た目になるか、全角の表示幅。**これは実機でしか見えない。**
 */
#include "line.h"

#include <stdio.h>
#include <string.h>

/* --- 陽性対照: わざと素直に書いた実装 ------------------------------------ */
static saan_line_event naive_feed(saan_line *ln, unsigned char c) {
    if (ln->done) { ln->done = 0; ln->len = 0; ln->overflow = 0; ln->buf[0] = '\0'; }
    if (c == '\r' || c == '\n') {                 /* CRLF を吸わない */
        ln->buf[ln->len] = '\0'; ln->done = 1; return SAAN_LINE_DONE;
    }
    if (c == 0x08u || c == 0x7Fu) {               /* 1 バイトだけ消す */
        if (ln->len > 0) { --ln->len; ln->buf[ln->len] = '\0'; return SAAN_LINE_EDIT; }
        return SAAN_LINE_MORE;
    }
    if (ln->len + 1 >= ln->cap) return SAAN_LINE_MORE;   /* 黙って捨てる（印を立てない） */
    ln->buf[ln->len++] = (char)c;                 /* ESC も素通し */
    ln->buf[ln->len] = '\0';
    return SAAN_LINE_MORE;
}

/* --- 駆動 ----------------------------------------------------------------- */
#define CAP        64
#define MAX_LINES  8
#define CANARY     0x5Au

typedef saan_line_event (*feed_fn)(saan_line *, unsigned char);

typedef struct {
    char   line[MAX_LINES][CAP];
    size_t len[MAX_LINES];
    int    overflow[MAX_LINES];
    /* ⚠️ **エコーされたバイト列を行ごとに別に貯める。**
     *    「合成される列」と「画面に出る列」は別物で、片方だけ壊れる
     *    （実際に踏んだ: 行の先頭 1 文字だけエコーされなかった）。 */
    char   echo[MAX_LINES][CAP];
    size_t echo_len[MAX_LINES];
    int    n;
    int    edits;
    int    canary_ok;
} capture;

/* `reset_each` = 1 は「1 行読むたびに saan_line_reset() する」呼び出し側を再現する。
 * ⚠️ **これは QEMU で実際に踏んだ壊し方**（G9 で対照として使う）。 */
static void drive_ex(feed_fn f, const unsigned char *in, size_t n, size_t cap,
                     int reset_each, capture *out) {
    static char buf[CAP + 1];
    saan_line ln;
    memset(buf, 0, sizeof buf);
    buf[cap] = (char)CANARY;                       /* buf[cap] より先に書いたら壊れる */
    saan_line_reset(&ln, buf, cap);
    memset(out, 0, sizeof *out);

    /* 呼び出し側が画面に出す列を、**イベントだけを見て**組み立てる。
     * EDIT（BS など）では表示を書き直すので、貯めた分を捨てて buf から作り直す。 */
    char   echo[CAP];
    size_t echo_n = 0;

    for (size_t i = 0; i < n; ++i) {
        saan_line_event ev = f(&ln, in[i]);
        if (ev == SAAN_LINE_ECHO) {
            if (echo_n < sizeof echo) echo[echo_n++] = (char)in[i];
        } else if (ev == SAAN_LINE_EDIT) {
            ++out->edits;
            echo_n = ln.len < sizeof echo ? ln.len : sizeof echo;  /* 書き直し */
            memcpy(echo, ln.buf, echo_n);
        }
        if (ev == SAAN_LINE_DONE && out->n < MAX_LINES) {
            memcpy(out->line[out->n], ln.buf, ln.len);
            out->line[out->n][ln.len] = '\0';
            out->len[out->n] = ln.len;
            out->overflow[out->n] = ln.overflow;
            memcpy(out->echo[out->n], echo, echo_n);
            out->echo[out->n][echo_n] = '\0';
            out->echo_len[out->n] = echo_n;
            ++out->n;
            echo_n = 0;
            if (reset_each) saan_line_reset(&ln, buf, cap);
        }
    }
    out->canary_ok = (buf[cap] == (char)CANARY);
}

static void drive(feed_fn f, const unsigned char *in, size_t n, size_t cap, capture *out) {
    drive_ex(f, in, n, cap, 0, out);
}

/* --- 検査項目 ------------------------------------------------------------- */
static int g_fail;
static int g_quiet;

static void chk(int cond, const char *what) {
    if (!cond) ++g_fail;
    if (!g_quiet) printf("  %s  %s\n", cond ? "OK " : "NG!", what);
}

#define S(x) (const unsigned char *)(x), sizeof(x) - 1

/* 戻り値 = 落ちた項目数 */
static int run(feed_fn f, int quiet) {
    capture c;
    g_fail = 0;
    g_quiet = quiet;

    /* G1 正常系: 中間表現がバイト単位でそのまま出る -------------------------- */
    if (!quiet) printf("\nG1. 正常系（中間表現がバイト単位で保存される）\n");
    drive(f, S("\xe3\x81\x8d\xe3\x82\x87][\xe3\x81\x8a\xe3\x82\x8f\n"), CAP, &c);
    chk(c.n == 1, "1 行だけ完成する");
    chk(c.n == 1 && c.len[0] == 14, "長さ 14 B（きょ][おわ）");
    chk(c.n == 1 && memcmp(c.line[0], "\xe3\x81\x8d\xe3\x82\x87][\xe3\x81\x8a\xe3\x82\x8f", 14) == 0,
        "内容がバイト単位で一致");
    chk(c.n == 1 && c.overflow[0] == 0, "overflow が立たない");
    chk(c.canary_ok, "カナリア無傷");

    /* G2 CR / LF / CRLF ---------------------------------------------------- */
    if (!quiet) printf("\nG2. 行末（CR / LF / CRLF のどれでも 1 行）\n");
    drive(f, S("\xe3\x81\x82\r\n"), CAP, &c);
    chk(c.n == 1, "CRLF で 1 行（LF が空行を作らない）");
    drive(f, S("\xe3\x81\x82\n"), CAP, &c);
    chk(c.n == 1, "LF だけで 1 行");
    drive(f, S("\xe3\x81\x82\r"), CAP, &c);
    chk(c.n == 1, "CR だけで 1 行");
    drive(f, S("\xe3\x81\x82\n\n"), CAP, &c);
    chk(c.n == 2 && c.len[1] == 0, "LF LF は 2 行目が空行（吸わない）");
    drive(f, S("\xe3\x81\x82\r\n\xe3\x81\x84\r\n"), CAP, &c);
    chk(c.n == 2 && c.len[0] == 3 && c.len[1] == 3, "CRLF 2 行がちょうど 2 行");

    /* G3 BS が UTF-8 を壊さない --------------------------------------------- */
    if (!quiet) printf("\nG3. BS は UTF-8 の 1 文字を消す（1 バイトではない）\n");
    drive(f, S("\xe3\x81\x82\xe3\x81\x84\x7f\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "あい + DEL → 3 B（1 バイト消しなら 5 B）");
    chk(c.n == 1 && memcmp(c.line[0], "\xe3\x81\x82", 3) == 0, "残りが あ そのもの");
    drive(f, S("\xe3\x81\x82\x08\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 0, "あ + BS → 空行");
    drive(f, S("\x08\x08\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 0 && c.canary_ok, "空行での BS が下に突き抜けない");
    drive(f, S("\xe3\x81\x82\xe3\x81\x84\x7f\x7f\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 0, "BS 2 回で 2 文字とも消える");

    /* G4 ESC シーケンス（**これが一番危ない**）------------------------------ */
    if (!quiet) printf("\nG4. ESC シーケンスを吸う（矢印キーが `[` を挿入しない）\n");
    drive(f, S("\xe3\x81\x82\x1b[A\xe3\x81\x84\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 6, "あ + ↑ + い → 6 B");
    chk(c.n == 1 && memchr(c.line[0], '[', c.len[0]) == NULL,
        "`[`（上昇アクセント）が混入していない");
    drive(f, S("\x1b[1;2D\xe3\x81\x82\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "パラメータ付き CSI（ESC [ 1 ; 2 D）も全部吸う");
    drive(f, S("\x1bOF\xe3\x81\x82\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "SS3（ESC O F = End キー）も吸う");
    drive(f, S("\x1bx\xe3\x81\x82\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "裸の ESC は次の 1 バイトだけ吸う");

    /* G5 溢れ: 切り詰めずに印を立てる + 領域外に書かない --------------------- */
    if (!quiet) printf("\nG5. 溢れ（黙って切り詰めない・領域外に書かない）\n");
    {
        static unsigned char big[4096];
        memset(big, 'a', sizeof big);
        big[sizeof big - 1] = '\n';
        drive(f, big, sizeof big, CAP, &c);
        chk(c.canary_ok, "buf[cap] のカナリアが無傷（領域外に書いていない）");
        chk(c.n == 1, "溢れても Enter で 1 行として完成する");
        chk(c.n == 1 && c.len[0] <= CAP - 1, "長さが cap-1 を超えない");
        chk(c.n == 1 && c.overflow[0] == 1, "overflow が立つ（**黙って切り詰めない**）");
    }
    drive(f, S("\xe3\x81\x82\n"), CAP, &c);
    chk(c.n == 1 && c.overflow[0] == 0, "溢れていない行では overflow が立たない");

    /* G6 行をまたいで状態が残らない ----------------------------------------- */
    if (!quiet) printf("\nG6. 行をまたいで残らない\n");
    drive(f, S("\xe3\x81\x82\n\xe3\x81\x84\n"), CAP, &c);
    chk(c.n == 2 && c.len[1] == 3, "2 行目が 1 行目の続きになっていない");
    chk(c.n == 2 && memcmp(c.line[1], "\xe3\x81\x84", 3) == 0, "2 行目の内容が い");

    /* G7 Ctrl-C / Ctrl-U で行を捨てる --------------------------------------- */
    if (!quiet) printf("\nG7. Ctrl-C / Ctrl-U で行を捨てる\n");
    drive(f, S("\xe3\x81\x82\xe3\x81\x84\x03\xe3\x81\x86\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "Ctrl-C の後は打ち直した分だけ");
    drive(f, S("\xe3\x81\x82\x15\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 0, "Ctrl-U で空行になる");

    /* G9 行をまたいで状態を持ち越す（**呼び出し側が毎行 reset してはいけない**）---
     *
     * ⚠️ **QEMU で実際に踏んだ。** saan_console_readline が呼び出しごとに
     *    `saan_line ln;` を作り直していたため、CRLF の `\r` で行が完成 → 合成 →
     *    次の readline で reset → 遅れて届いた `\n` が**空行としてもう 1 行**になり、
     *    発話のたびに「空行」の警告が 1 回出ていた。**音は普通に出るので気づきにくい。** */
    if (!quiet) printf("\nG9. 行をまたいで状態を持ち越す（CRLF を送る端末で空行を作らない）\n");
    {
        capture keep, per_line;
        drive_ex(f, S("\xe3\x81\x82\r\n\xe3\x81\x84\r\n"), CAP, 0, &keep);
        drive_ex(f, S("\xe3\x81\x82\r\n\xe3\x81\x84\r\n"), CAP, 1, &per_line);
        chk(keep.n == 2, "持ち越せば CRLF 2 行がちょうど 2 行");
        /* ⚠️ **陽性対照。** ここが 2 になったら「毎行 reset しても安全」に
         *    変わったということなので、saan_console.c の注意書きを直すこと。 */
        chk(per_line.n > 2,
            "陽性対照: 毎行 reset すると空行が増える（saan_console.c が reset しない理由）");
    }

    /* G10 画面に出る列と合成される列が一致する ------------------------------
     *
     * ⚠️ **これが合わないと「画面だけが嘘をつく」。** 実際に踏んだ壊れ方は
     *    「行の先頭 1 文字がエコーされない」で、**合成される列は正しかった**ので
     *    音では気づけない。しかも CRLF を送る端末では再現しない。
     * ⚠️ **2 行目以降を必ず入れる。** 1 行だけだと、DONE の次のバイトで
     *    自動クリアが起きる経路を通らないので通ってしまう。 */
    if (!quiet) printf("\nG10. エコーされる列 == 合成される列（画面が嘘をつかない）\n");
    drive(f, S("\xe3\x81\x82\n\xe3\x81\x84\xe3\x81\x86\n"), CAP, &c);
    chk(c.n == 2, "LF 区切りで 2 行");
    chk(c.n == 2 && c.echo_len[0] == c.len[0]
        && memcmp(c.echo[0], c.line[0], c.len[0]) == 0, "1 行目: エコー == 中身");
    chk(c.n == 2 && c.echo_len[1] == c.len[1]
        && memcmp(c.echo[1], c.line[1], c.len[1]) == 0,
        "**2 行目: エコー == 中身**（先頭 1 文字が落ちない）");
    drive(f, S("\xe3\x81\x82\r\n\xe3\x81\x84\r\n"), CAP, &c);
    chk(c.n == 2 && c.echo_len[1] == c.len[1]
        && memcmp(c.echo[1], c.line[1], c.len[1]) == 0, "CRLF でも 2 行目が一致");
    drive(f, S("\xe3\x81\x82\xe3\x81\x84\x7f\n"), CAP, &c);
    chk(c.n == 1 && c.echo_len[0] == c.len[0]
        && memcmp(c.echo[0], c.line[0], c.len[0]) == 0, "BS の後も一致（書き直し）");
    {
        static unsigned char big[4096];
        memset(big, 'a', sizeof big);
        big[sizeof big - 1] = '\n';
        drive(f, big, sizeof big, CAP, &c);
        chk(c.n == 1 && c.echo_len[0] == c.len[0],
            "溢れたバイトはエコーもしない（画面が止まるのが「もう入らない」の合図）");
    }

    /* G8 残りの制御文字は捨てる --------------------------------------------- */
    if (!quiet) printf("\nG8. TAB / NUL などの制御文字は捨てる\n");
    drive(f, S("\xe3\x81\x82\t\xe3\x81\x84\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 6, "TAB が混入しない");
    drive(f, S("\xe3\x81\x82\x01\x02\n"), CAP, &c);
    chk(c.n == 1 && c.len[0] == 3, "その他の C0 制御文字が混入しない");

    return g_fail;
}

int main(void) {
    printf("== 行編集ステートマシン（csrc/line.c）==\n");
    const int fails = run(saan_line_feed, 0);

    printf("\n== 陽性対照: わざと素直に書いた実装 naive_feed() ==\n");
    printf("  （BS は 1 バイト消し / ESC 素通し / CRLF 2 行 / 黙って切り詰め）\n");
    const int naive_fails = run(naive_feed, 1);
    printf("  同じベクタで %d 項目が落ちた\n", naive_fails);

    printf("\n----- 結果 -----\n");
    if (naive_fails == 0) {
        printf("NG! **陽性対照が 1 つも落ちない = このゲートは空虚**。\n");
        printf("    naive_feed() は 4 つの既知の壊し方をわざと入れてある。\n");
        printf("    それを検出できないベクタは、本物の回帰も検出できない。\n");
        return 1;
    }
    if (fails != 0) {
        printf("NG! csrc/line.c が %d 項目で落ちた\n", fails);
        return 1;
    }
    printf("OK  csrc/line.c は全項目通過 / 陽性対照は %d 項目で落ちた\n", naive_fails);
    printf("⚠️ 見ていないもの: UART から本当に 1 バイトずつ取れるか、"
           "端末のエコーの見た目、全角の表示幅（**実機でしか見えない**）\n");
    return 0;
}
