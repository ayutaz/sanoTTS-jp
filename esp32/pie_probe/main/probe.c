/* ESP32-S3 の PIE（128-bit 整数 SIMD）カーネルを QEMU で検証する。
 *
 * **なぜ要るか**: P-1 は `saan_conv1d_i8a` の内積を PIE で書き直す作業だが、
 * 手元に実機が無い。**QEMU が PIE を実装している**ので（M-56）、
 * 速度は測れないが**正しさは検証できる**。
 *
 * 2 段構え:
 *   A. PIE 命令そのもの（`ee.vmulas.s8.accx`）がスカラ内積と一致するか
 *   B. **本物のカーネル** `saan_conv1d_i8a` が PIE 有無で同じ結果を出すか
 *
 * ⚠️ **B が本番。** A だけ通しても「カーネルに正しく組み込めたか」は言えない。
 * ⚠️ **QEMU はサイクル精度ではない。速度は一切測れない。**
 */
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "saanotts.h"
#include "saanotts_int8.h"

#define AL16 __attribute__((aligned(16)))

/* --- A. PIE 命令そのもの ---------------------------------------------------- */

static int32_t dot_scalar(const int8_t *a, const int8_t *b, int n) {
    int32_t s = 0;
    for (int i = 0; i < n; ++i) s += (int32_t)a[i] * (int32_t)b[i];
    return s;
}

static int32_t dot_pie(const int8_t *a, const int8_t *b, int n) {
    int32_t out = 0;
    const int8_t *pa = a, *pb = b;
    int k = n >> 4;
    if (k <= 0) return 0;
    __asm__ volatile(
        "ee.zero.accx                 \n"
        "1:                           \n"
        "  ee.vld.128.ip q0, %[pa], 16\n"
        "  ee.vld.128.ip q1, %[pb], 16\n"
        "  ee.vmulas.s8.accx q0, q1   \n"
        "  addi %[k], %[k], -1        \n"
        "  bnez %[k], 1b              \n"
        "ee.srs.accx %[out], %[sh], 0 \n"
        : [out] "=&a"(out), [pa] "+&a"(pa), [pb] "+&a"(pb), [k] "+&a"(k)
        : [sh] "a"(0)
        : "memory");
    return out;
}

static AL16 int8_t A[512];
static AL16 int8_t B[512];

static uint32_t rs = 12345u;
static int8_t rnd8(void) {
    rs = rs * 1664525u + 1013904223u;
    return (int8_t)((rs >> 16) & 0xff);
}
static float rndf(void) {
    rs = rs * 1664525u + 1013904223u;
    return (float)((int32_t)(rs >> 8) % 2001 - 1000) / 1000.0f;
}

static int part_a(void) {
    int bad = 0;

    memset(A, 1, sizeof A);
    memset(B, 1, sizeof B);
    for (int n = 16; n <= 512; n += 16)
        if (dot_pie(A, B, n) != n) ++bad;
    printf("  %s A1 全1×全1 (n=16..512)\n", bad ? "NG!" : "OK ");

    memset(A, -128, sizeof A);
    memset(B, -128, sizeof B);
    {
        const int32_t p = dot_pie(A, B, 512);
        const int ok = (p == 512 * 16384);
        printf("  %s A2 最悪値 -128×-128 ×512 = %" PRId32 " (期待 %d)\n",
               ok ? "OK " : "NG!", p, 512 * 16384);
        if (!ok) ++bad;
    }

    int nr = 0;
    for (int t = 0; t < 300; ++t) {
        for (int i = 0; i < 512; ++i) { A[i] = rnd8(); B[i] = rnd8(); }
        const int n = 16 * (1 + (t % 32));
        if (dot_scalar(A, B, n) != dot_pie(A, B, n)) ++nr;
    }
    printf("  %s A3 乱数 300 回（不一致 %d）\n", nr ? "NG!" : "OK ", nr);
    bad += nr;

    /* 陰性対照: 1 要素変えたら必ず不一致になること */
    for (int i = 0; i < 512; ++i) { A[i] = rnd8(); B[i] = rnd8(); }
    {
        const int32_t s = dot_scalar(A, B, 64);
        B[3] = (int8_t)(B[3] + 1);
        const int ok = (s != dot_pie(A, B, 64));
        printf("  %s A4 陰性対照（1 要素変えたら不一致）\n", ok ? "OK " : "NG!");
        if (!ok) ++bad;
    }
    return bad;
}

