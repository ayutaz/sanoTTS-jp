# sanoTTS-jp

***日本語** · [English](README.en.md)*

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-ブラウザで試す-brightgreen.svg)](https://ayutaz.github.io/sanoTTS-jp/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Model license](https://img.shields.io/badge/model-not%20MIT-orange.svg)](LICENSE-MODEL.md)

**559 K パラメータの日本語 TTS を、$3 のマイコン（ESP32-S3）で動かす試み。**

### 🔊 ブラウザで試す → **<https://ayutaz.github.io/sanoTTS-jp/>**

インストール不要。**漢字かな交じり文をそのまま打つと喋る。**
⚠️ **マイコンに載っているのと同じ C99 コード**を WebAssembly にしたもの（arena も同じ 180,224 B。[D-050](docs/decisions.md#d-050)）。
⚠️ **初回に辞書 5.5 MB を落とす**（gzip）。

[arXiv:2608.21378](https://arxiv.org/abs/2608.21378) "sanoTTS" の蒸留レシピを日本語に適用し、
[piper-plus](https://github.com/ayutaz/piper-plus)（MB-iSTFT-VITS2）を教師として、
Duration / Acoustic / iSTFT Decoder の 3 つの小さな生徒に蒸留する。
**推論は依存ゼロの C99**（libm のみ・`malloc` を呼ばない）。

M5Stack CoreS3（スタックチャン）にファームを 1 本焼くと、シリアルに
**`今日は良い天気ですね。` と打つだけで喋る**。形態素解析もアクセント推定も端末の中で走る。

```
かな> 今日は良い天気ですね。
saanotts: 経路: 辞書
saanotts: 漢字 G2P: 33 B -> 形態素 7 個 / ids 53 個 / 25.69 ms
saanotts: init 21.56 ms / 53 ids / 106 frames / 27136 sample / 音声 1.231 s
saanotts: 定常 xRT = 0.446（満チャンク pull の中央値 / 92.88 ms）
saanotts: アンダーラン 0 / 14 チャンク
saanotts: 出力 PCM: 27136 sample / FNV-1a 0xa69a7ebbb5ccb05f
```

*（実機の生ログ [`reports/m90_cores3/device_m5_kanji.log`](reports/m90_cores3/device_m5_kanji.log) から抜粋）*

| | |
|---|---:|
| モデル | **559 K params** / int8 で **654,032 B**（flash） |
| 実行時 RAM | **157 KB**（ESP32-S3 の SRAM 512 KB の 34%） |
| 速度 | **xRT 0.446**（満チャンク 1 pull。⚠️ 発話全体では 0.54〜0.71） |
| 品質 | 教師の **64%**（SCOREQ 比 0.644）。⚠️ **予測器のスコアで、人の耳ではない** |
| 端末の G2P | 漢字あり **13.7 MB 辞書** / かなだけなら **877 B のテーブル** |

⚠️ **これは検証 (PoC) であって製品ではない。**

## 使う

| | 見るもの |
|---|---|
| **すぐ試す** | ↑ の[ブラウザ版](https://ayutaz.github.io/sanoTTS-jp/)（インストール不要） |
| **好きな文を合成する / 実機で喋らせる / ゲートを回す** | **[はじめかた](docs/getting-started.md)**（A〜E の 5 つの入口） |
| **どの板で動くか・辞書の大きさと精度** | **[対応状況](docs/support-matrix.md)** |
| **重み・ファーム・辞書を落とす** | **[ダウンロード](docs/downloads.md)** |

**いちばん短い道**（実機を持っているなら）:

```bash
# 16 MB の ESP32-S3 に、辞書ごと焼くだけ（ESP-IDF は要らない）
esptool.py --chip esp32s3 -p <ポート> write_flash 0x0 esp32s3-firmware-kanji-16mb.bin
```

⚠️ **8 MB / 4 MB の板でも動く**（辞書を小さくする。読みの精度は落ちる）。
→ [対応状況](docs/support-matrix.md)

## なぜ日本語版が別に要るのか

**英語版のレシピをそのまま移すと G2P で詰まる。** 漢字を読むには辞書が要り、
NAIST-JDIC は実測 **102 MB** でマイコンに載らない。そこで辞書を小さくするのではなく
**問題の切り分け方を変えた** — 端末は「ひらがな + アクセント記号」だけを受け取り、
**877 B のテーブル**で音素に変換する。**論文にも公式実装にも対応物が無い**、
このリポジトリの中心的な設計判断。

⚠️ **後にこの前提を測り直したら崩れた。** 辞書を TTS 専用の形式にすると
1 エントリ 130 B → **28 B** になり、16 MB ボードに **438,750 entries** が載る。
いまは**端末だけで漢字も読める**が、かな中間表現は**両方の経路の共通の中間形式**として
残っている（同じ文をどちらで書いても PCM が bit 一致する）。

ピッチアクセント（箸／橋／端）と無声化母音（「です」「した」の `i` `u`）も英語版には
無い問題で、どちらも**集約スコアでは検出できない**ため専用の評価を用意した
（→ [`MODEL_CARD.md`](MODEL_CARD.md)）。

## しくみ

```
漢字かな交じり文
   │  ホスト側・オフライン（OpenJTalk）  ┊  端末側・辞書 13.7 MB（-DSAAN_KANJI=1）
   ▼                                     ┊  438,750 entries を flash に mmap
かな中間表現   きょ][おわよ][いて][んきです°ね     [ 上昇 / ] 下降核 / # 句境界 / ° 無声化
   │  端末側・877 B のテーブルのみ        ┊  どちらの経路かは saan_g2p_classify() が決める
   ▼                                     ┊  （かな / 辞書 / 拒否。両経路とも同じ音素IDに合流）
音素ID ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
            33 K params      195 K            331 K
                        └─ 40 次元の潜在インターフェース（c-line）で接続
```

**3 つの生徒を明示的な潜在インターフェースで繋ぐのが要点。** これを省いて
テキスト→波形を 1 本のネットで学ぶと、論文の対照実験では訓練文を丸暗記して
未知の文が読めなくなる。

- **C99 推論コア**（`csrc/`）— 依存は libm のみ。`malloc` を呼ばず arena を使う。
  ストリーミング版は一括版と **bit 完全一致**（27,136 sample）
- **オンデバイス G2P**（`csrc/g2p.c`）— テーブル **877 B** / コード 1,549 B / **作業メモリ 0 B**
- **端末での自由入力**（`csrc/line.c` 369 B）— かなでも漢字かな交じり文でもよく、
  辞書を持たないビルドは漢字を**喋らずに理由を出す**
- **蒸留の全経路** — 教師ラベル生成 → 4 段の学習 → 評価（SCOREQ / UTMOS / DNSMOS /
  かな CER / 音素クラス別スペクトル平坦度）

## 測ってわかっていること

すべて手元の M5Stack CoreS3（W8A8 + PIE。ESP32-S3 では既定）での実測。

（冒頭の表に無いものだけ。品質・速度・メモリはそちらを見ること）

| 軸 | 値 |
|---|---|
| **アクセント** | ミニマルペア 37 ペアで教師との**符号一致 37/37** |
| **漢字 G2P** | 5.51〜66.30 ms（入力 15〜84 B）。MeCab と **1,977/1,977 文一致** |
| **かな経路と漢字経路** | 同じ文を**どちらで書いても PCM が bit 一致**する（端末・ホストとも） |
| **アンダーラン** | **0**（全文）。鳴らし始めまで **384 ms** |

**速度は作り直して要件（RTF ≤ 0.5）に届いた。** 最初の実測は 0.926 で、
1 step の内訳を取ると **MAC は 3 割**しかなく、活性化の量子化・GELU・毎 step 102 回の
テンソル検索・重みのコピー 489 KB/step が残りを占めていた。それを削って 1 step を
18.38 M → **11.66 M cyc** にした。**波形は 1 bit も変わっていない**（checksum が同一）。
経緯は [`docs/README.md`](docs/README.md) の年表に。

## 公式実装との関係

公式実装 [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS) は存在する（**GPL-3.0**）。
本リポジトリは MIT で、**そのソースコードを参照せずに**論文本文の数値と piper-plus の実装から
独立に書いた。公式実装は英語・ネパール語・ヒンディー語・ベトナム語・インドネシア語・中国語に
対応しており、**日本語は含まれていない**。

公式リポジトリの**公開ドキュメントに記載された実測値**（ESP32-S3 で 0.22× 実時間など）は、
本リポジトリの実測値との突き合わせに使っている。**コードは参照していない**
（この線引きは機械的に強制している。[`CONTRIBUTING.md`](CONTRIBUTING.md) の 4 番）。

## ドキュメント

索引は [`docs/README.md`](docs/README.md)。**数値が食い違ったら
[`docs/measurements.md`](docs/measurements.md) が正**（全項目に再現コマンド付き）。
決定と訂正の履歴は [`docs/decisions.md`](docs/decisions.md)、
モデルの中身と既知の制約は [`MODEL_CARD.md`](MODEL_CARD.md)、
実機で動かす手順は [`esp32/TESTING.md`](esp32/TESTING.md)。

## 貢献

[`CONTRIBUTING.md`](CONTRIBUTING.md) を読んでください。
**一番ありがたいのは聴いた感想**、次が別の ESP32-S3 での速度実測です。

> 🙏 **聴いた感想にはボードが要りません**
> （[`saanotts-jp-v3-samples.zip`](https://github.com/ayutaz/sanoTTS-jp/releases/latest) を再生するだけ）。
> **「変な音がする」の一言が、n=24 の数字より情報量が多いことがあります。**

**既知の制約と、それをどう測ったかは [`MODEL_CARD.md`](MODEL_CARD.md) §4** にまとめてあります。

⚠️ このリポジトリは **AI エージェント（Claude Code）が大半を書いている。**
そのための規律（推測を数値として書かない / 訂正履歴を消さない /
ゲートには陽性対照を付ける）を `CONTRIBUTING.md` と `CLAUDE.md` に明文化してある。

## ライセンス

⚠️ **コードとモデルでライセンスが違う。**

| 対象 | ライセンス |
|---|---|
| このリポジトリの**コードとドキュメント** | [MIT](LICENSE) |
| **配布されるモデルの重み**（[Releases](https://github.com/ayutaz/sanoTTS-jp/releases)） | **[`LICENSE-MODEL.md`](LICENSE-MODEL.md)** — MIT **ではない** |

重みは つくよみちゃんコーパスを素材に含む教師からの蒸留物で、そのコーパスの条件が

- **帰属表示を必須**とし、**出力の用途に禁止事項**を課し、**義務が下流に伝播する**

ため、MIT を名乗ることができない。使う前に
[`LICENSE-MODEL.md`](LICENSE-MODEL.md) と [`MODEL_CARD.md`](MODEL_CARD.md) を読むこと。

⚠️ **2026-09-09 に必須の帰属表示を直した**（[`docs/decisions.md`](docs/decisions.md) C-073）。
教師 base の **LibriTTS-R / CML-TTS（CC-BY-4.0）と AISHELL-3（Apache-2.0）が
抜けていた**ので足し、帰属を要求しない素材（MOE-Speech / CC0 / PD）は
**任意ブロック (B)** に移した。**再配布するなら (A) を丸ごと写すこと。**
⚠️ **「❌ アダルト用途」も誤りだった**（C-072）。禁止は
「**刺激の強い表現をゾーニングなしで公開すること**」で、
提供元は適切なゾーニングがあれば成人向け表現を制限していない。

**コーパス本文は配布しない。** 素材ごとの一次ソースは [`NOTICE.md`](NOTICE.md)。
