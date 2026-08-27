/* Phase D-3b: レイテンシ測定ハーネス
 *
 * **このプロジェクトはレイテンシを一度も測っていない。** ここで測るのは
 * 「手元のホストで音声 1 秒を何秒で作るか（× RT）」を、**段別**に出すこと。
 *
 * ⚠️ **ESP32 への外挿はここではやらない。** 推測の係数を掛けた数値は出さない。
 *
 * 測り方は 2 本立て:
 *
 *   (A) 全体   公開 API（`saan_stream_init` + `saan_stream_pull`）だけで
 *              1 発話を最後まで合成した実時間。**これが唯一の「真の値」**。
 *   (B) 段別   `saanotts_stream.c` を編集せずに内訳を出すため、各段の
 *              カーネル列を**同じ shape・同じ重み**でこのファイル内に再現し、
 *              単体で回して 1 チャンク（または 1 フレーム）あたりの時間を出す。
 *              それに実際の呼び出し回数を掛けて段別の ms にする。
 *
 * ⚠️ **(B) はモデルであって計測そのものではない。** 妥当性は
 *   Σ(段別) と (A) の比（reconciliation）で検証する。乖離が大きければ
 *   段別の数値は信用しないこと。
 *
 *   cc -std=c99 -O2 -Wall -Wextra -o bench bench.c saanotts.c saanotts_stream.c -lm
 *   ./bench --weights student.bin --golden golden.bin --reps 10 --json ../reports/d3b_latency.json
 */
#define _POSIX_C_SOURCE 199309L

#include "saanotts.h"
#include "fft.h"
#include "saanotts_stream.h"
#include "saanotts_internal.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* _POSIX_C_SOURCE を立てると macOS では M_PI が隠れる（clock_gettime に必要） */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define CH   SAAN_CHUNK
#define CD   SAAN_CDIM
#define ACW  SAAN_AC_W
#define DECW SAAN_DEC_W
#define DECE SAAN_DEC_E
#define DECR SAAN_DEC_R
#define NB   SAAN_NBINS
#define NFFT SAAN_NFFT
#define HOP  SAAN_HOP

/* saanotts_stream.c と同じ値。**ここを変えたら向こうも変わっている** */
#define TOK_HALO 12
#define TOK_MAXW (CH + 2 * TOK_HALO)
#define AC_PAD   4
#define DINP_PAD 1
#define DBLK_PAD 3
#define AC_W_BUF   (2 * AC_PAD + CH)     /* 16 */
#define DINP_W_BUF (2 * DINP_PAD + CH)   /* 10 */
#define DBLK_W_BUF (2 * DBLK_PAD + CH)   /* 14 */

/* --- 時計 ----------------------------------------------------------------- */

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec * 1e-6;
}

#define MAXR 64

typedef struct { double mean, sd, min, max, median; int n; } stat_t;

static int cmpd(const void *a, const void *b) {
    const double x = *(const double *)a, y = *(const double *)b;
    return x < y ? -1 : (x > y ? 1 : 0);
}

/* ⚠️ **min も併記する。** 他プロセスと CPU を取り合うと mean と sd は上振れするが、
 * min は汚染されにくい（下振れする外乱は無い）。mean/min の開きが大きい実行は
 * 「静かな機械で測り直せ」の合図。 */
static stat_t stat_of(const double *v, int n) {
    stat_t s; s.n = n; s.min = v[0]; s.max = v[0];
    double m = 0.0;
    for (int i = 0; i < n; ++i) {
        m += v[i];
        if (v[i] < s.min) s.min = v[i];
        if (v[i] > s.max) s.max = v[i];
    }
    s.mean = m / (double)n;
    double q = 0.0;
    for (int i = 0; i < n; ++i) { const double d = v[i] - s.mean; q += d * d; }
    s.sd = n > 1 ? sqrt(q / (double)(n - 1)) : 0.0;   /* 標本標準偏差 */
    double srt[MAXR];
    const int nn = n < MAXR ? n : MAXR;
    for (int i = 0; i < nn; ++i) srt[i] = v[i];
    qsort(srt, (size_t)nn, sizeof(double), cmpd);
    s.median = nn % 2 ? srt[nn / 2] : 0.5 * (srt[nn / 2 - 1] + srt[nn / 2]);
    return s;
}

/* --- ファイル ------------------------------------------------------------- */

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(1);
    }
    fclose(f);
    *size = (size_t)n;
    return b;
}

/* --- 段別マイクロベンチの作業領域 ------------------------------------------ */

typedef struct {
    /* acoustic frame */
    float *ac_buf, *ac_f1, *ac_f2, *ac_a, *ac_b;
    /* token block */
    float *tk_h, *tk_w1, *tk_w2, *tk_out;
    /* decoder */
    float *dinp_buf, *cdel0, *cdel[5], *dblk[5];
    float *w_full, *w_full2, *w_e, *w_r, *w_g, *o1539, *hr;
    float *h, *c_sync, *h_tmp, *c_tmp, *c_cur;
    /* iSTFT */
    float *mag, *cosv, *sinv, *re, *im, *frm, *win, *ola, *olw, *pop;
    int ola_len;
} work_t;

