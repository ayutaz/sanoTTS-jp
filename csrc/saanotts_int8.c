/* sanoTTS-jp int8 カーネル。詳細は saanotts_int8.h */
#include "saanotts_int8.h"

#include "saanotts_internal.h"
#include "saan_prof.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define I8_NAME_LEN 64

/* ⚠️ かつてここに「重みを [k][cin] へ並べ替えるスタック領域 wt（512 B）」があった。
 *    blob v2（S4）で重みが最初から [cout][k][cinp] なので要らなくなった。 */

/* --- PIE（ESP32-S3 の 128-bit 整数 SIMD） --------------------------------
 *
 * `ee.vmulas.s8.accx` が **16 レーンの int8 積和を 40-bit アキュムレータに**溜める。
 * これが W8A8 の内積そのものなので、そのまま置き換えられる。
 *
 * ⚠️ **GCC は `-O2` でも PIE へ自動ベクトル化しない**（M-53 で逆アセンブル確認済み。
 * W8A8 の int32 積和ループでも `ee.*` は 0 件）。手で書くしかない。
 *
 * ⚠️ **`ee.vld.128.ip` は 16 バイト境界を要求する。** そのため
 *   - 重みは blob v2 で最初から **[cout][k][cinp]** に並べてある（S4。exporter が 0 埋め）。
 *     実行時の転置コピーは無い
 *   - activation の行ストライドを **`cinp = align16(cin)`** に広げ、
 *     隙間 `[cin, cinp)` を **0 で埋める**（0 は積和に寄与しない = 端数処理も不要）
 * これで **MAC の 99.40%** を覆う（M-58）。
 * ⚠️ **depthwise の 0.60% だけは原理的に載らない** — `qx[u*ch + o]` は
 * チャネル方向のギャザーで、内積命令では表現できない（C-035）。
 *
 * 正しさは **QEMU で検証できる**（M-56 / `esp32/pie_probe`）。
 * ⚠️ **速度は測れない** — QEMU はサイクル精度ではない。実機が要る。
 */
/* --- 検査用の「毒」フック（既定 0）------------------------------------------
 * パディング部を 0 ではなく 127 で埋めるビルドを作るためのもの。**本番では 0。**
 *
 * ⚠️ **なぜ要るか**: 隙間の寄与は `Σ w_pad · a_pad` なので、
 * **片方が 0 なら他方がゴミでも出力は変わらない**。したがって
 * 「片方のゼロ埋めを外して出力が変わるか」では検出できない（実際に踏んだ）。
 * **相手側を非ゼロにしたうえで**「出力が変わらないこと」を見るしかない。
 *   - `SAAN_PAD_POISON_W=1` で重み側を汚す → **活性化側**のゼロ埋めを証明する
 *   - `SAAN_PAD_POISON_A=1` で活性化側を汚す → **重み側**のゼロ埋めを証明する
 *   - 両方 1 にすると**出力が変わらなければならない理由が無くなる** = 陽性対照
 * 詳細は `csrc/int8_pad_test.c`。 */
#if defined(__XTENSA__) && defined(SAAN_PIE) && SAAN_PIE
#define SAAN_HAVE_PIE 1
#define SAAN_AL16 __attribute__((aligned(16)))
#else
#define SAAN_HAVE_PIE 0
#define SAAN_AL16
#endif

/* `a` と `b` の内積。**`n` は 16 の倍数、両ポインタは 16 バイト境界**であること。
 * 呼び出し側が保証する（`saan_pie_ok()` で判定）。
 *
 * S5a（2026-09-02）: 16 MAC あたり **5 命令 → 2 命令**。
 *   - `ee.vmulas.s8.accx.ld.ip qu, as, 16, qx, qy` は「qx·qy を accx に足す」と
 *     「qu ← [as], as += 16」を 1 命令でやる。qu == qx にしても積和は**ロード前の** qx を使う
 *   - `loopnez` は Xtensa のゼロオーバーヘッドループ（`__XCHAL_HAVE_LOOPS`）。addi + bnez が消える
 *   - 最後の 1 組だけ併合しない `ee.vmulas.s8.accx` で締める。**併合形で回し切ると
 *     配列の 16 B 先を 1 回読む**（arena や blob の端では未マップ領域に触りうる）
 *   - k == 1（cinp = 16。cup 12→16）は `loopnez` の回数が 0 で本体を飛ばし、締めの 1 命令だけ走る
 * 整数演算なので旧ループと **bit 同一**（esp32/pie_probe の B 節 7 形状 + QEMU の checksum で確認）。
 * ⚠️ QEMU が併合形の意味論を実機と同じに実装しているかは、実機の checksum が一致するかで分かる。 */
