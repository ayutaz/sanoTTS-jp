/* saanoTTS-jp 推論コア（C99 / 依存は libm のみ） */
#include "saanotts.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define NAME_LEN 64
#define HDR_ENT  (NAME_LEN + 4 + 4 + 16 + 8 + 8)
#define ALIGN16(x) (((x) + 15u) & ~(size_t)15u)

/* --- 重みブロブ ---------------------------------------------------------- */

static uint32_t rd_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t rd_u64(const uint8_t *p) {
    return (uint64_t)rd_u32(p) | ((uint64_t)rd_u32(p + 4) << 32);
}

saan_status saan_weights_open(saan_weights *w, const void *blob, size_t size) {
    const uint8_t *b = (const uint8_t *)blob;
    if (size < 16 || memcmp(b, "SAAN", 4) != 0) return SAAN_ERR_MAGIC;
    if (rd_u32(b + 4) != 1u) return SAAN_ERR_VERSION;
    w->base = b;
    w->size = size;
    w->n_tensors = rd_u32(b + 8);
    if (16u + (size_t)w->n_tensors * HDR_ENT > size) return SAAN_ERR_SHAPE;
    return SAAN_OK;
}

const void *saan_tensor(const saan_weights *w, const char *name,
                        uint32_t *dtype, uint32_t dims[4], uint64_t *nbytes) {
    for (uint32_t i = 0; i < w->n_tensors; ++i) {
        const uint8_t *e = w->base + 16 + (size_t)i * HDR_ENT;
        if (strncmp((const char *)e, name, NAME_LEN) != 0) continue;
        const uint8_t *p = e + NAME_LEN;
        if (dtype) *dtype = rd_u32(p);
        if (dims) for (int k = 0; k < 4; ++k) dims[k] = rd_u32(p + 8 + 4 * k);
        uint64_t off = rd_u64(p + 24), nb = rd_u64(p + 32);
        if (off + nb > w->size) return NULL;
        if (nbytes) *nbytes = nb;
        return w->base + off;
    }
    return NULL;
}

/* 名前を組み立てて fp32 テンソルを引く。**見つからなければ NULL** */
static const float *tf(const saan_weights *w, const char *fmt, ...) {
    char buf[NAME_LEN];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    uint32_t dt;
    const void *p = saan_tensor(w, buf, &dt, NULL, NULL);
    return (p && dt == 0) ? (const float *)p : NULL;
}

/* --- arena --------------------------------------------------------------- */

void saan_arena_init(saan_arena *a, void *buf, size_t size) {
    a->buf = (uint8_t *)buf; a->size = size; a->used = 0;
}
void saan_arena_reset(saan_arena *a) { a->used = 0; }

static void *alloc(saan_arena *a, size_t n) {
    size_t need = ALIGN16(n);
    if (a->used + need > a->size) return NULL;
    void *p = a->buf + a->used;
    a->used += need;
    return p;
}

size_t saan_arena_needed(int32_t n_ids) {
    /* 最悪ケース: 全トークンが上限 80 フレームまで伸びる */
    size_t T = (size_t)n_ids * SAAN_CLIP_HI;
    size_t S = T * SAAN_HOP;
    size_t s = 0;
    s += ALIGN16(sizeof(float) * (size_t)n_ids);            /* log_d */
    s += ALIGN16(sizeof(int32_t) * (size_t)n_ids);          /* d_hat */
    s += 2 * ALIGN16(sizeof(float) * SAAN_DUR_W * (size_t)n_ids);
    s += 2 * ALIGN16(sizeof(float) * SAAN_AC_W * (size_t)n_ids);
    s += 2 * ALIGN16(sizeof(float) * SAAN_AC_W * T);
    s += ALIGN16(sizeof(float) * SAAN_CDIM * T);            /* c */
    s += 2 * ALIGN16(sizeof(float) * SAAN_DEC_W * T);
    s += ALIGN16(sizeof(float) * SAAN_DEC_E * T);
    s += 3 * ALIGN16(sizeof(float) * SAAN_NBINS * T);       /* mag/cos/sin */
    s += ALIGN16(sizeof(float) * S);                        /* pcm */
    s += ALIGN16(sizeof(float) * SAAN_NFFT * 4);            /* iSTFT の作業 */
    return s + 4096;
}

