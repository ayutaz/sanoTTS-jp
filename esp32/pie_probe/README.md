# PIE プローブ

ESP32-S3 の **PIE（128-bit 整数 SIMD）** が使えるかを確かめる最小プロジェクト。
**P-1（PIE カーネル）の前提検証**であって、成果物ではない。

⚠️ **前提検証は終わっている。** 本体のカーネルは実装済みで（M-57 / M-58、MAC の 99.40%）、
出荷ファームでも `idf.py -DSAAN_ENABLE_PIE=1 build` で有効にでき、
**QEMU で全経路 bit 一致**を確認した（M-62）。
このプローブは**最小の切り分け用**として残してある（実機で不正命令例外が出たときなど）。

```bash
export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:$PATH"
. ~/esp/esp-idf/export.sh
export PATH="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin:$PATH"
cd esp32/pie_probe && idf.py set-target esp32s3 && idf.py qemu
```

期待する出力の末尾は `PIE PROBE: PASS`。

## 確かめていること

| # | 内容 |
|---|---|
| 1 | `ee.*` 命令が**実行できる**（不正命令例外にならない） |
| 2 | `ee.vmulas.s8.accx` が 16 レーンの int8 積和を**正しく**計算する |
| 3 | 乱数 200 回でスカラ実装と**完全一致** |
| 4 | **陰性対照** — 1 要素変えたら不一致になる（比較が効いている証拠） |
| B | **本物のカーネル** `saan_conv1d_i8a` が PIE 有無で bit 一致（7 形状。cin 12→16 〜 304、blob v2 の `[cout][k][cinp]` レイアウト）+ 陰性対照 24 件 |
| C | **丸め**（S2）: `saan_rint_i32`（Xtensa は `round.s`）が `rintf` とタイ・境界 22 値で一致 + 陽性対照（half-away-from-zero は 6 値で違う） |

## ⚠️ 確かめていないこと

- **速度**。QEMU はサイクル精度ではないので**一切測れない**
- **実機の PIE と QEMU の PIE が同じである保証**
- 実 SRAM / キャッシュ / メモリ帯域

詳細は [`docs/measurements.md`](../../docs/measurements.md) の **M-56**（A）/ **M-57 / M-58**（B）/ **M-81**（C と、S5a のロード併合ループ）。
⚠️ S5a 以降のカーネルは `ee.vmulas.s8.accx.ld.ip` + `loopnez` を使う。**QEMU がその意味論を実機と同じに
実装しているかは実機の checksum で分かる**（期待 `0xa69a7ebbb5ccb05f`）。
