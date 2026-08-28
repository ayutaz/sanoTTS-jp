/* sanoTTS-jp int8 カーネル。詳細は saanotts_int8.h */
#include "saanotts_int8.h"

#include "saanotts_internal.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define I8_NAME_LEN 64

/* W8A8 の ksz>1 用に、重みを [k][cin] へ並べ替えるスタック領域（バイト）。
 * この生徒の ksz>1 層は最大 48·5 = 240 B なので 512 で足りる。
 * 超える形状は並べ替えずに元のレイアウトで回す（結果は同じ、遅いだけ）。 */
#define SAAN_I8_WT_SCRATCH 512

/* --- PIE（ESP32-S3 の 128-bit 整数 SIMD） --------------------------------
 *
 * `ee.vmulas.s8.accx` が **16 レーンの int8 積和を 40-bit アキュムレータに**溜める。
 * これが W8A8 の内積そのものなので、そのまま置き換えられる。
 *
 * ⚠️ **GCC は `-O2` でも PIE へ自動ベクトル化しない**（M-53 で逆アセンブル確認済み。
 * W8A8 の int32 積和ループでも `ee.*` は 0 件）。手で書くしかない。
 *
 * ⚠️ **`ee.vld.128.ip` は 16 バイト境界を要求する。** そのため
 *   - 重みは必ず 16 バイト整列のスクラッチ `wt` に写してから使う
 *   - activation `qx + u*cin` が整列するのは **`cin % 16 == 0` のときだけ**
 * なので、**`cin % 16 != 0` の層はスカラのまま**にしてある。
 * この生徒では `cin ∈ {32, 48, 304}` が整列し、**MAC の 77.1%** を覆う
 * （`cin = 40 / 76 / 12` の層が残り。`reports/d3c_int8.json` の内訳）。
 *
 * 正しさは **QEMU で検証できる**（M-56 / `esp32/pie_probe`）。
 * ⚠️ **速度は測れない** — QEMU はサイクル精度ではない。実機が要る。
 */
#if defined(__XTENSA__) && defined(SAAN_PIE) && SAAN_PIE
#define SAAN_HAVE_PIE 1
#define SAAN_AL16 __attribute__((aligned(16)))
#else
#define SAAN_HAVE_PIE 0
#define SAAN_AL16
#endif

/* `a` と `b` の内積。**`n` は 16 の倍数、両ポインタは 16 バイト境界**であること。
 * 呼び出し側が保証する（`saan_pie_ok()` で判定）。 */
#if SAAN_HAVE_PIE
static int32_t saan_dot_i8_pie(const int8_t *a, const int8_t *b, int n) {
    int32_t out = 0;
    const int8_t *pa = a, *pb = b;
    int k = n >> 4;
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
#endif

/* この (cin) で PIE を使ってよいか。**両方の整列条件をここ 1 箇所で判定する** */
static int saan_pie_ok(int cin) {
#if SAAN_HAVE_PIE
    return cin > 0 && (cin & 15) == 0;
#else
    (void)cin;
    return 0;
#endif
}

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
    SAAN_AL16 int8_t wt[SAAN_I8_WT_SCRATCH];
    /* ⚠️ **PIE を使うときは ksz==1 でも `wt` に写す。** blob 内の `W` の整列は
     * 保証されていないので、`ee.vld.128.ip` に直接渡せない。写す手間は
     * O(cout·cin) で、積和の O(cout·cin·T) に対して無視できる（T ≈ 106）。 */
    const int pie = saan_pie_ok(cin) && cin * ksz <= SAAN_I8_WT_SCRATCH;
    const int transposable = pie || (ksz > 1 && cin * ksz <= SAAN_I8_WT_SCRATCH);

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
#if SAAN_HAVE_PIE
                if (pie) {
                    /* cin は 16 の倍数、`wt` は 16 整列、`qx + u*cin` も 16 整列 */
                    a32 = saan_dot_i8_pie(wt + (size_t)k * cin, qu, cin);
                } else
#endif
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

/* --- fp32 / int8 のディスパッチ（D-3c'-2） --------------------------------
 *
 * ここに置くのは **`saanotts.c` を int8 に依存させない**ため。
 * 一括版もストリーミング版も `saan_conv1d_w` だけを呼べばよく、
 * どちらの経路を通るかは読み込んだブロブの dtype が決める。
 */

saan_wref saan_w(const saan_weights *w, const char *fmt, ...) {
    char buf[I8_NAME_LEN];
    char sbuf[I8_NAME_LEN + 8];
    saan_wref r = {NULL, NULL, NULL};
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);

    uint32_t dt = 0;
    const void *p = saan_tensor(w, buf, &dt, NULL, NULL);
    if (!p) return r;                       /* 名前が無い = 両方 NULL のまま */
    if (dt == 0u) { r.f32 = (const float *)p; return r; }
    if (dt != 1u) return r;                 /* scale(2) を重みとして掴まない */

    snprintf(sbuf, sizeof sbuf, "%s.scale", buf);
    uint32_t sdt = 0;
    const void *sp = saan_tensor(w, sbuf, &sdt, NULL, NULL);
    if (!sp || sdt != 2u) return r;         /* scale が無いなら「引けなかった」 */
    r.q = (const int8_t *)p;
    r.scale = (const float *)sp;
    return r;
}

