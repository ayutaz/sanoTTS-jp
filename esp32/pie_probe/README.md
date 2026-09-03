# PIE プローブ

ESP32-S3 の **PIE（128-bit 整数 SIMD）** が使えるかを確かめる最小プロジェクト（A〜C）に、
**実機のマイクロベンチ 2 本（D / E。T6 / P-0）**を足したもの。成果物ではない。

⚠️ **A〜C の前提検証は終わっている。** 本体のカーネルは実装済みで（M-57 / M-58、MAC の 99.40%）、
**ESP32-S3 では出荷ファームの既定になった**（D-048。フラグを付けなくても W8A8 + PIE）。
**QEMU で全経路 bit 一致**（M-62）、**実機でも checksum 一致**（M-83 / M-86 / M-90）。
A〜C は**最小の切り分け用**として残してある（実機で不正命令例外が出たときなど）。

**D / E は「MAC 1.61 cyc/MAC（M-82）は flash 律速か」「GELU 118 cyc/要素の原因は表か call か」を
実機で数字にするためのもの。** 結果は M-85（`docs/measurements.md`）。計画 §5 の分岐はこれで決めた。

## ビルドと実行

```bash
export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:$PATH"   # ⚠️ 3.14 では venv が壊れる
. ~/esp/esp-idf/export.sh
export PATH="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin:$PATH"
cd esp32/pie_probe        # ⚠️ `idf.py set-target` は不要（sdkconfig.defaults が esp32s3 を持つ）

# (1) QEMU: 正しさだけ（A〜E の bit 一致と陰性対照）。cyc は読まない
idf.py -B build_probe -DSDKCONFIG=build_probe/sdkconfig -DSAAN_QEMU=1 build
( cd build_probe && esptool.py --chip esp32s3 merge_bin --fill-flash-size 4MB -o /tmp/probe.bin @flash_args )
qemu-system-xtensa -nographic -machine esp32s3 -m 4M -drive file=/tmp/probe.bin,if=mtd,format=raw
#   → `PIE PROBE: PASS` の後で寝続けるので Ctrl-A X で抜ける

# (2) 実機（CoreS3 / QIO の板）: D / E の cyc を読む唯一の経路
idf.py -B build_probe_dev -DSDKCONFIG=build_probe_dev/sdkconfig \
       -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" build
idf.py -B build_probe_dev -p /dev/cu.usbmodem* flash monitor

# (3) 実機 + PSRAM（D4 を出す。CoreS3 と同じ Quad / 80 MHz）
idf.py -B build_probe_psram -DSDKCONFIG=build_probe_psram/sdkconfig \
       -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3;sdkconfig.psram" build
```

期待する出力の末尾は `PIE PROBE: PASS`。起動時の行に **`SAAN_PIE=1` / `SAAN_QEMU=1` の有無 /
CPU MHz / D-cache / flash モード**が出るので、cyc を読む前に必ず確認する。

| ファイル | 役割 |
|---|---|
| `sdkconfig.defaults` | 既定。240 MHz / -O2 / flash 80 MHz / D-cache 64 KB。⚠️ **行は IDF 既定の 32 B のまま**で、出荷構成（`esp32/boards/m5unified/sdkconfig.cores3`）は 2026-09-03 に **64 B** になった（M-84）。**D 節の cyc/行 は 32 B 行の値**（M-85 もそう）。flash モードだけ **DIO** |
| `sdkconfig.cores3` | **実機だけ**に重ねる: `CONFIG_ESPTOOLPY_FLASHMODE_QIO=y` |
| `sdkconfig.psram` | D4 用: `CONFIG_SPIRAM=y`（Quad / 80 MHz / `IGNORE_NOTFOUND`）。無い板や QEMU では「D4 skip」 |
| `main/probe_blob.c` | 本物の `csrc/student_i8.bin` を `.rodata` に埋める翻訳単位（`esp32/cmake/saan_model_rodata.cmake`。M5 構成と同じ仕組み） |

