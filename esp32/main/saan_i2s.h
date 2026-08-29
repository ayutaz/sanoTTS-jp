/* I2S 出力（22.05 kHz / 16 bit / mono）。
 *
 * ⚠️ **ESP32-S3 に APLL が無い**（soc_caps.h に SOC_I2S_SUPPORTS_APLL が無い）。
 *    22,050 Hz は PLL_160M の分数分周で近似する。**実サンプルレートの誤差は
 *    実機で測るまで分からない。** ずれるとピッチがずれる（SCOREQ/UTMOS には
 *    効かないが聴けば分かる）。「22.05 kHz で鳴った」と書く前に実測すること。
 *
 * 使う順番:
 *   saan_i2s_setup()                     チャンネルを作る（まだ鳴らさない）
 *   saan_i2s_preroll_push() を数回        先に計算だけ済ませる
 *   saan_i2s_start()                     enable してプリロールを吐く
 *   saan_i2s_write_f32() を繰り返す
 *   saan_i2s_stop()
 */
#ifndef SAAN_I2S_H
#define SAAN_I2S_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* プリロールに貯めるサンプル数。既定 8,192 = 4 チャンク = 371 ms・16 KB。
 *
 * なぜ要るか: **最初の saan_stream_pull だけ定常の約 6 倍かかる**
 * （ホスト実測 12.2 ms vs 2.04 ms。受容野 36 + iSTFT 2 = 38 フレームの
 * warmup で内部の step_chunk が複数回走るため）。i2s_channel_enable の直後から
 * 合成を始めると、その 1 回ぶんが確実にアンダーランになる。 */
#ifndef SAAN_I2S_PREROLL_SAMPLES
#define SAAN_I2S_PREROLL_SAMPLES 8192
#endif

/* I2S ペリフェラルへの書き込みだけを外す（**変換は通す**）。
 * ⚠️ **QEMU 専用の逃げ道。既定 0。** 1 にすると音は一切出ない。
 * QEMU の esp32s3 は I2S DMA を捌かず `i2s_channel_write` が返らないため、
 * これが無いと合成ループを 1 回も回せない（実測）。 */
#ifndef SAAN_SKIP_I2S
#define SAAN_SKIP_I2S 0
#endif

bool saan_i2s_setup(uint32_t sample_rate);

/* まだ鳴らさずに変換して貯める。プリロールが一杯なら false */
bool saan_i2s_preroll_push(const float *pcm, size_t n_samples);

/* enable してプリロールを吐き出す */
bool saan_i2s_start(void);

/* float[-1,1] → int16 に変換して書く（ブロッキング）。
 * ⚠️ `n_samples` は**サンプル数**。saan_stream_pull が返すフレーム数ではない
 *    （サンプル数 = フレーム数 × SAAN_HOP）。 */
bool saan_i2s_write_f32(const float *pcm, size_t n_samples);

void saan_i2s_stop(void);

/* float → int16。**正規化しない**（発話ごとに音量が変わると決定性が壊れる）。
 * golden.bin の out.pcm は最大 0.26 でヘッドルームが大きいが、それは
 * この発話の性質であって全発話の保証ではない。クリップ数は数えて出す。 */
int16_t saan_f32_to_i16(float x);

uint32_t saan_i2s_clip_count(void);

/* --- 出力 PCM のチェックサム（移植の検証用）--------------------------------
 *
 * `saan_f32_to_i16()` を通った **すべての** int16 サンプルの FNV-1a。
 * プリロールも定常ループも同じ関数を通るので、**I2S に出たはずの列そのもの**。
 *
 * ⚠️ **なぜ要るか**: 実機や QEMU で「音が鳴った」は移植が正しい証拠にならない。
 * ホスト（`esp32/host_stub/`、C 一括版と bit 一致を確認済み）と同じ値が出れば、
 * **ターゲット上の全経路が bit 単位で一致している**と言える。
 * ⚠️ 逆に **1 サンプルでも違えば値は必ず変わる**（陰性対照は host_stub 側にある）。
 */
uint64_t saan_i2s_pcm_checksum(void);
uint32_t saan_i2s_pcm_samples(void);

/* ⚠️ **checksum と必ずセットで読む。** アーキテクチャが違えば float の丸めが
 * 変わるので checksum は一致しない。そのとき「1 LSB の丸め差」なのか
 * 「経路が壊れている」のかは、**大きさ**でしか区別できない。 */
int32_t  saan_i2s_pcm_absmax(void);
uint64_t saan_i2s_pcm_sqsum(void);

#endif /* SAAN_I2S_H */
