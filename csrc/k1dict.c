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
#include <stdio.h>
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
    /* version. ⚠️ 2 で poolck / termck が入った（K-6）。1 も読める。 */
    uint16_t ver = rd16(blob + 4);
    if (ver != 1 && ver != 2) return -2;
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
    /* classes セクションは [n u32][8 B × n][pos6 の NUL 区切り表] */
    d->pos6tab = s.p + 4 + 8u * d->n_classes;
    d->pos6tab_len = s.len - 4u - 8u * d->n_classes;
    if (find_sec(blob, n, "keytab", &s) != 0) return -9;
    d->keytab = s.p; d->keytab_len = s.len;
    if (find_sec(blob, n, "keyesc", &s) != 0) return -10;
    d->keyesc = s.p; d->keyesc_len = s.len;
    /* K-6 で要るもの。⚠️ 無くても K-2 / K-3 は動くので **任意**にしてある
     *    （古い blob をそのまま読めるように）。 */
    if (find_sec(blob, n, "moratab", &s) == 0) { d->moratab = s.p; d->moratab_len = s.len; }
    if (find_sec(blob, n, "chains", &s) == 0)  { d->chaintab = s.p; d->chaintab_len = s.len; }
    if (find_sec(blob, n, "poolck", &s) == 0)  { d->poolck = s.p; }
    if (find_sec(blob, n, "termck", &s) == 0)  { d->termck = s.p; d->n_termck = s.len / 4u; }
    if (find_sec(blob, n, "char", &s) == 0) {
        d->n_char_cats = rd32(s.p);
        d->char_names  = s.p + 4;
        d->char_info   = s.p + 4 + 32u * d->n_char_cats;
        d->n_codepoints = (s.len - 4u - 32u * d->n_char_cats) / 4u;
    }
    if (find_sec(blob, n, "unk", &s) == 0) {
        d->n_unk = rd32(s.p);
        d->unk = s.p + 4;
        d->unk_len = s.len - 4;
    }
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

/* 前方宣言。実体は K-6 の節（索引を使う）。 */
static uint32_t term_rank_fast(const k1_dict_t *d, uint32_t node);
#define term_rank(d, node) term_rank_fast((d), (node))

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

/* ------------------------------------------------- K-6: 素性の復元 */

/* 前方宣言（実体は下の「未知語」節にある）。 */
static size_t cp_to_utf8(uint32_t cp, char *out);
static const uint8_t *strtab_at(const uint8_t *tab, uint32_t len, int idx,
                                uint32_t *out_len);
static uint32_t key_codepoint(const k1_dict_t *d, const uint8_t *key, size_t p,
                              uint32_t *sym_bytes);

#define REC_FLAG_ORIG_EQ_SURFACE 0x01u
#define REC_FLAG_READ_EQ_PRON    0x02u
#define TERM_CHECKPOINT 512u

/* node より前の終端の数。⚠️ **索引が無いと O(n) 走査**（K-6 以前の実装）。 */
static uint32_t term_rank_fast(const k1_dict_t *d, uint32_t node) {
    if (!d->termck) {
        uint32_t r = 0;
        for (uint32_t i = 0; i < node; i++) r += bit_at(d->term_bits, i);
        return r;
    }
    uint32_t base = node / TERM_CHECKPOINT;
    uint32_t r = rd32(d->termck + 4u * base);
    for (uint32_t i = base * TERM_CHECKPOINT; i < node; i++)
        r += bit_at(d->term_bits, i);
    return r;
}

/* 終端 rank のノード番号。無ければ 0xFFFFFFFF。 */
static uint32_t node_of_term_rank(const k1_dict_t *d, uint32_t rank) {
    if (!d->termck) return 0xFFFFFFFFu;
    /* 累積が rank 以下である最後のチェックポイントを二分探索 */
    uint32_t lo = 0, hi = d->n_termck;
    while (lo + 1 < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (rd32(d->termck + 4u * mid) <= rank) lo = mid; else hi = mid;
    }
    uint32_t r = rd32(d->termck + 4u * lo);
    for (uint32_t i = lo * TERM_CHECKPOINT; i < d->n_nodes; i++) {
        if (bit_at(d->term_bits, i)) {
            if (r == rank) return i;
            r++;
        }
    }
    return 0xFFFFFFFFu;
}

