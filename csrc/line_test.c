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
 * G11 は**行の経路判定**（`saan_g2p_classify` / K-B）。こちらの陽性対照は
 * `naive_classify()` = **手書きの文字集合で判定する実装**で、審査が却下したもの。
 * 同じベクタで落ちることを見て初めて「文字集合ではだめ」と言える。
 *
 * ⚠️ **見ていないもの**: UART から本当に 1 バイトずつ取れるか（IDF の API）、
 *    端末のエコーが読める見た目になるか、全角の表示幅。**これは実機でしか見えない。**
 *    経路判定がホスト側と一致するかは**ここでは見ていない** —
 *    `uv run python scripts/k1/kb_route_parity.py`（held-out 298 文 + 中間表現 298 行）。
 */
#include "line.h"
#include "g2p.h"

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

/* --- G11 経路の判定（K-B / saan_g2p_classify）------------------------------
 *
 * ⚠️ **陽性対照は「手書きの文字集合」。** 審査が却下した実装をここに置いてある:
 *    「ひらがな + ー + [ ] # ° + ? 系ならかな経路、1 文字でも外なら辞書経路」。
 *    もっともらしいが、凍結テーブルと 3 か所でずれる:
 *      - `ぁぃぇぉゃゅょゎゐゑゕゖ` は**単独ではモーラになれない**（表に無い）
 *      - `_ ^ $` はひらがなではないが**マーク側**
 *      - **拒否という 3 番目の値が無い**ので「中間表現 + `。`」が
 *        黙って辞書経路に回り、**それらしい音が出てしまう**
 *    この 3 つを検出できないベクタは、本物の回帰も検出できない。
 */
typedef saan_g2p_route (*route_fn)(const char *, size_t, saan_g2p_status *, int32_t *);

static int cp_in(uint32_t cp, uint32_t lo, uint32_t hi) { return cp >= lo && cp <= hi; }

static saan_g2p_route naive_classify(const char *text, size_t n,
                                     saan_g2p_status *why, int32_t *err_byte) {
    const unsigned char *t = (const unsigned char *)text;
    size_t i;
    if (why) *why = SAAN_G2P_OK;
    if (err_byte) *err_byte = -1;
    for (i = 0; i < n; ) {
        uint32_t cp;
        size_t l;
        if (t[i] < 0x80u)      { cp = t[i]; l = 1; }
        else if (t[i] < 0xE0u) { if (i + 1 >= n) return SAAN_G2P_ROUTE_DICT;
                                 cp = ((uint32_t)(t[i] & 0x1Fu) << 6) | (t[i+1] & 0x3Fu); l = 2; }
        else if (t[i] < 0xF0u) { if (i + 2 >= n) return SAAN_G2P_ROUTE_DICT;
                                 cp = ((uint32_t)(t[i] & 0x0Fu) << 12)
                                    | ((uint32_t)(t[i+1] & 0x3Fu) << 6) | (t[i+2] & 0x3Fu); l = 3; }
        else return SAAN_G2P_ROUTE_DICT;
        /* 「かな経路のアルファベット」を手で並べたもの */
        if (!(cp_in(cp, 0x3041, 0x3096)                /* ひらがな */
              || cp == 0x30FC                          /* ー */
              || cp == 0x00B0                          /* ° */
              || cp == '[' || cp == ']' || cp == '#'
              || cp == '?' || cp == '!' || cp == '.' || cp == '~'))
            return SAAN_G2P_ROUTE_DICT;
        i += l;
    }
    return SAAN_G2P_ROUTE_KANA;
}

#define R(f, lit) (f)((lit), sizeof(lit) - 1, NULL, NULL)

