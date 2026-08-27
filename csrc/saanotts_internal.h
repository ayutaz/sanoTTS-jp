/* 一括版とストリーミング版で共有する内部カーネル。
 *
 * ⚠️ **公開 API ではない。** `saanotts.h` を使うこと。
 * ここを共有するのは「同じカーネルを 2 回書かない」ため —
 * 2 つ書くと bit 一致（D-029 の G2）が構造的に守れなくなる。
 */
#ifndef SAANOTTS_INTERNAL_H
#define SAANOTTS_INTERNAL_H

#include "saanotts.h"

#define SAAN_ALIGN16(x) (((x) + 15u) & ~(size_t)15u)

void *saan_alloc(saan_arena *a, size_t n);

/* y[o,t] = b[o] + Σ_i Σ_k W[o,i,k] · x[i, t+k-pad]。**両端ゼロパディング** */
void saan_conv1d(float *y, const float *x, const float *W, const float *b,
                 int cin, int cout, int ksz, int T);

/* 左右のコンテキストを外から与える版。`left`/`right` は [cin * pad]
 * （NULL ならゼロ = 発話の端）。**これが bit 一致の要**:
 * 一括版の「両端ゼロパディング」を、チャンク境界では実データで置き換える。
 * 内部で x を [cin][pad + T + pad] に展開してから既存カーネルを呼ぶので、
 * **積和の順序が一括版と同一**になる（float の加算は非結合なので順序が命）。 */
void saan_conv1d_ctx(float *y, const float *x, const float *left,
                     const float *right, const float *W, const float *b,
                     int cin, int cout, int ksz, int T, float *scratch);

void saan_dwconv1d(float *y, const float *x, const float *W, int ch, int ksz, int T);
void saan_dwconv1d_ctx(float *y, const float *x, const float *left,
                       const float *right, const float *W,
                       int ch, int ksz, int T, float *scratch);

void saan_layernorm_c(float *x, const float *g, const float *b, int C, int T);
void saan_relu(float *x, size_t n);
void saan_gelu(float *x, size_t n);

const float *saan_tf(const saan_weights *w, const char *fmt, ...);

/* --- fp32 / int8 のディスパッチ（D-3c'-2） --------------------------------
 *
 * **どちらの経路を通るかは「読み込んだブロブの dtype」だけで決まる**（実行時）。
 * `student.bin` を渡せば fp32、`student_i8.bin` を渡せば int8。
 * 同じバイナリで両方が動くので、fp32 のゴールデンテストは今までどおり通る。
 *
 * ⚠️ **fp32 が無ければ int8、という「フォールバック」にしない。**
 * 名前を打ち間違えたときに黙って NULL になり、その層だけ消えた音声が出る。
 * `saan_w()` はどちらも引けなければ **両方 NULL** を返し、
 * 呼び出し側は `SAAN_W_OK()` で弾いて `SAAN_ERR_MISSING` にすること。
 */
typedef struct {
    const float  *f32;    /* fp32 ブロブのとき。int8 なら NULL */
    const int8_t *q;      /* int8 ブロブのとき。fp32 なら NULL */
    const float  *scale;  /* int8 の per-output-channel scale [cout] */
} saan_wref;

#define SAAN_W_OK(r) ((r).f32 != NULL || (r).q != NULL)

saan_wref saan_w(const saan_weights *w, const char *fmt, ...);

/* activation の量子化（W8A8）を使うか。**既定は 0 = W8A32**
 * （重みだけ int8 / activation は fp32）。理由は「W8A8 が危険だから」ではなく
 * 「flash が 1 バイトも減らず、ホストの速度利得も 0.86 倍しかない」から。
 * 実機で足りなければ `-DSAAN_INT8_ACT=1` で切り替える。
 * ⚠️ W8A8 は conv ごとに `[T][cin]` の作業領域を arena から取る。
 * `saan_arena_needed` / `saan_stream_arena_needed` はその分を数えている。 */
#ifndef SAAN_INT8_ACT
#define SAAN_INT8_ACT 0
#endif

/* W8A8 の作業領域（16 B 境界込み）。W8A32 なら 0 を返す */
size_t saan_act_scratch_needed(int cin, int T);

/* `saan_conv1d` / `saan_dwconv1d` と同じ意味論。重みが fp32 なら
 * **まったく同じ関数を呼ぶ**ので bit 一致する。`a` は W8A8 の作業領域用
 * （W8A32 では触らない。NULL 可） */
saan_status saan_conv1d_w(float *y, const float *x, saan_wref W, const float *b,
                          int cin, int cout, int ksz, int T, saan_arena *a);
saan_status saan_dwconv1d_w(float *y, const float *x, saan_wref W,
                            int ch, int ksz, int T, saan_arena *a);

/* ⚠️ **失敗すると呼び出し元から return する。** W8A8 の arena 不足を
 * 黙って握りつぶさないため。名前の TRY がその意味 */
#define SAAN_TRY(expr) do { const saan_status s_ = (expr); \
                            if (s_ != SAAN_OK) return s_; } while (0)

/* 一括版とストリーミング版で共有する前段。**2 回書かない**（bit 一致のため） */
saan_status saan_run_duration(const saan_weights *w, saan_arena *a,
                              const int32_t *ids, int T, float *log_d);
saan_status saan_run_acoustic_tokens(const saan_weights *w, saan_arena *a,
                                     const int32_t *ids, int L, float *ht);

#endif
