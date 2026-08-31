/* K-1 辞書バイナリの読み出しと K-2 の Viterbi。詳細は k1dict.h。
 *
 * ⚠️ 実装で必ず踏む落とし穴（K-1 §9-3 / §9-4。全部実際に踏んだ）:
 *   - 遷移コストの索引は flat[rc_prev + lsize*lc_cur]。
 *     MeCab 本家の式 (lcAttr + lsize*rcAttr) では**この辞書に合わない**（一致 0.91%）
 *   - LOUDS の子ノード番号は rank1(p)。rank1(p+1) にすると落ちずに
 *     **全部 1 文字に分割される**
 *   - 位置ごとに 1 本だけ最良経路を残すのは Viterbi ではない。
 *     同じ位置で終わるノードでも rc が違えば後続コストが変わるので、
 *     **ノードごと**に最良の前任を持つ
 */
#include "k1dict.h"
#include <string.h>

#define SUPER_BITS   256u
#define BLOCK_BITS    64u
#define SELECT_STEP  512u
#define CHECKPOINT    32u
#define REC_SIZE       9u
#define BOS_RC         0u
#define EOS_LC         0u
#define COST_INF   0x3FFFFFFF

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16)
           | ((uint32_t)p[3] << 24);
}
static uint16_t rd16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

/* ---------------------------------------------------------------- 区画 */

struct sec { const uint8_t *p; uint32_t len; };

static int find_sec(const uint8_t *blob, size_t n, const char *name, struct sec *out) {
    if (n < 8) return -1;
    uint16_t n_sec = rd16(blob + 6);
    for (uint16_t i = 0; i < n_sec; i++) {
        const uint8_t *e = blob + 8 + 16u * i;
        if ((size_t)(e - blob) + 16 > n) return -1;
        char nm[9]; memcpy(nm, e, 8); nm[8] = 0;
        if (strcmp(nm, name) == 0) {
            uint32_t off = rd32(e + 8), len = rd32(e + 12);
            if ((size_t)off + len > n) return -1;
            out->p = blob + off; out->len = len; return 0;
        }
    }
    return -1;
}

int k1_open(k1_dict_t *d, const uint8_t *blob, size_t n) {
    struct sec s;
    memset(d, 0, sizeof *d);
    if (n < 8 || memcmp(blob, "K1D1", 4) != 0) return -1;
    if (rd16(blob + 4) != 1) return -2;                 /* version */
    d->blob = blob; d->blob_len = n;

    if (find_sec(blob, n, "louds", &s) != 0) return -3;
    d->louds_bitlen = rd32(s.p);
    d->n_nodes      = rd32(s.p + 4);
    uint32_t n_sup  = rd32(s.p + 8), n_blk = rd32(s.p + 12), n_sel = rd32(s.p + 16);
    const uint8_t *q = s.p + 20;
    d->louds_bits = q; q += (d->louds_bitlen + 7) / 8;
    d->labels     = q; q += d->n_nodes;
    d->term_bits  = q; q += (d->n_nodes + 7) / 8;
    d->rank_sup = q; q += n_sup;
    d->rank_blk = q; q += n_blk;
    d->sel0     = q; d->n_sel0 = n_sel / 4;

    if (find_sec(blob, n, "counts", &s) != 0) return -4;
    d->counts = s.p; d->n_surfaces = s.len;
    if (find_sec(blob, n, "surfck", &s) != 0) return -5;
    d->surfck = s.p;
    if (find_sec(blob, n, "records", &s) != 0) return -6;
    d->records = s.p; d->n_entries = s.len / REC_SIZE;
    if (find_sec(blob, n, "pool", &s) != 0) return -7;
    d->pool = s.p; d->pool_len = s.len;
    if (find_sec(blob, n, "classes", &s) != 0) return -8;
    d->n_classes = rd32(s.p);
    d->classes = s.p + 4;
    if (find_sec(blob, n, "keytab", &s) != 0) return -9;
    d->keytab = s.p; d->keytab_len = s.len;
    if (find_sec(blob, n, "keyesc", &s) != 0) return -10;
    d->keyesc = s.p; d->keyesc_len = s.len;
    if (find_sec(blob, n, "matrix", &s) == 0) {
        d->lsize = rd16(s.p); d->rsize = rd16(s.p + 2);
        d->matrix = (const int16_t *)(const void *)(s.p + 4);
    }
    return 0;
}

