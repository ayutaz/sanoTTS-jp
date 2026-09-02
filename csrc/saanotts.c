/* sanoTTS-jp 推論コア（C99 / 依存は libm のみ） */
#include "saanotts.h"

#include "saanotts_internal.h"
#include "saan_prof.h"

#include "fft.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/* ⚠️ **移植性。** `M_PI` は C99 の <math.h> に無い（POSIX の拡張）。
 * macOS では既定で見えるが、**Linux + glibc の厳密 `-std=c99` では見えない**。
 * `bench.c` と同じ形で自前に持つ（C-033 / CI が Linux で見つけた）。 */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

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
        /* 走査したヘッダのエントリ数（× HDR_ENT B が flash から読む量）。プロファイラ用 */
        SAAN_PROF_ADD(SAAN_PROF_LOOKUP, i + 1);
        const uint8_t *p = e + NAME_LEN;
        if (dtype) *dtype = rd_u32(p);
        if (dims) for (int k = 0; k < 4; ++k) dims[k] = rd_u32(p + 8 + 4 * k);
        uint64_t off = rd_u64(p + 24), nb = rd_u64(p + 32);
        if (off + nb > w->size) return NULL;
        if (nbytes) *nbytes = nb;
        return w->base + off;
    }
    SAAN_PROF_ADD(SAAN_PROF_LOOKUP, w->n_tensors);
    return NULL;
}

/* 名前を組み立てて fp32 テンソルを引く。**見つからなければ NULL** */
const float *saan_tf(const saan_weights *w, const char *fmt, ...) {
    char buf[NAME_LEN];
    va_list ap;
    SAAN_PROF_BEGIN(SAAN_PROF_LOOKUP);
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    {
        uint32_t dt;
        const void *p = saan_tensor(w, buf, &dt, NULL, NULL);
        const float *r = (p && dt == 0) ? (const float *)p : NULL;
        SAAN_PROF_END(SAAN_PROF_LOOKUP);
        return r;
    }
}

/* --- arena --------------------------------------------------------------- */

void saan_arena_init(saan_arena *a, void *buf, size_t size) {
    a->buf = (uint8_t *)buf; a->size = size; a->used = 0; a->peak = 0;
    a->failed = 0;
}
/* ⚠️ `peak` は**戻さない**（発話をまたいだ高水位を測るため）。0 に戻すのは init。
 * `failed` は**戻す** — 次の発話は小さいかもしれない */
void saan_arena_reset(saan_arena *a) { a->used = 0; a->failed = 0; }

void *saan_alloc(saan_arena *a, size_t n) {
    size_t need = ALIGN16(n);
    /* ⚠️ **一度失敗したら以降すべて NULL を返す（粘着）。**
     * これが無いと「大きい確保だけ失敗して後続の小さい確保は成功する」ため、
     * 最後の 1 個しか NULL 検査していない呼び出し側で
     * **init が成功を返したまま NULL を抱える**（arena 175〜191 KB の 15 サイズで実測）。 */
    if (a->failed) return NULL;
    if (a->used + need > a->size) { a->failed = 1; return NULL; }
    void *p = a->buf + a->used;
    a->used += need;
    if (a->used > a->peak) a->peak = a->used;
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
    /* W8A8（`-DSAAN_INT8_ACT=1`）のとき conv 1 本ぶんの activation 作業領域。
     * 使い終わりに arena へ返すので**同時に 1 本ぶんだけ**。最大は
     * `decoder.pw2`（cin=304）。W8A32（既定）では 0 が返る */
    s += saan_act_scratch_needed(304, (int)T);
    return s + 4096;
}

/* --- 基本カーネル -------------------------------------------------------- */

/* y[o,t] = b[o] + Σ_i Σ_k W[o,i,k] · x[i, t + k - pad]  （ゼロパディング） */
void saan_conv1d(float *y, const float *x, const float *W, const float *b,
                   int cin, int cout, int ksz, int T) {
    const int pad = ksz / 2;
    SAAN_PROF_BEGIN(SAAN_PROF_CONV32);
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
    SAAN_PROF_END(SAAN_PROF_CONV32);
    SAAN_PROF_ADD(SAAN_PROF_CONV32, (size_t)cout * cin * ksz * T);
}

