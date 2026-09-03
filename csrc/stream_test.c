/* Phase D-2 の受け入れ条件（D-029）を検証する
 *
 *   G1 ピーク RAM < 200 KB
 *   G2 一括版と **bit 完全一致**（memcmp）。⚠️ SNR では不可
 *   G3 発話長に対して RAM が O(1)
 *   G4 golden test が通り続ける（別バイナリ）
 *
 *   G2 多文（T2a）: held-out 24 文 × 一括 vs ストリーミング memcmp。
 *      n_frames mod SAAN_CHUNK の残差 0〜7 を**全部**含むこと、pull ごとの ofill の
 *      最大が SAAN_OBUF_HOPS（= 2·CH − (SAAN_LATENCY mod CH) = CH+4）を超えず、
 *      **かつ届く**（= 上限がきつい。届かないならテスト文が最悪ケースを含んでいない）
 *      ことを assert する。⚠️ 1 文だけの G2 は残差 1 通りしか見ない（demo は 106 ≡ 2）。
 *      obuf を (CH+2) に縮めた壊れ方は 1 文の G2 でも QEMU checksum でも捕まらなかった
 *      （審査 2026-09-03）。
 *
 *   cc -std=c99 -O2 -o stream_test stream_test.c saanotts.c saanotts_stream.c \
 *       saanotts_int8.c fft.c -lm
 *   ./stream_test student.bin golden.bin [ids_heldout.bin]
 *   ./stream_test student_i8.bin golden.bin ids_heldout.bin       # W8A32
 *   （-DSAAN_INT8_ACT=1 でビルドしたものに student_i8.bin を渡せば W8A8）
 */
#include "saanotts.h"
#include "saanotts_stream.h"
#include <stdint.h>

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

/* SAAN ブロブから fp32 テンソルを引いて int32 の ids に直す（int8_e2e_test.c と同じ）。
 * 要素数を返す。無ければ -1 */
static int read_ids(const saan_weights *w, const char *name, int32_t *dst, int cap) {
    uint32_t dt = 0;
    uint64_t nb = 0;
    const void *p = saan_tensor(w, name, &dt, NULL, &nb);
    if (!p || dt != 0u) return -1;
    const int n = (int)(nb / sizeof(float));
    if (n > cap) return -1;
    const float *f = (const float *)p;
    for (int i = 0; i < n; ++i) dst[i] = (int32_t)f[i];
    return n;
}

/* 1 文を一括版とストリーミング版で合成して memcmp する（G2 多文の 1 単位）。
 * 戻り値: 0 = bit 一致 / 1 = 不一致 / -1 = どちらかがエラー（stderr に出す）。
 * `*n_frames` に一括版のフレーム数、`*ofill_max` に pull ごとの obuf 充填の最大、
 * `*n_pull` に pull 回数（n_out > 0 のもの）を返す */
static int stream_vs_batch(const saan_weights *W, const int32_t *ids, int n_ids,
                           int *n_frames, int32_t *ofill_max, int *n_pull) {
    const size_t need_b = saan_arena_needed(n_ids);
    void *ab = malloc(need_b);
    saan_arena A;
    saan_arena_init(&A, ab, need_b);
    saan_output out;
    saan_status s = saan_synthesize(W, &A, ids, n_ids, SAAN_S_V, &out);
    if (s != SAAN_OK) { fprintf(stderr, "一括: %s\n", saan_strerror(s)); free(ab); return -1; }
    const int S = out.n_samples;
    *n_frames = out.n_frames;

    const size_t need_s = saan_stream_arena_needed(n_ids);
    void *as = malloc(need_s);
    saan_arena B;
    saan_arena_init(&B, as, need_s);
    saan_stream st;
    s = saan_stream_init(&st, W, &B, ids, n_ids, SAAN_S_V);
    if (s != SAAN_OK) {
        fprintf(stderr, "stream init: %s\n", saan_strerror(s));
        free(ab); free(as); return -1;
    }
    if (st.n_frames != out.n_frames) {
        fprintf(stderr, "フレーム数が違う: 一括 %d / stream %d\n", out.n_frames, st.n_frames);
        free(ab); free(as); return -1;
    }
    float *got = calloc((size_t)S, sizeof(float));
    float chunk[SAAN_CHUNK * SAAN_HOP];
    int32_t n, pos = 0;
    int pulls = 0;
    int rc = 0;
    while (1) {
        s = saan_stream_pull(&st, chunk, &n);
        if (s != SAAN_OK) { fprintf(stderr, "pull: %s\n", saan_strerror(s)); rc = -1; break; }
        if (n == 0) break;
        ++pulls;
        const int32_t take = n * SAAN_HOP;
        const int32_t room = S - pos;
        memcpy(got + pos, chunk, sizeof(float) * (size_t)(take < room ? take : room));
        pos += take;
        if (pos >= S) break;
    }
    *ofill_max = st.ofill_max;
    *n_pull = pulls;
    if (rc == 0) rc = memcmp(got, out.pcm, sizeof(float) * (size_t)S) != 0;
    free(got); free(ab); free(as);
    return rc;
}