⚠️ **QIO を既定にしていない理由**: QEMU の flash モデルは QIO を受け付けず
（`qio_mode: Failed to set QIE bit` → spi_flash の初期化で assert → ブートループ。実際に踏んだ）、
**既定を QIO にすると (1) が起動しない**。逆に **DIO のまま実機で D 節を読むと、行フィルが 2 bit 幅ぶん遅く出て
M-82 / M-85 と比べられない**ので、実機は必ず `sdkconfig.cores3` を重ねる。probe は起動時の行に
`flash QIO` / `flash DIO` を出す。

⚠️ **blob が無いと D 節は skip**（`csrc/student_i8.bin` を置くか `-DSAAN_MODEL_BLOB=<絶対パス>`）。
skip は末尾に `⚠️ D 節は blob 無しで skip` と出て、**黙って PASS にはならない**。
⚠️ **blob v2 が要る**（`saan_weights_open` を通すので v1 は `SAAN_ERR_VERSION`）。
リリース v0.2.0 の `saanotts-jp-v3-int8.bin` は **v1** なので、
`uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt --int8 --out csrc/student_i8.bin
--golden csrc/golden_i8.bin --golden-from-quantized --report csrc/export_i8.json` で作ること。

## 確かめていること

| # | 内容 |
|---|---|
| 1 | `ee.*` 命令が**実行できる**（不正命令例外にならない） |
| 2 | `ee.vmulas.s8.accx` が 16 レーンの int8 積和を**正しく**計算する |
| 3 | 乱数 300 回でスカラ実装と**完全一致** |
| 4 | **陰性対照** — 1 要素変えたら不一致になる（比較が効いている証拠） |
| B | **本物のカーネル** `saan_conv1d_i8a` が PIE 有無で bit 一致（**11 形状**。cin 12→16 〜 304、blob v2 の `[cout][k][cinp]` レイアウト。うち 4 形状は S5b の m = 2 / 4 / 6 / (5, ksz=3) の枝を通すためのもの。m=2 は duration の実形状、m=4 / 6 とこの ksz=3 はモデルには無い）+ 陰性対照 24 件 |
| C | **丸め**（S2）: `saan_rint_i32`（Xtensa は `round.s`）が `rintf` とタイ・境界 22 値で一致 + 陽性対照（half-away-from-zero は 6 値で違う） |
| **D** | **重みの置き場所**（T6 / P-0）と **S5b の前後**: 同じ `saan_conv1d_i8a`・同じ 131,040 B・同じ dot 数で、重みを flash / DRAM / PSRAM に置いて CCOUNT を取る（D1〜D5）。**D6 / D7 は S5b 前のループ形**で D3 / D1 と対になる。ゲートは **D2 / D3 / (D4) の y が memcmp 一致** + **D5（T=16）の各行 = D2 の行の 2 回繰り返し** + **新形（本番）と旧形の y が bit 一致** + **DRAM 側 1 バイトを壊すと不一致・戻すと一致** + 実機では 5 回の min-max 幅 < 1% |
| **E** | **GELU**（T6）: 同じ 21,664 要素を「表 flash / DRAM × erf を call / inline」の 4 条件で。ゲートは **E2〜E4 の出力が E1（本体 `saan_gelu`）と memcmp 一致** + **DRAM の表 1 要素を壊すと不一致・戻すと一致**（壊した区間に入力が落ちた個数も出す。0 個なら空振り） + 幅 < 1% |

## D 節の読み方（重みの置き場所）

**何をしているか。** blob の `decoder.inp.weight` から連続 131,040 B を「cin=48 / k=1 / cout=2,730」の
int8 行列と見なす（中身は何でもよい。**D-cache 64 KB の 2 倍**あるので、1 パスで全 4,095 行がミスする）。