static float *fz(size_t n) {
    float *p = (float *)calloc(n, sizeof(float));
    if (!p) { fprintf(stderr, "malloc 失敗\n"); exit(1); }
    /* ゼロだと conv の分岐（wv==0 スキップ）や denormal で速度が変わりうるので、
     * **入力側は小さな非ゼロで埋める**。重みは本物を使う */
    for (size_t i = 0; i < n; ++i) p[i] = 0.01f * (float)((i % 17) - 8);
    return p;
}

static void work_init(work_t *k) {
    k->ac_buf = fz((size_t)ACW * AC_W_BUF);
    k->ac_f1  = fz((size_t)ACW * AC_W_BUF);
    k->ac_f2  = fz((size_t)ACW * AC_W_BUF);
    k->ac_a   = fz((size_t)ACW * CH);
    k->ac_b   = fz((size_t)ACW * CH);

    k->tk_h   = fz((size_t)ACW * TOK_MAXW);
    k->tk_w1  = fz((size_t)ACW * TOK_MAXW);
    k->tk_w2  = fz((size_t)ACW * TOK_MAXW);
    k->tk_out = fz((size_t)ACW * CH);

    k->dinp_buf = fz((size_t)CD * DINP_W_BUF);
    k->cdel0    = fz((size_t)CD * DINP_W_BUF);
    for (int i = 0; i < 5; ++i) {
        k->cdel[i] = fz((size_t)CD * DBLK_W_BUF);
        k->dblk[i] = fz((size_t)DECW * DBLK_W_BUF);
    }
    k->w_full  = fz((size_t)DECW * AC_W_BUF);
    k->w_full2 = fz((size_t)DECW * AC_W_BUF);
    k->w_e     = fz((size_t)DECE * AC_W_BUF);
    k->w_r     = fz((size_t)DECR * AC_W_BUF);
    k->w_g     = fz((size_t)DECW * AC_W_BUF);
    k->o1539   = fz((size_t)1539 * CH);
    k->hr      = fz((size_t)SAAN_DEC_HEAD * CH);
    k->h       = fz((size_t)DECW * CH);
    k->c_sync  = fz((size_t)CD * CH);
    k->h_tmp   = fz((size_t)DECW * CH);
    k->c_tmp   = fz((size_t)CD * CH);
    k->c_cur   = fz((size_t)CD * CH);

    k->mag  = fz((size_t)NB * CH);
    k->cosv = fz((size_t)NB * CH);
    k->sinv = fz((size_t)NB * CH);
    k->re   = fz((size_t)NB);
    k->im   = fz((size_t)NB);
    k->frm  = fz((size_t)NFFT);
    k->win  = fz((size_t)NFFT);
    k->ola_len = NFFT + 2 * HOP;
    k->ola  = fz((size_t)k->ola_len);
    k->olw  = fz((size_t)k->ola_len);
    k->pop  = fz((size_t)HOP);
    for (int i = 0; i < NFFT; ++i)
        k->win[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * (float)i / (float)NFFT);
    /* cos/sin は [-1,1] に収める（本物の decoder 出力と同じレンジ） */
    for (int i = 0; i < NB * CH; ++i) {
        k->mag[i]  = 0.1f;
        k->cosv[i] = 0.6f;
        k->sinv[i] = 0.8f;
    }
}

/* パイプ段の push / center（`saanotts_stream.c` の pipe_push / pipe_center と同じ） */
static void bpush(float *buf, int C, int W, const float *src) {
    for (int c = 0; c < C; ++c) {
        float *row = buf + (size_t)c * W;
        memmove(row, row + CH, sizeof(float) * (size_t)(W - CH));
        memcpy(row + W - CH, src + (size_t)c * CH, sizeof(float) * CH);
    }
}
static void bcenter(const float *buf, int C, int W, int pad, float *dst) {
    for (int c = 0; c < C; ++c)
        memcpy(dst + (size_t)c * CH, buf + (size_t)c * W + pad, sizeof(float) * CH);
}