/* G2 多文（T2a）。held-out の全文で一括 vs ストリーミング。
 * 残差 0〜(CH−1) のカバーと ofill の上限（超えない **かつ 届く**）を assert する。
 * 戻り値は NG の数 */
static int g2_multi(const saan_weights *W, const char *path) {
    size_t hsz;
    void *hbuf = slurp(path, &hsz);
    saan_weights H;
    if (saan_weights_open(&H, hbuf, hsz) != SAAN_OK) {
        fprintf(stderr, "ids ブロブを開けない: %s\n", path); return 1;
    }
    static int32_t ids[4096];
    int cnt[SAAN_CHUNK], omax[SAAN_CHUNK], ndiff[SAAN_CHUNK];
    for (int r = 0; r < SAAN_CHUNK; ++r) { cnt[r] = 0; omax[r] = 0; ndiff[r] = 0; }
    int n_utt = 0, n_same = 0, n_err = 0;
    int32_t omax_all = 0;
    printf("\n  G2 多文（%s）: 一括 vs ストリーミング memcmp\n", path);
    printf("      %3s %5s %7s  mod%-2d %5s %9s\n",
           "#", "ids", "frames", SAAN_CHUNK, "pull", "ofill最大");
    for (int k = 0; k < 4096; ++k) {
        char nm[32];
        snprintf(nm, sizeof nm, "ids.%03d", k);
        const int n = read_ids(&H, nm, ids, 4096);
        if (n <= 0) break;
        int nf = 0, pulls = 0;
        int32_t om = 0;
        const int rc = stream_vs_batch(W, ids, n, &nf, &om, &pulls);
        const int r = nf % SAAN_CHUNK;
        ++cnt[r];
        if (om > omax[r]) omax[r] = om;
        if (om > omax_all) omax_all = om;
        if (rc == 0) ++n_same; else if (rc < 0) ++n_err; else ++ndiff[r];
        printf("      %3d %5d %7d  %5d %5d %9d %s\n", k, n, nf, r, pulls, om,
               rc == 0 ? "一致" : rc < 0 ? "ERROR" : "NG! 不一致");
        ++n_utt;
    }
    if (n_utt == 0) { fprintf(stderr, "ids ブロブが空: %s\n", path); return 1; }

    /* 残差の補完。⚠️ int8 blob は d̂ が fp32 と違う（int8_e2e の d̂ 一致 < 100%）ので
     * 同じ 24 文でもフレーム数が変わり、残差 1 つが空くことがある（実測: W8A32 で
     * ≡6、W8A8 で ≡4 が空いた。≡4 が空くと上限 12 に届かない）。
     * 空いた残差は**文の prefix**（ids の先頭 n 個。T1 の陽性対照と同じ作り方）で埋める。
     * 一括 vs ストリーミングの一致は入力列の言語的な妥当性に依らない */
    int n_fill = 0, n_fill_fail = 0;
    for (int r = 0; r < SAAN_CHUNK; ++r) {
        if (cnt[r]) continue;
        int found = 0;
        for (int k = 0; k < n_utt && !found; ++k) {
            char nm[32];
            snprintf(nm, sizeof nm, "ids.%03d", k);
            const int n = read_ids(&H, nm, ids, 4096);
            if (n <= 0) break;
            for (int m = n - 1; m >= 8 && m >= n - 48; --m) {
                int nf = 0, pulls = 0;
                int32_t om = 0;
                const int rc = stream_vs_batch(W, ids, m, &nf, &om, &pulls);
                if (nf % SAAN_CHUNK != r) continue;
                ++cnt[r];
                if (om > omax[r]) omax[r] = om;
                if (om > omax_all) omax_all = om;
                if (rc == 0) ++n_same; else if (rc < 0) ++n_err; else ++ndiff[r];
                printf("      %3d %5d %7d  %5d %5d %9d %s  ← 補完: #%d の先頭 %d ids\n",
                       k, m, nf, r, pulls, om,
                       rc == 0 ? "一致" : rc < 0 ? "ERROR" : "NG! 不一致", k, m);
                ++n_utt; ++n_fill; found = 1;
                break;
            }
        }
        if (!found) ++n_fill_fail;
    }
    free(hbuf);

    int bad = 0;
    printf("      残差ごと（n_frames mod %d）:\n", SAAN_CHUNK);
    printf("        %4s %4s %9s %6s\n", "mod", "文数", "ofill最大", "不一致");
    int uncovered = 0;
    for (int r = 0; r < SAAN_CHUNK; ++r) {
        printf("        %4d %4d %9d %6d%s\n", r, cnt[r], omax[r], ndiff[r],
               cnt[r] == 0 ? "  ← 覆われていない" : "");
        if (cnt[r] == 0) ++uncovered;
    }
    const int all_same = (n_same == n_utt);
    printf("  %s G2 多文: 一括版と bit 完全一致 %d/%d 文（エラー %d。うち残差の補完 %d 件）\n",
           all_same ? "OK " : "NG!", n_same, n_utt, n_err, n_fill);
    bad += !all_same;
    printf("  %s G2 多文: 残差 0〜%d を全部含む（覆われていない残差 %d、補完できなかった %d）\n",
           uncovered == 0 ? "OK " : "NG!", SAAN_CHUNK - 1, uncovered, n_fill_fail);
    bad += (uncovered != 0);
    /* ofill の上限。⚠️ 「超えない」だけでは空虚に通る（buffer を余分に取れば必ず通る）。
     * **上限にちょうど届く**ことも要求して、テスト文が最悪ケース（CH=8 では
     * n_frames ≡ 4 (mod 8)）を含んでいることを同時に保証する */
    const int within = omax_all <= SAAN_OBUF_HOPS;
    const int tight  = omax_all == SAAN_OBUF_HOPS;
    printf("  %s G2 多文: pull ごとの ofill 最大 %d ≤ SAAN_OBUF_HOPS %d"
           "（= 2·CH − (SAAN_LATENCY mod CH) = %d − %d）\n",
           within ? "OK " : "NG!", omax_all, SAAN_OBUF_HOPS,
           2 * SAAN_CHUNK, SAAN_LATENCY % SAAN_CHUNK);
    printf("  %s G2 多文: 上限に届いている（最大 %d == %d。届かないなら最悪ケースを含んでいない）\n",
           tight ? "OK " : "NG!", omax_all, SAAN_OBUF_HOPS);
    bad += !within;
    bad += !tight;
    return bad;
}

