/* ストリーミング推論の本体（Phase D-2 / D-029）
 *
 * **方式: ステート保持型パイプライン。**
 *
 * 各段は入力を `[C][2*pad + CHUNK]` のバッファに溜める。左 pad は前チャンクの
 * 実データ、右 pad は次チャンクの実データ。**バッファ全体に conv を掛け、
 * 中央 CHUNK フレームだけを下流へ渡す。**
 *
 * ⚠️ **これが bit 一致（D-029 の G2）の根拠**: 中央フレーム t の計算に必要な
 * 入力は `[t-pad, t+pad]` だけで、それは全部バッファの中にある。バッファの外を
 * ゼロとみなす既存カーネルの挙動は、**中央 CHUNK フレームには影響しない**。
 * だから一括版とまったく同じ積和になる。カーネルも一括版と共有する。
 *
 * 計算量は各段 `(2*pad + CHUNK) / CHUNK` 倍（pad=3, CHUNK=8 で 1.75 倍）。
 * ハロー再計算方式（全体で 10 倍）よりはるかに軽い。
 *
 * ⚠️ **代償はレイテンシ。** 出力は受容野ぶん 36 フレーム = 0.42 秒遅れる。
 */
#include "saanotts_stream.h"
#include "saanotts_internal.h"

#include "fft.h"

#include <math.h>
#include <string.h>

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
    for (int c = 0; c < p->C; ++c) {
        float *row = p->buf + (size_t)c * p->W;
        memmove(row, row + CH, sizeof(float) * (size_t)(p->W - CH));
        float *tail = row + p->W - CH;
        if (src) memcpy(tail, src + (size_t)c * CH, sizeof(float) * CH);
        else memset(tail, 0, sizeof(float) * CH);
    }
}

/* 中央 CH フレームを [C][CH] として dst に取り出す */
static void pipe_center(const pipe_t *p, const float *full, float *dst) {
    for (int c = 0; c < p->C; ++c)
        memcpy(dst + (size_t)c * CH, full + (size_t)c * p->W + p->pad,
               sizeof(float) * CH);
}

/* --- 内部状態 ------------------------------------------------------------- */

struct saan_stream_impl {
    pipe_t ac[5];        /* AcBlock。pad=4（c1 k=5 と c2 k=5 で ±2+±2） */
    pipe_t dinp;         /* decoder inp。pad=1 */
    pipe_t dblk[5];      /* dw ブロック。pad=3 */
    pipe_t cdel[6];      /* 各 decoder 段に同期させる c（conv しないので遅延だけ） */

    float *w_full;       /* [max(C) * max(W)] 段内の一時（conv 出力） */
    float *w_full2;
    float *w_ch;         /* [max(C) * CH] 中央の取り出し */
    float *w_ch2;
    float *w_e;          /* [E * maxW] pw1 の出力 */
    float *w_r;          /* [R * maxW] */
    float *w_g;          /* [DEC_W * maxW] */
    float *o1539;        /* [1539] decoder の生出力 **1 フレーム分**（mag/cos/sin のビュー） */
    float *hr;           /* [DEC_HEAD * CH] */

    float *ola, *olw, *win, *re, *im, *frm;   /* iSTFT */
    int32_t fpush;       /* push 済みフレーム数（絶対フレーム番号 + 1） */
    int32_t out_pos;     /* 次に出す絶対サンプル位置 */
    int32_t skip_hops;   /* 捨てる先頭 hop 数（一括版の N/2 切り出しに対応） */
    int ola_len;
    float *obuf;         /* [(CH + 4) * HOP] 出力の詰め替え */
    int32_t ofill;
    float *tok_buf, *tok_w1, *tok_w2, *tok_out;   /* token チャンクの作業領域 */
};