/* --- 段別: acoustic frame（1 チャンク = CH フレーム） ---------------------- */
/* `step_chunk` の ac 5 段 + `acoustic.out` に対応 */
static void mb_acoustic(const saan_weights *w, work_t *k) {
    const int W = AC_W_BUF;
    float *a = k->ac_a, *b = k->ac_b;
    for (int bi = 0; bi < 5; ++bi) {
        const float *c1w = saan_tf(w, "acoustic.frame.%d.c1.weight", bi);
        const float *c1b = saan_tf(w, "acoustic.frame.%d.c1.bias", bi);
        const float *c2w = saan_tf(w, "acoustic.frame.%d.c2.weight", bi);
        const float *c2b = saan_tf(w, "acoustic.frame.%d.c2.bias", bi);
        const float *ng  = saan_tf(w, "acoustic.frame.%d.norm.weight", bi);
        const float *nb  = saan_tf(w, "acoustic.frame.%d.norm.bias", bi);
        bpush(k->ac_buf, ACW, W, a);
        saan_conv1d(k->ac_f1, k->ac_buf, c1w, c1b, ACW, ACW, 5, W);
        saan_relu(k->ac_f1, (size_t)ACW * W);
        saan_conv1d(k->ac_f2, k->ac_f1, c2w, c2b, ACW, ACW, 5, W);
        saan_layernorm_c(k->ac_f2, ng, nb, ACW, W);
        for (size_t i = 0; i < (size_t)ACW * W; ++i) k->ac_f2[i] += k->ac_buf[i];
        bcenter(k->ac_f2, ACW, W, AC_PAD, b);
        float *t = a; a = b; b = t;
    }
    const float *ow = saan_tf(w, "acoustic.out.weight");
    saan_conv1d(k->c_cur, a, ow, NULL, ACW, CD, 1, CH);
}

/* --- 段別: token block（1 チャンク。span はチャンクごとに違う） ------------ */
static void mb_token(const saan_weights *w, work_t *k, int span) {
    const int n = span + 2 * TOK_HALO;
    for (int bi = 0; bi < 3; ++bi) {
        const float *c1w = saan_tf(w, "acoustic.token.%d.c1.weight", bi);
        const float *c1b = saan_tf(w, "acoustic.token.%d.c1.bias", bi);
        const float *c2w = saan_tf(w, "acoustic.token.%d.c2.weight", bi);
        const float *c2b = saan_tf(w, "acoustic.token.%d.c2.bias", bi);
        const float *ng  = saan_tf(w, "acoustic.token.%d.norm.weight", bi);
        const float *nb  = saan_tf(w, "acoustic.token.%d.norm.bias", bi);
        saan_conv1d(k->tk_w1, k->tk_h, c1w, c1b, ACW, ACW, 5, n);
        saan_relu(k->tk_w1, (size_t)ACW * n);
        saan_conv1d(k->tk_w2, k->tk_w1, c2w, c2b, ACW, ACW, 5, n);
        saan_layernorm_c(k->tk_w2, ng, nb, ACW, n);
        for (size_t x = 0; x < (size_t)ACW * n; ++x) k->tk_w2[x] += k->tk_h[x];
        memcpy(k->tk_h, k->tk_w2, sizeof(float) * (size_t)ACW * n);
    }
    const float *pos = saan_tf(w, "acoustic.pos.weight");
    for (int m = 0; m < CH; ++m)
        for (int c = 0; c < ACW; ++c)
            k->ac_a[(size_t)c * CH + m] =
                k->tk_h[(size_t)c * n + TOK_HALO] + pos[(size_t)(m % SAAN_POS_MAX) * ACW + c];
}

/* --- 段別: decoder（1 チャンク。iSTFT は含まない） ------------------------- */
static void mb_decoder(const saan_weights *w, work_t *k) {
    const float *iw = saan_tf(w, "decoder.inp.weight");
    const float *ib = saan_tf(w, "decoder.inp.bias");
    bpush(k->dinp_buf, CD, DINP_W_BUF, k->c_cur);
    bpush(k->cdel0,    CD, DINP_W_BUF, k->c_cur);
    saan_conv1d(k->w_g, k->dinp_buf, iw, ib, CD, DECW, 3, DINP_W_BUF);
    for (int ch = 0; ch < DECW; ++ch)
        memcpy(k->h + (size_t)ch * CH,
               k->w_g + (size_t)ch * DINP_W_BUF + DINP_PAD, sizeof(float) * CH);
    bcenter(k->cdel0, CD, DINP_W_BUF, DINP_PAD, k->c_sync);

    const int W = DBLK_W_BUF;
    for (int i = 0; i < 5; ++i) {
        const float *dw  = saan_tf(w, "decoder.dw.%d.weight", i);
        const float *p1w = saan_tf(w, "decoder.pw1.%d.weight", i);
        const float *p1b = saan_tf(w, "decoder.pw1.%d.bias", i);
        const float *p2w = saan_tf(w, "decoder.pw2.%d.weight", i);
        const float *p2b = saan_tf(w, "decoder.pw2.%d.bias", i);
        const float *cdw = saan_tf(w, "decoder.cdown.%d.weight", i);
        const float *cdb = saan_tf(w, "decoder.cdown.%d.bias", i);
        const float *cuw = saan_tf(w, "decoder.cup.%d.weight", i);
        const float *cub = saan_tf(w, "decoder.cup.%d.bias", i);
        const float *gm  = saan_tf(w, "decoder.gamma.%d", i);
        bpush(k->dblk[i], DECW, W, k->h);
        bpush(k->cdel[i], CD,   W, k->c_sync);
        saan_conv1d(k->w_r, k->cdel[i], cdw, cdb, CD, DECR, 1, W);
        saan_conv1d(k->w_g, k->w_r, cuw, cub, DECR, DECW, 1, W);
        saan_dwconv1d(k->w_full, k->dblk[i], dw, DECW, 7, W);
        for (size_t x = 0; x < (size_t)DECW * W; ++x) k->w_full[x] += k->w_g[x];
        saan_conv1d(k->w_e, k->w_full, p1w, p1b, DECW, DECE, 1, W);
        saan_gelu(k->w_e, (size_t)DECE * W);
        saan_conv1d(k->w_full2, k->w_e, p2w, p2b, DECE, DECW, 1, W);
        for (size_t x = 0; x < (size_t)DECW * W; ++x)
            k->w_full2[x] = k->dblk[i][x] + gm[0] * k->w_full2[x];
        bcenter(k->w_full2, DECW, W, DBLK_PAD, k->h_tmp);
        bcenter(k->cdel[i], CD, W, DBLK_PAD, k->c_tmp);
        memcpy(k->h, k->h_tmp, sizeof(float) * (size_t)DECW * CH);
        memcpy(k->c_sync, k->c_tmp, sizeof(float) * (size_t)CD * CH);
    }
    const float *hdw = saan_tf(w, "decoder.hdown.weight");
    const float *hdb = saan_tf(w, "decoder.hdown.bias");
    const float *how = saan_tf(w, "decoder.hout.weight");
    const float *hob = saan_tf(w, "decoder.hout.bias");
    saan_conv1d(k->hr, k->h, hdw, hdb, DECW, SAAN_DEC_HEAD, 1, CH);
    saan_gelu(k->hr, (size_t)SAAN_DEC_HEAD * CH);
    saan_conv1d(k->o1539, k->hr, how, hob, SAAN_DEC_HEAD, 1539, 1, CH);
}