| 条件 | 何が違うか | 何が出るか |
|---|---|---|
| **Q** | 活性化の量子化 48×8 だけ | 差し引き用。D1 は 171 回、D5 は 4 単位（T=16 × 2 回）ぶん引く |
| **D1 hot** | flash の先頭 16 行（768 B = 32 B 行 24 本）を 171 回 | **dot 固定費の床**。⚠️ 171 × Q が raw の約半分なので、床の主証拠は D2 |
| **D2 DRAM** | 同じバイト列を `.bss` に memcpy して 1 回 | flash の待ちが無い床（DRAM はキャッシュを通らない） |
| **D3 flash** | `.rodata` の行列そのものを 1 回 | **(D3 − D2) = flash の待ち**。÷ 4,095 で cyc/キャッシュ行 |
| **D4 PSRAM** | `sdkconfig.psram` のビルドだけ | (D4 − D3) が PSRAM と flash の差 |
| **D5 flash T=16** | D3 を T=16（cout=1,365 × 2 呼び出し） | 行の再利用 2 倍で flash 分が dot あたり半分になるか（S7 の先取り） |
| **D6 旧形 flash** | D3 と同じだが **S5b 前のループ**（o → t → k、dot ごとに重み行を再ロード） | **(D6 − D3) = S5b の利得**（flash 込み） |
| **D7 旧形 hot** | D1 と同じだが S5b 前のループ | **(D7 − D1) = S5b の利得**（固定費の床で。flash の待ちを含まない） |

**判定の目安（probe が印字する）:**

- **cyc/キャッシュ行 ((D3−D2)/4,095) が 200〜300 → flash 律速の仮説成立 / < 60 → 棄却**
- **flash の割合 (D3−D2)/D3 が MAC の 1/3 以上 → S7（CHUNK 16）側の分岐**（計画 §5）
- **(D2 − D1) ≈ 0 → DRAM ストリームは無償**（重みを DRAM に常駐させても固定費は消えない）
- **(D7 − D1) / dot = S5b が固定費から削った分**。⚠️ D 節は cinp=48（m=3）の 1 形状だけ。
  本番の 1 step は m = 1 / 2 / 3 / 5 / 19 が混ざるので、step の見込みは
  `csrc/saanotts_int8.c` の S5b の表（dot の 95.1% が常駐経路）で重みづけして読むこと

**実機の値（M-85。CoreS3 / QIO 80 MHz / 240 MHz / D-cache 64 KB・32 B 行 / PSRAM 無し、2026-09-03）:**

| 条件 | cyc/dot | cyc/MAC | 読み |
|---|---:|---:|---|
| D1 hot | **78.0** | 1.626 | 固定費の床。**flash が無くても 1.6 cyc/MAC** |
| D2 DRAM | 77.5 | 1.615 | D1 と同じ = DRAM ストリームは無償 |
| D3 flash | **126.1** | 2.627 | flash の待ち **48.6 cyc/dot = 259.2 cyc / 32 B 行 = D3 の 38.5%** |
| D5 flash T=16 | 99.6 | 2.076 | flash 分が dot あたりほぼ半分（再利用で償却） |

**flash 律速と固定費律速の両方が成立**（cinp=48 では固定費 61.5% / flash 38.5%）。
5 回の幅は D1 0.20% / D2 0.01% / D3 0.22% / D5 0.00%（CCOUNT は決定的）。
⚠️ M-85 の D5 は Q を **2 単位**で引いた値（99.6）。その後 `d_nquant` を 4 単位に直した
（量子化はフレームごとなので T=16 は 2 倍）。同じ raw（4,371,297 − 4 × 9,687）なら **99.2**（換算。再測はしていない）。
⚠️ M-82 の本番 MAC 段は 1.61 cyc/MAC で D1/D2 の床と同じ値だが、本番は cinp 16〜304 の層が混ざるので
**D3 との対応（本番のうち flash 待ちが何%か）はこのプローブでは測っていない**。

⚠️ **QEMU では cyc を読まない。** QEMU の CCOUNT は命令数や壁時計に比例するだけで（同じ QEMU で
D3 ≈ D2、E1 4.8 cyc/要素）、**幅も 3〜300% 出る**。`-DSAAN_QEMU=1` を付けたビルドは幅を合否に入れず、
起動時の行に `SAAN_QEMU=1（D / E の cyc は意味が無い）` と出る。**QEMU で見るのは memcmp と陰性対照だけ。**
逆に実機向けビルドに `-DSAAN_QEMU=1` を付けると幅の NG が消えるので付けないこと。

## E 節の読み方（GELU）

| 条件 | 表 | erf | 何が分かるか |
|---|---|---|---|
| **E1** | flash（本体 `csrc/saanotts.c` の `saan_gelu`） | call | 現行。参照出力もこれ |
| **E2** | DRAM に memcpy したローカル表 | call（`noinline`） | 表の置き場所だけの効き |
| **E3** | flash | `always_inline` | 関数呼び出しだけの効き |
| **E4** | DRAM | inline | 両方 |