/* ---------------------------------------------------------------- 鍵 */

/* NUL 区切りの表から idx 番目の項目を探す。見つからなければ -1。 */
static int strtab_find(const uint8_t *tab, uint32_t len,
                       const uint8_t *item, uint32_t item_len) {
    uint32_t i = 0; int idx = 0;
    while (i < len) {
        uint32_t j = i;
        while (j < len && tab[j]) j++;
        if (j - i == item_len && memcmp(tab + i, item, item_len) == 0) return idx;
        idx++; i = j + 1;
    }
    return -1;
}

static uint32_t utf8_len(uint8_t c) {
    if (c < 0x80) return 1;
    if ((c & 0xE0) == 0xC0) return 2;
    if ((c & 0xF0) == 0xE0) return 3;
    return 4;
}

static uint32_t utf8_cp(const uint8_t *p, uint32_t n) {
    if (n == 1) return p[0];
    if (n == 2) return (uint32_t)((p[0] & 0x1F) << 6) | (uint32_t)(p[1] & 0x3F);
    if (n == 3) return (uint32_t)((p[0] & 0x0F) << 12)
                     | (uint32_t)((p[1] & 0x3F) << 6) | (uint32_t)(p[2] & 0x3F);
    return (uint32_t)((p[0] & 0x07) << 18) | (uint32_t)((p[1] & 0x3F) << 12)
         | (uint32_t)((p[2] & 0x3F) << 6) | (uint32_t)(p[3] & 0x3F);
}

int k1_encode_key(const k1_dict_t *d, const uint8_t *u, size_t n,
                  uint8_t *out, size_t *out_n) {
    size_t o = 0, i = 0;
    while (i < n) {
        uint32_t cl = utf8_len(u[i]);
        if (i + cl > n) return -1;
        int id = strtab_find(d->keytab, d->keytab_len, u + i, cl);
        if (id >= 0) {
            if (o + 1 > *out_n) return -2;
            out[o++] = (uint8_t)id;
        } else {
            int e = strtab_find(d->keyesc, d->keyesc_len, u + i, cl);
            if (e >= 0) {
                if (o + 3 > *out_n) return -2;
                out[o++] = 0xFF;
                out[o++] = (uint8_t)((e >> 8) & 0xFF);
                out[o++] = (uint8_t)(e & 0xFF);
            } else {
                uint32_t cp = utf8_cp(u + i, cl);
                if (o + 4 > *out_n) return -2;
                out[o++] = 0xFE;
                out[o++] = (uint8_t)((cp >> 16) & 0xFF);
                out[o++] = (uint8_t)((cp >> 8) & 0xFF);
                out[o++] = (uint8_t)(cp & 0xFF);
            }
        }
        i += cl;
    }
    *out_n = o;
    return 0;
}

/* ---------------------------------------------------------------- LOUDS */

static uint32_t bit_at(const uint8_t *b, uint32_t i) {
    return (uint32_t)((b[i >> 3] >> (i & 7)) & 1);
}

static uint32_t popcnt8(uint8_t x) {
    static const uint8_t t[16] = {0,1,1,2,1,2,2,3,1,2,2,3,2,3,3,4};
    return (uint32_t)(t[x & 0xF] + t[x >> 4]);
}

static uint32_t rank1(const k1_dict_t *d, uint32_t i) {
    uint32_t r = rd32(d->rank_sup + 4u * (i / SUPER_BITS));
    r += d->rank_blk[i / BLOCK_BITS];
    uint32_t base = i - (i % BLOCK_BITS);
    uint32_t j = base;
    for (; j + 8 <= i; j += 8) r += popcnt8(d->louds_bits[j >> 3]);
    for (; j < i; j++) r += bit_at(d->louds_bits, j);
    return r;
}