/* node 番目の 1 のビット位置（LOUDS の select1）。 */
static uint32_t select1(const k1_dict_t *d, uint32_t node) {
    uint32_t lo = 0, hi = d->louds_bitlen;
    while (lo + 1 < hi) {                    /* rank1(x) <= node の最大 x */
        uint32_t mid = (lo + hi) / 2;
        if (rank1(d, mid) <= node) lo = mid; else hi = mid;
    }
    while (lo < d->louds_bitlen && !bit_at(d->louds_bits, lo)) lo++;
    return lo;
}

/* ビット位置 i より前の 0 の数。 */
static uint32_t rank0(const k1_dict_t *d, uint32_t i) { return i - rank1(d, i); }

int k1_surface_of_rank(const k1_dict_t *d, uint32_t rank, char *out, size_t out_n) {
    uint32_t node = node_of_term_rank(d, rank);
    if (node == 0xFFFFFFFFu) return -1;
    /* 親へ遡って鍵バイトを逆順に集める */
    uint8_t key[256];
    int kn = 0;
    while (node != 0) {
        if (kn >= (int)sizeof key) return -2;
        key[kn++] = d->labels[node];
        uint32_t p = select1(d, node);
        uint32_t parent = rank0(d, p);
        if (parent == 0) break;              /* 根の子 */
        node = parent - 1;
    }
    /* 鍵は逆順。前から復号して UTF-8 にする */
    size_t o = 0;
    for (int i = kn - 1; i >= 0; ) {
        /* key_codepoint は前方向の並びを前提にするので、いったん整列する */
        uint8_t buf[8]; int bn = 0;
        buf[bn++] = key[i];
        if (key[i] == 0xFE && i >= 3) {      /* 直接コードポイント（4 B） */
            buf[bn++] = key[i-1]; buf[bn++] = key[i-2]; buf[bn++] = key[i-3];
            i -= 4;
        } else if (key[i] == 0xFF && i >= 2) {
            buf[bn++] = key[i-1]; buf[bn++] = key[i-2];
            i -= 3;
        } else {
            i -= 1;
        }
        uint32_t sym; (void)sym;
        uint32_t cp = key_codepoint(d, buf, 0, &sym);
        /* コードポイント → UTF-8 */
        if (o + 4 >= out_n) return -3;
        o += cp_to_utf8(cp, out + o);
    }
    out[o] = 0;
    return (int)o;
}

/* コードポイント → UTF-8。書いたバイト数を返す。 */
static size_t cp_to_utf8(uint32_t cp, char *out) {
    if (cp < 0x80) { out[0] = (char)cp; return 1; }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

int k1_key_to_utf8(const k1_dict_t *d, const uint8_t *key, size_t from, size_t to,
                   char *out, size_t out_n) {
    size_t o = 0;
    for (size_t p = from; p < to; ) {
        uint32_t sym = 0;
        uint32_t cp = key_codepoint(d, key, p, &sym);
        if (sym == 0) return -1;
        if (o + 4 >= out_n) return -2;
        o += cp_to_utf8(cp, out + o);
        p += sym;
    }
    out[o] = 0;
    return (int)o;
}

/* 値プール中の entry のオフセット。 */
static uint32_t pool_offset(const k1_dict_t *d, uint32_t entry) {
    uint32_t base = entry / CHECKPOINT;
    uint32_t off = d->poolck ? rd32(d->poolck + 4u * base) : 0;
    uint32_t from = d->poolck ? base * CHECKPOINT : 0;
    for (uint32_t j = from; j < entry; j++) {
        const uint8_t *r = d->records + (size_t)j * REC_SIZE;
        off += r[7] + r[8];                  /* pool_len + extra_len */
    }
    return off;
}

/* モーラ ID 列 → UTF-8。書いたバイト数を返す。 */
static size_t mora_decode(const k1_dict_t *d, const uint8_t *p, size_t n,
                          char *out, size_t out_n) {
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        int idx = p[i];
        if (idx == 0xFF && i + 2 < n) {       /* エスケープ（副表） */
            idx = ((int)p[i+1] << 8) | p[i+2];
            i += 2;
        }
        uint32_t len; const uint8_t *s = strtab_at(d->moratab, d->moratab_len, idx, &len);
        if (!s || o + len >= out_n) break;
        memcpy(out + o, s, len); o += len;
    }
    out[o] = 0;
    return o;
}