static int run_route(route_fn f, int quiet) {
    g_fail = 0;
    g_quiet = quiet;

    if (!quiet) printf("\nG11. 経路の判定（かな / 辞書 / 拒否）\n");

    /* (1) かな経路 — トークン化が行末まで通る */
    chk(R(f, "\xe3\x81\x8d\xe3\x82\x87][\xe3\x81\x8a\xe3\x82\x8f\xe3\x82\x88][\xe3\x81\x84"
            "\xe3\x81\xa6][\xe3\x82\x93\xe3\x81\x8d\xe3\x81\xa7\xe3\x81\x99\xc2\xb0\xe3\x81\xad")
        == SAAN_G2P_ROUTE_KANA, "M-63 の中間表現 → かな");
    chk(R(f, "\xe3\x81\x93\xe3\x82\x93\xe3\x81\xab\xe3\x81\xa1\xe3\x81\xaf")
        == SAAN_G2P_ROUTE_KANA, "純ひらがな `こんにちは` → かな（D-040 を守る）");
    chk(R(f, "][#") == SAAN_G2P_ROUTE_KANA, "記号だけの行 `][#` → かな");
    chk(R(f, "") == SAAN_G2P_ROUTE_KANA, "空行 → かな（案内は呼び出し側の仕事）");
    /* ⚠️ `_ ^ $` はひらがなではないが**マーク側**。手書きの集合はここで落ちる */
    chk(R(f, "\xe3\x81\x82_\xe3\x81\x84") == SAAN_G2P_ROUTE_KANA,
        "`あ_い`（PAD マーク）→ かな");
    chk(R(f, "\xe3\x81\x82?~") == SAAN_G2P_ROUTE_KANA, "疑問 EOS `あ?~` → かな");
    /* ⚠️ `あ°` は**正当な無声化モーラ**（`A`）。`°` があるだけでは拒否にならない */
    chk(R(f, "\xe3\x81\x82\xc2\xb0") == SAAN_G2P_ROUTE_KANA, "`あ°`（無声化モーラ）→ かな");

    /* (2) 辞書経路 — 通らず、マークが 1 つも無い */
    chk(R(f, "\xe4\xbb\x8a\xe6\x97\xa5\xe3\x81\xaf\xe8\x89\xaf\xe3\x81\x84"
            "\xe5\xa4\xa9\xe6\xb0\x97\xe3\x81\xa7\xe3\x81\x99\xe3\x81\xad\xe3\x80\x82")
        == SAAN_G2P_ROUTE_DICT, "漢字文 `今日は良い天気ですね。` → 辞書");
    chk(R(f, "\xe3\x82\xb3\xe3\x83\xb3\xe3\x83\x8b\xe3\x83\x81\xe3\x83\x8f")
        == SAAN_G2P_ROUTE_DICT, "カタカナだけ `コンニチハ` → 辞書");
    chk(R(f, "2026\xe5\xb9\xb4") == SAAN_G2P_ROUTE_DICT, "数字混じり `2026年` → 辞書");
    chk(R(f, "\xe3\x81\x82\xe3\x80\x82") == SAAN_G2P_ROUTE_DICT,
        "ひらがな + 句点 `あ。` → 辞書（`。` は端末のかな経路には無い）");
    /* ⚠️ 単独では**モーラになれない**小書き文字。手書きの集合はここでも落ちる */
    chk(R(f, "\xe3\x81\x81") == SAAN_G2P_ROUTE_DICT, "`ぁ` 単独 → 辞書（表に無い）");
    chk(R(f, "\xe3\x82\x83") == SAAN_G2P_ROUTE_DICT, "`ゃ` 単独 → 辞書（表に無い）");
    chk(R(f, "\xe3\x82\x90") == SAAN_G2P_ROUTE_DICT, "`ゐ` 単独 → 辞書（表に無い）");

    /* (3) 拒否 — 通らず、マークがある。**ここが 3 値にした理由** */
    chk(R(f, "\xe3\x81\x8d\xe3\x82\x87][\xe3\x81\x8a\xe3\x82\x8f\xe3\x82\x88][\xe3\x81\x84"
            "\xe3\x81\xa6][\xe3\x82\x93\xe3\x81\x8d\xe3\x81\xa7\xe3\x81\x99\xc2\xb0\xe3\x81\xad"
            "\xe3\x80\x82")
        == SAAN_G2P_ROUTE_REJECT,
        "**中間表現 + `。` → 拒否**（黙って辞書経路に回さない）");
    /* ⚠️ **半角 `?` は拒否しない。** EOS のマークであると同時に普通の疑問文の約物でもあり、
     *    拒否にすると held-out 2,333 行の 45 行（1.93%、うち 42 行は `本当なんでしょうか?` の
     *    ような普通の文）が喋れなくなる（K-B の実測）。`has_mark()` は `?` 系を数えない。 */
    chk(R(f, "\xe4\xbb\x8a\xe6\x97\xa5\xe3\x81\xaf?") == SAAN_G2P_ROUTE_DICT,
        "漢字文 + 半角 `?` → **辞書**（`?` は普通の疑問文にも出るので拒否しない）");
    chk(R(f, "\xe6\x9c\xac\xe5\xbd\x93\xe3\x81\xaa\xe3\x82\x93\xe3\x81\xa7"
            "\xe3\x81\x97\xe3\x82\x87\xe3\x81\x86\xe3\x81\x8b?") == SAAN_G2P_ROUTE_DICT,
        "`本当なんでしょうか?` → 辞書（K-B が見つけた 42 行の代表）");
    /* かな + `?` は**かな経路**（`?` は EOS のマークなのでトークン化が通る） */
    chk(R(f, "\xe3\x81\x82?") == SAAN_G2P_ROUTE_KANA,
        "`あ?` → かな（`?` は EOS のマーク）");
    /* ⚠️ `°` は**モーラの後ろでしか読まれない**（g2p.h の規約 4）。マークの後ろ・
     *    行頭の `°` はトークン化で落ちる。行にはマーク（`°` 自身）があるので拒否 */
    chk(R(f, "[\xc2\xb0") == SAAN_G2P_ROUTE_REJECT,
        "`[°`（モーラに付かない `°`）→ 拒否");
    /* ⚠️ **`°` だけがマークの行**を必ず入れる。`[ ] #` がある行しか見ないと、
     *    マーク集合から `°` を落とす壊し方を検出できない（実際に取り逃がした） */
    chk(R(f, "\xe3\x81\xa7\xe3\x81\x99\xc2\xb0\xe3\x81\xad\xe3\x80\x82")
        == SAAN_G2P_ROUTE_REJECT,
        "**`です°ね。`（マークが `°` だけ）→ 拒否**");
    chk(R(f, "\xff\xfe") == SAAN_G2P_ROUTE_REJECT,
        "不正な UTF-8 → 拒否（辞書経路にも回さない）");

    /* (4) 位置を返す（「どの文字を消せばいいか」が分からないと直せない）--------- */
    {
        saan_g2p_status why = SAAN_G2P_OK;
        int32_t eb = -1;
        /* "あ" (3B) + "。" (3B) + "[" -> 3 バイト目で止まる */
        (void)f("\xe3\x81\x82\xe3\x80\x82[", 7, &why, &eb);
        chk(eb == 3, "拒否の err_byte が引けない文字の先頭（3）");
        chk(why == SAAN_G2P_ERR_UNKNOWN, "why が ERR_UNKNOWN");
        why = SAAN_G2P_ERR_ARG; eb = 99;
        (void)f("\xe3\x81\x82", 3, &why, &eb);
        chk(why == SAAN_G2P_OK && eb == -1, "かな経路では why=OK / err_byte=-1");
    }
    return g_fail;
}

