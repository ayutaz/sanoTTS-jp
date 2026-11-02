/* G-AR10 — `SanoTTSSpeakerBuffered`（区間ごとに貯めて鳴らす）の受け入れ（QEMU）。
 *
 * `scripts/check_arduino_qemu.sh` が重み入りでビルドして QEMU で走らせ、
 * `SAAN_TEST PASS` の行を探す。⚠️ **M5.Speaker は QEMU に無い**ので、音の出口は
 * 下の `FakeOut`（キューを真似るだけ）で置き換える。**M5 の実機で鳴るかは見ていない。**
 *
 * 見ているもの:
 *   T1  貯めた PCM が**ストリーミングと同じ列**か。同じ文を `synthesize()`（PCM を
 *       コールバックで受ける経路）で合成した FNV-1a 64 と、積んだバッファの FNV-1a 64 が
 *       一致する。キューに積むのは**1 発話 1 回**
 *   T2  `say()` が**鳴り終わる前に戻る**（= 鳴らしている間に次を合成できる）
 *   T3  **まだキューに居るバッファを上書きしない**（3 発話続けて、鳴り終わった時点の
 *       中身が積んだ時点の中身と同じか）。3 発話目では空きを**待つ**（idle が呼ばれる）
 *   T4  `start()` の前に `stop()` が来たら**積まない**（say() の失敗経路）
 *   T5  `wait()` でキューが空になる
 *
 * ⚠️ **ESP-IDF 版の基準値（0x390bf4b2…）とは比べない。** それは G-AR4 の仕事で、
 *    しかも **Windows ホストでは基準値そのものが出ない**（docs/measurements.md M-150 §4）。
 *    ここで比べるのは**同じビルド・同じ環境の 2 経路**だけ（それなら環境に依らず bit 一致する）。
 *
 * ⚠️ **陽性対照**: `-DSANOTTS_BUFFERED_TEST_ONE_SLOT=1` でバッファを 1 枚に潰すと
 *    **T3 が落ちる**こと（`check_arduino_qemu.sh` が要求する）。落ちなければ T3 は空虚。
 */
#include <Arduino.h>
#include <SanoTTS.h>
#include <SanoTTSSpeakerBuffered.h>
#include <esp_heap_caps.h>

static uint64_t fnv(const int16_t* p, size_t n) {
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < n; ++i) {
        const uint16_t u = (uint16_t)p[i];
        h = (h ^ (uint8_t)(u & 0xff)) * 1099511628211ull;
        h = (h ^ (uint8_t)(u >> 8)) * 1099511628211ull;
    }
    return h;
}

/* `synthesize()` のコールバックで受けた PCM を、同じ式で畳む。 */
struct RefSink { uint64_t h = 1469598103934665603ull; size_t n = 0; };
static void refSink(const int16_t* p, size_t n, void* u) {
    RefSink* r = (RefSink*)u;
    for (size_t i = 0; i < n; ++i) {
        const uint16_t v = (uint16_t)p[i];
        r->h = (r->h ^ (uint8_t)(v & 0xff)) * 1099511628211ull;
        r->h = (r->h ^ (uint8_t)(v >> 8)) * 1099511628211ull;
    }
    r->n += n;
}

/* キューを真似る。`idle()` が呼ばれるたびに先頭を 1 つ「鳴り終わった」ことにする。 */
class FakeOut : public SanoTTSSpeakerBuffered {
public:
    struct Ent { const int16_t* p; size_t n; uint64_t h; };
    Ent    q[8];
    size_t qn = 0;
    int    enqueued = 0, played = 0, idles = 0, overwritten = 0;
    uint64_t lastEnqHash = 0;
    size_t   lastEnqN = 0;

    void finishOne() {
        if (qn == 0) return;
        /* ⚠️ 鳴り終わった時点の中身が、積んだ時点と同じでなければ上書きされている */
        if (fnv(q[0].p, q[0].n) != q[0].h) ++overwritten;
        for (size_t i = 1; i < qn; ++i) q[i - 1] = q[i];
        --qn; ++played;
    }
protected:
    bool beginOutput(uint32_t) override { return true; }
    bool enqueue(const int16_t* p, size_t n) override {
        if (qn >= 8) return false;
        q[qn++] = Ent{p, n, fnv(p, n)};
        lastEnqHash = q[qn - 1].h; lastEnqN = n;
        ++enqueued;
        return true;
    }
    size_t queued() const override { return qn; }
    void idle() override { ++idles; finishOne(); }
};

