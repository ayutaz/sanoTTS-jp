# sanoTTS-jp — Arduino / PlatformIO ライブラリ

ESP32-S3 で**実時間に間に合う日本語ニューラル TTS**（567 K params / 22.05 kHz）。
**漢字かな交じり文をそのまま**喋らせられる（端末側 G2P。ネットワーク不要）。

```cpp
#include <M5Unified.h>
#include <SanoTTS.h>
#include <SanoTTSSpeakerM5.h>

SanoTTS tts;
SanoTTSSpeakerM5 spk;

void setup() {
  M5.begin();
  tts.setSpeaker(&spk);
  tts.begin();
  tts.say("今日は良い天気ですね。");
}
void loop() { M5.update(); }
```

---

## ⚠️ 先に読むこと

| | |
|---|---|
| **板** | **ESP32-S3 が要る。** ESP32 / C3 / C5 では実時間に間に合わない（整数 SIMD が無い） |
| **flash** | かな専用なら 4 MB。**漢字は 16 MB 推奨**（辞書 13.7 MB） |
| **PlatformIO** | ⚠️ **公式の `espressif32` では動かない。** [pioarduino](https://github.com/pioarduino/platform-espressif32)（arduino-esp32 3.x）が要る |
| **Arduino IDE** | Boards Manager の `esp32` **3.x**。⚠️ **漢字は PlatformIO を推奨**（下記） |
| **ライセンス** | このコードは **MIT**。⚠️ **重みは MIT ではない**（下記） |
| **実機** | ⚠️ **このライブラリは実機で鳴らしていない**（作者が板を持っていない）。下記「何が確かめてあるか」 |

---

## 入れる

**.zip を 2 本**使う。コードと重みでライセンスが違うので分けてある。

### PlatformIO

```ini
[env:m5stack-cores3]
; ⚠️ 公式の espressif32 は arduino-esp32 2.0.17（ESP-IDF 4.4）で止まっており、
;    ESP_PARTITION_MMAP_DATA も driver/i2s_std.h も無い。3.x が要る。
platform = https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip
board = m5stack-cores3
framework = arduino

; 漢字を使うときだけ。extras/partitions/ からプロジェクト直下にコピーする
board_build.partitions = sanotts_16mb.csv

lib_deps =
    https://github.com/ayutaz/sanoTTS-jp/releases/latest/download/sanoTTS-jp-arduino.zip
    https://github.com/ayutaz/sanoTTS-jp/releases/latest/download/sanoTTS-jp-voice-tsukuyomi-v4.zip
    m5stack/M5Unified
```

⚠️ **版を固定したいなら `latest` ではなくタグを書く**
（`.../releases/download/v1.1.0/sanoTTS-jp-arduino.zip`）。**同じファイルが両方の URL で取れる** — 資産名に版を入れていないのは `latest/download/` が**完全一致**を要求するため。

### Arduino IDE

1. [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) から .zip を 2 本落とす
2. **スケッチ → ライブラリをインクルード → .ZIP 形式のライブラリをインストール** を 2 回
3. **ツール → ボード → esp32 → ESP32S3 Dev Module**（または使う板）
4. **ツール → Partition Scheme → Huge APP (3MB No OTA/1MB SPIFFS)**

⚠️ **これはかな専用の手順。** 漢字を使うには 13.7 MB の `dict` パーティションが要り、
Arduino IDE でカスタムパーティション表を使うのは**確実ではない**（下記）。

---

## 使う

### 入力は 2 通り。**どちらでもよく、端末が自分で判定する**

```cpp
tts.say("今日は良い天気ですね。");            // 漢字かな交じり（辞書が要る）
tts.say("きょ][おわよ][いて][んきです°ね");   // かな中間表現（辞書が要らない）
```

かな中間表現の記号: `[` 上昇 / `]` 下降核 / `#` 句境界 / `°` 無声化。
ホストで漢字文から作るなら `uv run python scripts/to_intermediate.py "文"`。

⚠️ **同じ文をかなで書いても漢字で書いても、出る PCM は bit 一致する。**
⚠️ **混ぜると拒否する**（中間表現の記号が混じった漢字文は喋らない）。

### 例（`examples/`）

| 例 | 何をするか | 要るもの |
|---|---|---|
| `HelloKana` | かな中間表現を I2S DAC で喋る | なし（辞書不要） |
| `HelloKanji` | 漢字かな交じり文を M5.Speaker で喋る | M5Unified / 16 MB の辞書 |
| `PcmCallback` | PCM をコールバックで受ける（スピーカー無し） | なし |
| `M5Stack-Avatar-Talk` | **スタックチャンの顔を出し、喋る音量で口を動かす**（タッチで次の文） | M5Unified 0.2.22 / M5GFX 0.2.29 / **`meganetaaan/M5Stack-Avatar@0.10.0`**（Arduino IDE では `M5Stack_Avatar`）  |

### PCM を自分で扱う

```cpp
tts.synthesize("今日は良い天気ですね。",
  [](const int16_t* pcm, size_t n, void* user) {
    // 22,050 Hz / モノラル / int16。SD に書く、BLE で送る、自前の DAC に流す …
  });
```

⚠️ **コールバックが返るまで次のチャンクは作られない。** ここで待つと速度が落ちる。

### スピーカーを自分で書く

`SanoTTSSpeaker` の 6 メソッドを実装して `setSpeaker()` する（`src/SanoTTSSpeaker.h`）。
⚠️ **プリロールを省かないこと** — 最初の 1 回の合成は定常の約 6 倍かかるので、
貯めずに鳴らし始めると必ず途切れる。

---

## 漢字を喋らせる（辞書を焼く）

**1 回だけ**。app とは別のパーティションに 13.7 MB を書き込む。

```bash
# 1. 表をプロジェクト直下にコピーして platformio.ini で指定する
cp <ライブラリ>/extras/partitions/sanotts_16mb.csv .

# 2. 辞書を落とす
gh release download v1.0.0 -R ayutaz/sanoTTS-jp -p k1-dict-438750.bin

# 3. 焼く（offset は表の dict 行と同じ 0x2D0000）
pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 \
    -p /dev/cu.usbmodem2101 write_flash 0x2D0000 k1-dict-438750.bin
```

⚠️ **辞書を焼かなくても起動する。** `kanjiReady()` が false になり、**かな中間表現なら喋れる**。
SHA-256 が合わない場合も同じで、**漢字経路だけ無効にして起動は続く**。黙って無音にはならない。

⚠️ **辞書とパーティション表は必ずセット。** 8 MB 板には 8 MB 用の辞書
（`k1-dict-213000-8mb-m5.bin` + `sanotts_8mb.csv`）を使うこと。代償は読みの精度
（音素の誤り 0.63% → 1.09%）。

⚠️ **Arduino IDE では確実ではない。** スケッチフォルダの `partitions.csv` は
[公式ドキュメントにはある](https://docs.espressif.com/projects/arduino-esp32/en/latest/tutorials/partition_table.html)が、
[IDE 2.x で効かない報告がある](https://github.com/espressif/arduino-esp32/issues/10120)。
**漢字を使うなら PlatformIO を勧める。**

---

## 設定

⚠️ **Arduino IDE はライブラリに `-D` を渡せない**（1.5 仕様）。設定は
**`src/sanotts_config.h` 1 枚**に集約してあるので、PlatformIO なら `build_flags`、
Arduino IDE なら**このファイルを直接編集する**。

| マクロ | 既定 | 何が変わるか |
|---|---|---|
| `SANOTTS_ENABLE_KANJI` | `1` | `0` にすると辞書リーダと Open JTalk が消え、app が約 230 KB 小さくなる |
| `SANOTTS_ENABLE_PIE` | S3 なら `1` | 整数 SIMD。⚠️ **これ無しでは実時間に間に合わない**。S3 以外では自動で 0 |
| `SANOTTS_MODEL_FROM_PARTITION` | `0` | `1` で重みを `model` パーティションから読む（app が 654 KB 小さくなる） |
| `SANOTTS_ARENA_HEAP` | `0` | `1` で arena 136 KB をヒープから取る。**非 S3 板では必須** |
| `SANOTTS_DICT_SHA256` | 未定義 | 辞書の SHA-256 を照合する。⚠️ 未定義なら起動時に「照合していない」と警告が出る |
| `SANOTTS_MAX_INPUT_BYTES` | `512` | 受け付ける 1 行の最大バイト数 |

⚠️ **`SAAN_PIE` を直接立てないこと。** `SAAN_INT8_ACT` とセットでしか意味が無く、
片方だけだと**1 命令も効かないのに「有効にしたつもり」**になる。`SANOTTS_ENABLE_PIE` を使う。

---

## メモリと大きさ（実測）

`pioarduino 55.03.311`（arduino-esp32 3.3.11 / ESP-IDF 5.5.5）での実測。
詳細は [`docs/measurements.md` M-137](../docs/measurements.md#m-137)。

| 構成 | RAM | Flash |
|---|---:|---:|
| かな専用（重み抜き） | 219,384 | 308,505 |
| 漢字 + M5Unified（重み抜き） | 228,356 | 541,231 |
| **漢字 + M5Unified + 重み**（実際に配る形） | 224,216 | **1,067,844** |

RAM の大半は合成用の arena 139,264 B（`.bss` に静的確保。⚠️ **v1.1.0 までは 180,224 B** = [M-142](../docs/measurements.md#m-142) で 28,672 B / [M-144](../docs/measurements.md#m-144) でさらに 12,288 B 詰めた）。⚠️ **136 KB は漢字経路の Viterbi のための値**で、合成だけなら 116 KB で足りる（[C-101](../docs/decisions.md#c-101)）。
⚠️ **PSRAM の無い板**では、漢字の一時ヒープが内部 DRAM から来る（最大 80 KB）。

---

## 何が確かめてあるか / 確かめていないか

⚠️ **このライブラリは実機で鳴らしていない。** 作者が板を持っていない。

✅ **確かめたこと**:

- **PCM が ESP-IDF ビルドと bit 一致する**（QEMU。W8A8+PIE `0x390bf4b2aef8f2ec` /
  W8A32 `0x9cbe622a4a53af7e`。どちらも実機で確かめてある基準値と同じ）
- 3 構成（かな / 漢字 / 非 S3）でビルドが通る
- **整数 SIMD が実際に効いている**（`saanotts_int8.c.o` に 74 命令。非 S3 では 0）
- **PlatformIO と arduino-cli の両方**で、.zip から引いて重みがリンクされる

⚠️ **確かめていないこと**:

- **実時間に間に合うか**（`xRT` / アンダーラン / 鳴らし始めまでの時間）。
  ESP-IDF 版は同じ CoreS3 で **xRT 0.474 / アンダーラン 0 / 364〜374 ms** だが、
  **同じ PCM が出ることと、間に合って出ることは別**
- **Arduino IDE の GUI**（検証は arduino-cli 1.5.1）
- **Arduino ビルドで漢字を実際に喋らせること**（コンパイルは通っている）
- **音そのもの**（聴取は ESP-IDF 版で 1 名・対照つき・非盲検のみ）

**実機をお持ちなら報告してください** → [esp32/TESTING.md](../esp32/TESTING.md)

---

## ライセンス

| | |
|---|---|
| **このライブラリのコード** | **MIT**（[LICENSE](../LICENSE)） |
| 取り込んだ Open JTalk | 修正 BSD。⚠️ **改変している** → [NOTICE.txt](NOTICE.txt) |
| **重み**（`SanoTTS-jp-voice-*`） | ⚠️ **MIT ではない。** `LicenseRef-sanoTTS-jp-Model-1.0` |

⚠️ **重みを含む製品を配る側には義務がある**（重み .zip 同梱の `LICENSE-MODEL.md`）:

1. **帰属表示**（§3.1 のブロックをそのまま表示する。[NOTICE.txt](NOTICE.txt) に写しがある）
2. **出力の用途制限 4 項目**（§3.2）を**自社の利用規約に書く**
3. **コピーレフト**

⚠️ **「商用利用可」だけを見て組み込むと 2 を落とす。** `LICENSE-MODEL.md` を読むこと。

---

## 開発（このリポジトリから作る）

```bash
uv sync --extra arduino

# src/core/ は生成物（.gitignore）。clone しただけでは存在しない
uv run --no-project python scripts/build_arduino_lib.py

# csrc/ の逐語であることの検査（陽性対照 7 件）
uv run --no-project python scripts/build_arduino_lib.py --check

# リリース .zip 2 本を組む
uv run --no-project python scripts/build_arduino_lib.py \
    --zip arduino/dist --version 1.1.0 --blob /path/to/saanotts-jp-v4-int8.bin

# ゲート
bash scripts/ci_arduino_build.sh    # G-AR1 / G-AR2 / G-AR6（3 構成 + PIE の命令数）
bash scripts/ci_arduino_zip.sh      # G-AR3 / G-AR7（.zip から引く + 重みが在るか）
bash scripts/check_arduino_qemu.sh  # G-AR4 ⭐ PCM が ESP-IDF ビルドと bit 一致するか
```

⚠️ **`arduino/src/core/` を手で編集しない。** `scripts/build_arduino_lib.py` の生成物で、
`csrc/` を直したら作り直される。`--check` が食い違いを落とす。
