# CI に入れているもの / 入れていないもの

**入れてよいのは「新規 clone だけで通るゲート」だけ。**
どれが通るかは推測せず、**素の clone + 依存ゼロの venv で 1 本ずつ実測して決めた。**

## 入れてある（[`ci.yml`](ci.yml)）

| job | 中身 | 依存 |
|---|---|---|
| `docs` | 索引の M/D/C 番号・**引用アンカー**・件数 / md の相対リンク / hook の回帰 94 ケース / 本文検出 | **なし**（stdlib のみ） |
| `csrc` | `line` `fft` `pad` `g2p` | cc のみ |
| `python` | `test_losses`（26 項目）/ `test_labelpack` | torch（**CPU ビルド**）+ numpy |
| `release-assets` | **ドキュメントが名前を挙げた資産がリリースに在るか**（C-052 の再発防止） | ネットワーク |

## 入れていない（**理由つき**）

| ゲート | なぜ入らないか |
|---|---|
| `make -C csrc golden` / `stream` / `int8` / `int8-e2e` / `arena` | **重み blob が要る**（`csrc/*.bin` は git 管理外）。リリースから落とせば通るが、CI で毎回 2.9 MB 落とすのは割に合わない |
| `scripts/test_discriminator.py` | **ラベルパックが要る**（`data/pack_sib*`。コーパス由来なので配布しない） |
| `scripts/kana_g2p.py` | **pyopenjtalk が要る**（凍結テーブルとの突き合わせは live 側が要る） |
| `make -C csrc k2` … `k7` | **辞書 13.7 MB と pyopenjtalk が要る**。`all-test` にも入れていないのと同じ理由 |
| `scripts/phase0_verify_teacher.py` | **教師 ckpt が private** |
| ESP-IDF ビルド / QEMU | toolchain が重く、**実機の代わりにならない**（QEMU はサイクル精度ではない） |

⚠️ **「CI が緑」は「正しい」ではない。** ここで見ているのは
**ドキュメントの整合と、重みが要らない範囲の C / Python だけ**。
品質・速度・音は 1 つも見ていない。
