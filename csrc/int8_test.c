/* int8 カーネルの検証（Phase D-3c）
 *
 *   1. 合成入力で fp32 カーネル（saan_conv1d / saan_dwconv1d）との SNR
 *   2. **実際の重み**で fp32 ブロブ（student.bin）と int8 ブロブ（student_i8.bin）を
 *      同じ層に通して SNR
 *   3. 速度（ns/call, ns/MAC）。⚠️ **手元は x86/arm の fp32 SIMD が効くので
 *      int8 が速いとは限らない。ESP32-S3 では別**（測れないので測らない）
 *
 *   cc -std=c99 -O2 -Wall -Wextra -o int8_test int8_test.c saanotts.c saanotts_int8.c -lm
 *   ./int8_test student.bin student_i8.bin golden.bin ../reports/d3c_int8.json
 */
#define _POSIX_C_SOURCE 200809L

#include "saanotts.h"
#include "saanotts_internal.h"
#include "saanotts_int8.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define HDR_ENTRY (64 + 4 + 4 + 16 + 8 + 8)

/* --- 小物 ---------------------------------------------------------------- */

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "読めない\n"); exit(1); }
    fclose(f);
    *size = (size_t)n;
    return b;
}

static uint32_t rng_s = 0x13572468u;
static uint32_t rng_u32(void) {
    rng_s ^= rng_s << 13; rng_s ^= rng_s >> 17; rng_s ^= rng_s << 5;
    return rng_s;
}
static double rng_u01(void) { return ((double)(rng_u32() >> 8) + 0.5) / 16777216.0; }
static float rng_normal(void) {
    const double u = rng_u01(), v = rng_u01();
    return (float)(sqrt(-2.0 * log(u)) * cos(6.283185307179586 * v));
}

/* SNR(dB) = 10 log10( Σ ref² / Σ (got-ref)² ) */
static double snr_db(const float *got, const float *ref, size_t n) {
    double sig = 0.0, err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const double r = ref[i], e = (double)got[i] - r;
        sig += r * r; err += e * e;
    }
    if (err == 0.0) return INFINITY;
    if (sig == 0.0) return -INFINITY;
    return 10.0 * log10(sig / err);
}

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

static const void *tensor_of(const saan_weights *w, const char *name,
                             uint32_t want_dt, uint32_t dims[4]) {
    uint32_t dt = 0;
    const void *p = saan_tensor(w, name, &dt, dims, NULL);
    return (p && dt == want_dt) ? p : NULL;
}

/* JSON 用のバッファ */
static char jbuf[1 << 18];
static size_t jlen = 0;
static void jp(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    jlen += (size_t)vsnprintf(jbuf + jlen, sizeof jbuf - jlen, fmt, ap);
    va_end(ap);
}
static void jnum(double v) {
    if (isinf(v)) jp("%s", v > 0 ? "\"inf\"" : "\"-inf\"");
    else jp("%.4f", v);
}

/* --- 1. 合成入力でのカーネル検証 ----------------------------------------- */

typedef struct { const char *tag; int cin, cout, ksz, T; } shape_t;

static void synth_conv(const shape_t *s, double out[3]) {
    const int cin = s->cin, cout = s->cout, ksz = s->ksz, T = s->T;
    const size_t nw = (size_t)cout * cin * ksz, ny = (size_t)cout * T;
    float *W = malloc(sizeof(float) * nw), *Wd = malloc(sizeof(float) * nw);
    float *x = malloc(sizeof(float) * (size_t)cin * T);
    float *b = malloc(sizeof(float) * (size_t)cout);
    float *y0 = malloc(sizeof(float) * ny), *y1 = malloc(sizeof(float) * ny);
    float *y2 = malloc(sizeof(float) * ny), *y3 = malloc(sizeof(float) * ny);
    int8_t *q = malloc(nw);
    float *sc = malloc(sizeof(float) * (size_t)cout);
    int8_t *qx = malloc((size_t)cin * T);
    float *sx = malloc(sizeof(float) * (size_t)T);

    const float gain = 1.0f / sqrtf((float)(cin * ksz));
    for (size_t i = 0; i < nw; ++i) W[i] = rng_normal() * gain;
    for (size_t i = 0; i < (size_t)cin * T; ++i) x[i] = rng_normal();
    for (int i = 0; i < cout; ++i) b[i] = 0.1f * rng_normal();

    saan_quantize_w_i8(q, sc, W, cout, cin * ksz);
    for (int o = 0; o < cout; ++o)
        for (int i = 0; i < cin * ksz; ++i)
            Wd[(size_t)o * cin * ksz + i] = (float)q[(size_t)o * cin * ksz + i] * sc[o];

    saan_conv1d(y0, x, W, b, cin, cout, ksz, T);     /* 参照: fp32 の重み */
    saan_conv1d(y1, x, Wd, b, cin, cout, ksz, T);    /* 逆量子化した重みを fp32 で */
    saan_conv1d_i8(y2, x, q, sc, b, cin, cout, ksz, T);
    saan_conv1d_i8a(y3, x, q, sc, b, cin, cout, ksz, T, qx, sx);

    out[0] = snr_db(y2, y0, ny);   /* W8A32 vs fp32 */
    out[1] = snr_db(y2, y1, ny);   /* W8A32 vs 逆量子化 fp32 = カーネル自体の忠実さ */
    out[2] = snr_db(y3, y0, ny);   /* W8A8 vs fp32 */

    free(W); free(Wd); free(x); free(b);
    free(y0); free(y1); free(y2); free(y3);
    free(q); free(sc); free(qx); free(sx);
}