#undef R

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

    printf("\n== 経路の判定（csrc/g2p.c の saan_g2p_classify / K-B）==\n");
    const int rfails = run_route(saan_g2p_classify, 0);
    printf("\n== 陽性対照: 手書きの文字集合で判定する naive_classify() ==\n");
    printf("  （審査が却下した実装。小書き文字 / `_ ^ $` / 拒否の欠落を検出できるか）\n");
    const int naive_rfails = run_route(naive_classify, 1);
    printf("  同じベクタで %d 項目が落ちた\n", naive_rfails);
    if (naive_rfails == 0) {
        printf("NG! **経路判定の陽性対照が 1 つも落ちない = G11 は空虚**。\n");
        printf("    naive_classify() は 3 つの既知のずれをわざと持っている。\n");
        return 1;
    }
    if (rfails != 0) {
        printf("NG! saan_g2p_classify が %d 項目で落ちた\n", rfails);
        return 1;
    }

    printf("OK  csrc/line.c は全項目通過 / 陽性対照は %d 項目で落ちた\n", naive_fails);
    printf("OK  saan_g2p_classify は全項目通過 / 陽性対照は %d 項目で落ちた\n", naive_rfails);
    printf("⚠️ 見ていないもの: UART から本当に 1 バイトずつ取れるか、"
           "端末のエコーの見た目、全角の表示幅（**実機でしか見えない**）\n");
    return 0;
}
