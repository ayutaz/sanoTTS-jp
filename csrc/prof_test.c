/* 段別プロファイラのホスト harness（`make -C csrc prof`）
 *
 * ⚠️ **ゲートではない。** OK / NG を出さない。段・カーネルごとの内訳を表にするだけ。
 *
 * 何を測るか: 実機（M5Stack CoreS3、第三者の報告）で W8A8 + PIE が 1.55× RT だった。
 * 1 チャンクの MAC 数に対してサイクル数が 70 倍多く、**積和以外が支配的**なのは
 * 分かるが、どれかは測らないと分からない。この harness は `saan_prof.h` の区間を
 * ホストで回して、**回数・要素数**（ホストでも実機でも同じ）と時間の内訳を出す。
 *
 * ⚠️ **ホストの時間の内訳は実機の内訳ではない。** M4 Max には FPU の除算も
 *    erff も速い実装があり、flash も無い。**回数と要素数だけを実機の推定に使い、
 *    時間の比は「ホストではこうだった」以上に読まない。** 実機の内訳は同じ表を
 *    `idf.py -DSAAN_PROFILE=1` で焼いて取る（esp32/main/main.c）。
 *
 * 入力は esp32/main/demo_ids.h の 53 ids（実機の報告と同じ 1 文）。
 *
 *   cc -std=c99 -O2 -DSAAN_INT8_ACT=1 -DSAAN_PROFILE=1 -o prof_test prof_test.c \
 *      saanotts.c saanotts_stream.c saanotts_int8.c fft.c -lm
 *   ./prof_test student_i8.bin [--reps 20]
 */
#define _POSIX_C_SOURCE 199309L

#include "saanotts.h"
#include "saanotts_stream.h"
#include "saan_prof.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../esp32/main/demo_ids.h"

#if !SAAN_PROFILE
#error "prof_test は -DSAAN_PROFILE=1 でビルドすること（区間が全部空になる）"
#endif

/* ホストの時計 = ns。uint32 に切るので 4.29 s で一周するが、区間の差にしか使わない */
uint32_t saan_prof_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec);
}

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(1);
    }
    fclose(f);
    *size = (size_t)n;
    return b;
}

#define ARENA_BYTES (256 * 1024)
#define REPS_DEFAULT 20

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s student_i8.bin [--reps N]\n", argv[0]);
        return 2;
    }
    int reps = REPS_DEFAULT;
    for (int i = 2; i + 1 < argc; ++i)
        if (strcmp(argv[i], "--reps") == 0) reps = atoi(argv[i + 1]);
    if (reps < 1) reps = 1;

    size_t wsz = 0;
    void *wbuf = slurp(argv[1], &wsz);
    saan_weights w;
    if (saan_weights_open(&w, wbuf, wsz) != SAAN_OK) {
        fprintf(stderr, "saan_weights_open 失敗: %s\n", argv[1]);
        return 1;
    }

    static float chunk[SAAN_CHUNK * SAAN_HOP];
    void *abuf = malloc(ARENA_BYTES);
    if (!abuf) return 1;

    saan_prof_reset();
    int32_t pulls = 0, frames = 0;
    for (int r = 0; r < reps; ++r) {
        saan_arena a;
        saan_arena_init(&a, abuf, ARENA_BYTES);
        saan_stream st;
        if (saan_stream_init(&st, &w, &a, kSaanDemoIds, SAAN_DEMO_N_IDS, SAAN_S_V) != SAAN_OK) {
            fprintf(stderr, "saan_stream_init 失敗\n");
            return 1;
        }
        int32_t n = 0;
        for (;;) {
            if (saan_stream_pull(&st, chunk, &n) != SAAN_OK) { fprintf(stderr, "pull 失敗\n"); return 1; }
            if (n <= 0) break;
            if (r == 0) { ++pulls; frames += n; }
        }
    }

    const uint32_t steps = saan_prof_cnt[SAAN_PROF_STEP];
    const double step_per_chunk = steps > 0 ? (double)saan_prof_acc[SAAN_PROF_STEP] / (double)steps : 0.0;
    printf("入力: demo_ids.h の %d ids / %d frames / pull %d 回 / step_chunk %u 回（%d 発話の合計）\n",
           (int)SAAN_DEMO_N_IDS, (int)frames, (int)pulls, (unsigned)steps, reps);
    printf("時計: ホストの ns（⚠️ 実機のサイクルではない。回数と要素数だけが実機と共通）\n\n");
    printf("%-8s %12s %14s %8s %14s %12s\n", "区間", "回数/step", "ns/step", "%STEP", "要素/step", "ns/要素");
    for (int id = 0; id < SAAN_PROF_N; ++id) {
        if (id == SAAN_PROF_INIT) continue;
        const double cnt = steps ? (double)saan_prof_cnt[id] / steps : 0.0;
        const double acc = steps ? (double)saan_prof_acc[id] / steps : 0.0;
        const double n   = steps ? (double)saan_prof_n[id] / steps : 0.0;
        printf("%-8s %12.2f %14.0f %7.1f%% %14.0f %12.2f\n", saan_prof_name(id), cnt, acc,
               step_per_chunk > 0 ? 100.0 * acc / step_per_chunk : 0.0, n,
               saan_prof_n[id] ? (double)saan_prof_acc[id] / (double)saan_prof_n[id] : 0.0);
    }
    printf("\nINIT（発話ごと）: %.0f ns / 回 (%u 回)\n",
           saan_prof_cnt[SAAN_PROF_INIT] ? (double)saan_prof_acc[SAAN_PROF_INIT] / saan_prof_cnt[SAAN_PROF_INIT] : 0.0,
           (unsigned)saan_prof_cnt[SAAN_PROF_INIT]);
    printf("1 発話の step_chunk 合計: %.3f ms（%u step × %.0f ns）\n",
           step_per_chunk * steps / reps / 1e6, (unsigned)(steps / (uint32_t)reps), step_per_chunk);
    free(abuf);
    free(wbuf);
    return 0;
}
