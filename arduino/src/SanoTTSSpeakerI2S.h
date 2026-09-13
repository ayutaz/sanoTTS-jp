/* 汎用 I2S DAC へ流す実装（ESP-IDF の `driver/i2s_std`）。
 *
 *   SanoTTSSpeakerI2S spk(5, 6, 7);      // BCLK, WS(LRCK), DOUT
 *
 * ⚠️ この行に C の引数コメントを書かないこと — ブロックコメントの中の
 *    アスタリスク + スラッシュでコメントが閉じる（CLAUDE.md が警告している罠）。
 *
 * ⚠️ **arduino-esp32 3.x（ESP-IDF 5.x）が要る。** 2.0.x には `driver/i2s_std.h` が無い。
 * ⚠️ **S3 に APLL は無い。実サンプルレートの誤差は未測定**（ESP-IDF 版も同じ）。
 * ⚠️ **M5Stack 系の板ではこれを使わない** — 電源とアンプの初期化を M5Unified が
 *    やっているので、`SanoTTSSpeakerM5` の方が正しい。
 */
#ifndef SANOTTS_SPEAKER_I2S_H
#define SANOTTS_SPEAKER_I2S_H

#include "SanoTTSSpeaker.h"

#if defined(__has_include)
#  if __has_include(<driver/i2s_std.h>)
#    define SANOTTS_HAVE_I2S_STD 1
#  endif
#endif

#ifdef SANOTTS_HAVE_I2S_STD

#include <driver/i2s_std.h>

class SanoTTSSpeakerI2S : public SanoTTSSpeaker {
public:
    SanoTTSSpeakerI2S(int bclk = 5, int ws = 6, int dout = 7)
        : m_bclk(bclk), m_ws(ws), m_dout(dout) {}

    bool begin(uint32_t sampleRate) override;
    bool beginUtterance(size_t nSamples) override;
    bool prerollPush(const int16_t* pcm, size_t nSamples) override;
    bool start() override;
    bool write(const int16_t* pcm, size_t nSamples) override;
    void stop() override;

    const char* lastError() const { return m_err; }

    /* DMA の段数と 1 段のフレーム数。6 × 512 = 3,072 フレーム ≒ 139 ms。
     * ⚠️ **これでスループット不足は埋まらない**（DMA を何段積んでも、1 チャンクの
     *    計算がその音声より遅ければ途切れる）。埋めたのは W8A8 + PIE の方。 */
    static const int    kDmaDesc  = 6;
    static const int    kDmaFrame = 512;
    static const size_t kMaxChunk = 2048;

private:
    bool writeRaw(const int16_t* p, size_t n);

    i2s_chan_handle_t m_tx = nullptr;
    int16_t* m_preroll = nullptr;
    size_t   m_prerollCap = 0;
    size_t   m_prerollFill = 0;
    bool     m_enabled = false;
    int m_bclk, m_ws, m_dout;
    const char* m_err = "";
};

#endif /* SANOTTS_HAVE_I2S_STD */
#endif /* SANOTTS_SPEAKER_I2S_H */