#if SAAN_HAVE_PIE
static int32_t saan_dot_i8_pie(const int8_t *a, const int8_t *b, int n) {
    int32_t out = 0;
    const int8_t *pa = a, *pb = b;
    const int k = n >> 4;
    if (k <= 0) return 0;      /* ⚠️ 無いと締めの 1 組が未初期化の q0/q1 を掛ける */
    const int km1 = k - 1;
    __asm__ volatile(
        "ee.zero.accx                                   \n"
        "ee.vld.128.ip q0, %[pa], 16                    \n"
        "ee.vld.128.ip q1, %[pb], 16                    \n"
        "loopnez %[km1], 1f                             \n"
        "  ee.vmulas.s8.accx.ld.ip q0, %[pa], 16, q0, q1\n"
        "  ee.vld.128.ip q1, %[pb], 16                  \n"
        "1:                                             \n"
        "ee.vmulas.s8.accx q0, q1                       \n"
        "ee.srs.accx %[out], %[sh], 0                   \n"
        : [out] "=&a"(out), [pa] "+&a"(pa), [pb] "+&a"(pb)
        : [km1] "a"(km1), [sh] "a"(0)
        : "memory");
    return out;
}
#endif

/* この (cin, W) で PIE を使ってよいか。**整列条件をここ 1 箇所で判定する**。
 *
 * 活性化も重みも **`cinp = align16(cin)` のストライド**に揃えたので、
 * `cin` の 16 倍数性はもう要らない。残る条件は
 *   - `cin > 0`（⚠️ `saan_dot_i8_pie` は `k <= 0` を守っていない。ここが唯一の砦）
 *   - `W` が 16 B 境界（blob のテンソル offset は 16 の倍数。テストの配列は aligned(16)）
 * ⚠️ **depthwise は対象外。** `saan_dwconv1d_i8a` はチャネル方向のギャザーで、
 * `ee.vmulas.s8.accx`（16 レーンを 1 アキュムレータに畳む内積）では表現できない。
 * ストライドを揃えても載らない（MAC の 0.60%。C-035）。 */
static int saan_pie_ok(int cin, const int8_t *W) {
#if SAAN_HAVE_PIE
    return cin > 0 && (((uintptr_t)W & 15u) == 0u);
#else
    (void)cin; (void)W;
    return 0;
#endif
}

/* --- v2 レイアウトへの並べ替え（テスト / 毒テスト用。本番は exporter が書く）--------- */

size_t saan_packed_w_bytes(int cout, int cin, int ksz) {
    return (size_t)cout * (size_t)ksz * (size_t)SAAN_W_STRIDE(cin);
}

void saan_pack_w_i8(int8_t *dst, const int8_t *q, int cout, int cin, int ksz) {
    const int cinp = SAAN_W_STRIDE(cin);
    for (int o = 0; o < cout; ++o)
        for (int k = 0; k < ksz; ++k) {
            int8_t *row = dst + ((size_t)o * ksz + k) * cinp;
            for (int i = 0; i < cin; ++i) row[i] = q[((size_t)o * cin + i) * ksz + k];
            /* ⚠️ 本番 0。毒テスト（SAAN_PAD_POISON_W）はここで 127 にする。
             *    blob v2 の padding は exporter が 0 で書くので、本番経路にこのコードは無い */
            for (int i = cin; i < cinp; ++i) row[i] = (int8_t)SAAN_PAD_FILL_W;
        }
}

/* --- 量子化 -------------------------------------------------------------- */

