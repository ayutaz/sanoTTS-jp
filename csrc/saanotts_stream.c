/* ストリーミング推論の本体（Phase D-2 / D-029）
 *
 * **方式: ステート保持型パイプライン。**
 *
 * 各段は入力を `[C][2*pad + CHUNK]` のバッファに溜める。左 pad は前チャンクの
 * 実データ、右 pad は次チャンクの実データ。**バッファを入力にして、下流へ渡す
 * 中央 CHUNK フレーム（とその計算に要る範囲）だけを出力する。**
 *
 * ⚠️ **これが bit 一致（D-029 の G2）の根拠**: 中央フレーム t の計算に必要な
 * 入力は `[t-pad, t+pad]` だけで、それは全部バッファの中にある。バッファの外を
 * ゼロとみなす既存カーネルの挙動は、**中央 CHUNK フレームには影響しない**。
 * だから一括版とまったく同じ積和になる。カーネルも一括版と共有する。
 *
 * S9（T2、2026-09-03）: 以前は**バッファ全体**に conv / LN / GELU を掛けて中央だけ取り出して
 * いたが、捨てられる列（AC の 8/16、DEC の 6/14、TOKEN の (6n−84)/6n）の計算が MAC の 37%・
 * GELU の 42% だった（maps / 計画 §1）。今は各カーネルの出力範囲版（`saan_conv1d_wr` /
 * `saan_dwconv1d_wr`。出力は圧縮 [C][t1−t0]）で**要る列だけ**を計算する。
 * 出力要素ごとの積和順序・per-frame の量子化・per-frame の LN は変わらないので bit 同一
 * （stream G2 多文 3 レーン + QEMU の checksum で確認）。
 *
 * 計算量は k=5 / k=7 の conv だけが `(CHUNK + 2*pad) / CHUNK` 倍で、1×1 conv / LN / GELU /
 * 残差は CHUNK ぶんちょうど。ハロー再計算方式（全体で 10 倍）よりはるかに軽い。
 *
 * ⚠️ **代償はレイテンシ。** 出力は受容野ぶん 36 フレーム = 0.42 秒遅れる。
 */
#include "saanotts_stream.h"
#include "saanotts_internal.h"
#include "saan_prof.h"

#include "fft.h"

#include <math.h>
#include <string.h>

/* ⚠️ **移植性。** `M_PI` は C99 の <math.h> に無い（POSIX の拡張）。
 * macOS では既定で見えるが、**Linux + glibc の厳密 `-std=c99` では見えない**。
 * `bench.c` と同じ形で自前に持つ（C-033 / CI が Linux で見つけた）。 */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define AC_W  SAAN_AC_W
#define DEC_W SAAN_DEC_W
#define CD    SAAN_CDIM
#define E     SAAN_DEC_E
#define CH    SAAN_CHUNK
#define NB    SAAN_NBINS

/* token block の受容野。(c1 k=5 + c2 k=5) × 3 段 = ±12 トークン */
#define TOK_HALO 12
#define TOK_MAXW (CH + 2 * TOK_HALO)   /* 1 チャンクが跨ぐトークンは最大 CH */

/* --- パイプ段 ------------------------------------------------------------- */

typedef struct {
    float *buf;   /* [C][W]。W = 2*pad + CH */
    int C, pad, W;
} pipe_t;

/* --- 解決済みの重み（S1）---------------------------------------------------
 *
 * ⚠️ **テンソル検索（saan_w / saan_tf）は init で 1 回だけ。** step の中では呼ばない。
 *    検索は名前の vsnprintf + ヘッダ（104 B × 183 エントリ）の strncmp 線形走査で、
 *    step ごとに 102 回・約 16,000 エントリ（1.66 MB 相当）を舐めていた（M-80）。
 *    実機では flash から読むので、積和と同じ桁のコストになる。
 *    ここに置くのは**ポインタだけ**で、計算の順序は 1 つも変わらない（bit 同一）。 */
typedef struct {
    saan_wref c1w, c2w;
    const float *c1b, *c2b, *ng, *nb;
} saan_acblk_w;

typedef struct {
    saan_wref dw, p1w, p2w, cdw, cuw;
    const float *p1b, *p2b, *cdb, *cub, *gm;
} saan_decblk_w;

static int pipe_init(pipe_t *p, saan_arena *a, int C, int pad) {
    p->C = C; p->pad = pad; p->W = 2 * pad + CH;
    p->buf = (float *)saan_alloc(a, sizeof(float) * (size_t)C * p->W);
    if (!p->buf) return 0;
    memset(p->buf, 0, sizeof(float) * (size_t)C * p->W);   /* 先頭はゼロパディング */
    return 1;
}

/* CH フレームを末尾に押し込む（左へ CH シフト）。`src` は [C][CH]。
 * NULL なら**ゼロ**を押し込む = 発話末尾の右パディング（一括版と同じ挙動） */
static void pipe_push(pipe_t *p, const float *src) {
    SAAN_PROF_BEGIN(SAAN_PROF_PIPE);
    for (int c = 0; c < p->C; ++c) {
        float *row = p->buf + (size_t)c * p->W;
        memmove(row, row + CH, sizeof(float) * (size_t)(p->W - CH));
        float *tail = row + p->W - CH;
        if (src) memcpy(tail, src + (size_t)c * CH, sizeof(float) * CH);
        else memset(tail, 0, sizeof(float) * CH);
    }
    SAAN_PROF_END(SAAN_PROF_PIPE);
}

/* 中央 CH フレームを [C][CH] として dst に取り出す */
static void pipe_center(const pipe_t *p, const float *full, float *dst) {
    SAAN_PROF_BEGIN(SAAN_PROF_PIPE);
    for (int c = 0; c < p->C; ++c)
        memcpy(dst + (size_t)c * CH, full + (size_t)c * p->W + p->pad,
               sizeof(float) * CH);
    SAAN_PROF_END(SAAN_PROF_PIPE);
}

/* --- 内部状態 ------------------------------------------------------------- */

struct saan_stream_impl {
    pipe_t ac[5];        /* AcBlock。pad=4（c1 k=5 と c2 k=5 で ±2+±2） */
    pipe_t dinp;         /* decoder inp。pad=1 */
    pipe_t dblk[5];      /* dw ブロック。pad=3 */
    pipe_t cdel[6];      /* 各 decoder 段に同期させる c（conv しないので遅延だけ） */

    /* S9 で作業領域は「要る列だけ」の圧縮形になった（arena −11 KB。CH=8）。
     * w_full: AC では c1 の出力 [AC_W][W_AC − 4]、DEC では dw の出力 + g [DEC_W][CH]、
     *         step_chunk では decoder の h [DEC_W][CH]（同じ領域を段ごとに使い回す） */
    float *w_full;       /* [max(AC_W * (W_AC − 4), DEC_W * CH)] */
    float *w_c;          /* [CD * CH] step_chunk の c_sync（旧 w_full2 [maxC][maxW]） */
    float *w_ch;         /* [max(C) * CH] 中央の取り出し */
    float *w_ch2;
    float *w_e;          /* [E * CH] pw1 の出力（旧 [E][W_DEC]） */
    float *w_r;          /* [R * CH]（旧 [R][W_DEC]） */
    float *w_g;          /* [DEC_W * CH]（旧 [DEC_W][W_DEC]） */
    float *o1539;        /* [1539] decoder の生出力 **1 フレーム分**（mag/cos/sin のビュー） */
    float *hr;           /* [DEC_HEAD * CH] */

