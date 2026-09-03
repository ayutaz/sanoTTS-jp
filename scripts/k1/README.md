# scripts/k1 — K トラック（端末で漢字・カタカナ）の測定コードと生成器

結論は [`../../docs/research/k1-kanji-katakana-ondevice.md`](../../docs/research/k1-kanji-katakana-ondevice.md)、
実装計画は [`../../docs/plan/k1-kanji-implementation-plan.md`](../../docs/plan/k1-kanji-implementation-plan.md)。

**K-0 〜 K-7 は完了**（QEMU で漢字文から合成まで完走。M-76）。残りは実機（K-8）。

## 本番で使うもの（実装が依存している）

```bash
uv run python scripts/k1/k0_verify_dict.py                    # 辞書の同一性（**最初にこれ**）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin   # 端末に焼く辞書 blob
                                                              #   既定 438,750 entries（D-044）
                                                              #   → 13,702,320 B / 枠 13,828,096 B
uv run python scripts/k1/k1_fit_point.py                      # 予算に入るエントリ数
uv run python scripts/k1/k4b_vendor.py --sdist <tgz> [--check]  # Open JTalk の取り込み / 検査
uv run python scripts/k1/k2_gen_vectors.py                    # K-2/K-3 のベクタ
uv run python scripts/k1/k4_gen_vectors.py                    # K-4 のベクタ
uv run python scripts/k1/k4b_gen_vectors.py                   # K-4b のベクタ
uv run python scripts/k1/k5_gen_labels.py --vectors ... --out ...  # K-5 の basis
uv run python scripts/k1/k6_gen_vectors.py                    # K-6/K-7 のベクタ
```

⚠️ **`k1_build_dict.py` は行列 / char / unk も入れる。** 入れないと blob が
8.1 MB になり（12.2 MB のはず）、**端末で Viterbi も未知語も動かない**。

## 調査時の測定コード（結論は出ている）

```bash
uv run python scripts/k1/k0_verify_dict.py       # **最初にこれ**。使う辞書が D-042 の凍結物か
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

## K-1 / K-2 のビルドとゲート

```bash
uv run python scripts/k1/k1_build_dict.py        # 本番の辞書 blob を組んで G1〜G5
uv run python scripts/k1/k1_fit_point.py         # 予算に入るエントリ数（本番エンコーダで実測）
uv run python scripts/k1/k2_gen_vectors.py       # C 側ゲートの参照ベクタ（参照は MeCab）
uv run python scripts/k1/k4_gen_vectors.py       # K-4 の参照ベクタ（参照は Python 版の 4 段）
make -C csrc jdict                                  # K-2/K-3 の受け入れ（G6〜G11）
make -C csrc accent                                  # K-4 の受け入れ（G12/G13）
```

⚠️ `make -C csrc jdict` / `k4` は **`all-test` に入れていない**。辞書（pyopenjtalk / piper-plus）と
数 MB の生成ベクタが要るので、`g2p-corpus` と同じ扱い。

エンコーダ本体は `src/saanotts_jp/jdict.py`、その単体テストは
`scripts/test_k1_dict.py`（**TDD で書いた**。実装より先にテストを書き、
落ちることを確認してから実装している）。

## K-0 の凍結物

| ファイル | 役割 |
|---|---|
| `dict_manifest.json` | D-042 で凍結した辞書の同一性（8 ファイルの SHA-256 + ヘッダ + 環境） |
| `k0_freeze_dict.py` | 凍結する。既存と食い違えば **exit 1 で止まる**（`--force` で更新） |
| `k0_verify_dict.py` | 照合する。G0-2 / G0-3a（合成）/ G0-3b（別リビジョン実物） |
| `k0_mmu_window.py` | ESP-IDF ヘッダから MMU 窓を確定 |
| `k0_fit_point.py` | 予算に入るエントリ数を二分探索（内挿は C-009 で禁止） |
| `k0_dict_inventory.py` | マシン上の sys.dic を全数え上げ |

## 出力

集計結果は `reports/k1_*.json`（22 本）。
