/* K-4 の受け入れゲート。   make -C csrc k4
 *
 * G12 4 段を適用した結果が Python 版と一致する
 * G13 陰性対照: 1 段抜くと G12 が落ちる（段ごとに）
 *
 * ⚠️ **段によっては陰性対照が空虚になる。** suppress_u_long は
 *    このコーパスで 1 文も動かさない（K-1 §5-2 の LOO 0 と整合）。
 *    その段は PASS ではなく「効かない」と報告する。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "k4_accent.h"

#define MAX_NODES 512

static const unsigned STAGE_BIT[4] = { K4_FILLER, K4_SUPPRESS_U,
                                       K4_RETREAT, K4_CHAINING };
static const char *STAGE_NAME[4] = {
    "modify_filler_accent", "suppress_u_long",
    "retreat_acc_nuc", "modify_acc_after_chaining" };

static const uint8_t *g;
static uint32_t rd32(void) { uint32_t v = (uint32_t)g[0] | ((uint32_t)g[1]<<8)
    | ((uint32_t)g[2]<<16) | ((uint32_t)g[3]<<24); g += 4; return v; }
static int32_t rdi32(void) { return (int32_t)rd32(); }
static uint16_t rd16(void) { uint16_t v = (uint16_t)(g[0] | (g[1]<<8)); g += 2; return v; }
static void rdstr(char *out, size_t cap) {
    uint16_t n = rd16();
    size_t k = (n < cap - 1) ? n : cap - 1;
    memcpy(out, g, k); out[k] = 0; g += n;
}

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "k4_vectors.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)sz);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    g = buf;
    if (memcmp(g, "K4V1", 4)) { fprintf(stderr, "NG: magic\n"); return 1; }
    g += 4;
    uint32_t n_cases = rd32(), n_stages = rd32();
    uint32_t n_dan = rd32();
    static k4_dan_t dan[256];
    static char dan_buf[256][8];
    for (uint32_t i = 0; i < n_dan && i < 256; i++) {
        rdstr(dan_buf[i], sizeof dan_buf[i]);
        dan[i].kana = dan_buf[i];
        dan[i].dan = (char)*g++;
    }
    printf("ケース %u / 段 %u / _DAN_MAP %u 件\n", n_cases, n_stages, n_dan);

    static k4_node_t in[MAX_NODES], work[MAX_NODES];
    int ok_all = 0, ng_all = 0;
    int omit_diff[4] = {0,0,0,0}, omit_match[4] = {0,0,0,0}, omit_total[4] = {0,0,0,0};

    for (uint32_t c = 0; c < n_cases; c++) {
        uint32_t nn = rd32();
        if (nn > MAX_NODES) { fprintf(stderr, "NG: ノードが多すぎる %u\n", nn); return 1; }
        for (uint32_t i = 0; i < nn; i++) {
            rdstr(in[i].pos, K4_STR_MAX);   rdstr(in[i].ctype, K4_STR_MAX);
            rdstr(in[i].cform, K4_STR_MAX); rdstr(in[i].orig, K4_STR_MAX);
            rdstr(in[i].pron, K4_PRON_MAX); rdstr(in[i].read, K4_PRON_MAX);
            in[i].acc = rdi32(); in[i].mora_size = rdi32(); in[i].chain_flag = rdi32();
        }
        /* 期待値は 1 + n_stages 組。**全段の期待値を保持して比較に使う。** */
        static int32_t full_acc[MAX_NODES], full_chain[MAX_NODES];
        static char full_pron[MAX_NODES][K4_PRON_MAX];
        for (uint32_t grp = 0; grp <= n_stages; grp++) {
            unsigned mask = K4_ALL;
            if (grp > 0) mask &= ~STAGE_BIT[grp - 1];
            memcpy(work, in, sizeof(k4_node_t) * nn);
            k4_apply(work, (int)nn, mask, dan, (int)n_dan);

            int same_exp = 1;      /* C が期待値と一致するか */
            int same_full = 1;     /* この期待値は「全段」と同じか */
            for (uint32_t i = 0; i < nn; i++) {
                int32_t e_acc = rdi32(), e_chain = rdi32();
                char e_pron[K4_PRON_MAX]; rdstr(e_pron, K4_PRON_MAX);
                if (work[i].acc != e_acc || work[i].chain_flag != e_chain
                    || strcmp(work[i].pron, e_pron)) same_exp = 0;
                if (grp == 0) {
                    full_acc[i] = e_acc; full_chain[i] = e_chain;
                    memcpy(full_pron[i], e_pron, sizeof e_pron);
                } else if (e_acc != full_acc[i] || e_chain != full_chain[i]
                           || strcmp(e_pron, full_pron[i])) {
                    same_full = 0;
                }
            }
            if (grp == 0) { if (same_exp) ok_all++; else ng_all++; }
            else {
                omit_total[grp - 1]++;
                if (same_exp) omit_match[grp - 1]++;
                if (!same_full) omit_diff[grp - 1]++;   /* 段が効いた文 */
            }
        }
    }

    printf("\n=== G12: 4 段の適用が Python 版と一致 ===\n");
    printf("  %s %d / %u 文\n", (ng_all == 0 && ok_all > 0) ? "OK " : "NG ",
           ok_all, n_cases);

    printf("\n=== G13: 陰性対照（1 段抜くと結果が変わるか）===\n");
    int bad13 = 0, n_effective = 0;
    for (uint32_t k = 0; k < n_stages; k++) {
        const char *note = omit_diff[k] ? "" :
            "   ⚠️ **この corpus では効かない。陰性対照は空虚**";
        printf("  %-28s 抜くと変わる %3d 文 / C が一致 %d / %d%s\n",
               STAGE_NAME[k], omit_diff[k], omit_match[k], omit_total[k], note);
        if (omit_match[k] != omit_total[k]) bad13 = 1;
        if (omit_diff[k]) n_effective++;
    }
    printf("  → 判別力のある段: %d / %u\n", n_effective, n_stages);
    printf("  ⚠️ 見ていないもの: **段の順序**。retreat と chaining を入れ替えても\n");
    printf("     held-out 1,200 文で結果が変わらない（Python 側で実測）\n");
    if (n_effective == 0) { printf("  NG  **全段が空虚。ゲートが何も見ていない**\n"); bad13 = 1; }

    free(buf);
    int bad = (ng_all != 0) || (ok_all == 0) || bad13;
    printf("\n%s\n", bad ? "NG!" : "OK  G12 / G13 通過");
    return bad;
}
