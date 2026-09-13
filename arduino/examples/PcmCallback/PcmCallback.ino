/* PCM をコールバックで受け取る（スピーカーを使わない）最小の例。
 *
 * ⚠️ 重みライブラリ SanoTTS-jp-voice-tsukuyomi-v4 が要る。無いと begin() が
 *    理由つきで false を返す（黙って無音にはならない）。 */
#include <Arduino.h>
#include <SanoTTS.h>

SanoTTS tts;
static size_t g_total;

static void onPcm(const int16_t* pcm, size_t n, void* user) {
  (void)pcm; (void)user;
  g_total += n;                     // ← ここで I2S / SD / BLE に流す
}

void setup() {
  Serial.begin(115200);
  delay(300);

  if (!tts.begin()) { Serial.println(tts.lastError()); return; }
  Serial.printf("漢字: %s\n", tts.kanjiReady() ? "使える" : "辞書が無いのでかな専用");

  // 漢字かな交じり文でも、かな中間表現でもよい（端末が自分で判定する）
  const char* text = tts.kanjiReady() ? "今日は良い天気ですね。"
                                      : "きょ][おわよ][いて][んきです°ね";
  if (!tts.synthesize(text, onPcm)) { Serial.println(tts.lastError()); return; }

  Serial.printf("%u sample / %.3f s / checksum 0x%016llx / |max| %d\n",
                (unsigned)g_total, (double)g_total / SanoTTS::kSampleRate,
                (unsigned long long)tts.checksum(), (int)tts.absmax());
}

void loop() {}