/* --- 段別: iSTFT（1 フレーム = 逆実 FFT + overlap-add + 1 hop 取り出し） ----
 *
 * ⚠️ **本体と同じ関数を呼ぶこと。** ここに naive DFT を写していると、
 * 本体を FFT 化しても段別モデルだけ古いままになり、
 * 整合性（Σ段別 / 全体）が 8.9 に飛ぶ（実際にそうなった）。
 * `-DSAAN_USE_NAIVE_DFT` のときだけ naive に落とす。 */
static void mb_istft_frame(work_t *k, int t, int32_t abs_frame) {
    const int N = NFFT;
    for (int kk = 0; kk < NB; ++kk) {
        const float m = k->mag[(size_t)kk * CH + t];
        k->re[kk] = m * k->cosv[(size_t)kk * CH + t];
        k->im[kk] = m * k->sinv[(size_t)kk * CH + t];
    }
#ifdef SAAN_USE_NAIVE_DFT
    for (int n = 0; n < N; ++n) {
        double acc = (double)k->re[0];
        for (int kk = 1; kk < N / 2; ++kk) {
            const double ang = 2.0 * M_PI * (double)kk * (double)n / (double)N;
            acc += 2.0 * ((double)k->re[kk] * cos(ang) - (double)k->im[kk] * sin(ang));
        }
        acc += (double)k->re[N / 2] * cos(M_PI * (double)n);
        k->frm[n] = (float)(acc / (double)N);
    }
#else
    saan_irfft_1024(k->re, k->im, k->frm);
#endif
    const int L = k->ola_len;
    const int32_t base = abs_frame * HOP;
    for (int i = 0; i < N; ++i) {
        const int j = (int)((base + i) % L);
        k->ola[j] += k->frm[i] * k->win[i];
        k->olw[j] += k->win[i] * k->win[i];
    }
}

static void mb_istft_pop(work_t *k, int32_t out_pos) {
    const int L = k->ola_len;
    for (int i = 0; i < HOP; ++i) {
        const int j = (int)((out_pos + i) % L);
        k->pop[i] = k->olw[j] > 1e-11f ? k->ola[j] / k->olw[j] : 0.0f;
        k->ola[j] = 0.0f;
        k->olw[j] = 0.0f;
    }
}

/* --- 1 ケース -------------------------------------------------------------- */

#define OUTER 7

typedef struct {
    const char *name;
    int n_ids;
    int32_t n_frames;
    double audio_sec;
    int32_t chunks;
    stat_t total;      /* ms */
    stat_t dur, tok, ac, dec, istft;   /* ms（発話全体ぶん） */
    double per_frame_dft_us;
    double model_sum, model_sum_min;
    double recon, recon_min;
} case_t;