void saan_quantize_w_i8(int8_t *q, float *scale, const float *W,
                        int cout, int inner) {
    for (int o = 0; o < cout; ++o) {
        const float *wo = W + (size_t)o * inner;
        float amax = 0.0f;
        for (int i = 0; i < inner; ++i) {
            const float v = fabsf(wo[i]);
            if (v > amax) amax = v;
        }
        /* 全ゼロ行は scale = 1（0 割りを避ける。Python 側と同じ規則） */
        /* ⚠️ 丸めは **rintf = half-to-even**。torch.round と同じ。roundf
         * (half-away-from-zero) にすると実測で 544,292 値のうち 5 個が
         * exporter と食い違う（int8_test の 2c が検出する） */
        const float s = (amax == 0.0f) ? 1.0f : amax / 127.0f;
        scale[o] = s;
        int8_t *qo = q + (size_t)o * inner;
        for (int i = 0; i < inner; ++i) {
            float v = rintf(wo[i] / s);
            if (v > 127.0f) v = 127.0f;
            if (v < -127.0f) v = -127.0f;
            qo[i] = (int8_t)v;
        }
    }
}

void saan_quantize_act_i8p(int8_t *q, float *sx, const float *x, int C, int T,
                           int P) {
    SAAN_PROF_BEGIN(SAAN_PROF_QUANT);
    for (int t = 0; t < T; ++t) {
        float amax = 0.0f;
        for (int c = 0; c < C; ++c) {
            const float v = fabsf(x[(size_t)c * T + t]);
            if (v > amax) amax = v;
        }
        int8_t *qt = q + (size_t)t * P;
        /* ⚠️ **パディング部を毎フレーム 0 にする。** ここを外すと
         * 「前の層の活性化のバイトが内積に混ざる」形で**静かに壊れる**:
         * `saan_conv1d_w` は arena を `a->used = mark` で即返すので、
         * 次の conv が**同じ番地を再利用する**。例外も NaN も出ない。
         * ⚠️ **これを検出できるゲートは 1 つしかない** — 相手側（重み）の
         * パディングを非ゼロで汚したうえで bit 一致を見ること。
         * 内積は `Σ w_pad · a_pad` なので、**片方が 0 なら他方がゴミでも答えが変わらず**、
         * 素直な参照比較では捕まらない（`csrc/int8_pad_test.c` の G-1）。 */
        if (P > C) memset(qt + C, SAAN_PAD_FILL_A, (size_t)(P - C));
        if (amax == 0.0f) {
            sx[t] = 0.0f;
            memset(qt, 0, (size_t)C);
            continue;
        }
        const float s = amax / 127.0f;
        sx[t] = s;
        /* ⚠️ **S2: 除算を逆数の乗算に。** ESP32-S3 の FPU に除算は無く、`x / s` は
         *    要素ごとに `__divsf3`（ソフト浮動小数）を呼んでいた（M-80。nm -u で確認）。
         *    `x * (127 / amax)` は `x / (amax / 127)` と**最終ビットが違いうる**ので、
         *    これは「丸め水準」の変更。出力の checksum は変わる（M-62 の値とは一致しない）。
         *    `sx[t]` は従来どおり `amax / 127`（逆量子化の意味は変えない）。
         *    クランプは int で行う（float の 127.0f 比較より安い）。 */
        const float inv = 127.0f / amax;
        for (int c = 0; c < C; ++c) {
            int32_t q = saan_rint_i32(x[(size_t)c * T + t] * inv);
            if (q > 127) q = 127;
            if (q < -127) q = -127;
            qt[c] = (int8_t)q;
        }
    }
    SAAN_PROF_END(SAAN_PROF_QUANT);
    SAAN_PROF_ADD(SAAN_PROF_QUANT, (size_t)C * T);
}

void saan_quantize_act_i8(int8_t *q, float *sx, const float *x, int C, int T) {
    saan_quantize_act_i8p(q, sx, x, C, T, C);   /* パディング無し = 従来の挙動 */
}

