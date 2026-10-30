/* SanoTTSSpeakerBuffered.h の実装。
 *
 * ⚠️ **落としてはいけない不変条件が 2 つある。どちらも「音は出るが壊れている」形で失敗する。**
 *
 *   1. **キューに居るバッファに書かない。** `enqueue()` はコピーしないので、
 *      鳴り終わる前に同じ枠へ次の発話を書くと、**鳴っている音が途中から次の文になる**。
 *      → 枠を 2 枚交互に使い、**次に使う枠（= 2 発話前に積んだもの）が鳴り終わるまで待つ**。
 *        キューは FIFO なので「`queued() < kSlots`」なら 2 発話前の枠は空いている
 *   2. **鳴っている枠を realloc しない。** 伸ばすのは貯めている枠だけで、
 *      それは (1) で空いていることが保証されている
 *
 * ⚠️ 陽性対照 `SANOTTS_BUFFERED_TEST_ONE_SLOT` は**テスト専用**（枠を 1 枚に潰して (1) を破る）。
 *    `scripts/arduino_qemu_tests/buffered.cpp` の T3 がこれで落ちることを要求する。
 */
#include "SanoTTSSpeakerBuffered.h"

#include <string.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#if defined(SANOTTS_BUFFERED_TEST_ONE_SLOT) && SANOTTS_BUFFERED_TEST_ONE_SLOT
#  define SANOTTS_BUFFERED_NSLOTS 1
#else
#  define SANOTTS_BUFFERED_NSLOTS SanoTTSSpeakerBuffered::kSlots
#endif

/* ⚠️ 陽性対照は**回す枚数だけ**を 1 にする。待つ条件（`queued() >= kSlots`）は変えないので、
 *    2 発話目が**まだ鳴っている 1 発話目の枠**に書く = 不変条件 1 が破れる。 */

/* 確保の刻み（= 1 チャンク 8 フレーム × hop 256）。`beginUtterance()` に来た数が
 * 足りなかったとき（自前で say() 相当を書いた場合など）にだけ使う。 */
static const size_t kGrowStep = 2048;

SanoTTSSpeakerBuffered::~SanoTTSSpeakerBuffered() {
    /* ⚠️ **ここで wait() を呼べない**（基底のデストラクタから派生の queued() は呼べない）。
     *    破棄する前に呼び出し側が `wait()` すること。鳴っている枠を free すると
     *    解放済みメモリを鳴らす。 */
    for (size_t i = 0; i < kSlots; ++i) {
        if (m_slot[i].buf) heap_caps_free(m_slot[i].buf);
        m_slot[i] = Slot{nullptr, 0, 0};
    }
}

void SanoTTSSpeakerBuffered::idle() { vTaskDelay(1); }

bool SanoTTSSpeakerBuffered::begin(uint32_t sampleRate) {
    if (m_ready) return true;
    if (sampleRate != 22050) { m_err = "コアは 22,050 Hz 固定"; return false; }
    if (!beginOutput(sampleRate)) {
        if (!m_err || !*m_err) m_err = "音の出口の beginOutput() が失敗した";
        return false;
    }
    m_ready = true;
    return true;
}

/* ⚠️ **倍々に伸ばさない。** 1 回目は `say()` が渡す総サンプル数ちょうどを取る。
 *    倍々だと最大 2 倍を無駄に抱え、**PSRAM の無い板では 2 枚目が取れなかった**
 *    （QEMU。内部 RAM の空き 110,764 B に対し、1 枚目が 27,136 sample の発話に 88,200 B を
 *    抱えていた。M-151）。
 * ⚠️ **中身が要らないときは realloc しない**（free → malloc）。realloc は新旧の 2 ブロックを
 *    同時に要るので、捨てる中身のために倍のメモリを一瞬要求する。 */
bool SanoTTSSpeakerBuffered::grow(Slot& s, size_t need) {
    if (need <= s.cap) return true;
    const size_t cap = (need + kGrowStep - 1) / kGrowStep * kGrowStep;
    const size_t nb = cap * sizeof(int16_t);
    void* p = nullptr;
    if (s.fill == 0) {
        if (s.buf) heap_caps_free(s.buf);
        s.buf = nullptr; s.cap = 0;
        /* まず PSRAM、無ければ内部 DRAM */
        p = heap_caps_malloc(nb, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!p) p = heap_caps_malloc(nb, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    } else {
        /* ⚠️ realloc は失敗しても元のバッファを残す */
        p = heap_caps_realloc(s.buf, nb, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!p) p = heap_caps_realloc(s.buf, nb, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (!p) { m_err = "発話を貯めるバッファを確保できない（PSRAM も内部 DRAM も足りない）"; return false; }
    s.buf = (int16_t*)p;
    s.cap = cap;
    return true;
}

bool SanoTTSSpeakerBuffered::beginUtterance(size_t nSamples) {
    if (!m_ready) { m_err = "begin() が済んでいない"; return false; }
    if (m_cur >= 0) stop();            /* 前の発話が閉じられていない。捨てる */
    /* ⚠️ **不変条件 1。** 次の枠は 2 発話前に積んだもの。鳴り終わるまで待つ。 */
    while (queued() >= kSlots) idle();
    m_cur = (int)m_next;
    m_slot[m_cur].fill = 0;
    m_started = false;
    /* `SanoTTS::say()` は総サンプル数を渡す。**ちょうどを 1 回で取る**（この枠は (1) で空いている）。
     * ⚠️ `kWholeUtterance` のまま来たら（総数を知らない呼び出し側）先取りせず、push で伸ばす。 */
    if (nSamples < kWholeUtterance && !grow(m_slot[m_cur], nSamples)) {
        m_cur = -1;
        return false;
    }
    return true;
}

bool SanoTTSSpeakerBuffered::prerollPush(const int16_t* pcm, size_t nSamples) {
    if (m_cur < 0) { m_err = "beginUtterance() が済んでいない"; return false; }
    if (m_started) { m_err = "start() の後に prerollPush() が来た"; return false; }
    Slot& s = m_slot[m_cur];
    if (!grow(s, s.fill + nSamples)) return false;
    memcpy(s.buf + s.fill, pcm, nSamples * sizeof(int16_t));
    s.fill += nSamples;
    return true;
}

bool SanoTTSSpeakerBuffered::start() {
    if (m_cur < 0) { m_err = "beginUtterance() が済んでいない"; return false; }
    Slot& s = m_slot[m_cur];
    m_started = true;
    if (s.fill == 0) return true;      /* 0 sample の発話は積まない */
    if (!enqueue(s.buf, s.fill)) {
        if (!m_err || !*m_err) m_err = "音の出口の enqueue() が失敗した";
        return false;
    }
    m_next = (m_next + 1) % (size_t)SANOTTS_BUFFERED_NSLOTS;
    return true;
}

bool SanoTTSSpeakerBuffered::write(const int16_t*, size_t) {
    m_err = "SanoTTSSpeakerBuffered に write() が来た（発話は全部プリロールに入るはず）";
    return false;
}

void SanoTTSSpeakerBuffered::stop() {
    /* ⚠️ **待たない。** start() 前なら積んでいないので、そのまま捨てる。 */
    if (m_cur >= 0 && !m_started) m_slot[m_cur].fill = 0;
    m_cur = -1;
    m_started = false;
}

void SanoTTSSpeakerBuffered::wait() {
    while (queued() > 0) idle();
}
