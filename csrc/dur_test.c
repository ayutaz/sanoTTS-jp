/* MEM-7 のゲート — **duration net の窓分割が正しいか**
 *
 *   make -C csrc dur
 *
 * **なぜ要るか。** MEM-7（[M-143](../docs/measurements.md#m-143) / [M-144](../docs/measurements.md#m-144)）は
 * `saan_run_duration` を 3×[32][n_ids] の一括確保から窓分割に変えた。350 ids で
 * **147,008 B → 66,656 B** で、これが arena の下限を 149,824 → 116,592 B に下げている。
 *
 * ⚠️ **PCM の checksum では守れない。** `log_d` は exp → round → clip[1,80] を通るので、
 *    窓の取り方を間違えても **`d_hat` が変わらず PCM も変わらない文が多い**
 *    （下の P1 = ハロー 11: 350 ids で違う列 20/350 / max|Δ| 0.005）。
 *    **`log_d` を memcmp すること。**
 *
 * ⚠️⚠️ **テストの中に一括版の写しを置いてはいけない**（[C-103](../docs/decisions.md#c-103) で実際に踏んだ）。
 *    `h[i] += gm[0] * t2[i]` を**コンパイラが FMA に契約するかは翻訳単位で違う**ので、
 *    写しと本番が **1 ulp** ずれ、「窓分割で値が変わった」と読める結果が出る。
 *    **同じ関数（`saan_run_duration_ex`）を違う引数で呼んで比べる。**
 *
 * ゲート:
 *   G-DUR1  `K` を 1 / 8 / 13 / 32 / 128 / 4096 と振って `log_d` が全部 bit 一致
 *           （**K ≥ n_ids なら 1 窓 = 一括版と同じ形**なので、これが「窓分割しても同じ」の中身）
 *   G-DUR2  陽性対照 3 本が**落ちる**（ハロー−1 / c1 のゼロクリア無し / 残差後のゼロクリア無し）
 *   G-DUR3  本番の `saan_run_duration` が `_ex` の既定引数と bit 一致（既定値がずれていないか）
 *   G-DUR4  `saan_stream_arena_peak(n)` が実測の `a.peak` と一致（C-100 / C-102）
 *   G-DUR5  ⚠️ **ハローが受容野そのものであること**を、`halo` を 12 より**増やしても**
 *           値が変わらないことで確かめる（12 が足りているなら 13 も同じ値になる）
 *   G-DUR6  **D-029 の予算**（ピーク RAM < 200 KB）。`peak_used + FFT stack 4,224 B`。
 *           ⚠️ **`stream_test` の G1 から移した**（[C-106](../docs/decisions.md#c-106)）。
 *           あちらは `ids_heldout.bin`（第三者コーパス）が要って**手元も CI も回らない**ので、
 *           **`--g1-kb` の上書きが 2 回続けて間違っていても誰も踏まなかった。**
 *           G1 自身は 350 ids を巡回させるだけなのでコーパスは要らない = ここで回せる。
 */
#include "saanotts.h"
#include "saanotts_internal.h"
#include "saanotts_stream.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(1);
    }
    fclose(f); *size = (size_t)n; return b;
}

static unsigned char g_buf[16u << 20];
static saan_weights g_W, g_G;

/* 1 回走らせて log_d を得る。0 で成功 */
static int run(const int32_t *ids, int n, float *out,
               int K, int halo, int z1, int zh, size_t *peak) {
    saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
    memset(out, 0xAA, sizeof(float) * (size_t)n);
    const saan_status s = saan_run_duration_ex(&g_W, &a, ids, n, out, K, halo, z1, zh);
    if (peak) *peak = a.peak;
    return s == SAAN_OK ? 0 : 1;
}

