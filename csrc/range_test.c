/* 出力範囲つきカーネル（S9 / T2）の受け入れゲート — `make -C csrc range`
 *
 *   G-R1  fp32   saan_conv1d_r / saan_dwconv1d_r     が [0,T) 版の列 [t0,t1) と bit 一致
 *   G-R2  W8A32  saan_conv1d_i8_r / saan_dwconv1d_i8_r  同上
 *   G-R3  W8A8   saan_conv1d_i8a_r / saan_dwconv1d_i8a_r 同上（量子化は参照フレームだけ）
 *   G-LN  saan_layernorm_c の結果が **T（ストライド）に依らない**
 *         同じ列を T=1 の連続配列と T>1 のストライド配列で正規化して bit 一致
 *
 * なぜ要るか: ストリーミングは S9 で「要る列だけ」を圧縮して計算する。範囲版が [0,T) 版と
 * 1 ulp でもずれると stream G2（一括版との memcmp）が落ちるが、G2 は held-out 24 文の
 * 形（cin / cout / T / 範囲）しか通らない。ここはランダムな形を数千通り通す。
 *
 * ⚠️ G-LN は**実際に踏んだ壊れ方**（2026-09-03）: clang は c ループを T == 1 のときだけ
 *    ベクトル化し、その経路では `var += d*d` が融合されない（fmul + fadd）。ストライド経路は
 *    fmadd。同じ列でも T=1 と T>1 で最終ビットが違い、token block の最終段が 1 列になる文
 *    （1 チャンクが 1 トークンに収まる）だけ G2 が落ちた。列を連続の局所配列に写して直した。
 *
 * ⚠️ **陽性対照つき。** 各項目で「1 列ずらした比較」が必ず不一致になることを同時に見る
 *    （memcmp が空虚に通っていないことの証明。長さ 0 の比較や同じバッファ同士の比較を防ぐ）。
 *
 *   cc -std=c99 -O2 -o range_test range_test.c saanotts.c saanotts_stream.c \
 *       saanotts_int8.c fft.c -lm && ./range_test
 */
#include "saanotts_internal.h"
#include "saanotts_int8.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TRIALS 2000

static unsigned g_seed = 12345u;
static unsigned rnd_u(void) { g_seed = g_seed * 1103515245u + 12345u; return (g_seed >> 8) & 0xffffffu; }
static int rnd_i(int lo, int hi) { return lo + (int)(rnd_u() % (unsigned)(hi - lo + 1)); }   /* [lo, hi] */
static float rnd_f(void) { return (float)rnd_u() / 16777216.0f * 2.0f - 1.0f; }

/* 圧縮出力 y_r [cout][t1-t0] と [0,T) 版 y_f [cout][T] の列 [t0,t1) を比べる。
 * 戻り値: 0 = 一致 / 1 = 不一致。`shift` を 1 にすると y_f の列を 1 つずらして比べる（陽性対照） */
static int cmp_cols(const float *y_f, const float *y_r, int cout, int T, int t0, int t1, int shift) {
    const int Ty = t1 - t0;
    for (int o = 0; o < cout; ++o) {
        const int s0 = t0 + shift;
        if (s0 + Ty > T) return 1;            /* ずらすと窓の外 = 「違う」扱い */
        if (memcmp(y_f + (size_t)o * T + s0, y_r + (size_t)o * Ty, sizeof(float) * (size_t)Ty)) return 1;
    }
    return 0;
}

typedef struct { int bad, ctl_ok, ctl_n; } tally;

static void note(tally *t, int mismatch, int ctl_mismatch, int ctl_valid) {
    t->bad += mismatch;
    if (ctl_valid) { ++t->ctl_n; t->ctl_ok += ctl_mismatch; }
}

static int report(const char *name, const tally *t, int trials) {
    const int ok = t->bad == 0 && t->ctl_n > 0 && t->ctl_ok == t->ctl_n;
    printf("  %s %-6s 範囲版 vs [0,T) 版 bit 一致 %d/%d 試行 / 陽性対照（1 列ずらすと不一致）%d/%d\n",
           ok ? "OK " : "NG!", name, trials - t->bad, trials, t->ctl_ok, t->ctl_n);
    return !ok;
}