    float *ola, *olw, *win, *re, *im, *frm;   /* iSTFT */
    int32_t fpush;       /* push 済みフレーム数（絶対フレーム番号 + 1） */
    int32_t out_pos;     /* 次に出す絶対サンプル位置 */
    int32_t skip_hops;   /* 捨てる先頭 hop 数（一括版の N/2 切り出しに対応） */
    int ola_len;
    float *obuf;         /* [SAAN_OBUF_HOPS * HOP] 出力の詰め替え。
                          * 深さは 2·CH − (SAAN_LATENCY mod CH) = CH+4（saanotts_stream.h） */
    int32_t ofill;
    /* token チャンクの作業領域。S9 で段ごとに 8 列ずつ縮む（compute_tokens_body）:
     * 容量は tok_buf [AC_W][TOK_MAXW] / tok_w1 [AC_W][TOK_MAXW−4] / tok_w2 [AC_W][TOK_MAXW−8] */
    float *tok_buf, *tok_w1, *tok_w2, *tok_out;

    /* S1: 解決済みの重み。init の resolve_weights() が埋める */
    saan_acblk_w  tokw[3];   /* acoustic.token.%d */
    saan_acblk_w  acw[5];    /* acoustic.frame.%d */
    saan_decblk_w decw[5];   /* decoder.{dw,pw1,pw2,cdown,cup,gamma}.%d */
    saan_wref ow, iw, hdw, how;
    const float *ib, *hdb, *hob, *emb, *pos;
};

/* 1 段ぶんの重みを引く。**欠けを許すのは元の step と同じ場所だけ**（bias / LN の bias） */
static int acblk_resolve(const saan_weights *w, const char *pfx, int bi, saan_acblk_w *o) {
    o->c1w = saan_w(w, "%s.%d.c1.weight", pfx, bi);
    o->c1b = saan_tf(w, "%s.%d.c1.bias", pfx, bi);
    o->c2w = saan_w(w, "%s.%d.c2.weight", pfx, bi);
    o->c2b = saan_tf(w, "%s.%d.c2.bias", pfx, bi);
    o->ng  = saan_tf(w, "%s.%d.norm.weight", pfx, bi);
    o->nb  = saan_tf(w, "%s.%d.norm.bias", pfx, bi);
    return SAAN_W_OK(o->c1w) && SAAN_W_OK(o->c2w) && o->ng != NULL;
}

static int decblk_resolve(const saan_weights *w, int i, saan_decblk_w *o) {
    o->dw  = saan_w(w, "decoder.dw.%d.weight", i);
    o->p1w = saan_w(w, "decoder.pw1.%d.weight", i);
    o->p1b = saan_tf(w, "decoder.pw1.%d.bias", i);
    o->p2w = saan_w(w, "decoder.pw2.%d.weight", i);
    o->p2b = saan_tf(w, "decoder.pw2.%d.bias", i);
    o->cdw = saan_w(w, "decoder.cdown.%d.weight", i);
    o->cdb = saan_tf(w, "decoder.cdown.%d.bias", i);
    o->cuw = saan_w(w, "decoder.cup.%d.weight", i);
    o->cub = saan_tf(w, "decoder.cup.%d.bias", i);
    o->gm  = saan_tf(w, "decoder.gamma.%d", i);
    return SAAN_W_OK(o->dw) && SAAN_W_OK(o->p1w) && SAAN_W_OK(o->p2w)
        && SAAN_W_OK(o->cdw) && SAAN_W_OK(o->cuw) && o->gm != NULL;
}

/* 全部の重みを引く。**1 つでも欠けたら SAAN_ERR_MISSING**（step の中で初めて分かるより早い） */
static saan_status resolve_weights(saan_stream *st) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const saan_weights *w = st->w;
    for (int i = 0; i < 3; ++i)
        if (!acblk_resolve(w, "acoustic.token", i, &im->tokw[i])) return SAAN_ERR_MISSING;
    for (int i = 0; i < 5; ++i)
        if (!acblk_resolve(w, "acoustic.frame", i, &im->acw[i])) return SAAN_ERR_MISSING;
    for (int i = 0; i < 5; ++i)
        if (!decblk_resolve(w, i, &im->decw[i])) return SAAN_ERR_MISSING;
    im->ow  = saan_w(w, "acoustic.out.weight");
    im->iw  = saan_w(w, "decoder.inp.weight");
    im->ib  = saan_tf(w, "decoder.inp.bias");
    im->hdw = saan_w(w, "decoder.hdown.weight");
    im->hdb = saan_tf(w, "decoder.hdown.bias");
    im->how = saan_w(w, "decoder.hout.weight");
    im->hob = saan_tf(w, "decoder.hout.bias");
    im->emb = saan_tf(w, "acoustic.emb.weight");
    im->pos = saan_tf(w, "acoustic.pos.weight");
    if (!SAAN_W_OK(im->ow) || !SAAN_W_OK(im->iw) || !SAAN_W_OK(im->hdw)
        || !SAAN_W_OK(im->how) || !im->emb || !im->pos) return SAAN_ERR_MISSING;
    return SAAN_OK;
}

/* init が成功したときの a->used そのもの。**stream_init_body の確保と 1:1**（順序は和なので不問）。
 * ⚠️ 2 回書いている。arena_stress の §5 が実測と bit 一致で突き合わせる（ずれたら NG） */