/* --- B. 本物のカーネル ------------------------------------------------------
 * `saan_conv1d_i8a` を **PIE が効く形状（cin%16==0）**と **効かない形状**の
 * 両方で回し、fp32 の参照畳み込みと突き合わせる。
 *
 * **主判定は PIE 版と、同じバイナリ内のスカラ再実装との bit 完全一致。**
 * int8×int8 → int32 の積和は厳密な整数演算なので、一致しなければ実装が違う。
 * ⚠️ **SNR は補助**。「SNR が同じくらい」は一致の証明にならない（丸めで隠れる）。
 */
#define MAXC 320
#define MAXT 24

static AL16 int8_t qbuf[MAXC * MAXT];
static float sxbuf[MAXT];
static float xin[MAXC * MAXT];
static float yout[MAXC * MAXT];
static float yref[MAXC * MAXT];
static AL16 int8_t qw[MAXC * MAXC / 8];
static float wf[MAXC * MAXC / 8];
static float wsc[MAXC];

/* `saan_conv1d_i8a` の**スカラ再実装**。PIE を一切使わない。
 *
 * ⚠️ **これが本当の合否基準。** int8×int8 → int32 の積和は**厳密な整数演算**なので、
 * PIE 版とスカラ版は **bit 完全一致**しなければならない。
 * 「SNR が同じくらい」は一致の証明にならない（丸めで隠れる）。
 * ⚠️ 積和の順序も本体と揃えること。float への足し込み順が変わると
 * 最後の `acc` が 1 ulp 違いうる。 */
static void scalar_conv1d_i8a(float *y, const float *x, const int8_t *W,
                              const float *sc, const float *b, int cin, int cout,
                              int ksz, int T, int8_t *qx, float *sx) {
    const int pad = ksz / 2;
    /* ⚠️ **本体と同じ padded ストライドを使う。** ここを `cin` のままにすると
     * 「主判定が両方同じように間違う」のではなく、**参照だけがずれて偽の NG** が出る。
     * カーネルの読み位置は 1 箇所（本体）で決まっているので必ず追従させること。 */
    const int cinp = (int)((cin + 15) & ~15);
    saan_quantize_act_i8p(qx, sx, x, cin, T, cinp);
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        const float s = sc[o];
        const float bias = b ? b[o] : 0.0f;
        const int8_t *wo = W + (size_t)o * cin * ksz;
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;
                const int8_t *qu = qx + (size_t)u * cinp;
                int32_t a32 = 0;
                for (int i = 0; i < cin; ++i)
                    a32 += (int32_t)wo[(size_t)i * ksz + k] * (int32_t)qu[i];
                acc += (float)a32 * sx[u];
            }
            yo[t] = acc * s + bias;
        }
    }
}

/* fp32 の参照畳み込み（量子化した重みを float に戻して掛ける = W8A32 相当） */
static void ref_conv(float *y, const float *x, const int8_t *W, const float *sc,
                     int cin, int cout, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        for (int t = 0; t < T; ++t) {
            float a = 0.0f;
            for (int i = 0; i < cin; ++i)
                for (int k = 0; k < ksz; ++k) {
                    const int u = t + k - pad;
                    if (u < 0 || u >= T) continue;
                    a += (float)W[((size_t)o * cin + i) * ksz + k] * x[(size_t)i * T + u];
                }
            yo[t] = a * sc[o];
        }
    }
}

