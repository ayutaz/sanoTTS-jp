# ダウンロード

> ✅ **v1.0.0 を配っています**（2026-09-11。資産 **28 本** / 140 MB）。
> 重みは **v4**（蒸留テキストから JSUT を外し、CC0 / パブリックドメインのみで学習し直したもの)。
> 中身と実測は
> [リリースノート](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) と
> [M-119](measurements.md#m-119)〜[M-134](measurements.md#m-134)。
>
> ⚠️ **v0.3.0 / v0.3.1 の `NOTICE.txt` / `LICENSE-MODEL.md` / `MODEL_CARD.md` /
> `saanotts-jp-v4-samples.zip` は帰属が足りていません**（LibriTTS-R / CML-TTS / AISHELL-3 の
> 記載と Apache-2.0 全文が無い。[C-081](decisions.md#c-081)）。
> ❌ **差し替えないと決めました**（[D-061](decisions.md#d-061)）。**正しい帰属は v1.0.0 からです。**


*[← README](../README.md)*

⚠️ **ここに名前を書いた資産は「実在すること」を CI が検査する**
（`scripts/check_release_assets.py`。C-052 の再発防止）。**消えたリンクを放置できない。**

**最新の [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) に全部入っている。**
⚠️ **タグの `v1.0.0` と資産名の `v4` は別の軸**（`v4` は**モデルの版**で、v0.3.x が `-v3-` を配っていたのと同じ関係）。
⚠️ **重みは v1.0.0 で変わった** — v0.1.0 〜 v0.3.1 は `-v3-` で bit 同一だったが、
v1.0.0 は蒸留テキストから JSUT を外して学習し直した **v4**（[D-057](decisions.md#d-057) / [D-059](decisions.md#d-059)）。
⚠️ **v3 の資産も消していない**（v0.3.1 のタグから今も落とせる）。

| 資産 | どこ | 中身 |
|---|---|---|
| `saanotts-jp-v4-samples.zip` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 合成音の WAV |
| `saanotts-jp-v4-stage4.pt` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | PyTorch の重み（2,744,874 B） |
| `saanotts-jp-v4-int8.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | C99 コア用の int8 blob（**654,032 B / 形式 v2**）。⚠️ v0.2.0 の v1 は現行コアが拒む |
| `saanotts-jp-v4-fp32.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 参照・デバッグ用の fp32 blob |
| `golden-v4-int8.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | `make -C csrc int8-golden` 用の参照出力 |
| `golden-v4-fp32.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | `make -C csrc test` 用の参照出力 |
| `m5-cores3-firmware-kanji-16mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **M5Stack CoreS3 / スタックチャン**（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **漢字入力**・UART0（16 MB 必須） |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 同・**USB Serial/JTAG**（native USB の板はこちら） |
| `esp32s3-firmware-w8a8-pie.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **かな入力**・UART0（8 MB 以上） |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 同・**USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | かな入力・最適化なし（**PIE の比較対照**） |
| `k1-dict-438750.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 辞書 blob 単体（13,702,320 B） |

**小さい flash 向け**（[v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) で正式に配布。⚠️ **読みの精度が落ちる** — 出荷の基準は上の 16 MB で音素の誤り 0.63%）:

| ファイル | どこに | 何 |
|---|---|---|
| `esp32s3-firmware-kanji-8mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 8 MB の **DevKit**（228,000 entries / 1.01%） |
| `m5-cores3-firmware-kanji-8mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 8 MB の **M5Stack 系**（213,000 / 1.09%）。⚠️ **M5Unified を積む板はこちら** |
| `esp32s3-firmware-kanji-4mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **4 MB**（135,000 / 1.94%）。✅ **PSRAM 無しの ATOMS3 で鳴った**（M-109） |
| `esp32s3-firmware-kanji-2mb-budget.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **2 MB の枠**（44,000 / 3.86%）。⚠️ **4 MB のイメージとして焼く** |
| `k1-dict-228000-8mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 8 MB / DevKit の辞書単体 |
| `k1-dict-213000-8mb-m5.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 8 MB / M5Stack 系の辞書単体 |
| `k1-dict-135000-4mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 4 MB の辞書単体 |
| `k1-dict-44000-2mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | 2 MB 枠の辞書単体 |
| `SHA256SUMS.txt` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | **28 本のうち 27 本の SHA-256**（自分自身の行は入っていない = D-045 の 3） |

⚠️ **辞書単体を焼くときは表とセットにすること。** 取り違えても**端末は止まらず、読みだけが落ちます**（⚠️ **検査の入れ方は決めましたが、実装は v1.0.0 の後**です = [D-063](decisions.md#d-063)）。
迷ったら**辞書入りの flash イメージ 1 本**を使ってください（オフセット 0 に焼くだけ）。

⚠️ **4 MB / 2 MB は第三者の実機で鳴りましたが**（[M-109](measurements.md#m-109)。PSRAM 無しの ATOMS3 でも）、
**checksum・定常 xRT・アンダーランは今も報告がありません**。私自身も再現していません。

検証用の [v0.3.1-rc1-smallflash](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1-rc1-smallflash) は**履歴として残してあります**（中身は v0.3.1 の 8 本と bit 同一）。