static size_t put(char *out, size_t o, size_t out_n, const char *s) {
    size_t n = strlen(s);
    if (o + n >= out_n) return o;
    memcpy(out + o, s, n);
    return o + n;
}

int k1_entry_feature(const k1_dict_t *d, uint32_t entry,
                     const char *surface, char *out, size_t out_n) {
    if (entry >= d->n_entries) return -1;
    const uint8_t *r = d->records + (size_t)entry * REC_SIZE;
    uint16_t cid = rd16(r);
    uint16_t chid = rd16(r + 4);
    uint8_t flags = r[6], pl = r[7], el = r[8];
    if (cid >= d->n_classes) return -2;
    uint16_t p6 = rd16(d->classes + 8u * cid + 4);

    uint32_t off = pool_offset(d, entry);
    const uint8_t *pb = d->pool + off;
    const uint8_t *ex = pb + pl;

    char pron[256], read[256], orig[256];
    mora_decode(d, pb, pl, pron, sizeof pron);

    uint32_t j = 0;
    if (flags & REC_FLAG_ORIG_EQ_SURFACE) {
        snprintf(orig, sizeof orig, "%s", surface);
    } else if (ex[j] == 1) {
        uint32_t sid = (uint32_t)ex[j+1] | ((uint32_t)ex[j+2] << 8)
                     | ((uint32_t)ex[j+3] << 16);
        if (k1_surface_of_rank(d, sid, orig, sizeof orig) < 0) return -3;
        j += 4;
    } else {
        uint32_t n = ex[j+1];
        if (n >= sizeof orig) return -4;
        memcpy(orig, ex + j + 2, n); orig[n] = 0;
        j += 2 + n;
    }
    if (flags & REC_FLAG_READ_EQ_PRON) {
        snprintf(read, sizeof read, "%s", pron);
    } else {
        uint32_t n = ex[j];
        mora_decode(d, ex + j + 1, n, read, sizeof read);
        j += 1 + n;
    }

    uint32_t p6len; const uint8_t *p6s = strtab_at(d->pos6tab, d->pos6tab_len,
                                                   p6, &p6len);
    uint32_t chlen; const uint8_t *chs = strtab_at(d->chaintab, d->chaintab_len,
                                                   chid, &chlen);
    if (!p6s || !chs) return -5;

    size_t o = 0;
    o = put(out, o, out_n, surface);
    if (o + 1 < out_n) out[o++] = ',';
    if (o + p6len < out_n) { memcpy(out + o, p6s, p6len); o += p6len; }
    if (o + 1 < out_n) out[o++] = ',';
    o = put(out, o, out_n, orig);
    if (o + 1 < out_n) out[o++] = ',';
    o = put(out, o, out_n, read);
    if (o + 1 < out_n) out[o++] = ',';
    o = put(out, o, out_n, pron);
    if (o + 1 < out_n) out[o++] = ',';
    /* アクセント: "acc/mora" を ':' で連結。255 は '*' */
    int first = 1;
    for (; j + 1 < el; j += 2) {
        char tmp[24];
        if (!first && o + 1 < out_n) out[o++] = ':';
        first = 0;
        /* ⚠️ **必ず "a/m" の形で書く。** 255 は "*"。
         *    Python の `_decode_acc` がそう書くので、`*` 単独にすると
         *    記号（読点など）で 251 / 300 文が食い違う。 */
        char a[8], m[8];
        if (ex[j] == 255) snprintf(a, sizeof a, "*"); else snprintf(a, sizeof a, "%u", ex[j]);
        if (ex[j+1] == 255) snprintf(m, sizeof m, "*"); else snprintf(m, sizeof m, "%u", ex[j+1]);
        snprintf(tmp, sizeof tmp, "%s/%s", a, m);
        o = put(out, o, out_n, tmp);
    }
    if (o + 1 < out_n) out[o++] = ',';
    if (o + chlen < out_n) { memcpy(out + o, chs, chlen); o += chlen; }
    if (o >= out_n) return -6;
    out[o] = 0;
    return (int)o;
}

