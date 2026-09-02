# 公式実装 `Ampixa/sanoTTS` から得た事実

作成 2026-08-28 / 対象 commit `27c912252bef` (2026-08-20T12:15:30Z)

## このドキュメントの位置づけ

⚠️ **ここに載っている数値はすべて「上流の申告値」であり、私たちは再現していない。**
`docs/measurements.md` の M-番号（自己実測）とは**別扱い**にすること。
突き合わせの材料であって、うちの実測値を上書きする根拠ではない。

出所は公開ドキュメントのみ:

```bash
gh api repos/Ampixa/sanoTTS/contents/README.md --jq '.content' | base64 -d
gh api repos/Ampixa/sanoTTS/contents/docs/distillation-recipe.md --jq '.content' | base64 -d
gh api repos/Ampixa/sanoTTS/contents/docs/mcu-classes-and-porting.md --jq '.content' | base64 -d
gh api repos/Ampixa/sanoTTS/contents/docs/roota-language-porting-recipe.md --jq '.content' | base64 -d
gh api repos/Ampixa/sanoTTS/contents/docs/repository-layout.md --jq '.content' | base64 -d
```

## ⚠️ ライセンス — ソースコードを読まない

公式実装は **GPL-3.0**。本リポジトリは **MIT** で公開済み。

| 行為 | 可否 |
|---|---|
| 公開ドキュメントの**数値・ハイパーパラメータ・アーキテクチャ構成**を参照 | ✅ 事実は著作権の対象外 |
| ソースコード（`.c` / `.py` / `.S`）をコピー | ❌ GPL が伝播し MIT で配布できない |
| ソースコードを読んでから書き直す | ⚠️ グレー。**特にアセンブリは表現の幅が狭い**ので避ける |

**2026-08-28 時点でソースコードは 1 行も読んでいない。**
この線引きは **D-032** として凍結し、`.claude/hooks/guard_bash.py` が機械的に強制する（ドキュメント取得は通し、ソース取得と `git clone` と `uv add sanotts` を deny。回帰 94 ケース）。
読む必要が出たら、先に D-032 を撤回すること。

## 上流が持っているもの

6 言語 9 音声（en / ne / hi / vi / id / zh）の製品リポジトリ。
**日本語は含まれない。** Python パッケージ・npm・Arduino ライブラリ・WASM デモを配布。

ポーティングレシピは「音素ID in / 波形 out」を境界としており、
上流自身が *"A complete arbitrary-text product still needs the language frontend
packaged or reimplemented"* と書いている。
**うちの 877 B オンデバイス G2P はこの穴を埋めるもので、上流に対応物が無い**
（上流は espeak-ng を同梱。`en_dict` だけで 168,204 B）。

## 上流申告値 vs うちの実測値

| 項目 | 上流（**申告値・未再現**） | うち（**自己実測**） | 出典 |
|---|---:|---:|---|
| ESP32-S3 / int8 + PIE | **0.22× RT**（float との相関 0.985） | **0.926× RT**（CoreS3 / W8A8+PIE / S1〜S5a、n=3。上流申告の **4.2 倍遅い**）。S1 前は第三者報告 1.554（未再現） | M-82 / M-80 / M-81 |
| ESP32-C3 / スカラ C・FPU 無し | **5.72× RT** | — | — |
| 移植可能 C / fp32 の外挿 | — | **2.47× RT** | M-43 |
| int8 重み | ~680 KB | **624,692 B**（payload。blob v2 は事前整列の padding で 634,788 B / ファイル 654,032 B） | M-39 / M-81 |
| RAM（ストリーミング） | 130〜160 KB | **196.9 KB** | M-42 |
| RAM（一括） | ~300 KB | 1,258 KB → 197 KB に削減済み | M-42 |
| 演算量 | ~45 MMAC/s | **43.618 MMAC/s** | M-26 |
| 合計 params | 1.40M（quality）/ 745k（MCU int8） | **559,008** | D-016 |

**うちの「fp32 では届かない、int8 + PIE が必須」という結論は上流の申告と整合する。**
上流は Tier C3（FPU 無しスカラ）について *"the float glue, not the MACs, dominates"*
と書いており、これもうちの η_host=0.364 の解釈と同じ向き。

⚠️ **未解決だった「45 MMAC/s との 12% 乖離」（自前計算 43.618）は、
上流が「~45」と概数で書いているだけだった可能性が高い。**
ただし上流の内訳は公開されていないので、**確認したわけではない**。

## 蒸留レシピの差分

