/* matrixc（行・列クラスタ + 代表行列の接続行列。M-106 §2）の C リーダを検証する。
 *
 * ⚠️ **「MeCab と N/M 一致した」は、リーダが正しい証拠にならない。**
 *    量子化そのものが分割を変えるので、リーダにバグがあっても同じ数が出うる
 *    （matrixa_test.c で実際に両方の形式が同じ数を出した）。
 *
 * ここでやるのは**同じ逆量子化値を 2 通りの形式で持った blob を突き合わせる**こと:
 *
 *   deq: クラスタで畳んだ値を **生 int16**（セクション `matrix`）で持つ
 *   clu: **同じ値**を **`matrixc`**（lo / span / q / rmap / cmap）で持つ
 *
 * リーダが正しければ、`jdict_trans` は **全 lsize×rsize 要素で一致**する。
 * 1 要素でも違えば整数式か索引がずれている。
 *
 * G-C1  2 つの blob の寸法が一致する（比べる相手が別物でないこと）
 * G-C2  形式が実際に違う（deq は matrix / clu は matrixc を使っている）
 * G-C3  jdict_trans が全要素で一致する
 * G-C4  陽性対照 1: clu の lo を 1 だけずらすと G-C3 が落ちる
 * G-C5  陽性対照 2: **rmap を 1 つずらす**と G-C3 が落ちる
 *       ⚠️ **G-C4 だけでは写像を検証できない。** lo をずらすのは代表行列側の検査で、
 *          `rmap` / `cmap` を読み違えていても G-C4 は通る（matrixa と同じ経路だから）。
 *
 * ⚠️ **G-C2 が要る。** 両方 `matrix` を読んでいたら G-C3 は自明に通る。
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
        fprintf(stderr, "usage: %s <deq.bin> <clu.bin>\n", argv[0]);
        return 2;
    }
    /* ⚠️ **バッファリングを切る。** 落ちたときに printf の中身が消えると
     *    「何も出さずに終了」に見えて原因が分からない（matrixa_test で踏んだ）。 */
    setvbuf(stdout, NULL, _IONBF, 0);

    uint8_t *ra = NULL, *rb = NULL;
    jdict_t da, db;
    if (open_vec(argv[1], &ra, &da) != 0) return 1;
    if (open_vec(argv[2], &rb, &db) != 0) return 1;

    int ng = 0;

    printf("=== G-C1: 寸法が一致する ===\n");
    if (da.lsize == db.lsize && da.rsize == db.rsize && da.lsize && da.rsize) {
        printf("  OK  %ux%u\n", (unsigned)da.lsize, (unsigned)da.rsize);
    } else {
        printf("  NG  %ux%u vs %ux%u\n", (unsigned)da.lsize, (unsigned)da.rsize,
               (unsigned)db.lsize, (unsigned)db.rsize);
        ng = 1;
    }

    /* ⚠️ **これが無いと G-C3 は自明に通る。** */
    printf("\n=== G-C2: 形式が実際に違う（片方だけ matrixc）===\n");
    if (da.matrix && !da.matrix_rmap && !db.matrix && db.matrix_rmap) {
        printf("  OK  第1引数 = 生 int16 / 第2引数 = matrixc（%ux%u クラスタ）\n",
               (unsigned)db.matrix_kr, (unsigned)db.matrix_kc);
        /* ⚠️ **クラスタ数が寸法と同じなら畳めていない** = このゲートは何も見ていない */
        if (db.matrix_kr >= db.rsize || db.matrix_kc >= db.lsize) {
            printf("  NG  クラスタ数が寸法以上（%u>=%u / %u>=%u）= 畳めていない\n",
                   (unsigned)db.matrix_kr, (unsigned)db.rsize,
                   (unsigned)db.matrix_kc, (unsigned)db.lsize);
            ng = 1;
        }
    } else {
        printf("  NG  第1引数 matrix=%p rmap=%p / 第2引数 matrix=%p rmap=%p\n",
               (const void *)da.matrix, (const void *)da.matrix_rmap,
               (const void *)db.matrix, (const void *)db.matrix_rmap);
        printf("      ⚠️ 順番が逆か、両方が同じ形式。**この状態では G-C3 に意味が無い**\n");
        ng = 1;
    }

    /* ⚠️ **形式が違うことを確かめてから先へ進む。** 第2引数が matrixc でないのに
     *    G-C4 / G-C5 を走らせると NULL 参照で落ちる（matrixa_test で踏んだ）。 */
    if (ng) {
        printf("\n⚠️ **前提が崩れているので G-C3 以降は走らせない**"
               "（走らせても意味が無く、NULL 参照で落ちる）\n");
        free(ra); free(rb);
        printf("\nNG!\n");
        return 1;
    }

    printf("\n=== G-C3: jdict_trans が全要素で一致する ===\n");
    long total = (long)da.lsize * (long)da.rsize;
    long bad = compare_all(&da, &db);
    if (bad == 0) printf("  OK  %ld / %ld 要素が一致\n", total, total);
    else { printf("  NG  不一致 %ld / %ld 要素\n", bad, total); ng = 1; }

    /* 陽性対照 1: 代表行列の lo をずらす。その行クラスタに属する全 lc 行が動く。
     * ⚠️ **「落ちること」だけでなく「何件動いたか」を出す。** 0 件なら無効。 */
    printf("\n=== G-C4: 陽性対照 1（代表行列の lo[0] を +1）===\n");
    {
        int16_t *lo = (int16_t *)(void *)(uintptr_t)db.matrix_lo;   /* テスト専用 */
        int16_t save = lo[0];
        lo[0] = (int16_t)(save + 1);
        long bad2 = compare_all(&da, &db);
        lo[0] = save;
        if (bad2 > 0) printf("  OK  不一致 %ld 件（クラスタ 0 に属する行が動いた）\n", bad2);
        else { printf("  NG  不一致 0 件 = **陽性対照が効いていない**\n"); ng = 1; }
    }

    /* 陽性対照 2: 写像を壊す。**これが matrixc 固有の経路**で、G-C4 では覆えない。
     * ⚠️ **値が同じクラスタへ移すと 0 件になる**ので、実際に値が違うクラスタを探す。 */
    printf("\n=== G-C5: 陽性対照 2（rmap を別クラスタへ向ける）===\n");
    {
        uint16_t *rmap = (uint16_t *)(void *)(uintptr_t)db.matrix_rmap;
        uint16_t save = rmap[0];
        long bad3 = 0;
        uint16_t used = save;
        for (uint16_t k = 0; k < db.matrix_kr; k++) {
            if (k == save) continue;
            rmap[0] = k;
            bad3 = compare_all(&da, &db);
            if (bad3 > 0) { used = k; break; }
        }
        rmap[0] = save;
        if (bad3 > 0)
            printf("  OK  不一致 %ld 件（行クラスタ %u → %u に付け替えた）\n",
                   bad3, (unsigned)save, (unsigned)used);
        else {
            printf("  NG  どの行クラスタへ向けても不一致 0 件 = "
                   "**rmap が読まれていない可能性**\n");
            ng = 1;
        }
    }

    free(ra); free(rb);
    printf("\n%s\n", ng ? "NG!" : "OK  G-C1 / G-C2 / G-C3 / G-C4 / G-C5 通過");
    return ng;
}
