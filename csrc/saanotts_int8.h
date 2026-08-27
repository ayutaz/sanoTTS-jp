/* sanoTTS-jp int8 カーネル（C99 / 依存は libm のみ）
 *
 * 方式（論文 §II-C / M-39 のシミュレーションと同一）:
 *   - 重み  : **symmetric int8 / per-output-channel**。scale[o] = max|W[o,:,:]| / 127
 *   - 埋め込み・LayerNorm・1-D パラメータ（bias / LayerScale）は **fp32 のまま**
 *   - activation は **2 通り**用意した。どちらを使うかは呼び出し側が決める:
 *
 *       W8A32  saan_conv1d_i8   / saan_dwconv1d_i8    重みだけ int8、activation は fp32
 *       W8A8   saan_conv1d_i8a  / saan_dwconv1d_i8a   activation も per-frame int8、
 *                                                     積和は **int32** で溜める
 *
 *   ⚠️ **既定は W8A32 を推奨する**（`reports/d3c_int8.json` の理由節）。
 *      W8A8 は flash を減らさない（重みのビット幅は同じ）。減るのは演算コストだけで、
 *      その代わり SNR を大きく落とす。ESP32-S3 に載せるときに初めて効く選択なので、
 *      まず W8A32 で fp32 との差を潰し、速度が足りなければ W8A8 に切り替える。
 *
 * ⚠️ **量子化 activation のレイアウトは転置 `[T][C]`**（重み側の `[cout][cin][k]` と
 *    内積が連続になるようにするため）。fp32 の activation は `[C][T]` のままなので
 *    **取り違えると黙って別物になる**。
 */
#ifndef SAANOTTS_INT8_H
#define SAANOTTS_INT8_H

#include <stddef.h>
#include <stdint.h>

#include "saanotts.h"

/* --- 量子化 -------------------------------------------------------------- */

/* symmetric int8 / per-output-channel。`W` は [cout][inner] 行優先
 * （conv なら inner = cin*ksz）。**丸めは rintf = half-to-even**
 * （PyTorch の torch.round と同じ。roundf は half-away-from-zero で食い違う） */
void saan_quantize_w_i8(int8_t *q, float *scale, const float *W,
                        int cout, int inner);

/* activation を **per-frame**（時刻 t ごとに全チャネル共通の scale）で int8 に。
 * 入力 `x` は [C][T]、出力 `q` は **[T][C]（転置）**、`sx` は [T]。
 * 全チャネルが 0 のフレームは sx[t] = 0 / q = 0 になる。 */
void saan_quantize_act_i8(int8_t *q, float *sx, const float *x, int C, int T);

/* W8A8 の作業領域バイト数（q [T][C] + sx [T]） */
size_t saan_act_scratch_bytes(int C, int T);

/* --- W8A32（重みだけ int8、activation は fp32） -------------------------- */

/* `saan_conv1d` と同じ意味論（両端ゼロパディング、y[o,t] = b[o] + Σ W·x）。
 * `W` は [cout][cin][ksz] の int8、`scale` は [cout] の fp32、`b` は fp32 か NULL。
 * 積和は int8 を float に上げて溜め、**最後に一度だけ scale[o] を掛ける**
 * （per-output-channel なので出力チャネル内では定数）。 */
void saan_conv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                    const float *b, int cin, int cout, int ksz, int T);

/* depthwise。`saan_dwconv1d` と同じく **bias 無し**。W は [ch][1][ksz] */
void saan_dwconv1d_i8(float *y, const float *x, const int8_t *W, const float *scale,
                      int ch, int ksz, int T);

/* --- W8A8（activation も int8、int32 で積和） ---------------------------- */

/* `qx` は [T][cin] の int8、`sx` は [T] の fp32。呼び出し側が確保する
 * （`saan_act_scratch_bytes(cin, T)`）。内部で `saan_quantize_act_i8` を呼ぶ。
 * 積和は **タップ k ごとに int32** で溜め（同一フレームなので scale が共通）、
 * フレームをまたぐ合成だけ fp32 で行う。cin ≤ 304 なので
 * 304 · 127 · 127 = 4.9e6 で int32 は溢れない。 */
void saan_conv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                     const float *b, int cin, int cout, int ksz, int T,
                     int8_t *qx, float *sx);

void saan_dwconv1d_i8a(float *y, const float *x, const int8_t *W, const float *scale,
                       int ch, int ksz, int T, int8_t *qx, float *sx);

/* --- ブロブから int8 テンソルを引く -------------------------------------- */

/* `fmt` で名前を組み立てて int8 テンソル（dtype 1）と、
 * `<name>.scale`（dtype 2）を同時に引く。どちらか無ければ NULL を返す。 */
const int8_t *saan_ti8(const saan_weights *w, const float **scale,
                       const char *fmt, ...);

#endif /* SAANOTTS_INT8_H */