static void synth_dw(int ch, int ksz, int T, double out[3]) {
    const size_t nw = (size_t)ch * ksz, ny = (size_t)ch * T;
    float *W = malloc(sizeof(float) * nw), *Wd = malloc(sizeof(float) * nw);
    float *x = malloc(sizeof(float) * ny);
    float *y0 = malloc(sizeof(float) * ny), *y1 = malloc(sizeof(float) * ny);
    float *y2 = malloc(sizeof(float) * ny), *y3 = malloc(sizeof(float) * ny);
    int8_t *q = malloc(nw);
    float *sc = malloc(sizeof(float) * (size_t)ch);
    int8_t *qx = malloc(ny);
    float *sx = malloc(sizeof(float) * (size_t)T);

    const float gain = 1.0f / sqrtf((float)ksz);
    for (size_t i = 0; i < nw; ++i) W[i] = rng_normal() * gain;
    for (size_t i = 0; i < ny; ++i) x[i] = rng_normal();

    saan_quantize_w_i8(q, sc, W, ch, ksz);
    for (int o = 0; o < ch; ++o)
        for (int k = 0; k < ksz; ++k)
            Wd[(size_t)o * ksz + k] = (float)q[(size_t)o * ksz + k] * sc[o];

    saan_dwconv1d(y0, x, W, ch, ksz, T);
    saan_dwconv1d(y1, x, Wd, ch, ksz, T);
    saan_dwconv1d_i8(y2, x, q, sc, ch, ksz, T);
    saan_dwconv1d_i8a(y3, x, q, sc, ch, ksz, T, qx, sx);

    out[0] = snr_db(y2, y0, ny);
    out[1] = snr_db(y2, y1, ny);
    out[2] = snr_db(y3, y0, ny);

    free(W); free(Wd); free(x); free(y0); free(y1); free(y2); free(y3);
    free(q); free(sc); free(qx); free(sx);
}

/* --- 2. 実重みでの層ごと検証 --------------------------------------------- */

typedef struct { char name[64]; uint32_t dims[4]; } ent_t;

/* i8 ブロブのヘッダを歩いて dtype==1 のテンソルを列挙する
 * （層の一覧をここに書き写さない。書き写すと export 側の変更で黙ってずれる） */
static int list_i8(const uint8_t *blob, uint32_t n_tensors, ent_t *out, int cap) {
    int m = 0;
    for (uint32_t i = 0; i < n_tensors && m < cap; ++i) {
        const uint8_t *e = blob + 16 + (size_t)i * HDR_ENTRY;
        const uint32_t dt = (uint32_t)e[64] | ((uint32_t)e[65] << 8)
                          | ((uint32_t)e[66] << 16) | ((uint32_t)e[67] << 24);
        if (dt != 1u) continue;
        memcpy(out[m].name, e, 64);
        out[m].name[63] = '\0';
        for (int k = 0; k < 4; ++k) {
            const uint8_t *p = e + 64 + 8 + 4 * k;
            out[m].dims[k] = (uint32_t)p[0] | ((uint32_t)p[1] << 8)
                           | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
        }
        ++m;
    }
    return m;
}

