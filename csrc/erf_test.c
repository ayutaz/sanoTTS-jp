/* S3: saan_erf_approx() が libm の erff() と一致するかのゲート（`make -C csrc erf`）。
 * T5-G3 で「S3 実装との全格子 bit 一致」を足した（下記 §2）。
 *
 *   ./erf_test                        … §1 max|Δ| <= SAAN_ERF_TOL と §2 全格子 bit 一致で OK
 *   ./erf_test_linear --expect-fail   … 線形補間に落とした**陽性対照**（§1 が落ちなければ NG）
 *   ./erf_test_clamp --expect-grid-fail … クランプをずらした**陽性対照**（§2 が落ちなければ NG）
 *
 * ⚠️ しきい値 2e-7 は「float の 1.0 の 3〜4 ulp」。Hermite の理論誤差 1.1e-8 より
 *    float 演算の丸めの方が大きいので、実測でこの水準に収まることを確かめる。
 * ⚠️ **陽性対照が要る理由**: 表を読む位置がずれても erf は滑らかなので、緩いしきい値なら
 *    通ってしまう。線形補間（誤差 ~1e-4）が落ちることで、しきい値が効いていると言える。
 *
 * §2（T5-G3）: GELU のコード生成を直す作業（分岐の除去・表の事前スケール・インライン化）は
 *    **式を変えない**のが約束。それを「erff と 2e-7 で一致」だけで守ると、丸め 1 ulp の変化を
 *    見逃す（しきい値は 3〜4 ulp）。そこで **S3 時点の実装を参照コピーとしてここに凍結**し
 *    （表も含めて。本体の表が変わっても参照は動かない）、全格子で bit を比べる。
 *    ⚠️ **ホスト（この比較）と Xtensa は丸めが違う**ので、ここが通っても QEMU の checksum
 *    不変は別に確かめる。ここで守れるのは「同じコンパイラなら同じ bit」だけ。
 *    ⚠️ x = −0.0 だけは erf の符号が違ってよい（S3 は +0.0、G3 は −0.0）。GELU は
 *    1.0f + (∓0.0f) = 1.0f で同一 — それは GELU 側の bit 比較が全点で確かめる。 */
#include "saanotts_internal.h"
#include "erf_table.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef SAAN_ERF_TOL
#define SAAN_ERF_TOL 2e-7
#endif

/* ---------------------------------------------------------------- 参照（S3 の凍結コピー）
 * csrc/saanotts.c の S3 時点（commit 2d1294c）の saan_erf_approx() と erf_table.h をそのまま写した。
 * ⚠️ **本体が変わってもここは変えない。** 変えると比較が空虚になる。 */
static const float kRefErfV[129] = {
    0.0f, 0.0352503739f, 0.0704319777f, 0.105476444f,
    0.140316205f, 0.174884885f, 0.209117677f, 0.24295171f,
    0.27632639f, 0.309183728f, 0.341468634f, 0.373129194f,
    0.404116909f, 0.434386911f, 0.463898136f, 0.492613473f,
    0.520499878f, 0.547528445f, 0.573674457f, 0.598917387f,
    0.623240882f, 0.646632708f, 0.669084663f, 0.690592469f,
    0.711155634f, 0.730777292f, 0.749464026f, 0.767225661f,
    0.784075061f, 0.800027894f, 0.815102401f, 0.829319151f,
    0.842700793f, 0.85527181f, 0.867058269f, 0.878087575f,
    0.888388232f, 0.897989609f, 0.90692172f, 0.915215004f,
    0.922900128f, 0.930007797f, 0.936568575f, 0.942612727f,
    0.948170073f, 0.953269851f, 0.957940606f, 0.962210084f,
    0.966105146f, 0.969651695f, 0.972874614f, 0.975797721f,
    0.978443733f, 0.980834246f, 0.982989717f, 0.984929464f,
    0.986671671f, 0.988233404f, 0.989630626f, 0.990878228f,
    0.991990058f, 0.992978959f, 0.993856806f, 0.994634552f,
    0.995322265f, 0.995929182f, 0.996463751f, 0.996933677f,
    0.997345971f, 0.997706995f, 0.998022509f, 0.998297711f,
    0.998537283f, 0.998745432f, 0.998925927f, 0.999082135f,
    0.999217062f, 0.999333379f, 0.999433457f, 0.999519395f,
    0.999593048f, 0.999656048f, 0.999709831f, 0.999755656f,
    0.999794624f, 0.999827697f, 0.999855711f, 0.999879395f,
    0.999899378f, 0.999916206f, 0.999930349f, 0.999942213f,
    0.999952145f, 0.999960444f, 0.999967365f, 0.999973125f,
    0.99997791f, 0.999981876f, 0.999985159f, 0.999987869f,
    0.999990103f, 0.999991941f, 0.99999345f, 0.999994686f,
    0.999995697f, 0.999996522f, 0.999997195f, 0.999997741f,
    0.999998185f, 0.999998544f, 0.999998834f, 0.999999068f,
    0.999999257f, 0.999999408f, 0.99999953f, 0.999999627f,
    0.999999705f, 0.999999767f, 0.999999816f, 0.999999855f,
    0.999999886f, 0.999999911f, 0.99999993f, 0.999999945f,
    0.999999957f, 0.999999967f, 0.999999974f, 0.99999998f,
    0.999999985f
};