/* --- 基本カーネル -------------------------------------------------------- */

/* y[o,t] = b[o] + Σ_i Σ_k W[o,i,k] · x[i, t + k - pad]  （ゼロパディング） */
static void conv1d(float *y, const float *x, const float *W, const float *b,
                   int cin, int cout, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        const float bias = b ? b[o] : 0.0f;
        for (int t = 0; t < T; ++t) yo[t] = bias;
        for (int i = 0; i < cin; ++i) {
            const float *xi = x + (size_t)i * T;
            const float *wk = W + ((size_t)o * cin + i) * ksz;
            for (int k = 0; k < ksz; ++k) {
                const float wv = wk[k];
                if (wv == 0.0f) continue;
                const int sh = k - pad;
                const int t0 = sh < 0 ? -sh : 0;
                const int t1 = sh > 0 ? T - sh : T;
                for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
            }
        }
    }
}

/* depthwise: 出力チャネル o は入力チャネル o だけを見る */
static void dwconv1d(float *y, const float *x, const float *W,
                     int ch, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const float *xi = x + (size_t)o * T;
        const float *wk = W + (size_t)o * ksz;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        for (int k = 0; k < ksz; ++k) {
            const float wv = wk[k];
            const int sh = k - pad;
            const int t0 = sh < 0 ? -sh : 0;
            const int t1 = sh > 0 ? T - sh : T;
            for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
        }
    }
}

/* PyTorch の LayerNorm は **チャネル方向**に正規化する（[B,T,C] の C）。
 * ここは [C,T] レイアウトなので、時刻ごとに C 本を見る。**軸を間違えると
 * 数値は出るが別物になる**（参照実装は h.transpose(1,2) して LayerNorm） */
static void layernorm_c(float *x, const float *g, const float *b, int C, int T) {
    const float eps = 1e-5f;
    for (int t = 0; t < T; ++t) {
        float mean = 0.0f;
        for (int c = 0; c < C; ++c) mean += x[(size_t)c * T + t];
        mean /= (float)C;
        float var = 0.0f;
        for (int c = 0; c < C; ++c) {
            const float d = x[(size_t)c * T + t] - mean;
            var += d * d;
        }
        var /= (float)C;
        const float inv = 1.0f / sqrtf(var + eps);
        for (int c = 0; c < C; ++c) {
            float *p = &x[(size_t)c * T + t];
            *p = (*p - mean) * inv * g[c] + b[c];
        }
    }
}

static void relu_(float *x, size_t n) {
    for (size_t i = 0; i < n; ++i) if (x[i] < 0.0f) x[i] = 0.0f;
}

/* PyTorch の既定は tanh 近似ではなく erf 版 */
static void gelu_(float *x, size_t n) {
    for (size_t i = 0; i < n; ++i)
        x[i] = 0.5f * x[i] * (1.0f + erff(x[i] * 0.70710678f));
}

/* --- Duration Dα --------------------------------------------------------- */

