/* `SanoTTSSpeakerBuffered` の M5.Speaker 版（CoreS3 / Core2 / AtomS3 …）。
 *
 *   #include <M5Unified.h>
 *   #include <SanoTTS.h>
 *   #include <SanoTTSSpeakerM5Buffered.h>
 *
 *   SanoTTS tts;  SanoTTSSpeakerM5Buffered spk;
 *   void setup() {
 *     M5.begin(); tts.setSpeaker(&spk); tts.begin();
 *     tts.say("今日は良い天気ですね。");   // 合成し終わったら、鳴り終わる前に戻る
 *     tts.say("明日も晴れるでしょう。");   // ↑ を鳴らしている間に合成する
 *     spk.wait();                          // 鳴り終わりを待つ
 *   }
 *
 * ⚠️ **M5.Speaker のレートは begin() で 1 回だけ 22,050 Hz にする。** 発話ごとには触らない
 *    （発話の直前に設定し直すと雑音が出たという報告がある。README「出力のサンプリングレート」）。
 * ⚠️ **M5 の実機では鳴らしていない。** 積み方の不変条件は QEMU で偽の出口を使って
 *    確かめてある（`scripts/arduino_qemu_tests/buffered.cpp`）が、M5.Speaker の上で
 *    途切れないか・雑音が無いかは見ていない。
 * ⚠️ **M5Unified が入っていなければ、このヘッダは中身ごと消える。**
 */
#ifndef SANOTTS_SPEAKER_M5_BUFFERED_H
#define SANOTTS_SPEAKER_M5_BUFFERED_H

#if defined(__has_include)
#  if __has_include(<M5Unified.h>)
#    define SANOTTS_HAVE_M5UNIFIED_BUFFERED 1
#  endif
#endif

#ifdef SANOTTS_HAVE_M5UNIFIED_BUFFERED

#include <M5Unified.h>
#include "SanoTTSSpeakerBuffered.h"

class SanoTTSSpeakerM5Buffered : public SanoTTSSpeakerBuffered {
public:
    /* `volume` は 0-255。`channel` は M5.Speaker の仮想チャネル（他の音と分けたいとき）。 */
    explicit SanoTTSSpeakerM5Buffered(uint8_t volume = 128, uint8_t channel = 0)
        : m_volume(volume), m_channel(channel) {}

protected:
    bool   beginOutput(uint32_t sampleRate) override;
    bool   enqueue(const int16_t* pcm, size_t nSamples) override;
    size_t queued() const override;

private:
    uint8_t m_volume;
    uint8_t m_channel;
};

#endif /* SANOTTS_HAVE_M5UNIFIED_BUFFERED */
#endif /* SANOTTS_SPEAKER_M5_BUFFERED_H */