int16_t k1_trans(const k1_dict_t *d, uint16_t rc_prev, uint16_t lc_cur) {
    if (!d->matrix) return 0;
    return d->matrix[(size_t)rc_prev + (size_t)d->lsize * lc_cur];
}

/* ---------------------------------------------------------------- 未知語 */

#define MAX_GROUPING 24u          /* MeCab の MAX_GROUPING_SIZE */

static uint32_t char_raw(const k1_dict_t *d, uint32_t cp) {
    if (!d->char_info || cp >= d->n_codepoints) return 0;   /* 表の外は DEFAULT */
    return rd32(d->char_info + 4u * cp);
}
static uint32_t ci_type(uint32_t v)    { return v & 0x3FFFFu; }
static uint32_t ci_default(uint32_t v) { return (v >> 18) & 0xFFu; }
static uint32_t ci_length(uint32_t v)  { return (v >> 26) & 0xFu; }
static uint32_t ci_group(uint32_t v)   { return (v >> 30) & 1u; }
static uint32_t ci_invoke(uint32_t v)  { return (v >> 31) & 1u; }

/* NUL 区切り表の idx 番目を返す（長さも返す）。 */
static const uint8_t *strtab_at(const uint8_t *tab, uint32_t len, int idx,
                                uint32_t *out_len) {
    uint32_t i = 0; int k = 0;
    while (i < len) {
        uint32_t j = i;
        while (j < len && tab[j]) j++;
        if (k == idx) { *out_len = j - i; return tab + i; }
        k++; i = j + 1;
    }
    *out_len = 0; return NULL;
}

/* 鍵バイト列の位置 p にある記号のコードポイントと、記号のバイト長。 */
static uint32_t key_codepoint(const k1_dict_t *d, const uint8_t *key, size_t p,
                              uint32_t *sym_bytes) {
    uint8_t c = key[p];
    if (c == 0xFE) { *sym_bytes = 4;
        return ((uint32_t)key[p+1] << 16) | ((uint32_t)key[p+2] << 8) | key[p+3]; }
    if (c == 0xFF) {
        *sym_bytes = 3;
        int idx = ((int)key[p+1] << 8) | key[p+2];
        uint32_t n; const uint8_t *u = strtab_at(d->keyesc, d->keyesc_len, idx, &n);
        return u ? utf8_cp(u, n) : 0;
    }
    *sym_bytes = 1;
    uint32_t n; const uint8_t *u = strtab_at(d->keytab, d->keytab_len, c, &n);
    return u ? utf8_cp(u, n) : 0;
}

/* unk エントリ i の (lc, rc, wcost, カテゴリ名)。 */
static int unk_at(const k1_dict_t *d, uint32_t i, uint16_t *lc, uint16_t *rc,
                  int16_t *wcost, const uint8_t **cat, uint32_t *cat_len) {
    const uint8_t *p = d->unk;
    const uint8_t *e = d->unk + d->unk_len;
    for (uint32_t k = 0; k < d->n_unk; k++) {
        if (p + 6 > e) return -1;
        uint16_t l = rd16(p), r = rd16(p + 2); int16_t w = (int16_t)rd16(p + 4);
        p += 6;
        const uint8_t *c0 = p;
        while (p < e && *p) p++;
        uint32_t clen = (uint32_t)(p - c0); p++;          /* カテゴリ名 */
        while (p < e && *p) p++;
        p++;                                              /* feature */
        if (k == i) { *lc = l; *rc = r; *wcost = w; *cat = c0; *cat_len = clen; return 0; }
    }
    return -1;
}