static const float kRefErfD[129] = {
    1.12837917f, 1.12727777f, 1.12398003f, 1.11850523f,
    1.11088527f, 1.10116441f, 1.0893988f, 1.07565596f,
    1.06001413f, 1.04256151f, 1.02339547f, 1.00262161f,
    0.98035281f, 0.956708219f, 0.931812176f, 0.905793137f,
    0.878782579f, 0.850913905f, 0.822321359f, 0.793138972f,
    0.763499536f, 0.733533637f, 0.703368732f, 0.673128302f,
    0.642931069f, 0.612890294f, 0.58311316f, 0.553700238f,
    0.524745045f, 0.496333686f, 0.468544587f, 0.441448318f,
    0.415107497f, 0.389576774f, 0.364902891f, 0.341124821f,
    0.318273959f, 0.29637438f, 0.275443153f, 0.255490686f,
    0.236521122f, 0.218532764f, 0.201518516f, 0.185466348f,
    0.170359774f, 0.156178324f, 0.142898025f, 0.130491874f,
    0.118930289f, 0.108181563f, 0.0982122808f, 0.0889877264f,
    0.080472259f, 0.0726296655f, 0.0654234833f, 0.0588172956f,
    0.0527749959f, 0.0472610247f, 0.0422405756f, 0.0376797741f,
    0.0335458284f, 0.0298071547f, 0.0264334768f, 0.0233959038f,
    0.0206669854f, 0.0182207482f, 0.0160327141f, 0.0140799029f,
    0.0123408206f, 0.010795436f, 0.0094251464f, 0.00821273464f,
    0.00714231902f, 0.00619929734f, 0.00537028654f, 0.00464305918f,
    0.00400647786f, 0.0034504286f, 0.0029657539f, 0.0025441865f,
    0.00217828423f, 0.00186136661f, 0.00158745367f, 0.00135120725f,
    0.00114787513f, 0.000973238074f, 0.000823560114f, 0.000695541885f,
    0.000586277247f, 0.000493213051f, 0.000414112032f, 0.000347018722f,
    0.000290228283f, 0.000242258111f, 0.000201822086f, 0.000167807289f,
    0.000139253052f, 0.000115332151f, 9.53340029e-05f, 7.86496935e-05f,
    6.47586832e-05f, 5.32170446e-05f, 4.3647087e-05f, 3.57282336e-05f,
    2.91890254e-05f, 2.38001345e-05f, 1.93682775e-05f, 1.57309283e-05f,
    1.27517408e-05f, 1.03165947e-05f, 8.33019234e-06f, 6.7131362e-06f,
    5.39942678e-06f, 4.33432649e-06f, 3.4725408e-06f, 2.77667379e-06f,
    2.21592028e-06f, 1.76496125e-06f, 1.40303331e-06f, 1.1131471e-06f,
    8.81432191e-07f, 6.96589658e-07f, 5.4943574e-07f, 4.32522355e-07f,
    3.39822382e-07f, 2.66469293e-07f, 2.08542285e-07f, 1.62889411e-07f,
    1.26982347e-07f
};

static float ref_erf_s3(float x) {
    const float ax = fabsf(x);
    if (ax >= 4.0f) return x < 0.0f ? -1.0f : 1.0f;
    const float u = ax * 32.0f;
    int i = (int)u;
    if (i >= 128) i = 127;
    const float t = u - (float)i;
    const float f0 = kRefErfV[i], f1 = kRefErfV[i + 1];
    const float h = 1.0f / 32.0f;
    const float d0 = kRefErfD[i] * h, d1 = kRefErfD[i + 1] * h;
    const float t2 = t * t, t3 = t2 * t;
    const float y = f0 * (2.0f * t3 - 3.0f * t2 + 1.0f)
                  + d0 * (t3 - 2.0f * t2 + t)
                  + f1 * (-2.0f * t3 + 3.0f * t2)
                  + d1 * (t3 - t2);
    return x < 0.0f ? -y : y;
}

static uint32_t f2u(float f) { uint32_t u; memcpy(&u, &f, sizeof u); return u; }
static float gelu_from_erf(float x, float e) { return 0.5f * x * (1.0f + e); }

static uint32_t rs = 0x9e3779b9u;
static float rndf(float lo, float hi) {
    rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5;
    return lo + (hi - lo) * ((float)(rs & 0xffffffu) / 16777216.0f);
}

/* §2 の 1 点。erf は ±0.0 だけ許す（上記）。GELU は厳密に bit */
static long g_grid_n = 0, g_grid_bad_erf = 0, g_grid_bad_gelu = 0;
static float g_first_bad = 0.0f; static int g_have_first = 0;
static void grid_point(float x) {
    const float a = saan_erf_approx(x), b = ref_erf_s3(x);
    const float ga = gelu_from_erf(x, a), gb = gelu_from_erf(x, b);
    ++g_grid_n;
    int bad = 0;
    if (f2u(a) != f2u(b) && !(a == 0.0f && b == 0.0f)) { ++g_grid_bad_erf; bad = 1; }
    if (f2u(ga) != f2u(gb)) { ++g_grid_bad_gelu; bad = 1; }
    if (bad && !g_have_first) { g_first_bad = x; g_have_first = 1; }
}

