import math, sys

N = 1024
M = N // 2

def fmt(v, dbl):
    if dbl:
        return "%.17g" % v
    s = "%.9g" % v
    if ("." not in s) and ("e" not in s) and ("E" not in s):
        s += ".0"
    return s + "f"

def table(name, vals, dbl, per=4):
    lines = []
    lines.append("static const saan_fr %s[%d] = {" % (name, len(vals)))
    for i in range(0, len(vals), per):
        chunk = ", ".join(fmt(v, dbl) for v in vals[i:i+per])
        lines.append("    " + chunk + ("," if i + per < len(vals) else ""))
    lines.append("};")
    return "\n".join(lines)

# We emit both float and double literal tables guarded by SAAN_FFT_DOUBLE.
w_re = [math.cos(2.0 * math.pi * j / M) for j in range(M // 2)]
w_im = [math.sin(2.0 * math.pi * j / M) for j in range(M // 2)]
s_re = [math.cos(2.0 * math.pi * k / N) for k in range(M)]
s_im = [math.sin(2.0 * math.pi * k / N) for k in range(M)]

out = []
out.append('''/* radix-2 実 IFFT (N = 1024)。`csrc/saanotts.c` の naive DFT (O(N^2)) の
 * 置き換え。**契約は naive 版と同一** — 入力 513 bin の実部/虚部、
 * 出力 1024 サンプル、1/N 正規化済み。
 *
 * ⚠️ このファイルは自動生成物ではあるが **リポジトリ上の正本**である。
 *    twiddle テーブルの再生成手順は fft.h のコメントを参照。
 *
 * 依存は libm のみ（実際には twiddle が定数なので実行時は libm も呼ばない）。
 * malloc / arena を使わない。作業領域は 512 complex の自動変数のみ。
 *
 * アルゴリズム: 長さ N の実 IFFT を長さ M=N/2 の複素 IFFT 1 回に落とす。
 *   A[k] = (X[k] + X[k+M]) / 2                       k = 0..M-1
 *   B[k] = (X[k] - X[k+M]) / 2 * exp(+i2*pi*k/N)
 *   Z[k] = A[k] + i*B[k]
 *   z[m] = (1/M) * sum_k Z[k] exp(+i2*pi*k*m/M)
 *   x[2m] = Re z[m],  x[2m+1] = Im z[m]
 * ここで X[k+M] = conj(X[M-k]) （エルミート性）を使う。
 *
 * ⚠️ naive 版は im[0] と im[512] を**読まない**（DC と Nyquist を実数と見なす）。
 *    こちらも同じく無視する。呼び出し側の mag*sin は DC/Nyquist で非ゼロに
 *    なりうるので、これを合わせないと出力が食い違う。
 */
#include "fft.h"

#define SAAN_FFT_M (SAAN_FFT_N / 2)

#ifdef SAAN_FFT_DOUBLE
typedef double saan_fr;
#else
typedef float saan_fr;
#endif
''')

out.append("#ifdef SAAN_FFT_DOUBLE")
out.append("/* 複素 IFFT (M = 512) の twiddle: exp(+i2*pi*j/M), j = 0..M/2-1 */")
out.append(table("SAAN_W_RE", w_re, True))
out.append(table("SAAN_W_IM", w_im, True))
out.append("/* 実/複素 分解の twiddle: exp(+i2*pi*k/N), k = 0..M-1 */")
out.append(table("SAAN_S_RE", s_re, True))
out.append(table("SAAN_S_IM", s_im, True))
out.append("#else")
out.append("/* 複素 IFFT (M = 512) の twiddle: exp(+i2*pi*j/M), j = 0..M/2-1 */")
out.append(table("SAAN_W_RE", w_re, False))
out.append(table("SAAN_W_IM", w_im, False))
out.append("/* 実/複素 分解の twiddle: exp(+i2*pi*k/N), k = 0..M-1 */")
out.append(table("SAAN_S_RE", s_re, False))
out.append(table("SAAN_S_IM", s_im, False))
out.append("#endif")

out.append('''
/* in-place DIT radix-2 複素 IFFT（正の指数）。正規化はしない。 */
static void saan_ifft_m(saan_fr *xr, saan_fr *xi) {
    /* bit-reversal permutation（テーブルを持たない: 1 KB の節約） */
    unsigned j = 0u;
    for (unsigned i = 1u; i < (unsigned)SAAN_FFT_M; ++i) {
        unsigned bit = (unsigned)SAAN_FFT_M >> 1;
        for (; (j & bit) != 0u; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            saan_fr t;
            t = xr[i]; xr[i] = xr[j]; xr[j] = t;
            t = xi[i]; xi[i] = xi[j]; xi[j] = t;
        }
    }
    for (unsigned len = 2u; len <= (unsigned)SAAN_FFT_M; len <<= 1) {
        const unsigned half = len >> 1;
        const unsigned step = (unsigned)SAAN_FFT_M / len;
        for (unsigned i = 0u; i < (unsigned)SAAN_FFT_M; i += len) {
            for (unsigned k = 0u; k < half; ++k) {
                const saan_fr wr = SAAN_W_RE[k * step];
                const saan_fr wi = SAAN_W_IM[k * step];
                const unsigned a = i + k, b = a + half;
                const saan_fr br = xr[b], bi = xi[b];
                const saan_fr vr = br * wr - bi * wi;
                const saan_fr vi = br * wi + bi * wr;
                const saan_fr ur = xr[a], ui = xi[a];
                xr[a] = ur + vr; xi[a] = ui + vi;
                xr[b] = ur - vr; xi[b] = ui - vi;
            }
        }
    }
}

void saan_irfft_1024(const float *re, const float *im, float *out) {
    saan_fr zr[SAAN_FFT_M], zi[SAAN_FFT_M];
    const unsigned M = (unsigned)SAAN_FFT_M;

    /* k = 0: X[0] と X[M] は実数として扱う（naive 版と同じく im を読まない） */
    {
        const saan_fr x0 = (saan_fr)re[0];
        const saan_fr xm = (saan_fr)re[M];
        const saan_fr ar = (saan_fr)0.5 * (x0 + xm);   /* A[0]、虚部 0 */
        const saan_fr br = (saan_fr)0.5 * (x0 - xm);   /* B[0]、twiddle = 1 */
        zr[0] = ar;                                     /* Re(A + iB) = Ar - Bi */
        zi[0] = br;                                     /* Im(A + iB) = Ai + Br */
    }
    for (unsigned k = 1u; k < M; ++k) {
        const saan_fr xr = (saan_fr)re[k],     xi = (saan_fr)im[k];
        const saan_fr yr = (saan_fr)re[M - k], yi = -(saan_fr)im[M - k];
        const saan_fr ar = (saan_fr)0.5 * (xr + yr), ai = (saan_fr)0.5 * (xi + yi);
        const saan_fr dr = (saan_fr)0.5 * (xr - yr), di = (saan_fr)0.5 * (xi - yi);
        const saan_fr cr = SAAN_S_RE[k], ci = SAAN_S_IM[k];
        const saan_fr Br = dr * cr - di * ci;
        const saan_fr Bi = dr * ci + di * cr;
        zr[k] = ar - Bi;
        zi[k] = ai + Br;
    }

    saan_ifft_m(zr, zi);

    const saan_fr inv = (saan_fr)1.0 / (saan_fr)SAAN_FFT_M;
    for (unsigned m = 0u; m < M; ++m) {
        out[2u * m]      = (float)(zr[m] * inv);
        out[2u * m + 1u] = (float)(zi[m] * inv);
    }
}
''')

with open(sys.argv[1], "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", sys.argv[1])
