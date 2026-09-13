/* 音の出口の抽象インターフェース。
 *
 * ⚠️ **`esp32/main/saan_audio.h` と 1:1 に対応させてある。** あちらは実機で
 *    アンダーラン 0 を出している実績のある設計で、勝手に簡略化すると必ず途切れる。
 *
 * 同梱の実装:
 *   SanoTTSSpeakerM5   … M5Unified の M5.Speaker（CoreS3 / Core2 / AtomS3 など）
 *   SanoTTSSpeakerI2S  … 汎用の I2S DAC（GPIO を指定する）
 *
 * 自分で書くなら 6 つのメソッドを埋める。`SanoTTS::say()` が次の順で呼ぶ:
 *
 *   begin(22050)            起動時に 1 回
 *   beginUtterance(n)       発話ごと。n = プリロールに使うサンプル数
 *   prerollPush(pcm, n) ×k  鳴らす前に貯める
 *   start()                 ここで鳴り始める
 *   write(pcm, n) ×k        定常。**ここが間に合わないとアンダーラン**
 *   stop()                  発話の終わり
 *
 * ⚠️ **プリロールを省かないこと。** 初回の `saan_stream_pull` は定常の約 6 倍かかる
 *    （受容野 38 フレームの warmup で内部の step が複数回走る）。省くと必ず途切れる。
 */
#ifndef SANOTTS_SPEAKER_H
#define SANOTTS_SPEAKER_H

#include <stddef.h>
#include <stdint.h>

class SanoTTSSpeaker {
public:
    virtual ~SanoTTSSpeaker() {}

    /* 起動時に 1 回。⚠️ **22,050 Hz 以外を渡さない**（コアの出力レート）。 */
    virtual bool begin(uint32_t sampleRate) = 0;

    /* 鳴らし始める前に貯めるサンプル数。既定 8,192 は ESP-IDF 版の
     * `SAAN_AUDIO_PREROLL_SAMPLES` と同じ値（実機でアンダーラン 0）。 */
    virtual size_t prerollSamples() const { return 8192; }

    /* 発話の頭。`nSamples` ぶんのプリロール用バッファを用意する。 */
    virtual bool beginUtterance(size_t nSamples) = 0;

    /* プリロールに積む。`start()` の前にだけ呼ばれる。 */
    virtual bool prerollPush(const int16_t* pcm, size_t nSamples) = 0;

    /* 鳴らし始める。 */
    virtual bool start() = 0;

    /* 定常。⚠️ **ブロックしてよい**（その間に次のチャンクは作られない）。 */
    virtual bool write(const int16_t* pcm, size_t nSamples) = 0;

    /* 発話の終わり。鳴り終わるまで待つ。 */
    virtual void stop() = 0;
};

#endif /* SANOTTS_SPEAKER_H */
