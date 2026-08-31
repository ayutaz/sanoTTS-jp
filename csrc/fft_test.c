/* D-3a: radix-2 実 IFFT の検証。
 *
 *   1. naive DFT との一致 (SNR >= 120 dB) — ランダム入力 100 本
 *   2. 既知入力（DC / 単一 bin / Nyquist）の解析解との一致
 *   3. 速度: naive と FFT の ns/call、n=1000 の平均と標準偏差
 *
 * ⚠️ bit 一致は要求しない。積和の順序が変わるので必ずずれる。
 * ⚠️ 基準の naive 版は saanotts.c から**写した**もの。saanotts.c は触らない。
 *
 *   cc -std=c99 -O2 -Wall -Wextra -o fft_test fft_test.c fft.c -lm
 */
/* ⚠️ **移植性。** `clock_gettime` / `CLOCK_MONOTONIC` は POSIX で、
 * 厳密 `-std=c99` だと glibc が隠す。**どのヘッダより先に**立てる。
 * ⚠️ これを立てると macOS 側で `M_PI` が隠れるので、下でガードしている。 */
#define _POSIX_C_SOURCE 199309L
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

/* ⚠️ **移植性。** `M_PI` は C99 の <math.h> に無い（POSIX の拡張）。
 * macOS では既定で見えるが、**Linux + glibc の厳密 `-std=c99` では見えない**。
 * `bench.c` と同じ形で自前に持つ（C-033 / CI が Linux で見つけた）。 */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#include <string.h>
#include <time.h>
#include "fft.h"

#define N       SAAN_FFT_N
#define NBINS   (N / 2 + 1)

/* ---- saanotts.c:367 の naive DFT をそのまま写したもの（基準） ---------- */
static void irfft_1024_naive(const float *re, const float *im, float *out) {
    for (int n = 0; n < N; ++n) {
        double acc = (double)re[0];
        for (int k = 1; k < N / 2; ++k) {
            const double ang = 2.0 * M_PI * (double)k * (double)n / (double)N;
            acc += 2.0 * ((double)re[k] * cos(ang) - (double)im[k] * sin(ang));
        }
        acc += (double)re[N / 2] * cos(M_PI * (double)n);
        out[n] = (float)(acc / (double)N);
    }
}

/* ---- 決定的な乱数（xorshift32。環境差を出さない） --------------------- */
static unsigned g_rng = 2463534242u;
static double urand(void) {
    g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5;
    return (double)(g_rng >> 8) / (double)(1u << 24);   /* [0,1) */
}
static double srand_pm1(void) { return 2.0 * urand() - 1.0; }

static double snr_db(const float *ref, const float *tst, int n) {
    double s = 0.0, e = 0.0;
    for (int i = 0; i < n; ++i) {
        const double d = (double)ref[i] - (double)tst[i];
        s += (double)ref[i] * (double)ref[i];
        e += d * d;
    }
    if (e <= 0.0) return INFINITY;
    return 10.0 * log10(s / e);
}

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

/* 統計 */
static void mean_sd(const double *v, int n, double *m, double *sd) {
    double s = 0.0;
    for (int i = 0; i < n; ++i) s += v[i];
    *m = s / (double)n;
    double q = 0.0;
    for (int i = 0; i < n; ++i) { const double d = v[i] - *m; q += d * d; }
    *sd = (n > 1) ? sqrt(q / (double)(n - 1)) : 0.0;
}

/* ====================================================================== */