static double snr_db(const float *got, const float *ref, size_t n) {
    double sig = 0.0, err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const double r = ref[i], e = (double)got[i] - r;
        sig += r * r; err += e * e;
    }
    if (err == 0.0) return 1e9;
    return 10.0 * log10(sig / err);
}

static AL16 int8_t qbuf2[MAXC * MAXT];
static float sxbuf2[MAXT];
static float yscal[MAXC * MAXT];

static int one_shape(const char *tag, int cin, int cout, int ksz, int T,
                     double min_db) {
    for (int i = 0; i < cin * T; ++i) xin[i] = rndf();
    const int inner = cin * ksz;
    for (int o = 0; o < cout; ++o) {
        for (int i = 0; i < inner; ++i) wf[(size_t)o * inner + i] = rndf();
    }
    saan_quantize_w_i8(qw, wsc, wf, cout, inner);
    saan_conv1d_i8a(yout, xin, qw, wsc, NULL, cin, cout, ksz, T, qbuf, sxbuf);
    scalar_conv1d_i8a(yscal, xin, qw, wsc, NULL, cin, cout, ksz, T, qbuf2, sxbuf2);
    ref_conv(yref, xin, qw, wsc, cin, cout, ksz, T);

    /* **主判定: PIE 版とスカラ版が bit 完全一致すること** */
    const size_t n = (size_t)cout * T;
    size_t ndiff = 0;
    for (size_t i = 0; i < n; ++i)
        if (memcmp(&yout[i], &yscal[i], sizeof(float)) != 0) ++ndiff;

    const double db = snr_db(yout, yref, n);
    const int pie = 1;   /* パディング後は全 conv 層が PIE（dwconv を除く） */
    const int ok = (ndiff == 0) && (db >= min_db);
    printf("  %s B  %-22s cin=%-4d ksz=%d  bit差 %zu/%zu  SNR %6.2f dB  (PIE %s)\n",
           ok ? "OK " : "NG!", tag, cin, ksz, ndiff, n, db, pie ? "有効" : "無効");
    return ok ? 0 : 1;
}

/* 陰性対照: **わざと 1 要素壊したら bit 差が出ること**を確かめる。
 * これが出ないなら比較自体が効いていない（`.claude/skills/writing-gates/`） */
static int negative_control(void) {
    const int cin = 48, cout = 8, ksz = 1, T = MAXT;
    for (int i = 0; i < cin * T; ++i) xin[i] = rndf();
    for (int i = 0; i < cout * cin; ++i) wf[i] = rndf();
    saan_quantize_w_i8(qw, wsc, wf, cout, cin * ksz);
    saan_conv1d_i8a(yout, xin, qw, wsc, NULL, cin, cout, ksz, T, qbuf, sxbuf);
    qw[5] = (int8_t)(qw[5] + 1);                 /* ← わざと壊す */
    scalar_conv1d_i8a(yscal, xin, qw, wsc, NULL, cin, cout, ksz, T, qbuf2, sxbuf2);
    size_t ndiff = 0;
    for (size_t i = 0; i < (size_t)cout * T; ++i)
        if (memcmp(&yout[i], &yscal[i], sizeof(float)) != 0) ++ndiff;
    const int ok = ndiff > 0;
    printf("  %s B  陰性対照: 重み 1 要素を壊すと bit 差 %zu 件\n",
           ok ? "OK " : "NG!", ndiff);
    return ok ? 0 : 1;
}

static int part_b(void) {
    int bad = 0;
    /* ⚠️ しきい値は **activation 量子化の性質**で決まる。per-frame int8 なので
     * 30 dB 台が正常。ホスト（PIE 無し）で同じ形状を測った値に合わせてある。 */
    bad += one_shape("dec pw2  (304)",  304, 32, 1, MAXT, 25.0);
    bad += one_shape("hout    (48)",     48, 64, 1, MAXT, 25.0);
    bad += one_shape("ac c1 k5 (48)",     48, 48, 5, MAXT, 25.0);
    bad += one_shape("dec inp  (40->48)*", 40, 76, 3, MAXT, 25.0);
    bad += one_shape("cup      (12->16)*", 12, 76, 1, MAXT, 25.0);
    bad += one_shape("pw1      (76->80)*", 76, 96, 1, MAXT, 25.0);
    bad += one_shape("hdown    (76->80)*", 76, 48, 1, MAXT, 25.0);
    bad += negative_control();
    return bad;
}

