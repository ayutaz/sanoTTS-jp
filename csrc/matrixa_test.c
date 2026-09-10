/* matrixa（行ごとアフィン uint8 の接続行列。D-051 の ①）の C リーダを検証する。
 *
 * ⚠️ **「MeCab と 183/184 一致した」は、リーダが正しい証拠にならない。**
 *    量子化そのものが 1 文の分割を変えるので、リーダにバグがあっても
 *    同じ 183/184 が出うる（実際に両方の形式で同じ数が出た）。
 *
 * ここでやるのは**同じ逆量子化値を 2 通りの形式で持った blob を突き合わせる**こと:
 *
 *   deq: 逆量子化した値を **生 int16**（セクション `matrix`）で持つ
 *   aff: **同じ値**を **`matrixa`**（lo / span / uint8）で持つ
 *
 * リーダが正しければ、`jdict_trans` は **全 lsize×rsize 要素で一致**する。
 * 1 要素でも違えば整数式か索引がずれている。
 *
 * G-A1  2 つの blob の寸法が一致する（比べる相手が別物でないこと）
 * G-A2  形式が実際に違う（deq は matrix / aff は matrixa を使っている）
 * G-A3  jdict_trans が全要素で一致する
 * G-A4  陽性対照: aff の lo を 1 だけずらすと G-A3 が落ちる
 *
 * ⚠️ **G-A2 が要る。** 両方 `matrix` を読んでいたら G-A3 は自明に通る。
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#include "jdict.h"

static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG! 開けない: %s\n", path); return NULL; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = (uint8_t *)malloc((size_t)sz);
    if (!b || fread(b, 1, (size_t)sz, f) != (size_t)sz) {
        fprintf(stderr, "NG! 読めない: %s\n", path); fclose(f); free(b); return NULL;
    }
    fclose(f); *n = (size_t)sz; return b;
}

/* K2V1: magic u32 / n_cases u32 / blob_bytes u32 / <blob> / <cases> */
static int open_vec(const char *path, uint8_t **raw, jdict_t *d) {
    size_t n = 0;
    *raw = slurp(path, &n);
    if (!*raw) return -1;
    if (n < 12 || memcmp(*raw, "K2V1", 4) != 0) {
        fprintf(stderr, "NG! magic が K2V1 でない: %s\n", path); return -1;
    }
    uint32_t blob_n = (uint32_t)((*raw)[8] | ((*raw)[9] << 8)
                                 | ((*raw)[10] << 16) | ((uint32_t)(*raw)[11] << 24));
    if ((size_t)blob_n + 12u > n) { fprintf(stderr, "NG! blob 長が壊れている\n"); return -1; }
    int r = jdict_open(d, *raw + 12, blob_n);
    if (r != 0) { fprintf(stderr, "NG! jdict_open(%s) = %d\n", path, r); return -1; }
    return 0;
}

/* 全要素を突き合わせ、不一致数を返す */
static long compare_all(const jdict_t *a, const jdict_t *b) {
    long bad = 0;
    for (uint32_t lc = 0; lc < a->rsize; lc++)
        for (uint32_t rc = 0; rc < a->lsize; rc++)
            if (jdict_trans(a, (uint16_t)rc, (uint16_t)lc)
                != jdict_trans(b, (uint16_t)rc, (uint16_t)lc)) bad++;
    return bad;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <deq.bin> <aff.bin>\n", argv[0]);
        return 2;
    }
    /* ⚠️ **バッファリングを切る。** 落ちたときに printf の中身が消えると、
     *    「何も出さずに終了」に見えて原因が分からない（実際に踏んだ）。 */
    setvbuf(stdout, NULL, _IONBF, 0);

    uint8_t *ra = NULL, *rb = NULL;
    jdict_t da, db;
    if (open_vec(argv[1], &ra, &da) != 0) return 1;
    if (open_vec(argv[2], &rb, &db) != 0) return 1;

    int ng = 0;

    printf("=== G-A1: 寸法が一致する ===\n");
    if (da.lsize == db.lsize && da.rsize == db.rsize && da.lsize && da.rsize) {
        printf("  OK  %ux%u\n", (unsigned)da.lsize, (unsigned)da.rsize);
    } else {
        printf("  NG  %ux%u vs %ux%u\n", (unsigned)da.lsize, (unsigned)da.rsize,
               (unsigned)db.lsize, (unsigned)db.rsize);
        ng = 1;
    }

    /* ⚠️ **これが無いと G-A3 は自明に通る。** 引数を 2 回同じにしても気づけない
     *    （`int8_e2e_test` が同じブロブ 2 つで「平均 inf dB / OK」を出していた形）。 */
    printf("\n=== G-A2: 形式が実際に違う（片方だけ matrixa）===\n");
    if (da.matrix && !da.matrix_q && !db.matrix && db.matrix_q) {
        printf("  OK  第1引数 = 生 int16 / 第2引数 = matrixa\n");
    } else {
        printf("  NG  第1引数 matrix=%p matrix_q=%p / 第2引数 matrix=%p matrix_q=%p\n",
               (const void *)da.matrix, (const void *)da.matrix_q,
               (const void *)db.matrix, (const void *)db.matrix_q);
        printf("      ⚠️ 順番が逆か、両方が同じ形式。**この状態では G-A3 に意味が無い**\n");
        ng = 1;
    }

    /* ⚠️ **形式が違うことを確かめてから先へ進む。** 第2引数が matrixa でないのに
     *    G-A4 を走らせると `matrix_lo` が NULL で落ちる（陰性対照で実際に踏んだ）。 */
    if (ng) {
        printf("\n⚠️ **前提が崩れているので G-A3 / G-A4 は走らせない**"
               "（走らせても意味が無く、NULL 参照で落ちる）\n");
        free(ra); free(rb);
        printf("\nNG!\n");
        return 1;
    }

    printf("\n=== G-A3: jdict_trans が全要素で一致する ===\n");
    long total = (long)da.lsize * (long)da.rsize;
    long bad = compare_all(&da, &db);
    if (bad == 0) printf("  OK  %ld / %ld 要素が一致\n", total, total);
    else { printf("  NG  不一致 %ld / %ld 要素\n", bad, total); ng = 1; }

    /* 陽性対照: matrixa の lo を 1 だけずらす。行 7 の全列（lsize 件）が動くはず。
     * ⚠️ **「落ちること」だけでなく「何件動いたか」を出す。** 0 件なら陽性対照が無効。 */
    printf("\n=== G-A4: 陽性対照（matrixa の lo[7] を +1）===\n");
    if (!db.matrix_lo) {
        printf("  NG  第2引数が matrixa ではない（matrix_lo が NULL）\n");
        ng = 1;
    } else {
        int16_t *lo = (int16_t *)(void *)(uintptr_t)db.matrix_lo;   /* テスト専用 */
        int16_t save = lo[7];
        lo[7] = (int16_t)(save + 1);
        long bad2 = compare_all(&da, &db);
        lo[7] = save;
        if (bad2 == (long)da.lsize)
            printf("  OK  不一致 %ld 件（= lsize %u。行 7 の全列が動いた）\n",
                   bad2, (unsigned)da.lsize);
        else if (bad2 > 0)
            printf("  OK  不一致 %ld 件（⚠️ 期待は lsize %u。飽和で一部動かない可能性）\n",
                   bad2, (unsigned)da.lsize);
        else { printf("  NG  不一致 0 件 = **陽性対照が効いていない**\n"); ng = 1; }
    }

    free(ra); free(rb);
    printf("\n%s\n", ng ? "NG!" : "OK  G-A1 / G-A2 / G-A3 / G-A4 通過");
    return ng;
}