static void diffstat(const float *ref, const float *got, int n, int *nd, double *mx) {
    *nd = 0; *mx = 0.0;
    for (int i = 0; i < n; ++i) {
        if (memcmp(&ref[i], &got[i], sizeof(float)) != 0) ++*nd;
        double d = (double)got[i] - (double)ref[i]; if (d < 0) d = -d;
        if (d > *mx) *mx = d;
    }
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s student.bin golden.bin\n", argv[0]); return 2; }
    size_t wsz, gsz;
    void *wb = slurp(argv[1], &wsz), *gb = slurp(argv[2], &gsz);
    if (saan_weights_open(&g_W, wb, wsz) != SAAN_OK) { fprintf(stderr, "重みが読めない\n"); return 1; }
    if (saan_weights_open(&g_G, gb, gsz) != SAAN_OK) { fprintf(stderr, "golden が読めない\n"); return 1; }
    uint64_t nb = 0;
    const float *idf = (const float *)saan_tensor(&g_G, "in.ids", NULL, NULL, &nb);
    if (!idf) { fprintf(stderr, "golden に in.ids が無い\n"); return 1; }
    const int base_n = (int)(nb / sizeof(float));

    printf("MEM-7 ゲート（既定 SAAN_DUR_K = %d / SAAN_DUR_HALO = %d / W8A8 = %d）\n",
           SAAN_DUR_K, SAAN_DUR_HALO,
#ifdef SAAN_INT8_ACT
           SAAN_INT8_ACT
#else
           0
#endif
    );
    printf("  golden の ids %d 個を巡回させて任意長を作る\n\n", base_n);

    static const int NS[] = { 1, 13, 53, 100, 224, 350 };
    static const int KS[] = { 1, 8, 13, 32, 128, 4096 };
    int bad = 0;

    printf("== G-DUR1: K を振っても log_d が bit 一致するか（基準は K=4096 = 1 窓）==\n");
    printf("| n_ids | 基準 K=4096 のピーク B |");
    for (size_t k = 0; k < sizeof KS / sizeof *KS; ++k) printf(" K=%d |", KS[k]);
    printf("\n|---:|---:|"); for (size_t k = 0; k < sizeof KS / sizeof *KS; ++k) printf("---|");
    printf("\n");
    for (size_t q = 0; q < sizeof NS / sizeof *NS; ++q) {
        const int n = NS[q];
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        float *ref = (float *)malloc(sizeof(float) * (size_t)n);
        float *got = (float *)malloc(sizeof(float) * (size_t)n);
        size_t rpk = 0;
        if (run(ids, n, ref, 4096, SAAN_DUR_HALO, 1, 1, &rpk)) { printf("| %d | 基準が失敗 |\n", n); ++bad; continue; }
        printf("| %d | %zu |", n, rpk);
        for (size_t k = 0; k < sizeof KS / sizeof *KS; ++k) {
            size_t pk = 0;
            const int fail = run(ids, n, got, KS[k], SAAN_DUR_HALO, 1, 1, &pk);
            const int eq = !fail && memcmp(ref, got, sizeof(float) * (size_t)n) == 0;
            if (!eq) ++bad;
            printf(" %s(%zu) |", eq ? "OK" : "**NG**", pk);
        }
        printf("\n");
        free(ids); free(ref); free(got);
    }

    printf("\n== G-DUR2 / G-DUR5: 陽性対照（ハロー不足とゼロクリア欠落は落ち、ハロー過剰は変わらない）==\n");
    {
        const int n = 350;
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        float *ref = (float *)malloc(sizeof(float) * (size_t)n);
        float *got = (float *)malloc(sizeof(float) * (size_t)n);
        if (run(ids, n, ref, 32, SAAN_DUR_HALO, 1, 1, NULL)) { printf("  NG! 基準が失敗\n"); ++bad; }
        struct { const char *name; int halo, z1, zh, want_eq; } P[] = {
            { "P1 ハロー 11（受容野を 1 足りなくする）",   SAAN_DUR_HALO - 1, 1, 1, 0 },
            { "P2 c1 出力のゼロクリアを外す",              SAAN_DUR_HALO,     0, 1, 0 },
            { "P3 残差後の h のゼロクリアを外す",          SAAN_DUR_HALO,     1, 0, 0 },
            { "G-DUR5 ハロー 13（12 で足りているなら同じ）", SAAN_DUR_HALO + 1, 1, 1, 1 },
            { "G-DUR5 ハロー 24（同上）",                   SAAN_DUR_HALO * 2, 1, 1, 1 },
        };
        for (size_t p = 0; p < sizeof P / sizeof *P; ++p) {
            const int fail = run(ids, n, got, 32, P[p].halo, P[p].z1, P[p].zh, NULL);
            const int eq = !fail && memcmp(ref, got, sizeof(float) * (size_t)n) == 0;
            int nd; double mx; diffstat(ref, got, n, &nd, &mx);
            const int pass = (eq == P[p].want_eq);
            if (!pass) ++bad;
            printf("  %s %s → %s（違う列 %d/%d / max|Δ| %.6g）\n", pass ? "OK " : "NG!",
                   P[p].name, eq ? "一致" : "落ちた", nd, n, mx);
        }
        free(ids); free(ref); free(got);
    }

    printf("\n== G-DUR3: 本番の saan_run_duration が _ex の既定引数と一致するか ==\n");
    {
        const int n = 350;
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        float *ref = (float *)malloc(sizeof(float) * (size_t)n);
        float *got = (float *)malloc(sizeof(float) * (size_t)n);
        run(ids, n, ref, SAAN_DUR_K, SAAN_DUR_HALO, 1, 1, NULL);
        saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
        memset(got, 0xAA, sizeof(float) * (size_t)n);
        const int fail = saan_run_duration(&g_W, &a, ids, n, got) != SAAN_OK;
        const int eq = !fail && memcmp(ref, got, sizeof(float) * (size_t)n) == 0;
        if (!eq) ++bad;
        printf("  %s 既定 (K=%d / halo=%d / ゼロクリア両方) と bit 一致\n",
               eq ? "OK " : "NG!", SAAN_DUR_K, SAAN_DUR_HALO);
        free(ids); free(ref); free(got);
    }

    printf("\n== G-DUR4: saan_stream_arena_peak(n) == 実測の a.peak か（C-100 / C-102）==\n");
    for (size_t q = 0; q < sizeof NS / sizeof *NS; ++q) {
        const int n = NS[q];
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
        saan_stream st;
        if (saan_stream_init(&st, &g_W, &a, ids, n, SAAN_S_V) != SAAN_OK) {
            printf("  NG! init が失敗（n=%d）\n", n); ++bad; free(ids); continue;
        }
        static float ch[8 * SAAN_HOP];
        int32_t nh = 0;
        for (;;) { if (saan_stream_pull(&st, ch, &nh) != SAAN_OK || nh == 0) break; }
        const size_t want = saan_stream_arena_peak(n);
        const int eq = (a.peak == want);
        if (!eq) ++bad;
        printf("  %s n_ids %4d : a.peak %zu B / arena_peak() %zu B\n",
               eq ? "OK " : "NG!", n, a.peak, want);
        free(ids);
    }

    printf("\n== G-DUR6: D-029 の予算（ピーク RAM < 200 KB）==\n");
    {
        /* ⚠️ **arena だけでは足りない。** 逆実 FFT は 512 complex を**自動変数（stack）**に
         *    取る。実測 4,224 B で arena の外にあり、ESP32 では SRAM を共有する
         *    （`stream_test.c` の G1 と同じ式。D-3a の照合で指摘された）。 */
        const size_t FFT_STACK = 4224, BUDGET = 200u * 1024u;
        const int n = 350;          /* D-017 の実用最大（max_spec_length=700 相当） */
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
        saan_stream st;
        if (saan_stream_init(&st, &g_W, &a, ids, n, SAAN_S_V) != SAAN_OK) {
            printf("  NG! init が失敗\n"); ++bad;
        } else {
            static float ch[8 * SAAN_HOP];
            int32_t nh = 0;
            for (;;) { if (saan_stream_pull(&st, ch, &nh) != SAAN_OK || nh == 0) break; }
            const size_t total = st.peak_used + FFT_STACK;
            const int ok = total < BUDGET;
            if (!ok) ++bad;
            printf("  %s n_ids %d: peak_used %zu + FFT stack %zu = %zu B（%.1f KB）< %zu B\n",
                   ok ? "OK " : "NG!", n, st.peak_used, FFT_STACK, total,
                   (double)total / 1024.0, BUDGET);
            printf("      ⚠️ **arena サイズと比べてはいけない** — これは SRAM の予算で、\n"
                   "         stack を含む。`--g1-kb` に arena を渡す誤りが C-106 の中身。\n");
        }
        free(ids);
    }

    printf("\n%s\n", bad ? "NG: MEM-7 のゲートに落ちた"
                         : "MEM-7: K に依らず同じ値 / ハローは 12 で必要十分 / 陽性対照は全部落ちた"
                           " / D-029 の予算も満たす");
    return bad ? 1 : 0;
}