int main(int argc, char **argv) {
    /* --g1-kb N: G1 の上限（既定 200 = D-029）。⚠️ **W8A8（-DSAAN_INT8_ACT=1）は 200 KB を
     * 超えることが分かっている**（M-55: conv 1 本ぶんの activation 作業領域）。その
     * レーンは Makefile が実機の静的 arena（esp32/main/main.c の SAAN_ARENA_BYTES = 208 KB）
     * を渡す。既定値を動かさないのは、W8A32 / fp32 の 200 KB を黙って緩めないため */
    int g1_kb = 200;
    for (int i = 1; i + 1 < argc; ++i) {
        if (strcmp(argv[i], "--g1-kb") == 0) {
            g1_kb = atoi(argv[i + 1]);
            for (int j = i; j + 2 < argc; ++j) argv[j] = argv[j + 2];
            argc -= 2; --i;
        }
    }
    if (argc < 3) {
        fprintf(stderr, "usage: %s [--g1-kb N] student.bin golden.bin [ids_heldout.bin]\n",
                argv[0]);
        return 2;
    }
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
        const int g1 = total < (size_t)g1_kb * 1024u;
        printf("\n  %s G1 ピーク RAM < %d KB%s\n", g1 ? "OK " : "NG!", g1_kb,
               g1_kb != 200 ? "  ⚠️ --g1-kb で D-029 の 200 KB から変えている" : "");
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
    printf("      （1 文: %d frames ≡ %d mod %d、pull ごとの ofill 最大 %d）\n",
           T, T % SAAN_CHUNK, SAAN_CHUNK, st.ofill_max);

    /* G2 多文（T2a）: held-out 24 文。⚠️ 省略可だが、省くと残差 1 通りしか見ない */
    if (argc >= 4) {
        bad += g2_multi(&W, argv[3]);
    } else {
        printf("\n  ⚠️ G2 多文は走らせていない（第 3 引数に ids_heldout.bin を渡す）\n");
    }

    /* G3: 発話長に対して RAM が O(1)
     *
     * ⚠️ **測るのは「パイプライン定常部」**（init 完了時点の arena 使用量）。
     * `peak_used` は arena の高水位なので、`saan_stream_init` の冒頭で
     * `saan_run_duration` が確保してすぐ返す **O(n_ids) の一時領域**
     * （h/t1/t2 で 3 × 32 × 4 = 384 B/id）を含む。この一時領域は
     * `saan_stream_arena_needed` が最初から数えているので**確保漏れではない**が、
     * 「RAM が O(1)」という主張の対象でもない（duration は発話全体を一度に見る段）。
     * 両方を並べて出す。 */
    printf("\n  G3 発話長に対する RAM:\n");
    printf("      %5s %7s  %9s  %9s  %9s\n",
           "ids", "frames", "定常部", "-8B/id", "高水位");
    size_t prev = 0;
    int g3 = 1;
    int cross = 0;
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
        const size_t pipe = C.used;      /* パイプライン定常部（duration の一時は返済済み） */
        float tmp[SAAN_CHUNK * SAAN_HOP];
        int32_t k;
        while (saan_stream_pull(&s2, tmp, &k) == SAAN_OK && k > 0) { }
        /* ids に比例して残るのは log_d と d_hat（8 B/id）だけ。
         * それを引いた分が発話長に依存しなければ O(1) */
        const size_t fixed = pipe - (size_t)m * 8;
        printf("      %5d %7d  %6.1f KB  %6.1f KB  %6.1f KB%s\n",
               m, s2.n_frames, (double)pipe / 1024.0, (double)fixed / 1024.0,
               (double)s2.peak_used / 1024.0,
               s2.peak_used > pipe ? "  ← duration の一時が上回る" : "");
        if (s2.peak_used > pipe) cross = 1;
        if (prev && (fixed > prev + 2048 || fixed + 2048 < prev)) g3 = 0;
        prev = fixed;
        free(am); free(idm);
    }
    printf("  %s G3 パイプライン定常部が発話長に対して O(1)"
           "（ids 比例分 8 B/id を除いて一定）\n", g3 ? "OK " : "NG!");
    if (cross)
        printf("      ⚠️ 高水位は duration の一時領域 384 B/id で O(n_ids)。"
               "D-017 の実用最大 350 ids では定常部が上回るので G1 は影響を受けない\n");
    bad += !g3;

    printf("\n%s\n", bad ? "受け入れ条件を満たしていない" : "Phase D-2 の受け入れ条件を満たした");
    return bad ? 1 : 0;
}
