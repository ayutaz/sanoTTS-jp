/* M5Unified の M5.Speaker へ流す実装（CoreS3 / Core2 / AtomS3 / Basic …）。
 *
 *   #include <M5Unified.h>
 *   #include <SanoTTS.h>
 *   #include <SanoTTSSpeakerM5.h>
 *
 *   SanoTTS tts;  SanoTTSSpeakerM5 spk;
 *   void setup() { M5.begin(); tts.setSpeaker(&spk); tts.begin(); tts.say("…"); }
 *
 * ⚠️ **`M5.begin()` はスケッチ側で呼ぶこと。** ここでは呼ばない
 *    （ESP-IDF 版の `saan_audio_m5.cpp` は自分で呼んでいたが、Arduino では
 *      画面や電源の設定をする人が決めるべき）。
 *
 * ⚠️ **M5Unified が入っていなければ、このヘッダは中身ごと消える。**
 *    `__has_include` で判定するので、依存として書かなくてもビルドは通る。
 */
#ifndef SANOTTS_SPEAKER_M5_H
#define SANOTTS_SPEAKER_M5_H

#if defined(__has_include)
#  if __has_include(<M5Unified.h>)
#    define SANOTTS_HAVE_M5UNIFIED 1
#  endif
#endif

#ifdef SANOTTS_HAVE_M5UNIFIED

#include <M5Unified.h>
#include "SanoTTSSpeaker.h"

class SanoTTSSpeakerM5 : public SanoTTSSpeaker {
public:
    /* `volume` は 0-255。⚠️ **聴取で決めること。** 大きすぎると int16 の
     * クリップではなく**アンプ側で歪む**ので、`SanoTTS::absmax()` には出ない。 */
    explicit SanoTTSSpeakerM5(uint8_t volume = 128) : m_volume(volume) {}

    bool   begin(uint32_t sampleRate) override;
    bool   beginUtterance(size_t nSamples) override;
    bool   prerollPush(const int16_t* pcm, size_t nSamples) override;
    bool   start() override;
    bool   write(const int16_t* pcm, size_t nSamples) override;
    void   stop() override;

    const char* lastError() const { return m_err; }

    /* 1 チャンク = SAAN_CHUNK * SAAN_HOP = 2,048 sample（92.88 ms）。 */
    static const size_t kMaxChunk = 2048;
    /* ⚠️ **3 未満にしないこと。** `playRaw` はポインタを持つだけで、
     *    M5.Speaker のキューは 2 枚（current + next）ある。2 枚回しだと
     *    **まだ鳴っているバッファを上書きする**（音は出るので気づけない）。 */
    static const size_t kNumBuffers = 3;

private:
    bool play(const int16_t* p, size_t n);

    int16_t* m_ring[kNumBuffers] = {nullptr, nullptr, nullptr};
    size_t   m_ringIdx = 0;
    int16_t* m_preroll = nullptr;
    size_t   m_prerollCap = 0;
    size_t   m_prerollFill = 0;
    bool     m_ready = false;
    uint8_t  m_volume;
    const char* m_err = "";
};

#endif /* SANOTTS_HAVE_M5UNIFIED */
#endif /* SANOTTS_SPEAKER_M5_H */