/* depthwise: 出力チャネル o は入力チャネル o だけを見る */
void saan_dwconv1d(float *y, const float *x, const float *W,
                     int ch, int ksz, int T) {
    const int pad = ksz / 2;
    SAAN_PROF_BEGIN(SAAN_PROF_CONV32);
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
    SAAN_PROF_END(SAAN_PROF_CONV32);
    SAAN_PROF_ADD(SAAN_PROF_CONV32, (size_t)ch * ksz * T);
}

/* PyTorch の LayerNorm は **チャネル方向**に正規化する（[B,T,C] の C）。
 * ここは [C,T] レイアウトなので、時刻ごとに C 本を見る。**軸を間違えると
 * 数値は出るが別物になる**（参照実装は h.transpose(1,2) して LayerNorm） */
void saan_layernorm_c(float *x, const float *g, const float *b, int C, int T) {
    const float eps = 1e-5f;
    SAAN_PROF_BEGIN(SAAN_PROF_LN);
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
    SAAN_PROF_END(SAAN_PROF_LN);
    SAAN_PROF_ADD(SAAN_PROF_LN, (size_t)C * T);
}

void saan_relu(float *x, size_t n) {
    SAAN_PROF_BEGIN(SAAN_PROF_RELU);
    for (size_t i = 0; i < n; ++i) if (x[i] < 0.0f) x[i] = 0.0f;
    SAAN_PROF_END(SAAN_PROF_RELU);
    SAAN_PROF_ADD(SAAN_PROF_RELU, n);
}

/* --- erf の近似（S3）-------------------------------------------------------
 *
 * なぜ: GELU は要素ごとに `erff()` を呼び、1 step に 21,664 回（M-80）。newlib の erff は
 * 多項式 + `expf` の関数呼び出しで、QEMU の命令数比で 1 step の 14〜25% を使っていた。
 *
 * 方式: x ∈ [0, 4] を h = 1/32 の 128 区間に割り、節点の erf と erf' を表に持ち（erf_table.h）、
 * 区間内は 3 次 Hermite。奇関数なので |x| で計算して符号を戻す。|x| ≥ 4 は ±1
 * （erf(4) = 1 − 1.5e-8 は float で 1.0）。理論誤差 h⁴/384 · max|erf⁗| ≈ 1.1e-8 で、
 * float 演算の丸めの方が大きい。**libm の erff との max|Δ| ≤ 2e-7** を `make -C csrc erf` が守る。
 *
 * ⚠️ **陽性対照のためのフック。** `-DSAAN_ERF_TEST_LINEAR=1` は区間内を線形補間にする
 *    （誤差 ~1e-4）。これが erf ゲートで**落ちること**で、しきい値が効いていると言える。
 *    本番では定義しない（saanotts_internal.h に無い = 既定 0）。 */
#include "erf_table.h"

#ifndef SAAN_ERF_TEST_LINEAR
#define SAAN_ERF_TEST_LINEAR 0
#endif

float saan_erf_approx(float x) {
    const float ax = fabsf(x);
    if (ax >= SAAN_ERF_XMAX) return x < 0.0f ? -1.0f : 1.0f;
    const float u = ax * (float)SAAN_ERF_H_INV;    /* 区間座標。整数部が区間番号 */
    int i = (int)u;                                /* 0 .. N-1（ax < XMAX なので N 未満） */
    if (i >= SAAN_ERF_N) i = SAAN_ERF_N - 1;       /* 丸めで u == N になったときの保険 */
    const float t = u - (float)i;                  /* [0, 1) */
    const float f0 = kSaanErfV[i], f1 = kSaanErfV[i + 1];
#if SAAN_ERF_TEST_LINEAR
    const float y = f0 + t * (f1 - f0);
#else
    const float h = 1.0f / (float)SAAN_ERF_H_INV;
    const float d0 = kSaanErfD[i] * h, d1 = kSaanErfD[i + 1] * h;   /* 導関数 × h */
    const float t2 = t * t, t3 = t2 * t;
    /* Hermite 基底: h00 = 2t³−3t²+1, h10 = t³−2t²+t, h01 = −2t³+3t², h11 = t³−t² */
    const float y = f0 * (2.0f * t3 - 3.0f * t2 + 1.0f)
                  + d0 * (t3 - 2.0f * t2 + t)
                  + f1 * (-2.0f * t3 + 3.0f * t2)
                  + d1 * (t3 - t2);
#endif
    return x < 0.0f ? -y : y;
}

