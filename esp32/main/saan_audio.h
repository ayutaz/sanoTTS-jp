/* 音声出力の抽象 API（22.05 kHz / 16 bit / mono）。
 *
 * 実装は 2 つ。**main.c はどちらが繋がっているか知らない。**
 *   saan_i2s.c                               DevKit: ESP-IDF の driver/i2s_std を直叩き
 *   boards/m5unified/main/saan_audio_m5.cpp  M5Stack: M5Unified の M5.Speaker
 *
 * float → int16 の変換と checksum は **saan_pcm.c が唯一の実装**で、どの実装も
 * `saan_f32_to_i16()` を呼ぶ（2 か所に書かない）。
 *
 * ⚠️ **ESP32-S3 に APLL が無い**（soc_caps.h に SOC_I2S_SUPPORTS_APLL が無い）。
 *    22,050 Hz は PLL の分数分周で近似する。**実サンプルレートの誤差は実機で測るまで
 *    分からない。** ずれるとピッチがずれる。「22.05 kHz で鳴った」と書く前に実測すること。
 *
 * 使う順番（1 発話）:
 *   saan_audio_setup()                  1 回だけ。ペリフェラルを作る（まだ鳴らさない）
 *   saan_audio_begin_utterance(n)       貯める場所を n sample ぶん確保
 *   saan_audio_preroll_push() を数回     先に計算だけ済ませる（鳴らさない）
 *   saan_audio_start()                  鳴らし始め、貯めたぶんを吐く
 *   saan_audio_write_f32() を繰り返す    計算しながら鳴らす（ストリーミング）
 *   saan_audio_stop()                   鳴らし終わるまで待ち、begin_utterance のバッファを返す
 */
#ifndef SAAN_AUDIO_H
#define SAAN_AUDIO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ストリーミング時にプリロールするサンプル数。既定 8,192 = 4 チャンク = 371 ms・16 KB。
 *
 * なぜ要るか: **最初の saan_stream_pull だけ定常の約 5〜6 倍かかる**
 * （ホスト実測 12.2 ms vs 2.04 ms / CoreS3 の報告値 766 ms vs 144 ms。受容野 36 + iSTFT 2 =
 * 38 フレームの warmup で内部の step_chunk が複数回走るため）。鳴らし始めた直後から
 * 合成を始めると、その 1 回ぶんが確実にアンダーランになる。 */
#ifndef SAAN_AUDIO_PREROLL_SAMPLES
#define SAAN_AUDIO_PREROLL_SAMPLES 8192
#endif

bool saan_audio_setup(uint32_t sample_rate);

/* 発話の開始。`n_samples` ぶんの int16 を貯める場所を確保する。
 *   ストリーミング   … SAAN_AUDIO_PREROLL_SAMPLES を渡す
 *   貯めてから鳴らす … 発話の総サンプル数（n_frames × SAAN_HOP）を渡す
 * 取れなければ false（**黙って切り詰めない**）。バッファは saan_audio_stop() が返す。
 * DevKit 実装: SAAN_AUDIO_PREROLL_SAMPLES 以下なら静的バッファ、超えるときだけヒープ
 * （PSRAM 優先 → 内部 DRAM。どちらから取れたかをログに出す）。 */
bool saan_audio_begin_utterance(size_t n_samples);

/* まだ鳴らさずに変換して貯める。begin_utterance の量を超えたら false */
bool saan_audio_preroll_push(const float *pcm, size_t n_samples);

/* 鳴らし始めて、貯めたぶんを送出する */
bool saan_audio_start(void);

/* float[-1,1] → int16 に変換して送る（キューが満杯なら空くまでブロック）。
 * ⚠️ `n_samples` は**サンプル数**。saan_stream_pull が返すフレーム数ではない
 *    （サンプル数 = フレーム数 × SAAN_HOP）。 */
bool saan_audio_write_f32(const float *pcm, size_t n_samples);

/* 鳴らし終わるまで待ち、begin_utterance のバッファを返す */
void saan_audio_stop(void);

#ifdef __cplusplus
}
#endif
#endif /* SAAN_AUDIO_H */
