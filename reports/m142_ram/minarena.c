/* 350 ids で init が通る最小の arena を 16 B 刻みで探す（二分探索しない）。 */
#include "saanotts.h"
#include "saanotts_stream.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    (void)argc;
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *blob = malloc((size_t)n);
    if (fread(blob, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);
    saan_weights w;
    if (saan_weights_open(&w, blob, (size_t)n) != SAAN_OK) { puts("open NG"); return 1; }
    int32_t ids[350];
    uint32_t rng = 7u;
    for (int i = 0; i < 350; ++i) { rng = rng*1103515245u+12345u; ids[i] = (int32_t)((rng>>16)%57); }
    void *abuf = malloc(300u*1024u);
    size_t lo = 0;
    for (size_t A = 100u*1024u; A <= 200u*1024u; A += 16) {
        saan_arena a; saan_arena_init(&a, abuf, A);
        saan_stream st;
        if (saan_stream_init(&st, &w, &a, ids, 350, SAAN_S_V) == SAAN_OK) { lo = A; break; }
    }
    printf("350 ids で init が通る最小 arena = %zu B\n", lo);
    printf("saan_stream_arena_peak(350)     = %zu B\n", saan_stream_arena_peak(350));
    printf("saan_stream_arena_used(350)     = %zu B\n", saan_stream_arena_used(350));
    printf("判定: peak >= 実測最小 なら安全側 -> %s\n",
           saan_stream_arena_peak(350) >= lo ? "OK（安全側）" : "⚠️ 過小");
    return 0;
}
