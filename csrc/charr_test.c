/* charr（文字カテゴリのレンジ表。M-106 §5）の C リーダを検証する。
 *
 * 65,535 符号位置の CharInfo は **106 run しかない**（M-97）ので、
 * run に畳めば 262,496 B → 832 B になる。**畳むだけなので無損失のはず。**
 *
 * ⚠️ **「辞書が引けた」は、レンジ表が正しい証拠にならない。**
 *    未知語の経路にしか効かないので、ほとんどの文は char を読まずに通る。
 *    だから**全符号位置を直に突き合わせる**。
 *
 *   full: `char`（65,535 × u32 をそのまま）
 *   rng : **同じ値**を `charr`（run 表）で
 *
 * G-R1  2 つの blob がどちらも開ける（比べる相手が存在すること）
 * G-R2  形式が実際に違う（full は char_info / rng は char_runs を使っている）
 * G-R3  **全 65,535 符号位置**で CharInfo の生 u32 が一致する
 *       （5 属性 type:18 / default_type:8 / length:4 / group:1 / invoke:1 を全部覆う）
 * G-R4  陽性対照: rng の**いちばん長い** run を書き換えると G-R3 が落ちる
 *       ⚠️ 短い run を選ぶと数件しか動かず、弱い対照になる（1 回踏んだ）
 *
 * ⚠️ **G-R2 が要る。** 両方 `char` を読んでいたら G-R3 は自明に通る。
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#include "jdict.h"

static uint32_t rd32_le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

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

/* 全符号位置で 5 属性を突き合わせ、不一致数を返す */
static long compare_all(const jdict_t *a, const jdict_t *b) {
    long bad = 0;
    for (uint32_t cp = 0; cp < 65535u; cp++) {
        /* CharInfo の生 u32 を比べる。**5 属性すべてがこの 32 bit に入っている**
         * （type:18 / default_type:8 / length:4 / group:1 / invoke:1）ので、
         * これが一致すれば属性も全部一致する。 */
        if (jdict_char_raw(a, cp) != jdict_char_raw(b, cp)) bad++;
    }
    return bad;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <full.bin> <rng.bin>\n", argv[0]);
        return 2;
    }
    setvbuf(stdout, NULL, _IONBF, 0);   /* 落ちたときに出力が消えないように */

    uint8_t *ra = NULL, *rb = NULL;
    jdict_t da, db;
    if (open_vec(argv[1], &ra, &da) != 0) return 1;
    if (open_vec(argv[2], &rb, &db) != 0) return 1;

    int ng = 0;

    printf("=== G-R1: どちらも開ける ===\n");
    printf("  OK  符号位置 %u / %u\n",
           (unsigned)da.n_codepoints, (unsigned)db.n_codepoints);
    if (da.n_codepoints != db.n_codepoints) {
        printf("  NG  符号位置の数が違う\n"); ng = 1;
    }

    printf("\n=== G-R2: 形式が実際に違う（片方だけ charr）===\n");
    if (da.char_info && !da.char_runs && !db.char_info && db.char_runs) {
        printf("  OK  第1引数 = char（生 u32） / 第2引数 = charr（run %u / 値 %u）\n",
               (unsigned)db.n_char_runs, (unsigned)db.n_char_vals);
    } else {
        printf("  NG  第1引数 info=%p runs=%p / 第2引数 info=%p runs=%p\n",
               (const void *)da.char_info, (const void *)da.char_runs,
               (const void *)db.char_info, (const void *)db.char_runs);
        printf("      ⚠️ 順番が逆か、両方が同じ形式。**この状態では G-R3 に意味が無い**\n");
        ng = 1;
    }

    if (ng) {
        printf("\n⚠️ **前提が崩れているので G-R3 以降は走らせない**\n");
        free(ra); free(rb);
        printf("\nNG!\n");
        return 1;
    }

    printf("\n=== G-R3: 全 65,535 符号位置で CharInfo が一致する ===\n");
    long bad = compare_all(&da, &db);
    if (bad == 0) printf("  OK  65535 / 65535 符号位置が一致\n");
    else { printf("  NG  不一致 %ld / 65535 符号位置\n", bad); ng = 1; }

    /* 陽性対照: run を 1 つ別の値へ向ける。
     * ⚠️ **同じ値の ID に向けると 0 件になる**ので、値が違う ID を探す。
     * ⚠️ **いちばん長い run を選ぶ。** 最初に run[1]（長さ 3）を選んだら
     *    不一致 3 件しか出ず、「効いてはいるが弱い」対照になっていた。
     *    run の長さはばらばら（最短 1 / 中央 26 / 最長 22,874）なので、
     *    **短い run を選ぶと、読み違いがあっても数件しか動かず見逃しうる**。 */
    printf("\n=== G-R4: 陽性対照（いちばん長い run を別の値へ向ける）===\n");
    if (db.n_char_runs < 2u || db.n_char_vals < 2u) {
        printf("  NG  run か値が 1 つしかない = 陽性対照を張れない\n"); ng = 1;
    } else {
        uint32_t best = 0, best_len = 0;
        for (uint32_t i = 0; i < db.n_char_runs; i++) {
            uint32_t st  = rd32_le(db.char_runs + 4u * i) >> 12;
            uint32_t en  = (i + 1 < db.n_char_runs)
                         ? (rd32_le(db.char_runs + 4u * (i + 1)) >> 12) : 65535u;
            if (en - st > best_len) { best_len = en - st; best = i; }
        }
        printf("  いちばん長い run: #%u（%u 符号位置）\n",
               (unsigned)best, (unsigned)best_len);
        uint8_t *r1 = (uint8_t *)(uintptr_t)(db.char_runs + 4u * best);   /* テスト専用 */
        uint32_t save = (uint32_t)r1[0] | ((uint32_t)r1[1] << 8)
                      | ((uint32_t)r1[2] << 16) | ((uint32_t)r1[3] << 24);
        long bad2 = 0;
        uint32_t used = save & 0xFFFu;
        for (uint32_t v = 0; v < db.n_char_vals; v++) {
            if (v == (save & 0xFFFu)) continue;
            uint32_t nv = (save & ~0xFFFu) | v;
            r1[0] = (uint8_t)nv; r1[1] = (uint8_t)(nv >> 8);
            r1[2] = (uint8_t)(nv >> 16); r1[3] = (uint8_t)(nv >> 24);
            bad2 = compare_all(&da, &db);
            if (bad2 > 0) { used = v; break; }
        }
        r1[0] = (uint8_t)save; r1[1] = (uint8_t)(save >> 8);
        r1[2] = (uint8_t)(save >> 16); r1[3] = (uint8_t)(save >> 24);
        if (bad2 > 0)
            printf("  OK  不一致 %ld 件（値 ID %u → %u に付け替えた）\n",
                   bad2, (unsigned)(save & 0xFFFu), (unsigned)used);
        else {
            printf("  NG  どの値へ向けても不一致 0 件 = **run が読まれていない可能性**\n");
            ng = 1;
        }
    }

    free(ra); free(rb);
    printf("\n%s\n", ng ? "NG!" : "OK  G-R1 / G-R2 / G-R3 / G-R4 通過");
    return ng;
}