static void run_case(case_t *cs, const saan_weights *W, const int32_t *ids,
                     int n_ids, int reps, work_t *k) {
    double v[MAXR];
    cs->n_ids = n_ids;

    const size_t need = saan_stream_arena_needed((int32_t)n_ids);
    void *ab = malloc(need);
    if (!ab) { fprintf(stderr, "arena 確保失敗\n"); exit(1); }
    float *pcm = (float *)malloc(sizeof(float) * CH * HOP);

    /* --- (A) 全体: 公開 API のみ --- */
    for (int r = -1; r < reps; ++r) {          /* r = -1 は warmup */
        saan_arena A;
        saan_arena_init(&A, ab, need);
        saan_stream st;
        const double t0 = now_ms();
        if (saan_stream_init(&st, W, &A, ids, (int32_t)n_ids, SAAN_S_V) != SAAN_OK) {
            fprintf(stderr, "stream init 失敗\n"); exit(1);
        }
        int32_t n;
        while (saan_stream_pull(&st, pcm, &n) == SAAN_OK && n > 0) { }
        const double t1 = now_ms();
        if (r >= 0) v[r] = t1 - t0;
        cs->n_frames = st.n_frames;
        cs->chunks = st.pushed / CH;
    }
    cs->total = stat_of(v, reps);
    cs->audio_sec = (double)cs->n_frames * HOP / (double)SAAN_SR;

    /* --- (B) 段別 --- */
    /* チャンクごとの token span を実データ（d_hat）から求める。
     * `make_hf` が跨ぐトークン範囲そのもの */
    saan_arena A2;
    saan_arena_init(&A2, ab, need);
    saan_stream st2;
    saan_stream_init(&st2, W, &A2, ids, (int32_t)n_ids, SAAN_S_V);
    const int32_t nch_tok = (cs->n_frames + CH - 1) / CH;
    int *spans = (int *)malloc(sizeof(int) * (size_t)nch_tok);
    for (int32_t ci = 0; ci < nch_tok; ++ci) {
        int32_t i0 = -1, i1 = -1;
        for (int m = 0; m < CH; ++m) {
            const int32_t f = ci * CH + m;
            if (f >= cs->n_frames) break;
            int32_t acc = 0, i = 0;
            while (i < st2.n_ids && acc + st2.d_hat[i] <= f) { acc += st2.d_hat[i]; ++i; }
            if (i0 < 0 || i < i0) i0 = i;
            if (i > i1) i1 = i;
        }
        spans[ci] = (int)(i1 + 1 - i0);
    }

    /* duration: 発話ごと 1 回（stream_init 内で走る） */
    float *logd = (float *)malloc(sizeof(float) * (size_t)n_ids);
    for (int o = 0; o < OUTER; ++o) {
        saan_arena A3;
        saan_arena_init(&A3, ab, need);
        const double t0 = now_ms();
        saan_run_duration(W, &A3, ids, n_ids, logd);
        v[o] = now_ms() - t0;
    }
    cs->dur = stat_of(v, OUTER);

    /* token block: 全チャンクぶん（span は実データ） */
    for (int o = 0; o < OUTER; ++o) {
        const double t0 = now_ms();
        for (int32_t ci = 0; ci < nch_tok; ++ci) mb_token(W, k, spans[ci]);
        v[o] = now_ms() - t0;
    }
    cs->tok = stat_of(v, OUTER);

    /* acoustic frame: チャンクあたり一定。実チャンク数を掛ける */
    {
        const int inner = 200;
        for (int o = 0; o < OUTER; ++o) {
            const double t0 = now_ms();
            for (int i = 0; i < inner; ++i) mb_acoustic(W, k);
            v[o] = (now_ms() - t0) / (double)inner * (double)cs->chunks;
        }
        cs->ac = stat_of(v, OUTER);
    }

    /* decoder: チャンクあたり一定。実チャンク数を掛ける */
    {
        const int inner = 100;
        for (int o = 0; o < OUTER; ++o) {
            const double t0 = now_ms();
            for (int i = 0; i < inner; ++i) mb_decoder(W, k);
            v[o] = (now_ms() - t0) / (double)inner * (double)cs->chunks;
        }
        cs->dec = stat_of(v, OUTER);
    }

    /* iSTFT: フレームあたり一定。実フレーム数（= n_frames）を掛ける。
     * pop はフレーム数 + N/2/HOP 回だが、pop は DFT に比べて桁違いに軽い */
    {
        const int inner = 24;
        for (int o = 0; o < OUTER; ++o) {
            const double t0 = now_ms();
            for (int i = 0; i < inner; ++i) {
                mb_istft_frame(k, i % CH, i);
                mb_istft_pop(k, i * HOP);
            }
            const double per = (now_ms() - t0) / (double)inner;
            v[o] = per * (double)cs->n_frames;
            if (o == 0) cs->per_frame_dft_us = per * 1000.0;
        }
        cs->istft = stat_of(v, OUTER);
    }

    cs->model_sum = cs->dur.mean + cs->tok.mean + cs->ac.mean + cs->dec.mean + cs->istft.mean;
    cs->recon = cs->model_sum / cs->total.mean;
    cs->model_sum_min = cs->dur.min + cs->tok.min + cs->ac.min + cs->dec.min + cs->istft.min;
    cs->recon_min = cs->model_sum_min / cs->total.min;

    free(spans); free(logd); free(pcm); free(ab);
}

/* --- 出力 ------------------------------------------------------------------ */

