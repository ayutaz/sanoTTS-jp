/* G-AR11 — 話速 `SanoTTS::setSpeechRate()` の受け入れ（QEMU）。
 *
 * `scripts/check_arduino_qemu.sh` が重み入りでビルドして QEMU で走らせ、
 * `SAAN_TEST PASS` の行を探す。スピーカーは要らない（`synthesize()` の PCM を数えるだけ）。
 *
 * 見ているもの:
 *   R0  既定の `lengthScale()` が `kDefaultLengthScale`（D-019 の 1.2187）と bit 一致
 *   R1  `setSpeechRate(1.0)` は**何も変えない** — 値が bit 一致し、PCM の FNV-1a 64 も一致
 *   R2  0.8 で**長く**、1.25 で**短く**なる（向き）。長さの比は 1/rate の近く
 *   R3  正の有限値以外（0 / 負 / NaN / ±INF / 割ると INF になる値）は **false を返して何も変えない**
 *   R4  `setLengthScale()` も同じ検査をする
 *   R5  1.0 に戻すと PCM が既定と bit 一致（状態が残らない）
 *
 * ⚠️ **ESP-IDF 版の基準値とは比べない**（それは G-AR4。Windows では基準値が出ない = M-150 §4）。
 *    比べるのは**同じビルドの中の既定値の合成**だけ。
 * ⚠️ **R2 の比の幅（±0.1）は回帰検出用で、合格基準ではない。** 音素ごとの長さは
 *    round と clip[1,80] を通るので 1/rate ちょうどにはならない。
 *
 * ⚠️ **陽性対照**（`check_arduino_qemu.sh` が要求する）:
 *   `-DSANOTTS_SPEECH_RATE_TEST_INVERT=1`   向きを逆にする（s_v = 既定 × rate）→ R2 が落ちる
 *   `-DSANOTTS_SPEECH_RATE_TEST_NO_CHECK=1` 値の検査を外す → R3 が落ちる
 */
#include <Arduino.h>
#include <SanoTTS.h>
#include <math.h>
#include <string.h>

static SanoTTS tts;
static int fails = 0;
#define CHECK(c, ...) do { if (c) { Serial.printf("SAAN_TEST OK   " __VA_ARGS__); Serial.println(); } \
                           else   { Serial.printf("SAAN_TEST FAIL " __VA_ARGS__); Serial.println(); ++fails; } } while (0)

static void nop(const int16_t*, size_t, void*) {}

static const char* kA = "きょ][おわよ][いて][んきです°ね";

static bool sameBits(float a, float b) { return memcmp(&a, &b, sizeof a) == 0; }

struct Run { bool ok; uint64_t h; uint32_t n; };
static Run synth() {
    Run r;
    r.ok = tts.synthesize(kA, nop, nullptr);
    r.h = tts.checksum();
    r.n = tts.samples();
    if (!r.ok) Serial.printf("SAAN_INFO synthesize: %s\n", tts.lastError());
    return r;
}

void setup() {
    Serial.begin(115200);
    delay(200);
    if (!tts.begin()) { Serial.printf("SAAN_ERR %s\n", tts.lastError()); return; }

    /* R0 */
    const float def = SanoTTS::kDefaultLengthScale;
    CHECK(sameBits(tts.lengthScale(), def) && sameBits(def, 1.2187f),
          "R0 既定の lengthScale() が 1.2187 と bit 一致（%.7g）", (double)tts.lengthScale());
    const Run base = synth();
    CHECK(base.ok && base.n > 0, "R0 既定で合成できる（0x%016llx / %u sample）",
          (unsigned long long)base.h, (unsigned)base.n);

    /* R1 */
    const bool ok1 = tts.setSpeechRate(1.0f);
    CHECK(ok1 && sameBits(tts.lengthScale(), def), "R1 setSpeechRate(1.0) は値を変えない（%.7g）",
          (double)tts.lengthScale());
    const Run r1 = synth();
    CHECK(r1.ok && r1.h == base.h && r1.n == base.n,
          "R1 setSpeechRate(1.0) の PCM が既定と bit 一致（0x%016llx / %u sample）",
          (unsigned long long)r1.h, (unsigned)r1.n);

    /* R2 */
    const bool okSlow = tts.setSpeechRate(0.8f);
    const Run slow = synth();
    const double kSlow = base.n ? (double)slow.n / base.n : 0.0;
    CHECK(okSlow && slow.ok && slow.n > base.n, "R2 setSpeechRate(0.8) で長くなる（%u → %u sample）",
          (unsigned)base.n, (unsigned)slow.n);
    CHECK(fabs(kSlow - 1.25) <= 0.1, "R2 0.8 の長さの比が 1/0.8 = 1.25 の ±0.1（%.4f）", kSlow);

    const bool okFast = tts.setSpeechRate(1.25f);
    const Run fast = synth();
    const double kFast = base.n ? (double)fast.n / base.n : 0.0;
    CHECK(okFast && fast.ok && fast.n < base.n, "R2 setSpeechRate(1.25) で短くなる（%u → %u sample）",
          (unsigned)base.n, (unsigned)fast.n);
    CHECK(fabs(kFast - 0.8) <= 0.1, "R2 1.25 の長さの比が 1/1.25 = 0.8 の ±0.1（%.4f）", kFast);

    /* R3 — どの値でも false を返し、直前の値（1.25 のときの s_v）を変えない */
    const float before = tts.lengthScale();
    const float bad[] = {0.0f, -0.0f, -1.0f, NAN, INFINITY, -INFINITY,
                         1e-45f /* 既定 ÷ これ = INF */};
    const char* badName[] = {"0", "-0", "-1", "NaN", "+INF", "-INF", "1e-45（割ると INF）"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; ++i) {
        const bool r = tts.setSpeechRate(bad[i]);
        CHECK(!r && sameBits(tts.lengthScale(), before),
              "R3 setSpeechRate(%s) は false で何も変えない（%s / %.7g）", badName[i],
              r ? "true" : "false", (double)tts.lengthScale());
    }

    /* R4 — ⚠️ **直前の値を取り直す。** R3 と同じ `before` を使うと、R3 が壊れたとき
     *    （陽性対照）に R4 まで巻き添えで落ち、どの検査が効いたのか読めなくなる。 */
    (void)tts.setSpeechRate(1.25f);
    const float before4 = tts.lengthScale();
    const float badLs[] = {0.0f, -1.0f, NAN, INFINITY};
    const char* badLsName[] = {"0", "-1", "NaN", "+INF"};
    for (size_t i = 0; i < sizeof badLs / sizeof badLs[0]; ++i) {
        const bool r = tts.setLengthScale(badLs[i]);
        CHECK(!r && sameBits(tts.lengthScale(), before4),
              "R4 setLengthScale(%s) は false で何も変えない（%s）", badLsName[i], r ? "true" : "false");
    }
    CHECK(tts.setLengthScale(def) && sameBits(tts.lengthScale(), def),
          "R4 setLengthScale(既定) は true");

    /* R5 */
    const bool ok5 = tts.setSpeechRate(0.8f) && tts.setSpeechRate(1.0f);
    const Run r5 = synth();
    CHECK(ok5 && r5.ok && r5.h == base.h && r5.n == base.n,
          "R5 1.0 に戻すと PCM が既定と bit 一致（0x%016llx）", (unsigned long long)r5.h);

    Serial.printf(fails == 0 ? "SAAN_TEST PASS\n" : "SAAN_TEST FAILED %d\n", fails);
}

void loop() {}