size_t saan_stream_arena_needed(int32_t n_ids) {
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;
    size_t s = 0;
    /* --- 発話長（ids 数）に比例する分 --- */
    s += SAAN_ALIGN16(sizeof(float) * (size_t)n_ids);            /* log_d */
    s += SAAN_ALIGN16(sizeof(int32_t) * (size_t)n_ids);          /* d_hat */
    /* duration と token block の一時領域（run 後に返すが、確保は要る） */
    s += SAAN_ALIGN16(sizeof(float) * SAAN_DUR_W * (size_t)n_ids) * 3;
    s += SAAN_ALIGN16(sizeof(float) * AC_W * TOK_MAXW) * 3;
    s += SAAN_ALIGN16(sizeof(float) * AC_W * CH);
    /* --- 発話長に依存しない分（G3 の対象） --- */
    s += SAAN_ALIGN16(sizeof(struct saan_stream_impl));
    s += SAAN_ALIGN16(sizeof(float) * AC_W * (2 * 4 + CH)) * 5;   /* ac */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 1 + CH));         /* dinp */
    s += SAAN_ALIGN16(sizeof(float) * DEC_W * (2 * 3 + CH)) * 5;  /* dblk */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 1 + CH));         /* cdel[0] */
    s += SAAN_ALIGN16(sizeof(float) * CD * (2 * 3 + CH)) * 5;     /* cdel[1..5] */
    {
        const int W_AC = 2 * 4 + CH, W_DEC = 2 * 3 + CH;
        const size_t full_n = (size_t)AC_W * W_AC > (size_t)DEC_W * W_DEC
                            ? (size_t)AC_W * W_AC : (size_t)DEC_W * W_DEC;
        s += SAAN_ALIGN16(sizeof(float) * full_n) * 2;            /* w_full/2 */
        s += SAAN_ALIGN16(sizeof(float) * (size_t)maxC * CH) * 2; /* w_ch/2 */
        s += SAAN_ALIGN16(sizeof(float) * (size_t)E * W_DEC);
        s += SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_R * W_DEC);
        s += SAAN_ALIGN16(sizeof(float) * (size_t)DEC_W * W_DEC);
    }
    s += SAAN_ALIGN16(sizeof(float) * 1539 * CH);
    s += SAAN_ALIGN16(sizeof(float) * (size_t)SAAN_DEC_HEAD * CH);
    s += SAAN_ALIGN16(sizeof(float) * (size_t)(SAAN_NFFT + 2 * SAAN_HOP)) * 2;
    s += SAAN_ALIGN16(sizeof(float) * (size_t)(CH + 4) * SAAN_HOP);
    s += SAAN_ALIGN16(sizeof(float) * SAAN_NFFT) * 2;
    s += SAAN_ALIGN16(sizeof(float) * NB) * 2;
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

/* AcBlock 1 段。buf 全体に conv を掛け、中央 CH を out へ。
 * 参照実装 `AcBlock.forward`: `x + LN(c2(relu(c1(x))))` */
static saan_status ac_step(saan_stream *st, int bi, const float *in,
                           float *out, int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->ac[bi];
    const saan_weights *w = st->w;
    const float *c1w = saan_tf(w, "acoustic.frame.%d.c1.weight", bi);
    const float *c1b = saan_tf(w, "acoustic.frame.%d.c1.bias", bi);
    const float *c2w = saan_tf(w, "acoustic.frame.%d.c2.weight", bi);
    const float *c2b = saan_tf(w, "acoustic.frame.%d.c2.bias", bi);
    const float *ng = saan_tf(w, "acoustic.frame.%d.norm.weight", bi);
    const float *nb = saan_tf(w, "acoustic.frame.%d.norm.bias", bi);
    if (!c1w || !c2w || !ng) return SAAN_ERR_MISSING;

    pipe_push(p, in);
    const int W = p->W;
    /* buf の先頭が対応する絶対時刻。中央の先頭 t_out から pad 引いた位置 */
    const int32_t t_buf = t_out - p->pad;

    saan_conv1d(im->w_full, p->buf, c1w, c1b, AC_W, AC_W, 5, W);
    saan_relu(im->w_full, (size_t)AC_W * W);
    /* ⚠️ **c1 の出力にもゼロクリアが要る。** 一括版では c1 の配列は [0,T) しかなく、
     * c2（k=5）がその外を参照するとゼロになる。ストリーミングは c1 を窓幅で
     * 計算するので、発話外にも **bias 由来の非ゼロ**が残る。
     * 実測: これが無いと先頭 pad フレームが max|Δ| 0.49 ずれた */
    zero_outside_n(im->w_full, AC_W, W, W, t_buf, st->n_frames);
    saan_conv1d(im->w_full2, im->w_full, c2w, c2b, AC_W, AC_W, 5, W);
    saan_layernorm_c(im->w_full2, ng, nb, AC_W, W);
    for (size_t i = 0; i < (size_t)AC_W * W; ++i) im->w_full2[i] += p->buf[i];
    pipe_center(p, im->w_full2, out);
    zero_outside(out, AC_W, t_out, st->n_frames);
    return SAAN_OK;
}

