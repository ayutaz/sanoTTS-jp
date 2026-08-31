/* K-4b の受け入れゲート。   make -C csrc k4b
 *
 * G14a  MeCab の feature 列 → NJD ノード列 がホストと一致する
 * G14b  陰性対照: njd_set_* を 1 つ抜くと G14a が落ちる（**njd_set_digit を必ず踏む**）
 *
 * 適用順は pyopenjtalk-plus の `openjtalk.pyx:1514-1526` と同じ:
 *   mecab2njd → pronunciation → **before_chaining** → digit → accent_phrase
 *   → accent_type → unvoiced_vowel → long_vowel
 * ⚠️ **before_chaining は素の Open JTalk に無い**（フォークが Python 側で
 *    挟んでいる段。`k4b_njd.h`）。飛ばすと 302 / 600 で止まる。
 * ⚠️ **K-4 の 4 段はこの後**に適用される。ここでは通さない。
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "openjtalk/njd.h"
#include "openjtalk/mecab2njd.h"
#include "k4b_njd.h"
#include "openjtalk/njd_set_pronunciation.h"
#include "openjtalk/njd_set_digit.h"
#include "openjtalk/njd_set_accent_phrase.h"
#include "openjtalk/njd_set_accent_type.h"
#include "openjtalk/njd_set_unvoiced_vowel.h"
#include "openjtalk/njd_set_long_vowel.h"

#define MAX_FEAT 1024
#define STAGES 7

static const char *STAGE_NAME[STAGES] = {
    "njd_set_pronunciation", "k4b_before_chaining", "njd_set_digit",
    "njd_set_accent_phrase", "njd_set_accent_type", "njd_set_unvoiced_vowel",
    "njd_set_long_vowel"
};
#define STAGE_DIGIT 2        /* 数詞の読み分けを持つ段。空虚だと音で気づけない */
#define STAGE_LONG_VOWEL 6   /* 上流が `#if 1 return;` で潰してある no-op */

static const uint8_t *g;
static uint32_t rd32(void) {
    uint32_t v = (uint32_t)g[0] | ((uint32_t)g[1] << 8)
               | ((uint32_t)g[2] << 16) | ((uint32_t)g[3] << 24);
    g += 4; return v;
}
static int32_t rdi32(void) { return (int32_t)rd32(); }
static char *rdstr(char *out, size_t cap) {
    uint16_t n = (uint16_t)(g[0] | (g[1] << 8)); g += 2;
    size_t k = (n < cap - 1) ? n : cap - 1;
    memcpy(out, g, k); out[k] = 0; g += n;
    return out;
}

