/* 長文を文末記号で区切る。規則と理由は split.h */
#include "split.h"

/* UTF-8 を 1 文字復号する。不正なら -1（*len は触らない）。
 * ⚠️ 過長符号化やサロゲートの厳密な検査はしない — それは G2P（saan_g2p_classify /
 *    saan_kanji_to_ids）の仕事で、ここは「区切りを文字の途中に置かない」ことだけを守る */
static int32_t utf8_dec(const unsigned char *s, size_t n, size_t i, int *len) {
    const unsigned c = s[i];
    if (c < 0x80u) { *len = 1; return (int32_t)c; }
    const int k = (c >= 0xF8u) ? 0 : (c >= 0xF0u) ? 4 : (c >= 0xE0u) ? 3 : (c >= 0xC0u) ? 2 : 0;
    if (k == 0 || i + (size_t)k > n) return -1;
    int32_t cp = (int32_t)(c & (0xFFu >> (k + 1)));
    for (int j = 1; j < k; ++j) {
        if ((s[i + j] & 0xC0u) != 0x80u) return -1;
        cp = (cp << 6) | (int32_t)(s[i + j] & 0x3Fu);
    }
    *len = k;
    return cp;
}

static int is_ws(int32_t c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == 0x3000;
}
/* 開始記号: 。！？!?  ⚠️ `.` と `．` は入れない（3.14 を切らない） */
static int is_starter(int32_t c) {
    return c == 0x3002 || c == 0xFF01 || c == 0xFF1F || c == '!' || c == '?';
}
/* 閉じ括弧: 」』）)】 */
static int is_closer(int32_t c) {
    return c == 0x300D || c == 0x300F || c == 0xFF09 || c == ')' || c == 0x3011;
}
/* 終端の並びに入る文字: 開始記号・閉じ括弧・．・.・〜(U+301C)・～(U+FF5E)・… */
static int is_run(int32_t c) {
    return is_starter(c) || is_closer(c) || c == 0xFF0E || c == '.' ||
           c == 0x301C || c == 0xFF5E || c == 0x2026;
}
static int is_content(int32_t c) { return !is_ws(c) && !is_run(c); }

/* [i, n) に中身があるか。不正な UTF-8 なら -1 を返し *eb に位置 */
static int has_content(const unsigned char *s, size_t n, size_t i, int32_t *eb) {
    while (i < n) {
        int L; const int32_t c = utf8_dec(s, n, i, &L);
        if (c < 0) { *eb = (int32_t)i; return -1; }
        if (is_content(c)) return 1;
        i += (size_t)L;
    }
    return 0;
}

/* [a, b) の末尾の空白を落とした終わり */
static size_t trim_end(const unsigned char *s, size_t a, size_t b) {
    size_t e = a, i = a;
    while (i < b) {
        int L; const int32_t c = utf8_dec(s, b, i, &L);
        if (c < 0) return b;   /* 呼び出し前に検査済み */
        i += (size_t)L;
        if (!is_ws(c)) e = i;
    }
    return e;
}

saan_split_next_status saan_split_next(const char *text, size_t nbytes, size_t *pos,
                                       saan_span *out, int32_t *err_byte) {
    const unsigned char *s = (const unsigned char *)text;
    size_t i = *pos;

    /* 1. 先頭の空白を飛ばす */
    while (i < nbytes) {
        int L; const int32_t c = utf8_dec(s, nbytes, i, &L);
        if (c < 0) { *err_byte = (int32_t)i; return SAAN_SPLIT_ERR_UTF8_NEXT; }
        if (!is_ws(c)) break;
        i += (size_t)L;
    }
    if (i >= nbytes) { *pos = nbytes; return SAAN_SPLIT_END; }

    /* 2. 中身の無い残りは区間にしない（先頭の呼び出しで全体が記号だけのときだけ来る。
     *    それ以外は前の区間が 4. で吸っている） */
    const int hc = has_content(s, nbytes, i, err_byte);
    if (hc < 0) return SAAN_SPLIT_ERR_UTF8_NEXT;
    if (hc == 0) { *pos = nbytes; return SAAN_SPLIT_END; }

    const size_t start = i;
    int seen = 0;
    size_t end = nbytes;
    /* 3. 中身のあとに来た最初の終端の並びの終わりで切る */
    while (i < nbytes) {
        int L; const int32_t c = utf8_dec(s, nbytes, i, &L);
        if (c < 0) { *err_byte = (int32_t)i; return SAAN_SPLIT_ERR_UTF8_NEXT; }
        if (is_starter(c)) {
            i += (size_t)L;
            while (i < nbytes) {
                int L2; const int32_t c2 = utf8_dec(s, nbytes, i, &L2);
                if (c2 < 0) { *err_byte = (int32_t)i; return SAAN_SPLIT_ERR_UTF8_NEXT; }
                if (!is_run(c2)) break;
                i += (size_t)L2;
            }
            if (seen) { end = i; break; }
            continue;   /* 中身の無い区間は次の区間の頭にくっつける */
        }
        if (is_content(c)) seen = 1;
        i += (size_t)L;
    }

    /* 4. 後ろに中身が残っていなければ、残り（記号だけの断片）もこの区間に吸う */
    if (end < nbytes) {
        const int rest = has_content(s, nbytes, end, err_byte);
        if (rest < 0) return SAAN_SPLIT_ERR_UTF8_NEXT;
        if (rest == 0) end = nbytes;
    }
    const size_t e = trim_end(s, start, end);
    out->off = (int32_t)start;
    out->len = (int32_t)(e - start);
    *pos = end;
    return SAAN_SPLIT_OK;
}

