/* (1) win の対称性が **bit** で成り立つか（成り立てば半分に折れて PCM は不変）
 * (2) Viterbi の cap と KEY_MAX の整合
 * (3) accent_node_t をポインタ化したときの寸法（32 bit ターゲット相当も計算）
 */
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdint.h>
#include "accent.h"
#include "jdict.h"

#define NFFT 1024
int main(void) {
    static float win[NFFT];
    for (int i = 0; i < NFFT; ++i)
        win[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)NFFT);
    int diff = 0, worst_i = -1; float worst = 0.0f;
    for (int i = 1; i < NFFT / 2; ++i) {
        if (memcmp(&win[i], &win[NFFT - i], sizeof(float)) != 0) {
            ++diff;
            float d = fabsf(win[i] - win[NFFT - i]);
            if (d > worst) { worst = d; worst_i = i; }
        }
    }
    printf("== (1) win の対称性 win[i] == win[1024-i] ==\n");
    printf("違う組 %d / 511", diff);
    if (diff) printf("（最大 |Δ| %.6g @ i=%d / %.8f vs %.8f）", worst, worst_i,
                     win[worst_i], win[NFFT - worst_i]);
    printf("\n折れる: %s -> %s\n\n", diff ? "**できない**" : "できる（bit 同一）",
           diff ? "半分に折ると PCM が変わる" : "4096 -> 2064 B / PCM 不変");

    printf("== (2) Viterbi の容量 ==\n");
    printf("sizeof(vnode の実質) = 24 + int32 x 2 = 32 B/node（jdict.c の cap 式）\n");
    const unsigned KEY_MAX = 1024, VIT = 48u * 1024u;
    printf("VITERBI_N = %u -> cap = %u node\n", VIT, VIT / 32);
    printf("必要条件 (key_n+1)*2 <= cap -> 受けられる key_n の上限 = %u\n", VIT / 32 / 2 - 1);
    printf("KEY_MAX = %u なので、key_n が %u..%u の入力は arena が下限ぴったりだと\n",
           KEY_MAX, VIT / 32 / 2, KEY_MAX);
    printf("  jdict_analyze が -1（= SAAN_KANJI_ERR_ANALYZE「経路が張れない」）を返す\n");
    printf("KEY_MAX を全部受けるのに要る VITERBI_N = %u B（+%u）\n\n",
           64u * (KEY_MAX + 1), 64u * (KEY_MAX + 1) - VIT);

    printf("== (3) accent_node_t ==\n");
    printf("現状 sizeof = %zu B（このホスト）/ 524 B（式 4*64 + 2*128 + 3*4）\n",
           sizeof(accent_node_t));
    printf("x 96 = %u B\n", 524u * 96u);
    printf("pos/ctype/cform/orig/read を const char* に（accent.c は読むだけ）:\n");
    printf("  32 bit ターゲット: 5*4 + 128 + 3*4 = %u B -> x96 = %u B（-%u）\n",
           5u*4+128+12, (5u*4+128+12)*96, 524u*96u - (5u*4+128+12)*96u);
    return 0;
}
