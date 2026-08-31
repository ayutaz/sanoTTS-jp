# sanoTTS-jp

***日本語** · [English](README.en.md)*

**559 K パラメータの日本語 TTS を、$3 のマイコン（ESP32-S3）で動かす試み。**

[arXiv:2608.21378](https://arxiv.org/abs/2608.21378) "sanoTTS" の蒸留レシピを日本語に適用し、
[piper-plus](https://github.com/ayutaz/piper-plus)（MB-iSTFT-VITS2）を教師として、
Duration / Acoustic / iSTFT Decoder の 3 つの小さな生徒に蒸留する。

⚠️ **実機ではまだ動いていない。** 品質とメモリは目標に届いたが、
**速度を一度も測っていない** — ESP32-S3 の実機が手元に無いため（[現在地](#現在地)）。
toolchain（ESP-IDF v5.5）と QEMU は導入済みで、ビルドと bit 一致検証までは通っている。

## 日本語でやると何が難しいか

英語版をそのまま移すと壊れる。この 3 つが日本語固有の壁だった。

| 壁 | どう解いたか |
|---|---|
| **G2P が載らない** — 漢字を読むには辞書が要る。NAIST-JDIC は実測 **102 MB** | **入力仕様を変えた。** 端末は「ひらがな + アクセント記号」だけを受け取り、**877 B のテーブル**で音素に変換する。漢字→かなはホスト側でオフラインに行う。⚠️ **後にこの前提を測り直したら崩れた** — 辞書を TTS 専用の形式にすると 1 エントリ 130 B → **28 B** になり、16 MB ボードに 37 万語が載る（[K-1 調査](docs/research/k1-kanji-katakana-ondevice.md)）。**まだ出荷していない** |
| **ピッチアクセント** — 「箸／橋／端」は音素列が同じで高低だけが違う。集約スコアでは検出できない | ミニマルペア 15 群を評価セットに入れた。教師との**符号一致 37/37** |
| **無声化母音** — 「です」「した」の `i` `u` が音響的にほぼ摩擦雑音になる | 音素クラス別スペクトル平坦度で分離を確認（AUC 0.847）してから、ノイズ注入の対象集合に入れた |

**一番効いたのは 1 つ目**で、これは辞書を小さくしたのではなく**問題の切り分け方を変えた**もの。
論文にも公式実装にも対応物が無い。

## 現在地

| 軸 | 状態 |
|---|---|
| **品質** | 教師の **64%**（SCOREQ 比 0.644）。論文の英語版が報告する比 0.5427 を上回る |
| **アクセント** | ミニマルペア 37 ペアで教師との符号一致 **37/37** |
| **メモリ** | **197 KB** — ESP32-S3 の SRAM 512 KB の 38%。重みは int8 で 629 KB（flash） |
| **速度** | ⚠️ **未達。** fp32 のまま移植すると **2.47× 実時間**（間に合わない） |

⚠️ **すべて n=24〜200 の指標による評価で、人が聴いた評価は一度もしていない。**
「教師比 0.644」は「教師の音を 100 としたとき 64」という意味ではなく、
較正されていない予測器のスコアの比。日本語では**実人間の音声ですら SCOREQ 2.50 / UTMOS 2.30**
しか出ないので、**絶対値を英語の論文と比べてはいけない**。

**この経路の残りは実機での速度実測だけ。** ESP32-S3 の PIE（SIMD）を使った int8 カーネルは
**書けていて、QEMU 上で bit 完全一致まで確認済み**（MAC の 99.4%）。
公式実装は同じ ESP32-S3 で **0.22× 実時間**を実測と申告しており、方向は裏づけられている。

**PIE カーネルは書けた** — `ee.vmulas.s8.accx`（16 レーンの int8 積和）で内積を置き換え、
**QEMU 上でスカラ実装と bit 完全一致**することを確認した（MAC の **99.4%** を覆う）。

⚠️ **それでも「速くなった」とは言えない。** QEMU はサイクル精度ではないので、
**速度は一度も測っていない**。0.22× 実時間に届くかは **ESP32-S3 実機でしか分からない**。

**2026-08-30 に QEMU で出荷ファームを起動から合成完了まで通した**（M-62）。
重みの mmap・端末側 G2P・arena・合成・int16 変換まで動き、
**PIE カーネルはスカラ実装と 27,136 sample すべて bit 一致**した（陰性対照つき）。
**残っているのは速度と実機の I2S だけ。**

> 🙏 **ESP32-S3 の実機をお持ちの方へ** — 15〜30 分で決着します。
> 手順は [`esp32/TESTING.md`](esp32/TESTING.md)。**DAC が無くても測れます。**

## はじめかた

**入口は 4 つ。A / B / D は piper-plus も教師モデルも要らない**（新規 clone で実測）。

| | やりたいこと | 要るもの | 所要 |
|---|---|---|---|
| **A** | **音を聴く** | [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` だけ | 1 分 |
| **B** | **好きな文を合成する** | + 最小セットアップ + `saanotts-jp-v3-stage4.pt` | 10 分 |
| **C** | **ESP32-S3 で喋らせる** | + ESP-IDF v5.5 + ボード（DAC は任意） | 15〜30 分 |
| **D** | **コードのゲートを回す** | 最小セットアップだけ | 5 分 |

### 最小セットアップ（B / D）— piper-plus も教師も要らない

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

⚠️ **漢字から中間表現を作るにはフルセットアップが要る**（OpenJTalk）。
かなを直接書けば不要:

```bash
uv run python scripts/to_intermediate.py "電源を入れてください。"   # ← フルセットアップ側
#   → で[んげんおい[れてくださ]い
```

⚠️ `--text "漢字混じり文"` でも合成できるが、**そちらは教師 ckpt（private）が要る**
（音素 ID を教師の `phoneme_id_map` 経由で組むため）。`--intermediate` は教師も
OpenJTalk も呼ばず、**同じ入力に対して WAV がバイト単位で一致する**（M-64 / M-65）。

⚠️ **モデルの重みは MIT ではない。** 使う前に [`LICENSE-MODEL.md`](LICENSE-MODEL.md) を読むこと。

### C. ESP32-S3 で喋らせる

手順は [`esp32/TESTING.md`](esp32/TESTING.md)。焼いたあとシリアルで:

```
かな> きょ][おわよ][いて][んきです°ね
```

⚠️ **端末は漢字を受け付けない。** ⚠️ **速度はまだ誰も測っていない。**

> かつて理由を「辞書が載らないため」と書いていたが、**測り直したら崩れた**。
> 辞書を TTS 専用のバイナリにすると 16 MB ボードに 37 万語が載り、
> C 実装が MeCab と **1,977/1,977 文で一致**（未知語込み）するところまで来ている。
> **アクセント規則も 126 行を C に移植して Python 版と 2,333/2,333 一致**した
> （SudachiDict 217 MB も ONNX も要らないことが実装で裏づけられた）
> （[K-1 調査](docs/research/k1-kanji-katakana-ondevice.md) / [実装計画](docs/plan/k1-kanji-implementation-plan.md)）。
> **ただし実機で一度も動かしておらず、出荷物には入っていない。**

### D. コードのゲートを回す

```bash
make -C csrc line                                       # 端末の行編集（**陽性対照つき**）
make -C csrc fft                                        # 逆 FFT（naive DFT の 1,435 倍）
make -C csrc g2p PYTHON="uv run --no-project python"    # オンデバイス G2P（2,819 ベクタ）
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **`PYTHON=...` を省くと `uv run python` になり、piper-plus を要求する。**
（この区別を付けるまで「piper-plus 無しで通った」と誤って観測した。C-041）

⚠️ **`make -C csrc all-test` は通らない** — golden との突き合わせに `csrc/*.bin`
（重みの書き出し）が要る。落とした `.pt` から
`scripts/export_c_weights.py` で書き出せば通る。

### フルセットアップ（漢字→かな変換 / 学習 / ラベル生成）

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ uv sync の前
uv sync
```

⚠️ **教師 checkpoint（private）はこれでも入らない。** 要るのは
**ラベル生成と学習をやり直すときだけ**で、漢字→かな変換は piper-plus のソースだけで動く。

### まだできないこと

| | 理由 |
|---|---|
| **ラベル生成・学習をやり直す** | 教師 checkpoint が private リポジトリにある |
| **実機での速度** | ⚠️ **ESP32-S3 の実機が無い。** [`esp32/TESTING.md`](esp32/TESTING.md) |

## アーキテクチャ

```
漢字かな交じり文
   │  ホスト側・オフライン（OpenJTalk）
   ▼
かな中間表現   きょ][おわよ][いて][んきです°ね     [ 上昇 / ] 下降核 / # 句境界 / ° 無声化
   │  端末側・877 B のテーブルのみ
   ▼
音素ID ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
            33 K params      195 K            331 K
                        └─ 40 次元の潜在インターフェース（c-line）で接続
```

**3 つの生徒を明示的な潜在インターフェースで繋ぐのが要点。** これを省いて
テキスト→波形を 1 本のネットで学ぶと、論文の対照実験では訓練文を丸暗記して
未知の文が読めなくなる。

- **C99 推論コア**（`csrc/`）— 依存は libm のみ。`malloc` を呼ばず arena を使う。
  ストリーミング版は一括版と **bit 完全一致**（27,136 sample）
- **オンデバイス G2P**（`csrc/g2p.c`）— テーブル **877 B** / コード 1,549 B（ESP32-S3 実測）/ **作業メモリ 0 B**
- **端末での自由入力** — シリアルに かな中間表現を 1 行打つと喋る（`csrc/line.c` 369 B）。
  漢字混じり文からは `uv run python scripts/to_intermediate.py "文"`（ホスト）
- **蒸留の全経路** — 教師ラベル生成 → 4 段の学習 → 評価（SCOREQ / UTMOS / DNSMOS /
  かな CER / 音素クラス別スペクトル平坦度）

## 他のプロジェクトで使えそうなもの

- **`csrc/`** — 依存なしの C99 推論コア。MCU に載る TTS のデコーダとして単体で読める
- **`csrc/g2p.c` + `scripts/kana_g2p.py`** — かな中間表現の設計と C 実装。
  **日本語 TTS を組み込みに載せるときの G2P 問題への 1 つの答え**
- **`csrc/line.c`** — 369 B の UTF-8 対応行編集。マイコンのシリアルで日本語を打たせるなら、
  **矢印キーが記号を挿入する / BS が UTF-8 を割る**の 2 つは必ず踏む
- **`docs/measurements.md`** — 全項目に再現コマンドを付けた実測記録。
  ESP32 のメモリ収支、esp-dsp のサイクル数からの外挿、日本語での MOS 予測器の較正など

## 公式実装との関係

公式実装 [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS) は存在する（**GPL-3.0**）。
本リポジトリは MIT で、**そのソースコードを参照せずに**論文本文の数値と piper-plus の実装から
独立に書いた。公式実装は英語・ネパール語・ヒンディー語・ベトナム語・インドネシア語・中国語に
対応しており、**日本語は含まれていない**。

公式リポジトリの**公開ドキュメントに記載された実測値**（ESP32-S3 で 0.22× 実時間など）は、
本リポジトリの外挿値との突き合わせに使っている。**コードは参照していない**
（この線引きは `docs/decisions.md` の D-032 として凍結し、hook で機械的に強制している）。

## ドキュメント

**数値が食い違ったら [`docs/measurements.md`](docs/measurements.md) が正。**

| | |
|---|---|
| [`docs/README.md`](docs/README.md) | 索引と現在地 |
| [`docs/measurements.md`](docs/measurements.md) | **実測値の一次ソース** M-1〜M-75。全項目に再現コマンド付き |
| [`docs/decisions.md`](docs/decisions.md) | 決定 D-001〜D-043 と**訂正履歴 C-001〜C-051** |
| [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) | 公式実装から得た事実（⚠️ すべて上流の申告値・未再現） |
| [`docs/plan/`](docs/plan/) | 作業計画と残りのタスク |
| [`docs/release-notes/`](docs/release-notes/) | 各リリースで何が変わったか（**訂正も残してある**） |
| [`esp32/README.md`](esp32/README.md) | ESP32-S3 のビルドと設計判断 |
| [`esp32/TESTING.md`](esp32/TESTING.md) | **実機で動かす手順**（焼き方・喋らせ方・報告してほしい 4 行） |
| [`MODEL_CARD.md`](MODEL_CARD.md) | モデルの中身・評価・既知の制約 |
| [`CLAUDE.md`](CLAUDE.md) | 実装時の要点。AI エージェント向けの運用ルールでもある |

## このリポジトリの進め方

**このプロジェクトは AI エージェント（Claude Code）が大半を書いている。**
そのための規律をリポジトリ内に明文化してある。

- **推測を数値として書かない。** 測っていないことは「未測定」と書く
- **訂正履歴を消さない。** 「1 コマンド打てば分かることを、打たずに推論した」種類の誤りが
  **51 件**記録してある。同じ間違いを繰り返さないための資料
- **決着したリスクも消さない。** 消すと同じ疑問が再燃する
- **n が小さいときは n と CI を必ず併記する**（n=3 の差を結論にして反証されたことがある）
- **ゲートは「落ちる壊し方」を言えないと書かない。** テストが緑のまま欠陥が潜んでいた例が
  6 件あり、`.claude/skills/writing-gates/` にまとめてある

`.claude/hooks/guard_bash.py` が、教師リポジトリへの書き込み・`pip install`・
本番ラベルパックの破棄・GPL ソースの取得・コーパス本文を含むコミットを機械的に止める
（回帰 83 ケース）。

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
