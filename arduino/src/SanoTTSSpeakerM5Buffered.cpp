/* SanoTTSSpeakerM5Buffered.h の実装。
 *
 * ⚠️ `M5.Speaker.playRaw` は**データをコピーしない**。基底クラスが「キューから外れるまで
 *    その枠に触らない」を守るので、ここではポインタをそのまま渡してよい。
 * ⚠️ `isPlaying(channel)` は**そのチャネルに積まれている数**（0〜2: 鳴っている 1 + 次の 1）。
 *    M5Unified の Speaker_Class.hpp の注記どおり、単一の生産者なら
 *    「2 未満になってから次を埋める」で 2 枚交互が成り立つ。
 */
#include "SanoTTSSpeakerM5Buffered.h"

#ifdef SANOTTS_HAVE_M5UNIFIED_BUFFERED

bool SanoTTSSpeakerM5Buffered::beginOutput(uint32_t sampleRate) {
    /* ⚠️ **ここで 1 回だけ設定する。** 発話ごとに config() を触らない。 */
    auto cfg = M5.Speaker.config();
    cfg.sample_rate = sampleRate;
    M5.Speaker.config(cfg);
    if (!(M5.Speaker.begin() && M5.Speaker.isEnabled())) {
        m_err = "M5.Speaker が使えない（begin/isEnabled が false）。"
                "スケッチで M5.begin() を呼んでいるか、この板にスピーカーが在るか確かめること";
        return false;
    }
    M5.Speaker.setVolume(m_volume);
    return true;
}

bool SanoTTSSpeakerM5Buffered::enqueue(const int16_t* pcm, size_t nSamples) {
    /* repeat=1 / stop_current=false（前の発話の後ろに並べる） */
    if (!M5.Speaker.playRaw(pcm, nSamples, 22050, false, 1, m_channel, false)) {
        m_err = "M5.Speaker.playRaw が false を返した";
        return false;
    }
    return true;
}

size_t SanoTTSSpeakerM5Buffered::queued() const {
    return M5.Speaker.isPlaying(m_channel);
}

#endif /* SANOTTS_HAVE_M5UNIFIED_BUFFERED */
