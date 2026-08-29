/* 端末からの 1 行入力。規約は line.h の冒頭コメントに書いてある。
 *
 * 作業メモリは 0 B（呼び出し側の buf のみ）。malloc も libm も stdio も使わない。
 * 検証: `make -C csrc line`（陽性対照つき）
 */
#include "line.h"

void saan_line_reset(saan_line *ln, char *buf, size_t cap) {
    ln->buf = buf;
    ln->cap = cap;
    ln->len = 0;
    ln->overflow = 0;
    ln->done = 0;
    ln->esc = 0;
    ln->cr = 0;
    if (cap > 0) buf[0] = '\0';
}

/* 末尾の UTF-8 文字を 1 個消す。
 * ⚠️ **1 バイトだけ消してはいけない。** ひらがなは 3 バイトなので、
 *    残った継続バイトが不正な UTF-8 になり `saan_g2p` が ERR_UTF8 を返す。
 *    継続バイト (10xxxxxx) を食い尽くしてから先頭バイトを 1 個消す。 */
static void backspace(saan_line *ln) {
    while (ln->len > 0 && ((unsigned char)ln->buf[ln->len - 1] & 0xC0u) == 0x80u)
        --ln->len;
    if (ln->len > 0) --ln->len;
    ln->buf[ln->len] = '\0';
}

static saan_line_event finish(saan_line *ln) {
    ln->buf[ln->len] = '\0';
    ln->done = 1;
    ln->esc = 0;
    return SAAN_LINE_DONE;
}

saan_line_event saan_line_feed(saan_line *ln, unsigned char c) {
    /* ⚠️ **DONE の次の feed で自動的に消す。** 呼び出し側に clear を任せると
     *    必ず忘れる箇所が出て、2 発話目が「1 発話目 + 2 発話目」になる。
     *    `cr` はここで消さない（CRLF の LF を吸うのは行をまたぐ判定なので）。 */
    if (ln->done) {
        ln->done = 0;
        ln->len = 0;
        ln->overflow = 0;
        ln->buf[0] = '\0';
    }

    /* --- ESC シーケンスの吸い込み ---------------------------------------
     * ⚠️ **これが無いと矢印キーが `[` を挿入する**（ESC [ A の `[`）。
     *    `[` は中間表現では上昇アクセントなので、**エラーも出ずに音が変わる**。 */
    if (ln->esc == 1) {
        ln->esc = (c == '[' || c == 'O') ? 2 : 0;   /* CSI / SS3 以外は 1 バイト吸って終わり */
        return SAAN_LINE_MORE;
    }
    if (ln->esc == 2) {
        /* パラメータ 0x30-0x3F と中間 0x20-0x2F は続き。終端は 0x40-0x7E */
        if (c >= 0x40u && c <= 0x7Eu) ln->esc = 0;
        return SAAN_LINE_MORE;
    }
    if (c == 0x1Bu) { ln->esc = 1; ln->cr = 0; return SAAN_LINE_MORE; }

    /* --- 行の終端（CR / LF / CRLF のどれでも 1 行） ---------------------- */
    if (c == '\r') { ln->cr = 1; return finish(ln); }
    if (c == '\n') {
        if (ln->cr) { ln->cr = 0; return SAAN_LINE_MORE; }   /* CRLF の LF を吸う */
        return finish(ln);
    }
    ln->cr = 0;

    /* --- 編集 ------------------------------------------------------------ */
    if (c == 0x08u || c == 0x7Fu) {                  /* BS / DEL */
        if (ln->len == 0) return SAAN_LINE_MORE;
        backspace(ln);
        return SAAN_LINE_EDIT;
    }
    if (c == 0x03u || c == 0x15u) {                  /* Ctrl-C / Ctrl-U = 行を捨てる */
        if (ln->len == 0 && !ln->overflow) return SAAN_LINE_MORE;
        ln->len = 0;
        ln->overflow = 0;
        ln->buf[0] = '\0';
        return SAAN_LINE_EDIT;
    }
    if (c < 0x20u) return SAAN_LINE_MORE;            /* TAB など残りの制御文字は捨てる */

    /* --- 追加 ------------------------------------------------------------
     * ⚠️ **溢れたら切り詰めずに印を立てる。** 黙って切ると長文の貼り付けで
     *    先頭だけ喋ってしまい、「端末とホストで同じ列」という入力仕様が崩れる。
     *    呼び出し側は `overflow` を見て行ごと拒否すること。 */
    if (ln->len + 1 >= ln->cap) { ln->overflow = 1; return SAAN_LINE_MORE; }
    ln->buf[ln->len++] = (char)c;
    ln->buf[ln->len] = '\0';
    return SAAN_LINE_ECHO;   /* ⚠️ **受理したときだけ。** 溢れたバイトは MORE */
}