size_t saan_act_scratch_needed(int cin, int T) {
#if SAAN_INT8_ACT
    /* qx [T][cin] と sx [T] を別々に saan_alloc するので、境界も別々に数える */
    return SAAN_ALIGN16((size_t)cin * (size_t)T)
         + SAAN_ALIGN16(sizeof(float) * (size_t)T);
#else
    (void)cin; (void)T;
    return 0;
#endif
}

saan_status saan_conv1d_w(float *y, const float *x, saan_wref W, const float *b,
                          int cin, int cout, int ksz, int T, saan_arena *a) {
    if (W.f32) {                            /* fp32 ブロブ: 既存カーネルそのもの */
        saan_conv1d(y, x, W.f32, b, cin, cout, ksz, T);
        return SAAN_OK;
    }
    if (!W.q || !W.scale) return SAAN_ERR_MISSING;
#if SAAN_INT8_ACT
    if (!a) return SAAN_ERR_ARENA;
    {
        const size_t mark = a->used;
        int8_t *qx = (int8_t *)saan_alloc(a, (size_t)cin * (size_t)T);
        float *sx = (float *)saan_alloc(a, sizeof(float) * (size_t)T);
        if (!qx || !sx) { a->used = mark; return SAAN_ERR_ARENA; }
        saan_conv1d_i8a(y, x, W.q, W.scale, b, cin, cout, ksz, T, qx, sx);
        a->used = mark;
    }
#else
    (void)a;
    saan_conv1d_i8(y, x, W.q, W.scale, b, cin, cout, ksz, T);
#endif
    return SAAN_OK;
}

saan_status saan_dwconv1d_w(float *y, const float *x, saan_wref W,
                            int ch, int ksz, int T, saan_arena *a) {
    if (W.f32) {
        saan_dwconv1d(y, x, W.f32, ch, ksz, T);
        return SAAN_OK;
    }
    if (!W.q || !W.scale) return SAAN_ERR_MISSING;
#if SAAN_INT8_ACT
    if (!a) return SAAN_ERR_ARENA;
    {
        const size_t mark = a->used;
        int8_t *qx = (int8_t *)saan_alloc(a, (size_t)ch * (size_t)T);
        float *sx = (float *)saan_alloc(a, sizeof(float) * (size_t)T);
        if (!qx || !sx) { a->used = mark; return SAAN_ERR_ARENA; }
        saan_dwconv1d_i8a(y, x, W.q, W.scale, ch, ksz, T, qx, sx);
        a->used = mark;
    }
#else
    (void)a;
    saan_dwconv1d_i8(y, x, W.q, W.scale, ch, ksz, T);
#endif
    return SAAN_OK;
}
