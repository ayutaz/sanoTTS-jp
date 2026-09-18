/* 現状の arena ピークの内訳（2 フェーズ）。saanotts_stream.c を取り込むので
 * 定数・struct サイズは本番と同一。検算: 各フェーズの和が
 * saan_stream_arena_peak(n) の両辺と bit 一致すること。 */
#include "saanotts_stream.c"
#include <stdio.h>
#include <stdlib.h>

static size_t g_sum;
static void row(const char *name, const char *shape, size_t b) {
    g_sum += b; printf("| `%s` | %s | %8zu |\n", name, shape, b);
}

int main(int argc, char **argv) {
    int n = (argc > 1) ? atoi(argv[1]) : 350;
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;
    const int W_AC = 2 * 4 + CH;
    const size_t full_n = (size_t)AC_W * (W_AC - 4) > (size_t)DEC_W * CH
                        ? (size_t)AC_W * (W_AC - 4) : (size_t)DEC_W * CH;

    printf("== ビルド ==\n");
    printf("SAAN_INT8_ACT     = %d\n",
#ifdef SAAN_INT8_ACT
           SAAN_INT8_ACT
#else
           0
#endif
    );
    printf("SAAN_CHUNK(CH)    = %d\n", CH);
    printf("SAAN_MEM_HEAD_PF  = %d (HEAD_COLS=%d)\n", SAAN_MEM_HEAD_PF, HEAD_COLS);
    printf("SAAN_MEM_PIPE_HALO= %d\n", SAAN_MEM_PIPE_HALO);
    printf("SAAN_DUR_W        = %d\n", SAAN_DUR_W);
    printf("n_ids             = %d\n\n", n);

    printf("== フェーズ 1: duration（init の途中。ここが今のピーク） ==\n");
    printf("| 確保 | 形 | B |\n|---|---|---:|\n");
    g_sum = 0;
    row("log_d", "[n] f32", SAAN_ALIGN16(sizeof(float) * (size_t)n));
    row("d_hat", "[n] i32", SAAN_ALIGN16(sizeof(int32_t) * (size_t)n));
    row("dur h/t1/t2", "3 x [32][n] f32",
        SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DUR_W * (size_t)n) * 3);
    row("act scratch", "align16(align16(32)*n)+align16(4n)",
        saan_act_scratch_needed(SAAN_DUR_W, n));
    printf("| **合計** | | **%zu** |\n", g_sum);
    size_t p1 = g_sum;

    printf("\n== フェーズ 2: streaming（init 完了後 = arena_used） ==\n");
    printf("| 確保 | 形 | B |\n|---|---|---:|\n");
    g_sum = 0;
    row("log_d", "[n] f32", SAAN_ALIGN16(sizeof(float) * (size_t)n));
    row("d_hat", "[n] i32", SAAN_ALIGN16(sizeof(int32_t) * (size_t)n));
    row("impl", "struct", SAAN_ALIGN16(sizeof(struct saan_stream_impl)));
    row("ac[0..4]", "5 x halo", SAAN_ALIGN16(sizeof(float) * PIPE_N(AC_W, 4)) * 5);
    row("dinp", "halo", SAAN_ALIGN16(sizeof(float) * PIPE_N(CD, 1)));
    row("dblk[0..4]", "5 x halo", SAAN_ALIGN16(sizeof(float) * PIPE_N(DEC_W, 3)) * 5);
#if SAAN_MEM_PIPE_HALO
    row("pwnd", "共用窓", SAAN_ALIGN16(sizeof(float) * PIPE_WND_N));
#endif
    row("cring", "[40][CRING_W] f32", SAAN_ALIGN16(sizeof(float) * CD * CRING_W));
    row("w_full", "[full_n] f32", SAAN_ALIGN16(sizeof(float) * full_n));
    row("w_c", "[40][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)CD * CH));
    row("w_ch,w_ch2", "2 x [76][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)maxC * CH) * 2);
    row("w_e", "[304][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)E * CH));
    row("w_r", "[12][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_R * CH));
    row("w_g", "[76][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)DEC_W * CH));
    row("o1539", "[1539][HEAD_COLS]", SAAN_ALIGN16(sizeof(float) * 1539 * HEAD_COLS));
    row("hr", "[48][CH]", SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_HEAD * CH));
    row("ola,olw", "2 x [1024+512]",
        SAAN_ALIGN16(sizeof(float) * (size_t)(SAAN_NFFT + 2 * SAAN_HOP)) * 2);
    row("win", "[1024] f32", SAAN_ALIGN16(sizeof(float) * SAAN_NFFT));
    row("obuf", "[OBUF_HOPS][256]",
        SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_OBUF_HOPS * SAAN_HOP));
    row("tok[0..2]", "3 x halo", SAAN_ALIGN16(sizeof(float) * PIPE_N(AC_W, TOK_PAD)) * 3);
    row("tok_ring", "[TOK_G][48][TOK_K]",
        SAAN_ALIGN16(sizeof(float) * (size_t)TOK_G * AC_W * TOK_K));
    printf("| **合計** | | **%zu** |\n", g_sum);
    size_t p2 = g_sum;

    printf("\n検算: phase1 %zu / phase2 %zu / used %zu / peak %zu -> %s\n",
           p1, p2, saan_stream_arena_used(n), saan_stream_arena_peak(n),
           (p2 == saan_stream_arena_used(n)
            && (p1 > p2 ? p1 : p2) == saan_stream_arena_peak(n)) ? "OK" : "NG");
    printf("\nピークは phase%d。phase1 の占有率 = %.1f%%\n",
           p1 > p2 ? 1 : 2, 100.0 * p1 / (double)(p1 > p2 ? p1 : p2));
    return 0;
}