/* --- C. 丸め（S2）----------------------------------------------------------------
 * `saan_rint_i32()` は Xtensa では `round.s`（モード 0）、ホストでは `(int32_t)rintf()`。
 * **同じ規則（ties-to-even）であること**を、タイと境界を含む値で確かめる。
 * 参照は同じバイナリ内の `rintf`（newlib）。⚠️ 陽性対照: half-away-from-zero の
 * 実装（`(int)(v + copysign(0.5, v))`）はタイで食い違うことを先に示す。 */
static int part_c(void) {
    static const float vals[] = {
        0.5f, 1.5f, 2.5f, 3.5f, -0.5f, -1.5f, -2.5f, -3.5f,
        0.49999997f, 0.50000006f, 126.5f, -126.5f, 127.49999f, -127.49999f,
        1e-8f, -1e-8f, 0.0f, -0.0f, 12345.5f, -12345.5f, 2.9999998f, -2.9999998f,
    };
    const int n = (int)(sizeof vals / sizeof vals[0]);
    int bad = 0, ties_differ_away = 0;
    for (int i = 0; i < n; ++i) {
        const int32_t got = saan_rint_i32(vals[i]);
        const int32_t ref = (int32_t)rintf(vals[i]);
        const int32_t away = (int32_t)(vals[i] + (vals[i] >= 0.0f ? 0.5f : -0.5f));
        if (got != ref) {
            printf("  NG! C  saan_rint_i32(%.9g) = %ld / rintf = %ld\n",
                   (double)vals[i], (long)got, (long)ref);
            ++bad;
        }
        if (away != ref) ++ties_differ_away;
    }
    if (!bad) printf("  OK  C1 saan_rint_i32 == rintf（%d 値。タイと境界を含む）\n", n);
    /* 陽性対照: half-away-from-zero が ties-to-even と食い違うのは**奇数側に切り上がるタイ**だけ:
     * ±0.5（0 vs 1）/ ±2.5（2 vs 3）/ ±126.5（126 vs 127）の 6 値。±1.5 / ±3.5 は偶数側で一致する。
     * ⚠️ 最初 8 と書いて落ちた（±1.5 / ±3.5 を数えていた）。 */
    const int ok2 = ties_differ_away >= 6;
    printf("  %s C2 陽性対照: half-away-from-zero は %d 値で rintf と違う（>= 6 なら比較は効いている）\n",
           ok2 ? "OK " : "NG!", ties_differ_away);
    if (!ok2) ++bad;
    return bad;
}

void app_main(void) {
    printf("\n=== ESP32-S3 PIE probe ===\n");
#if defined(SAAN_PIE) && SAAN_PIE
    printf("  ビルド: SAAN_PIE=1（PIE カーネル有効）\n");
#else
    printf("  ⚠️ ビルド: SAAN_PIE 未定義 — **PIE は使われていない**\n");
#endif
    printf("\n-- A. PIE 命令そのもの --\n");
    const int a = part_a();
    printf("\n-- B. saan_conv1d_i8a（本物のカーネル） --\n");
    const int b = part_b();
    printf("\n-- C. 丸め（round.s vs rintf。S2） --\n");
    const int c = part_c();

    printf("\n%s  (A %d 件 / B %d 件 / C %d 件 の失敗)\n",
           (a + b + c) ? "PIE PROBE: FAIL" : "PIE PROBE: PASS", a, b, c);
    printf("=== done ===\n");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(300));
    /* QEMU を素直に終わらせるため、あとは寝るだけにする */
    while (1) vTaskDelay(pdMS_TO_TICKS(1000));
}