/* カテゴリ番号 cat の unk エントリを列挙する。 */
static int unk_of_category(const k1_dict_t *d, uint32_t cat,
                           uint32_t *out, int max_out) {
    if (!d->unk || !d->char_names || cat >= d->n_char_cats) return 0;
    const uint8_t *want = d->char_names + 32u * cat;
    uint32_t wlen = 0;
    while (wlen < 32 && want[wlen]) wlen++;
    int n = 0;
    for (uint32_t i = 0; i < d->n_unk; i++) {
        uint16_t lc, rc; int16_t w; const uint8_t *c; uint32_t cl;
        if (unk_at(d, i, &lc, &rc, &w, &c, &cl) != 0) break;
        if (cl == wlen && memcmp(c, want, wlen) == 0 && n < max_out) out[n++] = i;
    }
    return n;
}

/* ---------------------------------------------------------------- Viterbi */

typedef struct {
    uint32_t begin, end, entry;
    uint16_t lc, rc;
    int32_t  cost;
    int32_t  prev;     /* -1 = BOS */
} vnode_t;

/* nodes[k] の前任を選び、終端リストに繋ぐ。既知語と未知語で共有する。 */
static int link_node(vnode_t *nodes, int32_t *ends_head, int32_t *next_at_end,
                     uint32_t k, size_t i, int16_t wcost, int use_cost,
                     const k1_dict_t *d) {
    vnode_t *v = &nodes[k];
    if (i == 0) {
        v->cost = (use_cost ? k1_trans(d, BOS_RC, v->lc) : 0) + wcost;
        v->prev = -1;
    } else {
        int32_t best = COST_INF, bp = -2;
        for (int32_t pk = ends_head[i]; pk >= 0; pk = next_at_end[pk]) {
            if (nodes[pk].cost >= COST_INF) continue;
            int32_t c = nodes[pk].cost
                      + (use_cost ? k1_trans(d, nodes[pk].rc, v->lc) : 0);
            /* ⚠️ 同点は辞書順で先勝ち（= ノード番号が小さい方）。K-2 で踏んだ。 */
            if (c < best || (c == best && (bp < 0 || pk < bp))) { best = c; bp = pk; }
        }
        if (bp == -2) return 0;
        v->cost = best + wcost;
        v->prev = bp;
    }
    next_at_end[k] = ends_head[v->end];
    ends_head[v->end] = (int32_t)k;
    return 1;
}

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

        /* --- 未知語ノード（K-3）--------------------------------------
         * MeCab の規則: 辞書に当たらなかったか、その文字カテゴリが invoke なら
         * 未知語ノードを作る。group なら同カテゴリの連なり（最大 24 文字）を
         * 1 ノードに、length があれば 1..length 文字のノードも作る。
         * ⚠️ wcost は unk.dic の値を**そのまま**使う（実測で確認。スケールしない）。 */
        uint32_t unk_len_list[MAX_GROUPING + 2];
        int n_unk_len = 0;
        if (d->unk && d->char_info) {
            uint32_t sb = 0;
            uint32_t cp0 = key_codepoint(d, key, i, &sb);
            uint32_t v0 = char_raw(d, cp0);
            if (nh == 0 || ci_invoke(v0)) {
                uint32_t cat = ci_default(v0);
                /* group: 同カテゴリの連なり */
                if (ci_group(v0)) {
                    size_t p2 = i + sb;
                    uint32_t nch = 1;
                    while (p2 < key_n && nch < MAX_GROUPING) {
                        uint32_t sb2 = 0;
                        uint32_t cpx = key_codepoint(d, key, p2, &sb2);
                        if (!(ci_type(char_raw(d, cpx)) & (1u << cat))) break;
                        p2 += sb2; nch++;
                    }
                    unk_len_list[n_unk_len++] = (uint32_t)(p2 - i);
                }
                /* length: 1..length 文字。
                 * ⚠️ **同カテゴリの文字しか伸ばせない。** これを入れないと
                 *    「たけーな」で `ー`(KATAKANA, length=2) が次の `な`(HIRAGANA) を
                 *    巻き込んで 2 文字ノードになり、MeCab と食い違う（実際に踏んだ）。 */
                for (uint32_t k2 = 1; k2 <= ci_length(v0); k2++) {
                    size_t p2 = i; uint32_t m2 = 0;
                    while (p2 < key_n && m2 < k2) {
                        uint32_t sb2 = 0;
                        uint32_t cpx = key_codepoint(d, key, p2, &sb2);
                        if (m2 > 0 && !(ci_type(char_raw(d, cpx)) & (1u << cat))) break;
                        p2 += sb2; m2++;
                    }
                    if (m2 != k2) break;
                    uint32_t bl = (uint32_t)(p2 - i);
                    int dup = 0;
                    for (int z = 0; z < n_unk_len; z++) if (unk_len_list[z] == bl) dup = 1;
                    if (!dup) unk_len_list[n_unk_len++] = bl;
                }
                uint32_t ids[32];
                int nid = unk_of_category(d, cat, ids, 32);
                for (int z = 0; z < n_unk_len; z++) {
                    for (int y = 0; y < nid; y++) {
                        if (n >= cap) return -1;
                        uint16_t lc, rc; int16_t w;
                        const uint8_t *cn; uint32_t cl;
                        if (unk_at(d, ids[y], &lc, &rc, &w, &cn, &cl) != 0) continue;
                        vnode_t *v = &nodes[n];
                        v->begin = (uint32_t)i;
                        v->end   = (uint32_t)(i + unk_len_list[z]);
                        v->entry = K1_UNKNOWN_FLAG | ids[y];
                        v->lc = lc; v->rc = rc;
                        v->cost = COST_INF; v->prev = -2;
                        if (link_node(nodes, ends_head, next_at_end, n, i, w, use_cost, d))
                            { }
                        n++;
                    }
                }
            }
        }

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
                /* ⚠️ 前任は位置ごとではなく **ノードごと**に持つ（link_node） */
                link_node(nodes, ends_head, next_at_end, n, i, w, use_cost, d);
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