static saan_status run_duration(const saan_weights *w, saan_arena *a,
                                const int32_t *ids, int T, float *log_d) {
    const int W = SAAN_DUR_W;
    const float *emb = tf(w, "duration.emb.weight");
    const float *pw = tf(w, "duration.proj.weight");
    const float *pb = tf(w, "duration.proj.bias");
    if (!emb || !pw || !pb) return SAAN_ERR_MISSING;

    float *h = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    float *t1 = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    if (!h || !t1) return SAAN_ERR_ARENA;

    /* 埋め込みは [V, W] 行優先。ここは [W, T] に置き換える */
    for (int t = 0; t < T; ++t) {
        if (ids[t] < 0 || ids[t] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
        for (int c = 0; c < W; ++c) h[(size_t)c * T + t] = emb[(size_t)ids[t] * W + c];
    }

    for (int bi = 0; bi < 3; ++bi) {
        const float *c1w = tf(w, "duration.blocks.%d.c1.weight", bi);
        const float *c1b = tf(w, "duration.blocks.%d.c1.bias", bi);
        const float *c2w = tf(w, "duration.blocks.%d.c2.weight", bi);
        const float *c2b = tf(w, "duration.blocks.%d.c2.bias", bi);
        const float *ng = tf(w, "duration.blocks.%d.norm.weight", bi);
        const float *nb = tf(w, "duration.blocks.%d.norm.bias", bi);
        const float *gm = tf(w, "duration.blocks.%d.gamma", bi);
        if (!c1w || !c2w || !ng || !gm) return SAAN_ERR_MISSING;

        conv1d(t1, h, c1w, c1b, W, W, 5, T);
        relu_(t1, (size_t)W * T);
        float *t2 = (float *)alloc(a, sizeof(float) * (size_t)W * T);
        if (!t2) return SAAN_ERR_ARENA;
        conv1d(t2, t1, c2w, c2b, W, W, 5, T);
        layernorm_c(t2, ng, nb, W, T);
        /* LayerScale 付き残差: x + γ·f(x) */
        for (size_t i = 0; i < (size_t)W * T; ++i) h[i] += gm[0] * t2[i];
        a->used -= ALIGN16(sizeof(float) * (size_t)W * T);   /* t2 を返す */
    }

    for (int t = 0; t < T; ++t) {
        float s = pb[0];
        for (int c = 0; c < W; ++c) s += pw[c] * h[(size_t)c * T + t];
        log_d[t] = s;
    }
    return SAAN_OK;
}

/* --- Acoustic Aβ --------------------------------------------------------- */

static saan_status ac_block(const saan_weights *w, saan_arena *a, float *h,
                            const char *kind, int bi, int T) {
    const int W = SAAN_AC_W;
    const float *c1w = tf(w, "acoustic.%s.%d.c1.weight", kind, bi);
    const float *c1b = tf(w, "acoustic.%s.%d.c1.bias", kind, bi);
    const float *c2w = tf(w, "acoustic.%s.%d.c2.weight", kind, bi);
    const float *c2b = tf(w, "acoustic.%s.%d.c2.bias", kind, bi);
    const float *ng = tf(w, "acoustic.%s.%d.norm.weight", kind, bi);
    const float *nb = tf(w, "acoustic.%s.%d.norm.bias", kind, bi);
    if (!c1w || !c2w || !ng) return SAAN_ERR_MISSING;

    const size_t sz = sizeof(float) * (size_t)W * T;
    float *t1 = (float *)alloc(a, sz);
    float *t2 = (float *)alloc(a, sz);
    if (!t1 || !t2) return SAAN_ERR_ARENA;
    conv1d(t1, h, c1w, c1b, W, W, 5, T);
    relu_(t1, (size_t)W * T);
    conv1d(t2, t1, c2w, c2b, W, W, 5, T);
    layernorm_c(t2, ng, nb, W, T);
    /* acoustic 側は LayerScale 無しの素の残差（参照実装 AcBlock と同じ） */
    for (size_t i = 0; i < (size_t)W * T; ++i) h[i] += t2[i];
    a->used -= ALIGN16(sz) * 2;
    return SAAN_OK;
}

static saan_status run_acoustic(const saan_weights *w, saan_arena *a,
                                const int32_t *ids, int L, const int32_t *d,
                                int T, float *c_out) {
    const int W = SAAN_AC_W;
    const float *emb = tf(w, "acoustic.emb.weight");
    const float *pos = tf(w, "acoustic.pos.weight");
    const float *ow = tf(w, "acoustic.out.weight");     /* bias 無し */
    if (!emb || !pos || !ow) return SAAN_ERR_MISSING;

    float *ht = (float *)alloc(a, sizeof(float) * (size_t)W * L);
    if (!ht) return SAAN_ERR_ARENA;
    for (int t = 0; t < L; ++t) {
        if (ids[t] < 0 || ids[t] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
        for (int ch = 0; ch < W; ++ch)
            ht[(size_t)ch * L + t] = emb[(size_t)ids[t] * W + ch];
    }
    for (int bi = 0; bi < 3; ++bi) {
        saan_status s = ac_block(w, a, ht, "token", bi, L);
        if (s != SAAN_OK) return s;
    }

    /* length regulator: 各トークンを d[i] フレームに複製し、音素内位置を足す */
    float *hf = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    if (!hf) return SAAN_ERR_ARENA;
    int f = 0;
    for (int i = 0; i < L; ++i) {
        for (int k = 0; k < d[i]; ++k, ++f) {
            const int pi = k < SAAN_POS_MAX ? k : SAAN_POS_MAX - 1;  /* clamp */
            for (int ch = 0; ch < W; ++ch)
                hf[(size_t)ch * T + f] = ht[(size_t)ch * L + i]
                                       + pos[(size_t)pi * W + ch];
        }
    }
    if (f != T) return SAAN_ERR_SHAPE;

    for (int bi = 0; bi < 5; ++bi) {
        saan_status s = ac_block(w, a, hf, "frame", bi, T);
        if (s != SAAN_OK) return s;
    }
    /* out: 1x1 conv, bias 無し */
    conv1d(c_out, hf, ow, NULL, W, SAAN_CDIM, 1, T);
    return SAAN_OK;
}

/* --- Decoder Gγ + iSTFT -------------------------------------------------- */

static saan_status run_decoder(const saan_weights *w, saan_arena *a,
                               const float *c, int T,
                               float *mag, float *cosv, float *sinv) {
    const int W = SAAN_DEC_W, E = SAAN_DEC_E, R = SAAN_DEC_R;
    const float *iw = tf(w, "decoder.inp.weight");
    const float *ib = tf(w, "decoder.inp.bias");
    const float *hdw = tf(w, "decoder.hdown.weight");
    const float *hdb = tf(w, "decoder.hdown.bias");
    const float *how = tf(w, "decoder.hout.weight");
    const float *hob = tf(w, "decoder.hout.bias");
    if (!iw || !hdw || !how) return SAAN_ERR_MISSING;

    float *h = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    float *tw = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    float *te = (float *)alloc(a, sizeof(float) * (size_t)E * T);
    float *tr = (float *)alloc(a, sizeof(float) * (size_t)R * T);
    float *tg = (float *)alloc(a, sizeof(float) * (size_t)W * T);
    if (!h || !tw || !te || !tr || !tg) return SAAN_ERR_ARENA;

    conv1d(h, c, iw, ib, SAAN_CDIM, W, 3, T);

    for (int i = 0; i < 5; ++i) {
        const float *dw = tf(w, "decoder.dw.%d.weight", i);        /* bias 無し */
        const float *p1w = tf(w, "decoder.pw1.%d.weight", i);
        const float *p1b = tf(w, "decoder.pw1.%d.bias", i);
        const float *p2w = tf(w, "decoder.pw2.%d.weight", i);
        const float *p2b = tf(w, "decoder.pw2.%d.bias", i);
        const float *cdw = tf(w, "decoder.cdown.%d.weight", i);
        const float *cdb = tf(w, "decoder.cdown.%d.bias", i);
        const float *cuw = tf(w, "decoder.cup.%d.weight", i);
        const float *cub = tf(w, "decoder.cup.%d.bias", i);
        const float *gm = tf(w, "decoder.gamma.%d", i);
        if (!dw || !p1w || !p2w || !cdw || !cuw || !gm) return SAAN_ERR_MISSING;

        /* rank-12 の条件付け: g = cup(cdown(c))。**c は毎段の元の入力**を使う
         * （h ではない。参照実装 Decoder.forward と同じ） */
        conv1d(tr, c, cdw, cdb, SAAN_CDIM, R, 1, T);
        conv1d(tg, tr, cuw, cub, R, W, 1, T);

        dwconv1d(tw, h, dw, W, 7, T);
        for (size_t k = 0; k < (size_t)W * T; ++k) tw[k] += tg[k];
        conv1d(te, tw, p1w, p1b, W, E, 1, T);
        gelu_(te, (size_t)E * T);
        conv1d(tw, te, p2w, p2b, E, W, 1, T);
        for (size_t k = 0; k < (size_t)W * T; ++k) h[k] += gm[0] * tw[k];
    }

    float *hr = (float *)alloc(a, sizeof(float) * (size_t)SAAN_DEC_HEAD * T);
    float *o = (float *)alloc(a, sizeof(float) * (size_t)1539 * T);
    if (!hr || !o) return SAAN_ERR_ARENA;
    conv1d(hr, h, hdw, hdb, W, SAAN_DEC_HEAD, 1, T);
    gelu_(hr, (size_t)SAAN_DEC_HEAD * T);
    conv1d(o, hr, how, hob, SAAN_DEC_HEAD, 1539, 1, T);

    memcpy(mag,  o,                                sizeof(float) * (size_t)513 * T);
    memcpy(cosv, o + (size_t)513 * T,              sizeof(float) * (size_t)513 * T);
    memcpy(sinv, o + (size_t)1026 * T,             sizeof(float) * (size_t)513 * T);
    return SAAN_OK;
}

/* naive DFT の逆変換。**ESP32 では FFT に差し替える**（ここは正しさ優先）。
 * 実部だけ要るので Σ_k [Re·cos(2πkn/N) − Im·sin(2πkn/N)] を直に計算する。 */
static void irfft_1024(const float *re, const float *im, float *out) {
    const int N = SAAN_NFFT;
    for (int n = 0; n < N; ++n) {
        double acc = (double)re[0];                       /* k=0 は実数 */
        for (int k = 1; k < N / 2; ++k) {
            const double ang = 2.0 * M_PI * (double)k * (double)n / (double)N;
            acc += 2.0 * ((double)re[k] * cos(ang) - (double)im[k] * sin(ang));
        }
        /* k=N/2 (Nyquist) も実数。係数は 1（2 倍しない） */
        acc += (double)re[N / 2] * cos(M_PI * (double)n);
        out[n] = (float)(acc / (double)N);
    }
}

/* torch.istft(center=True, length=T*256) と同じ結果を出す。
 * **length を渡した場合の切り出し**（n_fft/2 から T*hop サンプル）を再現する。 */
static saan_status istft(saan_arena *a, const float *mag, const float *cosv,
                         const float *sinv, int T, float *pcm) {
    const int N = SAAN_NFFT, H = SAAN_HOP;
    const size_t full = (size_t)N + (size_t)H * (T - 1);
    float *acc = (float *)alloc(a, sizeof(float) * full);
    float *wsq = (float *)alloc(a, sizeof(float) * full);
    float *frame = (float *)alloc(a, sizeof(float) * N);
    float *re = (float *)alloc(a, sizeof(float) * SAAN_NBINS);
    float *im = (float *)alloc(a, sizeof(float) * SAAN_NBINS);
    float *win = (float *)alloc(a, sizeof(float) * N);
    if (!acc || !wsq || !frame || !re || !im || !win) return SAAN_ERR_ARENA;

    for (int i = 0; i < N; ++i)                          /* periodic Hann */
        win[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)N);
    memset(acc, 0, sizeof(float) * full);
    memset(wsq, 0, sizeof(float) * full);

    for (int t = 0; t < T; ++t) {
        for (int k = 0; k < SAAN_NBINS; ++k) {
            const float m = mag[(size_t)k * T + t];
            re[k] = m * cosv[(size_t)k * T + t];
            im[k] = m * sinv[(size_t)k * T + t];
        }
        irfft_1024(re, im, frame);
        const size_t base = (size_t)H * t;
        for (int i = 0; i < N; ++i) {
            acc[base + i] += frame[i] * win[i];
            wsq[base + i] += win[i] * win[i];
        }
    }
    /* window squared で割る（torch.istft と同じ正規化） */
    const size_t start = (size_t)N / 2;
    for (int i = 0; i < T * H; ++i) {
        const size_t j = start + (size_t)i;
        pcm[i] = (j < full && wsq[j] > 1e-11f) ? acc[j] / wsq[j] : 0.0f;
    }
    return SAAN_OK;
}

/* --- 公開 API ------------------------------------------------------------ */

saan_status saan_synthesize(const saan_weights *w, saan_arena *a,
                            const int32_t *ids, int32_t n_ids,
                            float s_v, saan_output *out) {
    if (n_ids <= 0) return SAAN_ERR_SHAPE;
    memset(out, 0, sizeof *out);
    out->n_ids = n_ids;

    out->log_d = (float *)alloc(a, sizeof(float) * (size_t)n_ids);
    out->d_hat = (int32_t *)alloc(a, sizeof(int32_t) * (size_t)n_ids);
    if (!out->log_d || !out->d_hat) return SAAN_ERR_ARENA;

    const size_t mark = a->used;
    saan_status s = run_duration(w, a, ids, n_ids, out->log_d);
    if (s != SAAN_OK) return s;
    a->used = mark;                        /* duration の作業領域を返す */

    int32_t T = 0;
    for (int i = 0; i < n_ids; ++i) {
        /* d̂ = clip[1,80](round(s_v · exp(log_d)))。**round は half-away-from-zero**
         * （C の roundf）。PyTorch の torch.round は half-to-even なので、
         * ちょうど .5 のときだけ結果が割れる。golden test で検出する */
        float v = roundf(s_v * expf(out->log_d[i]));
        if (v < SAAN_CLIP_LO) v = SAAN_CLIP_LO;
        if (v > SAAN_CLIP_HI) v = SAAN_CLIP_HI;
        out->d_hat[i] = (int32_t)v;
        T += out->d_hat[i];
    }
    out->n_frames = T;
    out->n_samples = T * SAAN_HOP;

    out->c = (float *)alloc(a, sizeof(float) * SAAN_CDIM * (size_t)T);
    out->pcm = (float *)alloc(a, sizeof(float) * (size_t)out->n_samples);
    if (!out->c || !out->pcm) return SAAN_ERR_ARENA;

    const size_t mark2 = a->used;
    s = run_acoustic(w, a, ids, n_ids, out->d_hat, T, out->c);
    if (s != SAAN_OK) return s;
    a->used = mark2;

    float *mag = (float *)alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
    float *cv = (float *)alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
    float *sv = (float *)alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
    if (!mag || !cv || !sv) return SAAN_ERR_ARENA;
    const size_t mark3 = a->used;
    s = run_decoder(w, a, out->c, T, mag, cv, sv);
    if (s != SAAN_OK) return s;
    a->used = mark3;

    return istft(a, mag, cv, sv, T, out->pcm);
}

const char *saan_strerror(saan_status s) {
    switch (s) {
    case SAAN_OK: return "ok";
    case SAAN_ERR_MAGIC: return "SAAN ヘッダでない";
    case SAAN_ERR_VERSION: return "バージョンが違う";
    case SAAN_ERR_MISSING: return "必要なテンソルが無い";
    case SAAN_ERR_SHAPE: return "shape が想定と違う";
    case SAAN_ERR_ARENA: return "arena が足りない";
    case SAAN_ERR_RANGE: return "音素ID が語彙外";
    }
    return "不明";
}
