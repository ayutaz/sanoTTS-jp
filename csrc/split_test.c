/* 長文の区切り（D-070）のゲート。
 *
 *   make -C csrc split
 *
 * ⚠️ **辞書もコーパスも要らない**（文字列だけを見る）ので all-test と CI で回る。
 *
 * G-SP1  ベクタ 14 本。期待する区間の文字列と完全一致するか
 * G-SP2  擬似乱数 10,000 ケースで性質（順序 / 区間外は空白だけ / 中身がある / 文字境界 /
 *        終端の並びの途中で切らない / 中身のあとの終端で必ず切る）
 * 陽性対照  わざと素直に書いた naive_next()（開始記号 1 文字ごとに切るだけ）を G-SP1 に流し、
 *        **表で「落ちる」とした 9 本がちょうど落ち、残り 4 本が通る**ことを名前で照合する。
 *        食い違ったらベクタが空虚（または規則が変わった）なので NG。
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "split.h"

static int g_ng = 0;
#define NG(...) do { printf("NG! " __VA_ARGS__); printf("\n"); ++g_ng; } while (0)

/* --- テスト側の文字の分類（実装とは独立に書く）---------------------------- */
/* UTF-8 を 1 文字復号。不正なら -1 */
static int32_t dec(const unsigned char *s, size_t n, size_t i, int *len) {
    unsigned c = s[i];
    if (c < 0x80) { *len = 1; return (int32_t)c; }
    int k = (c >= 0xF0) ? 4 : (c >= 0xE0) ? 3 : (c >= 0xC0) ? 2 : 0;
    if (k == 0 || i + (size_t)k > n) return -1;
    int32_t cp = (int32_t)(c & (0xFFu >> (k + 1)));
    for (int j = 1; j < k; ++j) {
        if ((s[i + j] & 0xC0u) != 0x80u) return -1;
        cp = (cp << 6) | (s[i + j] & 0x3F);
    }
    *len = k;
    return cp;
}
static int is_ws(int32_t c)      { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == 0x3000; }
static int is_starter(int32_t c) { return c == 0x3002 || c == 0xFF01 || c == 0xFF1F || c == '!' || c == '?'; }
static int is_closer(int32_t c)  { return c == 0x300D || c == 0x300F || c == 0xFF09 || c == ')' || c == 0x3011; }
static int is_run(int32_t c) {
    return is_starter(c) || is_closer(c) || c == 0xFF0E || c == '.' || c == 0x301C || c == 0xFF5E || c == 0x2026;
}
static int is_content(int32_t c) { return !is_ws(c) && !is_run(c); }

/* --- 区間を全部取る ---------------------------------------------------------- */
typedef saan_split_next_status (*next_fn)(const char *, size_t, size_t *, saan_span *, int32_t *);
#define MAXS 256
static int collect(next_fn f, const char *t, size_t n, saan_span *sp, int *ns, int32_t *eb) {
    size_t pos = 0;
    *ns = 0;
    for (;;) {
        saan_span s;
        saan_split_next_status r = f(t, n, &pos, &s, eb);
        if (r == SAAN_SPLIT_END) return 0;
        if (r != SAAN_SPLIT_OK) return (int)r;
        if (*ns >= MAXS) return 99;
        sp[(*ns)++] = s;
        if (pos > n) return 98;
    }
}

/* --- 陽性対照: 素直な区切り器 ------------------------------------------------- */
static saan_split_next_status naive_next(const char *text, size_t n, size_t *pos,
                                         saan_span *out, int32_t *eb) {
    const unsigned char *s = (const unsigned char *)text;
    if (*pos >= n) return SAAN_SPLIT_END;
    size_t i = *pos;
    while (i < n) {
        int L; int32_t c = dec(s, n, i, &L);
        if (c < 0) { *eb = (int32_t)i; return SAAN_SPLIT_ERR_UTF8_NEXT; }
        i += (size_t)L;
        if (is_starter(c)) break;
    }
    out->off = (int32_t)*pos; out->len = (int32_t)(i - *pos);
    *pos = i;
    return SAAN_SPLIT_OK;
}