**実機の値（M-85、同上）**: E1 **112.4** / E2 113.4 / E3 **74.5** / E4 74.5 cyc/要素（幅 ≤ 0.12%）。
**表を DRAM に写しても効かず（M-84 と一致）、erf のインライン化で −34%。** T5 の G-1 がこれ。
残る 74.5 cyc/要素の内訳（Hermite 補間 / 表引き / GELU の乗算）は**このプローブでは分解していない**。

⚠️ **E2〜E4 は本体の式の写し**（`PROBE_ERF_BODY` / `PROBE_GELU_LOOP`。「同じカーネルを 2 回書かない」原則の例外）。
写しが本体とずれていないことは **E1 との memcmp 一致**で示し、比較が効いていることは
**DRAM の表 1 要素（節点 64）を壊すと不一致・戻すと一致**で示す（実機 315 要素 / 該当区間の入力 317 個）。
⚠️ **T5 と合流したので写しは書き直した**（S5b の作業中に）。本体の erf 表は `kSaanErfD`（erf'）から
`kSaanErfDh`（erf' × h を事前に掛けた表）になり、T5-G3 で早期 return がクランプになった。
⚠️ 直すまで probe は `'kSaanErfD' undeclared` でビルドできなかった（S5b 以前からのビルド破れ）。

⚠️ **いま写しは本体と 1 行ずれている（2026-09-03 時点）。** T5-G3 は符号の復元も
`x < 0 ? -y : y` から**符号ビットの memcpy OR** に変えたが、**それは M-87 で撤回された**
（実機の GELU が 118 → 211 cyc/要素に倍増。C-055）。**本体は三項演算子に戻り、
`probe.c` の `PROBE_ERF_BODY` は memcpy OR のまま**なので、**E3 / E4 が測っているのは
本番と別の形**。⚠️ **E1 との memcmp では検出できない** — 2 つの形は同じ値を出す
（速さだけが違う）。**これが C-055 そのもの**で、「マイクロベンチは同じ形でしか本番を予測しない」の
生きた実例として残してある。**新しい形を入れるときは、先に E 節へその形を足して実機で測ること**（1 回 10 秒）。
⚠️ IDF は `-ffp-contract=fast`。インライン化で `madd.s` への縮約が変わると**丸め水準で**出力が動きうる。
実機 / QEMU とも差 0 要素だったが、落ちたらゲートを緩めず記録すること。

## ⚠️ 確かめていないこと

- **QEMU では速度は一切測れない**（上記）。cyc は実機の CCOUNT だけ
- **実機の PIE と QEMU の PIE が同じである保証**（実機の checksum が QEMU と一致することで裏を取っている。M-82 / M-83）
- **D4（PSRAM）は未実測**（CoreS3 の pie_probe は PSRAM 無しで焼いた。`sdkconfig.psram` は QEMU で「D4 skip」まで）
- D 節は **cin=48 / k=1 の 1 形状**だけ。本番は cinp 16〜304 の混合で、固定費と flash の比は層ごとに違う
- **D-cache 64 B 行では測り直していない**（M-85 は 32 B 行。出荷構成は 64 B に変わった = M-84）。
  行フィルの回数が半分になるので **(D3 − D2) はおそらく変わる**が、**測っていない**
- D 節の割り込み禁止区間は最長 D5 の約 4.4 M cyc ≈ 18 ms（INT WDT 300 ms の内側）。**他コアの IDLE は止めていない**
  （キャッシュは共有だが、幅 ≤ 0.22% だったので影響は見えていない）

詳細は [`docs/measurements.md`](../../docs/measurements.md) の **M-56**（A）/ **M-57 / M-58**（B）/ **M-81**（C と、S5a のロード併合ループ）/ **M-85**（D / E）。
⚠️ S5a 以降のカーネルは `ee.vmulas.s8.accx.ld.ip` + `loopnez` を使う。**QEMU がその意味論を実機と同じに
実装しているかは実機の checksum で分かる**（期待 `0xa69a7ebbb5ccb05f`）。
