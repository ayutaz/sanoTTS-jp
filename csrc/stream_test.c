/* Phase D-2 の受け入れ条件（D-029）を検証する
 *
 *   G1 ピーク RAM < 200 KB
 *   G2 一括版と **bit 完全一致**（memcmp）。⚠️ SNR では不可
 *   G3 発話長に対して RAM が O(1)
 *   G4 golden test が通り続ける（別バイナリ）
 *
 *   cc -std=c99 -O2 -o stream_test stream_test.c saanotts.c saanotts_stream.c -lm
 *   ./stream_test student.bin golden.bin
 */
#include "saanotts.h"
#include "saanotts_stream.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (fread(b, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "読めない\n"); exit(1); }
    fclose(f); *size = (size_t)n; return b;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s student.bin golden.bin\n", argv[0]); return 2; }
    size_t wsz, gsz;
    void *wbuf = slurp(argv[1], &wsz), *gbuf = slurp(argv[2], &gsz);
    saan_weights W, G;
    if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) return 1;
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) return 1;

    uint32_t dt; uint64_t nb;
    const float *ids_f = (const float *)saan_tensor(&G, "in.ids", &dt, NULL, &nb);
    const int n_ids = (int)(nb / sizeof(float));
    int32_t *ids = malloc(sizeof(int32_t) * (size_t)n_ids);
    for (int i = 0; i < n_ids; ++i) ids[i] = (int32_t)ids_f[i];

    int bad = 0;

    /* --- 一括版（参照） --- */
    size_t need_b = saan_arena_needed(n_ids);
    void *ab = malloc(need_b);
    saan_arena A;
    saan_arena_init(&A, ab, need_b);
    saan_output out;
    saan_status s = saan_synthesize(&W, &A, ids, n_ids, SAAN_S_V, &out);
    if (s != SAAN_OK) { fprintf(stderr, "一括: %s\n", saan_strerror(s)); return 1; }
    const size_t batch_peak = A.used;
    const int T = out.n_frames, S = out.n_samples;
    float *ref = malloc(sizeof(float) * (size_t)S);
    memcpy(ref, out.pcm, sizeof(float) * (size_t)S);
    float *c_batch = malloc(sizeof(float) * (size_t)SAAN_CDIM * T);
    memcpy(c_batch, out.c, sizeof(float) * (size_t)SAAN_CDIM * T);
    printf("一括版      : %d frames / %d sample / arena %.1f KB\n",
           T, S, (double)batch_peak / 1024.0);

    /* --- ストリーミング版 --- */
    size_t need_s = saan_stream_arena_needed(n_ids);
    void *as = malloc(need_s);
    saan_arena B;
    saan_arena_init(&B, as, need_s);
    saan_stream st;
    s = saan_stream_init(&st, &W, &B, ids, n_ids, SAAN_S_V);
    if (s != SAAN_OK) { fprintf(stderr, "stream init: %s\n", saan_strerror(s)); return 1; }

    /* デバッグ: c-line を控えて、どの段までが一致するか切り分ける */
    float *dbg_c = calloc((size_t)SAAN_CDIM * T, sizeof(float));
    st.dbg_c = dbg_c; st.dbg_cap = T;

    float *got = calloc((size_t)S, sizeof(float));
    float chunk[SAAN_CHUNK * SAAN_HOP];
    int32_t n, pos = 0;
    while (1) {
        s = saan_stream_pull(&st, chunk, &n);
        if (s != SAAN_OK) { fprintf(stderr, "pull: %s\n", saan_strerror(s)); return 1; }
        if (n == 0) break;
        const int32_t take = n * SAAN_HOP;
        const int32_t room = S - pos;
        memcpy(got + pos, chunk, sizeof(float) * (size_t)(take < room ? take : room));
        pos += take;
        if (pos >= S) break;
    }
    printf("stream 版   : %d frames 出力 / arena ピーク %.1f KB "
           "(確保 %.1f KB)\n", st.emitted, (double)st.peak_used / 1024.0,
           (double)need_s / 1024.0);

    /* 切り分け: c-line が**一括版と**一致するか。
     * ⚠️ golden（PyTorch）と比べると fp32 の丸め差 7e-07 が乗って紛らわしい。
     * ここで見たいのは「ストリーミングが一括版を再現しているか」 */
    {
        const float *cref = c_batch;
        const size_t n = (size_t)SAAN_CDIM * T;
        int nd = 0, first = -1; double mx = 0;
        for (size_t i = 0; i < n; ++i) {
            if (dbg_c[i] != cref[i]) {
                if (first < 0) first = (int)i;
                ++nd;
                const double d = fabs((double)dbg_c[i] - cref[i]);
                if (d > mx) mx = d;
            }
        }
        printf("  [切り分け] c-line vs 一括版: %s  %d/%zu 不一致",
               nd ? "NG!" : "OK ", nd, n);
        if (nd) printf("（最初 ch=%d frame=%d, max|Δ| %.3e）", first / T, first % T, mx);
        printf("\n");
        if (nd) {
            printf("      frame:      "); for (int f=0; f<8; ++f) printf("%9d", f);
            printf("\n      ref  ch0:   ");
            for (int f=0; f<8; ++f) printf("%9.4f", cref[f]);
            printf("\n      got  ch0:   ");
            for (int f=0; f<8; ++f) printf("%9.4f", dbg_c[f]);
            printf("\n      ref  ch1:   ");
            for (int f=0; f<8; ++f) printf("%9.4f", cref[T+f]);
            printf("\n      got  ch1:   ");
            for (int f=0; f<8; ++f) printf("%9.4f", dbg_c[T+f]);
            printf("\n      末尾 ch0 ref/got: %.4f %.4f  /  %.4f %.4f\n",
                   cref[T-2], dbg_c[T-2], cref[T-1], dbg_c[T-1]);
        }
    }

    /* G1: ピーク < 200 KB。
     * ⚠️ **測るのは「テストに使った 1 文」ではなく実用最大長**（D-017 で
     * `max_spec_length=700` = 8.13 秒に切ると決めている ≒ 350 ids）。
     * 短い文だけで測ると甘い判定になる */
    const int G1_IDS = 350;
    {
        int32_t *idm = malloc(sizeof(int32_t) * G1_IDS);
        for (int i = 0; i < G1_IDS; ++i) idm[i] = ids[i % n_ids];
        size_t nd = saan_stream_arena_needed(G1_IDS);
        void *am = malloc(nd);
        saan_arena D; saan_arena_init(&D, am, nd);
        saan_stream s3;
        if (saan_stream_init(&s3, &W, &D, idm, G1_IDS, SAAN_S_V) != SAAN_OK) return 1;
        float tmp[SAAN_CHUNK * SAAN_HOP];
        int32_t k;
        while (saan_stream_pull(&s3, tmp, &k) == SAAN_OK && k > 0) { }
        /* ⚠️ **arena だけでは足りない。** 逆実 FFT は 512 complex を
         * **自動変数（stack）**に取る。実測 4,224 B（プロローグ 0x60 + 0x1020）で、
         * arena の外にある。ESP32 では SRAM を共有するので**合算して判定する**
         * （D-3a の照合で指摘された。arena だけだと 200 KB を超えていても気づかない） */
        const size_t FFT_STACK = 4224;
        const size_t total = s3.peak_used + FFT_STACK;
        const int g1 = total < 200u * 1024u;
        printf("\n  %s G1 ピーク RAM < 200 KB\n", g1 ? "OK " : "NG!");
        printf("        テスト文  %3d ids / %4d frames : arena %6.1f KB\n",
               n_ids, T, (double)st.peak_used / 1024.0);
        printf("        実用最大  %3d ids / %4d frames : arena %6.1f KB + FFT stack %.1f KB\n",
               G1_IDS, s3.n_frames, (double)s3.peak_used / 1024.0,
               (double)FFT_STACK / 1024.0);
        printf("        合計 %6.1f KB  ← 判定はこちら（SRAM 512 KB の %.0f%%）\n",
               (double)total / 1024.0, (double)total / (512.0 * 1024.0) * 100.0);
        bad += !g1;
        free(am); free(idm);
    }

    /* G2: bit 完全一致 */
    const int same = memcmp(got, ref, sizeof(float) * (size_t)S) == 0;
    printf("  %s G2 一括版と bit 完全一致         ", same ? "OK " : "NG!");
    if (same) {
        printf("%d sample すべて一致\n", S);
    } else {
        int first = -1, ndiff = 0;
        double mx = 0;
        for (int i = 0; i < S; ++i) {
            if (got[i] != ref[i]) {
                if (first < 0) first = i;
                ++ndiff;
                const double d = fabs((double)got[i] - ref[i]);
                if (d > mx) mx = d;
            }
        }
        printf("%d/%d sample が違う（最初 %d = frame %d, max|Δ| %.3e）\n",
               ndiff, S, first, first / SAAN_HOP, mx);
    }
    bad += !same;

    /* G3: 発話長に対して RAM が O(1) */
    printf("\n  G3 発話長に対する RAM:\n");
    size_t prev = 0;
    int g3 = 1;
    for (int rep = 1; rep <= 16; rep *= 2) {
        int m = n_ids * rep;
        int32_t *idm = malloc(sizeof(int32_t) * (size_t)m);
        for (int i = 0; i < m; ++i) idm[i] = ids[i % n_ids];
        size_t nd = saan_stream_arena_needed(m);
        void *am = malloc(nd);
        saan_arena C;
        saan_arena_init(&C, am, nd);
        saan_stream s2;
        if (saan_stream_init(&s2, &W, &C, idm, m, SAAN_S_V) != SAAN_OK) { bad++; break; }
        float tmp[SAAN_CHUNK * SAAN_HOP];
        int32_t k;
        while (saan_stream_pull(&s2, tmp, &k) == SAAN_OK && k > 0) { }
        /* ids に比例して残るのは log_d と d_hat（8 B/id）だけ。
         * それを引いた分が発話長に依存しなければ O(1) */
        const size_t fixed = s2.peak_used - (size_t)m * 8;
        printf("      %5d ids / %5d frames  ピーク %7.1f KB  ids 比例を除く %7.1f KB\n",
               m, s2.n_frames, (double)s2.peak_used / 1024.0, (double)fixed / 1024.0);
        if (prev && (fixed > prev + 2048 || fixed + 2048 < prev)) g3 = 0;
        prev = fixed;
        free(am); free(idm);
    }
    printf("  %s G3 発話長に対して RAM が O(1)（ids 比例分 8 B/id を除いて一定）\n",
           g3 ? "OK " : "NG!");
    bad += !g3;

    printf("\n%s\n", bad ? "受け入れ条件を満たしていない" : "Phase D-2 の受け入れ条件を満たした");
    return bad ? 1 : 0;
}