/* --- G-SP1 ------------------------------------------------------------------ */
typedef struct {
    const char *name, *in;
    const char *want[4];   /* NULL 終端 */
    int naive_fails;       /* 陽性対照で落ちるべきか */
} vec;

static const vec V[] = {
    {"two",        "今日は晴れ。明日は雨。",   {"今日は晴れ。", "明日は雨。", NULL}, 0},
    {"q_bang",     "本当？！それは良い。",     {"本当？！", "それは良い。", NULL}, 1},
    {"q_wave",     "そうですか？〜いいえ。",   {"そうですか？〜", "いいえ。", NULL}, 1},
    {"q_dot",      "本当?.うん。",             {"本当?.", "うん。", NULL}, 1},
    {"closer",     "「行こう。」と言った。",   {"「行こう。」", "と言った。", NULL}, 1},
    {"run",        "あ。。。い",               {"あ。。。", "い", NULL}, 1},
    {"lead_punct", "。あ。",                   {"。あ。", NULL}, 1},
    {"crlf",       "あ。\r\n  い。",           {"あ。", "い。", NULL}, 1},
    {"no_end",     "終わりなし",               {"終わりなし", NULL}, 0},
    {"decimal",    "円周率は3.14です。",       {"円周率は3.14です。", NULL}, 0},
    {"ascii",      "Hi! OK?",                  {"Hi!", "OK?", NULL}, 1},
    {"empty",      "",                         {NULL}, 0},
    {"blank",      "  \xE3\x80\x80",           {NULL}, 1},
};
#define NV ((int)(sizeof V / sizeof V[0]))

static int vec_ok(next_fn f, const vec *v) {
    saan_span sp[MAXS]; int ns; int32_t eb = -1;
    const size_t n = strlen(v->in);
    if (collect(f, v->in, n, sp, &ns, &eb) != 0) return 0;
    int nw = 0; while (v->want[nw]) ++nw;
    if (ns != nw) return 0;
    for (int k = 0; k < ns; ++k) {
        const size_t L = strlen(v->want[k]);
        if ((size_t)sp[k].len != L || memcmp(v->in + sp[k].off, v->want[k], L) != 0) return 0;
    }
    return 1;
}

static void g_sp1(void) {
    int pass = 0, total = NV + 1;
    for (int i = 0; i < NV; ++i) {
        if (vec_ok(saan_split_next, &V[i])) ++pass;
        else NG("G-SP1 %s", V[i].name);
    }
    /* bad_utf8: あ(3 B) の直後が 0xFF */
    {
        const char *t = "\xE3\x81\x82\xFF\xE3\x81\x84\xE3\x80\x82";
        saan_span sp[MAXS]; int ns; int32_t eb = -1;
        int r = collect(saan_split_next, t, strlen(t), sp, &ns, &eb);
        if (r == SAAN_SPLIT_ERR_UTF8_NEXT && eb == 3) ++pass;
        else NG("G-SP1 bad_utf8（戻り値 %d / err_byte %d。期待 %d / 3）", r, (int)eb, SAAN_SPLIT_ERR_UTF8_NEXT);
    }
    printf("%s  G-SP1 n=%d で %d/%d\n", pass == total ? "OK " : "NG!", total, pass, total);
}

/* --- 陽性対照 ---------------------------------------------------------------- */
static void control(void) {
    int want_fail = 0, got_as_planned = 0;
    for (int i = 0; i < NV; ++i) {
        const int fails = !vec_ok(naive_next, &V[i]);
        want_fail += V[i].naive_fails;
        if (fails == V[i].naive_fails) ++got_as_planned;
        else NG("陽性対照: naive が %s で %s（表では %s）", V[i].name,
                fails ? "落ちた" : "通った", V[i].naive_fails ? "落ちる" : "通る");
    }
    if (got_as_planned == NV)
        printf("OK   陽性対照 naive: 想定どおり %d/%d 落ちた（残り %d 本は通った）\n",
               want_fail, want_fail, NV - want_fail);
}