/* decoder の inp（k=3）。参照実装 `Decoder.forward` の 1 行目 */
static saan_status dec_inp_step(saan_stream *st, const float *c_in, float *h_out,
                                float *c_out, int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->dinp, *pc = &im->cdel[0];
    const float *iw = saan_tf(st->w, "decoder.inp.weight");
    const float *ib = saan_tf(st->w, "decoder.inp.bias");
    if (!iw) return SAAN_ERR_MISSING;

    pipe_push(p, c_in);           /* inp の入力は c そのもの（C=CD） */
    pipe_push(pc, c_in);          /* 下流の条件付け用に同じ c を同期させる */
    /* ⚠️ **作業バッファに `w_full` を使わない。** 呼び出し側は `h_out` に
     * `w_full` を渡してくるので、同じ領域に conv 出力を書くと
     * ストライドの違い（W=10 vs CH=8）で**自分の読み込み元を壊す**。
     * 実測で ch=3 以降が壊れ、全サンプルが不一致になった */
    saan_conv1d(im->w_g, p->buf, iw, ib, CD, DEC_W, 3, p->W);
    /* h は [DEC_W][W]。中央を取る（pipe_center は p->C を使うので手で書く） */
    for (int ch = 0; ch < DEC_W; ++ch)
        memcpy(h_out + (size_t)ch * CH,
               im->w_g + (size_t)ch * p->W + p->pad, sizeof(float) * CH);
    pipe_center(pc, pc->buf, c_out);
    zero_outside(h_out, DEC_W, t_out, st->n_frames);
    zero_outside(c_out, CD, t_out, st->n_frames);
    return SAAN_OK;
}

/* dw ブロック 1 段。参照実装:
 *   g = cup(cdown(c));  h = h + gamma * pw2(gelu(pw1(dw(h) + g)))
 * ⚠️ **c は毎段とも元の入力**（h ではない）。時刻を h と揃えるため遅延させる */
