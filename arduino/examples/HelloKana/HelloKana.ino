/* M5Stack 系の板で 1 文喋る最小の例。
 *
 * 要るもの:
 *   - SanoTTS-jp（このライブラリ）
 *   - SanoTTS-jp-voice-tsukuyomi-v4（重み。⚠️ MIT ではない）
 *   - M5Unified
 *
 * ⚠️ 漢字を喋らせるには辞書 13.7 MB を dict パーティションに焼く必要がある。
 *    焼いていなければ kanjiReady() が false になり、**かな中間表現なら喋れる**。 */
#include <Arduino.h>
#include <M5Unified.h>
#include <SanoTTS.h>
#include <SanoTTSSpeakerM5.h>

SanoTTS tts;
SanoTTSSpeakerM5 spk;

void setup() {
  M5.begin();
  Serial.begin(115200);
  delay(300);

  tts.setSpeaker(&spk);
  if (!tts.begin()) { Serial.println(tts.lastError()); return; }

  const char* text = tts.kanjiReady() ? "今日は良い天気ですね。"
                                      : "きょ][おわよ][いて][んきです°ね";
  if (!tts.say(text)) { Serial.println(tts.lastError()); return; }

  Serial.printf("%u sample / checksum 0x%016llx\n",
                (unsigned)tts.samples(), (unsigned long long)tts.checksum());
}

void loop() { M5.update(); }
