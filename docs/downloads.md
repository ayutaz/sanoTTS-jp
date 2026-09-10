# ダウンロード

*[← README](../README.md)*

⚠️ **ここに名前を書いた資産は「実在すること」を CI が検査する**
（`scripts/check_release_assets.py`。C-052 の再発防止）。**消えたリンクを放置できない。**

**最新の [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) に全部入っている。**
⚠️ **モデルの重みは v0.1.0 以降すべて bit 同一**（再学習していない）。

| 資産 | どこ | 中身 |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 合成音の WAV |
| `saanotts-jp-v3-stage4.pt` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | PyTorch の重み（2,744,874 B） |
| `saanotts-jp-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | C99 コア用の int8 blob（**654,032 B / 形式 v2**）。⚠️ v0.2.0 の v1 は現行コアが拒む |
| `saanotts-jp-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 参照・デバッグ用の fp32 blob |
| `golden-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | `make -C csrc int8-golden` 用の参照出力 |
| `golden-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | `make -C csrc test` 用の参照出力 |
| `m5-cores3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **M5Stack CoreS3 / スタックチャン**（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **漢字入力**・UART0（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 同・**USB Serial/JTAG**（native USB の板はこちら） |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **かな入力**・UART0（8 MB 以上） |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 同・**USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | かな入力・最適化なし（**PIE の比較対照**） |
| `k1-dict-438750.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 辞書 blob 単体（13,702,320 B） |

**小さい flash の検証用**（⚠️ **prerelease**。出荷版ではありません）:

| ファイル | どこに | 何 |
|---|---|---|
| `esp32s3-firmware-kanji-8mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 8 MB の **DevKit**（228,000 entries / 1.01%） |
| `m5-cores3-firmware-kanji-8mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 8 MB の **M5Stack 系**（213,000 / 1.09%）。⚠️ **M5Unified を積む板はこちら** |
| `esp32s3-firmware-kanji-4mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | **4 MB**（135,000 / 1.94%）。✅ **PSRAM 無しの ATOMS3 で鳴った**（M-109） |
| `esp32s3-firmware-kanji-2mb-budget.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | **2 MB の枠**（44,000 / 3.86%）。⚠️ **4 MB のイメージとして焼く** |
| `k1-dict-228000-8mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 8 MB / DevKit の辞書単体 |
| `k1-dict-213000-8mb-m5.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 8 MB / M5Stack 系の辞書単体 |
| `k1-dict-135000-4mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 4 MB の辞書単体 |
| `k1-dict-44000-2mb.bin` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 2 MB 枠の辞書単体 |
| `SHA256SUMS.txt` | [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) | 上の 8 本の SHA-256 |

⚠️ **辞書単体を焼くときは表とセットにすること。** 取り違えても**端末は止まらず、読みだけが落ちます**（残タスク 8）。