saan_split_status saan_split_say(const char *text, size_t nbytes, int32_t max_ids,
                                 saan_split_to_ids_fn to_ids, saan_split_speak_fn speak,
                                 void *ctx, saan_split_result *res) {
    res->n_spans = 0;
    res->bad_index = -1;
    res->bad_code = 0;
    res->bad_span.off = -1;
    res->bad_span.len = 0;

    /* 1. 全体。上限内ならここで終わる（今と同じ経路） */
    int32_t n = 0;
    const int r = to_ids(ctx, text, nbytes, &n);
    if (r == 0 && n <= max_ids) {
        res->n_spans = 1;
        const int sr = speak(ctx, 0, 1);
        if (sr != 0) {
            res->bad_index = 0; res->bad_code = sr;
            res->bad_span.off = 0; res->bad_span.len = (int32_t)nbytes;
            return SAAN_SPLIT_ERR_SPEAK;
        }
        return SAAN_SPLIT_SAID;
    }
    /* 2. 長さ以外の失敗では区切らない */
    if (r != 0 && r != SAAN_SPLIT_CB_TOO_LONG) { res->bad_code = r; return SAAN_SPLIT_ERR_TO_IDS; }

    /* 3. 区間を全部数える */
    saan_span sp[SAAN_SPLIT_MAX_SPANS];
    int32_t ns = 0;
    size_t pos = 0;
    for (;;) {
        saan_span s;
        int32_t eb = -1;
        const saan_split_next_status st = saan_split_next(text, nbytes, &pos, &s, &eb);
        if (st == SAAN_SPLIT_END) break;
        if (st != SAAN_SPLIT_OK) { res->bad_code = (int)eb; return SAAN_SPLIT_ERR_UTF8; }
        if (ns >= SAAN_SPLIT_MAX_SPANS) return SAAN_SPLIT_ERR_SPANS;
        sp[ns++] = s;
    }
    res->n_spans = ns;
    /* ... 全区間を検査する（1 区間でも駄目なら何も喋らない） */
    for (int32_t k = 0; k < ns; ++k) {
        int32_t nk = 0;
        const int rk = to_ids(ctx, text + sp[k].off, (size_t)sp[k].len, &nk);
        if (rk != 0 || nk > max_ids) {
            res->bad_index = k;
            res->bad_code = (rk != 0) ? rk : SAAN_SPLIT_CB_TOO_MANY_IDS;
            res->bad_span = sp[k];
            return SAAN_SPLIT_ERR_SEGMENT;
        }
    }
    /* 4. 喋る */
    for (int32_t k = 0; k < ns; ++k) {
        int32_t nk = 0;
        const int rk = to_ids(ctx, text + sp[k].off, (size_t)sp[k].len, &nk);
        const int sk = (rk != 0) ? rk : speak(ctx, k, ns);
        if (sk != 0) {
            res->bad_index = k; res->bad_code = sk; res->bad_span = sp[k];
            return SAAN_SPLIT_ERR_SPEAK;
        }
    }
    return SAAN_SPLIT_SAID;
}

const char *saan_split_strerror(saan_split_status s) {
    switch (s) {
    case SAAN_SPLIT_SAID:        return "喋った";
    case SAAN_SPLIT_ERR_UTF8:    return "不正な UTF-8";
    case SAAN_SPLIT_ERR_TO_IDS:  return "読みに変換できない";
    case SAAN_SPLIT_ERR_SEGMENT: return "区切ったうちの 1 区間が長すぎるか読めない（何も喋っていない）";
    case SAAN_SPLIT_ERR_SPANS:   return "区間が多すぎる（何も喋っていない）";
    case SAAN_SPLIT_ERR_SPEAK:   return "合成か再生が途中で失敗した";
    }
    return "不明";
}
