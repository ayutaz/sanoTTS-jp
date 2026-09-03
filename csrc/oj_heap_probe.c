/* K-5: 追跡アロケータ。詳細は oj_heap_probe.h。
 *
 * ⚠️ **このファイルはマクロを当てずにビルドする**（自分自身が calloc を呼ぶため）。
 */
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include "oj_heap_probe.h"
#undef calloc
#undef malloc
#undef strdup
#undef free

/* ポインタ → 要求バイト数。開番地法（線形探索）。
 * 容量は 2 の冪。**満杯になったら黙って諦めず落とす** — 静かに測り漏らすと
 * 「ピークが小さい」という嘘の数字が出る。 */
#define CAP (1u << 17)

static struct { void *p; size_t n; } tab[CAP];
static size_t n_used, peak, live, total, n_alloc, n_unknown;

static size_t slot(void *p) {
    size_t h = ((size_t)p >> 4) * 11400714819323198485ull;
    return h & (CAP - 1);
}

/* 表に入れるだけ。統計はいじらない（詰め直しで使う）。 */
static void insert(void *p, size_t n) {
    if (n_used * 4 >= CAP * 3) {
        fprintf(stderr, "NG: k5_alloc の表が満杯（%u）。CAP を増やすこと\n", CAP);
        abort();
    }
    size_t i = slot(p);
    while (tab[i].p) i = (i + 1) & (CAP - 1);
    tab[i].p = p; tab[i].n = n;
    n_used++;
}

static void put(void *p, size_t n) {
    if (!p) return;
    insert(p, n);
    live += n; total += n; n_alloc++;
    if (live > peak) peak = live;
}

/* 見つけたら要求バイト数を返し、詰め直す（線形探索なので墓石は使わない）。 */
static size_t take(void *p) {
    size_t i = slot(p);
    while (tab[i].p) {
        if (tab[i].p == p) {
            size_t n = tab[i].n;
            tab[i].p = NULL; tab[i].n = 0; n_used--;
            /* 後続のクラスタを詰め直す。⚠️ **統計を触らない `insert` を使う** —
             * `put` を使うと live が一瞬膨らんで peak が嘘をつく。 */
            size_t j = (i + 1) & (CAP - 1);
            while (tab[j].p) {
                void *q = tab[j].p; size_t m = tab[j].n;
                tab[j].p = NULL; tab[j].n = 0; n_used--;
                insert(q, m);
                j = (j + 1) & (CAP - 1);
            }
            return n;
        }
        i = (i + 1) & (CAP - 1);
    }
    return (size_t)-1;
}

void *oj_heap_calloc(size_t n, size_t s) {
    void *p = calloc(n, s);
    put(p, n * s);
    return p;
}

void *oj_heap_malloc(size_t n) {
    void *p = malloc(n);
    put(p, n);
    return p;
}

char *oj_heap_strdup(const char *s) {
    char *p = strdup(s);
    put(p, strlen(s) + 1);
    return p;
}

void oj_heap_free(void *p) {
    if (!p) return;
    size_t n = take(p);
    if (n == (size_t)-1) n_unknown++;
    else live -= n;
    free(p);
}

void oj_heap_reset(void) {
    memset(tab, 0, sizeof tab);
    n_used = peak = live = total = n_alloc = n_unknown = 0;
}

size_t oj_heap_peak(void)      { return peak; }
size_t oj_heap_live(void)      { return live; }
size_t oj_heap_total(void)     { return total; }
size_t oj_heap_n_alloc(void)   { return n_alloc; }
size_t oj_heap_n_unknown(void) { return n_unknown; }