size_t saan_stream_arena_used(int32_t n_ids) {
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;
    const int W_AC = 2 * 4 + CH;
    const size_t full_n = (size_t)AC_W * (W_AC - 4) > (size_t)DEC_W * CH
                        ? (size_t)AC_W * (W_AC - 4) : (size_t)DEC_W * CH;
    size_t s = 0;
    /* --- 発話長（ids 数）に比例する分（8 B/id） --- */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)n_ids);            /* log_d */
    s += SAAN_ALIGN16(sizeof(int32_t) * (size_t)n_ids);          /* d_hat */
    /* --- 発話長に依存しない分（G3 の対象） --- */
    s += SAAN_ALIGN16(sizeof(struct saan_stream_impl));          /* ⚠️ ポインタ幅で変わる */
    s += SAAN_ALIGN16(sizeof(float) * AC_W * (2 * 4 + CH)) * 5;   /* ac */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 1 + CH));         /* dinp */
    s += SAAN_ALIGN16(sizeof(float) * DEC_W * (2 * 3 + CH)) * 5;  /* dblk */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 1 + CH));         /* cdel[0] */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 3 + CH)) * 5;     /* cdel[1..5] */
    /* S9: 作業領域は圧縮形 */
    s += SAAN_ALIGN16(sizeof(float) * full_n);                    /* w_full */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)CD * CH);           /* w_c */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)maxC * CH) * 2;     /* w_ch/2 */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)E * CH);            /* w_e */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_R * CH);   /* w_r */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)DEC_W * CH);        /* w_g */
    s += SAAN_ALIGN16(sizeof(float) * 1539 * CH);                 /* o1539 */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_HEAD * CH);/* hr */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)(SAAN_NFFT + 2 * SAAN_HOP)) * 2;   /* ola / olw */
    s += SAAN_ALIGN16(sizeof(float) * SAAN_NFFT);                 /* win */
    s += SAAN_ALIGN16(sizeof(float) * NB) * 2;                    /* re / im */
    s += SAAN_ALIGN16(sizeof(float) * SAAN_NFFT);                 /* frm */
    /* obuf: 2·CH − (SAAN_LATENCY mod CH) hop（= CH+4。saanotts_stream.h の導出） */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_OBUF_HOPS * SAAN_HOP);
    s += SAAN_ALIGN16(sizeof(float) * AC_W * TOK_MAXW);          /* tok_buf */
    s += SAAN_ALIGN16(sizeof(float) * AC_W * (TOK_MAXW - 4));    /* tok_w1（S9） */
    s += SAAN_ALIGN16(sizeof(float) * AC_W * (TOK_MAXW - 8));    /* tok_w2（S9） */
    s += SAAN_ALIGN16(sizeof(float) * AC_W * CH);                /* tok_out */
    return s;
}

size_t saan_stream_arena_needed(int32_t n_ids) {
    /* 確保の一覧は saan_stream_arena_used に 1 つだけ持つ。ここは緩い上限:
     * duration の一時領域（init の中で確保して返す。3 × DUR_W × n_ids）を**和で**足す */
    size_t s = saan_stream_arena_used(n_ids);
    s += SAAN_ALIGN16(sizeof(float) * SAAN_DUR_W * (size_t)n_ids) * 3;
    /* W8A8（`-DSAAN_INT8_ACT=1`）のとき conv 1 本ぶんの activation 作業領域。
     * conv の中で確保してすぐ返すので**同時に 1 本ぶん**。S9 で 1×1 conv は T=CH になった
     * ので、候補の最大を取る: pw2（cin=E, T=CH）/ token の c1（cin=AC_W, T ≤ TOK_MAXW）/
     * dw（ch=DEC_W, T=W_DEC）/ AC の c1（cin=AC_W, T=W_AC）。
     * W8A32（既定）では 0 が返るので G1/G3 の実測値は変わらない */
    {
        size_t m = saan_act_scratch_needed(E, CH), v;
        v = saan_act_scratch_needed(AC_W, TOK_MAXW);   if (v > m) m = v;
        v = saan_act_scratch_needed(DEC_W, 2 * 3 + CH); if (v > m) m = v;
        v = saan_act_scratch_needed(AC_W, 2 * 4 + CH);  if (v > m) m = v;
        s += m;
    }
    return s + 8192;
}

/* --- 各段の処理 ----------------------------------------------------------- */

/* 発話の外（時刻 < 0 または >= n_frames）の列をゼロにする。
 *
 * ⚠️ **これが無いと一括版と一致しない。** 一括版は段 i の入力の外側を厳密に
 * ゼロとみなすが、ストリーミングでは段 i-1 が**その位置にも bias 由来の
 * 非ゼロを出す**（conv の bias / LayerNorm の bias / 残差）。
 * 実測で全サンプルが max|Δ| 0.37 ずれた原因がこれだった。 */
static void zero_outside_n(float *x, int C, int stride, int n,
                           int32_t t0, int32_t n_frames) {
    for (int m = 0; m < n; ++m) {
        const int32_t t = t0 + m;
        if (t >= 0 && t < n_frames) continue;
        for (int c = 0; c < C; ++c) x[(size_t)c * stride + m] = 0.0f;
    }
}

static void zero_outside(float *x, int C, int32_t t0, int32_t n_frames) {
    zero_outside_n(x, C, CH, CH, t0, n_frames);
}

/* AcBlock 1 段。buf（窓 W = 2·pad + CH）を入力に、中央 CH を out へ。
 * 参照実装 `AcBlock.forward`: `x + LN(c2(relu(c1(x))))`
 *
 * S9: 計算するのは要る列だけ。
 *   c1（k=5, pad 2）: c2 が中央 [pad, pad+CH) を出すのに要る列 [pad−2, pad+CH+2) = [2, W−2)
 *                    を圧縮して w_full [AC_W][W−4] に置く
 *   c2 / LN / 残差   : 中央 CH だけ。c2 の出力は `out` [AC_W][CH] に直接書く
 *                    （以前の w_full2 [AC_W][W] と pipe_center は要らない）
 * ⚠️ 陽性対照（計画 T2）: c1 の範囲を [3, W−2) に 1 列狭める（下の c1_lo を pad−1 にし、
 *    c2 の範囲を pad−c1_lo で追従させる）と、各チャンクの中央先頭フレームが c1 の 1 列を
 *    ゼロパディングで代用してしまい、stream G2 が落ちる（2026-09-03 に実際に落ちるのを見て戻した） */
static saan_status ac_step_body(saan_stream *st, int bi, const float *in,
                                float *out, int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->ac[bi];
    const saan_acblk_w *k = &im->acw[bi];   /* init で解決済み（S1） */
    const saan_wref c1w = k->c1w, c2w = k->c2w;
    const float *c1b = k->c1b, *c2b = k->c2b, *ng = k->ng, *nb = k->nb;

    pipe_push(p, in);
    const int W = p->W;
    /* buf の先頭が対応する絶対時刻。中央の先頭 t_out から pad 引いた位置 */
    const int32_t t_buf = t_out - p->pad;
    const int c1_lo = p->pad - 2, c1_hi = p->pad + CH + 2;   /* c1 の要る列 [2, W−2) */
    const int T1 = c1_hi - c1_lo;                            /* = W − 4 */

    SAAN_TRY(saan_conv1d_wr(im->w_full, p->buf, c1w, c1b, AC_W, AC_W, 5, W,
                            c1_lo, c1_hi, st->a));
    saan_relu(im->w_full, (size_t)AC_W * T1);
    /* ⚠️ **c1 の出力にもゼロクリアが要る。** 一括版では c1 の配列は [0,T) しかなく、
     * c2（k=5）がその外を参照するとゼロになる。ストリーミングは c1 を窓幅で
     * 計算するので、発話外にも **bias 由来の非ゼロ**が残る。
     * 実測: これが無いと先頭 pad フレームが max|Δ| 0.49 ずれた
     * （圧縮座標なので先頭の絶対時刻は t_buf + c1_lo） */
    zero_outside_n(im->w_full, AC_W, T1, T1, t_buf + c1_lo, st->n_frames);
    /* c2: 中央 [pad, pad+CH) = c1 の圧縮座標 [pad − c1_lo, pad − c1_lo + CH) = [2, 10) */
    SAAN_TRY(saan_conv1d_wr(out, im->w_full, c2w, c2b, AC_W, AC_W, 5, T1,
                            p->pad - c1_lo, p->pad - c1_lo + CH, st->a));
    saan_layernorm_c(out, ng, nb, AC_W, CH);
    for (int c = 0; c < AC_W; ++c)
        for (int m = 0; m < CH; ++m)
            out[(size_t)c * CH + m] += p->buf[(size_t)c * W + p->pad + m];
    zero_outside(out, AC_W, t_out, st->n_frames);
    return SAAN_OK;
}

