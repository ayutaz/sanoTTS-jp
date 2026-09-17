/* 長さを振った ids を多数流して PCM の checksum を並べる。
 * MEM-5 の唯一のリスクは「群が発話の外か」の判定なので、
 * n_frames mod CH を広く当てることが目的。 */
#include "saanotts.h"
#include "saanotts_stream.h"
#include <stdio.h>
#include <stdlib.h>
#ifndef SAAN_MEM_HEAD_PF
#define SAAN_MEM_HEAD_PF 0
#endif
#define ARENA_N (400 * 1024)

int main(int argc, char **argv) {
    (void)argc;
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *blob = malloc((size_t)n);
    if (fread(blob, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);
    saan_weights w;
    if (saan_weights_open(&w, blob, (size_t)n) != SAAN_OK) { puts("open NG"); return 1; }
    void *abuf = malloc(ARENA_N);
    float *pcm = (float *)malloc(sizeof(float) * SAAN_CHUNK * SAAN_HOP);
    int32_t ids[400];
    uint32_t rng = 12345u;
    uint64_t all = 1469598103934665603ULL;
    int nbad = 0;
    for (int L = 6; L <= 350; L += 7) {
        for (int i = 0; i < L; ++i) {
            rng = rng * 1103515245u + 12345u;
            ids[i] = (int32_t)((rng >> 16) % 57);
        }
        saan_arena a; saan_arena_init(&a, abuf, ARENA_N);
        saan_stream st;
        if (saan_stream_init(&st, &w, &a, ids, L, SAAN_S_V) != SAAN_OK) { ++nbad; continue; }
        uint64_t h = 1469598103934665603ULL; long tot = 0;
        for (;;) {
            int32_t nf = 0;
            if (saan_stream_pull(&st, pcm, &nf) != SAAN_OK) { ++nbad; break; }
            if (nf == 0) break;
            for (int32_t i = 0; i < nf * SAAN_HOP; ++i) {
                float v = pcm[i] * 32767.0f;
                int32_t s = (int32_t)(v < 0 ? v - 0.5f : v + 0.5f);
                if (s > 32767) s = 32767; if (s < -32768) s = -32768;
                uint16_t u = (uint16_t)(int16_t)s;
                h = (h ^ (u & 0xff)) * 1099511628211ULL;
                h = (h ^ (u >> 8)) * 1099511628211ULL;
                ++tot;
            }
        }
        /* 文ごとの checksum と n_frames mod CH を全体ハッシュに畳む */
        uint64_t mix = h ^ ((uint64_t)st.n_frames << 32) ^ (uint64_t)tot;
        for (int b = 0; b < 8; ++b) all = (all ^ ((mix >> (8 * b)) & 0xff)) * 1099511628211ULL;
    }
    printf("HEAD_PF=%d  文 50 本の総合 FNV-1a 0x%016llx  失敗 %d\n",
           SAAN_MEM_HEAD_PF, (unsigned long long)all, nbad);
    return nbad != 0;
}
