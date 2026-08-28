# PIE プローブ

ESP32-S3 の **PIE（128-bit 整数 SIMD）** が使えるかを確かめる最小プロジェクト。
**P-1（PIE カーネル）の前提検証**であって、成果物ではない。

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

## ⚠️ 確かめていないこと

- **速度**。QEMU はサイクル精度ではないので**一切測れない**
- **実機の PIE と QEMU の PIE が同じである保証**
- 実 SRAM / キャッシュ / メモリ帯域

詳細は [`docs/measurements.md`](../../docs/measurements.md) の **M-56**。