/* dtype ごとの payload バイト数（ヘッダを除く実データ）。
 * M-39 の `reports/quant_v2/quant.json` と突き合わせるため */
static void payload_stats(const uint8_t *blob, uint32_t n_tensors, uint64_t by_dt[3]) {
    by_dt[0] = by_dt[1] = by_dt[2] = 0;
    for (uint32_t i = 0; i < n_tensors; ++i) {
        const uint8_t *e = blob + 16 + (size_t)i * HDR_ENTRY;
        uint32_t dt = 0;
        for (int k = 0; k < 4; ++k) dt |= (uint32_t)e[64 + k] << (8 * k);
        uint64_t nb = 0;
        for (int k = 0; k < 8; ++k) nb |= (uint64_t)e[64 + 32 + k] << (8 * k);
        if (dt < 3u) by_dt[dt] += nb;
    }
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s student.bin student_i8.bin golden.bin [out.json]\n", argv[0]);
        return 2;
    }
    size_t fsz, isz, gsz;
    void *fbuf = slurp(argv[1], &fsz), *ibuf = slurp(argv[2], &isz), *gbuf = slurp(argv[3], &gsz);
    saan_weights F, I, G;
    if (saan_weights_open(&F, fbuf, fsz) != SAAN_OK) { fprintf(stderr, "fp32 ブロブが読めない\n"); return 1; }
    if (saan_weights_open(&I, ibuf, isz) != SAAN_OK) { fprintf(stderr, "int8 ブロブが読めない\n"); return 1; }
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) { fprintf(stderr, "golden が読めない\n"); return 1; }

    int bad = 0;
    jp("{\n \"task\": \"D-3c int8 カーネル\",\n");
    jp(" \"blobs\": {\"fp32\": \"%s\", \"int8\": \"%s\", \"int8_bytes\": %zu, \"fp32_bytes\": %zu},\n",
       argv[1], argv[2], isz, fsz);

    /* ---- 1. 合成入力 ---- */
    printf("== 1. 合成入力（ランダム重み・ランダム入力）でのカーネル SNR ==\n");
    printf("%-26s %10s %12s %10s\n", "shape", "W8A32", "W8A32/deq", "W8A8");
    const shape_t shapes[] = {
        {"dur c1 32->32 k5",   32,  32, 5, 106},
        {"ac  c1 48->48 k5",   48,  48, 5, 106},
        {"ac  out 48->40 k1",  48,  40, 1, 106},
        {"dec inp 40->76 k3",  40,  76, 3, 106},
        {"dec pw1 76->304 k1", 76, 304, 1, 106},
        {"dec pw2 304->76 k1",304,  76, 1, 106},
        {"dec cup 12->76 k1",  12,  76, 1, 106},
        {"dec hout 48->1539",  48,1539, 1, 106},
    };
    jp(" \"synthetic\": [\n");
    for (size_t i = 0; i < sizeof shapes / sizeof shapes[0]; ++i) {
        double r[3];
        synth_conv(&shapes[i], r);
        printf("%-26s %9.2f  %11.2f %9.2f\n", shapes[i].tag, r[0], r[1], r[2]);
        jp("  {\"shape\": \"%s\", \"w8a32_vs_fp32_db\": ", shapes[i].tag); jnum(r[0]);
        jp(", \"w8a32_vs_dequant_db\": "); jnum(r[1]);
        jp(", \"w8a8_vs_fp32_db\": "); jnum(r[2]); jp("},\n");
        if (!(r[1] > 100.0)) ++bad;   /* カーネルが逆量子化 fp32 と一致していない */
    }
    {
        double r[3];
        synth_dw(76, 7, 106, r);
        printf("%-26s %9.2f  %11.2f %9.2f\n", "dec dw 76 k7 (depthwise)", r[0], r[1], r[2]);
        jp("  {\"shape\": \"dec dw 76 k7 (depthwise)\", \"w8a32_vs_fp32_db\": "); jnum(r[0]);
        jp(", \"w8a32_vs_dequant_db\": "); jnum(r[1]);
        jp(", \"w8a8_vs_fp32_db\": "); jnum(r[2]); jp("}\n ],\n");
        if (!(r[1] > 100.0)) ++bad;
    }

    /* ---- 2. 実重み ---- */
    printf("\n== 2. 実際の重み（student.bin vs student_i8.bin）· 入力は N(0,1) · T=106 ==\n");
    printf("%-30s %14s %10s %12s %10s\n", "tensor", "shape", "W8A32", "W8A32/deq", "W8A8");
    ent_t ents[256];
    const int n_i8 = list_i8((const uint8_t *)ibuf, I.n_tensors, ents, 256);
    const int T = 106;
    double worst_w8a32 = 1e9, worst_w8a8 = 1e9;
    char worst_name[64] = "", worst_name8[64] = "";
    jp(" \"real_weights\": {\"T\": %d, \"input\": \"N(0,1)\", \"n_layers\": %d, \"layers\": [\n", T, n_i8);
    for (int e = 0; e < n_i8; ++e) {
        const uint32_t *d = ents[e].dims;
        const int cout = (int)d[0], cin = (int)d[1], ksz = (int)d[2];
        const int is_dw = strstr(ents[e].name, ".dw.") != NULL;
        const int in_ch = is_dw ? cout : cin;

        const float *scale = NULL;
        const int8_t *q = saan_ti8(&I, &scale, "%s", ents[e].name);
        const float *W = (const float *)tensor_of(&F, ents[e].name, 0u, NULL);
        if (!q || !scale || !W) { printf("  NG! %s が引けない\n", ents[e].name); ++bad; continue; }

        const size_t inner = is_dw ? (size_t)ksz : (size_t)cin * ksz;
        const size_t nw = (size_t)cout * inner, ny = (size_t)cout * T;
        float *x = malloc(sizeof(float) * (size_t)in_ch * T);
        float *Wd = malloc(sizeof(float) * nw);
        float *y0 = malloc(sizeof(float) * ny), *y1 = malloc(sizeof(float) * ny);
        float *y2 = malloc(sizeof(float) * ny), *y3 = malloc(sizeof(float) * ny);
        int8_t *qx = malloc((size_t)in_ch * T);
        float *sx = malloc(sizeof(float) * (size_t)T);
        for (size_t i = 0; i < (size_t)in_ch * T; ++i) x[i] = rng_normal();
        for (int o = 0; o < cout; ++o)
            for (size_t i = 0; i < inner; ++i)
                Wd[(size_t)o * inner + i] = (float)q[(size_t)o * inner + i] * scale[o];

        if (is_dw) {
            saan_dwconv1d(y0, x, W, cout, ksz, T);
            saan_dwconv1d(y1, x, Wd, cout, ksz, T);
            saan_dwconv1d_i8(y2, x, q, scale, cout, ksz, T);
            saan_dwconv1d_i8a(y3, x, q, scale, cout, ksz, T, qx, sx);
        } else {
            saan_conv1d(y0, x, W, NULL, cin, cout, ksz, T);
            saan_conv1d(y1, x, Wd, NULL, cin, cout, ksz, T);
            saan_conv1d_i8(y2, x, q, scale, NULL, cin, cout, ksz, T);
            saan_conv1d_i8a(y3, x, q, scale, NULL, cin, cout, ksz, T, qx, sx);
        }
        const double a = snr_db(y2, y0, ny), b2 = snr_db(y2, y1, ny), c = snr_db(y3, y0, ny);
        if (a < worst_w8a32) { worst_w8a32 = a; snprintf(worst_name, sizeof worst_name, "%s", ents[e].name); }
        if (c < worst_w8a8) { worst_w8a8 = c; snprintf(worst_name8, sizeof worst_name8, "%s", ents[e].name); }
        if (!(b2 > 100.0)) ++bad;

        char sh[16];
        snprintf(sh, sizeof sh, "%dx%dx%d", cout, cin, ksz);
        printf("%-30s %14s %9.2f  %11.2f %9.2f\n", ents[e].name, sh, a, b2, c);
        jp("  {\"name\": \"%s\", \"shape\": \"%s\", \"depthwise\": %s, \"w8a32_db\": ",
           ents[e].name, sh, is_dw ? "true" : "false"); jnum(a);
        jp(", \"w8a32_vs_dequant_db\": "); jnum(b2);
        jp(", \"w8a8_db\": "); jnum(c); jp("}%s\n", e == n_i8 - 1 ? "" : ",");

        free(x); free(Wd); free(y0); free(y1); free(y2); free(y3); free(qx); free(sx);
    }
    jp(" ],\n");
    jp("  \"worst_w8a32\": {\"name\": \"%s\", \"snr_db\": ", worst_name); jnum(worst_w8a32);
    jp("},\n  \"worst_w8a8\": {\"name\": \"%s\", \"snr_db\": ", worst_name8); jnum(worst_w8a8);
    jp("}\n },\n");
    printf("  最悪 W8A32 %.2f dB (%s) / 最悪 W8A8 %.2f dB (%s)\n",
           worst_w8a32, worst_name, worst_w8a8, worst_name8);

    /* ---- 2c. C の量子化器が Python の exporter と **バイト一致**するか ----
     * 丸めが half-to-even（rintf）でないとここで割れる。roundf に変えると落ちる。 */
    printf("\n== 2c. saan_quantize_w_i8 と scripts/export_c_weights.py の一致 ==\n");
    {
        long q_diff = 0, s_diff = 0, n_val = 0;
        for (int e = 0; e < n_i8; ++e) {
            const uint32_t *d = ents[e].dims;
            const int cout = (int)d[0];
            const int inner = (int)(d[1] ? d[1] : 1) * (int)(d[2] ? d[2] : 1);
            const float *scale = NULL;
            const int8_t *qref = saan_ti8(&I, &scale, "%s", ents[e].name);
            const float *W = (const float *)tensor_of(&F, ents[e].name, 0u, NULL);
            if (!qref || !scale || !W) { ++bad; continue; }
            int8_t *q = malloc((size_t)cout * inner);
            float *sc = malloc(sizeof(float) * (size_t)cout);
            saan_quantize_w_i8(q, sc, W, cout, inner);
            for (int o = 0; o < cout; ++o) if (sc[o] != scale[o]) ++s_diff;
            for (size_t i = 0; i < (size_t)cout * inner; ++i)
                if (q[i] != qref[i]) ++q_diff;
            n_val += (long)cout * inner;
            free(q); free(sc);
        }
        printf("  %s int8 値 %ld/%ld 一致 / scale の不一致 %ld\n",
               (q_diff || s_diff) ? "NG!" : "OK ", n_val - q_diff, n_val, s_diff);
        jp(" \"c_quantizer_matches_python\": {\"n_values\": %ld, \"value_mismatch\": %ld,"
           " \"scale_mismatch\": %ld}, \n", n_val, q_diff, s_diff);
        if (q_diff || s_diff) ++bad;
    }

    /* ---- 2b. 実 activation（golden の c）で decoder.inp ---- */
    printf("\n== 2b. 実 activation（golden out.c, 40x106）で decoder.inp ==\n");
    {
        uint32_t cd[4] = {0, 0, 0, 0};
        const float *c = (const float *)tensor_of(&G, "out.c", 0u, cd);
        const float *scale = NULL;
        const int8_t *q = saan_ti8(&I, &scale, "decoder.inp.weight");
        const float *W = (const float *)tensor_of(&F, "decoder.inp.weight", 0u, NULL);
        const float *b = (const float *)tensor_of(&F, "decoder.inp.bias", 0u, NULL);
        if (!c || !q || !W || !b) { printf("  NG! golden/重みが引けない\n"); ++bad; }
        else {
            const int Tg = (int)cd[1], cin = 40, cout = 76, ksz = 3;
            const size_t ny = (size_t)cout * Tg;
            float *y0 = malloc(sizeof(float) * ny), *y2 = malloc(sizeof(float) * ny);
            float *y3 = malloc(sizeof(float) * ny);
            int8_t *qx = malloc((size_t)cin * Tg);
            float *sx = malloc(sizeof(float) * (size_t)Tg);
            saan_conv1d(y0, c, W, b, cin, cout, ksz, Tg);
            saan_conv1d_i8(y2, c, q, scale, b, cin, cout, ksz, Tg);
            saan_conv1d_i8a(y3, c, q, scale, b, cin, cout, ksz, Tg, qx, sx);
            const double a = snr_db(y2, y0, ny), d2 = snr_db(y3, y0, ny);
            printf("  decoder.inp (T=%d)  W8A32 %.2f dB   W8A8 %.2f dB\n", Tg, a, d2);
            jp(" \"real_activation\": {\"layer\": \"decoder.inp\", \"T\": %d, \"w8a32_db\": ", Tg);
            jnum(a); jp(", \"w8a8_db\": "); jnum(d2); jp("},\n");
            free(y0); free(y2); free(y3); free(qx); free(sx);
        }
    }

    /* ---- 3. 速度 ---- */
    printf("\n== 3. 速度（このホスト。⚠️ ESP32-S3 では別の順位になる） ==\n");
    printf("%-22s %11s %11s %11s %8s %8s %9s %9s %9s\n",
           "shape", "fp32 ns", "W8A32 ns", "W8A8 ns", "A32/f32", "A8/f32",
           "f32 ps/M", "A32 ps/M", "A8 ps/M");
    jp(" \"speed\": {\"host\": \"darwin/arm64 (cc -O2)\", \"note\": \"ESP32-S3 では測れない\", \"rows\": [\n");
    const shape_t bshapes[] = {
        {"ac  c1 48->48 k5",   48,  48, 5, 106},
        {"dec inp 40->76 k3",  40,  76, 3, 106},
        {"dec pw1 76->304 k1", 76, 304, 1, 106},
        {"dec pw2 304->76 k1",304,  76, 1, 106},
        {"dec hout 48->1539",  48,1539, 1, 106},
    };
    for (size_t i = 0; i < sizeof bshapes / sizeof bshapes[0]; ++i) {
        const int cin = bshapes[i].cin, cout = bshapes[i].cout;
        const int ksz = bshapes[i].ksz, Tb = bshapes[i].T;
        const size_t nw = (size_t)cout * cin * ksz;
        float *W = malloc(sizeof(float) * nw);
        float *x = malloc(sizeof(float) * (size_t)cin * Tb);
        float *b = malloc(sizeof(float) * (size_t)cout);
        float *y = malloc(sizeof(float) * (size_t)cout * Tb);
        int8_t *q = malloc(nw);
        float *sc = malloc(sizeof(float) * (size_t)cout);
        int8_t *qx = malloc((size_t)cin * Tb);
        float *sx = malloc(sizeof(float) * (size_t)Tb);
        for (size_t j = 0; j < nw; ++j) W[j] = rng_normal() * 0.1f;
        for (size_t j = 0; j < (size_t)cin * Tb; ++j) x[j] = rng_normal();
        for (int j = 0; j < cout; ++j) b[j] = 0.0f;
        saan_quantize_w_i8(q, sc, W, cout, cin * ksz);

        const int reps = 200;
        double t0 = now_s();
        for (int r = 0; r < reps; ++r) saan_conv1d(y, x, W, b, cin, cout, ksz, Tb);
        const double tf = (now_s() - t0) / reps;
        t0 = now_s();
        for (int r = 0; r < reps; ++r) saan_conv1d_i8(y, x, q, sc, b, cin, cout, ksz, Tb);
        const double t1 = (now_s() - t0) / reps;
        t0 = now_s();
        for (int r = 0; r < reps; ++r) saan_conv1d_i8a(y, x, q, sc, b, cin, cout, ksz, Tb, qx, sx);
        const double t2 = (now_s() - t0) / reps;

        const double macs = (double)nw * (double)Tb;
        printf("%-22s %11.0f %11.0f %11.0f %8.2f %8.2f %9.1f %9.1f %9.1f\n",
               bshapes[i].tag, tf * 1e9, t1 * 1e9, t2 * 1e9, t1 / tf, t2 / tf,
               tf * 1e12 / macs, t1 * 1e12 / macs, t2 * 1e12 / macs);
        jp("  {\"shape\": \"%s\", \"macs\": %zu, \"fp32_ns\": %.0f, \"w8a32_ns\": %.0f,"
           " \"w8a8_ns\": %.0f, \"w8a32_over_fp32\": %.3f, \"w8a8_over_fp32\": %.3f,"
           " \"fp32_ps_per_mac\": %.1f, \"w8a32_ps_per_mac\": %.1f,"
           " \"w8a8_ps_per_mac\": %.1f}%s\n",
           bshapes[i].tag, nw * (size_t)Tb, tf * 1e9, t1 * 1e9, t2 * 1e9, t1 / tf, t2 / tf,
           tf * 1e12 / macs, t1 * 1e12 / macs, t2 * 1e12 / macs,
           i == sizeof bshapes / sizeof bshapes[0] - 1 ? "" : ",");
        free(W); free(x); free(b); free(y); free(q); free(sc); free(qx); free(sx);
    }
    jp(" ]},\n");

    /* ---- 3b. 全 conv 層を 1 フレーム分ずつ回した合計 ----
     * 「どのカーネルを採ると生徒 1 発話の conv 時間がどうなるか」を**推定ではなく実測**する。
     * ⚠️ iSTFT と naive DFT は含まない（D-3c の対象外）。 */
    printf("\n== 3b. 実重みの全 conv 層（T=%d を 1 回ずつ）の合計 ==\n", T);
    {
        double sum_f = 0.0, sum_a32 = 0.0, sum_a8 = 0.0;
        double macs_k1 = 0.0, macs_kn = 0.0;
        const int reps = 50;
        for (int e = 0; e < n_i8; ++e) {
            const uint32_t *d = ents[e].dims;
            const int cout = (int)d[0], cin = (int)d[1], ksz = (int)d[2];
            const int is_dw = strstr(ents[e].name, ".dw.") != NULL;
            const int in_ch = is_dw ? cout : cin;
            const float *scale = NULL;
            const int8_t *q = saan_ti8(&I, &scale, "%s", ents[e].name);
            const float *W = (const float *)tensor_of(&F, ents[e].name, 0u, NULL);
            if (!q || !scale || !W) { ++bad; continue; }
            const size_t inner = is_dw ? (size_t)ksz : (size_t)cin * ksz;
            float *x = malloc(sizeof(float) * (size_t)in_ch * T);
            float *y = malloc(sizeof(float) * (size_t)cout * T);
            int8_t *qx = malloc((size_t)in_ch * T);
            float *sx = malloc(sizeof(float) * (size_t)T);
            for (size_t j = 0; j < (size_t)in_ch * T; ++j) x[j] = rng_normal();

            double t0 = now_s();
            for (int r = 0; r < reps; ++r) {
                if (is_dw) saan_dwconv1d(y, x, W, cout, ksz, T);
                else saan_conv1d(y, x, W, NULL, cin, cout, ksz, T);
            }
            sum_f += (now_s() - t0) / reps;
            t0 = now_s();
            for (int r = 0; r < reps; ++r) {
                if (is_dw) saan_dwconv1d_i8(y, x, q, scale, cout, ksz, T);
                else saan_conv1d_i8(y, x, q, scale, NULL, cin, cout, ksz, T);
            }
            sum_a32 += (now_s() - t0) / reps;
            t0 = now_s();
            for (int r = 0; r < reps; ++r) {
                if (is_dw) saan_dwconv1d_i8a(y, x, q, scale, cout, ksz, T, qx, sx);
                else saan_conv1d_i8a(y, x, q, scale, NULL, cin, cout, ksz, T, qx, sx);
            }
            sum_a8 += (now_s() - t0) / reps;

            const double m = (double)cout * (double)inner * (double)T;
            if (ksz == 1) macs_k1 += m; else macs_kn += m;
            free(x); free(y); free(qx); free(sx);
        }
        const double share_k1 = macs_k1 / (macs_k1 + macs_kn);
        printf("  fp32 %.3f ms / W8A32 %.3f ms (%.2fx) / W8A8 %.3f ms (%.2fx)\n",
               sum_f * 1e3, sum_a32 * 1e3, sum_a32 / sum_f,
               sum_a8 * 1e3, sum_a8 / sum_f);
        printf("  MAC の内訳: ksz=1 が %.1f%% / ksz>1 が %.1f%%\n",
               100.0 * share_k1, 100.0 * (1.0 - share_k1));
        jp(" \"all_conv_layers\": {\"T\": %d, \"reps\": %d, \"fp32_ms\": %.4f,"
           " \"w8a32_ms\": %.4f, \"w8a8_ms\": %.4f, \"w8a32_over_fp32\": %.3f,"
           " \"w8a8_over_fp32\": %.3f, \"mac_share_ksz1\": %.4f,"
           " \"note\": \"iSTFT・埋め込み・LayerNorm は含まない\"},\n",
           T, reps, sum_f * 1e3, sum_a32 * 1e3, sum_a8 * 1e3,
           sum_a32 / sum_f, sum_a8 / sum_f, share_k1);
    }

    /* ---- 4. ブロブのサイズ ---- */
    {
        uint64_t pf[3], pi[3];
        payload_stats((const uint8_t *)fbuf, F.n_tensors, pf);
        payload_stats((const uint8_t *)ibuf, I.n_tensors, pi);
        const uint64_t ptot = pi[0] + pi[1] + pi[2];
        printf("\n== 4. ブロブのサイズ ==\n");
        printf("  fp32  %8zu B (payload %llu B)\n", fsz, (unsigned long long)pf[0]);
        printf("  int8  %8zu B (payload %llu B = int8 %llu + scale %llu + fp32 %llu)\n",
               isz, (unsigned long long)ptot, (unsigned long long)pi[1],
               (unsigned long long)pi[2], (unsigned long long)pi[0]);
        printf("  payload 比 %.2fx / ファイル比 %.2fx\n",
               (double)pf[0] / (double)ptot, (double)fsz / (double)isz);
        jp(" \"blob_size\": {\"fp32_file_bytes\": %zu, \"fp32_payload_bytes\": %llu,"
           " \"int8_file_bytes\": %zu, \"int8_payload_bytes\": %llu,"
           " \"int8_payload_int8\": %llu, \"int8_payload_scale\": %llu,"
           " \"int8_payload_fp32\": %llu, \"payload_ratio\": %.3f,"
           " \"matches_M39_quant_json\": %s},\n",
           fsz, (unsigned long long)pf[0], isz, (unsigned long long)ptot,
           (unsigned long long)pi[1], (unsigned long long)pi[2],
           (unsigned long long)pi[0], (double)pf[0] / (double)ptot,
           ptot == 624692ull ? "true" : "false");
        if (ptot != 624692ull) {
            printf("  ⚠️ M-39 の 624,692 B と一致しない\n");
            ++bad;
        }
    }

    /* ---- 5. 既存の fp32 コアに int8 ブロブを渡したときの挙動 ----
     * **黙って別物を出さないこと**を確かめる（saan_tf は dtype 0 以外を NULL にする）。 */
    {
        int32_t ids[4] = {1, 2, 3, 4};
        const size_t need = saan_arena_needed(4);
        void *ab = malloc(need);
        saan_arena A;
        saan_arena_init(&A, ab, need);
        saan_output o;
        const saan_status st = saan_synthesize(&I, &A, ids, 4, SAAN_S_V, &o);
        const int ok = (st == SAAN_ERR_MISSING);
        printf("\n== 5. fp32 コア + int8 ブロブ ==\n  %s %s（黙って走らない）\n",
               ok ? "OK " : "NG!", saan_strerror(st));
        jp(" \"fp32_core_rejects_int8_blob\": {\"status\": \"%s\", \"is_missing\": %s},\n",
           saan_strerror(st), ok ? "true" : "false");
        if (!ok) ++bad;
        free(ab);
    }

    jp(" \"scheme\": {\n");
    jp("  \"weights\": \"symmetric int8 / per-output-channel。scale[o] = max|W[o]|/127、丸めは rintf (half-to-even, torch.round と同じ。roundf だと 544,292 値中 5 個が exporter と食い違う)\",\n");
    jp("  \"fp32_kept\": \"embedding / pos / LayerNorm / bias / LayerScale\",\n");
    jp("  \"activation_default\": \"W8A32（activation は fp32 のまま）\",\n");
    jp("  \"activation_optional\": \"W8A8（per-frame 対称量子化・タップごとに int32 累積）\",\n");
    jp("  \"act_layout\": \"量子化 activation は転置 [T][C]。fp32 の [C][T] と取り違えると黙って別物になる\"\n");
    jp(" },\n");
    jp(" \"repro\": \"cd csrc && cc -std=c99 -O2 -Wall -Wextra -o int8_test int8_test.c saanotts.c saanotts_int8.c -lm && ./int8_test student.bin student_i8.bin golden.bin ../reports/d3c_int8.json\",\n");
    jp(" \"gate\": {\"kernel_faithful_to_dequant_fp32\": %s, \"n_fail\": %d}\n}\n",
       bad ? "false" : "true", bad);

    printf("\n%s\n", bad ? "NG: 一致しない項目がある"
                         : "OK: int8 カーネルは逆量子化 fp32 と 100 dB 超で一致");

    if (argc >= 5) {
        FILE *f = fopen(argv[4], "wb");
        if (!f) { fprintf(stderr, "書けない: %s\n", argv[4]); return 1; }
        fwrite(jbuf, 1, jlen, f);
        fclose(f);
        printf("JSON: %s (%zu B)\n", argv[4], jlen);
    }
    free(fbuf); free(ibuf); free(gbuf);
    return bad ? 1 : 0;
}
