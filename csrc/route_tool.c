/* 端末の経路判定（`saan_g2p_classify`）をホストから駆動する棒。
 *
 *   ./route_tool < records
 *
 * ⚠️ **これは「ホストにもう 1 つ判定を書く」の逆。** ホスト側 Python
 *    (`kana_g2p.classify_route`) と端末側 C の一致を測るために、
 *    **端末が実際に走らせるコードそのもの**をホストで動かす。判定を写した
 *    第 3 の実装を作ると、写し間違いごと「一致」してしまう。
 *
 * 入力（**行ではなく長さ前置き**。中間表現に改行は入らないが、不正な UTF-8 や
 * NUL を含む行も同じ経路で測れるようにするため）:
 *
 *     <10 進のバイト数>\n<そのバイト数ぶんの生バイト>   … を繰り返す
 *
 * 出力（1 レコード 1 行）:
 *
 *     kana|dict|reject <err_byte>
 *
 * 使うのは `scripts/k1/kb_route_parity.py` だけ。**受け入れゲートは
 * `make -C csrc line` の G11**（自己完結ベクタ + 陽性対照）で、こちらは
 * ホストとの一致を測るための入出力にすぎない。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "g2p.h"

#define MAX_REC (1 << 20)

int main(void) {
    static char buf[MAX_REC];
    char len_line[64];

    while (fgets(len_line, sizeof len_line, stdin)) {
        char *end = NULL;
        long n = strtol(len_line, &end, 10);
        size_t got;
        saan_g2p_status why = SAAN_G2P_OK;
        int32_t eb = -1;
        saan_g2p_route r;

        if (end == len_line || n < 0 || n > MAX_REC) {
            fprintf(stderr, "route_tool: 長さが読めない: %s", len_line);
            return 2;
        }
        got = (n > 0) ? fread(buf, 1, (size_t)n, stdin) : 0u;
        if (got != (size_t)n) {
            fprintf(stderr, "route_tool: 本体が %zu B しか読めない（%ld B 必要）\n", got, n);
            return 2;
        }
        r = saan_g2p_classify(buf, (size_t)n, &why, &eb);
        printf("%s %d\n",
               r == SAAN_G2P_ROUTE_KANA ? "kana"
                 : r == SAAN_G2P_ROUTE_DICT ? "dict" : "reject",
               (int)eb);
    }
    return 0;
}
