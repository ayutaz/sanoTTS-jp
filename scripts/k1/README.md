# scripts/k1 — K-1 調査（端末で漢字・カタカナ）の測定コード

結論は [`../../docs/research/k1-kanji-katakana-ondevice.md`](../../docs/research/k1-kanji-katakana-ondevice.md)、
実装計画は [`../../docs/plan/k1-kanji-implementation-plan.md`](../../docs/plan/k1-kanji-implementation-plan.md)。

## 使い方

```bash
uv run python scripts/k1/dump_entries2.py        # 辞書を .k1work/entries.tsv に展開（約 4 秒）
uv run python scripts/k1/completeness_audit.py   # 形式の完全性監査
uv run python scripts/k1/format_analyzer.py 200  # G9/G10/G11（新形式だけで MeCab を再現）
uv run python scripts/k1/verify_checkpoint.py    # G12/G13
uv run python scripts/k1/katakana_cost.py        # G7/G8
uv run python scripts/k1/silent_deletion.py      # 未知語の無音脱落
uv run python scripts/k1/heldout_source_check.py # held-out のソース順の確認
```

パスは [`k1_paths.py`](k1_paths.py) に集約してある。環境変数で上書きできる:

| 変数 | 既定 | 用途 |
|---|---|---|
| `K1_WORK` | `<repo>/.k1work` | 中間生成物。**リポジトリに入れない**（entries.tsv 90 MB / trie キャッシュ 54 MB） |
| `PIPER_PLUS_ROOT` | `~/Documents/piper-plus` | piper-plus 側の辞書 |
| `ENTRIES_TSV` | `$K1_WORK/entries.tsv` | 使うエントリ表を差し替える |

## ⚠️ 移設にあたっての改変

これらはセッションの scratchpad で書かれ、**絶対パスを直書きしていた**。
移設時に機械的に `k1_paths` 経由へ書き換えた（`_ROOT` / `_WORK` / `_PP`）。
**レーンが実際に走らせたコードとは、この 1 点だけ異なる。**
移設後に `completeness_audit` / `format_analyzer` / `verify_checkpoint` /
`katakana_cost` / `silent_deletion` / `heldout_source_check` を実行し、
**同じ数値が出ることを確認済み**。

## ⚠️ 移設していないもの

| | 理由 |
|---|---|
| `oj/`（OpenJTalk の C ソース 7.5 MB） | 第三者コードの複製。ram-lane が計装のために置いたもの |
| `saan_pages` / `saan_probe`（各 1.7 MB） | ビルド成果物。`build_probe.sh` / `build_pages2.sh` で作れる |
| `entries*.tsv`（各 90 MB）/ `trie_cache*.npz`（各 54 MB） | 派生データ。`dump_entries2.py` 等で再生成できる |
| `ablate_examples.json` (7.8 MB) / `flags_examples.json` (574 KB) | 差分の全列挙。集計は `reports/k1_*.json` にある |

`run_all.sh` / `build_probe.sh` / `build_pages2.sh` は `oj/` とクロスコンパイラに依存するので、
**そのままでは動かない**。RAM 測定を再現するには OpenJTalk のソース展開が要る。

## 出力

集計結果は `reports/k1_*.json`（22 本）。