static saan_status dec_step(saan_stream *st, int i, const float *h_in,
                            const float *c_in, float *h_out, float *c_out,
                            int32_t t_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    pipe_t *p = &im->dblk[i], *pc = &im->cdel[i + 1];
    const saan_weights *w = st->w;
    const float *dw = saan_tf(w, "decoder.dw.%d.weight", i);
    const float *p1w = saan_tf(w, "decoder.pw1.%d.weight", i);
    const float *p1b = saan_tf(w, "decoder.pw1.%d.bias", i);
    const float *p2w = saan_tf(w, "decoder.pw2.%d.weight", i);
    const float *p2b = saan_tf(w, "decoder.pw2.%d.bias", i);
    const float *cdw = saan_tf(w, "decoder.cdown.%d.weight", i);
    const float *cdb = saan_tf(w, "decoder.cdown.%d.bias", i);
    const float *cuw = saan_tf(w, "decoder.cup.%d.weight", i);
    const float *cub = saan_tf(w, "decoder.cup.%d.bias", i);
    const float *gm = saan_tf(w, "decoder.gamma.%d", i);
    if (!dw || !p1w || !cdw || !gm) return SAAN_ERR_MISSING;

    pipe_push(p, h_in);
    pipe_push(pc, c_in);
    const int W = p->W;
    /* 条件付けは 1x1 なので pad 不要。**pc の buf 全体**に掛けて h と同じ幅にする */
    saan_conv1d(im->w_r, pc->buf, cdw, cdb, CD, SAAN_DEC_R, 1, W);
    saan_conv1d(im->w_g, im->w_r, cuw, cub, SAAN_DEC_R, DEC_W, 1, W);

    saan_dwconv1d(im->w_full, p->buf, dw, DEC_W, 7, W);
    for (size_t k = 0; k < (size_t)DEC_W * W; ++k) im->w_full[k] += im->w_g[k];
    saan_conv1d(im->w_e, im->w_full, p1w, p1b, DEC_W, E, 1, W);
    saan_gelu(im->w_e, (size_t)E * W);
    saan_conv1d(im->w_full2, im->w_e, p2w, p2b, E, DEC_W, 1, W);
    for (size_t k = 0; k < (size_t)DEC_W * W; ++k)
        im->w_full2[k] = p->buf[k] + gm[0] * im->w_full2[k];

    pipe_center(p, im->w_full2, h_out);
    pipe_center(pc, pc->buf, c_out);
    zero_outside(h_out, DEC_W, t_out, st->n_frames);
    zero_outside(c_out, CD, t_out, st->n_frames);
    return SAAN_OK;
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
}

/* リングから HOP サンプル取り出して先へ進める。
 * ⚠️ **呼ぶのはそのサンプルへの寄与が出そろってから。** サンプル p に寄与する
 * 最大フレームは floor(p/hop) なので、一括版が切り出す `[N/2, ...)` の先頭
 * （= フレーム 2）まで push してからでないと値が違う。`istft_ready()` で判定する */
