# sanoTTS-jp

**567 K パラメータの日本語 TTS を ESP32-S3 で動かす**試み。
[arXiv:2608.21378](https://arxiv.org/abs/2608.21378) "sanoTTS" の蒸留レシピを日本語に適用し、
[piper-plus](https://github.com/ayutaz/piper-plus) (MB-iSTFT-VITS2) を教師として蒸留する。

⚠️ **これは論文の再現実装であり、著者らの実装ではない。**

公式実装 [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS) は存在する（**GPL-3.0**）。
本リポジトリは**そのソースコードを参照せずに**、論文本文の数値と
piper-plus の実装から独立に書いたもの（MIT）。
公式実装は英語・ネパール語・ヒンディー語・ベトナム語・インドネシア語・中国語に対応しており、
**日本語は含まれていない**。

公式リポジトリの**公開ドキュメントに記載された実測値**（ESP32-S3 で 0.22× RT など）は、
本リポジトリの外挿値との突き合わせに使っている。**コードは参照していない。**

⚠️ **検証 (PoC) であり、モデルの重みは配布していない**（理由は [`NOTICE.md`](NOTICE.md)）。

## 現在地

| 軸 | 状態 |
|---|---|
| **品質** | SCOREQ **教師比 0.611**（論文の英語 embedded 比 0.5427 を上回る）。⚠️ n=24 |
| **メモリ** | **196.9 KB** / ESP32-S3 の SRAM 512 KB の 38% |
| **速度** | ⚠️ 手元 0.023× RT。**fp32 のまま移植すると 2.47× RT で実時間に間に合わない** |

**残るのは速度だけ。** ESP32-S3 の PIE（SIMD）を使った int8 カーネルが要る。
実機はまだ動かしていない。

## 何ができているか

```
テキスト ──[ホスト・OpenJTalk]──▶ かな中間表現 ──[mora テーブル 1,786 B]──▶ 音素ID
   ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
```

- **C99 推論コア**（`csrc/`）— 依存は libm のみ。malloc を呼ばず arena を使う。
  ストリーミング版は**一括版と bit 完全一致**（27,136 sample）
- **蒸留の全経路** — 教師ラベル生成 → 4 段の学習 → 評価（SCOREQ / UTMOS / CER /
  音素クラス別スペクトル平坦度）
- **オンデバイス G2P** — 端末側は mora テーブル **1,786 B** のみ。
  102 MB の辞書を積む路線は実測で不成立と判定した

## セットアップ

```bash
# 1. 教師（piper-plus）を取得
git clone https://github.com/ayutaz/piper-plus.git
export PIPER_PLUS_ROOT=$PWD/piper-plus

# 2. pyproject.toml の path 依存を自分の環境に向ける
#    ⚠️ [tool.uv.sources] の path は ~ を展開しないので、ここだけ絶対パスが要る
python3 deploy/retarget_sources.py --root $PIPER_PLUS_ROOT

# 3. 依存を入れる（Python は必ず uv 経由）
uv sync --extra eval
```

⚠️ **教師 checkpoint は private リポジトリ**（`ayousanz/piper-plus-zero-shot-tsukuyomi`）
にあり、このリポジトリからは取得できない。ラベル生成を再現するにはアクセス権が要る。

## 動かす

```bash
# 健全性チェック（教師なしで通るもの）
uv run python scripts/test_losses.py          # 損失の性質（26 項目）
uv run python scripts/test_discriminator.py   # 判別器（23 チェック）
uv run python .claude/hooks/test_guard_bash.py

# C99 推論コア（重みが要る）
uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt
make -C csrc all-test     # golden test + ストリーミングの受け入れ条件 + FFT + int8
make -C csrc run-bench    # レイテンシ（段別の内訳）
```

## ドキュメント

**数値が食い違ったら [`docs/measurements.md`](docs/measurements.md) が正。**

| | |
|---|---|
| [`docs/README.md`](docs/README.md) | 索引と現在地 |
| [`docs/measurements.md`](docs/measurements.md) | **実測値の一次ソース** M-1〜M-48。全項目に再現コマンド付き |
| [`docs/decisions.md`](docs/decisions.md) | 決定 D-001〜D-029 と**訂正履歴 C-001〜C-025** |
| [`docs/plan/`](docs/plan/) | 作業計画 |
| [`CLAUDE.md`](CLAUDE.md) | 実装時の要点（AI エージェント向けの運用ルールでもある） |

**訂正履歴を残しているのは意図的**。「1 コマンド打てば分かることを打たずに推論した」
種類の誤りが 23 件記録されている。同じ間違いを繰り返さないための資料。

## このリポジトリの進め方

- **推測を数値として書かない。** 測っていないことは「未測定」と書く
- **決着したリスクも消さない。** 消すと同じ疑問が再燃する
- **n が小さいときは n と CI を必ず併記する**（n=3 の差を結論にして反証されたことがある）

## ライセンス

コードとドキュメントは [MIT](LICENSE)。
データと教師モデルの扱いは [`NOTICE.md`](NOTICE.md) を参照。
