/* W8A8 のパディング部が**本当に 0 で埋まっているか**を検査する（G-1）。
 *
 * ## なぜ素直なテストでは捕まらないのか — **実際に踏んだ**
 *
 * 活性化のストライドを `align16(cin)` にした結果、`[cin, cinp)` の隙間ができた。
 * ここを 0 で埋め忘れると、**前の層の残骸が内積に混ざる**（`saan_conv1d_w` は
 * arena を `a->used = mark` で即返すので、次の conv が同じ番地を再利用する）。
 * 例外も NaN も出ず、値だけが静かにずれる。
 *
 * ⚠️ **最初に書いたゲートは空虚だった。** 活性化のゼロ埋めを消しても PASS した。
 * 隙間の寄与は
 *
 *     Σ_{i ∈ [cin, cinp)} w_pad[i] · a_pad[i]
 *
 * で、**片方が 0 なら他方がゴミでも 0** だから。重み側が 0 で埋まっている限り、
 * 活性化側を壊しても出力は 1 ビットも変わらない。
 * 独立なスカラ参照を書いても同じ（参照側も `i < cin` までしか読まない）。
 * arena を 0xAA で汚して golden test を回しても同じ。
 *
 * ## 正しい設計: **相手側を非ゼロにしたうえで「変わらないこと」を見る**
 *
 * 検査専用のフックを置いた（既定は 0 = 本番の挙動）:
 *   `-DSAAN_PAD_POISON_W=1` … 重みの隙間を 127 で埋める（blob v2 では `saan_pack_w_i8` が埋める。
 *                             本番の blob は exporter が 0 で書く）
 *   `-DSAAN_PAD_POISON_A=1` … 活性化 `qx` の隙間を 127 で埋める
 *
 * | ビルド | 期待 | 証明されること |
 * |---|---|---|
 * | 毒なし | 基準 | — |
 * | **W だけ毒** | 基準と**一致** | **活性化側**のゼロ埋めが効いている |
 * | **A だけ毒** | 基準と**一致** | **重み側**のゼロ埋めが効いている |
 * | **両方毒** | 基準と**相違** | **陽性対照** — 隙間が実際に読まれている |
 *
 * 最後の 1 行が無いと、上の「一致」が「読んでいないから一致」と区別できない
 * （C-028 と同型）。
 *
 * この実行ファイルは**出力のチェックサムを 1 行で出すだけ**。
 * 4 通りのビルドを回して比べるのは `make -C csrc pad` の仕事。
 */
#include "saanotts_int8.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define AL16 __attribute__((aligned(16)))
#define MAXC 320
#define MAXT 32

static uint32_t rs = 987654321u;
static float rndf(void) {
    rs = rs * 1664525u + 1013904223u;
    return (float)((int32_t)(rs >> 8) % 2001 - 1000) / 1000.0f;
}

static AL16 int8_t qbuf[MAXC * MAXT];
static float sxbuf[MAXT];
static float xin[MAXC * MAXT];
static float yout[MAXC * MAXT];
static AL16 int8_t qw[MAXC * MAXC];
static AL16 int8_t qwp[MAXC * MAXC];   /* blob v2 のレイアウト [cout][k][cinp] */
static float wf[MAXC * MAXC];
static float wsc[MAXC];

/* 出力の生ビットを畳み込む。float の等値比較を避けるため memcpy 経由 */
static uint64_t fold(uint64_t h, const float *v, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        uint32_t b;
        memcpy(&b, &v[i], sizeof b);
        h ^= b + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
    }
    return h;
}

/* 隙間を持つ形状と、持たない形状の両方を回す。
 * ⚠️ **隙間を持つ形状が 1 つも無いと検査が成立しない**ので必ず混ぜること。 */
static const struct { const char *tag; int cin, cout, ksz; } SHAPES[] = {
    { "dec inp   40->48",   40,  76, 3 },
    { "dec pw1   76->80",   76,  96, 1 },
    { "dec cup   12->16",   12,  76, 1 },
    { "dec hdown 76->80",   76,  48, 1 },
    { "dec cdown 40->48",   40,  12, 1 },
    { "整列済み  48",       48,  64, 1 },
    { "整列済み  48 k5",    48,  48, 5 },
    { "整列済み  304",     304,  32, 1 },
};
#define NSHAPE ((int)(sizeof SHAPES / sizeof SHAPES[0]))

int main(void) {
    uint64_t h = 1469598103934665603ull;
    int n_pad = 0;
    for (int si = 0; si < NSHAPE; ++si) {
        const int cin = SHAPES[si].cin, cout = SHAPES[si].cout;
        const int ksz = SHAPES[si].ksz, T = MAXT;
        const int cinp = (int)((cin + 15) & ~15);
        if (cinp != cin) ++n_pad;
        rs = 987654321u + (uint32_t)si * 7919u;
        for (int i = 0; i < cin * T; ++i) xin[i] = rndf();
        const int inner = cin * ksz;
        for (int o = 0; o < cout; ++o)
            for (int i = 0; i < inner; ++i) wf[(size_t)o * inner + i] = rndf();
        saan_quantize_w_i8(qw, wsc, wf, cout, inner);
        saan_pack_w_i8(qwp, qw, cout, cin, ksz);   /* 毒（SAAN_PAD_POISON_W）はここで入る */
        /* ⚠️ **arena の使い回しを模す。** 毎回 0 で初期化すると
         * 「隙間がたまたま 0 だった」だけで通ってしまう */
        memset(qbuf, 0x5a, sizeof qbuf);
        saan_conv1d_i8a(yout, xin, qwp, wsc, NULL, cin, cout, ksz, T, qbuf, sxbuf);
        h = fold(h, yout, (size_t)cout * T);
    }
    printf("shapes=%d padded=%d poison_w=%d poison_a=%d checksum=%016llx\n",
           NSHAPE, n_pad, SAAN_PAD_POISON_W, SAAN_PAD_POISON_A,
           (unsigned long long)h);
    return n_pad > 0 ? 0 : 1;   /* 隙間を持つ形状が無ければ検査が空虚 */
}