| 項目 | 上流（英語） | うち（日本語） |
|---|---|---|
| duration | hidden 32〜64 / depth 3 / kernel 5 | width 32 / 3 blocks |
| acoustic | token-context / hidden 64〜96（~359k） | width 48（~199,536） |
| **decoder** | ~1.0M / **教師からチャネル切り出しで初期化** | 331,308 / **ゼロから学習**（教師比 0.802 / M-49） |
| decoder 学習 | recovery → z-mix → joint の 3 段 × **40k step** | 4 段 × 20k step |
| z-mix | `--acoustic-latent-mix-prob 0.5`（確率で切替） | ランダムな混合比 |
| β（式7） | **6.0**（英語・耳で決定） | sweep 0/2/4/6/8 → 候補 **0 と 2** |
| **de-metal 段** | MPD + iSTFT 位相損失の追加 fine-tune | **無い** |
| 評価指標 | SCOREQ + UTMOS + **DNSMOS** | SCOREQ + UTMOS + **DNSMOS**（M-49 で追加） |
| データ配分 | acoustic ~8k 行 / **decoder は ~512 行** | 20,790 行を一律 |
| 教師の与え方 | ONNX を **decoder-cut** して `generator_input → 波形` の graph を作る | `.ckpt` を PyTorch で読む |

## ⚠️ 追試すべき 2 点（**E-1 / E-2** として計画に起票済み）

作業内容は [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) §10。

### E-2. decoder の教師初期化 — **一部決着（M-49）**

上流は *"Teacher-init the decoder. It survives channel-pruning;
from-scratch sub-400k decoders are a dead class."* と書いている。

⚠️ **その「sub-400k」の根拠は論文本文では 357k の R8 = 波形領域の
transposed-convolution decoder（522 MMAC/s）の測定であり、
331k の iSTFT decoder（86 frames/s, 1024-pt iSTFT, 28 MMAC/s）に
教師潜在を通したセルは論文に存在しない**（§IV-B を自分で読んで確認。
PDF sha256 `64b0d426b585e05f87867375928755a79d02ffc989374c08f1aa52c37267eab1`）。
論文が並べているのは

| 潜在 → decoder | SCOREQ | 教師比 |
|---|---:|---:|
| 教師 z → 教師 decoder | 4.68 | — |
| 教師 z → **357k R8**（波形 transposed-conv） | 3.20 | 0.684 |
| 教師 z → **1.0M teacher-init** | 4.13 | 0.883 |
| 生徒潜在 → 教師 decoder | 3.70 | 0.791 |

の 4 セルで、**すべて z-line（192ch）**。

**うちが埋めたのはまさに欠けているセル**（M-49、日本語 / n=200）:
教師 z →（40 次元 c-line 経由）→ **331k iSTFT decoder** で**教師比 0.802**。
生徒潜在 → 教師 decoder は **0.747**。
⚠️ 言語も教師もレシピも違い、うちは 40 次元ボトルネックを挟んでいるので
**上流の 0.684 / 0.883 と直接比較しない**（論文自身が絶対値の言語間比較を禁じ、
教師比での報告を指示している）。

→ **「sub-400k は死んだクラス」は、うちのアーキテクチャについての主張ではなかった。**
ただしうちでも decoder は鎖分解で最大の単項（0.395 / 全体 0.729）である。**未再現**の
部分（上流の 0.684 / 0.883 そのもの）は変わらず未再現。

⚠️ **そのままでは実行できない。トポロジが違う**（自分でソースを読んで確認）:

| | 教師 `MBiSTFTGenerator` | 生徒 `Gγ` |
|---|---|---|
| 定義 | `~/Documents/piper-plus/src/python/piper_train/vits/mb_istft.py:133` | `src/saanotts_jp/_param_reference.py:79` |
| 構造 | `conv_pre` → `ConvTranspose1d` ×2 → `ResBlock2` → PQMF 4 サブバンド合成 | 深さ方向分離 conv ×5 + rank-12 FiLM + 分解出力ヘッド |
| 時間方向 | **アップサンプルする**（`upsample_rates=(4,4)`） | **しない**（フレームごとに 1539ch を直接出す） |
| 幅 | `upsample_initial_channel = 256` | `W=76 / E=304` |

**チャネル切り出しで初期化するには `Gγ` を「教師を細くしたコピー」にする必要があり、
論文 Table I の 331,308 params を捨てることになる。**
切り分けるべき 3 仮説は
[`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) §10 E-2。

### E-1. DNSMOS — **測った（M-49）**

上流は *"a metallic artifact scores high on SCOREQ and low on DNSMOS"* と書いている。

**測った結果、そのパターンは日本語の生徒には出ていない。**
実人間 2.7866 / 教師 2.7547 / L1 2.4493 / L2 2.1950 / 生徒 2.1071（OVRL, n=24）で、
SCOREQ・UTMOS・DNSMOS の 3 指標が**同じ順序で単調に下がる**。
⚠️ **教師/人間は DNSMOS で 0.989** と、SCOREQ の 0.820 / UTMOS の 0.758 より
はるかに高い。**DNSMOS は日本語の教師をほぼ人間と同じに採点する。**
⚠️ 上流の申告値そのもの（英語のどのモデルで何点か）は**未再現**。

どちらもライセンスと無関係に検証できる（数値と手法は著作権の対象外）。