static saan_status ac_step(saan_stream *st, int bi, const float *in,
                           float *out, int32_t t_out) {
    SAAN_PROF_BEGIN(SAAN_PROF_AC);
    const saan_status s = ac_step_body(st, bi, in, out, t_out);
    SAAN_PROF_END(SAAN_PROF_AC);
    return s;
}

/* decoder の inp（k=3）。参照実装 `Decoder.forward` の 1 行目 */
static saan_status dec_inp_step_body(saan_stream *st, const float *c_in,
                                     float *h_out, float *c_out, int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->dinp, *pc = &im->cdel[0];
    const saan_wref iw = im->iw;   /* init で解決済み（S1） */
    const float *ib = im->ib;

    pipe_push(p, c_in);           /* inp の入力は c そのもの（C=CD） */
    pipe_push(pc, c_in);          /* 下流の条件付け用に同じ c を同期させる */
    /* S9: 中央 [pad, pad+CH) だけを `h_out` [DEC_W][CH] に直接書く。入力は p->buf なので
     * 呼び出し側が `h_out` に `w_full` を渡してきても重ならない
     * （以前は w_g に [DEC_W][W] で出してから中央を memcpy していた。w_g が
     * [DEC_W][CH] に縮んだのでそこには入らない） */
    SAAN_TRY(saan_conv1d_wr(h_out, p->buf, iw, ib, CD, DEC_W, 3, p->W,
                            p->pad, p->pad + CH, st->a));
    pipe_center(pc, pc->buf, c_out);
    zero_outside(h_out, DEC_W, t_out, st->n_frames);
    zero_outside(c_out, CD, t_out, st->n_frames);
    return SAAN_OK;
}

static saan_status dec_inp_step(saan_stream *st, const float *c_in, float *h_out,
                                float *c_out, int32_t t_out) {
    SAAN_PROF_BEGIN(SAAN_PROF_DINP);
    const saan_status s = dec_inp_step_body(st, c_in, h_out, c_out, t_out);
    SAAN_PROF_END(SAAN_PROF_DINP);
    return s;
}

/* dw ブロック 1 段。参照実装:
 *   g = cup(cdown(c));  h = h + gamma * pw2(gelu(pw1(dw(h) + g)))
 * ⚠️ **c は毎段とも元の入力**（h ではない）。時刻を h と揃えるため遅延させる
 *
 * S9（計画 §9 の訂正どおり）: **dw の入力（p->buf）だけ窓全部**で、dw の出力・cdown / cup・
 * pw1 / GELU / pw2・残差はすべて中央 CH だけ。1×1 conv と GELU はフレーム独立、
 * W8A8 の量子化も per-frame なので、中央だけ計算しても各要素の値は同じ。
 *   - c は先に中央を取り出し（c_out [CD][CH]）、cdown / cup をそこに T=CH で掛ける
 *   - dw は範囲版で中央 [pad, pad+CH) だけを w_full [DEC_W][CH] に出す（DW −43%）
 *   - pw2 の出力は h_out [DEC_W][CH] に直接書き、残差もそこで取る
 * 以前は dw / cdown / cup / pw1 / GELU / pw2 を窓 W=14 全部に掛けて 6 列を捨てていた */
static saan_status dec_step_body(saan_stream *st, int i, const float *h_in,
                                 const float *c_in, float *h_out, float *c_out,
                                 int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->dblk[i], *pc = &im->cdel[i + 1];
    const saan_decblk_w *k = &im->decw[i];   /* init で解決済み（S1） */
    const saan_wref dw = k->dw, p1w = k->p1w, p2w = k->p2w, cdw = k->cdw, cuw = k->cuw;
    const float *p1b = k->p1b, *p2b = k->p2b, *cdb = k->cdb, *cub = k->cub, *gm = k->gm;

    pipe_push(p, h_in);
    pipe_push(pc, c_in);
    const int W = p->W;
    /* 条件付けは 1x1 なので pad 不要。**c の中央 CH だけ**に掛ける（値は窓全部に掛けた
     * ときの中央列と同じ = per-frame） */
    pipe_center(pc, pc->buf, c_out);
    SAAN_TRY(saan_conv1d_w(im->w_r, c_out, cdw, cdb, CD, SAAN_DEC_R, 1, CH, st->a));
    SAAN_TRY(saan_conv1d_w(im->w_g, im->w_r, cuw, cub, SAAN_DEC_R, DEC_W, 1, CH, st->a));

    /* dw: 入力は窓全部（p->buf [DEC_W][W]）、出力は中央だけ圧縮して w_full [DEC_W][CH] */
    SAAN_TRY(saan_dwconv1d_wr(im->w_full, p->buf, dw, DEC_W, 7, W,
                              p->pad, p->pad + CH, st->a));
    for (size_t k = 0; k < (size_t)DEC_W * CH; ++k) im->w_full[k] += im->w_g[k];
    SAAN_TRY(saan_conv1d_w(im->w_e, im->w_full, p1w, p1b, DEC_W, E, 1, CH, st->a));
    saan_gelu(im->w_e, (size_t)E * CH);
    SAAN_TRY(saan_conv1d_w(h_out, im->w_e, p2w, p2b, E, DEC_W, 1, CH, st->a));
    for (int c = 0; c < DEC_W; ++c)
        for (int m = 0; m < CH; ++m)
            h_out[(size_t)c * CH + m] =
                p->buf[(size_t)c * W + p->pad + m] + gm[0] * h_out[(size_t)c * CH + m];

    zero_outside(h_out, DEC_W, t_out, st->n_frames);
    zero_outside(c_out, CD, t_out, st->n_frames);
    return SAAN_OK;
}

static saan_status dec_step(saan_stream *st, int i, const float *h_in,
                            const float *c_in, float *h_out, float *c_out,
                            int32_t t_out) {
    SAAN_PROF_BEGIN(SAAN_PROF_DEC);
    const saan_status s = dec_step_body(st, i, h_in, c_in, h_out, c_out, t_out);
    SAAN_PROF_END(SAAN_PROF_DEC);
    return s;
}

/* --- iSTFT のリングバッファ -----------------------------------------------
 *
 * 一括版は全フレームを overlap-add してから `[N/2, N/2 + T*hop)` を切り出す。
 * ここでは **NFFT + HOP サンプルのリング**で同じことをする。
 * ⚠️ **窓の二乗和で割るのは、そのサンプルに寄与する全フレームが出そろってから。**
 * hop 256 / n_fft 1024 なので 4 フレーム分待つ必要がある。
 */
