/* ESP32-S3 の PIE（128-bit 整数 SIMD）カーネルを QEMU で検証する。
 *
 * **なぜ要るか**: P-1 は `saan_conv1d_i8a` の内積を PIE で書き直す作業だが、
 * 手元に実機が無い。**QEMU が PIE を実装している**ので（M-56）、
 * 速度は測れないが**正しさは検証できる**。
 *
 * 5 段構え:
 *   A. PIE 命令そのもの（`ee.vmulas.s8.accx`）がスカラ内積と一致するか
 *   B. **本物のカーネル** `saan_conv1d_i8a` が PIE 有無で同じ結果を出すか
 *   C. 丸め（`saan_rint_i32` = round.s と rintf）
 *   D. **重みの置き場所のマイクロベンチ**（T6 / P-0）: 同じカーネルを flash / DRAM / PSRAM の
 *      重みで回して CCOUNT を取る。「MAC 1.61 cyc/MAC（M-82）は flash 律速か」を実機で数字にする
 *   E. **GELU のマイクロベンチ**（T6）: 表の置き場所 × erf のインライン化の 4 条件
 *
 * ⚠️ **B が正しさの本番。** A だけ通しても「カーネルに正しく組み込めたか」は言えない。
 * ⚠️ **QEMU はサイクル精度ではない。D / E の cyc は実機で読む値。** QEMU では出力の
 *    bit 一致（memcmp）と陰性対照だけがゲートで、cyc は表示形式の確認にしか使えない。
 *    `-DSAAN_QEMU=1` を付けたビルドは cyc の再現幅（<1%）を合否に入れない。
 */
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_memory_utils.h"
#include "sdkconfig.h"

#include "saanotts.h"
#include "saanotts_int8.h"
#include "saanotts_internal.h"
#include "probe_blob.h"

#define AL16 __attribute__((aligned(16)))

#ifndef SAAN_QEMU
#define SAAN_QEMU 0
#endif

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

/* --- 作業領域（B / D / E で使い回す）------------------------------------------
 *
 * ⚠️ **union にしてある理由**: 3 節を別々に static 確保すると .bss が 620 KB を超え、
 *    ESP32-S3 の内部 DRAM（.bss に使えるのは 300 KB 弱）に入らない。
 *    B → D → E は順に走り生存期間が重ならないので、最大の節（D ≈ 230 KB）ぶんだけ持つ。
 *    ⚠️ D の重みは意図して **DRAM（.bss）** に置く。D2 の「DRAM ストリーム」がそれ。 */
#define MAXC 320
#define MAXT 24

#define D_CIN      48                       /* hout / ac と同じ cin。cinp = align16(48) = 48 */
#define D_COUT     2730                     /* 2,730 行 × 48 B = 131,040 B ≥ D-cache 64 KB の 2 倍 */
#define D_T8       8                        /* SAAN_CHUNK と同じ T */
#define D_T16      16                       /* S7（CHUNK 16）を先取りした T */
#define D_W_BYTES  (D_COUT * D_CIN)         /* 131,040 */
#define D_LINE_B   32                       /* CONFIG_ESP32S3_DATA_CACHE_LINE_32B（既定） */
#define D_LINES    (D_W_BYTES / D_LINE_B)   /* 4,095 行フィル / パス */
#define D_HOT_COUT 16                       /* D1: 16 行 = 768 B = 32 B のキャッシュ行 24 本（先頭が 32 B 境界のとき。
                                               blob が保証するのは 16 B 境界なので 25 本になりうる。起動時の行に出す）。
                                               D-cache 64 KB の 1.2% なので 2 回目以降は必ずヒット */
#define D_HOT_REPS 171                      /* 171 × 16 × 8 = 21,888 dot ≈ D2 の 21,840 */
#define D_CHUNK    273                      /* ゲート用の分割。2,730 = 10 × 273 */
#define D_REPS     5

#define E_N        21664                    /* 1 step の GELU 要素数（M-80） */

static AL16 union {
    struct {
        AL16 int8_t qbuf[MAXC * MAXT];
        AL16 int8_t qbuf2[MAXC * MAXT];
        AL16 int8_t qw[MAXC * MAXC / 8];
        AL16 int8_t qwp[MAXC * MAXC / 8];   /* blob v2 のレイアウト [cout][k][cinp]（S4） */
        float sxbuf[MAXT];
        float sxbuf2[MAXT];
        float xin[MAXC * MAXT];
        float yout[MAXC * MAXT];
        float yref[MAXC * MAXT];
        float yscal[MAXC * MAXT];
        float wf[MAXC * MAXC / 8];
        float wsc[MAXC];
    } b;
    struct {
        AL16 int8_t w_dram[D_W_BYTES];      /* D2: flash の同じバイト列を DRAM に写す */
        AL16 int8_t qx[D_T16 * D_CIN];
        AL16 int8_t qx2[D_T16 * D_CIN];
        float sx[D_T16];
        float sx2[D_T16];
        float x8[D_CIN * D_T8];
        float x16[D_CIN * D_T16];           /* x16[:, t] = x8[:, t % 8]（k=1 なので行ごとの y が一致する） */
        float scale[D_COUT];
        float y[D_COUT * D_T8];             /* 87,360 B。cout=1365 × T=16 も同じ大きさ */
    } d;
    struct {
        float ref[E_N];
        float cur[E_N];
    } e;
} U;

