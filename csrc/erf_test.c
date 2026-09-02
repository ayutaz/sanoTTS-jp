/* S3: saan_erf_approx() が libm の erff() と一致するかのゲート（`make -C csrc erf`）。
 *
 *   ./erf_test                  … max|Δ| <= SAAN_ERF_TOL で OK
 *   ./erf_test_linear --expect-fail   … 線形補間に落とした**陽性対照**。落ちなければ NG
 *
 * ⚠️ しきい値 2e-7 は「float の 1.0 の 3〜4 ulp」。Hermite の理論誤差 1.1e-8 より
 *    float 演算の丸めの方が大きいので、実測でこの水準に収まることを確かめる。
 * ⚠️ **陽性対照が要る理由**: 表を読む位置がずれても erf は滑らかなので、緩いしきい値なら
 *    通ってしまう。線形補間（誤差 ~1e-4）が落ちることで、しきい値が効いていると言える。 */
#include "saanotts_internal.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef SAAN_ERF_TOL
#define SAAN_ERF_TOL 2e-7
#endif

static uint32_t rs = 0x9e3779b9u;
static float rndf(float lo, float hi) {
    rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5;
    return lo + (hi - lo) * ((float)(rs & 0xffffffu) / 16777216.0f);
}

int main(int argc, char **argv) {
    const int expect_fail = (argc > 1 && strcmp(argv[1], "--expect-fail") == 0);
    const int N = 1000000;
    double maxd = 0.0; float at = 0.0f;
    /* 一様乱数 [-5, 5]。|x| >= 4 のクランプ側も踏む */
    for (int i = 0; i < N; ++i) {
        const float x = rndf(-5.0f, 5.0f);
        const double d = fabs((double)saan_erf_approx(x) - (double)erff(x));
        if (d > maxd) { maxd = d; at = x; }
    }
    /* 境界と節点そのもの、節点の中点、GELU が実際に渡す範囲（x/√2）の細かい格子 */
    static const float edges[] = { 0.0f, -0.0f, 1e-9f, -1e-9f, 0.03125f, 0.015625f, 0.5f, 1.0f,
                                   2.0f, 3.0f, 3.96875f, 3.99999f, 4.0f, 4.00001f, -4.0f, 6.0f, -6.0f,
                                   1e30f, -1e30f };
    for (size_t i = 0; i < sizeof edges / sizeof edges[0]; ++i) {
        const double d = fabs((double)saan_erf_approx(edges[i]) - (double)erff(edges[i]));
        if (d > maxd) { maxd = d; at = edges[i]; }
    }
    for (int i = -128000; i <= 128000; ++i) {
        const float x = (float)i / 32000.0f;   /* [-4, 4] を 1/32000 刻み */
        const double d = fabs((double)saan_erf_approx(x) - (double)erff(x));
        if (d > maxd) { maxd = d; at = x; }
    }
    const int within = maxd <= SAAN_ERF_TOL;
    printf("  saan_erf_approx vs erff: max|Δ| %.3e (x = %.7g) / しきい値 %.1e  n = %d + 256,001 格子\n",
           maxd, (double)at, (double)SAAN_ERF_TOL, N);
    if (expect_fail) {
        printf("  %s 陽性対照: 線形補間は しきい値を%s\n",
               within ? "NG!" : "OK ", within ? "通ってしまった（比較が効いていない）" : "超えて落ちる");
        return within ? 1 : 0;
    }
    printf("  %s saan_erf_approx は erff と max|Δ| <= %.1e で一致\n", within ? "OK " : "NG!",
           (double)SAAN_ERF_TOL);
    return within ? 0 : 1;
}