int main(int argc, char **argv) {
    const int expect_fail = (argc > 1 && strcmp(argv[1], "--expect-fail") == 0);
    const int expect_grid_fail = (argc > 1 && strcmp(argv[1], "--expect-grid-fail") == 0);
    const int N = 1000000;
    double maxd = 0.0; float at = 0.0f;
    /* --- §1: libm の erff との max|Δ| --------------------------------------------- */
    /* 一様乱数 [-5, 5]。|x| >= 4 のクランプ側も踏む */
    rs = 0x9e3779b9u;
    for (int i = 0; i < N; ++i) {
        const float x = rndf(-5.0f, 5.0f);
        const double d = fabs((double)saan_erf_approx(x) - (double)erff(x));
        if (d > maxd) { maxd = d; at = x; }
        grid_point(x);
    }
    /* 境界と節点そのもの、節点の中点、GELU が実際に渡す範囲（x/√2）の細かい格子 */
    static const float edges[] = { 0.0f, -0.0f, 1e-9f, -1e-9f, 0.03125f, 0.015625f, 0.5f, 1.0f,
                                   2.0f, 3.0f, 3.96875f, 3.99999f, 4.0f, 4.00001f, -4.0f, 6.0f, -6.0f,
                                   1e30f, -1e30f };
    for (size_t i = 0; i < sizeof edges / sizeof edges[0]; ++i) {
        const double d = fabs((double)saan_erf_approx(edges[i]) - (double)erff(edges[i]));
        if (d > maxd) { maxd = d; at = edges[i]; }
        grid_point(edges[i]);
    }
    for (int i = -128000; i <= 128000; ++i) {
        const float x = (float)i / 32000.0f;   /* [-4, 4] を 1/32000 刻み */
        const double d = fabs((double)saan_erf_approx(x) - (double)erff(x));
        if (d > maxd) { maxd = d; at = x; }
        grid_point(x);
    }
    const int within = maxd <= SAAN_ERF_TOL;
    printf("  §1 saan_erf_approx vs erff: max|Δ| %.3e (x = %.7g) / しきい値 %.1e  n = %d + 256,001 格子\n",
           maxd, (double)at, (double)SAAN_ERF_TOL, N);
    if (expect_fail) {
        printf("  %s 陽性対照: 線形補間は しきい値を%s\n",
               within ? "NG!" : "OK ", within ? "通ってしまった（比較が効いていない）" : "超えて落ちる");
        return within ? 1 : 0;
    }

    /* --- §2: S3 実装（凍結コピー）との全格子 bit 一致（T5-G3） ---------------------- */
    /* 節点 ±i/32（i = 0..128）と中点 ±(i+½)/32、±0.0 / ±4.0 / ±1e30、0〜8 を 1e-3 刻み（両符号）。
     * 上の §1 の乱数 1e6 点と 1/32000 格子もすでに grid_point() を通している */
    for (int i = 0; i <= 128; ++i) { const float x = (float)i / 32.0f; grid_point(x); grid_point(-x); }
    for (int i = 0; i < 128; ++i) { const float x = ((float)i + 0.5f) / 32.0f; grid_point(x); grid_point(-x); }
    { static const float sp[] = { 0.0f, -0.0f, 4.0f, -4.0f, 1e30f, -1e30f, 3.9999f, -3.9999f, 4.0001f, -4.0001f };
      for (size_t i = 0; i < sizeof sp / sizeof sp[0]; ++i) grid_point(sp[i]); }
    for (int i = -8000; i <= 8000; ++i) grid_point((float)i / 1000.0f);
    const int grid_ok = (g_grid_bad_erf == 0 && g_grid_bad_gelu == 0);
    printf("  §2 S3 実装との全格子 bit 一致: %ld 点 / erf 不一致 %ld / GELU 不一致 %ld",
           g_grid_n, g_grid_bad_erf, g_grid_bad_gelu);
    if (g_have_first) printf("（最初の不一致 x = %.7g）", (double)g_first_bad);
    printf("\n");
    if (expect_grid_fail) {
        printf("  %s 陽性対照: クランプをずらした版は全格子 bit 一致を%s\n",
               grid_ok ? "NG!" : "OK ", grid_ok ? "通ってしまった（比較が効いていない）" : "落とす");
        return grid_ok ? 1 : 0;
    }
    printf("  %s saan_erf_approx は erff と max|Δ| <= %.1e で一致\n", within ? "OK " : "NG!",
           (double)SAAN_ERF_TOL);
    printf("  %s saan_erf_approx は S3 実装と全格子で bit 一致（erf は ±0.0 のみ許容、GELU は厳密）\n",
           grid_ok ? "OK " : "NG!");
    return (within && grid_ok) ? 0 : 1;
}