/* 未知語の feature。⚠️ **8 列しか無い**（読み/発音/acc/結合規則が無い）。
 * これが「未知語は無音で消える」の入口（B-0 / M-73）。 */
int k1_unk_feature(const k1_dict_t *d, uint32_t entry,
                   const char *surface, char *out, size_t out_n) {
    if (!(entry & K1_UNKNOWN_FLAG)) return -1;
    uint32_t i = entry & ~K1_UNKNOWN_FLAG;
    const uint8_t *p = d->unk;
    const uint8_t *e = d->unk + d->unk_len;
    for (uint32_t k = 0; k < d->n_unk; k++) {
        if (p + 6 > e) return -2;
        p += 6;
        while (p < e && *p) p++;             /* カテゴリ名 */
        p++;
        const uint8_t *f0 = p;
        while (p < e && *p) p++;             /* feature 本体 */
        uint32_t flen = (uint32_t)(p - f0);
        p++;
        if (k == i) {
            size_t n = strlen(surface);
            if (n + 1 + flen >= out_n) return -3;
            memcpy(out, surface, n);
            out[n] = ',';
            memcpy(out + n + 1, f0, flen);
            out[n + 1 + flen] = 0;
            return (int)(n + 1 + flen);
        }
    }
    return -4;
}

int k1_analyze(const k1_dict_t *d, const uint8_t *key, size_t key_n,
               void *arena, size_t arena_n, k1_token_t *out, int max_out) {
    return analyze_impl(d, key, key_n, arena, arena_n, out, max_out, 1);
}

int k1_analyze_nocost(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                      void *arena, size_t arena_n, k1_token_t *out, int max_out) {
    return analyze_impl(d, key, key_n, arena, arena_n, out, max_out, 0);
}