static SanoTTS tts;
static FakeOut out;
static int fails = 0;
#define CHECK(c, ...) do { if (c) { Serial.printf("SAAN_TEST OK   " __VA_ARGS__); Serial.println(); } \
                           else   { Serial.printf("SAAN_TEST FAIL " __VA_ARGS__); Serial.println(); ++fails; } } while (0)

static const char* kA = "きょ][おわよ][いて][んきです°ね";
/* ⚠️ **3 文とも中身が違うこと。** 同じ文だと上書きされても hash が変わらず T3 が空虚になる。
 * ⚠️ **B / C は短くしてある**（雨 / 飴 のアクセントの対）。QEMU には PSRAM が無く、
 *    1.5 秒級の発話を 2 枚抱えると内部 RAM が足りず 3 発話目が確保できない（M-151）。
 *    T3 が見ているのは積み方で、長さには依らない。 */
static const char* kB = "あ]め";
static const char* kC = "あ[め";

void setup() {
    Serial.begin(115200);
    delay(200);
    tts.setSpeaker(&out);
    if (!tts.begin()) { Serial.printf("SAAN_ERR %s\n", tts.lastError()); return; }

    /* 比較の相手: 同じ文を PCM コールバックの経路で合成する */
    RefSink ref;
    if (!tts.synthesize(kA, refSink, &ref)) { Serial.printf("SAAN_ERR %s\n", tts.lastError()); return; }
    CHECK(ref.n > 0 && ref.h == tts.checksum(),
          "T0 synthesize() の PCM が checksum() と一致（0x%016llx / %u sample）",
          (unsigned long long)ref.h, (unsigned)ref.n);

    /* T1 / T2 */
    const bool okA = tts.say(kA);
    CHECK(okA, "T1 say(A) が成功する（%s）", okA ? "" : tts.lastError());
    CHECK(out.enqueued == 1, "T1 1 発話でキューに積むのは 1 回（%d 回）", out.enqueued);
    CHECK(out.lastEnqHash == ref.h && out.lastEnqN == ref.n,
          "T1 貯めた PCM が synthesize() と bit 一致（0x%016llx / %u sample）",
          (unsigned long long)out.lastEnqHash, (unsigned)out.lastEnqN);
    CHECK(out.pending() == 1 && out.played == 0, "T2 say() は鳴り終わる前に戻る（キュー %u）",
          (unsigned)out.pending());

    /* T3 */
    const int idles0 = out.idles;
    /* ⚠️ **ヒープを走査する関数（largest_free_block / check_integrity）は呼ばない。**
     *    この QEMU では起動直後でも tlsf の走査で LoadProhibited になる（M-151）。 */
    Serial.printf("SAAN_INFO 内部 RAM の空き %u B / PSRAM %u B\n",
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    const bool okB = tts.say(kB);
    if (!okB) Serial.printf("SAAN_INFO say(B): %s / %s\n", tts.lastError(), out.lastError());
    const bool okC = okB && tts.say(kC);
    if (okB && !okC) Serial.printf("SAAN_INFO say(C): %s / %s\n", tts.lastError(), out.lastError());
    CHECK(okB && okC, "T3 say(B) / say(C) が成功する");
    CHECK(out.idles > idles0, "T3 3 発話目では空きを待つ（idle %d 回）", out.idles - idles0);
    out.wait();
    CHECK(out.overwritten == 0, "T3 キューに居るバッファを上書きしていない（上書き %d 件）",
          out.overwritten);

    /* T4 — start() の前の stop() は積まない */
    const int enq0 = out.enqueued;
    const int16_t z[4] = {1, 2, 3, 4};
    out.beginUtterance(out.prerollSamples());
    out.prerollPush(z, 4);
    out.stop();
    CHECK(out.enqueued == enq0, "T4 start() 前の stop() は積まない（%d 件増えた）",
          out.enqueued - enq0);
    const bool okA2 = tts.say(kA);
    CHECK(okA2 && out.lastEnqHash == ref.h && out.lastEnqN == ref.n,
          "T4 その後の発話も synthesize() と bit 一致");

    /* T5 */
    out.wait();
    CHECK(out.pending() == 0 && out.played == out.enqueued,
          "T5 wait() でキューが空になる（積んだ %d / 鳴った %d）", out.enqueued, out.played);

    Serial.printf(fails == 0 ? "SAAN_TEST PASS\n" : "SAAN_TEST FAILED %d\n", fails);
}

void loop() {}
