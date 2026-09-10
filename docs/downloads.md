# ダウンロード

> ⚠️ **v4（JSUT を外した重み）は資産 28 本まで用意できているが、まだリリースしていない。**
> ここに名前を書くと [`../scripts/check_release_assets.py`](../scripts/check_release_assets.py) が
> **実在を CI で検査して落ちる**ので、**タグを打つときに追加する。**
> 中身と実測は [`release-notes/v1.0.0.md`](release-notes/v1.0.0.md) と
> [M-119](measurements.md#m-119)〜[M-124](measurements.md#m-124)。
>
> ⚠️ **下の v0.3.x の `NOTICE.txt` / `LICENSE-MODEL.md` / `MODEL_CARD.md` /
> `saanotts-jp-v3-samples.zip` は帰属が足りていない**（[C-081](decisions.md#c-081)）。
> 差し替え用の資産は用意済みで、**アップロードは未実行**。


*[← README](../README.md)*

⚠️ **ここに名前を書いた資産は「実在すること」を CI が検査する**
（`scripts/check_release_assets.py`。C-052 の再発防止）。**消えたリンクを放置できない。**

**最新の [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) に全部入っている。**
⚠️ **モデルの重みは v0.1.0 以降すべて bit 同一**（再学習していない）。

| 資産 | どこ | 中身 |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 合成音の WAV |
| `saanotts-jp-v3-stage4.pt` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | PyTorch の重み（2,744,874 B） |
| `saanotts-jp-v3-int8.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | C99 コア用の int8 blob（**654,032 B / 形式 v2**）。⚠️ v0.2.0 の v1 は現行コアが拒む |
| `saanotts-jp-v3-fp32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 参照・デバッグ用の fp32 blob |
| `golden-v3-int8.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | `make -C csrc int8-golden` 用の参照出力 |
| `golden-v3-fp32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | `make -C csrc test` 用の参照出力 |
| `m5-cores3-firmware-kanji-16mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **M5Stack CoreS3 / スタックチャン**（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **漢字入力**・UART0（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 同・**USB Serial/JTAG**（native USB の板はこちら） |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **かな入力**・UART0（8 MB 以上） |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 同・**USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | かな入力・最適化なし（**PIE の比較対照**） |
| `k1-dict-438750.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 辞書 blob 単体（13,702,320 B） |

**小さい flash 向け**（[v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) で正式に配布。⚠️ **読みの精度が落ちる** — 出荷の基準は上の 16 MB で音素の誤り 0.63%）:

| ファイル | どこに | 何 |
|---|---|---|
| `esp32s3-firmware-kanji-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB の **DevKit**（228,000 entries / 1.01%） |
| `m5-cores3-firmware-kanji-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB の **M5Stack 系**（213,000 / 1.09%）。⚠️ **M5Unified を積む板はこちら** |
| `esp32s3-firmware-kanji-4mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **4 MB**（135,000 / 1.94%）。✅ **PSRAM 無しの ATOMS3 で鳴った**（M-109） |
| `esp32s3-firmware-kanji-2mb-budget.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **2 MB の枠**（44,000 / 3.86%）。⚠️ **4 MB のイメージとして焼く** |
| `k1-dict-228000-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB / DevKit の辞書単体 |
| `k1-dict-213000-8mb-m5.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB / M5Stack 系の辞書単体 |
| `k1-dict-135000-4mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 4 MB の辞書単体 |
| `k1-dict-44000-2mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 2 MB 枠の辞書単体 |
| `SHA256SUMS.txt` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **上下の表の 26 本すべての SHA-256**（自分自身の行は入っていない = D-045 の 3） |

⚠️ **辞書単体を焼くときは表とセットにすること。** 取り違えても**端末は止まらず、読みだけが落ちます**（残タスク 8）。
迷ったら**辞書入りの flash イメージ 1 本**を使ってください（オフセット 0 に焼くだけ）。

⚠️ **4 MB / 2 MB は第三者の実機で鳴りましたが**（[M-109](measurements.md#m-109)。PSRAM 無しの ATOMS3 でも）、
**checksum・定常 xRT・アンダーランは今も報告がありません**。私自身も再現していません。

検証用の [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) は**履歴として残してあります**（中身は v0.3.1 の 8 本と bit 同一）。