#define qbuf   U.b.qbuf
#define qbuf2  U.b.qbuf2
#define qw     U.b.qw
#define qwp    U.b.qwp
#define sxbuf  U.b.sxbuf
#define sxbuf2 U.b.sxbuf2
#define xin    U.b.xin
#define yout   U.b.yout
#define yref   U.b.yref
#define yscal  U.b.yscal
#define wf     U.b.wf
#define wsc    U.b.wsc

/* --- B. 本物のカーネル ------------------------------------------------------
 * `saan_conv1d_i8a` を **PIE が効く形状（cin%16==0）**と **効かない形状**の
 * 両方で回し、fp32 の参照畳み込みと突き合わせる。
 *
 * **主判定は PIE 版と、同じバイナリ内のスカラ再実装との bit 完全一致。**
 * int8×int8 → int32 の積和は厳密な整数演算なので、一致しなければ実装が違う。
 * ⚠️ **SNR は補助**。「SNR が同じくらい」は一致の証明にならない（丸めで隠れる）。
 */

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
        /* ⚠️ W は blob v2 のレイアウト [cout][k][cinp]（S4）。本体と同じ添字 */
        const int8_t *wo = W + (size_t)o * ksz * cinp;
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;
                const int8_t *qu = qx + (size_t)u * cinp;
                const int8_t *wk = wo + (size_t)k * cinp;
                int32_t a32 = 0;
                for (int i = 0; i < cin; ++i)
                    a32 += (int32_t)wk[i] * (int32_t)qu[i];
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