static void istft_push(struct saan_stream_impl *im, const float *mag,
                       const float *cosv, const float *sinv, int t,
                       int32_t abs_frame) {
    const int N = SAAN_NFFT;
    SAAN_PROF_BEGIN(SAAN_PROF_ISTFT);
    for (int k = 0; k < NB; ++k) {
        const float m = mag[(size_t)k * CH + t];
        im->re[k] = m * cosv[(size_t)k * CH + t];
        im->im[k] = m * sinv[(size_t)k * CH + t];
    }
    /* 逆実 FFT。一括版と**同じ関数**を使う（2 回書かない）。
     * `-DSAAN_USE_NAIVE_DFT` で naive に戻せる（検証基準として残してある） */
#ifdef SAAN_USE_NAIVE_DFT
    for (int n = 0; n < N; ++n) {
        double acc = (double)im->re[0];
        for (int k = 1; k < N / 2; ++k) {
            const double ang = 2.0 * M_PI * (double)k * (double)n / (double)N;
            acc += 2.0 * ((double)im->re[k] * cos(ang) - (double)im->im[k] * sin(ang));
        }
        acc += (double)im->re[N / 2] * cos(M_PI * (double)n);
        im->frm[n] = (float)(acc / (double)N);
    }
#else
    saan_irfft_1024(im->re, im->im, im->frm);
#endif
    const int L = im->ola_len;
    /* ⚠️ **絶対フレーム番号で位置を決める。** ローカルのカウンタで数えると、
     * 時刻が負の（発話に存在しない）フレームまで数えてしまい、
     * 一括版と位置がずれる（実測でそうなった） */
    const int32_t base = abs_frame * SAAN_HOP;
    for (int i = 0; i < N; ++i) {
        const int j = (int)((base + i) % L);
        im->ola[j] += im->frm[i] * im->win[i];
        im->olw[j] += im->win[i] * im->win[i];
    }
    if (abs_frame + 1 > im->fpush) im->fpush = abs_frame + 1;
    SAAN_PROF_END(SAAN_PROF_ISTFT);
}

/* リングから HOP サンプル取り出して先へ進める。
 * ⚠️ **呼ぶのはそのサンプルへの寄与が出そろってから。** サンプル p に寄与する
 * 最大フレームは floor(p/hop) なので、一括版が切り出す `[N/2, ...)` の先頭
 * （= フレーム 2）まで push してからでないと値が違う。`istft_ready()` で判定する */
static void istft_pop(struct saan_stream_impl *im, float *out) {
    const int L = im->ola_len;
    SAAN_PROF_BEGIN(SAAN_PROF_ISTFT);
    for (int i = 0; i < SAAN_HOP; ++i) {
        const int j = (int)((im->out_pos + i) % L);
        out[i] = im->olw[j] > 1e-11f ? im->ola[j] / im->olw[j] : 0.0f;
        im->ola[j] = 0.0f;
        im->olw[j] = 0.0f;
    }
    im->out_pos += SAAN_HOP;
    SAAN_PROF_END(SAAN_PROF_ISTFT);
}

/* 次の HOP サンプルを出せるか。`out_pos + HOP - 1` に寄与する最大フレームが
 * push 済みなら出せる。**発話の全フレームを push し終えたら残りは全部出せる** */
static int istft_ready(const struct saan_stream_impl *im, int32_t n_frames) {
    if (im->out_pos >= SAAN_NFFT / 2 + n_frames * SAAN_HOP) return 0;
    if (im->fpush >= n_frames) return 1;   /* 全フレーム push 済みなら残りを出せる */
    const int32_t last = im->out_pos + SAAN_HOP - 1;
    return im->fpush > last / SAAN_HOP;
}

/* --- 公開 API ------------------------------------------------------------- */