static double xrt(double ms, double sec) { return ms / 1e3 / sec; }

static void print_case(const case_t *c) {
    printf("\n== %s : %d ids / %d frames / 音声 %.3f s ==\n",
           c->name, c->n_ids, c->n_frames, c->audio_sec);
    printf("  全体（公開 API, n=%d）\n", c->total.n);
    printf("      mean %9.2f ms  sd %7.2f   → %.3f × RT\n",
           c->total.mean, c->total.sd, xrt(c->total.mean, c->audio_sec));
    printf("      med  %9.2f ms  min %8.2f  max %8.2f → min で %.3f × RT\n",
           c->total.median, c->total.min, c->total.max,
           xrt(c->total.min, c->audio_sec));
    if (c->total.mean > c->total.min * 1.05)
        printf("      ⚠️ mean が min の 1.05 倍を超えている = 他プロセスと"
               "CPU を取り合っている。min を見ること\n");
    printf("  --- 段別（マイクロベンチ・モデル, n=%d。ms は mean / min） ---\n", OUTER);
    const char *nm[5] = { "duration      ", "token block   ", "acoustic frame",
                          "decoder       ", "iSTFT         " };
    const stat_t *ss[5] = { &c->dur, &c->tok, &c->ac, &c->dec, &c->istft };
    for (int i = 0; i < 5; ++i)
        printf("    %s %9.2f / %8.2f ms  sd %6.2f  %6.2f %%  %.4f × RT(min)\n",
               nm[i], ss[i]->mean, ss[i]->min, ss[i]->sd,
               100.0 * ss[i]->min / c->model_sum_min, xrt(ss[i]->min, c->audio_sec));
    printf("    %s %9.2f / %8.2f ms                    %.3f × RT(min)\n",
           "Σ 段別       ", c->model_sum, c->model_sum_min,
           xrt(c->model_sum_min, c->audio_sec));
    printf("    整合性 Σ段別 / 全体 : mean %.3f / min %.3f"
           "   （1.0 に近いほど内訳が信頼できる）\n", c->recon, c->recon_min);
    printf("    1 フレームあたり iSTFT %.3f ms  / chunk 数 %d\n",
           c->istft.min / (double)c->n_frames, c->chunks);
}

static void json_stat(FILE *f, const char *key, const stat_t *s, double sec) {
    fprintf(f, "      \"%s\": {\"ms_mean\": %.4f, \"ms_sd\": %.4f, \"ms_median\": %.4f, "
               "\"ms_min\": %.4f, \"ms_max\": %.4f, \"n\": %d, "
               "\"xrt_mean\": %.5f, \"xrt_min\": %.5f},\n",
            key, s->mean, s->sd, s->median, s->min, s->max, s->n,
            xrt(s->mean, sec), xrt(s->min, sec));
}