static void run_chain(NJD *njd, char **feat, int n, int skip) {
    mecab2njd(njd, feat, n);
    if (skip != 0) njd_set_pronunciation(njd);
    if (skip != 1) k4b_before_chaining(njd);
    if (skip != 2) njd_set_digit(njd);
    if (skip != 3) njd_set_accent_phrase(njd);
    if (skip != 4) njd_set_accent_type(njd);
    if (skip != 5) njd_set_unvoiced_vowel(njd);
    if (skip != 6) njd_set_long_vowel(njd);
}

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "k4b_vectors.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)sz);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    if (memcmp(buf, "K4B1", 4)) { fprintf(stderr, "NG: magic\n"); return 1; }
    g = buf + 4;
    uint32_t n_cases = rd32();
    const uint8_t *body = g;

    static char *feat[MAX_FEAT];
    static char febuf[MAX_FEAT][1024];
    int ok = 0, ng = 0, n_node = 0;
    int stage_diff[STAGES]; memset(stage_diff, 0, sizeof stage_diff);
    int first_bad = 1;
    /* 規則の発火は本番の 1 周（pass = -1）だけ数える。陰性対照の周回を
     * 混ぜると「何を覆えているか」の数字にならない。 */
    unsigned rule_hits[K4B_N_RULES];
    int rule_diff[K4B_N_RULES]; memset(rule_diff, 0, sizeof rule_diff);

    /* pass = -1        本番
     * pass < STAGES    段を 1 つ抜く（G14b）
     * それ以降          before_chaining の規則を 1 つ抜く（G14c） */
    /* --solo: 本番の 1 周だけ。上流の stderr が陰性対照の周回で出ているのか、
     * 本番でも出ているのかを切り分けるため。 */
    int last_pass = (argc > 2 && strcmp(argv[2], "--solo") == 0)
                    ? 0 : STAGES + K4B_N_RULES;
    for (int pass = -1; pass < last_pass; pass++) {
        int skip = (pass < STAGES) ? pass : -1;
        k4b_rule_mask = (pass >= STAGES) ? ~(1u << (pass - STAGES)) : ~0u;
        g = body;
        int local_ng = 0;
        for (uint32_t c = 0; c < n_cases; c++) {
            uint32_t nf = rd32();
            for (uint32_t i = 0; i < nf; i++) {
                rdstr(febuf[i], sizeof febuf[i]);
                feat[i] = febuf[i];
            }
            uint32_t nn = rd32();

            NJD njd; NJD_initialize(&njd);
            run_chain(&njd, feat, (int)nf, skip);

            /* 期待値と突き合わせる */
            NJDNode *node = njd.head;
            uint32_t cnt = 0;
            int same = 1;
            for (uint32_t i = 0; i < nn; i++) {
                char s[11][1024];
                for (int k = 0; k < 11; k++) rdstr(s[k], sizeof s[k]);
                int32_t e_acc = rdi32(), e_mora = rdi32(), e_chain = rdi32();
                if (!node) { same = 0; continue; }
                /* ⚠️ **必ず getter を通す。** 生フィールドは NULL を取りうるが、
                 *    ホストが見ているのは getter で、NULL は `"*"` に写る。
                 *    直読みすると 19 / 600 が偽の不一致になる。 */
                const char *got[11] = {
                    NJDNode_get_string(node), NJDNode_get_pos(node),
                    NJDNode_get_pos_group1(node), NJDNode_get_pos_group2(node),
                    NJDNode_get_pos_group3(node), NJDNode_get_ctype(node),
                    NJDNode_get_cform(node), NJDNode_get_orig(node),
                    NJDNode_get_read(node), NJDNode_get_pron(node),
                    NJDNode_get_chain_rule(node) };
                for (int k = 0; k < 11; k++)
                    if (strcmp(got[k] ? got[k] : "", s[k])) same = 0;
                if (node->acc != e_acc || node->mora_size != e_mora
                    || node->chain_flag != e_chain) same = 0;
                if (pass < 0 && !same && first_bad) {
                    printf("  最初の食い違い（case %u node %u）:\n", c, i);
                    for (int k = 0; k < 11; k++)
                        if (strcmp(got[k] ? got[k] : "", s[k]))
                            printf("    [%d] 期待 %-14s 実際 %s\n", k, s[k],
                                   got[k] ? got[k] : "(null)");
                    if (node->acc != e_acc) printf("    acc 期待 %d 実際 %d\n", e_acc, node->acc);
                    if (node->mora_size != e_mora) printf("    mora 期待 %d 実際 %d\n", e_mora, node->mora_size);
                    if (node->chain_flag != e_chain) printf("    chain 期待 %d 実際 %d\n", e_chain, node->chain_flag);
                    first_bad = 0;
                }
                node = node->next; cnt++;
            }
            while (node) { node = node->next; cnt++; }
            if (cnt != nn) same = 0;
            if (pass < 0) { n_node += (int)nn; if (same) ok++; else ng++; }
            else if (!same) local_ng++;
            NJD_clear(&njd);
        }
        if (pass < 0) memcpy(rule_hits, k4b_rule_hits, sizeof rule_hits);
        else if (pass < STAGES) stage_diff[pass] = local_ng;
        else rule_diff[pass - STAGES] = local_ng;
    }
    k4b_rule_mask = ~0u;

    printf("\n=== G14a: MeCab feature 列 → NJD 列 がホストと一致 ===\n");
    printf("  %s %d / %u 文（NJD ノード %d 件）\n",
           (ng == 0 && ok > 0) ? "OK " : "NG ", ok, n_cases, n_node);

    printf("\n=== G14c: before_chaining の規則を 1 つずつ抜く ===\n");
    printf("  %-24s %8s %8s\n", "規則", "発火", "抜くと落ちる");
    int n_cold = 0;
    for (int k = 0; k < K4B_N_RULES; k++) {
        /* ⚠️ 発火しても落ちない規則がある（規則 1 は後段の njd_set_digit に
         *    上書きされて消える）。**「落ちる」の方だけが覆えている証拠。** */
        const char *note = rule_diff[k] ? ""
            : (rule_hits[k] ? "   ⚠️ **発火するが結果に出ない = 検証できていない**"
                            : "   ⚠️ **一度も発火しない = 検証できていない**");
        printf("  %-24s %8u %8d%s\n",
               k4b_rule_name[k], rule_hits[k], rule_diff[k], note);
        if (!rule_diff[k]) n_cold++;
    }
    printf("  → **検証できている規則: %d / %d**\n", K4B_N_RULES - n_cold, K4B_N_RULES);

    printf("\n=== G14b: 陰性対照（段を 1 つ抜く）===\n");
    int bad13 = 0, n_eff = 0;
    for (int k = 0; k < STAGES; k++) {
        const char *note = "";
        if (!stage_diff[k])
            note = (k == STAGE_LONG_VOWEL)
                /* 上流が `#if 1 return;` で潰してある。corpus のせいではない */
                ? "   （上流で no-op。`#if 1 return;`）"
                : "   ⚠️ **この corpus では効かない。陰性対照は空虚**";
        printf("  %-24s 抜くと落ちる %4d / %u 文%s\n",
               STAGE_NAME[k], stage_diff[k], n_cases, note);
        if (stage_diff[k]) n_eff++;
    }
    printf("  → 判別力のある段: %d / %d"
           "（long_vowel は上流が no-op なので原理的に 0）\n", n_eff, STAGES);
    /* ⚠️ njd_set_digit は数値の読み分けを持つ。抜いて落ちないなら
     *    corpus に数値が足りていない（音では気づけない欠陥）。 */
    if (!stage_diff[STAGE_DIGIT]) {
        printf("  NG  **njd_set_digit が空虚**。数詞を含む文を corpus に入れること\n");
        bad13 = 1;
    }
    if (n_eff == 0) { printf("  NG  **全段が空虚**\n"); bad13 = 1; }

    free(buf);
    int bad = (ng != 0) || (ok == 0) || bad13;
    printf("\n%s\n", bad ? "NG!" : "OK  G14a / G14b 通過");
    return bad;
}