/* PyTorch の既定は tanh 近似ではなく erf 版。erf は saan_erf_approx（S3。丸め水準で erff と一致） */
void saan_gelu(float *x, size_t n) {
    SAAN_PROF_BEGIN(SAAN_PROF_GELU);
    for (size_t i = 0; i < n; ++i)
        x[i] = 0.5f * x[i] * (1.0f + saan_erf_approx(x[i] * 0.70710678f));
    SAAN_PROF_END(SAAN_PROF_GELU);
    SAAN_PROF_ADD(SAAN_PROF_GELU, n);
}

/* --- Duration Dα --------------------------------------------------------- */

saan_status saan_run_duration(const saan_weights *w, saan_arena *a,
                                const int32_t *ids, int T, float *log_d) {
    const int W = SAAN_DUR_W;
    const float *emb = saan_tf(w, "duration.emb.weight");   /* 表引きは fp32 */
    const saan_wref pw = saan_w(w, "duration.proj.weight");
    const float *pb = saan_tf(w, "duration.proj.bias");
    if (!emb || !SAAN_W_OK(pw) || !pb) return SAAN_ERR_MISSING;

    float *h = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
    float *t1 = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
    if (!h || !t1) return SAAN_ERR_ARENA;

    /* 埋め込みは [V, W] 行優先。ここは [W, T] に置き換える */
    for (int t = 0; t < T; ++t) {
        if (ids[t] < 0 || ids[t] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
        for (int c = 0; c < W; ++c) h[(size_t)c * T + t] = emb[(size_t)ids[t] * W + c];
    }

    for (int bi = 0; bi < 3; ++bi) {
        const saan_wref c1w = saan_w(w, "duration.blocks.%d.c1.weight", bi);
        const float *c1b = saan_tf(w, "duration.blocks.%d.c1.bias", bi);
        const saan_wref c2w = saan_w(w, "duration.blocks.%d.c2.weight", bi);
        const float *c2b = saan_tf(w, "duration.blocks.%d.c2.bias", bi);
        const float *ng = saan_tf(w, "duration.blocks.%d.norm.weight", bi);
        const float *nb = saan_tf(w, "duration.blocks.%d.norm.bias", bi);
        const float *gm = saan_tf(w, "duration.blocks.%d.gamma", bi);
        if (!SAAN_W_OK(c1w) || !SAAN_W_OK(c2w) || !ng || !gm) return SAAN_ERR_MISSING;

        SAAN_TRY(saan_conv1d_w(t1, h, c1w, c1b, W, W, 5, T, a));
        saan_relu(t1, (size_t)W * T);
        float *t2 = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
        if (!t2) return SAAN_ERR_ARENA;
        SAAN_TRY(saan_conv1d_w(t2, t1, c2w, c2b, W, W, 5, T, a));
        saan_layernorm_c(t2, ng, nb, W, T);
        /* LayerScale 付き残差: x + γ·f(x) */
        for (size_t i = 0; i < (size_t)W * T; ++i) h[i] += gm[0] * t2[i];
        a->used -= ALIGN16(sizeof(float) * (size_t)W * T);   /* t2 を返す */
    }

    /* proj は nn.Conv1d(32, 1, 1)。**インライン内積で書かない** —
     * 書くと 52 個の int8 テンソルのうちこれだけが量子化経路に載らない
     * （D-3c の照合で発覚。c'-1）。積和の順序は cin 昇順で同一なので
     * fp32 では bit 一致する */
    SAAN_TRY(saan_conv1d_w(log_d, h, pw, pb, W, 1, 1, T, a));
    return SAAN_OK;
}

/* --- Acoustic Aβ --------------------------------------------------------- */

static saan_status ac_block(const saan_weights *w, saan_arena *a, float *h,
                            const char *kind, int bi, int T) {
    const int W = SAAN_AC_W;
    const saan_wref c1w = saan_w(w, "acoustic.%s.%d.c1.weight", kind, bi);
    const float *c1b = saan_tf(w, "acoustic.%s.%d.c1.bias", kind, bi);
    const saan_wref c2w = saan_w(w, "acoustic.%s.%d.c2.weight", kind, bi);
    const float *c2b = saan_tf(w, "acoustic.%s.%d.c2.bias", kind, bi);
    const float *ng = saan_tf(w, "acoustic.%s.%d.norm.weight", kind, bi);
    const float *nb = saan_tf(w, "acoustic.%s.%d.norm.bias", kind, bi);
    if (!SAAN_W_OK(c1w) || !SAAN_W_OK(c2w) || !ng) return SAAN_ERR_MISSING;

    const size_t sz = sizeof(float) * (size_t)W * T;
    float *t1 = (float *)saan_alloc(a, sz);
    float *t2 = (float *)saan_alloc(a, sz);
    if (!t1 || !t2) return SAAN_ERR_ARENA;
    SAAN_TRY(saan_conv1d_w(t1, h, c1w, c1b, W, W, 5, T, a));
    saan_relu(t1, (size_t)W * T);
    SAAN_TRY(saan_conv1d_w(t2, t1, c2w, c2b, W, W, 5, T, a));
    saan_layernorm_c(t2, ng, nb, W, T);
    /* acoustic 側は LayerScale 無しの素の残差（参照実装 AcBlock と同じ） */
    for (size_t i = 0; i < (size_t)W * T; ++i) h[i] += t2[i];
    a->used -= ALIGN16(sz) * 2;
    return SAAN_OK;
}

/* token レートの部分だけ。**ストリーミング版と共有する**（音素数ぶんなので小さい） */
saan_status saan_run_acoustic_tokens(const saan_weights *w, saan_arena *a,
                                     const int32_t *ids, int L, float *ht) {
    const int W = SAAN_AC_W;
    const float *emb = saan_tf(w, "acoustic.emb.weight");
    if (!emb) return SAAN_ERR_MISSING;
    for (int t = 0; t < L; ++t) {
        if (ids[t] < 0 || ids[t] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
        for (int ch = 0; ch < W; ++ch)
            ht[(size_t)ch * L + t] = emb[(size_t)ids[t] * W + ch];
    }
    for (int bi = 0; bi < 3; ++bi) {
        saan_status s = ac_block(w, a, ht, "token", bi, L);
        if (s != SAAN_OK) return s;
    }
    return SAAN_OK;
}

static saan_status run_acoustic(const saan_weights *w, saan_arena *a,
                                const int32_t *ids, int L, const int32_t *d,
                                int T, float *c_out) {
    const int W = SAAN_AC_W;
    const float *pos = saan_tf(w, "acoustic.pos.weight");    /* 表引きは fp32 */
    const saan_wref ow = saan_w(w, "acoustic.out.weight");   /* bias 無し */
    if (!pos || !SAAN_W_OK(ow)) return SAAN_ERR_MISSING;

    float *ht = (float *)saan_alloc(a, sizeof(float) * (size_t)W * L);
    if (!ht) return SAAN_ERR_ARENA;
    saan_status s0 = saan_run_acoustic_tokens(w, a, ids, L, ht);
    if (s0 != SAAN_OK) return s0;

    /* length regulator: 各トークンを d[i] フレームに複製し、音素内位置を足す */
    float *hf = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
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
    SAAN_TRY(saan_conv1d_w(c_out, hf, ow, NULL, W, SAAN_CDIM, 1, T, a));
    return SAAN_OK;
}

/* --- Decoder Gγ + iSTFT -------------------------------------------------- */

static saan_status run_decoder(const saan_weights *w, saan_arena *a,
                               const float *c, int T,
                               float *mag, float *cosv, float *sinv) {
    const int W = SAAN_DEC_W, E = SAAN_DEC_E, R = SAAN_DEC_R;
    const saan_wref iw = saan_w(w, "decoder.inp.weight");
    const float *ib = saan_tf(w, "decoder.inp.bias");
    const saan_wref hdw = saan_w(w, "decoder.hdown.weight");
    const float *hdb = saan_tf(w, "decoder.hdown.bias");
    const saan_wref how = saan_w(w, "decoder.hout.weight");
    const float *hob = saan_tf(w, "decoder.hout.bias");
    if (!SAAN_W_OK(iw) || !SAAN_W_OK(hdw) || !SAAN_W_OK(how)) return SAAN_ERR_MISSING;

    float *h = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
    float *tw = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
    float *te = (float *)saan_alloc(a, sizeof(float) * (size_t)E * T);
    float *tr = (float *)saan_alloc(a, sizeof(float) * (size_t)R * T);
    float *tg = (float *)saan_alloc(a, sizeof(float) * (size_t)W * T);
    if (!h || !tw || !te || !tr || !tg) return SAAN_ERR_ARENA;

    SAAN_TRY(saan_conv1d_w(h, c, iw, ib, SAAN_CDIM, W, 3, T, a));

    for (int i = 0; i < 5; ++i) {
        const saan_wref dw = saan_w(w, "decoder.dw.%d.weight", i);      /* bias 無し */
        const saan_wref p1w = saan_w(w, "decoder.pw1.%d.weight", i);
        const float *p1b = saan_tf(w, "decoder.pw1.%d.bias", i);
        const saan_wref p2w = saan_w(w, "decoder.pw2.%d.weight", i);
        const float *p2b = saan_tf(w, "decoder.pw2.%d.bias", i);
        const saan_wref cdw = saan_w(w, "decoder.cdown.%d.weight", i);
        const float *cdb = saan_tf(w, "decoder.cdown.%d.bias", i);
        const saan_wref cuw = saan_w(w, "decoder.cup.%d.weight", i);
        const float *cub = saan_tf(w, "decoder.cup.%d.bias", i);
        const float *gm = saan_tf(w, "decoder.gamma.%d", i);
        if (!SAAN_W_OK(dw) || !SAAN_W_OK(p1w) || !SAAN_W_OK(p2w)
            || !SAAN_W_OK(cdw) || !SAAN_W_OK(cuw) || !gm) return SAAN_ERR_MISSING;

        /* rank-12 の条件付け: g = cup(cdown(c))。**c は毎段の元の入力**を使う
         * （h ではない。参照実装 Decoder.forward と同じ） */
        SAAN_TRY(saan_conv1d_w(tr, c, cdw, cdb, SAAN_CDIM, R, 1, T, a));
        SAAN_TRY(saan_conv1d_w(tg, tr, cuw, cub, R, W, 1, T, a));

        SAAN_TRY(saan_dwconv1d_w(tw, h, dw, W, 7, T, a));
        for (size_t k = 0; k < (size_t)W * T; ++k) tw[k] += tg[k];
        SAAN_TRY(saan_conv1d_w(te, tw, p1w, p1b, W, E, 1, T, a));
        saan_gelu(te, (size_t)E * T);
        SAAN_TRY(saan_conv1d_w(tw, te, p2w, p2b, E, W, 1, T, a));
        for (size_t k = 0; k < (size_t)W * T; ++k) h[k] += gm[0] * tw[k];
    }

    float *hr = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_DEC_HEAD * T);
    float *o = (float *)saan_alloc(a, sizeof(float) * (size_t)1539 * T);
    if (!hr || !o) return SAAN_ERR_ARENA;
    SAAN_TRY(saan_conv1d_w(hr, h, hdw, hdb, W, SAAN_DEC_HEAD, 1, T, a));
    saan_gelu(hr, (size_t)SAAN_DEC_HEAD * T);
    SAAN_TRY(saan_conv1d_w(o, hr, how, hob, SAAN_DEC_HEAD, 1539, 1, T, a));

    memcpy(mag,  o,                                sizeof(float) * (size_t)513 * T);
    memcpy(cosv, o + (size_t)513 * T,              sizeof(float) * (size_t)513 * T);
    memcpy(sinv, o + (size_t)1026 * T,             sizeof(float) * (size_t)513 * T);
    return SAAN_OK;
}

/* 逆実 FFT。既定は radix-2（`csrc/fft.c`、naive の 1,470 倍）。
 *
 * ⚠️ **naive DFT は消さない。** FFT の検証基準として残す。
 * `-DSAAN_USE_NAIVE_DFT` でこちらに切り替わる。
 * 両者は **bit 一致しない**（積和の順序が違う）。実測 SNR 138.7 dB（D-3a）。
 *
 * 実部だけ要るので Σ_k [Re·cos(2πkn/N) − Im·sin(2πkn/N)] を直に計算する。 */
static void irfft_naive_1024(const float *re, const float *im, float *out) {
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

static void irfft_1024(const float *re, const float *im, float *out) {
#ifdef SAAN_USE_NAIVE_DFT
    irfft_naive_1024(re, im, out);
#else
    saan_irfft_1024(re, im, out);
    (void)irfft_naive_1024;
#endif
}

/* torch.istft(center=True, length=T*256) と同じ結果を出す。
 * **length を渡した場合の切り出し**（n_fft/2 から T*hop サンプル）を再現する。 */
static saan_status istft(saan_arena *a, const float *mag, const float *cosv,
                         const float *sinv, int T, float *pcm) {
    const int N = SAAN_NFFT, H = SAAN_HOP;
    const size_t full = (size_t)N + (size_t)H * (T - 1);
    float *acc = (float *)saan_alloc(a, sizeof(float) * full);
    float *wsq = (float *)saan_alloc(a, sizeof(float) * full);
    float *frame = (float *)saan_alloc(a, sizeof(float) * N);
    float *re = (float *)saan_alloc(a, sizeof(float) * SAAN_NBINS);
    float *im = (float *)saan_alloc(a, sizeof(float) * SAAN_NBINS);
    float *win = (float *)saan_alloc(a, sizeof(float) * N);
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
    return saan_synthesize_d(w, a, ids, n_ids, s_v, NULL, out);
}

saan_status saan_synthesize_d(const saan_weights *w, saan_arena *a,
                              const int32_t *ids, int32_t n_ids, float s_v,
                              const int32_t *d_fixed, saan_output *out) {
    if (n_ids <= 0) return SAAN_ERR_SHAPE;
    memset(out, 0, sizeof *out);
    out->n_ids = n_ids;

    out->log_d = (float *)saan_alloc(a, sizeof(float) * (size_t)n_ids);
    out->d_hat = (int32_t *)saan_alloc(a, sizeof(int32_t) * (size_t)n_ids);
    if (!out->log_d || !out->d_hat) return SAAN_ERR_ARENA;

    const size_t mark = a->used;
    saan_status s = saan_run_duration(w, a, ids, n_ids, out->log_d);
    if (s != SAAN_OK) return s;
    a->used = mark;                        /* duration の作業領域を返す */

    int32_t T = 0;
    for (int i = 0; i < n_ids; ++i) {
        if (d_fixed) {                       /* 測定用に外から d̂ を固定する */
            if (d_fixed[i] < SAAN_CLIP_LO || d_fixed[i] > SAAN_CLIP_HI)
                return SAAN_ERR_RANGE;
            out->d_hat[i] = d_fixed[i];
            T += out->d_hat[i];
            continue;
        }
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

    out->c = (float *)saan_alloc(a, sizeof(float) * SAAN_CDIM * (size_t)T);
    out->pcm = (float *)saan_alloc(a, sizeof(float) * (size_t)out->n_samples);
    if (!out->c || !out->pcm) return SAAN_ERR_ARENA;

    const size_t mark2 = a->used;
    s = run_acoustic(w, a, ids, n_ids, out->d_hat, T, out->c);
    if (s != SAAN_OK) return s;
    a->used = mark2;

    float *mag = (float *)saan_alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
    float *cv = (float *)saan_alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
    float *sv = (float *)saan_alloc(a, sizeof(float) * SAAN_NBINS * (size_t)T);
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

/* --- コンテキスト付きカーネル（ストリーミング用） -------------------------
 *
 * ⚠️ **積和の順序を一括版と揃える。** float の加算は非結合なので、
 * 順序が変わると bit 一致（D-029 の G2）が崩れる。そこで
 * 「x を [pad + T + pad] に展開してから既存カーネルを T+2pad 幅で呼び、
 * 中央 T フレームを取る」のではなく、**既存カーネルと同じループを、
 * 境界だけ left/right から読むように書く**。
 */

void saan_conv1d_ctx(float *y, const float *x, const float *left,
                     const float *right, const float *W, const float *b,
                     int cin, int cout, int ksz, int T, float *scratch) {
    const int pad = ksz / 2;
    const int W2 = pad + T + pad;
    /* [cin][pad + T + pad] に展開する。**一括版が見るのと同じ値の並び**にした上で
     * 同一のループを回すので、積和の順序が一致する */
    for (int i = 0; i < cin; ++i) {
        float *dst = scratch + (size_t)i * W2;
        const float *src = x + (size_t)i * T;
        for (int k = 0; k < pad; ++k)
            dst[k] = left ? left[(size_t)i * pad + k] : 0.0f;
        memcpy(dst + pad, src, sizeof(float) * (size_t)T);
        for (int k = 0; k < pad; ++k)
            dst[pad + T + k] = right ? right[(size_t)i * pad + k] : 0.0f;
    }
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        const float bias = b ? b[o] : 0.0f;
        for (int t = 0; t < T; ++t) yo[t] = bias;
        for (int i = 0; i < cin; ++i) {
            const float *xi = scratch + (size_t)i * W2;
            const float *wk = W + ((size_t)o * cin + i) * ksz;
            for (int k = 0; k < ksz; ++k) {
                const float wv = wk[k];
                if (wv == 0.0f) continue;
                for (int t = 0; t < T; ++t) yo[t] += wv * xi[t + k];
            }
        }
    }
}

void saan_dwconv1d_ctx(float *y, const float *x, const float *left,
                       const float *right, const float *W,
                       int ch, int ksz, int T, float *scratch) {
    const int pad = ksz / 2;
    const int W2 = pad + T + pad;
    for (int i = 0; i < ch; ++i) {
        float *dst = scratch + (size_t)i * W2;
        const float *src = x + (size_t)i * T;
        for (int k = 0; k < pad; ++k)
            dst[k] = left ? left[(size_t)i * pad + k] : 0.0f;
        memcpy(dst + pad, src, sizeof(float) * (size_t)T);
        for (int k = 0; k < pad; ++k)
            dst[pad + T + k] = right ? right[(size_t)i * pad + k] : 0.0f;
    }
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const float *xi = scratch + (size_t)o * W2;
        const float *wk = W + (size_t)o * ksz;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        for (int k = 0; k < ksz; ++k) {
            const float wv = wk[k];
            for (int t = 0; t < T; ++t) yo[t] += wv * xi[t + k];
        }
    }
}

/* --- 段別プロファイラの実体（saan_prof.h。SAAN_PROFILE=1 のときだけ） ------- */
#if SAAN_PROFILE
uint64_t saan_prof_acc[SAAN_PROF_N];
uint32_t saan_prof_cnt[SAAN_PROF_N];
uint64_t saan_prof_n[SAAN_PROF_N];

const char *saan_prof_name(int id) {
    /* ⚠️ saan_prof.h の enum と同じ順序。ずれると表の行名が入れ替わる */
    static const char *const names[SAAN_PROF_N] = {
        "STEP", "HF", "TOKEN", "AC", "DINP", "DEC", "HEAD", "ISTFT",
        "LOOKUP", "QUANT", "WCOPY", "MAC", "DW", "CONV32", "GELU", "LN", "RELU",
        "PIPE", "INIT",
    };
    return (id >= 0 && id < SAAN_PROF_N) ? names[id] : "?";
}

void saan_prof_reset(void) {
    memset(saan_prof_acc, 0, sizeof saan_prof_acc);
    memset(saan_prof_cnt, 0, sizeof saan_prof_cnt);
    memset(saan_prof_n, 0, sizeof saan_prof_n);
}
#endif