int main(int argc, char **argv) {
    const char *wpath = "student.bin", *gpath = "golden.bin";
    const char *jpath = NULL, *label = "unknown";
    int reps = 10;
    /* 差分クロスチェック: DFT 内側ループを無効化したビルドの全体 ms（短/中/長）。
     * **測って渡す値**であって、ここで推定するものではない（--nodft-ms で与える） */
    double nodft[3] = { -1.0, -1.0, -1.0 };
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--weights") && i + 1 < argc) wpath = argv[++i];
        else if (!strcmp(argv[i], "--golden") && i + 1 < argc) gpath = argv[++i];
        else if (!strcmp(argv[i], "--json") && i + 1 < argc) jpath = argv[++i];
        else if (!strcmp(argv[i], "--label") && i + 1 < argc) label = argv[++i];
        else if (!strcmp(argv[i], "--reps") && i + 1 < argc) reps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--nodft-ms") && i + 1 < argc) {
            if (sscanf(argv[++i], "%lf,%lf,%lf", &nodft[0], &nodft[1], &nodft[2]) != 3) {
                fprintf(stderr, "--nodft-ms は short,medium,long の 3 値\n"); return 2;
            }
        }
        else { fprintf(stderr, "unknown arg: %s\n", argv[i]); return 2; }
    }
    if (reps < 10) { fprintf(stderr, "reps は 10 以上（1 回計測は数値にしない）\n"); return 2; }
    if (reps > MAXR) reps = MAXR;

    size_t wsz, gsz;
    void *wbuf = slurp(wpath, &wsz), *gbuf = slurp(gpath, &gsz);
    saan_weights W, G;
    if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) { fprintf(stderr, "重みが読めない\n"); return 1; }
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) { fprintf(stderr, "golden が読めない\n"); return 1; }

    uint64_t nb;
    const float *ids_f = (const float *)saan_tensor(&G, "in.ids", NULL, NULL, &nb);
    if (!ids_f) { fprintf(stderr, "golden に in.ids が無い\n"); return 1; }
    const int base_n = (int)(nb / sizeof(float));
    int32_t *base = (int32_t *)malloc(sizeof(int32_t) * (size_t)base_n);
    for (int i = 0; i < base_n; ++i) base[i] = (int32_t)ids_f[i];

    work_t k;
    work_init(&k);

    /* 短 = golden のテスト文そのもの / 中 / 長 = D-017 の実用最大（350 ids ≒ 8 秒） */
    const int lens[3] = { base_n, 150, 350 };
    const char *names[3] = { "short", "medium", "long" };
    case_t cases[3];

    printf("saanoTTS-jp D-3b レイテンシ測定\n");
    printf("  host label : %s\n", label);
    printf("  compiler   : %s\n", __VERSION__);
    printf("  weights    : %s (%zu B, %u tensors)\n", wpath, wsz, W.n_tensors);
    printf("  reps       : %d（全体）/ %d（段別）\n", reps, OUTER);

    for (int ci = 0; ci < 3; ++ci) {
        const int n = lens[ci];
        int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n);
        for (int i = 0; i < n; ++i) ids[i] = base[i % base_n];
        memset(&cases[ci], 0, sizeof(case_t));
        cases[ci].name = names[ci];
        run_case(&cases[ci], &W, ids, n, reps, &k);
        print_case(&cases[ci]);
        free(ids);
    }

    if (nodft[0] > 0.0) {
        printf("\n== 差分クロスチェック（DFT 内側ループ無効化ビルドとの差）==\n");
        printf("  （すべて min 同士の比較）\n");
        printf("  %-7s %10s %10s %10s %9s %9s\n",
               "case", "全体 ms", "DFT無 ms", "差 ms", "DFT 比率", "micro/差");
        for (int ci = 0; ci < 3; ++ci) {
            if (nodft[ci] <= 0.0) continue;
            const double d = cases[ci].total.min - nodft[ci];
            printf("  %-7s %10.2f %10.2f %10.2f %8.1f %% %9.3f\n",
                   cases[ci].name, cases[ci].total.min, nodft[ci], d,
                   100.0 * d / cases[ci].total.min, cases[ci].istft.min / d);
        }
        printf("  → 2 つの独立な方法が一致すれば内訳は信頼できる\n");
    }

    printf("\n判定: 論文の ESP32-S3 0.22 × RT はここでは**評価しない**"
           "（外挿は別タスク）。\n");

    if (jpath) {
        FILE *f = fopen(jpath, "w");
        if (!f) { fprintf(stderr, "JSON が書けない: %s\n", jpath); return 1; }
        fprintf(f, "{\n");
        fprintf(f, "  \"task\": \"D-3b\",\n");
        fprintf(f, "  \"what\": \"csrc のストリーミング推論のレイテンシ（ホスト実測）。段別内訳つき\",\n");
        fprintf(f, "  \"host_label\": \"%s\",\n", label);
        fprintf(f, "  \"compiler\": \"%s\",\n", __VERSION__);
        fprintf(f, "  \"cflags\": \"-std=c99 -O2 -Wall -Wextra\",\n");
        fprintf(f, "  \"clock\": \"clock_gettime(CLOCK_MONOTONIC)\",\n");
        fprintf(f, "  \"weights\": {\"path\": \"%s\", \"bytes\": %zu, \"n_tensors\": %u, \"dtype\": \"fp32\"},\n",
                wpath, wsz, W.n_tensors);
        fprintf(f, "  \"sample_rate\": %d, \"hop\": %d, \"n_fft\": %d, \"chunk_frames\": %d,\n",
                SAAN_SR, HOP, NFFT, CH);
        fprintf(f, "  \"reps_total\": %d, \"reps_stage\": %d, \"warmup\": 1,\n", reps, OUTER);
        fprintf(f, "  \"cases\": [\n");
        for (int ci = 0; ci < 3; ++ci) {
            const case_t *c = &cases[ci];
            fprintf(f, "    {\n");
            fprintf(f, "      \"name\": \"%s\",\n", c->name);
            fprintf(f, "      \"n_ids\": %d,\n", c->n_ids);
            fprintf(f, "      \"n_frames\": %d,\n", c->n_frames);
            fprintf(f, "      \"chunks\": %d,\n", c->chunks);
            fprintf(f, "      \"audio_sec\": %.4f,\n", c->audio_sec);
            fprintf(f, "      \"total_measured\": {\"ms_mean\": %.4f, \"ms_sd\": %.4f, "
                       "\"ms_median\": %.4f, \"ms_min\": %.4f, \"ms_max\": %.4f, \"n\": %d, "
                       "\"xrt_mean\": %.5f, \"xrt_min\": %.5f},\n",
                    c->total.mean, c->total.sd, c->total.median, c->total.min,
                    c->total.max, c->total.n, xrt(c->total.mean, c->audio_sec),
                    xrt(c->total.min, c->audio_sec));
            fprintf(f, "      \"stages_model\": {\n");
            json_stat(f, "duration", &c->dur, c->audio_sec);
            json_stat(f, "token_block", &c->tok, c->audio_sec);
            json_stat(f, "acoustic_frame", &c->ac, c->audio_sec);
            json_stat(f, "decoder", &c->dec, c->audio_sec);
            json_stat(f, "istft", &c->istft, c->audio_sec);
            fprintf(f, "      \"_sum\": {\"ms_mean\": %.4f, \"ms_min\": %.4f, "
                       "\"xrt_mean\": %.5f, \"xrt_min\": %.5f}\n",
                    c->model_sum, c->model_sum_min,
                    xrt(c->model_sum, c->audio_sec), xrt(c->model_sum_min, c->audio_sec));
            fprintf(f, "      },\n");
            fprintf(f, "      \"stage_share_pct\": {\"duration\": %.2f, \"token_block\": %.2f, "
                       "\"acoustic_frame\": %.2f, \"decoder\": %.2f, \"istft_naive_dft\": %.2f},\n",
                    100.0 * c->dur.min / c->model_sum_min, 100.0 * c->tok.min / c->model_sum_min,
                    100.0 * c->ac.min / c->model_sum_min, 100.0 * c->dec.min / c->model_sum_min,
                    100.0 * c->istft.min / c->model_sum_min);
            fprintf(f, "      \"reconciliation_sum_over_measured\": {\"mean\": %.4f, "
                       "\"min\": %.4f},\n", c->recon, c->recon_min);
            fprintf(f, "      \"istft_ms_per_frame\": %.4f", c->istft.min / (double)c->n_frames);
            if (nodft[ci] > 0.0) {
                const double d = c->total.min - nodft[ci];
                fprintf(f, ",\n      \"cross_check_dft_disabled\": {\n");
                fprintf(f, "        \"method\": \"saanotts_stream.c のコピー（scratchpad）で "
                           "istft_push の内側 DFT ループだけを無効化し、同じ全体計測を回した差分。値はどちらも ms_min 同士。"
                           "csrc の既存ファイルは触っていない\",\n");
                fprintf(f, "        \"total_ms_without_dft\": %.4f,\n", nodft[ci]);
                fprintf(f, "        \"xrt_without_dft\": %.5f,\n", xrt(nodft[ci], c->audio_sec));
                fprintf(f, "        \"dft_ms_by_difference\": %.4f,\n", d);
                fprintf(f, "        \"dft_share_of_total\": %.4f,\n", d / c->total.min);
                fprintf(f, "        \"microbench_over_difference\": %.4f\n",
                        c->istft.min / d);
                fprintf(f, "      }\n");
            } else {
                fprintf(f, "\n");
            }
            fprintf(f, "    }%s\n", ci < 2 ? "," : "");
        }
        fprintf(f, "  ],\n");
        fprintf(f, "  \"method\": {\n");
        fprintf(f, "    \"total\": \"公開 API のみ（saan_stream_init + saan_stream_pull を最後まで）。saanotts_stream.c は未編集。warmup 1 回のあと reps_total 回。これが唯一の直接計測\",\n");
        fprintf(f, "    \"stages\": \"saanotts_stream.c を編集できないので、各段のカーネル列を同じ shape・同じ重みで bench.c 内に再現して単体計測し、実際の呼び出し回数（chunks / n_frames）を掛けたモデル\",\n");
        fprintf(f, "    \"validity_check\": \"reconciliation_sum_over_measured = Σ段別 / 全体実測。mean と min の両方を出す。1.0 から離れるほど内訳の信頼度は下がる\"\n");
        fprintf(f, "  },\n");
        fprintf(f, "  \"limits\": [\n");
        fprintf(f, "    \"段別はマイクロベンチの再現であり、saanotts_stream.c 内部を直接計測したものではない（既存ファイルを編集しない制約のため）\",\n");
        fprintf(f, "    \"段別マイクロベンチの入力データは実際の中間出力ではなく合成値。saan_conv1d は重みが 0 の項をスキップするが重みは本物なので分岐挙動は同じ、一方 denormal の発生率は実データと違いうる\",\n");
        fprintf(f, "    \"acoustic / decoder は 1 チャンクあたり一定として chunks 倍、iSTFT は 1 フレームあたり一定として n_frames 倍にスケールした（どちらも入力値に依存しない固定ループ）\",\n");
        fprintf(f, "    \"zero_outside / obuf の詰め替え / pull のコピーなど、段に割り当てていない小さなコストがある。その分 Σ段別 は全体をわずかに下回る\",\n");
        fprintf(f, "    \"ESP32 への外挿はしていない。ここの数値はホスト CPU のもの\",\n");
        fprintf(f, "    \"重みは fp32。int8 カーネルは未計測\",\n");
        fprintf(f, "    \"同一マシンで他プロセスが走ると mean と sd が上振れする（実測で sd が 1.3 ms から 398 ms まで悪化した実行があった）。ms_min と xrt_min が汚染に強い推定値\"\n");
        fprintf(f, "  ]\n");
        fprintf(f, "}\n");
        fclose(f);
        printf("JSON: %s\n", jpath);
    }
    free(base); free(wbuf); free(gbuf);
    return 0;
}
