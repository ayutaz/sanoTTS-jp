/* 段別プロファイラのホスト harness（`make -C csrc prof`）
 *
 * 段・カーネルごとの内訳を表にする。**時間の行はゲートではない**。ゲートになるのは
 * 下の `--expect-*` で明示した回数（S1 の LOOKUP 0 回 / T1 の step_chunk 回数）だけ。
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
 *   ./prof_test student_i8.bin [--reps 20] [--expect-no-lookup] [--expect-steps N]
 *
 * ゲートは 2 つだけ（どちらも指定したときだけ効く）:
 *   `--expect-no-lookup`  **pull の中でテンソル検索（LOOKUP）が 1 回でも走ったら exit 1**。
 *                         S1（検索を init で 1 回に）の受け入れ条件。S1 前は 102 回/step で落ちる（陰性対照）。
 *   `--expect-steps N`    **step_chunk の総回数（reps 発話の合計）が N と違ったら exit 1**。
 *                         T1（pull ループの早期終了）の受け入れ条件。demo_ids.h の 106 frames は
 *                         3 発話で **54**（18/発話）。T1 前は 63（21/発話。全フレームが obuf に
 *                         出そろった後に 3 step 余分に回っていた）で落ちる（陰性対照）。
 *                         ⚠️ 回数は reps に比例するので、`--reps` と組で指定すること。
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
        fprintf(stderr, "usage: %s student_i8.bin [--reps N] [--expect-no-lookup] [--expect-steps N]\n", argv[0]);
        return 2;
    }
    int reps = REPS_DEFAULT;
    int expect_no_lookup = 0;
    long expect_steps = -1;   /* < 0 = 検査しない */
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--reps") == 0 && i + 1 < argc) reps = atoi(argv[++i]);
        else if (strcmp(argv[i], "--expect-no-lookup") == 0) expect_no_lookup = 1;
        else if (strcmp(argv[i], "--expect-steps") == 0 && i + 1 < argc) expect_steps = atol(argv[++i]);
    }
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
    uint32_t lookups_in_pull = 0;   /* init の外（= pull の中）で走った LOOKUP の回数 */
    for (int r = 0; r < reps; ++r) {
        saan_arena a;
        saan_arena_init(&a, abuf, ARENA_BYTES);
        saan_stream st;
        if (saan_stream_init(&st, &w, &a, kSaanDemoIds, SAAN_DEMO_N_IDS, SAAN_S_V) != SAAN_OK) {
            fprintf(stderr, "saan_stream_init 失敗\n");
            return 1;
        }
        const uint32_t lk_before = saan_prof_cnt[SAAN_PROF_LOOKUP];
        int32_t n = 0;
        for (;;) {
            if (saan_stream_pull(&st, chunk, &n) != SAAN_OK) { fprintf(stderr, "pull 失敗\n"); return 1; }
            if (n <= 0) break;
            if (r == 0) { ++pulls; frames += n; }
        }
        lookups_in_pull += saan_prof_cnt[SAAN_PROF_LOOKUP] - lk_before;
    }

    const uint32_t steps = saan_prof_cnt[SAAN_PROF_STEP];
    const double step_per_chunk = steps > 0 ? (double)saan_prof_acc[SAAN_PROF_STEP] / (double)steps : 0.0;
    printf("入力: demo_ids.h の %d ids / %d frames / pull %d 回 / step_chunk %u 回（%d 発話の合計）\n",
           (int)SAAN_DEMO_N_IDS, (int)frames, (int)pulls, (unsigned)steps, reps);
    printf("時計: ホストの ns（⚠️ 実機のサイクルではない。回数と要素数だけが実機と共通）\n\n");
    printf("%-8s %12s %14s %8s %14s %12s\n", "区間", "回数/step", "ns/step", "%STEP", "要素/step", "ns/要素");
    for (int id = 0; id < SAAN_PROF_N; ++id) {
        if (id == SAAN_PROF_INIT || id == SAAN_PROF_LOOKUP) continue;   /* 発話側の表に出す */
        const double cnt = steps ? (double)saan_prof_cnt[id] / steps : 0.0;
        const double acc = steps ? (double)saan_prof_acc[id] / steps : 0.0;
        const double n   = steps ? (double)saan_prof_n[id] / steps : 0.0;
        printf("%-8s %12.2f %14.0f %7.1f%% %14.0f %12.2f\n", saan_prof_name(id), cnt, acc,
               step_per_chunk > 0 ? 100.0 * acc / step_per_chunk : 0.0, n,
               saan_prof_n[id] ? (double)saan_prof_acc[id] / (double)saan_prof_n[id] : 0.0);
    }
    /* ⚠️ DW 行の要素数には、その中で呼ぶ QUANT（saan_quantize_act_i8p）の要素が重複して
     *    入っている（DW は区間全体、QUANT は入れ子の内側）。カーネル行を足し合わせるときは
     *    DW − QUANT(dw 分) で読むこと。 */
    printf("  ⚠️ DW の ns/step には入れ子の QUANT（dw 入力の量子化）が含まれる。カーネル行の単純合算は二重計上\n");
    printf("\n--- INIT 側（発話あたり。step で割らない）---\n");
    printf("INIT   : %.0f ns / 回 (%u 回)\n",
           saan_prof_cnt[SAAN_PROF_INIT] ? (double)saan_prof_acc[SAAN_PROF_INIT] / saan_prof_cnt[SAAN_PROF_INIT] : 0.0,
           (unsigned)saan_prof_cnt[SAAN_PROF_INIT]);
    /* LOOKUP は S1 で init に移した。step で割ると「0.6%/step」に見えるが、pull の中では 0 回 */
    printf("LOOKUP : %.2f 回 / 発話, %.0f ns / 発話（init の resolve_weights。pull の中の回数は下）\n",
           (double)saan_prof_cnt[SAAN_PROF_LOOKUP] / reps,
           (double)saan_prof_acc[SAAN_PROF_LOOKUP] / reps);
    printf("1 発話の step_chunk 合計: %.3f ms（%u step × %.0f ns）\n",
           step_per_chunk * steps / reps / 1e6, (unsigned)(steps / (uint32_t)reps), step_per_chunk);
    printf("pull の中の LOOKUP: %u 回（%d 発話の合計。init の分は含まない）\n",
           (unsigned)lookups_in_pull, reps);
    if (expect_no_lookup) {
        if (lookups_in_pull != 0) {
            printf("  NG! pull の中でテンソル検索が %u 回走っている（S1 の受け入れ条件は 0 回）\n",
                   (unsigned)lookups_in_pull);
            free(abuf); free(wbuf);
            return 1;
        }
        printf("  OK  pull の中でテンソル検索は 0 回（重みは init で解決済み）\n");
    }
    if (expect_steps >= 0) {
        if ((long)steps != expect_steps) {
            printf("  NG! step_chunk が %u 回（期待 %ld。%d 発話の合計）\n",
                   (unsigned)steps, expect_steps, reps);
            free(abuf); free(wbuf);
            return 1;
        }
        printf("  OK  step_chunk %u 回（期待 %ld。%d 発話 × %u）\n",
               (unsigned)steps, expect_steps, reps, (unsigned)(steps / (uint32_t)reps));
    }
    free(abuf);
    free(wbuf);
    return 0;
}
