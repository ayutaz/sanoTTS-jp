# 貢献のしかた

*[English below](#contributing-english)*

**このリポジトリは検証 (PoC) で、製品ではありません。** それでも歓迎したいものが
はっきりしているので、先に書いておきます。

## 一番ありがたいもの

| # | 何 | ボードが要るか |
|---|---|---|
| **1** | **ESP32-S3 実機での速度実測**（`定常 xRT`） | ✅ 要る |
| **2** | **音を聴いた感想** — [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` | ❌ 不要 |
| **3** | 手順書をなぞって**詰まった場所**の報告 | ❌ 不要 |

**1 の手順は [`esp32/TESTING.md`](esp32/TESTING.md)。所要 15〜30 分・DAC は不要です。**

⚠️ **2 は本当に足りていません。** 品質の数字はすべて予測器（SCOREQ / UTMOS /
DNSMOS）で、**人が聴いた評価は 1 名 1 回しかありません**。日本語では
これらの予測器が較正されていない（[`docs/decisions.md`](docs/decisions.md) D-013 / D-020）ので、
**「変な音がする」の一言が、n=24 の数字より情報量が多いことがあります。**

## 守ってほしい 4 つ

このリポジトリには過去の事故から作った規律があります。
`.claude/hooks/guard_bash.py` が機械的に止めるものもあります。

### 1. 推測を数値として書かない

**測っていないことは「未測定」と書く。** [`docs/measurements.md`](docs/measurements.md) の
M-番号は**全部に再現コマンドが付いています**。数値を足すならそこに追記し、
`measured` か `estimated` かを明示してください。

n が小さいときは **n と信頼区間を数値の隣に**書いてください。

### 2. 訂正履歴を消さない

[`docs/decisions.md`](docs/decisions.md) の C-001〜C-053 は
**「1 コマンド打てば分かることを、打たずに推論した」種類の誤り**の記録です。
古い記述を直すときは、**上書きではなく C-番号として残して**ください。

### 3. ゲートは「落ちる壊し方」を言えないと書かない

**緑のまま欠陥が潜んでいた例が 12 件あります**（`.claude/skills/writing-gates/`）。
新しいテストを足すなら:

- **陽性対照**（必ず落ちるはずの入力）を一緒に入れる
- パターンで実ファイルを検査するなら **何件一致したかを出す**（0 件一致は合格ではない）
- **実ファイルを 1 つわざと壊して、落ちることを確認する**

### 4. 公式実装 `Ampixa/sanoTTS` のソースコードを読まない・持ち込まない

公式実装は **GPL-3.0** で、本リポジトリは MIT です。
**ソースを参照すると MIT のまま配布できなくなります**（`docs/decisions.md` D-032）。

- ✅ **数値・ハイパーパラメータ・アーキテクチャ構成の参照は可**（著作権の対象外）
- ✅ **公開ドキュメントの取得も可**。得た事実は [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) に
  **上流の申告値として**書く（`docs/measurements.md` の M-番号と混ぜない）
- ❌ **ソースファイルの取得・閲覧・書き写しは不可**

## 開発環境

```bash
# Python は必ず uv 経由。⚠️ pip install は使わない
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"   # 最小
uv sync                                                                # フル（piper-plus が要る）
```

`~/Documents/piper-plus`（教師）は**読み取り専用**です。checkout / commit / 編集は
hook が止めます。

## PR を出す前に通すもの

```bash
uv run python scripts/check_doc_counters.py         # 索引の M/D/C 番号・件数・引用アンカー
uv run python scripts/check_doc_links.py            # md の相対リンクが実在するか
uv run python .claude/hooks/test_guard_bash.py      # hook の回帰（94 ケース）
uv run python scripts/test_sanitize_reports.py      # レポートに本文が混じっていないか
uv run python scripts/test_blob_to_header.py        # blob → .rodata ヘッダ（fp32 拒否の陽性対照）
make -C csrc line && make -C csrc fft && make -C csrc erf   # C コアの軽いゲート（erf = GELU の近似）
```

**ドキュメントだけ直す PR でも上の 2 本は通してください。**
番号と件数は書いた瞬間から古くなります（C-042 / C-052）。

ℹ️ **これらは CI でも回ります**（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）。
CI に入れてあるもの・入れていないものと**その理由**は
[`.github/workflows/README.md`](.github/workflows/README.md)。
⚠️ **「CI が緑」は「正しい」ではありません** — 品質・速度・音は 1 つも見ていません。

⚠️ **コーパス本文をコミットしないでください。** 素材のライセンス上、
本文は再配布しません。staged なコーパス本文を含む `git commit` は hook が止めます。

## Issue

- **実機で動かした報告**は歓迎します。落ちても、落ちた場所が情報です
- **「ここが分からない」**も歓迎します。手順書の欠陥は実際に 2 回見つかっています（C-040 / C-041）
- ⚠️ **モデルの重みは MIT ではありません**。使う前に
  [`LICENSE-MODEL.md`](LICENSE-MODEL.md) と [`MODEL_CARD.md`](MODEL_CARD.md) を読んでください

---

# Contributing (English)

**This repository is a proof of concept, not a product.** The documentation is in
Japanese, but issues and PRs in English are fine.

## What would help most

| # | What | Board needed? |
|---|---|---|
| **1** | **Measured speed on real ESP32-S3 hardware** (steady-state xRT) | ✅ Yes |
| **2** | **What it sounds like to you** — `saanotts-jp-v3-samples.zip` in [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | ❌ No |
| **3** | Where the instructions broke for you | ❌ No |

Instructions for 1: [`esp32/TESTING.md`](esp32/TESTING.md) — 15–30 minutes, no DAC required.

⚠️ **2 is genuinely missing.** Every quality number here comes from a predictor
(SCOREQ / UTMOS / DNSMOS), and **exactly one person has listened, once**. None of those
predictors is calibrated for Japanese, so **"it sounds wrong" can carry more information
than an n=24 score.**

## Four rules

1. **Never write a guess as a number.** If it was not measured, say "not measured".
   Every entry in [`docs/measurements.md`](docs/measurements.md) carries a reproduction
   command; add yours the same way, and report n with a confidence interval when n is small.
2. **Never delete the correction log.** C-001–C-053 in
   [`docs/decisions.md`](docs/decisions.md) record errors of the form "one command would
   have answered this". Correct by appending a new C entry, not by overwriting.
3. **Do not write a gate you cannot break on purpose.** Eleven defects hid behind green
   tests (`.claude/skills/writing-gates/`). Include a positive control; if you match
   patterns against real files, print the match count — **zero matches is not a pass**.
4. **Do not read or copy the source of `Ampixa/sanoTTS`.** It is GPL-3.0 and this
   repository is MIT (D-032). Numbers, hyperparameters, and architecture are facts and may
   be cited — put them in [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) as
   *upstream-reported*, never as an M-number measurement.

## Before opening a PR

```bash
uv run python scripts/check_doc_counters.py         # index numbers, counts, citation anchors
uv run python scripts/check_doc_links.py            # relative links in markdown resolve
uv run python scripts/check_release_assets.py       # assets named in the docs exist in the release
uv run python .claude/hooks/test_guard_bash.py      # hook regression (94 cases)
uv run python scripts/test_sanitize_reports.py      # no corpus text in reports
uv run python scripts/test_blob_to_header.py        # blob → .rodata header (positive control: fp32 rejected)
make -C csrc line && make -C csrc fft && make -C csrc erf   # cheap C-core gates (erf = GELU approximation)
```

Python must go through `uv` — **never `pip install`**. `~/Documents/piper-plus` (the
teacher) is read-only. **Do not commit corpus text**; a hook blocks it.

⚠️ **The model weights are not MIT.** Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) and
[`MODEL_CARD.md`](MODEL_CARD.md) first.