static saan_status stream_init_body(saan_stream *st, const saan_weights *w,
                                    saan_arena *a, const int32_t *ids,
                                    int32_t n_ids, float s_v) {
    memset(st, 0, sizeof *st);
    st->w = w; st->a = a; st->ids = ids; st->n_ids = n_ids; st->s_v = s_v;
    if (n_ids <= 0) return SAAN_ERR_SHAPE;

    st->log_d = (float *)saan_alloc(a, sizeof(float) * (size_t)n_ids);
    st->d_hat = (int32_t *)saan_alloc(a, sizeof(int32_t) * (size_t)n_ids);
    if (!st->log_d || !st->d_hat) return SAAN_ERR_ARENA;

    const size_t mark = a->used;
    saan_status s = saan_run_duration(w, a, ids, n_ids, st->log_d);
    if (s != SAAN_OK) return s;
    a->used = mark;

    int32_t T = 0;
    for (int i = 0; i < n_ids; ++i) {
        float v = roundf(s_v * expf(st->log_d[i]));
        if (v < SAAN_CLIP_LO) v = SAAN_CLIP_LO;
        if (v > SAAN_CLIP_HI) v = SAAN_CLIP_HI;
        st->d_hat[i] = (int32_t)v;
        T += st->d_hat[i];
    }
    st->n_frames = T;

    struct saan_stream_impl *im =
        (struct saan_stream_impl *)saan_alloc(a, sizeof *im);
    if (!im) return SAAN_ERR_ARENA;
    memset(im, 0, sizeof *im);
    st->impl = im;
    SAAN_TRY(resolve_weights(st));   /* S1: 重みの検索はここで 1 回だけ */

    for (int i = 0; i < 5; ++i) if (!pipe_init(&im->ac[i], a, AC_W, 4)) return SAAN_ERR_ARENA;
    if (!pipe_init(&im->dinp, a, CD, 1)) return SAAN_ERR_ARENA;
    for (int i = 0; i < 5; ++i) if (!pipe_init(&im->dblk[i], a, DEC_W, 3)) return SAAN_ERR_ARENA;
    /* c の遅延: dinp は pad=1、各 dblk は pad=3 */
    if (!pipe_init(&im->cdel[0], a, CD, 1)) return SAAN_ERR_ARENA;
    for (int i = 1; i < 6; ++i) if (!pipe_init(&im->cdel[i], a, CD, 3)) return SAAN_ERR_ARENA;

    /* ⚠️ **作業領域は段ごとの実寸で取る。** 以前は maxC(76) × maxW(16) を
     * 一律に確保していたが、acoustic は C=48/W=16、decoder は C=76/W=14 で、
     * 76×16 は**どちらにも要らない上限**だった。
     * S9 でさらに圧縮形になった（要る列だけ）。**saan_stream_arena_needed と 1:1**:
     *   w_full  max(AC_W × (W_AC − 4), DEC_W × CH)   c1 の出力 / dw の出力 + g / h
     *   w_c     CD × CH                              step_chunk の c_sync
     *   w_e / w_r / w_g   E × CH / R × CH / DEC_W × CH（旧 W_DEC 幅から −9.4 KB） */
    const int W_AC = 2 * 4 + CH;    /* AcBlock の窓 */
    const size_t full_n = (size_t)AC_W * (W_AC - 4) > (size_t)DEC_W * CH
                        ? (size_t)AC_W * (W_AC - 4) : (size_t)DEC_W * CH;
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;
    im->w_full  = (float *)saan_alloc(a, sizeof(float) * full_n);
    im->w_c     = (float *)saan_alloc(a, sizeof(float) * (size_t)CD * CH);
    im->w_ch    = (float *)saan_alloc(a, sizeof(float) * (size_t)maxC * CH);
    im->w_ch2   = (float *)saan_alloc(a, sizeof(float) * (size_t)maxC * CH);
    im->w_e     = (float *)saan_alloc(a, sizeof(float) * (size_t)E * CH);
    im->w_r     = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_DEC_R * CH);
    im->w_g     = (float *)saan_alloc(a, sizeof(float) * (size_t)DEC_W * CH);
    im->o1539   = (float *)saan_alloc(a, sizeof(float) * 1539 * CH);
    im->hr      = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_DEC_HEAD * CH);
    im->ola_len = SAAN_NFFT + 2 * SAAN_HOP;   /* out_pos が N/2 先行する分 */
    im->ola     = (float *)saan_alloc(a, sizeof(float) * (size_t)im->ola_len);
    im->olw     = (float *)saan_alloc(a, sizeof(float) * (size_t)im->ola_len);
    im->win     = (float *)saan_alloc(a, sizeof(float) * SAAN_NFFT);
    im->re      = (float *)saan_alloc(a, sizeof(float) * NB);
    im->im      = (float *)saan_alloc(a, sizeof(float) * NB);
    im->frm     = (float *)saan_alloc(a, sizeof(float) * SAAN_NFFT);

    /* ⚠️ **全部の確保を検査する。最後の 1 つだけでは足りない。**
     * saan_alloc は入らなければ NULL を返すが `used` を進めないので、
     * 途中で失敗しても**より小さい後続の確保は成功しうる**。
     * 以前ここが `if (!im->frm)` だけだったため、arena 175〜191 KB の
     * 15 サイズで **init が SAAN_OK を返したまま NULL を抱えて**
     * あとから落ちていた（検証で発覚）。 */
    if (!im->w_full || !im->w_c || !im->w_ch || !im->w_ch2 ||
        !im->w_e || !im->w_r || !im->w_g || !im->o1539 || !im->hr ||
        !im->ola || !im->olw || !im->win || !im->re || !im->im || !im->frm)
        return SAAN_ERR_ARENA;

    memset(im->ola, 0, sizeof(float) * (size_t)im->ola_len);
    memset(im->olw, 0, sizeof(float) * (size_t)im->ola_len);
    for (int i = 0; i < SAAN_NFFT; ++i)
        im->win[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)SAAN_NFFT);
    im->fpush = 0;
    /* ⚠️ **out_pos は 0 から始める。** 一括版は `[N/2, ...)` を切り出すが、
     * リングでその先頭 N/2 サンプルを飛ばすと**その領域が pop されず
     * ゼロクリアもされない**ため、frame 3 の書き込みと衝突する（実測で
     * 出力サンプル 1025 以降が壊れた）。0 から pop して最初の
     * `N/2 / HOP` 回を捨てるのが正しい */
    im->out_pos = 0;
    im->skip_hops = SAAN_NFFT / 2 / SAAN_HOP;
    im->obuf = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_OBUF_HOPS * SAAN_HOP);
    /* S9: token 段は 8 列ずつ縮むので、tok_w1 / tok_w2 は 4 / 8 列少なくて足りる
     * （compute_tokens_body の回転を参照。saan_stream_arena_needed と 1:1） */
    im->tok_buf = (float *)saan_alloc(a, sizeof(float) * AC_W * TOK_MAXW);
    im->tok_w1  = (float *)saan_alloc(a, sizeof(float) * AC_W * (TOK_MAXW - 4));
    im->tok_w2  = (float *)saan_alloc(a, sizeof(float) * AC_W * (TOK_MAXW - 8));
    im->tok_out = (float *)saan_alloc(a, sizeof(float) * AC_W * CH);
    if (!im->obuf || !im->tok_buf || !im->tok_w1 || !im->tok_w2 || !im->tok_out)
        return SAAN_ERR_ARENA;
    im->ofill = 0;
    st->ofill_max = 0;
    st->peak_used = a->peak;
    return SAAN_OK;
}

saan_status saan_stream_init(saan_stream *st, const saan_weights *w,
                             saan_arena *a, const int32_t *ids, int32_t n_ids,
                             float s_v) {
    SAAN_PROF_BEGIN(SAAN_PROF_INIT);
    const saan_status s = stream_init_body(st, w, a, ids, n_ids, s_v);
    SAAN_PROF_END(SAAN_PROF_INIT);
    return s;
}

/* --- token レートのチャンク化 --------------------------------------------
 *
 * ⚠️ **`tok_h` を発話全体で持つと ids に比例して RAM が増える**（実測 192 B/id、
 * 350 ids で 68 KB）。G1（200 KB）を満たすため、必要なトークン範囲だけ都度計算する。
 *
 * token block の受容野は (c1 k=5 + c2 k=5) × 3 段 = **±12 トークン**。
 * `[i0-12, i1+12]` を入力すれば `[i0, i1)` が一括版と一致する。
 * 再計算は入るが token レートは frame レートの 1/2.3 なので軽い。
 */
