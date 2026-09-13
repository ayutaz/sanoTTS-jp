/* 漢字かな交じり文をそのまま喋る例（M5Stack 系 / 16 MB flash）。
 *
 * ## 先にやること
 *
 * 1. platformio.ini に 16 MB のパーティション表を指定する:
 *
 *      board_build.partitions = sanotts_16mb.csv
 *      （このライブラリの extras/partitions/ からコピーしてプロジェクト直下に置く）
 *
 * 2. 辞書 13,702,320 B を dict パーティションに焼く（**1 回だけ**）:
 *
 *      # リリースから落とす
 *      gh release download v1.0.0 -R ayutaz/sanoTTS-jp -p k1-dict-438750.bin
 *      esptool --chip esp32s3 -p /dev/cu.usbmodem2101 write_flash 0x2D0000 k1-dict-438750.bin
 *
 * ⚠️ **辞書を焼かなくても起動する。** kanjiReady() が false になり、
 *    かな中間表現なら喋れる（D-063）。「黙って無音」にはならない。
 *
 * ⚠️ **ホストのフル辞書とは読みが一致しない。** 枝刈りの分だけ必ず食い違う
 *    （音素の 0.63% / n=1,495）。**既知の代償**であって欠陥ではない。 */
#include <Arduino.h>
#include <M5Unified.h>
#include <SanoTTS.h>
#include <SanoTTSSpeakerM5.h>

SanoTTS tts;
SanoTTSSpeakerM5 spk;

static const char* kLines[] = {
  "今日は良い天気ですね。",
  "電池の残量は八十五パーセントです。",
  "二千二十六年九月十三日、午後三時。",
};

void setup() {
  M5.begin();
  Serial.begin(115200);
  delay(300);

  tts.setSpeaker(&spk);
  if (!tts.begin()) { Serial.println(tts.lastError()); return; }

  if (!tts.kanjiReady()) {
    Serial.println("辞書が無い（か SHA-256 が合わない）ので、かな専用で動いている。");
    Serial.println("dict パーティションに k1-dict-438750.bin を焼くこと。");
    tts.say("きょ][おわよ][いて][んきです°ね");
    return;
  }

  for (size_t i = 0; i < sizeof(kLines) / sizeof(kLines[0]); ++i) {
    if (!tts.say(kLines[i])) { Serial.println(tts.lastError()); continue; }
    Serial.printf("%s → %u sample / checksum 0x%016llx\n",
                  kLines[i], (unsigned)tts.samples(),
                  (unsigned long long)tts.checksum());
    delay(500);
  }
}

void loop() { M5.update(); }
