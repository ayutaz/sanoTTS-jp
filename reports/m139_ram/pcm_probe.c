/* 既定と -DSAAN_MEM_HEAD_PF=1 で PCM が bit 一致するかを見る。
 * 実機の firmware と同じ経路（stream init → pull ループ → int16 化 → FNV-1a）。 */
#include "saanotts.h"
#include "saanotts_stream.h"
#include "../../../../../../../Users/s19447/Documents/sanoTTS-jp/esp32/main/demo_ids.h"
#include <stdio.h>
#ifndef SAAN_MEM_HEAD_PF
#define SAAN_MEM_HEAD_PF 0
#endif
#include <stdlib.h>
#include <string.h>

#define ARENA_N (256 * 1024)

int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("blob"); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *blob = malloc((size_t)n);
    if (fread(blob, 1, (size_t)n, f) != (size_t)n) return 1;
    fclose(f);

    saan_weights w;
    if (saan_weights_open(&w, blob, (size_t)n) != SAAN_OK) { puts("open NG"); return 1; }

    void *abuf = malloc(ARENA_N);
    saan_arena a; saan_arena_init(&a, abuf, ARENA_N);
    saan_stream st;
    if (saan_stream_init(&st, &w, &a, kSaanDemoIds, SAAN_DEMO_N_IDS, SAAN_S_V) != SAAN_OK) {
        puts("init NG"); return 1;
    }
    float *pcm = (float *)malloc(sizeof(float) * SAAN_CHUNK * SAAN_HOP);
    uint64_t h = 1469598103934665603ULL;  /* FNV-1a 64 */
    long total = 0;
    for (;;) {
        int32_t nf = 0;
        if (saan_stream_pull(&st, pcm, &nf) != SAAN_OK) { puts("pull NG"); return 1; }
        if (nf == 0) break;
        for (int32_t i = 0; i < nf * SAAN_HOP; ++i) {
            float v = pcm[i] * 32767.0f;
            int32_t s = (int32_t)(v < 0 ? v - 0.5f : v + 0.5f);
            if (s > 32767) s = 32767; if (s < -32768) s = -32768;
            uint16_t u = (uint16_t)(int16_t)s;
            h = (h ^ (u & 0xff)) * 1099511628211ULL;
            h = (h ^ (u >> 8)) * 1099511628211ULL;
            ++total;
        }
    }
    printf("HEAD_PF=%d  sample %ld  FNV-1a 0x%016llx  arena used %zu / peak %zu  used() %zu\n",
           SAAN_MEM_HEAD_PF, total, (unsigned long long)h,
           a.used, a.peak, saan_stream_arena_used(SAAN_DEMO_N_IDS));
    return 0;
}
