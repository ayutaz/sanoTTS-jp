/* K-7: ラベル列 → 生徒インデックス列。詳細は k7_label2ids.h。
 *
 * `piper_plus_g2p/japanese.py` の `_phonemize_core()` /
 * `_apply_n_phoneme_rules()` / `_get_question_type()` をそのまま写した。
 * **規則を「改善」しない** — 目的はホストと同じ列を出すこと。
 */
#include "k7_label2ids.h"
#include "k7_table.h"

#include <stdio.h>
#include <string.h>

/* `_` `#` `[` `]` `^` `$` `?` `?!` `?.` `?~` は「次の音素」を探すとき飛ばす。 */
static int is_skip(const char *t) {
    static const char *const S[] = { "_", "#", "[", "]", "^", "$",
                                     "?", "?!", "?.", "?~" };
    for (size_t i = 0; i < sizeof S / sizeof *S; i++)
        if (strcmp(t, S[i]) == 0) return 1;
    return 0;
}

const int k7_n_tokens = K7_N_TOKENS;
int k7_debug_drop_rise = 0;

int32_t k7_token_id(const char *name) {
    for (int i = 0; i < K7_N_TOKENS; i++)
        if (strcmp(k7_tokens[i].name, name) == 0) return k7_tokens[i].id;
    return -1;
}

const char *k7_strerror(k7_status s) {
    switch (s) {
    case K7_OK:            return "OK";
    case K7_ERR_OVERFLOW:  return "ids バッファ不足";
    case K7_ERR_TOKEN:     return "語彙に無い音素";
    case K7_ERR_ARG:       return "引数が不正";
    }
    return "不明なエラー";
}

/* ---------------------------------------------------------------- 疑問符 */

static int ends_with(const char *s, const char *suf) {
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && memcmp(s + a - b, suf, b) == 0;
}

/* 末尾の空白を無視した「疑問の型」。非疑問なら NULL。
 * ⚠️ **長いパターンから見る。** `?!` を `?` より先に見ないと取り違える。 */
static const char *question_type(const char *text) {
    /* strip */
    size_t n = strlen(text);
    while (n && (text[n-1] == ' ' || text[n-1] == '\n' || text[n-1] == '\r'
                 || text[n-1] == '\t')) n--;
    static char buf[512];
    size_t k = n < sizeof buf - 1 ? n : sizeof buf - 1;
    memcpy(buf, text, k); buf[k] = 0;

    if (ends_with(buf, "?!") || ends_with(buf, "\xEF\xBC\x81\xEF\xBC\x9F")   /* ！？ */
                             || ends_with(buf, "\xEF\xBC\x9F\xEF\xBC\x81"))  /* ？！ */
        return "?!";
    if (ends_with(buf, "?.") || ends_with(buf, "\xE3\x80\x82\xEF\xBC\x9F")   /* 。？ */
                             || ends_with(buf, "\xEF\xBC\x9F\xE3\x80\x82"))  /* ？。 */
        return "?.";
    if (ends_with(buf, "?~") || ends_with(buf, "\xEF\xBD\x9E\xEF\xBC\x9F")   /* ～？ */
                             || ends_with(buf, "\xEF\xBC\x9F\xEF\xBD\x9E"))  /* ？～ */
        return "?~";
    if (ends_with(buf, "?")  || ends_with(buf, "\xEF\xBC\x9F"))              /* ？ */
        return "?";
    return NULL;
}

/* ---------------------------------------------------------------- ラベル */

/* `...-PHONEME+...` の PHONEME を out に取り出す。0 で成功。 */
static int label_phoneme(const char *lab, char *out, size_t out_n) {
    const char *dash = strchr(lab, '-');
    if (!dash) return -1;
    const char *plus = strchr(dash + 1, '+');
    if (!plus) return -1;
    size_t n = (size_t)(plus - dash - 1);
    if (n == 0 || n >= out_n) return -1;
    memcpy(out, dash + 1, n); out[n] = 0;
    return 0;
}

/* `/A:a1+a2+a3/` を取り出す。0 で成功。 */
static int label_prosody(const char *lab, int *a1, int *a2, int *a3) {
    const char *p = strstr(lab, "/A:");
    if (!p) return -1;
    p += 3;
    int sign = 1;
    if (*p == '-') { sign = -1; p++; }
    if (*p < '0' || *p > '9') return -1;
    int v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    *a1 = sign * v;
    if (*p++ != '+') return -1;
    if (*p < '0' || *p > '9') return -1;
    v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    *a2 = v;
    if (*p++ != '+') return -1;
    if (*p < '0' || *p > '9') return -1;
    v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    *a3 = v;
    return 0;
}

/* ---------------------------------------------------------------- 本体 */

/* ⚠️ **2048 にしていたら .bss を 32,768 B 食って、実機で I2S を有効にすると
 *    DRAM が 19,304 B 溢れた**（QEMU ビルドは I2S を gc-sections が落とすので
 *    通っていた）。端末は ids 350 個で拒否する（`SAAN_MAX_IDS`）ので、
 *    トークンは高々 175 個。**640 なら 3 倍以上の余裕がある。**
 * ⚠️ 上限（K7_MAX_TOKENS / K7_TOK_MAX）は **k7_label2ids.h** に移した。
 *    呼び出し側が置き場のバイト数（K7_SCRATCH_BYTES）を知る必要があるため。 */
#define MAX_TOKENS K7_MAX_TOKENS
#define TOK_MAX    K7_TOK_MAX