static int g_fail = 0;
static void check(int ok, const char *what) {
    printf("  [%s] %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) g_fail = 1;
}

/* --- 1. ランダム入力での naive 一致 ------------------------------------ */
static void test_random(int trials, int decaying, double *out_min,
                        double *out_mean, double *out_max, int *out_bitexact) {
    static float re[NBINS], im[NBINS], a[N], b[N];
    double mn = INFINITY, mx = -INFINITY, sum = 0.0;
    int bitexact = 0;
    for (int t = 0; t < trials; ++t) {
        for (int k = 0; k < NBINS; ++k) {
            /* decaying: 実際のスペクトルに近い 1/(1+k/32) の減衰をかける */
            const double g = decaying ? 1.0 / (1.0 + (double)k / 32.0) : 1.0;
            re[k] = (float)(srand_pm1() * g);
            im[k] = (float)(srand_pm1() * g);   /* im[0]/im[512] も埋める */
        }
        irfft_1024_naive(re, im, a);
        saan_irfft_1024(re, im, b);
        if (memcmp(a, b, sizeof a) == 0) ++bitexact;
        const double s = snr_db(a, b, N);
        if (s < mn) mn = s;
        if (s > mx) mx = s;
        sum += s;
    }
    /* bit 一致した試行は SNR が inf になるので平均から除く（min が実効値） */
    *out_min = mn;
    *out_mean = (bitexact == trials) ? INFINITY : sum / (double)trials;
    *out_max = mx;
    *out_bitexact = bitexact;
}

/* --- 2. 既知入力 -------------------------------------------------------- */
static double max_abs_err(const float *x, const double *ref) {
    double m = 0.0;
    for (int n = 0; n < N; ++n) {
        const double d = fabs((double)x[n] - ref[n]);
        if (d > m) m = d;
    }
    return m;
}

int main(void) {
    static float re[NBINS], im[NBINS], got[N];
    static double ref[N];
    int rc_json = 0;
    double rnd_min, rnd_mean, rnd_max, dec_min, dec_mean, dec_max;
    int rnd_be = 0, dec_be = 0;

    printf("== 1. naive DFT との一致 (n=100) ==\n");
    g_rng = 2463534242u;
    test_random(100, 0, &rnd_min, &rnd_mean, &rnd_max, &rnd_be);
    printf("  white   SNR dB: min %.2f  mean %.2f  max %.2f  (bit-exact %d/100)\n",
           rnd_min, rnd_mean, rnd_max, rnd_be);
    check(rnd_min >= 120.0, "white spectrum SNR >= 120 dB (min over 100)");
    g_rng = 88675123u;
    test_random(100, 1, &dec_min, &dec_mean, &dec_max, &dec_be);
    printf("  decay   SNR dB: min %.2f  mean %.2f  max %.2f  (bit-exact %d/100)\n",
           dec_min, dec_mean, dec_max, dec_be);
    check(dec_min >= 120.0, "decaying spectrum SNR >= 120 dB (min over 100)");

    printf("== 2. 既知入力 ==\n");
    /* DC: X[0] = 1、他ゼロ -> x[n] = 1/N */
    memset(re, 0, sizeof re); memset(im, 0, sizeof im);
    re[0] = 1.0f;
    for (int n = 0; n < N; ++n) ref[n] = 1.0 / (double)N;
    saan_irfft_1024(re, im, got);
    double e_dc = max_abs_err(got, ref);
    printf("  DC              max|err| = %.3e\n", e_dc);
    check(e_dc < 1e-9, "DC bin -> constant 1/N");

    /* Nyquist: X[512] = 1 -> x[n] = (-1)^n / N */
    memset(re, 0, sizeof re); memset(im, 0, sizeof im);
    re[N / 2] = 1.0f;
    for (int n = 0; n < N; ++n) ref[n] = ((n & 1) ? -1.0 : 1.0) / (double)N;
    saan_irfft_1024(re, im, got);
    double e_ny = max_abs_err(got, ref);
    printf("  Nyquist         max|err| = %.3e\n", e_ny);
    check(e_ny < 1e-9, "Nyquist bin -> (-1)^n / N");

    /* 単一 bin k=37, X = 1 + 0i -> x[n] = 2 cos(2*pi*37*n/N) / N */
    memset(re, 0, sizeof re); memset(im, 0, sizeof im);
    re[37] = 1.0f;
    for (int n = 0; n < N; ++n)
        ref[n] = 2.0 * cos(2.0 * M_PI * 37.0 * (double)n / (double)N) / (double)N;
    saan_irfft_1024(re, im, got);
    double e_c = max_abs_err(got, ref);
    printf("  bin37 real      max|err| = %.3e\n", e_c);
    check(e_c < 1e-7, "single real bin -> cosine");

    /* 単一 bin k=37, X = 0 + 1i -> x[n] = -2 sin(2*pi*37*n/N) / N */
    memset(re, 0, sizeof re); memset(im, 0, sizeof im);
    im[37] = 1.0f;
    for (int n = 0; n < N; ++n)
        ref[n] = -2.0 * sin(2.0 * M_PI * 37.0 * (double)n / (double)N) / (double)N;
    saan_irfft_1024(re, im, got);
    double e_s = max_abs_err(got, ref);
    printf("  bin37 imag      max|err| = %.3e\n", e_s);
    check(e_s < 1e-7, "single imag bin -> -sine");

    /* im[0] / im[512] を無視する契約（naive 版と同じ） */
    memset(re, 0, sizeof re); memset(im, 0, sizeof im);
    re[0] = 0.5f; re[N / 2] = -0.25f; re[11] = 0.3f; im[11] = -0.7f;
    static float base[N], poisoned[N];
    saan_irfft_1024(re, im, base);
    im[0] = 123.0f; im[N / 2] = -456.0f;
    saan_irfft_1024(re, im, poisoned);
    int same = (memcmp(base, poisoned, sizeof base) == 0);
    printf("  im[0]/im[512] を無視: %s\n", same ? "yes" : "no");
    check(same, "DC/Nyquist の虚部を読まない（naive 版と同じ契約）");

    printf("== 3. 速度 (n=1000) ==\n");
    {
        enum { REP = 1000 };
        static double t_fft[REP], t_naive[REP];
        static float sre[NBINS], sim[NBINS], sout[N];
        double sink = 0.0;
        g_rng = 1u;
        for (int k = 0; k < NBINS; ++k) {
            const double g = 1.0 / (1.0 + (double)k / 32.0);
            sre[k] = (float)(srand_pm1() * g);
            sim[k] = (float)(srand_pm1() * g);
        }
        /* warm-up */
        for (int i = 0; i < 50; ++i) { saan_irfft_1024(sre, sim, sout); sink += sout[3]; }
        for (int i = 0; i < REP; ++i) {
            const double t0 = now_s();
            saan_irfft_1024(sre, sim, sout);
            t_fft[i] = (now_s() - t0) * 1e9;
            sink += sout[i & 1023];
        }
        for (int i = 0; i < 5; ++i) { irfft_1024_naive(sre, sim, sout); sink += sout[3]; }
        for (int i = 0; i < REP; ++i) {
            const double t0 = now_s();
            irfft_1024_naive(sre, sim, sout);
            t_naive[i] = (now_s() - t0) * 1e9;
            sink += sout[i & 1023];
        }
        double mf, sf, mn2, sn2;
        mean_sd(t_fft, REP, &mf, &sf);
        mean_sd(t_naive, REP, &mn2, &sn2);
        printf("  fft   : %10.1f ns/call  (sd %8.1f, n=%d)\n", mf, sf, REP);
        printf("  naive : %10.1f ns/call  (sd %8.1f, n=%d)\n", mn2, sn2, REP);
        printf("  speedup: %.1fx\n", mn2 / mf);
        printf("  audio 1 秒あたり (86.13 frame): fft %.3f ms / naive %.1f ms\n",
               mf * 86.13 * 1e-6, mn2 * 86.13 * 1e-6);
        printf("  (sink %.6g)\n", sink);

        FILE *f = fopen("fft_bench.json", "w");
        if (f) {
            fprintf(f,
                "{\"snr_white_db\":{\"min\":%.4f,\"mean\":%.4f,\"max\":%.4f,\"n\":100,\"bit_exact\":%d},"
                "\"snr_decay_db\":{\"min\":%.4f,\"mean\":%.4f,\"max\":%.4f,\"n\":100,\"bit_exact\":%d},"
                "\"known_input_max_abs_err\":{\"dc\":%.6e,\"nyquist\":%.6e,"
                "\"bin37_real\":%.6e,\"bin37_imag\":%.6e},"
                "\"ns_per_call\":{\"fft_mean\":%.2f,\"fft_sd\":%.2f,"
                "\"naive_mean\":%.2f,\"naive_sd\":%.2f,\"n\":%d,\"speedup\":%.4f}}\n",
                rnd_min, rnd_mean, rnd_max, rnd_be,
                dec_min, dec_mean, dec_max, dec_be,
                e_dc, e_ny, e_c, e_s, mf, sf, mn2, sn2, REP, mn2 / mf);
            fclose(f);
        } else rc_json = 1;
    }

    printf("%s\n", g_fail ? "SOME CHECKS FAILED" : "ALL CHECKS PASSED");
    return g_fail || rc_json;
}
