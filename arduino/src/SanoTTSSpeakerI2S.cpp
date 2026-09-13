/* SanoTTSSpeakerI2S.h の実装。`esp32/main/saan_i2s.c` からの移植。 */
#include "SanoTTSSpeakerI2S.h"

#ifdef SANOTTS_HAVE_I2S_STD

#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

bool SanoTTSSpeakerI2S::begin(uint32_t sampleRate) {
    if (m_tx) return true;

    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    cc.dma_desc_num  = kDmaDesc;
    cc.dma_frame_num = kDmaFrame;
    /* ⚠️ アンダーラン時に前のデータを繰り返させない（繰り返すと「途切れ」ではなく
     *    「同じ音の反復」になり、原因が分かりにくくなる）。 */
    cc.auto_clear    = true;

    if (i2s_new_channel(&cc, &m_tx, nullptr) != ESP_OK) {
        m_err = "i2s_new_channel が失敗した";
        return false;
    }

    i2s_std_config_t sc = {};
    sc.clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(sampleRate);
    sc.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                      I2S_SLOT_MODE_MONO);
    sc.gpio_cfg.mclk = I2S_GPIO_UNUSED;
    sc.gpio_cfg.bclk = (gpio_num_t)m_bclk;
    sc.gpio_cfg.ws   = (gpio_num_t)m_ws;
    sc.gpio_cfg.dout = (gpio_num_t)m_dout;
    sc.gpio_cfg.din  = I2S_GPIO_UNUSED;

    if (i2s_channel_init_std_mode(m_tx, &sc) != ESP_OK) {
        m_err = "i2s_channel_init_std_mode が失敗した（GPIO を確かめること）";
        return false;
    }
    return true;
}

bool SanoTTSSpeakerI2S::beginUtterance(size_t nSamples) {
    if (!m_tx)         { m_err = "begin() が済んでいない"; return false; }
    if (nSamples == 0) { m_err = "0 sample の発話"; return false; }
    if (m_preroll) stop();

    const size_t nb = nSamples * sizeof(int16_t);
    void* p = heap_caps_malloc(nb, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) p = heap_caps_malloc(nb, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!p) { m_err = "プリロールを確保できない（PSRAM も内部 DRAM も）"; return false; }

    m_preroll = (int16_t*)p;
    m_prerollCap  = nSamples;
    m_prerollFill = 0;
    return true;
}

bool SanoTTSSpeakerI2S::prerollPush(const int16_t* pcm, size_t nSamples) {
    if (!m_preroll) { m_err = "beginUtterance() が済んでいない"; return false; }
    if (m_prerollFill + nSamples > m_prerollCap) return false;
    for (size_t i = 0; i < nSamples; ++i) m_preroll[m_prerollFill + i] = pcm[i];
    m_prerollFill += nSamples;
    return true;
}

bool SanoTTSSpeakerI2S::writeRaw(const int16_t* p, size_t n) {
    size_t wrote = 0;
    const size_t want = n * sizeof(int16_t);
    if (i2s_channel_write(m_tx, p, want, &wrote, portMAX_DELAY) != ESP_OK) {
        m_err = "i2s_channel_write が失敗した";
        return false;
    }
    if (wrote != want) {
        /* ⚠️ **握りつぶさない。** 部分書き込みは「音は出るが尾が切れる」形になる。 */
        m_err = "i2s_channel_write が全部書かなかった";
        return false;
    }
    return true;
}

bool SanoTTSSpeakerI2S::start() {
    if (!m_tx) { m_err = "begin() が済んでいない"; return false; }
    if (!m_enabled) {
        if (i2s_channel_enable(m_tx) != ESP_OK) { m_err = "i2s_channel_enable が失敗した"; return false; }
        m_enabled = true;
    }
    /* 貯めたぶんを kMaxChunk ずつに割って送る。 */
    size_t off = 0;
    while (off < m_prerollFill) {
        size_t n = m_prerollFill - off;
        if (n > kMaxChunk) n = kMaxChunk;
        if (!writeRaw(m_preroll + off, n)) return false;
        off += n;
    }
    m_prerollFill = 0;
    return true;
}

bool SanoTTSSpeakerI2S::write(const int16_t* pcm, size_t nSamples) {
    if (nSamples > kMaxChunk) { m_err = "チャンクが大きすぎる"; return false; }
    return writeRaw(pcm, nSamples);
}

void SanoTTSSpeakerI2S::stop() {
    if (m_tx && m_enabled) {
        /* ⚠️ `i2s_channel_write` は **DMA に渡し終えた時点で返る**。最後の DMA
         *    バッファ（6 × 512 frame ≒ 139 ms）が鳴り切るまで待ってから disable する。
         *    待たないと**語尾が切れる**（`esp32/main/saan_i2s.c` と同じ待ち方）。 */
        vTaskDelay(pdMS_TO_TICKS(kDmaDesc * kDmaFrame * 1000 / 22050 + 10));
        i2s_channel_disable(m_tx);
        m_enabled = false;
    }
    if (m_preroll) {
        heap_caps_free(m_preroll);
        m_preroll = nullptr;
        m_prerollCap = 0;
        m_prerollFill = 0;
    }
}

#endif /* SANOTTS_HAVE_I2S_STD */