size_t saan_act_scratch_bytes(int C, int T) {
    /* ⚠️ **パディング後のストライドで数える。** `C` のまま数えると
     * 呼び出し側が過少確保して隣接バッファを踏む（silent） */
    return (size_t)SAAN_ALIGN16((size_t)C) * (size_t)T * sizeof(int8_t)
         + (size_t)T * sizeof(float);
}

/* --- W8A32 --------------------------------------------------------------- */

void saan_conv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                    const float *b, int cin, int cout, int ksz, int T) {
    const int pad = ksz / 2;
    const int cinp = SAAN_W_STRIDE(cin);   /* blob v2: W[(o*ksz + k)*cinp + i] */
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        /* ⚠️ **ループの順序（i 外 / k 内 / t 最内）は v1 のまま。** float の加算順が変わると
         *    出力が bit 一致しなくなる。変えたのは W の添字だけ */
        for (int i = 0; i < cin; ++i) {
            const float *xi = x + (size_t)i * T;
            const int8_t *wo = W + (size_t)o * ksz * cinp;
            for (int k = 0; k < ksz; ++k) {
                const int qv = wo[(size_t)k * cinp + i];
                if (qv == 0) continue;           /* fp32 版と同じくゼロ枝刈り */
                const float wv = (float)qv;
                const int sh = k - pad;
                const int t0 = sh < 0 ? -sh : 0;
                const int t1 = sh > 0 ? T - sh : T;
                for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
            }
        }
        const float s = scale[o];
        const float bias = b ? b[o] : 0.0f;
        for (int t = 0; t < T; ++t) yo[t] = yo[t] * s + bias;
    }
}

void saan_dwconv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                      int ch, int ksz, int T) {
    const int pad = ksz / 2;
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const float *xi = x + (size_t)o * T;
        const int8_t *wk = W + (size_t)o * ksz;
        for (int t = 0; t < T; ++t) yo[t] = 0.0f;
        for (int k = 0; k < ksz; ++k) {
            const float wv = (float)wk[k];
            const int sh = k - pad;
            const int t0 = sh < 0 ? -sh : 0;
            const int t1 = sh > 0 ? T - sh : T;
            for (int t = t0; t < t1; ++t) yo[t] += wv * xi[t + sh];
        }
        const float s = scale[o];
        for (int t = 0; t < T; ++t) yo[t] *= s;
    }
}

/* --- W8A8 ---------------------------------------------------------------- */

void saan_conv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                     const float *b, int cin, int cout, int ksz, int T,
                     int8_t *qx, float *sx) {
    const int pad = ksz / 2;
    /* 活性化も重みも **`cinp = SAAN_W_STRIDE(cin)` のストライド**（blob v2。S4）。
     * パディング部は 0 なので積和に寄与せず、端数処理が要らない。
     * 重みは blob の中で最初から [cout][k][cinp] に並んでいるので、以前あった
     * スタック `wt` への転置コピー（1 step に 489 KB。M-80 の WCOPY）は無い。 */
    const int cinp = SAAN_W_STRIDE(cin);
    const int pie = saan_pie_ok(cin, W);
    (void)pie;   /* PIE 無しのビルドでは未使用 */

    saan_quantize_act_i8p(qx, sx, x, cin, T, cinp);
    for (int o = 0; o < cout; ++o) {
        float *yo = y + (size_t)o * T;
        const float s = scale[o];
        const float bias = b ? b[o] : 0.0f;
        const int8_t *wo = W + (size_t)o * ksz * cinp;   /* [k][cinp] */
        SAAN_PROF_BEGIN(SAAN_PROF_MAC);
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;   /* 両端ゼロパディング */
                const int8_t *qu = qx + (size_t)u * cinp;
                const int8_t *wk = wo + (size_t)k * cinp;   /* blob 内。16 B 境界 */
                int32_t a32 = 0;
#if SAAN_HAVE_PIE
                if (pie) {
                    /* `wk` も `qx + u*cinp` も 16 整列、`cinp` は 16 の倍数 */
                    a32 = saan_dot_i8_pie(wk, qu, cinp);
                } else
#endif
                {
                    /* ⚠️ 既定は `i < cin`。`SAAN_PIE_EMU=1` のときだけ PIE と
                     * 同じ `cinp` レーンまで回す（パディング検査用。上のヘッダ参照） */
                    const int lanes = SAAN_PIE_EMU ? cinp : cin;
                    for (int i = 0; i < lanes; ++i)
                        a32 += (int32_t)wk[i] * (int32_t)qu[i];
                }
                acc += (float)a32 * sx[u];
            }
            yo[t] = acc * s + bias;
        }
        SAAN_PROF_END(SAAN_PROF_MAC);
        SAAN_PROF_ADD(SAAN_PROF_MAC, (size_t)cin * ksz * T);
    }
}

