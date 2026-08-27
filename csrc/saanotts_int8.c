/* saanoTTS-jp int8 カーネル。詳細は saanotts_int8.h */
#include "saanotts_int8.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define I8_NAME_LEN 64

/* W8A8 の ksz>1 用に、重みを [k][cin] へ並べ替えるスタック領域（バイト）。
 * この生徒の ksz>1 層は最大 48·5 = 240 B なので 512 で足りる。
 * 超える形状は並べ替えずに元のレイアウトで回す（結果は同じ、遅いだけ）。 */
#define SAAN_I8_WT_SCRATCH 512

/* --- 量子化 -------------------------------------------------------------- */

void saan_quantize_w_i8(int8_t *q, float *scale, const float *W,
                        int cout, int inner) {
    for (int o = 0; o < cout; ++o) {
        const float *wo = W + (size_t)o * inner;
        float amax = 0.0f;
        for (int i = 0; i < inner; ++i) {
            const float v = fabsf(wo[i]);
            if (v > amax) amax = v;
        }
        /* 全ゼロ行は scale = 1（0 割りを避ける。Python 側と同じ規則） */
        /* ⚠️ 丸めは **rintf = half-to-even**。torch.round と同じ。roundf
         * (half-away-from-zero) にすると実測で 544,292 値のうち 5 個が
         * exporter と食い違う（int8_test の 2c が検出する） */
        const float s = (amax == 0.0f) ? 1.0f : amax / 127.0f;
        scale[o] = s;
        int8_t *qo = q + (size_t)o * inner;
        for (int i = 0; i < inner; ++i) {
            float v = rintf(wo[i] / s);
            if (v > 127.0f) v = 127.0f;
            if (v < -127.0f) v = -127.0f;
            qo[i] = (int8_t)v;
        }
    }
}

void saan_quantize_act_i8(int8_t *q, float *sx, const float *x, int C, int T) {
    for (int t = 0; t < T; ++t) {
        float amax = 0.0f;
        for (int c = 0; c < C; ++c) {
            const float v = fabsf(x[(size_t)c * T + t]);
            if (v > amax) amax = v;
        }
        int8_t *qt = q + (size_t)t * C;
        if (amax == 0.0f) {
            sx[t] = 0.0f;
            memset(qt, 0, (size_t)C);
            continue;
        }
        const float s = amax / 127.0f;
        sx[t] = s;
        for (int c = 0; c < C; ++c) {
            float v = rintf(x[(size_t)c * T + t] / s);
            if (v > 127.0f) v = 127.0f;
            if (v < -127.0f) v = -127.0f;
            qt[c] = (int8_t)v;
        }
    }
}

size_t saan_act_scratch_bytes(int C, int T) {
    return (size_t)C * (size_t)T * sizeof(int8_t) + (size_t)T * sizeof(float);
}

/* --- W8A32 --------------------------------------------------------------- */

void saan_conv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                    const float *b, int cin, int cout, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        for (int i = 0; i < cin; ++i) {
            const float *xi = x + (size_t)i * T;
            const int8_t *wk = W + ((size_t)o * cin + i) * ksz;
            for (int k = 0; k < ksz; ++k) {
                const int qv = wk[k];
                if (qv == 0) continue;           /* fp32 版と同じくゼロ枝刈り */
                const float wv = (float)qv;
                const int sh = k - pad;
                const int t0 = sh < 0 ? -sh : 0;
                const int t1 = sh > 0 ? T - sh : T;
                for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
            }
        }
        const float s = scale[o];
        const float bias = b ? b[o] : 0.0f;
        for (int t = 0; t < T; ++t) yo[t] = yo[t] * s + bias;
    }
}

void saan_dwconv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                      int ch, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const float *xi = x + (size_t)o * T;
        const int8_t *wk = W + (size_t)o * ksz;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        for (int k = 0; k < ksz; ++k) {
            const float wv = (float)wk[k];
            const int sh = k - pad;
            const int t0 = sh < 0 ? -sh : 0;
            const int t1 = sh > 0 ? T - sh : T;
            for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
        }
        const float s = scale[o];
        for (int t = 0; t < T; ++t) yo[t] *= s;
    }
}

/* --- W8A8 ---------------------------------------------------------------- */

void saan_conv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                     const float *b, int cin, int cout, int ksz, int T,
                     int8_t *qx, float *sx) {
    const int pad = ksz / 2;
    /* ⚠️ ksz > 1 のとき W[(o·cin + i)·ksz + k] は i 方向に **stride ksz** で
     * 飛ぶ。内積の内側ループがベクトル化できず、実測で ksz=1 の 5 倍
     * (31 ps/MAC → 304 ps/MAC) 遅くなった。出力チャネルごとに [k][cin] へ
     * 並べ替えてから回す。**i についての加算順序は変えていないので結果は同一**。 */
    int8_t wt[SAAN_I8_WT_SCRATCH];
    const int transposable = (ksz > 1 && cin * ksz <= SAAN_I8_WT_SCRATCH);

    saan_quantize_act_i8(qx, sx, x, cin, T);
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        const float s = scale[o];
        const float bias = b ? b[o] : 0.0f;
        const int8_t *wo = W + (size_t)o * cin * ksz;
        if (transposable)
            for (int k = 0; k < ksz; ++k)
                for (int i = 0; i < cin; ++i)
                    wt[(size_t)k * cin + i] = wo[(size_t)i * ksz + k];
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;   /* 両端ゼロパディング */
                const int8_t *qu = qx + (size_t)u * cin;
                int32_t a32 = 0;
                if (transposable) {
                    const int8_t *wk = wt + (size_t)k * cin;
                    for (int i = 0; i < cin; ++i)
                        a32 += (int32_t)wk[i] * (int32_t)qu[i];
                } else {
                    for (int i = 0; i < cin; ++i)
                        a32 += (int32_t)wo[(size_t)i * ksz + k] * (int32_t)qu[i];
                }
                acc += (float)a32 * sx[u];
            }
            yo[t] = acc * s + bias;
        }
    }
}

void saan_dwconv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                       int ch, int ksz, int T, int8_t *qx, float *sx) {
    const int pad = ksz / 2;
    saan_quantize_act_i8(qx, sx, x, ch, T);
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const int8_t *wk = W + (size_t)o * ksz;
        const float s = scale[o];
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;
                const int32_t p = (int32_t)wk[k] * (int32_t)qx[(size_t)u * ch + o];
                acc += (float)p * sx[u];
            }
            yo[t] = acc * s;
        }
    }
}

/* --- ブロブ -------------------------------------------------------------- */

const int8_t *saan_ti8(const saan_weights *w, const float **scale,
                       const char *fmt, ...) {
    char buf[I8_NAME_LEN];
    char sbuf[I8_NAME_LEN + 8];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);

    uint32_t dt = 0;
    const void *p = saan_tensor(w, buf, &dt, NULL, NULL);
    if (!p || dt != 1u) return NULL;

    snprintf(sbuf, sizeof sbuf, "%s.scale", buf);
    if (scale) {
        uint32_t sdt = 0;
        const void *sp = saan_tensor(w, sbuf, &sdt, NULL, NULL);
        if (!sp || sdt != 2u) return NULL;
        *scale = (const float *)sp;
    }
    return (const int8_t *)p;
}
