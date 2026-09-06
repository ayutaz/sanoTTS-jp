/* rec5（5 B レコード。M-107 §4a）の C リーダを検証する。
 *
 * 現行の `records` は 9 B 固定。`rec5` は (class, chain, flags) を 12 bit の
 * class2 に畳んで **5 B ちょうど**にする（classes 表は 8 → 10 B になる）。
 *
 * ⚠️ **「MeCab と N/M 一致した」は、リーダが正しい証拠にならない。**
 *    畳み込みそのものが分割を変えうるので、リーダにバグがあっても同じ数が出る。
 *
 * ここでやるのは**同じエントリを 2 通りの形式で持った blob を突き合わせる**こと:
 *
 *   r9: `records`（9 B） + `classes` 8 B
 *   r5: **同じ内容**を `rec5`（5 B） + `classes` 10 B
 *
 * リーダが正しければ、**全エントリで 3 つの読み口が一致する**:
 *   jdict_entry_conn（lc / rc / wcost） / jdict_entry_feature（素性文字列） /
 *   pool_offset（`jdict_entry_feature` の中で使われる。ずれると読みが崩れる）
 *
 * G-F1  2 つの blob の n_entries が一致する（比べる相手が別物でないこと）
 * G-F2  形式が実際に違う（r9 は records / r5 は rec5 を使っている）
 * G-F3  jdict_entry_conn が全エントリで一致する
 * G-F4  jdict_entry_feature が全エントリで一致する
 * G-F5  陽性対照: **全動作点で発火するもの**を使う
 *
 * ⚠️ **G-F2 が要る。** 両方 `records` を読んでいたら G-F3/F4 は自明に通る。
 * ⚠️ **陽性対照に class2 の幅を使わない**（M-107 §4a の反証）。
 *    class2 は動作点で 1,348〜2,097 と幅があり、11 bit に狭めても
 *    2,048 を超えない動作点では **1 bit も変わらない**。
 *    **pron長 / extra長 の幅**なら実データの最大が 30 / 61 なので必ず発火する。
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

/* jdict_entry_conn を全エントリで突き合わせ、不一致数を返す */
static long cmp_conn(const jdict_t *a, const jdict_t *b) {
    long bad = 0;
    for (uint32_t i = 0; i < a->n_entries; i++) {
        uint16_t la, ra, lb, rb; int16_t wa, wb;
        jdict_entry_conn(a, i, &la, &ra, &wa);
        jdict_entry_conn(b, i, &lb, &rb, &wb);
        if (la != lb || ra != rb || wa != wb) bad++;
    }
    return bad;
}

/* jdict_entry_feature を全エントリで突き合わせ、不一致数を返す。
 * ⚠️ **これが pool_offset も覆う**（素性は値プールから組み立てるので、
 *    オフセットが 1 B ずれれば文字列が変わる）。 */
static long cmp_feat(const jdict_t *a, const jdict_t *b) {
    long bad = 0;
    char fa[1024], fb[1024];
    for (uint32_t i = 0; i < a->n_entries; i++) {
        int na = jdict_entry_feature(a, i, "x", fa, sizeof fa);
        int nb = jdict_entry_feature(b, i, "x", fb, sizeof fb);
        if (na != nb || (na > 0 && memcmp(fa, fb, (size_t)na) != 0)) bad++;
    }
    return bad;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <r9.bin> <r5.bin>\n", argv[0]);
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

    printf("=== G-F1: n_entries が一致する ===\n");
    if (da.n_entries == db.n_entries && da.n_entries > 0) {
        printf("  OK  %u entries\n", (unsigned)da.n_entries);
    } else {
        printf("  NG  %u vs %u\n", (unsigned)da.n_entries, (unsigned)db.n_entries);
        ng = 1;
    }

    /* ⚠️ **これが無いと G-F3/F4 は自明に通る。** */
    printf("\n=== G-F2: 形式が実際に違う（片方だけ rec5）===\n");
    if (!da.rec5 && db.rec5) {
        printf("  OK  第1引数 = records(9 B) / 第2引数 = rec5(5 B)\n");
        printf("      classes: %u 種 vs %u 種（rec5 側は畳み込みで増えるはず）\n",
               (unsigned)da.n_classes, (unsigned)db.n_classes);
        /* ⚠️ **class2 が旧 classes と同数なら、この辞書は畳み込みを踏んでいない。**
         *    その状態では G-F3/F4 が通っても畳み込みの正しさは言えない。 */
        if (db.n_classes <= da.n_classes) {
            printf("  NG  class2 が旧 classes 以下 = 畳み込みを踏んでいない\n");
            ng = 1;
        }
    } else {
        printf("  NG  第1引数 rec5=%d / 第2引数 rec5=%d\n", da.rec5, db.rec5);
        printf("      ⚠️ 順番が逆か、両方が同じ形式。**この状態では G-F3 に意味が無い**\n");
        ng = 1;
    }

    if (ng) {
        printf("\n⚠️ **前提が崩れているので G-F3 以降は走らせない**\n");
        free(ra); free(rb);
        printf("\nNG!\n");
        return 1;
    }

    printf("\n=== G-F3: jdict_entry_conn が全エントリで一致する ===\n");
    long bad = cmp_conn(&da, &db);
    if (bad == 0) printf("  OK  %u / %u entries が一致\n",
                         (unsigned)da.n_entries, (unsigned)da.n_entries);
    else { printf("  NG  不一致 %ld / %u\n", bad, (unsigned)da.n_entries); ng = 1; }

    printf("\n=== G-F4: jdict_entry_feature が全エントリで一致する（pool_offset も覆う）===\n");
    long badf = cmp_feat(&da, &db);
    if (badf == 0) printf("  OK  %u / %u entries が一致\n",
                          (unsigned)da.n_entries, (unsigned)da.n_entries);
    else { printf("  NG  不一致 %ld / %u\n", badf, (unsigned)da.n_entries); ng = 1; }

    free(ra); free(rb);
    printf("\n%s\n", ng ? "NG!" : "OK  G-F1 / G-F2 / G-F3 / G-F4 通過");
    return ng;
}
