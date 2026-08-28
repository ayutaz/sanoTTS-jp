/* ESP32-S3 の PIE（128-bit 整数 SIMD）が使えるかを確かめる最小プローブ。
 *
 * **なぜ要るか**: P-1 は `saan_conv1d_i8a` の内積ループを PIE で書き直す作業だが、
 * 手元に実機が無い。**QEMU が PIE を実装しているか**が分からないと、
 * 「テストできないアセンブリ」を書くことになる（M-53 / C-032 の教訓）。
 *
 * ここで確かめるのは 3 つだけ:
 *   1. `ee.*` 命令が **実行できる**（不正命令例外で落ちない）
 *   2. `ee.vmulas.s8.accx` が **16 レーンの int8 積和を正しく計算する**
 *   3. スカラ実装と **完全一致**する
 *
 * ⚠️ **QEMU はサイクル精度ではない。** ここで速度は測らない。
 * 測れるのは正しさだけ。速度は実機（未入手）でしか出ない。
 */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_cpu.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* 16 バイト境界に置く。`ee.vld.128.ip` はアラインを要求する */
#define AL16 __attribute__((aligned(16)))

/* --- 参照（スカラ） --------------------------------------------------------- */
static int32_t dot_scalar(const int8_t *a, const int8_t *b, int n) {
    int32_t s = 0;
    for (int i = 0; i < n; ++i) s += (int32_t)a[i] * (int32_t)b[i];
    return s;
}

/* --- PIE 版 ----------------------------------------------------------------
 * `n` は 16 の倍数であること。端数は呼び出し側でスカラ処理する。
 *
 * ee.zero.accx        : 40-bit アキュムレータをゼロに
 * ee.vld.128.ip q,p,16: 128 bit ロードして p を 16 進める
 * ee.vmulas.s8.accx   : 16 レーンの int8 積を **すべて ACCX に加算**
 * ee.srs.accx dst,sh,0: ACCX を右シフト sh して 32 bit で取り出す
 */
static int32_t dot_pie(const int8_t *a, const int8_t *b, int n) {
    int32_t out = 0;
    const int8_t *pa = a, *pb = b;
    int k = n >> 4;
    if (k <= 0) return 0;
    asm volatile(
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

/* --- テストベクタ ----------------------------------------------------------- */
static AL16 int8_t A[256];
static AL16 int8_t B[256];

static uint32_t rng_state = 12345u;
static int8_t rnd8(void) {
    rng_state = rng_state * 1664525u + 1013904223u;
    return (int8_t)((rng_state >> 16) & 0xff);
}

void app_main(void) {
    printf("\n=== ESP32-S3 PIE probe ===\n");

    int bad = 0;

    /* 1. 既知の入力: すべて 1 × すべて 1 → n */
    memset(A, 1, sizeof A);
    memset(B, 1, sizeof B);
    for (int n = 16; n <= 256; n += 16) {
        const int32_t s = dot_scalar(A, B, n), p = dot_pie(A, B, n);
        if (s != p || s != n) {
            printf("  NG! n=%3d  scalar=%" PRId32 " pie=%" PRId32 " (期待 %d)\n", n, s, p, n);
            ++bad;
        }
    }
    printf("  %s 全 1 × 全 1（n=16..256）\n", bad ? "NG!" : "OK ");

    /* 2. 飽和側: -128 × -128 = 16384。16 レーンで 262144。**int16 では溢れる** */
    memset(A, -128, sizeof A);
    memset(B, -128, sizeof B);
    {
        const int32_t s = dot_scalar(A, B, 256), p = dot_pie(A, B, 256);
        const int ok = (s == p) && (s == 256 * 16384);
        printf("  %s 最悪値 -128×-128 ×256 = %" PRId32 " (pie %" PRId32 ")\n",
               ok ? "OK " : "NG!", s, p);
        if (!ok) ++bad;
    }

    /* 3. 乱数 200 回 */
    int nrand = 0;
    for (int t = 0; t < 200; ++t) {
        for (int i = 0; i < 256; ++i) { A[i] = rnd8(); B[i] = rnd8(); }
        const int n = 16 * (1 + (t % 16));
        const int32_t s = dot_scalar(A, B, n), p = dot_pie(A, B, n);
        if (s != p) {
            if (nrand < 3)
                printf("  NG! t=%d n=%d scalar=%" PRId32 " pie=%" PRId32 "\n", t, n, s, p);
            ++nrand;
        }
    }
    printf("  %s 乱数 200 回（不一致 %d）\n", nrand ? "NG!" : "OK ", nrand);
    bad += nrand;

    /* 4. 陰性対照: **わざと壊した入力で不一致が出ること**を確認する。
     *    ここが一致してしまうなら、そもそも比較が効いていない */
    for (int i = 0; i < 256; ++i) { A[i] = rnd8(); B[i] = rnd8(); }
    {
        const int32_t s = dot_scalar(A, B, 64);
        B[3] = (int8_t)(B[3] + 1);
        const int32_t p = dot_pie(A, B, 64);
        printf("  %s 陰性対照: 1 要素変えたら不一致になる (%" PRId32 " vs %" PRId32 ")\n",
               (s != p) ? "OK " : "NG!", s, p);
        if (s == p) ++bad;
    }

    printf("\n%s\n", bad ? "PIE PROBE: FAIL" : "PIE PROBE: PASS");
    printf("=== done ===\n");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(200));
    esp_cpu_stall(0);
    while (1) vTaskDelay(pdMS_TO_TICKS(1000));
}