/* --- トークン表の置き場（T10(a)）--------------------------------------------
 *
 * 既定は .bss（ホストのゲートはこれ）。ESP32 は `-DK7_EXTERNAL_SCRATCH=1` で
 * 呼び出し側（esp32/main/saan_kanji.c）が合成 arena の一部を渡す。
 * ⚠️ **どちらでも配列の形と上限は同じ**。変わるのは置き場だけなので、
 *    ホストで測った一致（G25）はそのまま端末にも当てはまる。 */
#if defined(K7_EXTERNAL_SCRATCH) && K7_EXTERNAL_SCRATCH
static char (*s_tok_buf)[TOK_MAX];

void k7_set_scratch(void *buf, size_t nbytes) {
    /* ⚠️ **足りないバッファは受け取らない。** 受けて切り詰めると
     *    「端末とホストで同じ列」が音としては再生できる形で崩れる。 */
    s_tok_buf = (buf != NULL && nbytes >= K7_SCRATCH_BYTES)
              ? (char (*)[TOK_MAX])buf : NULL;
}
#else
static char s_tok_storage[MAX_TOKENS][TOK_MAX];
static char (*const s_tok_buf)[TOK_MAX] = s_tok_storage;
#endif

k7_status k7_label2ids(const char *const *labels, int n_labels,
                       const char *text,
                       int32_t *ids, int32_t ids_cap, int32_t *n_ids) {
    if (!labels || !text || !n_ids || (ids_cap > 0 && !ids)) return K7_ERR_ARG;
    /* ⚠️ 外部置き場のときに k7_set_scratch() を呼び忘れると**ここで止まる**。
     *    NULL を書きに行って StoreProhibited で黙って再起動、にはしない。 */
    if (s_tok_buf == NULL) return K7_ERR_ARG;
    char (*tok)[TOK_MAX] = s_tok_buf;
    int nt = 0;
    const char *q = question_type(text);

    /* ⚠️ **溢れたら切り詰めずにエラーを返す。** 先頭だけ喋ると
     *    「端末とホストで同じ列」が崩れるのに、音としては再生できてしまう。 */
#define NEED(k) do { if (nt + (k) > MAX_TOKENS) return K7_ERR_OVERFLOW; } while (0)
    for (int i = 0; i < n_labels; i++) {
        NEED(4);          /* 音素 1 個 + 記号 3 個が最悪 */
        char ph[TOK_MAX];
        if (label_phoneme(labels[i], ph, sizeof ph) != 0) continue;

        if (strcmp(ph, "sil") == 0) {
            /* 先頭の sil は捨てる。末尾の sil は疑問符に化ける */
            if (i == n_labels - 1 && q) {
                snprintf(tok[nt++], TOK_MAX, "%s", q);
            }
            continue;
        }
        if (strcmp(ph, "pau") == 0) {
            snprintf(tok[nt++], TOK_MAX, "_");
            continue;
        }
        snprintf(tok[nt++], TOK_MAX, "%s", ph);

        int a1, a2, a3;
        if (label_prosody(labels[i], &a1, &a2, &a3) != 0) continue;
        int a2n = -1;
        if (i < n_labels - 1) {
            int b1, b2, b3;
            if (label_prosody(labels[i + 1], &b1, &b2, &b3) == 0) a2n = b2;
        }
        /* ⚠️ **3 つとも独立に判定する**（else if にしない）。Python 版と同じ */
        if (a1 == 0 && a2n == a2 + 1)
            snprintf(tok[nt++], TOK_MAX, "]");
        if (a2 == a3 && a2n == 1)
            snprintf(tok[nt++], TOK_MAX, "#");
        if (a2 == 1 && a2n == 2 && !k7_debug_drop_rise)
            snprintf(tok[nt++], TOK_MAX, "[");
    }
#undef NEED

    /* `ん` の異音: **後続の音素**で決まる。記号は飛ばす。 */
    for (int i = 0; i < nt; i++) {
        if (strcmp(tok[i], "N") != 0) continue;
        const char *nx = NULL;
        for (int j = i + 1; j < nt; j++) {
            if (is_skip(tok[j])) continue;
            nx = tok[j];
            break;
        }
        const char *rep = "N_uvular";
        if (nx) {
            if (!strcmp(nx, "m") || !strcmp(nx, "my") || !strcmp(nx, "b")
                || !strcmp(nx, "by") || !strcmp(nx, "p") || !strcmp(nx, "py"))
                rep = "N_m";
            else if (!strcmp(nx, "n") || !strcmp(nx, "ny") || !strcmp(nx, "t")
                     || !strcmp(nx, "ty") || !strcmp(nx, "d") || !strcmp(nx, "dy")
                     || !strcmp(nx, "ts") || !strcmp(nx, "ch"))
                rep = "N_n";
            else if (!strcmp(nx, "k") || !strcmp(nx, "ky") || !strcmp(nx, "kw")
                     || !strcmp(nx, "g") || !strcmp(nx, "gy") || !strcmp(nx, "gw"))
                rep = "N_ng";
        }
        snprintf(tok[i], TOK_MAX, "%s", rep);
    }

    /* ids へ。**canonical と同じ PAD 規則**（C-007）。 */
    int32_t pad = k7_token_id("_");
    int32_t n = 0;
    int overflow = 0;
    k7_status bad = K7_OK;

#define PUSH(v) do { if (n < ids_cap) ids[n] = (v); else overflow = 1; n++; } while (0)

    PUSH(k7_token_id("^"));
    PUSH(pad);
    for (int i = 0; i < nt; i++) {
        int32_t id = k7_token_id(tok[i]);
        if (id < 0) { bad = K7_ERR_TOKEN; break; }
        PUSH(id);
        if (id != pad) PUSH(pad);
    }
    PUSH(k7_token_id("$"));
#undef PUSH

    *n_ids = n;
    if (bad != K7_OK) return bad;
    return overflow ? K7_ERR_OVERFLOW : K7_OK;
}
