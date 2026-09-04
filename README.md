# sanoTTS-jp

***日本語** · [English](README.en.md)*

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-ブラウザで試す-brightgreen.svg)](https://ayutaz.github.io/sanoTTS-jp/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Model license](https://img.shields.io/badge/model-not%20MIT-orange.svg)](LICENSE-MODEL.md)

**559 K パラメータの日本語 TTS を、$3 のマイコン（ESP32-S3）で動かす試み。**

### 🔊 ブラウザで試す → **<https://ayutaz.github.io/sanoTTS-jp/>**

インストール不要。**漢字かな交じり文をそのまま打つと喋る**。
⚠️ **マイコンに載っているのと同じ C99 コード**を WebAssembly にしたもので（arena も同じ 180,224 B）、
piper-plus の WASM の置き換えではなく**実機のコードを触れる形にした入口**（[D-050](docs/decisions.md#d-050)）。
⚠️ **初回に辞書 5.5 MB を落とす**（gzip）。速度は Chrome で **0.008〜0.019 ×RT**（[M-95](docs/measurements.md#m-95)）。音は**両レーンとも聴いてもらって「問題なかった」/ 途切れ無し**（[M-96](docs/measurements.md#m-96)。⚠️ **1 名・対照なし・盲検なし**）。⚠️ **モバイルと Safari は未測定**。

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
saanotts: プリロール 4 チャンク完了（初回 pull 244.66 ms / 鳴らし始めまで 384 ms）
  ...
saanotts: 定常 xRT = 0.446（満チャンク pull の中央値 / 92.88 ms）
saanotts: アンダーラン 0 / 14 チャンク
saanotts: 出力 PCM: 27136 sample / FNV-1a 0xa69a7ebbb5ccb05f
```

*（実機の生ログ [`reports/m90_cores3/device_m5_kanji.log`](reports/m90_cores3/device_m5_kanji.log) から、
タイムスタンプと注記を省いて抜粋。`...` は 14 回の pull）*

| | |
|---|---:|
| モデル | **559 K params** / int8 で **654,032 B**（flash） |
| 実行時 RAM | **157 KB**（ESP32-S3 の SRAM 512 KB の 34%） |
| 速度 | **xRT 0.446**（満チャンク 1 pull。⚠️ 発話全体では 0.54〜0.71） |
| 品質 | 教師の **64%**（SCOREQ 比 0.644）。⚠️ **予測器のスコアで、人の耳ではない** |
| 端末の G2P | 漢字あり **13.7 MB 辞書** / かなだけなら **877 B のテーブル** |

⚠️ **これは検証 (PoC) であって製品ではない。** いちばん足りていないのは
**人が聴いた評価**で、いまのところ 1 名・対照なし・盲検なしが 1 回あるだけ。

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

## はじめかた

**入口は 5 つ。A / B / D / E は piper-plus も教師モデルも要らない**（新規 clone で実測）。

| | やりたいこと | 要るもの | 所要 |
|---|---|---|---|
| **A** | **音を聴く** | [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` だけ | 1 分 |
| **B** | **好きな文を合成する** | + 最小セットアップ + `saanotts-jp-v3-stage4.pt` | 10 分 |
| **C** | **ESP32-S3 で喋らせる** | ボード（DAC は任意）。**焼くだけなら ESP-IDF は不要** | 15〜30 分 |
| **D** | **コードのゲートを回す** | 最小セットアップだけ | 5 分 |
| **E** | **ブラウザで試す** | ブラウザだけ。**インストール不要** | 1 分 |

### 最小セットアップ（B / D）

⚠️ **`uv sync` は使わない。** `pyproject.toml` の `[tool.uv.sources]` が
piper-plus への**絶対パス**を指しているので、持っていない人は
`error: Distribution not found at: file://...` で止まる（実測）。
生徒の推論に要るのは **torch / numpy / soundfile の 3 つだけ**なので、
プロジェクトを経由しない venv を作る:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

### B. 好きな文を合成する

[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) から
`saanotts-jp-v3-stage4.pt`（2.7 MB）を落として、**かな中間表現**を渡す。

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v3-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav（22.05 kHz / 1.2 秒）「今日は良い天気ですね。」
```

```
[ アクセント上昇 / ] 下降核 / # 句境界 / ° 無声化
```

**漢字文をそのまま渡すこともできる**（下のフルセットアップが要る。OpenJTalk で
漢字→かなを行うため）:

```bash
uv run python scripts/synthesize_student.py --ckpt saanotts-jp-v3-stage4.pt \
    --text "今日は良い天気ですね。" --out out/
```

⚠️ **どちらの経路でも WAV はバイト単位で一致する**（M-92 で実測。held-out 300 文で
生徒インデックスも 300/300 一致）。違いは**漢字→かなに OpenJTalk が要るかどうかだけ**。

| 書きかた | 要るもの |
|---|---|
| `--intermediate "きょ][おわよ…"` | **最小セットアップだけ**（torch / numpy / soundfile） |
| `--text "今日は良い天気ですね。"` | + **フルセットアップ**（piper-plus = OpenJTalk） |

⚠️ **端末（`-DSAAN_KANJI=1`）はこの制約を受けない。** 辞書を載せた板は漢字文を
そのまま受ける。ホストで OpenJTalk が要るのは、**端末より広いフル辞書**を使うため
（端末の枝刈り辞書とは**音素の 0.63% が違う**。n=1,495。M-99 §4）。
⚠️ **n を必ず添えること。** 同じ量が n=298 では 0.32% に見える（C-059）。

⚠️ **モデルの重みは MIT ではない。** 使う前に [`LICENSE-MODEL.md`](LICENSE-MODEL.md) を読むこと。

### C. ESP32-S3 で喋らせる

手順は [`esp32/TESTING.md`](esp32/TESTING.md)。焼いたあとシリアルで:

```
かな> きょ][おわよ][いて][んきです°ね        ← かな中間表現
かな> 今日は良い天気ですね。                  ← 漢字版のビルドなら、そのまま打つ
```

**入力に印を付ける必要はない。** 端末が行を見て**かな / 辞書 / 拒否の 3 値**を決める
（`saan_g2p_classify()`）: 凍結テーブルのトークナイザが行末まで通れば**かな経路**、
通らず中間表現の記号（`[ ] # ° _ ^ $`）が無ければ**辞書経路**、通らないのに記号が
混じっていれば**拒否して喋らない**（「中間表現 + `。`」がそれらしい音で通るのを防ぐため）。
判定はホスト側 `scripts/kana_g2p.py` と同じ規則で、一致は `make -C csrc kb-parity` が
**596/596** で検査する。

**ファームは 3 通り。** コンソールが **UART0 の版と USB Serial/JTAG の版**があり、
CoreS3 / AtomS3 のような **native USB だけの板は `-usbjtag` の方**を焼く。

| | 焼くもの | 受け付ける入力 | flash |
|---|---|---|---|
| かな | `esp32s3-firmware-w8a8-pie.bin` / `…-usbjtag.bin` | かな中間表現のみ | 8 MB 以上 |
| **漢字** | `esp32s3-firmware-kanji-16mb.bin` / `…-usbjtag.bin` | **漢字かな交じり文**も | **16 MB 必須** |
| **M5 CoreS3** | `m5-cores3-firmware-kanji-16mb.bin` | 同上。**内蔵スピーカーで鳴る** | **16 MB 必須** |

⚠️ **v0.2.0 以前のイメージは `!` の前置が要り、入力が UART0**。必ず入れ替えること。

⚠️ **「16 MB 必須」は配布イメージの話。** **8 MB の板でもソースからなら漢字が動く**
（2026-09-05 に実機で確認。[M-105](docs/measurements.md#m-105)）。
**配布はしていない**ので、自分でビルドすることになる:

| | 表 | entries | **音素の誤り**（n=1,495） |
|---|---|---:|---:|
| **16 MB**（配布イメージ） | `partitions_16mb.csv` | 438,750 | **0.63%** |
| 8 MB / DevKit | `partitions_8mb_kanji.csv` | 228,000 | 1.01% |
| 8 MB / **M5Stack 系** | `boards/m5unified/partitions_8mb.csv` | 213,000 | **1.09%** |

手順は [`esp32/README.md`](esp32/README.md) の「8 MB flash の板」。
⚠️ **読みが落ちる**（枝刈りを深くするため）。⚠️ **音を人が聴いていない。**

**音の出口は 2 通り。**

| 板 | どう焼くか | 音の出口 |
|---|---|---|
| ESP32-S3 DevKit / AtomS3 + I2S DAC | 上のイメージを焼く | 外付け DAC（配線が要る。⚠️ `saan_i2s` は**実機未検証**） |
| **M5Stack CoreS3 / Core2 / Basic**（スタックチャンの中身） | 配布イメージ、または[ソースから](esp32/boards/m5unified/README.md) | 内蔵スピーカー。画面に文が出て、タッチで再生 |

### D. コードのゲートを回す

```bash
make -C csrc line                                       # 端末の行編集（陽性対照つき）
make -C csrc fft                                        # 逆 FFT（naive DFT の 1,435 倍）
make -C csrc g2p PYTHON="uv run --no-project python"    # オンデバイス G2P（2,819 ベクタ）
make -C csrc erf                                        # GELU の erf 近似 vs libm（陽性対照つき）
make -C csrc range                                      # 出力範囲つきカーネルが全域版と bit 一致
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata（fp32 拒否の陽性対照）
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **`PYTHON=...` を省くと `uv run python` になり、piper-plus を要求する。**
⚠️ **`make -C csrc all-test` は通らない** — golden との突き合わせに `csrc/*.bin`
（重みの書き出し）が要る。落とした `.pt` から `scripts/export_c_weights.py` で書き出せば通る。

### E. ブラウザで試す

**<https://ayutaz.github.io/sanoTTS-jp/>** — この C99 コアをそのまま WebAssembly にしたデモ。
配っているのは [`pages.yml`](.github/workflows/pages.yml)（`main` への push で走り、
重みと辞書は**リリース v0.3.0 からタグ固定で落として SHA-256 を照合**している）。

**インストールも設定も要らず**、入力欄に
`今日は良い天気ですね。` と打つだけで鳴る。**漢字・カタカナ・ひらがな**をそのまま受ける
（`!` のような印は要らない。経路は C 側の `saan_g2p_classify()` が決める）。

- **ESP32 と同じコードが動く。** `csrc/` の C99 と `esp32/main/saan_kanji.c` を書き換えずに
  wasm にしただけで、**arena も実機と同じ 180,224 B**（→ [D-050](docs/decisions.md#d-050)）
- 初回は**辞書 13,702,320 B（gzip -9 で 5,476,122 B）**を落とす。⚠️ 回線が細いと待たされる
- ⚠️ **ブラウザでは 1 種類も速度を測っていない**（測ったのは node だけ。[M-94](docs/measurements.md#m-94)）
- 音は **W8A32 / W8A8 の両方を聴いてもらい「問題なかった」/ 途切れ無し**（[M-96](docs/measurements.md#m-96)）。⚠️ **1 名・対照なし・盲検なし**。
  ⚠️ かつてここに書いていた「`AudioContext` のリサンプルが挟まる」は、**要求どおり 22,050 Hz が返る**ことが分かった（M-95 §3）ので前提が変わった。ただし
  **鳴っている音は checksum と一致しない**
- ⚠️ **成果物は今も ESP32。** Web は入口であって、このプロジェクトの目的ではない（[D-007](docs/decisions.md#d-007)）

手元で動かすなら（emcc が要る）。⚠️ **`index.html` と同じ階層に全部を平らに並べる**
（`.github/workflows/pages.yml` が CI でやっているのと同じ形）:

```bash
bash web/build.sh                                   # → web/dist/*.wasm と *.mjs
mkdir -p /tmp/saan-site
cp web/index.html web/main.js web/dist/*.mjs web/dist/*.wasm /tmp/saan-site/
cp csrc/student_i8.bin /tmp/saan-site/              # = リリースの saanotts-jp-v3-int8.bin
gzip -9 -c csrc/k1_dict.bin > /tmp/saan-site/k1_dict.bin.gz   # = k1-dict-438750.bin

# ⚠️ **ここまでで止めると footer の 4 本が全部 404 になる**（実測。音は鳴るので、
#    リンクを踏むまで気づかない）: NOTICE.txt / NOTICE-openjtalk.txt /
#    NOTICE-dictionary.txt / LICENSE-MODEL.md
cp LICENSE-MODEL.md /tmp/saan-site/                 # リポジトリのものでよい
#   （リリース資産 LICENSE-MODEL.md と SHA-256 が一致する。実測で確認済み）
# ⚠️ NOTICE*.txt 3 本は**リポジトリにその名前では無い**ので、リリースから落とす。
#    ⚠️ **ネットワークが要る**（CI の pages.yml も同じ 3 本を落としている）
gh release download v0.3.0 -R ayutaz/sanoTTS-jp -D /tmp/saan-site --clobber \
    -p 'NOTICE.txt' -p 'NOTICE-openjtalk.txt' -p 'NOTICE-dictionary.txt'

uv run --no-project python -m http.server -d /tmp/saan-site 8000
#   ⚠️ `python3 -m http.server` は hook が止める（D-012）
```

### フルセットアップ（漢字→かな変換 / 学習 / ラベル生成）

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ uv sync の前
uv sync
```

⚠️ **教師 checkpoint（private）はこれでも入らない。** 要るのは
**ラベル生成と学習をやり直すときだけ**で、漢字→かな変換は piper-plus のソースだけで動く。

## ダウンロード

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

## わかっていないこと

**これが README でいちばん大事な節。** 数字が並んでいても、次のことは確かめていない。

| | |
|---|---|
| **音の良し悪し** | ⚠️ **人が聴いた評価は 2 回だけで、どちらも 1 名・対照なし・盲検なし**（M-91 / M-93）。「破綻していない」までしか言えない。品質の数字は全部 SCOREQ / UTMOS / DNSMOS という**予測器**で、日本語では較正されていない（**実人間の音声ですら SCOREQ 2.50 / UTMOS 2.30** しか出ない）。だから **「教師比 0.644」は「教師の音を 100 として 64」ではなく、較正されていない予測器のスコアの比**（n=24）。**絶対値を英語の論文と比べてはいけない** |
| **発話全体の実時間性** | ⚠️ 満チャンク 1 pull は 0.446 だが、warmup 38 フレームが初回 pull に乗るので**発話全体では 0.54〜0.71**。要件の分母がどちらかは決めていない |
| **辞書の枝刈りの代償** | ⚠️ ホストと違う音素が 0.32% ある（n=298。M-77）。落ちた語は無音にならず、**短く切り直されて誤読される**（`上毛` → `上` + `毛`） |
| **DevKit の I2S 出力** | ⚠️ `saan_i2s`（I2S 直叩き）は**実機未検証**。音が出ているのは M5Unified 経路だけ |
| **他の板** | ⚠️ 自分で測ったのは **CoreS3 1 枚だけ**。第三者による独立した実測が 2 件あるが（[AtomS3 1.718](https://github.com/magatsux2019/sanotts-atoms3-results) / [CoreS3 1.558](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)）、**どちらも高速化前のコード**で、こちらでは未再現 |
| **公式実装との差** | ⚠️ 同じ ESP32-S3 で公式実装が申告する **0.22× 実時間**には届いていない |

制約の全部と、それをどう測ったかは [`MODEL_CARD.md`](MODEL_CARD.md) §4 に。

> 🙏 **いちばんありがたい貢献は「聴いた感想」です。** ボードは要りません
> （[`saanotts-jp-v3-samples.zip`](https://github.com/ayutaz/sanoTTS-jp/releases/latest) を再生するだけ）。
> **「変な音がする」の一言が、n=24 の数字より情報量が多いことがあります。**

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

**コーパス本文は配布しない。** 素材ごとの一次ソースは [`NOTICE.md`](NOTICE.md)。