static void istft_pop(struct saan_stream_impl *im, float *out) {
    const int L = im->ola_len;
    for (int i = 0; i < SAAN_HOP; ++i) {
        const int j = (int)((im->out_pos + i) % L);
        out[i] = im->olw[j] > 1e-11f ? im->ola[j] / im->olw[j] : 0.0f;
        im->ola[j] = 0.0f;
        im->olw[j] = 0.0f;
    }
    im->out_pos += SAAN_HOP;
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

saan_status saan_stream_init(saan_stream *st, const saan_weights *w,
                             saan_arena *a, const int32_t *ids, int32_t n_ids,
                             float s_v) {
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

    for (int i = 0; i < 5; ++i) if (!pipe_init(&im->ac[i], a, AC_W, 4)) return SAAN_ERR_ARENA;
    if (!pipe_init(&im->dinp, a, CD, 1)) return SAAN_ERR_ARENA;
    for (int i = 0; i < 5; ++i) if (!pipe_init(&im->dblk[i], a, DEC_W, 3)) return SAAN_ERR_ARENA;
    /* c の遅延: dinp は pad=1、各 dblk は pad=3 */
    if (!pipe_init(&im->cdel[0], a, CD, 1)) return SAAN_ERR_ARENA;
    for (int i = 1; i < 6; ++i) if (!pipe_init(&im->cdel[i], a, CD, 3)) return SAAN_ERR_ARENA;

    /* ⚠️ **作業領域は段ごとの実寸で取る。** 以前は maxC(76) × maxW(16) を
     * 一律に確保していたが、acoustic は C=48/W=16、decoder は C=76/W=14 で、
     * 76×16 は**どちらにも要らない上限**だった。E×W も decoder でしか使わない */
    const int W_AC = 2 * 4 + CH;    /* AcBlock の窓 */
    const int W_DEC = 2 * 3 + CH;   /* dw ブロックの窓 */
    const size_t full_n = (size_t)AC_W * W_AC > (size_t)DEC_W * W_DEC
                        ? (size_t)AC_W * W_AC : (size_t)DEC_W * W_DEC;
    const int maxC = DEC_W > AC_W ? DEC_W : AC_W;
    im->w_full  = (float *)saan_alloc(a, sizeof(float) * full_n);
    im->w_full2 = (float *)saan_alloc(a, sizeof(float) * full_n);
    im->w_ch    = (float *)saan_alloc(a, sizeof(float) * (size_t)maxC * CH);
    im->w_ch2   = (float *)saan_alloc(a, sizeof(float) * (size_t)maxC * CH);
    im->w_e     = (float *)saan_alloc(a, sizeof(float) * (size_t)E * W_DEC);
    im->w_r     = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_DEC_R * W_DEC);
    im->w_g     = (float *)saan_alloc(a, sizeof(float) * (size_t)DEC_W * W_DEC);
    im->o1539   = (float *)saan_alloc(a, sizeof(float) * 1539 * CH);
    im->hr      = (float *)saan_alloc(a, sizeof(float) * (size_t)SAAN_DEC_HEAD * CH);
    im->ola_len = SAAN_NFFT + 2 * SAAN_HOP;   /* out_pos が N/2 先行する分 */
    im->ola     = (float *)saan_alloc(a, sizeof(float) * (size_t)im->ola_len);
    im->olw     = (float *)saan_alloc(a, sizeof(float) * (size_t)im->ola_len);
    im->win     = (float *)saan_alloc(a, sizeof(float) * SAAN_NFFT);
    im->re      = (float *)saan_alloc(a, sizeof(float) * NB);
    im->im      = (float *)saan_alloc(a, sizeof(float) * NB);
    im->frm     = (float *)saan_alloc(a, sizeof(float) * SAAN_NFFT);
    if (!im->frm) return SAAN_ERR_ARENA;
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
    im->obuf = (float *)saan_alloc(a, sizeof(float) * (size_t)(CH + 4) * SAAN_HOP);
    im->tok_buf = (float *)saan_alloc(a, sizeof(float) * AC_W * TOK_MAXW);
    im->tok_w1  = (float *)saan_alloc(a, sizeof(float) * AC_W * TOK_MAXW);
    im->tok_w2  = (float *)saan_alloc(a, sizeof(float) * AC_W * TOK_MAXW);
    im->tok_out = (float *)saan_alloc(a, sizeof(float) * AC_W * CH);
    if (!im->obuf || !im->tok_out) return SAAN_ERR_ARENA;
    im->ofill = 0;
    st->peak_used = a->used;
    return SAAN_OK;
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
static saan_status compute_tokens(saan_stream *st, int32_t i0, int32_t i1,
                                  float *out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const saan_weights *w = st->w;
    const float *emb = saan_tf(w, "acoustic.emb.weight");
    if (!emb) return SAAN_ERR_MISSING;

    const int32_t lo = i0 - TOK_HALO, hi = i1 + TOK_HALO;
    const int n = (int)(hi - lo);
    if (n > TOK_MAXW) return SAAN_ERR_SHAPE;

    float *h = im->tok_buf;
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
        const float *c1w = saan_tf(w, "acoustic.token.%d.c1.weight", bi);
        const float *c1b = saan_tf(w, "acoustic.token.%d.c1.bias", bi);
        const float *c2w = saan_tf(w, "acoustic.token.%d.c2.weight", bi);
        const float *c2b = saan_tf(w, "acoustic.token.%d.c2.bias", bi);
        const float *ng = saan_tf(w, "acoustic.token.%d.norm.weight", bi);
        const float *nb = saan_tf(w, "acoustic.token.%d.norm.bias", bi);
        if (!c1w || !c2w || !ng) return SAAN_ERR_MISSING;
        saan_conv1d(im->tok_w1, h, c1w, c1b, AC_W, AC_W, 5, n);
        saan_relu(im->tok_w1, (size_t)AC_W * n);
        /* ⚠️ c1 の出力の発話外もゼロに（一括版では配列外＝ゼロ、frame 側と同じ理由） */
        zero_outside_n(im->tok_w1, AC_W, n, n, lo, st->n_ids);
        saan_conv1d(im->tok_w2, im->tok_w1, c2w, c2b, AC_W, AC_W, 5, n);
        saan_layernorm_c(im->tok_w2, ng, nb, AC_W, n);
        for (size_t x = 0; x < (size_t)AC_W * n; ++x) im->tok_w2[x] += h[x];
        zero_outside_n(im->tok_w2, AC_W, n, n, lo, st->n_ids);
        memcpy(h, im->tok_w2, sizeof(float) * (size_t)AC_W * n);
    }
    for (int c = 0; c < AC_W; ++c)
        memcpy(out + (size_t)c * (i1 - i0), h + (size_t)c * n + TOK_HALO,
               sizeof(float) * (size_t)(i1 - i0));
    return SAAN_OK;
}

