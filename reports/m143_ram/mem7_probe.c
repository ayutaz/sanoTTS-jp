/* MEM-7 プロトタイプ — duration 網を窓分割しても log_d が bit 一致するか。
 *
 * ⚠️⚠️ **2026-09-18 以降このプローブは空虚になった**（[M-144](../../docs/measurements.md#m-144)）。
 *    参照に `saan_run_duration` を使っているが、**その関数自身が窓分割になった**ので、
 *    いま走らせると「窓分割 vs 窓分割」を比べて必ず OK が出る。
 *    **MEM-7 の前の commit（79dd7f2）の csrc に対してだけ意味がある。**
 *    いまの不変量を見るのは `make -C csrc dur`（`csrc/dur_test.c`。G-DUR1〜5）。
 *
 * 現状: saan_run_duration は h / t1 / t2 を 3 本 [32][n_ids] で取る。
 *       350 ids で 134,400 B = arena のピークの 89.7%。
 * 主張: 受容野は ±12 トークン（3 ブロック × (k=5 の conv 2 本) = ±4 × 3）なので、
 *       ハロー 12 の窓で回せば中央 K 列は一括版と bit 一致する。
 *
 * ⚠️ 陽性対照を 3 本置く（どれも「落ちること」を要求する）:
 *   P1 ハロー 11  → 受容野の主張が間違っていれば通ってしまう
 *   P2 c1 出力のゼロクリアを外す → bias 由来の非ゼロが発話外に残る（acblk_step が
 *      既に踏んでいる穴。M-?? の「先頭 pad フレームが max|Δ| 0.49 ずれた」）
 *   P3 残差後の h のゼロクリアを外す → LN が発話外の列に bias を書く
 */
#include "saanotts.h"
#include "saanotts_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "読めない\n"); exit(1); }
    fclose(f); *size = (size_t)n; return b;
}

#define WD SAAN_DUR_W

/* 絶対列 [lo, lo+Tw) のうち [0, n) の外をゼロにする */
static void zoutside(float *x, int Tw, int lo, int n) {
    for (int j = 0; j < Tw; ++j) {
        const int t = lo + j;
        if (t >= 0 && t < n) continue;
        for (int c = 0; c < WD; ++c) x[(size_t)c * Tw + j] = 0.0f;
    }
}