int main(void) {
    int bad = 0;
    printf("S9 範囲版カーネルの受け入れゲート（%d 試行 × 3 経路 + LN）\n", TRIALS);

    /* --- G-R1 fp32 conv / dwconv --- */
    {
        tally tc = {0, 0, 0}, td = {0, 0, 0};
        for (int n = 0; n < TRIALS; ++n) {
            const int cin = rnd_i(1, 64), cout = rnd_i(1, 64);
            const int ksz = (rnd_u() % 3 == 0) ? 1 : (rnd_u() % 2 ? 5 : 7);
            const int T = rnd_i(1, 40);
            const int t0 = rnd_i(0, T - 1), t1 = rnd_i(t0 + 1, T);
            const int Ty = t1 - t0;
            float *x = malloc(sizeof(float) * (size_t)cin * T);
            float *W = malloc(sizeof(float) * (size_t)cout * cin * ksz);
            float *b = malloc(sizeof(float) * (size_t)cout);
            float *yf = malloc(sizeof(float) * (size_t)cout * T);
            float *yr = malloc(sizeof(float) * (size_t)cout * Ty);
            for (int i = 0; i < cin * T; ++i) x[i] = rnd_f();
            for (int i = 0; i < cout * cin * ksz; ++i) W[i] = (rnd_u() % 5 == 0) ? 0.0f : rnd_f();
            for (int i = 0; i < cout; ++i) b[i] = rnd_f();
            saan_conv1d(yf, x, W, b, cin, cout, ksz, T);
            saan_conv1d_r(yr, x, W, b, cin, cout, ksz, T, t0, t1);
            note(&tc, cmp_cols(yf, yr, cout, T, t0, t1, 0), cmp_cols(yf, yr, cout, T, t0, t1, 1), t1 < T);
            /* depthwise: W は [ch][ksz]、ch = cin */
            float *Wd = malloc(sizeof(float) * (size_t)cin * ksz);
            float *zf = malloc(sizeof(float) * (size_t)cin * T);
            float *zr = malloc(sizeof(float) * (size_t)cin * Ty);
            for (int i = 0; i < cin * ksz; ++i) Wd[i] = rnd_f();
            saan_dwconv1d(zf, x, Wd, cin, ksz, T);
            saan_dwconv1d_r(zr, x, Wd, cin, ksz, T, t0, t1);
            note(&td, cmp_cols(zf, zr, cin, T, t0, t1, 0), cmp_cols(zf, zr, cin, T, t0, t1, 1), t1 < T);
            free(x); free(W); free(b); free(yf); free(yr); free(Wd); free(zf); free(zr);
        }
        bad += report("G-R1", &tc, TRIALS);
        bad += report("G-R1dw", &td, TRIALS);
    }

    /* --- G-R2 / G-R3 int8（W8A32 と W8A8）--- */
    {
        tally t32 = {0, 0, 0}, t32d = {0, 0, 0}, t8 = {0, 0, 0}, t8d = {0, 0, 0};
        for (int n = 0; n < TRIALS; ++n) {
            const int cin = rnd_i(1, 64), cout = rnd_i(1, 64);
            const int ksz = (rnd_u() % 3 == 0) ? 1 : (rnd_u() % 2 ? 5 : 7);
            const int T = rnd_i(1, 40);
            const int t0 = rnd_i(0, T - 1), t1 = rnd_i(t0 + 1, T);
            const int Ty = t1 - t0;
            const int cinp = SAAN_W_STRIDE(cin);
            float *x = malloc(sizeof(float) * (size_t)cin * T);
            int8_t *q = malloc((size_t)cout * cin * ksz);
            int8_t *qp = malloc(saan_packed_w_bytes(cout, cin, ksz) + 16);
            int8_t *qpa = (int8_t *)(((uintptr_t)qp + 15u) & ~(uintptr_t)15u);   /* 16 B 境界 */
            float *sc = malloc(sizeof(float) * (size_t)cout);
            float *b = malloc(sizeof(float) * (size_t)cout);
            float *yf = malloc(sizeof(float) * (size_t)cout * T);
            float *yr = malloc(sizeof(float) * (size_t)cout * Ty);
            int8_t *qx = malloc((size_t)cinp * T);
            float *sx = malloc(sizeof(float) * (size_t)T);
            for (int i = 0; i < cin * T; ++i) x[i] = rnd_f();
            for (int i = 0; i < cout * cin * ksz; ++i) q[i] = (int8_t)((rnd_u() % 5 == 0) ? 0 : rnd_i(-127, 127));
            saan_pack_w_i8(qpa, q, cout, cin, ksz);
            for (int i = 0; i < cout; ++i) { sc[i] = 0.001f + (float)rnd_u() / 16777216.0f; b[i] = rnd_f(); }
            /* W8A32 */
            saan_conv1d_i8(yf, x, qpa, sc, b, cin, cout, ksz, T);
            saan_conv1d_i8_r(yr, x, qpa, sc, b, cin, cout, ksz, T, t0, t1);
            note(&t32, cmp_cols(yf, yr, cout, T, t0, t1, 0), cmp_cols(yf, yr, cout, T, t0, t1, 1), t1 < T);
            /* W8A8。⚠️ 範囲版は参照するフレームしか量子化しないので、qx / sx を毎回ゴミで満たしてから
             * 呼ぶ（残骸を読んでいれば bit 一致しない = 「範囲外を読まない」ことの検査でもある） */
            saan_conv1d_i8a(yf, x, qpa, sc, b, cin, cout, ksz, T, qx, sx);
            memset(qx, 0x5a, (size_t)cinp * T);
            for (int i = 0; i < T; ++i) sx[i] = 12345.0f;
            saan_conv1d_i8a_r(yr, x, qpa, sc, b, cin, cout, ksz, T, t0, t1, qx, sx);
            note(&t8, cmp_cols(yf, yr, cout, T, t0, t1, 0), cmp_cols(yf, yr, cout, T, t0, t1, 1), t1 < T);
            /* depthwise（W は [ch][ksz]、ストライド 1。ch = cin） */
            int8_t *qd = malloc((size_t)cin * ksz);
            float *scd = malloc(sizeof(float) * (size_t)cin);
            float *zf = malloc(sizeof(float) * (size_t)cin * T);
            float *zr = malloc(sizeof(float) * (size_t)cin * Ty);
            const int chp = (int)SAAN_ALIGN16((size_t)cin);
            int8_t *qxd = malloc((size_t)chp * T);
            for (int i = 0; i < cin * ksz; ++i) qd[i] = (int8_t)rnd_i(-127, 127);
            for (int i = 0; i < cin; ++i) scd[i] = 0.001f + (float)rnd_u() / 16777216.0f;
            saan_dwconv1d_i8(zf, x, qd, scd, cin, ksz, T);
            saan_dwconv1d_i8_r(zr, x, qd, scd, cin, ksz, T, t0, t1);
            note(&t32d, cmp_cols(zf, zr, cin, T, t0, t1, 0), cmp_cols(zf, zr, cin, T, t0, t1, 1), t1 < T);
            saan_dwconv1d_i8a(zf, x, qd, scd, cin, ksz, T, qxd, sx);
            memset(qxd, 0x5a, (size_t)chp * T);
            for (int i = 0; i < T; ++i) sx[i] = 12345.0f;
            saan_dwconv1d_i8a_r(zr, x, qd, scd, cin, ksz, T, t0, t1, qxd, sx);
            note(&t8d, cmp_cols(zf, zr, cin, T, t0, t1, 0), cmp_cols(zf, zr, cin, T, t0, t1, 1), t1 < T);
            free(x); free(q); free(qp); free(sc); free(b); free(yf); free(yr); free(qx); free(sx);
            free(qd); free(scd); free(zf); free(zr); free(qxd);
        }
        bad += report("G-R2", &t32, TRIALS);
        bad += report("G-R2dw", &t32d, TRIALS);
        bad += report("G-R3", &t8, TRIALS);
        bad += report("G-R3dw", &t8d, TRIALS);
    }

    /* --- G-LN LayerNorm の T 非依存 --- */
    {
        int nbad = 0, nctl = 0;
        const int C = SAAN_AC_W;
        for (int n = 0; n < TRIALS; ++n) {
            const int T = rnd_i(2, 40), t = rnd_i(0, T - 1);
            float *x = malloc(sizeof(float) * (size_t)C * T);
            float *g = malloc(sizeof(float) * (size_t)C), *b = malloc(sizeof(float) * (size_t)C);
            float col[SAAN_AC_W], other[SAAN_AC_W];
            for (int i = 0; i < C * T; ++i) x[i] = rnd_f() * 3.0f;
            for (int c = 0; c < C; ++c) { g[c] = rnd_f(); b[c] = rnd_f(); }
            const int t2 = (t + 1) % T;                      /* 別の列（陽性対照） */
            for (int c = 0; c < C; ++c) { col[c] = x[(size_t)c * T + t]; other[c] = x[(size_t)c * T + t2]; }
            saan_layernorm_c(x, g, b, C, T);                 /* ストライド T */
            saan_layernorm_c(col, g, b, C, 1);               /* 連続（T=1） */
            saan_layernorm_c(other, g, b, C, 1);
            for (int c = 0; c < C; ++c) if (x[(size_t)c * T + t] != col[c]) { ++nbad; break; }
            int diff = 0;
            for (int c = 0; c < C; ++c) if (x[(size_t)c * T + t] != other[c]) { diff = 1; break; }
            nctl += diff;
            free(x); free(g); free(b);
        }
        const int ok = nbad == 0 && nctl == TRIALS;
        printf("  %s G-LN   LayerNorm が T に依らない（T=1 の連続列 vs ストライド T）%d/%d 一致 / "
               "陽性対照（隣の列と比べると不一致）%d/%d\n", ok ? "OK " : "NG!", TRIALS - nbad, TRIALS, nctl, TRIALS);
        printf("      ⚠️ 2026-09-03 に落ちた形（列を局所配列に写す前）: 500 試行中 71 で 1 ulp 違った\n");
        bad += !ok;
    }

    printf("%s\n", bad ? "NG: 範囲版カーネルが [0,T) 版と一致しない" : "OK: 範囲版カーネルは [0,T) 版と bit 一致（陽性対照も効いている）");
    return bad ? 1 : 0;
}