static saan_status compute_tokens_body(saan_stream *st, int32_t i0, int32_t i1,
                                       float *out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const float *emb = im->emb;   /* init で解決済み（S1） */

    const int32_t lo = i0 - TOK_HALO, hi = i1 + TOK_HALO;
    int n = (int)(hi - lo);       /* 今の h の列数。段ごとに 8 ずつ縮む（S9） */
    int32_t l = lo;               /* h の先頭列の絶対トークン番号。段ごとに 4 ずつ進む */
    if (n > TOK_MAXW) return SAAN_ERR_SHAPE;

    /* S9: 各 conv（k=5, pad 2）で有効範囲が両側 2 ずつ縮むので、段 bi の出力は
     * 入力の [4, n−4) だけ計算すればよい（受容野 ±12 = 3 段 × 4 の逆算）。出力は圧縮形。
     * 3 本のバッファを回す: c1 の出力は常に B（容量 TOK_MAXW−4）、段の入出力は A と C を交互
     * （A: TOK_MAXW、C: TOK_MAXW−8。段 0 の入力は A、出力は C、段 1 は C → A、段 2 は A → C）。
     * memcpy で h を戻す必要が無く、最後の h（C）の先頭がそのまま [i0, i1) になる */
    float *A = im->tok_buf, *B = im->tok_w1, *C = im->tok_w2;
    float *h = A;
    for (int k = 0; k < n; ++k) {
        const int32_t i = lo + k;
        if (i < 0 || i >= st->n_ids) {        /* 発話外はゼロ（一括版と同じ） */
            for (int c = 0; c < AC_W; ++c) h[(size_t)c * n + k] = 0.0f;
            continue;
        }
        if (st->ids[i] < 0 || st->ids[i] >= SAAN_VOCAB) return SAAN_ERR_RANGE;
        for (int c = 0; c < AC_W; ++c)
            h[(size_t)c * n + k] = emb[(size_t)st->ids[i] * AC_W + c];
    }

    for (int bi = 0; bi < 3; ++bi) {
        const saan_acblk_w *k = &im->tokw[bi];   /* init で解決済み（S1） */
        const saan_wref c1w = k->c1w, c2w = k->c2w;
        const float *c1b = k->c1b, *c2b = k->c2b, *ng = k->ng, *nb = k->nb;
        float *t1 = B;
        float *o  = (h == A) ? C : A;
        const int n1 = n - 4, n2 = n - 8;
        /* c1: 出力 [2, n−2) を圧縮して t1 [AC_W][n−4]（先頭は絶対 l+2） */
        SAAN_TRY(saan_conv1d_wr(t1, h, c1w, c1b, AC_W, AC_W, 5, n, 2, n - 2, st->a));
        saan_relu(t1, (size_t)AC_W * n1);
        /* ⚠️ c1 の出力の発話外もゼロに（一括版では配列外＝ゼロ、frame 側と同じ理由） */
        zero_outside_n(t1, AC_W, n1, n1, l + 2, st->n_ids);
        /* c2: 出力 [4, n−4)（h の座標）= t1 の座標 [2, n−6) を圧縮して o [AC_W][n−8]（先頭は l+4） */
        SAAN_TRY(saan_conv1d_wr(o, t1, c2w, c2b, AC_W, AC_W, 5, n1, 2, n1 - 2, st->a));
        saan_layernorm_c(o, ng, nb, AC_W, n2);
        for (int c = 0; c < AC_W; ++c)
            for (int j = 0; j < n2; ++j)
                o[(size_t)c * n2 + j] += h[(size_t)c * n + j + 4];
        zero_outside_n(o, AC_W, n2, n2, l + 4, st->n_ids);
        h = o; n = n2; l += 4;
    }
    /* ここで n == i1 − i0、l == i0（3 段 × 4 = TOK_HALO） */
    if (n != (int)(i1 - i0) || l != i0) return SAAN_ERR_SHAPE;
    memcpy(out, h, sizeof(float) * (size_t)AC_W * (size_t)n);
    return SAAN_OK;
}

static saan_status compute_tokens(saan_stream *st, int32_t i0, int32_t i1,
                                  float *out) {
    SAAN_PROF_BEGIN(SAAN_PROF_TOKEN);
    const saan_status s = compute_tokens_body(st, i0, i1, out);
    SAAN_PROF_END(SAAN_PROF_TOKEN);
    SAAN_PROF_ADD(SAAN_PROF_TOKEN, (size_t)(i1 - i0 + 2 * TOK_HALO));
    return s;
}

/* CH フレームぶんの `hf`（length regulator の出力）を作る。
 * 参照実装の `repeat_interleave` + 位置埋め込みと同じ。
 * `f0` は絶対フレーム番号。範囲外（発話末尾より後ろ）は**ゼロ**にする */
static saan_status make_hf_body(saan_stream *st, int32_t f0, float *out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const float *pos = im->pos;   /* init で解決済み（S1） */

    /* このチャンクが跨ぐトークン範囲を先に求める */
    int32_t tok_of[CH], within_of[CH];
    int32_t i0 = -1, i1 = -1;
    for (int k = 0; k < CH; ++k) {
        const int32_t f = f0 + k;
        if (f >= st->n_frames) { tok_of[k] = -1; continue; }
        int32_t acc = 0, i = 0;
        while (i < st->n_ids && acc + st->d_hat[i] <= f) { acc += st->d_hat[i]; ++i; }
        tok_of[k] = i;
        within_of[k] = f - acc;
        if (i0 < 0 || i < i0) i0 = i;
        if (i > i1) i1 = i;
    }
    if (i0 < 0) {                     /* 全部が発話外 */
        memset(out, 0, sizeof(float) * (size_t)AC_W * CH);
        return SAAN_OK;
    }
    saan_status s = compute_tokens(st, i0, i1 + 1, im->tok_out);
    if (s != SAAN_OK) return s;

    const int32_t span = i1 + 1 - i0;
    for (int k = 0; k < CH; ++k) {
        if (tok_of[k] < 0) {
            for (int c = 0; c < AC_W; ++c) out[(size_t)c * CH + k] = 0.0f;
            continue;
        }
        const int32_t j = tok_of[k] - i0;
        const int pi = within_of[k] < SAAN_POS_MAX ? (int)within_of[k]
                                                   : SAAN_POS_MAX - 1;
        for (int c = 0; c < AC_W; ++c)
            out[(size_t)c * CH + k] =
                im->tok_out[(size_t)c * span + j] + pos[(size_t)pi * AC_W + c];
    }
    return SAAN_OK;
}

static saan_status make_hf(saan_stream *st, int32_t f0, float *out) {
    SAAN_PROF_BEGIN(SAAN_PROF_HF);
    const saan_status s = make_hf_body(st, f0, out);
    SAAN_PROF_END(SAAN_PROF_HF);
    return s;
}

