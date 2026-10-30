/* 発話を**丸ごと貯めてから鳴らす**スピーカー（区間ごとの蓄積再生）。
 *
 * ## いつ使うか
 *
 * **合成が実時間に間に合わない構成**で、ストリーミング再生（`SanoTTSSpeakerM5` など）が
 * 途切れるとき。典型は **Wi-Fi と併用して arena を PSRAM に置いた**（`SANOTTS_ARENA_HEAP=1`）
 * 場合。AI スタックチャン Ex は PSRAM の arena で音が途切れ、この形にして途切れなくなったと
 * 報告している（第三者の報告。⚠️ 私は再現していない）。
 *
 * ## 何が変わるか
 *
 *   - `say()` は**合成し終わったら、鳴り終わるのを待たずに戻る**。次の `say()` は
 *     **前の発話を鳴らしている間に合成する**（バッファは 2 枚を交互に使う）
 *   - 発話の頭で鳴り出すまでの時間は**合成にかかる時間**になる（ストリーミングより遅い）
 *   - **途切れない**（鳴らすときには全部そろっている）
 *   - PCM は PSRAM に置く（無ければ内部 DRAM）。**1 秒 = 44,100 B**、2 枚ぶん要る
 *
 * 長い文章は**アプリ側で文に区切って** `say()` を続けて呼ぶ（`examples/BufferedPlayback`）。
 * 最後に `wait()` で鳴り終わりを待つ。
 *
 * ## 音の出口を足すには
 *
 * 3 つの関数を埋める（`SanoTTSSpeakerM5Buffered` が M5.Speaker 版）:
 *
 *   beginOutput(22050)  起動時に 1 回
 *   enqueue(pcm, n)     **非同期に**鳴らし始める。⚠️ **コピーしなくてよい** — `queued()` から
 *                       外れるまで、このクラスはそのバッファに触らない
 *   queued()            積んだうち**まだ鳴り終わっていない数**（0〜kSlots）。
 *                       ⚠️ **先に積んだものから順に**鳴り終わること（FIFO）
 *
 * ⚠️ **1 つのタスクからだけ呼ぶこと**（`SanoTTS` と同じ）。
 */
#ifndef SANOTTS_SPEAKER_BUFFERED_H
#define SANOTTS_SPEAKER_BUFFERED_H

#include <stddef.h>
#include <stdint.h>

#include "SanoTTSSpeaker.h"

class SanoTTSSpeakerBuffered : public SanoTTSSpeaker {
public:
    virtual ~SanoTTSSpeakerBuffered();

    bool   begin(uint32_t sampleRate) override;
    /* ⚠️ **発話を丸ごとプリロールに積ませるための値。** `SanoTTS::say()` はプリロールを
     *    この数まで貯めてから `start()` を呼ぶので、大きくしておけば全部が貯まる。
     *    **この数のバッファを確保するわけではない** — `say()` は `beginUtterance()` に
     *    min(この値, 発話の総サンプル数) を渡すので、**総サンプル数ちょうど**を 1 回で取る。 */
    size_t prerollSamples() const override { return kWholeUtterance; }
    bool   beginUtterance(size_t nSamples) override;
    bool   prerollPush(const int16_t* pcm, size_t nSamples) override;
    bool   start() override;
    /* ⚠️ **呼ばれないはず**（発話は全部プリロールに入る）。呼ばれたら false。 */
    bool   write(const int16_t* pcm, size_t nSamples) override;
    /* ⚠️ **待たずに戻る**（基底の約束「鳴り終わるまで待つ」と違う）。待つなら `wait()`。
     *    `start()` の前に呼ばれたら（合成の失敗経路）、その発話は**鳴らさずに捨てる**。 */
    void   stop() override;

    /* 積んだ発話が全部鳴り終わるまで待つ。 */
    void   wait();
    /* まだ鳴り終わっていない発話の数。 */
    size_t pending() const { return queued(); }

    const char* lastError() const { return m_err; }

    static const size_t kSlots = 2;
    static const size_t kWholeUtterance = (size_t)1 << 30;

protected:
    virtual bool   beginOutput(uint32_t sampleRate) = 0;
    virtual bool   enqueue(const int16_t* pcm, size_t nSamples) = 0;
    virtual size_t queued() const = 0;
    /* 空きを待つ間に呼ぶ。既定は 1 tick 眠る。 */
    virtual void   idle();

    const char* m_err = "";

private:
    struct Slot { int16_t* buf; size_t cap; size_t fill; };
    bool grow(Slot& s, size_t need);

    Slot   m_slot[kSlots] = {{nullptr, 0, 0}, {nullptr, 0, 0}};
    size_t m_next = 0;          /* 次の発話が使う枠 */
    int    m_cur = -1;          /* いま貯めている枠（-1 = 発話の外） */
    bool   m_started = false;
    bool   m_ready = false;
};

#endif /* SANOTTS_SPEAKER_BUFFERED_H */