static int one_shape(const char *tag, int cin, int cout, int ksz, int T,
                     double min_db) {
    for (int i = 0; i < cin * T; ++i) xin[i] = rndf();
    const int inner = cin * ksz;
    for (int o = 0; o < cout; ++o) {
        for (int i = 0; i < inner; ++i) wf[(size_t)o * inner + i] = rndf();
    }
    saan_quantize_w_i8(qw, wsc, wf, cout, inner);
    saan_pack_w_i8(qwp, qw, cout, cin, ksz);   /* v2 レイアウトに（ref_conv は論理形 qw を読む） */
    saan_conv1d_i8a(yout, xin, qwp, wsc, NULL, cin, cout, ksz, T, qbuf, sxbuf);
    scalar_conv1d_i8a(yscal, xin, qwp, wsc, NULL, cin, cout, ksz, T, qbuf2, sxbuf2);
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
    saan_pack_w_i8(qwp, qw, cout, cin, ksz);
    saan_conv1d_i8a(yout, xin, qwp, wsc, NULL, cin, cout, ksz, T, qbuf, sxbuf);
    qwp[5] = (int8_t)(qwp[5] + 1);               /* ← わざと壊す（cin=48 なので index 5 は実値） */
    scalar_conv1d_i8a(yscal, xin, qwp, wsc, NULL, cin, cout, ksz, T, qbuf2, sxbuf2);
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

/* --- 計時（D / E 共通）-----------------------------------------------------------
 * CCOUNT（`esp_cpu_get_cycle_count`）の差。区間中は割り込みを止める（tick と ISR を
 * 混ぜない）。区間は最長でも数 M cyc（≈ 20 ms）で、INT WDT（300 ms）には届かない。
 * ⚠️ QEMU の CCOUNT はサイクルではない（命令数や壁時計に比例）。実機で読む値。 */
typedef struct { uint32_t min, max; } cyc_minmax;

static uint32_t cyc_begin(void) {
    portDISABLE_INTERRUPTS();
    return esp_cpu_get_cycle_count();
}
static uint32_t cyc_end(uint32_t t0) {
    const uint32_t t1 = esp_cpu_get_cycle_count();
    portENABLE_INTERRUPTS();
    return t1 - t0;
}
static void mm_add(cyc_minmax *m, uint32_t c) {
    if (c < m->min) m->min = c;
    if (c > m->max) m->max = c;
}
static double mm_width_pct(const cyc_minmax *m) {
    return m->min ? 100.0 * (double)(m->max - m->min) / (double)m->min : 0.0;
}
/* 再現幅のゲート。**実機では CCOUNT は決定的**なので 5 回の幅が 1% を超えたら何かが
 * 混ざっている（割り込み / キャッシュの初期状態）。QEMU では意味が無いので合否に入れない。 */
static int width_bad(const cyc_minmax *m) {
#if SAAN_QEMU
    (void)m;
    return 0;
#else
    return mm_width_pct(m) >= 1.0;
#endif
}

static const char *region_of(const void *p) {
    if (esp_ptr_external_ram(p)) return "PSRAM";
    if (esp_ptr_in_drom(p)) return "flash(.rodata)";
    if (esp_ptr_internal(p)) return "内部 SRAM";
    return "?";
}

/* --- D. 重みの置き場所（T6 / P-0）--------------------------------------------------
 *
 * 何を測るか: 実機の MAC は 1.61 cyc/MAC（M-82）で、内訳の候補は
 *   (a) 重みの flash 行フィル（1 step に約 17,000 行）
 *   (b) dot ごとの固定費（zero/srs/float/madd の直列チェーン）
 * が同じ桁と見積もられている（estimate。docs/plan/s2-fast-kanji-m5-plan.md §1）。
 * **同じカーネル・同じバイト列・同じ dot 数**で重みの置き場所だけを変えれば、差が (a) になる。
 *
 * 重みは**本物の blob**（.rodata に埋めた student_i8.bin）の decoder 領域 131,040 B を
 * cin=48 / k=1 / cout=2,730 の行列と見なす（バイト列の中身は何でもよい。int8 として読むだけ）。
 * D-cache 64 KB の 2 倍あるので、LRU なら毎パス全行がミスする。
 *
 *   D1 hot   : flash の先頭 16 行だけを 171 回（21,888 dot）。2 回目以降は全部キャッシュヒット
 *              = **dot 固定費の床**。⚠️ 呼び出しごとに活性化の量子化（48×8）が入るので、
 *              別に測った Q（量子化だけ）× 171 を引いて MAC 区間だけにする。
 *              ⚠️ **171 × Q は D1 の raw の約半分**（M-85: 1.66 M / 3.36 M cyc）。Q の min が
 *              1% ずれると D1 は 0.5% 動く。**床の主証拠は D2**（Q を 1 回しか引かない。DRAM は
 *              キャッシュを通らないので flash の待ちが無い）で、D1 は「D2 ≈ D1」の裏取り
 *   D2 DRAM  : 同じ 131,040 B を .bss に memcpy した行列を 1 回（21,840 dot）
 *   D3 flash : .rodata の行列そのものを 1 回（21,840 dot）。**(D3 − D2) が flash の待ち**
 *   D4 PSRAM : CONFIG_SPIRAM=y のビルド（sdkconfig.psram）だけ。無ければ skip
 *   D5 flash : D3 を T=16 で（行の再利用が 2 倍 = S7 の先取り）。⚠️ y が 175 KB になるので
 *              cout=1,365 の 2 呼び出しに分ける（読む行の列は D3 と同一。量子化は 2 回）
 *
 * ゲート（QEMU でも効く）: D2 / D3 / (D4) の y が memcmp で一致（同じデータを処理した証明）
 * + D5 の各行が D2 の行の 2 回繰り返しと一致 + **DRAM 側 1 バイトを壊した陰性対照が不一致**。
 * ⚠️ y は 87 KB あるので、比較は 273 行ずつ 10 回に分けて行う（行は独立なので結果は同じ）。 */

enum { D_Q = 0, D_1, D_2, D_3, D_4, D_5, D_NCOND };

static const char *d_name[D_NCOND] = {
    "Q  量子化 48×8 だけ（差し引き用）  ",
    "D1 hot   flash 16 行 × 171 回 (T=8) ",
    "D2 DRAM  2,730 行 (T=8)           ",
    "D3 flash 2,730 行 (T=8)           ",
    "D4 PSRAM 2,730 行 (T=8)           ",
    "D5 flash 2,730 行 (T=16, 2 呼び出し)",
};
static const uint32_t d_dots[D_NCOND]  = { 0, D_HOT_REPS * D_HOT_COUT * D_T8, D_COUT * D_T8,
                                           D_COUT * D_T8, D_COUT * D_T8, D_COUT * D_T16 };
/* Q（48×8 の量子化 1 回）を何個ぶん引くか。`saan_quantize_act_i8p` はフレームごとのループなので
 * コストは T に比例する。D5 は T=16 を 2 回 = **4 単位**。
 * ⚠️ M-85（2026-09-03 の実機表）はここが 2 のまま取った値: D5 の raw min 4,371,297 − 2 × 9,687 = 99.6 cyc/dot。
 *    同じ raw から 4 単位で引き直すと 99.2 cyc/dot（換算。再測はしていない）。差 0.4% は読みを変えない。 */
static const uint32_t d_nquant[D_NCOND] = { 1, D_HOT_REPS, 1, 1, 1, 4 };

static void d_run(int c, const int8_t *wflash, const int8_t *wpsram) {
    switch (c) {
    case D_Q:
        saan_quantize_act_i8p(U.d.qx, U.d.sx, U.d.x8, D_CIN, D_T8, D_CIN);
        break;
    case D_1:
        for (int r = 0; r < D_HOT_REPS; ++r)
            saan_conv1d_i8a(U.d.y, U.d.x8, wflash, U.d.scale, NULL,
                            D_CIN, D_HOT_COUT, 1, D_T8, U.d.qx, U.d.sx);
        break;
    case D_2:
        saan_conv1d_i8a(U.d.y, U.d.x8, U.d.w_dram, U.d.scale, NULL,
                        D_CIN, D_COUT, 1, D_T8, U.d.qx, U.d.sx);
        break;
    case D_3:
        saan_conv1d_i8a(U.d.y, U.d.x8, wflash, U.d.scale, NULL,
                        D_CIN, D_COUT, 1, D_T8, U.d.qx, U.d.sx);
        break;
    case D_4:
        saan_conv1d_i8a(U.d.y, U.d.x8, wpsram, U.d.scale, NULL,
                        D_CIN, D_COUT, 1, D_T8, U.d.qx, U.d.sx);
        break;
    case D_5: {
        const int half = D_COUT / 2;   /* 1,365 行 = 65,520 B（16 の倍数なので 2 本目も整列） */
        saan_conv1d_i8a(U.d.y, U.d.x16, wflash, U.d.scale, NULL,
                        D_CIN, half, 1, D_T16, U.d.qx, U.d.sx);
        saan_conv1d_i8a(U.d.y, U.d.x16, wflash + (size_t)half * D_CIN, U.d.scale + half, NULL,
                        D_CIN, half, 1, D_T16, U.d.qx, U.d.sx);
        break;
    }
    default: break;
    }
}

/* 273 行ずつ、`wa`（T=8）と `wb`（T=8 か T=16）の y を突き合わせる。返り値は違った要素数。
 * `tb == 16` のときは wb の各行 [0,8) と [8,16) がどちらも wa の行と一致すること
 * （x16[:, t] = x8[:, t%8] なので k=1 の 1×1 conv では厳密に一致する）。 */
static size_t d_compare_rows(const int8_t *wa, const int8_t *wb, int tb) {
    float *ya = U.d.y;                              /* 273 × 8 */
    float *yb = U.d.y + (size_t)D_CHUNK * D_T8;     /* 273 × tb */
    size_t ndiff = 0;
    for (int c = 0; c < D_COUT / D_CHUNK; ++c) {
        const size_t off = (size_t)c * D_CHUNK * D_CIN;
        saan_conv1d_i8a(ya, U.d.x8, wa + off, U.d.scale + c * D_CHUNK, NULL,
                        D_CIN, D_CHUNK, 1, D_T8, U.d.qx, U.d.sx);
        if (tb == D_T8) {
            saan_conv1d_i8a(yb, U.d.x8, wb + off, U.d.scale + c * D_CHUNK, NULL,
                            D_CIN, D_CHUNK, 1, D_T8, U.d.qx2, U.d.sx2);
            for (size_t i = 0; i < (size_t)D_CHUNK * D_T8; ++i)
                if (memcmp(&ya[i], &yb[i], sizeof(float)) != 0) ++ndiff;
        } else {
            saan_conv1d_i8a(yb, U.d.x16, wb + off, U.d.scale + c * D_CHUNK, NULL,
                            D_CIN, D_CHUNK, 1, D_T16, U.d.qx2, U.d.sx2);
            for (int o = 0; o < D_CHUNK; ++o) {
                const float *ra = ya + (size_t)o * D_T8;
                const float *rb = yb + (size_t)o * D_T16;
                for (int t = 0; t < D_T16; ++t)
                    if (memcmp(&rb[t], &ra[t % D_T8], sizeof(float)) != 0) ++ndiff;
            }
        }
    }
    return ndiff;
}

static int part_d(void) {
    int bad = 0;
    size_t blob_n = 0;
    const uint8_t *blob = probe_blob(&blob_n);
    if (!blob) {
        printf("  --  D 節 skip: 重み blob がビルドに埋まっていない（csrc/student_i8.bin か "
               "-DSAAN_MODEL_BLOB=<絶対パス>）\n");
        return -1;
    }
    saan_weights w;
    const saan_status st = saan_weights_open(&w, blob, blob_n);
    if (st != SAAN_OK) {
        printf("  NG! D  saan_weights_open: %s\n", saan_strerror(st));
        return 1;
    }
    /* 領域の先頭は decoder.inp.weight（int8。offset は 16 の倍数）。そこから 131,040 B を
     * 「重み行列」と見なす。中身にテンソル境界や scale / bias の float が混ざるが、int8 の
     * バイト列として読むだけなので測定には関係ない。 */
    const float *sc_unused = NULL;
    const int8_t *wflash = saan_ti8(&w, &sc_unused, "decoder.inp.weight");
    if (!wflash) { printf("  NG! D  decoder.inp.weight が引けない\n"); return 1; }
    if ((size_t)(wflash - (const int8_t *)blob) + D_W_BYTES > blob_n) {
        printf("  NG! D  領域 %d B が blob の末尾を越える\n", D_W_BYTES);
        return 1;
    }
    if (((uintptr_t)wflash & 15u) != 0u) {
        printf("  NG! D  領域が 16 B 境界に無い（PIE に載らない）\n");
        return 1;
    }
    printf("  blob %zu B / sha256 %.16s… / tensors %" PRIu32 "\n", blob_n, probe_blob_sha256(),
           w.n_tensors);
    printf("  行列: cin=%d k=1 cout=%d → %d B = %d 行 × %d B（32 B のキャッシュ行 %d 本 / パス）\n",
           D_CIN, D_COUT, D_W_BYTES, D_COUT, D_CIN, D_LINES);

    /* 入力: 決定的な擬似乱数。x16 は x8 をフレーム方向に 2 回並べる */
    for (int i = 0; i < D_CIN * D_T8; ++i) U.d.x8[i] = rndf();
    for (int c = 0; c < D_CIN; ++c)
        for (int t = 0; t < D_T16; ++t) U.d.x16[c * D_T16 + t] = U.d.x8[c * D_T8 + (t % D_T8)];
    for (int o = 0; o < D_COUT; ++o) U.d.scale[o] = 0.5f + 0.5f * fabsf(rndf());
    memcpy(U.d.w_dram, wflash, D_W_BYTES);

    int8_t *wpsram = NULL;
#if CONFIG_SPIRAM
    wpsram = (int8_t *)heap_caps_aligned_alloc(16, D_W_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (wpsram) memcpy(wpsram, wflash, D_W_BYTES);
    else printf("  --  D4 skip: CONFIG_SPIRAM=y だが PSRAM から %d B 取れない（PSRAM 無しの板 / QEMU）\n",
                D_W_BYTES);
#else
    printf("  --  D4 skip: CONFIG_SPIRAM 無し（sdkconfig.psram を重ねたビルドで測る）\n");
#endif
    printf("  置き場所: flash %p [%s] / DRAM %p [%s] / PSRAM %p [%s]\n",
           (const void *)wflash, region_of(wflash), (const void *)U.d.w_dram, region_of(U.d.w_dram),
           (const void *)wpsram, wpsram ? region_of(wpsram) : "skip");
    printf("  D1 の hot 集合: %d 行 × %d B = %d B = キャッシュ行 %d 本（先頭%s）\n",
           D_HOT_COUT, D_CIN, D_HOT_COUT * D_CIN,
           ((uintptr_t)wflash % D_LINE_B) ? D_HOT_COUT * D_CIN / D_LINE_B + 1 : D_HOT_COUT * D_CIN / D_LINE_B,
           ((uintptr_t)wflash % D_LINE_B) ? "は 32 B 境界に無い（16 B のみ）" : "は 32 B 境界");

    /* --- 計時 --- */
    cyc_minmax mm[D_NCOND];
    for (int c = 0; c < D_NCOND; ++c) {
        mm[c].min = UINT32_MAX; mm[c].max = 0;
        if (c == D_4 && !wpsram) continue;
        for (int r = 0; r < D_REPS; ++r) {
            const uint32_t t0 = cyc_begin();
            d_run(c, wflash, wpsram);
            mm_add(&mm[c], cyc_end(t0));
        }
    }
    const double q1 = (double)mm[D_Q].min;
    printf("  %-40s min %10" PRIu32 " / max %10" PRIu32 " cyc（幅 %.2f%%）\n",
           d_name[D_Q], mm[D_Q].min, mm[D_Q].max, mm_width_pct(&mm[D_Q]));
    double net[D_NCOND] = {0};
    for (int c = D_1; c < D_NCOND; ++c) {
        if (c == D_4 && !wpsram) { printf("  %-40s skip\n", d_name[c]); continue; }
        net[c] = (double)mm[c].min - q1 * d_nquant[c];
        const int wb = width_bad(&mm[c]);
        if (wb) ++bad;
        printf("  %-40s min %10" PRIu32 " / max %10" PRIu32 " cyc（幅 %.2f%%%s）"
               " dot %6" PRIu32 "  net %7.1f cyc/dot  %.3f cyc/MAC\n",
               d_name[c], mm[c].min, mm[c].max, mm_width_pct(&mm[c]),
               wb ? " NG!" : "", d_dots[c], net[c] / d_dots[c], net[c] / (d_dots[c] * (double)D_CIN));
    }
    printf("  （net = min − Q × 量子化回数。cyc/MAC は M-82 の 1.61 と比べる値。"
           "QEMU の cyc は%s）\n", SAAN_QEMU ? "**意味が無い**（表示形式の確認のみ）" : "実機の CCOUNT");

    /* --- 読み方 --- */
    const double d32 = net[D_3] - net[D_2];
    printf("  cyc/行（(D3−D2)/%d 行）        : %8.1f\n", D_COUT, d32 / D_COUT);
    printf("  cyc/キャッシュ行（(D3−D2)/%d）  : %8.1f   ← 200〜300 なら flash 律速の仮説成立 / <60 なら棄却\n",
           D_LINES, d32 / D_LINES);
    printf("  flash の割合 (D3−D2)/D3          : %7.1f%%  ← MAC の 1/3 以上なら S7 側の分岐（計画 §5）\n",
           net[D_3] > 0 ? 100.0 * d32 / net[D_3] : 0.0);
    printf("  DRAM ストリームの上乗せ (D2−D1)  : %8.1f cyc/dot（≈0 なら DRAM ストリームは無償）\n",
           net[D_2] / d_dots[D_2] - net[D_1] / d_dots[D_1]);
    printf("  D5 の償却 (D5 − 2·D2)/%d 行     : %8.1f cyc/行（D3 の cyc/行の半分なら「再利用で償却」）\n",
           D_COUT, (net[D_5] - 2.0 * net[D_2]) / D_COUT);
    if (wpsram)
        printf("  PSRAM の差 (D4−D3)/%d 行       : %8.1f cyc/行\n", D_COUT, (net[D_4] - net[D_3]) / D_COUT);

    /* --- ゲート: 同じデータを処理した証明 --- */
    {
        const size_t n23 = d_compare_rows(U.d.w_dram, wflash, D_T8);
        printf("  %s D  D2 (DRAM) と D3 (flash) の y が bit 一致（273 行 × 10、差 %zu 要素）\n",
               n23 == 0 ? "OK " : "NG!", n23);
        if (n23) ++bad;
        const size_t n25 = d_compare_rows(U.d.w_dram, wflash, D_T16);
        printf("  %s D  D5 (T=16) の各行 = D2 (T=8) の行の 2 回繰り返し（差 %zu 要素）\n",
               n25 == 0 ? "OK " : "NG!", n25);
        if (n25) ++bad;
        if (wpsram) {
            const size_t n24 = d_compare_rows(U.d.w_dram, wpsram, D_T8);
            printf("  %s D  D2 (DRAM) と D4 (PSRAM) の y が bit 一致（差 %zu 要素）\n",
                   n24 == 0 ? "OK " : "NG!", n24);
            if (n24) ++bad;
        }
        /* 陰性対照: DRAM 側の 1 バイトを壊すと不一致になり、戻すと一致に戻ること */
        U.d.w_dram[5] = (int8_t)(U.d.w_dram[5] + 1);
        const size_t nneg = d_compare_rows(U.d.w_dram, wflash, D_T8);
        U.d.w_dram[5] = wflash[5];
        const size_t nback = d_compare_rows(U.d.w_dram, wflash, D_T8);
        const int okn = (nneg > 0) && (nback == 0);
        printf("  %s D  陰性対照: DRAM 側 1 バイトを壊すと差 %zu 要素 / 戻すと %zu 要素\n",
               okn ? "OK " : "NG!", nneg, nback);
        if (!okn) ++bad;
    }
    if (wpsram) heap_caps_free(wpsram);
    return bad;
}

/* --- E. GELU（T6）------------------------------------------------------------------
 *
 * 実機の GELU は 118 cyc/要素（M-82）。原因の候補は (i) erf の表が flash にある
 * (ii) `saan_erf_approx` が要素ごとの関数呼び出し（call8 + FP 定数の再ロード + 戻り値の往復）。
 * 調査（docs/plan/s2-fast-kanji-m5-plan.md §1）は (i) を ≈2%、(ii) を主因と見積もった（estimate）。
 * 同じ 21,664 要素で 4 条件を回して確かめる:
 *
 *   E1 現行の saan_gelu（csrc/saanotts.c。表 flash / erf は call）
 *   E2 表を DRAM に写したローカル版（erf は noinline = call のまま）   → (i) だけの効き
 *   E3 erf を always_inline にしたローカル版（表は flash のまま）       → (ii) だけの効き
 *   E4 両方
 *
 * E2〜E4 は本体の式を**そのまま**写したもの（`PROBE_ERF_BODY` / `PROBE_GELU_LOOP`）。
 * ⚠️ 「同じカーネルを 2 回書かない」原則の例外。写しが本体とずれていないことは
 *    **4 条件の出力が memcmp で一致する**ことで示す（陰性対照: DRAM の表を 1 要素壊すと不一致）。
 * ⚠️ **T5（GELU のコード生成。別ブランチ）で本体の表が `kSaanErfD`（erf'）から
 *    `kSaanErfDh`（erf' × h を事前に掛けた表）に変わる。** そのとき本体は `D[i] * h` を掛けなくなるので、
 *    T5 と合流したら `PROBE_ERF_BODY` の `d0 / d1` の行と `g_tabD` の memcpy 元を本体に合わせて
 *    書き直すこと。ずれたままなら E1 と E3/E4 の memcmp が落ちて教えてくれる（黙って通ることはない）。
 * ⚠️ IDF は `-ffp-contract=fast`。インライン化で madd.s への縮約が変わると**丸め水準で**
 *    出力が動きうる（T5 の懸念）。E1 と E3/E4 の memcmp が落ちたらそれが検出されたということで、
 *    ゲートを緩めずに記録すること。 */
#include "erf_table.h"   /* kSaanErfV / kSaanErfD の .rodata コピー（本体と同じ生成物） */

static float g_tabV[SAAN_ERF_N + 1];   /* .bss = 内部 DRAM */
static float g_tabD[SAAN_ERF_N + 1];

/* ⚠️ csrc/saanotts.c の saan_erf_approx と 1 行ずつ同じ式（このブランチ = T5 前の形: `D[i] * h`）。
 *    T5 合流後は本体に合わせて更新する（上の注記） */
#define PROBE_ERF_BODY(V, D)                                                    \
    const float ax = fabsf(x);                                                  \
    if (ax >= SAAN_ERF_XMAX) return x < 0.0f ? -1.0f : 1.0f;                    \
    const float u = ax * (float)SAAN_ERF_H_INV;                                 \
    int i = (int)u;                                                             \
    if (i >= SAAN_ERF_N) i = SAAN_ERF_N - 1;                                    \
    const float t = u - (float)i;                                               \
    const float f0 = V[i], f1 = V[i + 1];                                       \
    const float h = 1.0f / (float)SAAN_ERF_H_INV;                               \
    const float d0 = D[i] * h, d1 = D[i + 1] * h;                               \
    const float t2 = t * t, t3 = t2 * t;                                        \
    const float y = f0 * (2.0f * t3 - 3.0f * t2 + 1.0f)                         \
                  + d0 * (t3 - 2.0f * t2 + t)                                   \
                  + f1 * (-2.0f * t3 + 3.0f * t2)                               \
                  + d1 * (t3 - t2);                                             \
    return x < 0.0f ? -y : y;

static float __attribute__((noinline)) erf_call_dram(float x) { PROBE_ERF_BODY(g_tabV, g_tabD) }
static inline float __attribute__((always_inline)) erf_inl_flash(float x) { PROBE_ERF_BODY(kSaanErfV, kSaanErfD) }
static inline float __attribute__((always_inline)) erf_inl_dram(float x) { PROBE_ERF_BODY(g_tabV, g_tabD) }

#define PROBE_GELU_LOOP(ERF)                                                    \
    for (size_t i = 0; i < n; ++i)                                              \
        x[i] = 0.5f * x[i] * (1.0f + ERF(x[i] * 0.70710678f));

static void __attribute__((noinline)) gelu_e2(float *x, size_t n) { PROBE_GELU_LOOP(erf_call_dram) }
static void __attribute__((noinline)) gelu_e3(float *x, size_t n) { PROBE_GELU_LOOP(erf_inl_flash) }
static void __attribute__((noinline)) gelu_e4(float *x, size_t n) { PROBE_GELU_LOOP(erf_inl_dram) }

/* 入力: 決定的な擬似乱数 [-6, 6]（GELU が実際に受ける範囲。|x| ≥ 5.66 でクランプ側も踏む） */
static void e_fill(float *x) {
    uint32_t s = 0x2545f491u;
    for (int i = 0; i < E_N; ++i) {
        s = s * 1664525u + 1013904223u;
        x[i] = 12.0f * ((float)(s >> 8) / 16777216.0f) - 6.0f;
    }
}

static void e_run(int c) {
    switch (c) {
    case 0: saan_gelu(U.e.cur, E_N); break;
    case 1: gelu_e2(U.e.cur, E_N); break;
    case 2: gelu_e3(U.e.cur, E_N); break;
    default: gelu_e4(U.e.cur, E_N); break;
    }
}

static size_t e_diff_vs_ref(void) {
    size_t n = 0;
    for (int i = 0; i < E_N; ++i)
        if (memcmp(&U.e.cur[i], &U.e.ref[i], sizeof(float)) != 0) ++n;
    return n;
}

static int part_e(void) {
    static const char *name[4] = {
        "E1 現行 saan_gelu（表 flash / erf は call）",
        "E2 表を DRAM に（erf は call）             ",
        "E3 erf を inline（表 flash）               ",
        "E4 inline + 表 DRAM                        ",
    };
    int bad = 0;
    memcpy(g_tabV, kSaanErfV, sizeof g_tabV);
    memcpy(g_tabD, kSaanErfD, sizeof g_tabD);
    printf("  表: flash %p [%s] / DRAM %p [%s]   要素 %d\n",
           (const void *)kSaanErfV, region_of(kSaanErfV), (const void *)g_tabV, region_of(g_tabV), E_N);

    /* 参照 = E1 の出力 */
    e_fill(U.e.cur);
    saan_gelu(U.e.cur, E_N);
    memcpy(U.e.ref, U.e.cur, sizeof U.e.ref);

    for (int c = 0; c < 4; ++c) {
        cyc_minmax mm = { UINT32_MAX, 0 };
        size_t ndiff = 0;
        for (int r = 0; r < D_REPS; ++r) {
            e_fill(U.e.cur);
            const uint32_t t0 = cyc_begin();
            e_run(c);
            mm_add(&mm, cyc_end(t0));
            ndiff += e_diff_vs_ref();
        }
        const int wb = width_bad(&mm);
        if (wb) ++bad;
        if (ndiff) ++bad;
        printf("  %s %s min %9" PRIu32 " / max %9" PRIu32 " cyc（幅 %.2f%%%s） %6.1f cyc/要素  E1 との差 %zu 要素\n",
               ndiff ? "NG!" : "OK ", name[c], mm.min, mm.max, mm_width_pct(&mm), wb ? " NG!" : "",
               (double)mm.min / E_N, ndiff);
    }

    /* 陰性対照: DRAM の表 1 要素を壊すと E4 が E1 と食い違い、戻すと一致に戻ること。
     * 壊すのは節点 64（x/√2 ∈ [2.0, 2.03125) → |x| ∈ [2.83, 2.87)）。その区間に入力が
     * 何個落ちたかも数える（0 個なら陰性対照が空振りする）。 */
    {
        int hits = 0;
        e_fill(U.e.cur);
        for (int i = 0; i < E_N; ++i) {
            const float ax = fabsf(U.e.cur[i] * 0.70710678f);
            const int b = (int)(ax * (float)SAAN_ERF_H_INV);
            if (b == 63 || b == 64) ++hits;     /* 節点 64 は区間 63 の f1 / 区間 64 の f0 */
        }
        g_tabV[64] += 1e-3f;
        gelu_e4(U.e.cur, E_N);
        const size_t nneg = e_diff_vs_ref();
        g_tabV[64] = kSaanErfV[64];
        e_fill(U.e.cur);
        gelu_e4(U.e.cur, E_N);
        const size_t nback = e_diff_vs_ref();
        const int okn = (hits > 0) && (nneg > 0) && (nback == 0);
        printf("  %s E  陰性対照: DRAM の表 1 要素（節点 64）を壊すと差 %zu 要素（該当区間の入力 %d 個）/ 戻すと %zu 要素\n",
               okn ? "OK " : "NG!", nneg, hits, nback);
        if (!okn) ++bad;
    }
    return bad;
}

void app_main(void) {
    printf("\n=== ESP32-S3 PIE probe ===\n");
#if defined(SAAN_PIE) && SAAN_PIE
    printf("  ビルド: SAAN_PIE=1（PIE カーネル有効）\n");
#else
    printf("  ⚠️ ビルド: SAAN_PIE 未定義 — **PIE は使われていない**\n");
#endif
#if SAAN_QEMU
    printf("  ビルド: SAAN_QEMU=1（D / E の cyc は意味が無い。再現幅は合否に入れない）\n");
#else
    printf("  ビルド: 実機向け（D / E の cyc は CCOUNT。5 回の幅 ≥ 1%% は NG）\n");
#endif
    printf("  CPU %d MHz / D-cache %d KB / I-cache %d KB / flash %s\n",
           CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
#if CONFIG_ESP32S3_DATA_CACHE_64KB
           64,
#elif CONFIG_ESP32S3_DATA_CACHE_32KB
           32,
#else
           16,
#endif
#if CONFIG_ESP32S3_INSTRUCTION_CACHE_32KB
           32,
#else
           16,
#endif
#if CONFIG_ESPTOOLPY_FLASHMODE_QIO
           "QIO"
#elif CONFIG_ESPTOOLPY_FLASHMODE_DIO
           "DIO"
#else
           "?"
#endif
           );
    printf("\n-- A. PIE 命令そのもの --\n");
    const int a = part_a();
    printf("\n-- B. saan_conv1d_i8a（本物のカーネル） --\n");
    const int b = part_b();
    printf("\n-- C. 丸め（round.s vs rintf。S2） --\n");
    const int c = part_c();
    printf("\n-- D. 重みの置き場所（saan_conv1d_i8a。T6 / P-0） --\n");
    const int d = part_d();                 /* -1 = blob 無しで skip */
    printf("\n-- E. GELU 21,664 要素（表 flash / DRAM × erf call / inline。T6） --\n");
    const int e = part_e();

    const int dfail = d < 0 ? 0 : d;
    printf("\n%s  (A %d 件 / B %d 件 / C %d 件 / D %s / E %d 件 の失敗)%s\n",
           (a + b + c + dfail + e) ? "PIE PROBE: FAIL" : "PIE PROBE: PASS", a, b, c,
           d < 0 ? "skip" : (d == 0 ? "0 件" : "NG"), e,
           d < 0 ? "  ⚠️ D 節は blob 無しで skip" : "");
    if (d > 0) printf("  D の失敗 %d 件\n", d);
    printf("=== done ===\n");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(300));
    /* QEMU を素直に終わらせるため、あとは寝るだけにする */
    while (1) vTaskDelay(pdMS_TO_TICKS(1000));
}
