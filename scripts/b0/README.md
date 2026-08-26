# B-0 の測定スクリプト

[`docs/research/b0-g2p-footprint.md`](../../docs/research/b0-g2p-footprint.md) と
[`reports/b0_*.json`](../../reports/) を生成したスクリプト群。**測定の記録として保全している。**

## ⚠️ そのままでは動かない

多くのスクリプトが **scratchpad の絶対パスをハードコード**している（18 本中 9 本）。
生成時の一時ディレクトリで、もう存在しない。再実行するときは入出力パスを書き換えること。

```bash
grep -l claude-1518468357 scripts/b0/*.py   # 要修正のファイル一覧
```

また、これらは複数のエージェントが**共有ディレクトリで並行して書いた**もので、
作業中に上書き事故が起きている。`compact_matrix.py` は入力 `dic.csv` を
**in-place で書き換える**ので、同じディレクトリで再実行すると context id が
二重に remap されて辞書が黙って壊れる。

## 何を再現するスクリプトか

| ファイル | 役割 | 重要度 |
|---|---|---|
| `build_corpus.py` | `data/splits/` の train / heldout 分割を生成。**コーパスは .gitignore で除外しているので、再現にはこれが必要** | **高** |
| `make_embedded.py` | ESP32 実運用想定の評価セット 183 文。**文が inline された手書きの独自資産**（時刻・日付・金額・通知文・助数詞・固有名詞） | **高** |
| `measure_coverage.py` | 枝刈り辞書のカバー率測定（B-0 の判定基準） | 高 |
| `measure_size.py` | 枝刈り辞書の実バイナリサイズ測定 | 高 |
| `make_pruned.py` | 枝刈り辞書のビルド | 中 |
| `dump_entries.py` | `sys.dic` のエントリ列挙 | 中 |
| `compact_matrix.py` | `matrix.bin` の context-id 圧縮 | 中 |
| `b0_feature_sizes.py` / `b0_field_ablation.py` | feature 削減水準ごとのサイズ | 中 |
| `b0alt_*.py` | 代替 G2P 経路（かな入力・定型文・ニューラル）の見積もり | 中 |
| `class3.py` | 失敗の 3 分類（読めない / 誤読 / アクセントのみ） | 低 |

**なお B-0 の結論は「辞書枝刈りは実装対象から外す」**（[`docs/decisions.md`](../../docs/decisions.md) D-009）
なので、枝刈り系のスクリプトは記録目的であって今後使う予定はない。
現行の入力仕様に対応する実装は [`scripts/kana_g2p.py`](../kana_g2p.py)。
