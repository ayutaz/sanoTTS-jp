/* arena の内訳を実測する（saanotts_stream.c をそのまま取り込むので、
 * 定数も struct のサイズも本番と同一）。
 * 検算: 各項の和が saan_stream_arena_used(n_ids) と bit 一致すること。 */
#include "saanotts_stream.c"
#include <stdio.h>
#include <stdlib.h>

static size_t g_sum = 0;
static void row(const char *name, const char *shape, size_t bytes) {
    g_sum += bytes;
    printf("| `%s` | %s | %zu |\n", name, shape, bytes);
}

int main(int argc, char **argv) {
    int n_ids = (argc > 1) ? atoi(argv[1]) : 53;
    const int W_AC = 2 * 4 + CH;
    const size_t full_n = (size_t)AC_W * (W_AC - 4) > (size_t)DEC_W * CH
                        ? (size_t)AC_W * (W_AC - 4) : (size_t)DEC_W * CH;
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;

    printf("n_ids = %d / CHUNK = %d\n\n", n_ids, CH);
    printf("| 確保 | 形 | B |\n|---|---|---:|\n");
    row("log_d",    "[n_ids] f32",            SAAN_ALIGN16(sizeof(float) * (size_t)n_ids));
    row("d_hat",    "[n_ids] i32",            SAAN_ALIGN16(sizeof(int32_t) * (size_t)n_ids));
    row("impl",     "struct saan_stream_impl", SAAN_ALIGN16(sizeof(struct saan_stream_impl)));
    row("ac[0..4]", "5 x [48][16] f32",       SAAN_ALIGN16(sizeof(float) * AC_W * (2*4+CH)) * 5);
    row("dinp",     "[40][10] f32",           SAAN_ALIGN16(sizeof(float) * CD * (2*1+CH)));
    row("dblk[0..4]","5 x [76][14] f32",      SAAN_ALIGN16(sizeof(float) * DEC_W * (2*3+CH)) * 5);
    row("cring",    "[40][24] f32",           SAAN_ALIGN16(sizeof(float) * CD * CRING_W));
    row("w_full",   "[608] f32",              SAAN_ALIGN16(sizeof(float) * full_n));
    row("w_c",      "[40][8] f32",            SAAN_ALIGN16(sizeof(float) * (size_t)CD * CH));
    row("w_ch,w_ch2","2 x [76][8] f32",       SAAN_ALIGN16(sizeof(float) * (size_t)maxC * CH) * 2);
    row("w_e",      "[304][8] f32",           SAAN_ALIGN16(sizeof(float) * (size_t)E * CH));
    row("w_r",      "[12][8] f32",            SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_R * CH));
    row("w_g",      "[76][8] f32",            SAAN_ALIGN16(sizeof(float) * (size_t)DEC_W * CH));
    row("o1539",    "[1539][8] f32",          SAAN_ALIGN16(sizeof(float) * 1539 * CH));
    row("hr",       "[48][8] f32",            SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_HEAD * CH));
    row("ola,olw",  "2 x [1024+512] f32",     SAAN_ALIGN16(sizeof(float) * (size_t)(SAAN_NFFT + 2*SAAN_HOP)) * 2);
    row("win",      "[1024] f32",             SAAN_ALIGN16(sizeof(float) * SAAN_NFFT));
    row("obuf",     "[12 hop][256] f32",      SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_OBUF_HOPS * SAAN_HOP));
    row("tok[0..2]","3 x [48][16] f32",       SAAN_ALIGN16(sizeof(float) * AC_W * (2*TOK_PAD+TOK_K)) * 3);
    row("tok_ring", "[2][48][8] f32",         SAAN_ALIGN16(sizeof(float) * (size_t)TOK_G * AC_W * TOK_K));
    printf("| **合計** | | **%zu** |\n\n", g_sum);

    size_t real = saan_stream_arena_used(n_ids);
    printf("saan_stream_arena_used(%d) = %zu\n", n_ids, real);
    printf("検算: %s\n", (real == g_sum) ? "一致" : "⚠️ 食い違い");
    printf("saan_stream_arena_needed(%d) = %zu\n", n_ids, saan_stream_arena_needed(n_ids));
    return real == g_sum ? 0 : 1;
}
