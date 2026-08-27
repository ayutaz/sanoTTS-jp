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

/* 一括版とストリーミング版で共有する前段。**2 回書かない**（bit 一致のため） */
saan_status saan_run_duration(const saan_weights *w, saan_arena *a,
                              const int32_t *ids, int T, float *log_d);
saan_status saan_run_acoustic_tokens(const saan_weights *w, saan_arena *a,
                                     const int32_t *ids, int L, float *ht);

#endif
