/* MEM-8 のゲート — **`saan_stream_pull_ptr` がコピー版と同じサンプル列を出すか**
 *
 *   make -C csrc pullptr
 *
 * **なぜ要るか。** MEM-8 は呼び出し側の `float [SAAN_CHUNK * SAAN_HOP]`（**8,192 B**）を
 * 消すために、`obuf` の中を指して返す形を足した（[M-145](../docs/measurements.md#m-145)）。
 * 詰め直し（memmove）を**次の呼び出しまで遅らせる**ので、
 *   - 遅らせ方を間違えると **同じフレームを 2 回出す / 1 回飛ばす**
 *   - `st->emitted` の進み方が変わる
 * ⚠️ **どちらも「音は出る」形で壊れる。** 列を memcmp すること。
 *
 * ゲート:
 *   G-PP1  `pull_ptr` の全サンプル列が `pull` の列と bit 一致（n_ids 6 点）
 *   G-PP2  発話後の `st.emitted` / 総フレーム数 / pull 回数が一致
 *   G-PP3  ⚠️ **陽性対照**: 返ったポインタは「次の呼び出しまで有効」と書いてある。
 *          **本当に次の呼び出しで中身が動くか**を確かめる（動かないなら警告が空虚で、
 *          将来「保持しても大丈夫」と誤解される）
 */
#include "saanotts.h"
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

/* 列を全部ためる。copy=1 ならコピー版、0 ならポインタ版 */
static int run_all(const int32_t *ids, int n, int copy,
                   float **out, size_t *n_samp, int *pulls, int32_t *emitted) {
    saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
    saan_stream st;
    if (saan_stream_init(&st, &g_W, &a, ids, n, SAAN_S_V) != SAAN_OK) return 1;
    size_t cap = (size_t)st.n_frames * SAAN_HOP + 4096, len = 0;
    float *buf = (float *)malloc(sizeof(float) * cap);
    if (!buf) return 1;
    static float tmp[SAAN_CHUNK * SAAN_HOP];
    int np = 0;
    for (;;) {
        int32_t nh = 0;
        if (copy) {
            if (saan_stream_pull(&st, tmp, &nh) != SAAN_OK) return 1;
            if (nh == 0) break;
            memcpy(buf + len, tmp, sizeof(float) * (size_t)nh * SAAN_HOP);
        } else {
            const float *p = NULL;
            if (saan_stream_pull_ptr(&st, &p, &nh) != SAAN_OK) return 1;
            if (nh == 0) break;
            memcpy(buf + len, p, sizeof(float) * (size_t)nh * SAAN_HOP);
        }
        len += (size_t)nh * SAAN_HOP;
        ++np;
        if (len > cap) { fprintf(stderr, "溢れた\n"); return 1; }
    }
    *out = buf; *n_samp = len; *pulls = np; *emitted = st.emitted;
    return 0;
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

    printf("MEM-8 ゲート（pull_ptr == pull / W8A8 = %d）\n\n",
#ifdef SAAN_INT8_ACT
           SAAN_INT8_ACT
#else
           0
#endif
    );
    static const int NS[] = { 1, 13, 53, 100, 224, 350 };
    int bad = 0;

    printf("== G-PP1 / G-PP2: サンプル列と簿記が一致するか ==\n");
    printf("| n_ids | sample | pull 回数 | emitted | 列 bit 一致 |\n|---:|---:|---:|---:|---|\n");
    for (size_t q = 0; q < sizeof NS / sizeof *NS; ++q) {
        const int n = NS[q];
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        float *a = NULL, *b = NULL; size_t na = 0, nbs = 0;
        int pa = 0, pb = 0; int32_t ea = 0, eb = 0;
        if (run_all(ids, n, 1, &a, &na, &pa, &ea) || run_all(ids, n, 0, &b, &nbs, &pb, &eb)) {
            printf("| %d | 走らせられない |\n", n); ++bad; free(ids); continue;
        }
        const int eq = (na == nbs) && (pa == pb) && (ea == eb)
                    && memcmp(a, b, sizeof(float) * na) == 0;
        if (!eq) ++bad;
        printf("| %d | %zu %s %zu | %d %s %d | %d %s %d | %s |\n", n,
               na, na == nbs ? "==" : "!=", nbs,
               pa, pa == pb ? "==" : "!=", pb,
               (int)ea, ea == eb ? "==" : "!=", (int)eb,
               eq ? "**OK**" : "**NG**");
        free(a); free(b); free(ids);
    }

    printf("\n== G-PP3: 陽性対照（返ったポインタは次の呼び出しで無効になるか）==\n");
    {
        const int n = 53;
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = (int32_t)idf[i % base_n];
        saan_arena a; saan_arena_init(&a, g_buf, sizeof g_buf);
        saan_stream st;
        if (saan_stream_init(&st, &g_W, &a, ids, n, SAAN_S_V) != SAAN_OK) {
            printf("  NG! init が失敗\n"); ++bad;
        } else {
            const float *p1 = NULL; int32_t n1 = 0;
            saan_stream_pull_ptr(&st, &p1, &n1);
            static float keep[SAAN_CHUNK * SAAN_HOP];
            memcpy(keep, p1, sizeof(float) * (size_t)n1 * SAAN_HOP);
            const float *p2 = NULL; int32_t n2 = 0;
            saan_stream_pull_ptr(&st, &p2, &n2);
            /* p1 が指していた所は詰め直されているはず = 保持は危険 */
            const int moved = memcmp(keep, p1, sizeof(float) * (size_t)n1 * SAAN_HOP) != 0;
            printf("  %s 1 回目 %d hop / 2 回目 %d hop / 同じ番地か %s / 中身が動いたか %s\n",
                   moved ? "OK " : "NG!", (int)n1, (int)n2,
                   p1 == p2 ? "はい" : "いいえ", moved ? "はい" : "**いいえ**");
            if (!moved) {
                printf("      ⚠️ 動かないなら「次の呼び出しまで有効」の警告が空虚で、\n"
                       "         将来「保持してよい」と誤解される。警告を直すか実装を見直すこと。\n");
                ++bad;
            }
        }
        free(ids);
    }

    printf("\n%s\n", bad ? "NG: MEM-8 のゲートに落ちた"
                         : "MEM-8: pull_ptr はコピー版と同じ列を出し、ポインタの寿命の警告も空虚でない");
    return bad ? 1 : 0;
}
