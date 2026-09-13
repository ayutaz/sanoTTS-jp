/* SanoTTSSpeakerM5.h の実装。`esp32/boards/m5unified/main/saan_audio_m5.cpp` からの移植。
 *
 * ⚠️ **落としてはいけない不変条件が 3 つある。どれも「音は出るが壊れている」形で失敗する。**
 *
 *   1. `M5.Speaker.playRaw` は**データをコピーしない**。渡したポインタを Speaker が
 *      CPU で読みに来るので、鳴り終わるまでそのメモリを生かしておく必要がある
 *      → リングを **3 枚**回す（キューは current + next の 2 枚なので、
 *        今から書く枠は 2 回前に queue したもの = 既に再生済み）
 *   2. `stop()` は `isPlaying()` が false になるまで**待つ**。待たずに戻ると、
 *      次の発話の変換がバッファを上書きして**前の発話の尾が化ける**
 *   3. プリロール用バッファは**再生が終わってから**解放する。先に free すると
 *      解放済みメモリを鳴らす（音は出るので気づけない）
 */
#include "SanoTTSSpeakerM5.h"

#ifdef SANOTTS_HAVE_M5UNIFIED

#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

/* ⚠️ **コアの出力レートと同じ 22,050 Hz を M5 側にも設定する。**
 *    違う値にすると M5Unified がリサンプルし、`SanoTTS::checksum()` が一致していても
 *    実際に鳴る波形が変わる（checksum では検出できない）。 */
static const uint32_t kOutRate = 22050;

/* まず PSRAM、無ければ内部 DRAM。
 * ⚠️ **DMA から読まれるバッファではない**（M5.Speaker は CPU で読んで自前の DMA へ
 *    ミックスする）ので PSRAM でよい。 */
static int16_t* spkAlloc(size_t nSamples) {
    const size_t nb = nSamples * sizeof(int16_t);
    void* p = heap_caps_malloc(nb, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p) return (int16_t*)p;
    return (int16_t*)heap_caps_malloc(nb, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

bool SanoTTSSpeakerM5::begin(uint32_t sampleRate) {
    if (m_ready) return true;
    if (sampleRate != kOutRate) {
        m_err = "コアは 22,050 Hz 固定";
        return false;
    }

    /* ⚠️ **確保を先に済ませる。** 途中で失敗したらスピーカーを触らずに戻る。
     *    ⚠️ **二度と解放しない** — playRaw が持つポインタの生存期間は
     *    プログラムと同じでなければならない。 */
    for (size_t i = 0; i < kNumBuffers; ++i) {
        if (m_ring[i]) continue;
        m_ring[i] = spkAlloc(kMaxChunk);
        if (!m_ring[i]) { m_err = "送出バッファを確保できない（PSRAM も内部 DRAM も）"; return false; }
    }

    auto cfg = M5.Speaker.config();
    cfg.sample_rate = kOutRate;
    M5.Speaker.config(cfg);

    if (!(M5.Speaker.begin() && M5.Speaker.isEnabled())) {
        m_err = "M5.Speaker が使えない（begin/isEnabled が false）。"
                "スケッチで M5.begin() を呼んでいるか、この板にスピーカーが在るか確かめること";
        return false;
    }
    M5.Speaker.setVolume(m_volume);

    m_ready = true;
    return true;
}

bool SanoTTSSpeakerM5::play(const int16_t* p, size_t n) {
    /* repeat=1 / channel=0 / stop_current=false。
     * キューが満杯なら M5 側でブロックして戻ってくる。 */
    if (!M5.Speaker.playRaw(p, n, kOutRate, false, 1, 0, false)) {
        m_err = "M5.Speaker.playRaw が false を返した";
        return false;
    }
    return true;
}

bool SanoTTSSpeakerM5::beginUtterance(size_t nSamples) {
    if (!m_ready)    { m_err = "begin() が済んでいない"; return false; }
    if (nSamples == 0) { m_err = "0 sample の発話"; return false; }
    if (m_preroll) {
        /* stop() を呼ばずに次の発話へ来た。前の再生が終わっているとは限らない。 */
        stop();
    }
    m_preroll = spkAlloc(nSamples);
    if (!m_preroll) { m_err = "プリロールを確保できない"; return false; }
    m_prerollCap  = nSamples;
    m_prerollFill = 0;
    return true;
}

bool SanoTTSSpeakerM5::prerollPush(const int16_t* pcm, size_t nSamples) {
    if (!m_preroll) { m_err = "beginUtterance() が済んでいない"; return false; }
    if (m_prerollFill + nSamples > m_prerollCap) return false;
    for (size_t i = 0; i < nSamples; ++i) m_preroll[m_prerollFill + i] = pcm[i];
    m_prerollFill += nSamples;
    return true;
}

bool SanoTTSSpeakerM5::start() {
    if (!m_ready) { m_err = "begin() が済んでいない"; return false; }
    if (m_prerollFill > 0) {
        /* ⚠️ m_preroll は stop() が再生完了を待ってから解放する。 */
        if (!play(m_preroll, m_prerollFill)) return false;
        m_prerollFill = 0;
    }
    return true;
}

bool SanoTTSSpeakerM5::write(const int16_t* pcm, size_t nSamples) {
    if (nSamples > kMaxChunk) { m_err = "チャンクが大きすぎる"; return false; }
    /* ⚠️ **コピーしてから playRaw。** 生きているポインタは最大 2 本なので、
     *    3 枚回しの今の枠は既に再生済み。 */
    int16_t* dst = m_ring[m_ringIdx];
    for (size_t i = 0; i < nSamples; ++i) dst[i] = pcm[i];
    m_ringIdx = (m_ringIdx + 1) % kNumBuffers;
    return play(dst, nSamples);
}

void SanoTTSSpeakerM5::stop() {
    /* ⚠️ **鳴らし終わるまで待つ。**（不変条件 2） */
    while (M5.Speaker.isPlaying()) vTaskDelay(1);
    /* ⚠️ **再生が終わってから解放する。**（不変条件 3） */
    if (m_preroll) {
        heap_caps_free(m_preroll);
        m_preroll = nullptr;
        m_prerollCap = 0;
        m_prerollFill = 0;
    }
}

#endif /* SANOTTS_HAVE_M5UNIFIED */