void saan_dwconv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                       int ch, int ksz, int T, int8_t *qx, float *sx) {
    const int pad = ksz / 2;
    SAAN_PROF_BEGIN(SAAN_PROF_DW);
    /* ⚠️ **depthwise は PIE に載らない**（チャネル方向のギャザーで、
     * `ee.vmulas.s8.accx` の内積では表現できない。C-035）。
     * それでも `saan_quantize_act_i8p` を共有するので**ストライドは追従する**。
     * ここを `ch` のままにすると読み位置がずれて黙って壊れる。 */
    const int chp = (int)SAAN_ALIGN16((size_t)ch);
    saan_quantize_act_i8p(qx, sx, x, ch, T, chp);
    for (int o = 0; o < ch; ++o) {
        float *yo = y + (size_t)o * T;
        const int8_t *wk = W + (size_t)o * ksz;
        const float s = scale[o];
        for (int t = 0; t < T; ++t) {
            float acc = 0.0f;
            for (int k = 0; k < ksz; ++k) {
                const int u = t + k - pad;
                if (u < 0 || u >= T) continue;
                const int32_t p = (int32_t)wk[k] * (int32_t)qx[(size_t)u * chp + o];
                acc += (float)p * sx[u];
            }
            yo[t] = acc * s;
        }
    }
    SAAN_PROF_END(SAAN_PROF_DW);
    SAAN_PROF_ADD(SAAN_PROF_DW, (size_t)ch * ksz * T);
}

/* --- ブロブ -------------------------------------------------------------- */

const int8_t *saan_ti8(const saan_weights *w, const float **scale,
                       const char *fmt, ...) {
    char buf[I8_NAME_LEN];
    char sbuf[I8_NAME_LEN + 8];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);

    uint32_t dt = 0;
    const void *p = saan_tensor(w, buf, &dt, NULL, NULL);
    if (!p || dt != 1u) return NULL;

    snprintf(sbuf, sizeof sbuf, "%s.scale", buf);
    if (scale) {
        uint32_t sdt = 0;
        const void *sp = saan_tensor(w, sbuf, &sdt, NULL, NULL);
        if (!sp || sdt != 2u) return NULL;
        *scale = (const float *)sp;
    }
    return (const int8_t *)p;
}

/* --- fp32 / int8 のディスパッチ（D-3c'-2） --------------------------------
 *
 * ここに置くのは **`saanotts.c` を int8 に依存させない**ため。
 * 一括版もストリーミング版も `saan_conv1d_w` だけを呼べばよく、
 * どちらの経路を通るかは読み込んだブロブの dtype が決める。
 */

static saan_wref saan_w_named(const saan_weights *w, const char *buf) {
    char sbuf[I8_NAME_LEN + 8];
    saan_wref r = {NULL, NULL, NULL, 0};

    uint32_t dt = 0, dims[4] = {0, 0, 0, 0};
    uint64_t nb = 0;
    const void *p = saan_tensor(w, buf, &dt, dims, &nb);
    if (!p) return r;                       /* 名前が無い = 両方 NULL のまま */
    if (dt == 0u) { r.f32 = (const float *)p; return r; }
    if (dt != 1u) return r;                 /* scale(2) を重みとして掴まない */

    /* ⚠️ **v2 のレイアウトを nbytes で検算する。** dims は論理形 (cout, cin, k) のまま
     *    なので、payload が [cout][k][cinp] ぶんあるかを見る。合わなければ「引けなかった」
     *    （= SAAN_ERR_MISSING で止まる。黙って別物の音を出さない）。 */
    {
        const int cout = (int)dims[0], cin = (int)dims[1], ksz = (int)dims[2];
        if (cout <= 0 || cin <= 0 || ksz <= 0) return r;
        if (nb != (uint64_t)saan_packed_w_bytes(cout, cin, ksz)) return r;
        r.cinp = SAAN_W_STRIDE(cin);
    }

    snprintf(sbuf, sizeof sbuf, "%s.scale", buf);
    uint32_t sdt = 0;
    const void *sp = saan_tensor(w, sbuf, &sdt, NULL, NULL);
    if (!sp || sdt != 2u) { r.cinp = 0; return r; }   /* scale が無いなら「引けなかった」 */
    r.q = (const int8_t *)p;
    r.scale = (const float *)sp;
    return r;
}