/* --- G-SP2 ------------------------------------------------------------------ */
static uint32_t rng = 12345u;
static uint32_t rnd(void) { rng = rng * 1103515245u + 12345u; return rng >> 8; }

static const char *const ALPHA[] = {
    "あ", "漢", "。", "！", "？", "?", "!", "〜", "」", ".", " ", "\n",
};
#define NA ((int)(sizeof ALPHA / sizeof ALPHA[0]))

/* 1 ケースの性質。違反があれば理由を返す */
static const char *props(const char *t, size_t n, const saan_span *sp, int ns) {
    const unsigned char *s = (const unsigned char *)t;
    size_t prev_end = 0;
    for (int k = 0; k < ns; ++k) {
        const size_t a = (size_t)sp[k].off, b = a + (size_t)sp[k].len;
        if (sp[k].len <= 0 || a < prev_end || b > n) return "順序 / 範囲";
        if ((s[a] & 0xC0u) == 0x80u || (b < n && (s[b] & 0xC0u) == 0x80u)) return "文字境界";
        /* 区間外（前の区間の後ろ〜この区間の頭）は空白だけ */
        for (size_t i = prev_end; i < a;) { int L; int32_t c = dec(s, n, i, &L); if (!is_ws(c)) return "区間外に空白以外"; i += (size_t)L; }
        /* 中身がある / 端が空白でない / 中身のあとの終端で切れている */
        int seen = 0, after_run = 0, has = 0; int32_t first = -1, last = -1;
        for (size_t i = a; i < b;) {
            int L; int32_t c = dec(s, n, i, &L); i += (size_t)L;
            if (first < 0) first = c;
            last = c;
            if (is_content(c)) { has = 1; if (after_run) return "中身のあとの終端で切れていない"; seen = 1; }
            else if (is_run(c)) { if (seen && is_starter(c)) after_run = 1; }
            else if (after_run) { /* 空白 */ }
        }
        if (!has) return "中身の無い区間";
        if (is_ws(first) || is_ws(last)) return "区間の端が空白";
        /* 終端の並びの途中で切らない */
        if (b < n) { int L; int32_t c = dec(s, n, b, &L); if (is_run(c)) return "終端の並びの途中で切った"; }
        prev_end = b;
    }
    if (ns == 0) {
        for (size_t i = 0; i < n;) { int L; int32_t c = dec(s, n, i, &L); if (is_content(c)) return "中身があるのに区間 0"; i += (size_t)L; }
    } else {
        for (size_t i = prev_end; i < n;) { int L; int32_t c = dec(s, n, i, &L); if (!is_ws(c)) return "末尾の区間外に空白以外"; i += (size_t)L; }
    }
    return NULL;
}

static void g_sp2(void) {
    const int N = 10000;
    int pass = 0, shown = 0;
    char buf[40 * 4 + 1];
    for (int c = 0; c < N; ++c) {
        size_t n = 0;
        const int len = (int)(rnd() % 41u);
        for (int j = 0; j < len; ++j) {
            const char *a = ALPHA[rnd() % (uint32_t)NA];
            memcpy(buf + n, a, strlen(a)); n += strlen(a);
        }
        buf[n] = '\0';
        saan_span sp[MAXS]; int ns; int32_t eb = -1;
        const char *why = NULL;
        if (collect(saan_split_next, buf, n, sp, &ns, &eb) != 0) why = "区切りが失敗した";
        else why = props(buf, n, sp, ns);
        if (!why) { ++pass; continue; }
        if (shown++ < 5) NG("G-SP2 ケース %d: %s 「%s」", c, why, buf);
        else ++g_ng;
    }
    printf("%s  G-SP2 n=%d で %d/%d\n", pass == N ? "OK " : "NG!", N, pass, N);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    g_sp1();
    control();
    g_sp2();
    if (g_ng) { printf("NG! %d 件\n", g_ng); return 1; }
    printf("OK  全部通った\n");
    return 0;
}