/* 窓分割版。halo / z1 / zh は陽性対照のためのつまみ */
static saan_status dur_win(const saan_weights *w, saan_arena *a, const int32_t *ids,
                           int n, float *log_d, int K, int halo, int z1, int zh) {
    const float *emb = saan_tf(w, "duration.emb.weight");
    const saan_wref pw = saan_w(w, "duration.proj.weight");
    const float *pb = saan_tf(w, "duration.proj.bias");
    if (!emb || !SAAN_W_OK(pw) || !pb) return SAAN_ERR_MISSING;

    for (int s = 0; s < n; s += K) {
        const int kk = (n - s < K) ? (n - s) : K;    /* この窓で確定させる列数 */
        const int lo = s - halo;
        const int Tw = kk + 2 * halo;
        const size_t mark = a->used;
        float *h  = (float *)saan_alloc(a, sizeof(float) * (size_t)WD * Tw);
        float *t1 = (float *)saan_alloc(a, sizeof(float) * (size_t)WD * Tw);
        if (!h || !t1) return SAAN_ERR_ARENA;

        for (int j = 0; j < Tw; ++j) {
            const int t = lo + j;
            if (t < 0 || t >= n) { for (int c = 0; c < WD; ++c) h[(size_t)c * Tw + j] = 0.0f; continue; }
            if (ids[t] < 0 || ids[t] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
            for (int c = 0; c < WD; ++c) h[(size_t)c * Tw + j] = emb[(size_t)ids[t] * WD + c];
        }

        for (int bi = 0; bi < 3; ++bi) {
            const saan_wref c1w = saan_w(w, "duration.blocks.%d.c1.weight", bi);
            const float *c1b = saan_tf(w, "duration.blocks.%d.c1.bias", bi);
            const saan_wref c2w = saan_w(w, "duration.blocks.%d.c2.weight", bi);
            const float *c2b = saan_tf(w, "duration.blocks.%d.c2.bias", bi);
            const float *ng = saan_tf(w, "duration.blocks.%d.norm.weight", bi);
            const float *nb = saan_tf(w, "duration.blocks.%d.norm.bias", bi);
            const float *gm = saan_tf(w, "duration.blocks.%d.gamma", bi);
            if (!SAAN_W_OK(c1w) || !SAAN_W_OK(c2w) || !ng || !gm) return SAAN_ERR_MISSING;

            SAAN_TRY(saan_conv1d_w(t1, h, c1w, c1b, WD, WD, 5, Tw, a));
            saan_relu(t1, (size_t)WD * Tw);
            if (z1) zoutside(t1, Tw, lo, n);
            float *t2 = (float *)saan_alloc(a, sizeof(float) * (size_t)WD * Tw);
            if (!t2) return SAAN_ERR_ARENA;
            SAAN_TRY(saan_conv1d_w(t2, t1, c2w, c2b, WD, WD, 5, Tw, a));
            saan_layernorm_c(t2, ng, nb, WD, Tw);
            for (size_t i = 0; i < (size_t)WD * Tw; ++i) h[i] += gm[0] * t2[i];
            if (zh) zoutside(h, Tw, lo, n);
            a->used -= SAAN_ALIGN16(sizeof(float) * (size_t)WD * Tw);
        }
        /* proj は 1x1。中央 [halo, halo+kk) だけ出す（出力は圧縮 [1][kk]） */
        SAAN_TRY(saan_conv1d_wr(log_d + s, h, pw, pb, WD, 1, 1, Tw, halo, halo + kk, a));
        a->used = mark;
    }
    return SAAN_OK;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s student.bin golden.bin [n_ids]\n", argv[0]); return 2; }
    size_t wsz, gsz;
    void *wbuf = slurp(argv[1], &wsz), *gbuf = slurp(argv[2], &gsz);
    static saan_weights W, G;
    if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) { fprintf(stderr, "重みが読めない\n"); return 1; }
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) { fprintf(stderr, "golden が読めない\n"); return 1; }
    uint64_t nb = 0;
    const float *idf = (const float *)saan_tensor(&G, "in.ids", NULL, NULL, &nb);
    if (!idf) { fprintf(stderr, "golden に in.ids が無い\n"); return 1; }
    const int base_n = (int)(nb / sizeof(float));

    static const int NS[] = { 53, 100, 224, 350 };
    static const int KS[] = { 8, 16, 32, 64, 128 };
    int bad = 0;

    printf("MEM-7 プロトタイプ（W8A8=%d / SAAN_DUR_W=%d / golden の ids %d 個を巡回）\n\n",
#ifdef SAAN_INT8_ACT
           SAAN_INT8_ACT,
#else
           0,
#endif
           WD, base_n);

    for (size_t ni = 0; ni < sizeof NS / sizeof *NS; ++ni) {
        const int n = NS[ni];
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        float *ref = (float *)malloc(sizeof(float) * (size_t)n);
        float *got = (float *)malloc(sizeof(float) * (size_t)n);

        /* 一括版（現状） */
        static unsigned char buf[4u << 20];
        saan_arena a; saan_arena_init(&a, buf, sizeof buf);
        size_t mark = a.used;
        if (saan_run_duration(&W, &a, ids, n, ref) != SAAN_OK) { printf("参照が失敗\n"); return 1; }
        const size_t ref_peak = a.peak;
        a.used = mark;

        printf("n_ids = %d : 一括版のピーク %zu B\n", n, ref_peak);
        printf("| K | 窓幅 Tw | 窓の数 | ピーク B | 一括比 | 再計算比 | log_d bit 一致 |\n");
        printf("|---:|---:|---:|---:|---:|---:|---|\n");
        for (size_t ki = 0; ki < sizeof KS / sizeof *KS; ++ki) {
            const int K = KS[ki];
            saan_arena b; saan_arena_init(&b, buf, sizeof buf);
            memset(got, 0xAA, sizeof(float) * (size_t)n);
            const saan_status s = dur_win(&W, &b, ids, n, got, K, 12, 1, 1);
            const int eq = (s == SAAN_OK) &&
                           memcmp(ref, got, sizeof(float) * (size_t)n) == 0;
            if (!eq) ++bad;
            /* 再計算比 = Σ Tw / n */
            long cols = 0;
            for (int s2 = 0; s2 < n; s2 += K) {
                const int kk = (n - s2 < K) ? (n - s2) : K;
                cols += kk + 24;
            }
            printf("| %d | %d | %d | %zu | %.3f | %.2fx | %s |\n", K, K + 24,
                   (n + K - 1) / K, b.peak, (double)b.peak / (double)ref_peak,
                   (double)cols / (double)n, eq ? "**OK**" : "NG");
        }
        printf("\n");

        /* 陽性対照（どれも NG になること） */
        struct { const char *name; int halo, z1, zh; } P[] = {
            { "P1 ハロー 11（受容野を 1 足りなくする）", 11, 1, 1 },
            { "P2 c1 出力のゼロクリアを外す",           12, 0, 1 },
            { "P3 残差後の h のゼロクリアを外す",       12, 1, 0 },
        };
        for (size_t p = 0; p < sizeof P / sizeof *P; ++p) {
            saan_arena b; saan_arena_init(&b, buf, sizeof buf);
            memset(got, 0xAA, sizeof(float) * (size_t)n);
            const saan_status s = dur_win(&W, &b, ids, n, got, 32, P[p].halo, P[p].z1, P[p].zh);
            const int eq = (s == SAAN_OK) && memcmp(ref, got, sizeof(float) * (size_t)n) == 0;
            /* 差の大きさも出す（「ほぼ同じ」で見逃さないため） */
            double mx = 0.0; int nd = 0;
            for (int i = 0; i < n; ++i) { double d = (double)got[i] - (double)ref[i];
                if (d < 0) d = -d; if (d > mx) mx = d; if (got[i] != ref[i]) ++nd; }
            printf("  陽性対照 %s -> %s（違う列 %d/%d / max|Δ| %.6g）\n", P[p].name,
                   eq ? "⚠️ 通ってしまった（対照が効いていない）" : "OK（落ちた）", nd, n, mx);
            if (eq) ++bad;
        }
        printf("\n");
        free(ids); free(ref); free(got);
    }
    printf("%s\n", bad ? "NG" : "全部 OK");
    return bad ? 1 : 0;
}
