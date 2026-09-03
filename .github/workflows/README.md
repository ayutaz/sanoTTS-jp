# CI に入れているもの / 入れていないもの

**入れてよいのは「新規 clone だけで通るゲート」だけ。**
どれが通るかは推測せず、**素の clone + 依存ゼロの venv で 1 本ずつ実測して決めた。**

## 入れてある（[`ci.yml`](ci.yml)）

| job | 中身 | 依存 | 実測 |
|---|---|---|---:|
| `docs` | 索引の M/D/C 番号・**引用アンカー**・件数 / md の相対リンク / hook の回帰 94 ケース / 本文検出 / **blob → .rodata ヘッダ変換の回帰**（fp32 拒否の陽性対照つき） | **なし**（stdlib のみ） | **9 s** |
| `csrc` | `line` `fft` `pad` `g2p` `erf`（GELU の erf 近似 vs libm）**`range`**（S9 の範囲版カーネル vs `[0,T)` 版。2026-09-03 追加）。**どれも重み blob を要らない** | cc のみ | **163 s** |
| `python` | `test_losses`（26 項目）/ `test_labelpack` | torch（**CPU ビルド**）+ numpy | **15 s** |
| `release-assets` | **ドキュメントが名前を挙げた資産がリリースに在るか**（C-052 の再発防止） | ネットワーク | **10 s** |

実測は run `33718551954`（2026-09-03、ubuntu-latest、uv のキャッシュあり）。
⚠️ `csrc` が 26 → 163 s に伸びたのは **`erf` の全 float 走査**（[-4, 4] の 2,164,260,866 値。T5）と `range` の追加による。
再現: `gh run view <id> --json jobs -q '.jobs[] | "\(.name) \(.startedAt) \(.completedAt)"'`

⚠️ **torch は `--torch-backend cpu` を明示している。** 外すと Linux で
CUDA 版（数 GB）を引いて `python` job が数分になる。

## 初回で移植性バグを 1 件見つけた

`csrc` が Linux で落ちた。**CI の設定ではなくコードの欠陥**だった:

```
fft_test.c:27: error: 'M_PI' undeclared
fft_test.c:56: error: 'CLOCK_MONOTONIC' undeclared
```

`M_PI` は C99 の `<math.h>` に無い（POSIX 拡張）。macOS では既定で見えるが、
Linux + glibc の厳密 `-std=c99` では見えない。**出荷するコア 2 本
（`saanotts.c` / `saanotts_stream.c`）も同じ穴を持っていた**（C-052 の追記 / C-033）。

**1 つの OS でしか通していないビルドは「通る」と言えない。**

## 入れていない（**理由つき**）

| ゲート | なぜ入らないか |
|---|---|
| `make -C csrc golden` / `int8-golden` / `stream` / `int8` / `int8-e2e` / `arena` | **重み blob が要る**（`csrc/*.bin` は git 管理外）。リリースから落とせば通るが、CI で毎回 2.9 MB 落とすのは割に合わない |
| `scripts/test_discriminator.py` | **ラベルパックが要る**（`data/pack_sib*`。コーパス由来なので配布しない） |
| `scripts/kana_g2p.py` | **pyopenjtalk が要る**（凍結テーブルとの突き合わせは live 側が要る） |
| `make -C csrc jdict` / `accent` / `njd-rules` / `oj-heap` / `kanji-e2e` / `label-ids` | **辞書 13.7 MB と pyopenjtalk が要る**。`all-test` にも入れていないのと同じ理由 |
| `scripts/phase0_verify_teacher.py` | **教師 ckpt が private** |
| ESP-IDF ビルド / QEMU | toolchain が重く、**実機の代わりにならない**（QEMU はサイクル精度ではない） |

⚠️ **「CI が緑」は「正しい」ではない。** ここで見ているのは
**ドキュメントの整合と、重みが要らない範囲の C / Python だけ**。
品質・速度・音は 1 つも見ていない。