static uint32_t select0(const k1_dict_t *d, uint32_t k) {
    uint32_t base = k / SELECT_STEP;
    uint32_t pos = rd32(d->sel0 + 4u * base);
    uint32_t need = k - base * SELECT_STEP;
    while (need) { if (!bit_at(d->louds_bits, pos)) need--; pos++; }
    while (bit_at(d->louds_bits, pos)) pos++;
    return pos;
}

/* node の子のうちラベルが ch のもの。無ければ -1。 */
static int32_t child_of(const k1_dict_t *d, uint32_t node, uint8_t ch) {
    uint32_t p = select0(d, node) + 1;
    while (p < d->louds_bitlen && bit_at(d->louds_bits, p)) {
        uint32_t c = rank1(d, p);            /* ⚠️ p より前の 1 の数 */
        if (c < d->n_nodes && d->labels[c] == ch) return (int32_t)c;
        p++;
    }
    return -1;
}

static uint32_t term_rank(const k1_dict_t *d, uint32_t node) {
    uint32_t r = 0;
    for (uint32_t i = 0; i < node; i++) r += bit_at(d->term_bits, i);
    return r;
}

int k1_common_prefix_search(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                            size_t start, k1_hit_t *out, int max_out) {
    int n = 0;
    uint32_t node = 0;
    for (size_t k = start; k < key_n; k++) {
        int32_t nx = child_of(d, node, key[k]);
        if (nx < 0) break;
        node = (uint32_t)nx;
        if (bit_at(d->term_bits, node)) {
            if (n < max_out) {
                out[n].len = (uint32_t)(k - start + 1);
                out[n].rank = term_rank(d, node);
                n++;
            }
        }
    }
    return n;
}

/* ---------------------------------------------------------------- エントリ */

void k1_entry_range(const k1_dict_t *d, uint32_t rank,
                    uint32_t *first, uint32_t *count) {
    uint32_t base = rank / CHECKPOINT;
    uint32_t off = rd32(d->surfck + 4u * base);
    for (uint32_t j = base * CHECKPOINT; j < rank; j++) off += d->counts[j];
    *first = off;
    *count = d->counts[rank];
}

void k1_entry_conn(const k1_dict_t *d, uint32_t entry,
                   uint16_t *lc, uint16_t *rc, int16_t *wcost) {
    const uint8_t *r = d->records + (size_t)entry * REC_SIZE;
    uint16_t cid = rd16(r);
    *wcost = (int16_t)rd16(r + 2);
    const uint8_t *c = d->classes + 8u * cid;
    *lc = rd16(c);
    *rc = rd16(c + 2);
}

int16_t k1_trans(const k1_dict_t *d, uint16_t rc_prev, uint16_t lc_cur) {
    if (!d->matrix) return 0;
    return d->matrix[(size_t)rc_prev + (size_t)d->lsize * lc_cur];
}

/* ---------------------------------------------------------------- Viterbi */

typedef struct {
    uint32_t begin, end, entry;
    uint16_t lc, rc;
    int32_t  cost;
    int32_t  prev;     /* -1 = BOS */
} vnode_t;