saan_wref saan_w(const saan_weights *w, const char *fmt, ...) {
    char buf[I8_NAME_LEN];
    va_list ap;
    SAAN_PROF_BEGIN(SAAN_PROF_LOOKUP);
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    {
        const saan_wref r = saan_w_named(w, buf);
        SAAN_PROF_END(SAAN_PROF_LOOKUP);
        return r;
    }
}

size_t saan_act_scratch_needed(int cin, int T) {
#if SAAN_INT8_ACT
    /* qx [T][cin] と sx [T] を別々に saan_alloc するので、境界も別々に数える */
    /* ⚠️ **パディング後のストライドで数える**（`saan_conv1d_w` の確保と必ず一致させる。
     * 片方だけ直すと arena 不足＝loud か、隣接バッファの上書き＝silent かが分かれる） */
    return SAAN_ALIGN16(SAAN_ALIGN16((size_t)cin) * (size_t)T)
         + SAAN_ALIGN16(sizeof(float) * (size_t)T);
#else
    (void)cin; (void)T;
    return 0;
#endif
}

saan_status saan_conv1d_w(float *y, const float *x, saan_wref W, const float *b,
                          int cin, int cout, int ksz, int T, saan_arena *a) {
    if (W.f32) {                            /* fp32 ブロブ: 既存カーネルそのもの */
        saan_conv1d(y, x, W.f32, b, cin, cout, ksz, T);
        return SAAN_OK;
    }
    if (!W.q || !W.scale) return SAAN_ERR_MISSING;
#if SAAN_INT8_ACT
    if (!a) return SAAN_ERR_ARENA;
    {
        const size_t mark = a->used;
        int8_t *qx = (int8_t *)saan_alloc(a, SAAN_ALIGN16((size_t)cin) * (size_t)T);
        float *sx = (float *)saan_alloc(a, sizeof(float) * (size_t)T);
        if (!qx || !sx) { a->used = mark; return SAAN_ERR_ARENA; }
        saan_conv1d_i8a(y, x, W.q, W.scale, b, cin, cout, ksz, T, qx, sx);
        a->used = mark;
    }
#else
    (void)a;
    saan_conv1d_i8(y, x, W.q, W.scale, b, cin, cout, ksz, T);
#endif
    return SAAN_OK;
}

saan_status saan_dwconv1d_w(float *y, const float *x, saan_wref W,
                            int ch, int ksz, int T, saan_arena *a) {
    if (W.f32) {
        saan_dwconv1d(y, x, W.f32, ch, ksz, T);
        return SAAN_OK;
    }
    if (!W.q || !W.scale) return SAAN_ERR_MISSING;
#if SAAN_INT8_ACT
    if (!a) return SAAN_ERR_ARENA;
    {
        const size_t mark = a->used;
        int8_t *qx = (int8_t *)saan_alloc(a, SAAN_ALIGN16((size_t)ch) * (size_t)T);
        float *sx = (float *)saan_alloc(a, sizeof(float) * (size_t)T);
        if (!qx || !sx) { a->used = mark; return SAAN_ERR_ARENA; }
        saan_dwconv1d_i8a(y, x, W.q, W.scale, ch, ksz, T, qx, sx);
        a->used = mark;
    }
#else
    (void)a;
    saan_dwconv1d_i8(y, x, W.q, W.scale, ch, ksz, T);
#endif
    return SAAN_OK;
}
