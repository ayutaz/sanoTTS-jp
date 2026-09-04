# 貢献のしかた

*[English below](#contributing-english)*

**このリポジトリは検証 (PoC) で、製品ではありません。** それでも歓迎したいものが
はっきりしているので、先に書いておきます。

## 一番ありがたいもの

| # | 何 | ボードが要るか |
|---|---|---|
| **1** | **音を聴いた感想** — [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` | ❌ 不要 |
| **2** | **ESP32-S3 実機での速度実測**（満チャンク 1 pull の xRT） | ✅ 要る |
| **3** | 手順書をなぞって**詰まった場所**の報告 | ❌ 不要 |

⚠️ **1 は本当に足りていません。いま残っている最大の穴です。**
品質の数字はすべて予測器（SCOREQ / UTMOS / DNSMOS）で、**人が聴いた評価は 2 回しかありません**
（β の決定 = M-60 / 実機の漢字経路 = M-91）。**どちらも 1 名・対照なし・盲検なし**なので、
言えるのは「破綻していない」までです。日本語ではこれらの予測器が較正されていない
（[`docs/decisions.md`](docs/decisions.md) D-013 / D-020）ので、
**「変な音がする」の一言が、n=24 の数字より情報量が多いことがあります。**
特に聴かれていないのは **`reports/k8_listen/` の 12 組**（辞書の枝刈りで読みが変わるペア）と
**アクセントのミニマルペア**（`reports/d4_accent/`）です。

**2 の手順は [`esp32/TESTING.md`](esp32/TESTING.md)。所要 15〜30 分・DAC は不要です。**
速度の要件（RTF ≤ 0.5）は M5Stack CoreS3 で満たしました（満チャンク xRT **0.446**。M-90）が、
**測ったのは 1 枚の板だけ**です。別の ESP32-S3（AtomS3 / DevKit / Core2 …）での実測を歓迎します。
✅ **v0.3.0 の配布イメージは高速化後のコード**なので、焼くだけで測れます。
native USB だけの板（CoreS3 / AtomS3）は **`-usbjtag` の版**を選んでください。
⚠️ **v0.2.0 以前は S1 前のコードで、コンソール入力も UART0** です。

## 守ってほしい 4 つ

このリポジトリには過去の事故から作った規律があります。
`.claude/hooks/guard_bash.py` が機械的に止めるものもあります。

### 1. 推測を数値として書かない

**測っていないことは「未測定」と書く。** [`docs/measurements.md`](docs/measurements.md) の
M-番号は**全部に再現コマンドが付いています**。数値を足すならそこに追記し、
`measured` か `estimated` かを明示してください。

n が小さいときは **n と信頼区間を数値の隣に**書いてください。

### 2. 訂正履歴を消さない

[`docs/decisions.md`](docs/decisions.md) の C-001〜C-065 は
**「1 コマンド打てば分かることを、打たずに推論した」種類の誤り**の記録です。
古い記述を直すときは、**上書きではなく C-番号として残して**ください。

### 3. ゲートは「落ちる壊し方」を言えないと書かない

**緑のまま欠陥が潜んでいた例が 16 件あります**（`.claude/skills/writing-gates/`）。
新しいテストを足すなら:

- **陽性対照**（必ず落ちるはずの入力）を一緒に入れる
- パターンで実ファイルを検査するなら **何件一致したかを出す**（0 件一致は合格ではない）
- **実ファイルを 1 つわざと壊して、落ちることを確認する**

⚠️ **速度の主張は「その形のコードを実機で測った数字」だけです。** 命令数・QEMU・
「似た形」のマイクロベンチはどれも根拠になりません。命令数が減ったのに実機で GELU が
2 倍遅くなった実例があります（C-055 / M-87）。

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
uv run python .claude/hooks/test_guard_bash.py      # hook の回帰（105 ケース）
uv run python scripts/test_sanitize_reports.py      # レポートに本文が混じっていないか
uv run python scripts/test_blob_to_header.py        # blob → .rodata ヘッダ（fp32 拒否の陽性対照）
make -C csrc line && make -C csrc fft && make -C csrc erf   # C コアの軽いゲート（erf = GELU の近似）
make -C csrc range                                  # 出力範囲つきカーネル（S9）が全域版と bit 一致
```

⚠️ **`make -C csrc all-test` に入っていないゲートが 2 系統あります。**
どちらも外部の資産が要るので、触ったなら手で回してください。

| ゲート | 何を見るか | 要るもの |
|---|---|---|
| `make -C csrc kb-parity` | 端末の経路判定（`saan_g2p_classify`）がホストの `classify_route()` と一致するか（**596/596**。K-B / M-90）。⚠️ **片方だけ直すとここが落ちます** | pyopenjtalk |
| `make -C csrc jdict` / `accent` / `njd-rules` / `oj-heap` / `kanji-e2e` / `label-ids` | K トラック（辞書リーダ・Viterbi・アクセント規則・NJD・RAM・端末とホストの一致） | 辞書 `csrc/k1_dict.bin` と pyopenjtalk |
| `make -C csrc prof` | 段別プロファイラ。`--expect-no-lookup` が「pull 中のテンソル検索 0 回」を守る（S1） | なし |
| `scripts/check_esp32_template.sh` | ESP32 雛形の静的検査。§10 は**漢字経路の作業領域が arena に収まるか** | なし |

⚠️ **ホストのプロファイラは実機の内訳ではありません。** 速度の主張は
**実機の `idf.py -DSAAN_PROFILE=1` の表**でだけ行ってください。QEMU の命令数が減ったのに
実機で GELU が 2 倍遅くなった実例があります（C-055）。

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
| **1** | **What it sounds like to you** — `saanotts-jp-v3-samples.zip` in [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | ❌ No |
| **2** | **Measured speed on real ESP32-S3 hardware** (xRT of one full chunk pull) | ✅ Yes |
| **3** | Where the instructions broke for you | ❌ No |

⚠️ **1 is genuinely missing, and it is the biggest hole right now.** Every quality number
here comes from a predictor (SCOREQ / UTMOS / DNSMOS), and **human listening amounts to two
sessions** (the β decision, M-60; the kanji path on hardware, M-91). **Both were one listener,
no control, not blind**, so they say no more than "not broken". None of those predictors is
calibrated for Japanese, so **"it sounds wrong" can carry more information than an n=24 score.**
Still entirely unlistened: the **12 pairs in `reports/k8_listen/`** (where dictionary pruning
changes a reading) and the **accent minimal pairs** in `reports/d4_accent/`.

Instructions for 2: [`esp32/TESTING.md`](esp32/TESTING.md) — 15–30 minutes, no DAC required.
The RTF ≤ 0.5 requirement is met on an M5Stack CoreS3 (full-chunk xRT **0.446**, M-90), but
**that is one board**; measurements on any other ESP32-S3 are welcome.
✅ **The v0.3.0 images carry the reworked code**, so flashing is enough to measure it; pick
the **`-usbjtag`** variant on a native-USB-only board (CoreS3 / AtomS3).
⚠️ Images before v0.3.0 predate the speed rework and read the console on UART0.

## Four rules

1. **Never write a guess as a number.** If it was not measured, say "not measured".
   Every entry in [`docs/measurements.md`](docs/measurements.md) carries a reproduction
   command; add yours the same way, and report n with a confidence interval when n is small.
2. **Never delete the correction log.** C-001–C-065 in
   [`docs/decisions.md`](docs/decisions.md) record errors of the form "one command would
   have answered this". Correct by appending a new C entry, not by overwriting.
3. **Do not write a gate you cannot break on purpose.** Sixteen defects hid behind green tests
   (`.claude/skills/writing-gates/`). Include a positive control; if you match
   patterns against real files, print the match count — **zero matches is not a pass**.
   Also: **a speed claim is only valid if that exact code was measured on hardware** —
   instruction counts, QEMU, and "similar shape" microbenchmarks are not evidence (C-055).
4. **Do not read or copy the source of `Ampixa/sanoTTS`.** It is GPL-3.0 and this
   repository is MIT (D-032). Numbers, hyperparameters, and architecture are facts and may
   be cited — put them in [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) as
   *upstream-reported*, never as an M-number measurement.

## Before opening a PR

```bash
uv run python scripts/check_doc_counters.py         # index numbers, counts, citation anchors
uv run python scripts/check_doc_links.py            # relative links in markdown resolve
uv run python scripts/check_release_assets.py       # assets named in the docs exist in the release
uv run python .claude/hooks/test_guard_bash.py      # hook regression (105 cases)
uv run python scripts/test_sanitize_reports.py      # no corpus text in reports
uv run python scripts/test_blob_to_header.py        # blob → .rodata header (positive control: fp32 rejected)
make -C csrc line && make -C csrc fft && make -C csrc erf   # cheap C-core gates (erf = GELU approximation)
make -C csrc range                                  # range-limited kernels (S9) match the full-range ones bit for bit
```

Two families of gates are **not** in `make -C csrc all-test` because they need external
assets — run them by hand if you touched the code they cover:
`make -C csrc kb-parity` (the device's route classifier agrees with the host's,
**596/596**; needs pyopenjtalk) and `make -C csrc jdict accent njd-rules oj-heap kanji-e2e label-ids` (the kanji track;
needs `csrc/k1_dict.bin` and pyopenjtalk).

Python must go through `uv` — **never `pip install`**. `~/Documents/piper-plus` (the
teacher) is read-only. **Do not commit corpus text**; a hook blocks it.

⚠️ **The model weights are not MIT.** Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) and
[`MODEL_CARD.md`](MODEL_CARD.md) first.