static int analyze_impl(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                        void *arena, size_t arena_n, k1_token_t *out, int max_out,
                        int use_cost) {
    size_t cap = arena_n / (sizeof(vnode_t) + sizeof(int32_t) * 2);
    if (cap < 8) return -1;
    vnode_t *nodes = (vnode_t *)arena;
    int32_t *ends_head = (int32_t *)(void *)(nodes + cap);      /* 位置 -> 先頭 */
    int32_t *next_at_end = ends_head + (key_n + 1);             /* 連結リスト */
    if ((size_t)(key_n + 1) * 2 > cap) return -1;

    for (size_t i = 0; i <= key_n; i++) ends_head[i] = -1;

    uint32_t n = 0;
    k1_hit_t hits[64];
    for (size_t i = 0; i <= key_n; i++) {
        /* この位置に到達できるか（BOS または既存ノードの終端） */
        if (i > 0 && ends_head[i] < 0) continue;
        if (i == key_n) continue;
        int nh = k1_common_prefix_search(d, key, key_n, i, hits, 64);
        for (int h = 0; h < nh; h++) {
            uint32_t first, cnt;
            k1_entry_range(d, hits[h].rank, &first, &cnt);
            for (uint32_t e = 0; e < cnt; e++) {
                if (n >= cap) return -1;
                uint16_t lc, rc; int16_t w;
                k1_entry_conn(d, first + e, &lc, &rc, &w);
                vnode_t *v = &nodes[n];
                v->begin = (uint32_t)i;
                v->end   = (uint32_t)(i + hits[h].len);
                v->entry = first + e;
                v->lc = lc; v->rc = rc;
                v->cost = COST_INF; v->prev = -2;
                /* 前任を選ぶ。⚠️ 位置ごとではなく **ノードごと**に持つ */
                if (i == 0) {
                    v->cost = (use_cost ? k1_trans(d, BOS_RC, lc) : 0) + w;
                    v->prev = -1;
                } else {
                    int32_t best = COST_INF, bp = -2;
                    for (int32_t pk = ends_head[i]; pk >= 0; pk = next_at_end[pk]) {
                        if (nodes[pk].cost >= COST_INF) continue;
                        int32_t c = nodes[pk].cost
                                  + (use_cost ? k1_trans(d, nodes[pk].rc, lc) : 0);
                        /* ⚠️ **同点は辞書順で先勝ち**（= ノード番号が小さい方）。
                         *    同綴り異義語は lc/rc/wcost が同じことがあり、
                         *    コストでは決着しない。実際 白熊(シロクマ/ハグマ) や
                         *    漁り(アサリ/スナドリ) が read/pron/acc だけ違って同点になる。
                         *    連結リストは生成と逆順なので、ここで明示的に決める。 */
                        if (c < best || (c == best && (bp < 0 || pk < bp))) {
                            best = c; bp = pk;
                        }
                    }
                    if (bp != -2) { v->cost = best + w; v->prev = bp; }
                }
                if (v->prev != -2) {
                    next_at_end[n] = ends_head[v->end];
                    ends_head[v->end] = (int32_t)n;
                }
                n++;
            }
        }
    }

    /* EOS */
    int32_t best = COST_INF, bk = -2;
    for (int32_t pk = ends_head[key_n]; pk >= 0; pk = next_at_end[pk]) {
        if (nodes[pk].cost >= COST_INF) continue;
        int32_t c = nodes[pk].cost + (use_cost ? k1_trans(d, nodes[pk].rc, EOS_LC) : 0);
        if (c < best || (c == best && (bk < 0 || pk < bk))) { best = c; bk = pk; }
    }
    if (bk == -2) return -1;

    int cnt = 0;
    for (int32_t k = bk; k != -1; k = nodes[k].prev) cnt++;
    if (cnt > max_out) return -1;
    int w = cnt;
    for (int32_t k = bk; k != -1; k = nodes[k].prev) {
        w--;
        out[w].begin = nodes[k].begin;
        out[w].end   = nodes[k].end;
        out[w].entry = nodes[k].entry;
    }
    return cnt;
}

int k1_analyze(const k1_dict_t *d, const uint8_t *key, size_t key_n,
               void *arena, size_t arena_n, k1_token_t *out, int max_out) {
    return analyze_impl(d, key, key_n, arena, arena_n, out, max_out, 1);
}

int k1_analyze_nocost(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                      void *arena, size_t arena_n, k1_token_t *out, int max_out) {
    return analyze_impl(d, key, key_n, arena, arena_n, out, max_out, 0);
}