/* CH フレームぶんの `hf`（length regulator の出力）を作る。
 * 参照実装の `repeat_interleave` + 位置埋め込みと同じ。
 * `f0` は絶対フレーム番号。範囲外（発話末尾より後ろ）は**ゼロ**にする */
static saan_status make_hf(saan_stream *st, int32_t f0, float *out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const float *pos = saan_tf(st->w, "acoustic.pos.weight");
    if (!pos) return SAAN_ERR_MISSING;

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

/* パイプラインを CH フレーム進める。出力は `pcm`（CH * HOP サンプル） */
static saan_status step_chunk(saan_stream *st, float *pcm) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    const float *ow = saan_tf(st->w, "acoustic.out.weight");
    const float *hdw = saan_tf(st->w, "decoder.hdown.weight");
    const float *hdb = saan_tf(st->w, "decoder.hdown.bias");
    const float *how = saan_tf(st->w, "decoder.hout.weight");
    const float *hob = saan_tf(st->w, "decoder.hout.bias");
    if (!ow || !hdw || !how) return SAAN_ERR_MISSING;

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
    saan_conv1d(c_cur, a, ow, NULL, AC_W, CD, 1, CH);

    if (st->dbg_c) {          /* デバッグ: c を絶対時刻で控える */
        for (int m = 0; m < CH; ++m) {
            const int32_t tt = t + m;   /* t は ac 5 段を通った後の出力時刻 */
            if (tt < 0 || tt >= st->dbg_cap) continue;
            for (int ch = 0; ch < CD; ++ch)
                st->dbg_c[(size_t)ch * st->dbg_cap + tt] = c_cur[(size_t)ch * CH + m];
        }
    }

    float *h = im->w_full;      /* [DEC_W][CH] として使い回す */
    float *c_sync = im->w_full2;
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

    saan_conv1d(im->hr, h, hdw, hdb, DEC_W, SAAN_DEC_HEAD, 1, CH);
    saan_gelu(im->hr, (size_t)SAAN_DEC_HEAD * CH);

    /* ⚠️ **hout は CH フレームまとめて計算する。** 1 フレームずつにすると
     * `o1539` が 49 KB → 6 KB に減るが、`saan_conv1d` の T=1 呼び出しが
     * 効率を落として**全体が 35% 遅くなる**（実測 0.023 → 0.031 × RT）。
     * ESP32 では速度が律速（移植可能 C で 0.93 × RT）なので**速度を取る**。
     * メモリは他の作業領域を正確に詰めて G1 を満たす。 */
    saan_conv1d(im->o1539, im->hr, how, hob, SAAN_DEC_HEAD, 1539, 1, CH);

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
            istft_pop(im, im->obuf + (size_t)im->ofill * SAAN_HOP);
            ++im->ofill;
        }
    }
    (void)pcm;
    return SAAN_OK;
}

saan_status saan_stream_pull(saan_stream *st, float *pcm, int32_t *n_out) {
    struct saan_stream_impl *im = (struct saan_stream_impl *)st->impl;
    *n_out = 0;
    if (st->emitted >= st->n_frames) return SAAN_OK;      /* 発話の終わり */

    /* CH フレームぶん溜まるまでパイプラインを進める。
     * warmup（遅延 38 フレーム）の間は obuf に何も溜まらない */
    while (im->ofill < CH) {
        saan_status s = step_chunk(st, pcm);
        if (s != SAAN_OK) return s;
        if (st->a->used > st->peak_used) st->peak_used = st->a->used;
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