/* パイプラインを CH フレーム進める。出力は `pcm`（CH * HOP サンプル） */
static saan_status step_chunk_body(saan_stream *st, float *pcm) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const saan_wref ow = im->ow, hdw = im->hdw, how = im->how;   /* init で解決済み（S1） */
    const float *hdb = im->hdb, *hob = im->hob;

    float *a = im->w_ch, *b = im->w_ch2;
    /* 今回入力するフレームの先頭時刻。**段を通るごとに pad ぶん過去にずれる** */
    int32_t t = st->pushed;
    {
        saan_status sh = make_hf(st, st->pushed, a);
        if (sh != SAAN_OK) return sh;
    }
    st->pushed += CH;

    for (int i = 0; i < 5; ++i) {
        t -= im->ac[i].pad;
        saan_status s = ac_step(st, i, a, b, t);
        if (s != SAAN_OK) return s;
        float *tmp = a; a = b; b = tmp;
    }
    /* acoustic.out は 1x1（bias 無し）。CH フレームに直接掛ける */
    float *c_cur = b;
    SAAN_TRY(saan_conv1d_w(c_cur, a, ow, NULL, AC_W, CD, 1, CH, st->a));

    if (st->dbg_c) {          /* デバッグ: c を絶対時刻で控える */
        for (int m = 0; m < CH; ++m) {
            const int32_t tt = t + m;   /* t は ac 5 段を通った後の出力時刻 */
            if (tt < 0 || tt >= st->dbg_cap) continue;
            for (int ch = 0; ch < CD; ++ch)
                st->dbg_c[(size_t)ch * st->dbg_cap + tt] = c_cur[(size_t)ch * CH + m];
        }
    }

    float *h = im->w_full;      /* [DEC_W][CH] として使い回す */
    float *c_sync = im->w_c;
    t -= im->dinp.pad;
    saan_status s = dec_inp_step(st, c_cur, h, c_sync, t);
    if (s != SAAN_OK) return s;

    /* 段ごとに h と c を同期して進める。**c は毎段とも元の入力**（参照実装どおり） */
    static float h_tmp[DEC_W * CH], c_tmp[CD * CH];
    for (int i = 0; i < 5; ++i) {
        t -= im->dblk[i].pad;
        s = dec_step(st, i, h, c_sync, h_tmp, c_tmp, t);
        if (s != SAAN_OK) return s;
        memcpy(h, h_tmp, sizeof h_tmp);
        memcpy(c_sync, c_tmp, sizeof c_tmp);
    }

    SAAN_PROF_BEGIN(SAAN_PROF_HEAD);
    SAAN_TRY(saan_conv1d_w(im->hr, h, hdw, hdb, DEC_W, SAAN_DEC_HEAD, 1, CH, st->a));
    saan_gelu(im->hr, (size_t)SAAN_DEC_HEAD * CH);

    /* ⚠️ **hout は CH フレームまとめて計算する。** 1 フレームずつにすると
     * `o1539` が 49 KB → 6 KB に減るが、`saan_conv1d` の T=1 呼び出しが
     * 効率を落として**全体が 35% 遅くなる**（実測 0.023 → 0.031 × RT）。
     * ESP32 では速度が律速（移植可能 C で 0.93 × RT）なので**速度を取る**。
     * メモリは他の作業領域を正確に詰めて G1 を満たす。 */
    SAAN_TRY(saan_conv1d_w(im->o1539, im->hr, how, hob, SAAN_DEC_HEAD, 1539, 1, CH, st->a));
    SAAN_PROF_END(SAAN_PROF_HEAD);

    const float *mag = im->o1539;
    const float *cosv = im->o1539 + (size_t)513 * CH;
    const float *sinv = im->o1539 + (size_t)1026 * CH;
    for (int m = 0; m < CH; ++m) {
        const int32_t tt = t + m;          /* decoder 最終段の出力の絶対時刻 */
        if (tt < 0 || tt >= st->n_frames) continue;   /* 発話に存在しない */
        istft_push(im, mag, cosv, sinv, m, tt);
        while (istft_ready(im, st->n_frames)) {
            if (im->skip_hops > 0) {          /* 一括版が捨てる先頭 N/2 に相当 */
                istft_pop(im, im->obuf + (size_t)im->ofill * SAAN_HOP);
                --im->skip_hops;
                continue;                     /* ofill は増やさない = 捨てる */
            }
            /* ⚠️ obuf の深さは SAAN_OBUF_HOPS で閉じている（導出は saanotts_stream.h）。
             *    超えるなら隣のバッファを黙って壊すので、書く前に止める */
            if (im->ofill >= SAAN_OBUF_HOPS) return SAAN_ERR_ARENA;
            istft_pop(im, im->obuf + (size_t)im->ofill * SAAN_HOP);
            ++im->ofill;
        }
    }
    (void)pcm;
    return SAAN_OK;
}

static saan_status step_chunk(saan_stream *st, float *pcm) {
    SAAN_PROF_BEGIN(SAAN_PROF_STEP);
    const saan_status s = step_chunk_body(st, pcm);
    SAAN_PROF_END(SAAN_PROF_STEP);
    return s;
}

saan_status saan_stream_pull(saan_stream *st, float *pcm, int32_t *n_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    *n_out = 0;
    if (st->emitted >= st->n_frames) return SAAN_OK;      /* 発話の終わり */

    /* CH フレームぶん溜まるまでパイプラインを進める。
     * warmup（遅延 38 フレーム）の間は obuf に何も溜まらない。
     * ⚠️ **まだ出していないフレームが残っている間だけ step する**（T1）。
     *    iSTFT は `istft_ready` で n_frames hop ちょうどで止まるので、
     *    `emitted + ofill == n_frames` になったら obuf に全フレームが出そろっている。
     *    そこで ofill < CH でも回し続けると、末尾の pull だけ出力に 1 sample も
     *    寄与しない step_chunk が 3 回走る（106 frames の 1 文で 21 → 18 step。
     *    出力サンプル列は不変 = stream G2 が bit 一致で示す）。
     *    ⚠️ 陽性対照は `- 1` では**落ちない**（実測）。pull の境目では常に
     *    `emitted + ofill = 8m + 2`、`fpush = 8m + 4` で、fpush が n_frames に届く step が
     *    残りを**全部一度に**吐く（istft_ready）ため、境目に残る r = n_frames − (8m + 2) が
     *    3..10 フレームの範囲でしか壊れない。demo_ids.h の prefix 1..53（n_frames mod 8 の
     *    8 残差すべて + T=2 / T=5 の短い発話）を一括版と memcmp した実測（fp32 / W8A8）:
     *      `- 1`  0/53 落ちない
     *      `- 3`  5/53: n_frames ≡ 5 (mod 8) の 4/4 **と** T=2（ループが一度も回らず出力 0）
     *      `- 8`  40/53: demo（106 ≡ 2）は 98 で切れるが **≡ 3, 4 (mod 8) は素通り**（r が 9〜10）
     *      `- 10` 53/53（= `- (CH+2)`。全残差で落ちる唯一の値）
     *    「1 フレーム早く」は検出できず `- 8` も 2 残差を見逃すので、条件を触るなら `- (CH+2)` で
     *    確かめること（`make stream` の多文 G2 は ≡ 3, 4 の文を含む = T2a）。 */
    while (im->ofill < CH && st->emitted + im->ofill < st->n_frames) {
        saan_status s = step_chunk(st, pcm);
        if (s != SAAN_OK) return s;
        /* ⚠️ `used` ではなく `peak` を見る。W8A8 の activation 作業領域は
         * conv の中で確保して**すぐ返す**ので、`used` では捕まらない */
        if (st->a->peak > st->peak_used) st->peak_used = st->a->peak;
        if (im->ofill > st->ofill_max) st->ofill_max = im->ofill;
        /* 入力を出し切ってなお足りないなら打ち切る（無限ループ防止）。
         * ⚠️ **真に必要な余剰は遅延 SAAN_LATENCY + iSTFT の 2 フレームだけ。**
         * 以前は `+ 4*CH + 16` の安全マージンを積んでいたが、それは
         * **48 フレーム（5 チャンク）ぶん出力に寄与しない純粋な無駄**で、
         * 外しても PCM は bit 一致する（short で −18.6%、D-3b の照合）。
         * チャンク境界で切り上がるぶんだけ余裕を持たせる */
        if (st->pushed > st->n_frames + SAAN_LATENCY + 2 + 2 * CH) break;
    }

    int32_t n = im->ofill < CH ? im->ofill : CH;
    if (st->emitted + n > st->n_frames) n = st->n_frames - st->emitted;
    memcpy(pcm, im->obuf, sizeof(float) * (size_t)n * SAAN_HOP);
    if (im->ofill > n)
        memmove(im->obuf, im->obuf + (size_t)n * SAAN_HOP,
                sizeof(float) * (size_t)(im->ofill - n) * SAAN_HOP);
    im->ofill -= n;
    st->emitted += n;
    *n_out = n;
    return SAAN_OK;
}
