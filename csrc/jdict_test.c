/* K-2 の受け入れゲート。
 *
 *   make -C csrc k2
 *
 * G6  MeCab と分割・エントリが一致
 * G7  陰性対照: 接続コストを 0 にすると G6 が落ちる
 * G8  -Wall -Wextra で警告 0 / コアで malloc を呼ばない（arena を渡す）
 * G9  経路を張れない文が 0 件（K-3）
 * G10 未知語を含む文でも MeCab と一致（K-3）
 * G11 陽性対照: 幽霊漢字が未知語ノードになる（K-3）
 *
 * ベクタは scripts/k1/k2_gen_vectors.py が作る（参照は MeCab そのもの）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "jdict.h"

#define MAX_TOK  512
#define ARENA_N  (2u << 20)

static uint8_t g_arena[ARENA_N];

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16)
           | ((uint32_t)p[3] << 24);
}

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "jdict_vectors.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = (uint8_t *)malloc((size_t)sz);      /* テスト側の読み込みのみ */
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        fprintf(stderr, "NG: 読み込み失敗\n"); return 1;
    }
    fclose(f);
    if (memcmp(buf, "K2V1", 4) != 0) { fprintf(stderr, "NG: magic\n"); return 1; }
    uint32_t n_cases = rd32(buf + 4), blob_n = rd32(buf + 8);

    jdict_t d;
    if (jdict_open(&d, buf + 12, blob_n) != 0) {
        fprintf(stderr, "NG: jdict_open が失敗\n"); return 1;
    }
    printf("辞書: %u entries / %u 見出し語 / matrix %ux%u\n",
           d.n_entries, d.n_surfaces, d.lsize, d.rsize);

    const uint8_t *p = buf + 12 + blob_n;
    int n_ok = 0, n_ng = 0, n_tok = 0;
    int n_nopath = 0, n_unk_case = 0, n_unk_ok = 0;
    static uint8_t key[8192];
    static jdict_token_t got[MAX_TOK];
    /* 陰性対照用に 2 周する: 0=通常, 1=接続コストを無視 */
    int fails_when_zeroed = 0;

    for (int pass = 0; pass < 2; pass++) {
        const uint8_t *q = p;
        int ok = 0, ng = 0;
        for (uint32_t c = 0; c < n_cases; c++) {
            uint32_t tn = rd32(q); q += 4;
            const uint8_t *text = q; q += tn;
            uint32_t nt = rd32(q); q += 4;
            const uint32_t *ref = (const uint32_t *)(const void *)q;
            q += 12u * nt;

            size_t kn = sizeof key;
            if (jdict_encode_key(&d, text, tn, key, &kn) != 0) { ng++; continue; }
            int m = (pass == 0)
                  ? jdict_analyze(&d, key, kn, g_arena, ARENA_N, got, MAX_TOK)
                  : jdict_analyze_nocost(&d, key, kn, g_arena, ARENA_N, got, MAX_TOK);
            int has_unk = 0;
            for (uint32_t i = 0; i < nt; i++)
                if (ref[3 * i + 2] & JDICT_UNKNOWN_FLAG) has_unk = 1;
            if (pass == 0 && m < 0) n_nopath++;
            /* ⚠️ **分母は先に数える。** 失敗したケースを分母から落とすと
             *    「53/53 通過」なのに実際は 6 件落ちている、という空虚な
             *    ゲートになる（実際に踏んだ）。 */
            if (pass == 0 && has_unk) n_unk_case++;
            if (m < 0 || (uint32_t)m != nt) { ng++; continue; }
            int same = 1;
            for (uint32_t i = 0; i < nt; i++) {
                if (got[i].begin != ref[3 * i] || got[i].end != ref[3 * i + 1]
                    || got[i].entry != ref[3 * i + 2]) { same = 0; break; }
            }
            if (same) ok++; else ng++;
            if (pass == 0) {
                n_tok += (int)nt;
                if (has_unk && same) n_unk_ok++;
            }
        }
        if (pass == 0) { n_ok = ok; n_ng = ng; }
        else fails_when_zeroed = ng;
    }

    printf("\n=== G6: MeCab と一致 ===\n");
    printf("  %s 一致 %d / %u 文（token %d 件）\n",
           (n_ng == 0 && n_ok > 0) ? "OK " : "NG ", n_ok, n_cases, n_tok);
    printf("\n=== G7: 陰性対照（接続コストを 0 にする）===\n");
    printf("  %s 落ちた文 %d / %u\n",
           (fails_when_zeroed > 0) ? "OK " : "NG ", fails_when_zeroed, n_cases);

    printf("\n=== G9: 経路を張れない文 ===\n");
    printf("  %s %d / %u 文\n", (n_nopath == 0) ? "OK " : "NG ", n_nopath, n_cases);

    printf("\n=== G10: 未知語を含む文でも一致 ===\n");
    printf("  %s %d / %d 文\n",
           (n_unk_case > 0 && n_unk_ok == n_unk_case) ? "OK " : "NG ",
           n_unk_ok, n_unk_case);
    if (n_unk_case == 0)
        printf("  NG  **未知語を含む文が 0 件。ゲートが空虚**（ベクタを見直すこと）\n");

    printf("\n=== G11: 陽性対照（幽霊漢字が未知語になる）===\n");
    {
        static const uint8_t probe[] = {
            0xE5,0xBD,0x81, 0xE3,0x81,0x8C, 0xE5,0x95,0x8F, 0xE9,0xA1,0x8C,
            0xE3,0x81,0xA7,0xE3,0x81,0x99, 0xE3,0x80,0x82 };  /* 彁が問題です。 */
        size_t kn2 = sizeof key;
        int gm = -1, n_unknown = 0;
        if (jdict_encode_key(&d, probe, sizeof probe, key, &kn2) == 0)
            gm = jdict_analyze(&d, key, kn2, g_arena, ARENA_N, got, MAX_TOK);
        for (int i = 0; i < gm; i++)
            if (got[i].entry & JDICT_UNKNOWN_FLAG) n_unknown++;
        printf("  %s 幽霊漢字を含む文 → %d token / 未知語 %d 件\n",
               (gm > 0 && n_unknown > 0) ? "OK " : "NG ", gm, n_unknown);
        if (!(gm > 0 && n_unknown > 0)) n_ng++;
    }

    free(buf);
    int bad = (n_ng != 0) || (n_ok == 0) || (fails_when_zeroed == 0)
            || (n_nopath != 0) || (n_unk_case == 0) || (n_unk_ok != n_unk_case);
    printf("\n%s\n", bad ? "NG!" : "OK  G6 / G7 / G9 / G10 / G11 通過");
    return bad;
}
