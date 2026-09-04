# ESP32-S3 のファームウェア（c'-4）

`csrc/` の C99 推論コアを ESP-IDF アプリとして起動し、ストリーミング API で
合成しながら I2S に流す。**実機（M5Stack CoreS3）で動く**ところまで来た（M-90）。

- **DevKit 構成**はこのディレクトリ（`esp32/`。音は I2S DAC）
- **M5Stack 構成**は [`boards/m5unified/`](boards/m5unified/README.md)（内蔵スピーカー + 画面。**実機の測定はすべてこちら**）
- **実機を持っている人向けの手順**は [`TESTING.md`](TESTING.md)
- **PIE の切り分けと実機マイクロベンチ**は [`pie_probe/`](pie_probe/README.md)

---

## 現在地（2026-09-03）— **実機で動いた。要件 RTF ≤ 0.5 も満たした。残るのは聴取だけ**

**これを最初に読むこと。**
実機を持っている方への手順は [`TESTING.md`](TESTING.md) にまとめてある。

✅ **2026-09-02〜03、自分の M5Stack CoreS3 で測った**（板の同定は D-047: ESP32-S3 / 16 MB / Quad PSRAM 8 MB / native USB）。
それ以前は「第三者が CoreS3 で 1.554× RT と報告したが私は未再現」という状態だった
（[`../docs/research/s1-m5-cores3-speed.md`](../docs/research/s1-m5-cores3-speed.md)）。**その報告はもう現在地ではない。**

| | 9/2 夜（M-82） | **いま（M-90）** |
|---|---:|---:|
| 満チャンク 1 pull の xRT | 0.926 | **0.446**（要件 RTF ≤ 0.5 達成） |
| 1 step | 18,378,513 cyc | **11,659,500 cyc**（M-89） |
| アンダーラン | 1 / 14 | **0**（全文） |
| 鳴らし始めまで | 719 ms | **384 ms**（M-90 の生ログ。M-89 は 432 ms） |
| 内部 DRAM の空き（起動直後） | 99,987 B | **132,039 B**（最大ブロック 86,016。漢字辞書込みの構成で） |
| arena（静的確保 / 実機の used） | 212,992 / 195,808 B | **180,224 / 157,360 B** |

**PCM の checksum は M-82 から 1 bit も変わっていない**: W8A8+PIE **`0xa69a7ebbb5ccb05f`** /
W8A32 **`0xe4b645c30835d42d`**。速度の作り直し（S1〜S5b / T1〜T5）は**すべて出力を変えない変更**だった。
⚠️ **配布イメージ v0.2.0 までは旧コアの `0x04de91103a0e49f9` / `0x78c209af06affc01`**（S3 = GELU の erf 近似で変わった。D-046）。
⚠️ **blob は v2**（654,032 B）。リリース資産は v1 のままで、このコアは `SAAN_ERR_VERSION` で拒む。

⚠️ **発話全体で見ると 0.541〜0.712 でまだ 0.5 を超える**（warmup 38 フレームぶんが初回 pull に乗る。
要件の分母がどちらかは `docs/requirements.md` に書かれておらず未定 = D-049。M-88 / M-90）。
⚠️ **G32 の対照つき聴取が残っている。** ⚠️ **ざっとした聴取は済んでいる**（M-91 / M-93 = 実機 / M-96 = ブラウザ。どれも**1 名・対照なし・盲検なし**）。**残るのは対照つきの聴取**（`reports/k8_listen/` の 12 組 / `reports/d4_accent/`）。
checksum が同じなので波形は同一だが、**途切れの有無と読み違いの重大さは聴かないと分からない。**

### ⚠️ 変わった既定（古い手順をそのまま打つと別の構成になる）

| | 前 | いま | 出典 |
|---|---|---|---|
| **PIE** | `-DSAAN_ENABLE_PIE=1` が要った | **ESP32-S3 なら既定で有効**（W8A8 + PIE）。W8A32 で測るときだけ `-DSAAN_ENABLE_PIE=0` | **D-048** |
| **flash モード** | IDF 既定の DIO | **QIO**（`sdkconfig.defaults`）。⚠️ `-DSAAN_QEMU=1` のときだけ `sdkconfig.qemu` が DIO に戻す（QEMU は QIO を受け付けない） | M-86 |
| **D-cache の行** | 32 B | **64 B** | M-84 / M-86 |
| **入力** | 漢字は `!` を前置 | **`!` は要らない**。`saan_g2p_classify()` が「かな / 辞書 / 拒否」の 3 値で決める（`!` は辞書経路への強制として残してある。試験用） | `csrc/g2p.h` |
| **arena** | 208 KB（212,992 B） | **176 KB（180,224 B）** | M-89 |
| **漢字 × M5** | 構成が無かった | **`boards/m5unified/` に辞書パーティションごと載る**（`esp_mmu_map`） | M-90 |

### 実機で分かったこと / まだ分かっていないこと

| # | 見たかったこと | いま |
|---|---|---|
| ~~1~~ | ~~`idf.py build` が通るか~~ | ✅ **通った**（M-54 / v5.5） |
| ~~2~~ | ~~起動するか~~ | ✅ QEMU（M-62）→ **実機**（M-83 / M-86 / M-89 / M-90） |
| ~~3~~ | ~~実際の SRAM 消費（IDF + FreeRTOS + I2S DMA 込みの free heap）~~ | ✅ **実機で 132,039 B**（M5 構成・漢字辞書込み。M-90）。DevKit の漢字構成は 59,044 B（M-83 / M-86） |
| ~~4~~ | ~~実際の xRT とアンダーラン~~ | ✅ **満チャンク 0.446 / アンダーラン 0**（M-90）。⚠️ 発話全体では 0.541〜0.712（M-90 の 4 文） |
| **5** | I2S の実サンプルレート誤差（**ESP32-S3 に APLL が無い**） | ❌ **未測定**。オシロか長時間録音が要る（ファーム自身が警告を出す） |
| ~~6~~ | ~~flash から mmap した重みが D-cache を thrash しないか~~ | ✅ 測った: flash の待ちは MAC の **38.5%**（M-85 の D 節）。64 B 行で **−7.0%**（M-84） |
| ~~7~~ | ~~`sdkconfig.defaults` のオプション名が実在するか~~ | ✅ ビルドが通った（M-54） |
| ~~8~~ | ~~`esp32/main` が呼ぶ IDF API の綴り~~ | ✅ 実機で起動した（M-83 以降） |
| **9** | **DevKit の `saan_i2s.c`（I2S 直叩き）** | ⚠️ **未**。M5 の `M5.Speaker` 経由では鳴らしている（M-90。ログは `貯めた 8192 sample を送出` まで） |
| **10** | **音（聴取 G32）** | ❌ **未。これだけが人を待っている** |

**「たぶん動く」とは書かない。「未検証」と書く。**

### QEMU で取れたもの（M-62）

| 項目 | 値 |
|---|---|
| `model` mmap / 重み | offset `0x00210000` / **183 tensors** / base `0x3c040000`（16 B 境界 OK） |
| 端末側 G2P | 44 B → **53 ids**、`demo_ids.h` の錨と**完全一致** / 0.602 ms |
| 合成 | 106 frames / 27,136 sample / arena used 194,848 B |
| **PIE の bit 一致** | **スカラ実装と 27,136 sample すべて一致**（陰性対照つき） |
| **自由入力**（M-63） | UART0 に打ち込んで合成。基準の 1 行から**起動時に喋らせたときと同じ checksum** |

⚠️ **ホストとターゲットは bit 一致しない。それは正常**（float の丸めが違う）。
`|max|` は一致し `Σx²` の相対差は 1.6e-7 なので丸め差と切り分けられる。
**bit 一致を主張してよいのは「同じターゲット上の 2 構成」を比べたときだけ。**

⚠️ **ビルドを通したときに実際にバグが 1 件出た** — `M_PI` は C99 標準ではないので
newlib の厳密 `-std=c99` で `saanotts.c` / `saanotts_stream.c` が落ちた。
ホストの clang では見えるので**クロスコンパイルするまで一度も検出されなかった**
（`csrc/saanotts_internal.h` で定義して解決。M-54）。

### では何を確かめたのか

`bash scripts/check_esp32_template.sh` が通ることだけ。中身は 10 個:

| # | ゲート | 何が言えるか |
|---|---|---|
| 1 | コア 4 ファイル **+ `g2p.c`** が `-std=gnu17 -O2 -Wall -Wextra -Werror` で警告 0 | IDF 既定の方言でコンパイルは通る |
| 2 | `nm -u` に malloc / free / fopen / mmap / POSIX / printf が **1 つも無い** | コアはベアメタルに載る形をしている |
| 3 | csrc のホスト専用 API はすべてテストコード側 | コアとテストの境界が守られている |
| 4 | blob の全テンソル offset が 16 の倍数 | **base さえ 16 バイト境界なら**全テンソルが揃う |
| 5 | CMake 3 本の構文が通る | 括弧・パスの誤りは無い（**configure ではない**） |
| 6 | `partitions.csv` の境界・重なり・容量 | blob が入る配置になっている |
| 7 | `main.c` が呼ぶ `saan_*` が csrc のヘッダに実在 | typo は無い |
| 8 | ホスト stub ビルド + **C コアと bit 完全一致** | アプリ側ロジックが正しい（下記） |
| 9 | IDF API の棚卸し | 実機で最初に照合する一覧 |
| **10** | **静的 arena が漢字経路の作業領域を収められるか**（`SAAN_ARENA_BYTES ≥ SAAN_KANJI_WORKBYTES`） | 漢字ビルドで arena が溢れない。**陽性対照つき**（足りない arena の同じ typedef がコンパイルに失敗する） |

ゲート 8 が実質の主検証。`esp32/host_stub/` に IDF API の偽ヘッダを置いて
`esp32/main/*.c` をそのままホストでビルドし、I2S に書いたはずの int16 を
`csrc/` の一括版 `saan_synthesize` の出力と突き合わせる:

```
OK  [厳密] C 一括版 → int16 と 27136 sample **bit 完全一致**   （fp32 blob）
OK  [厳密] C 一括版 → int16 と 27136 sample **bit 完全一致**   （int8 blob）
```

これで **フレーム数とサンプル数の取り違え / 端数チャンクの落とし /
プリロールの順序 / int16 変換 / arena サイズ / 下限ガード** は全部潰れている。
残るのは IDF と実機の側だけ。

⚠️ ホスト stub のヒープ・スタック値は stub が 0 を返しているだけで、
**実機の値ではない**。

---

## 前提

| 項目 | 値 |
|---|---|
| ターゲット | **ESP32-S3**（内部 SRAM 512 KB / flash **8 MB 以上**。⚠️ **漢字対応は 16 MB 必須**） |
| ESP-IDF | **v5.5 で実測**。新 I2S ドライバ `driver/i2s_std.h` を使う |
| 音声出力 | I2S DAC（MAX98357A / PCM5102 など）22.05 kHz / 16 bit / mono。M5 構成は `M5.Speaker` |
| 実機で測った板 | **M5Stack CoreS3**（ESP32-S3 / 16 MB flash / Quad PSRAM 8 MB / native USB。**D-047**） |

✅ **ESP-IDF v5.5 でビルドが通り、実機で動くことを実測した**（M-83 以降）。それ以外の
マイナーバージョンは未検証。

⚠️ `main/CMakeLists.txt` の `REQUIRES` は **`driver` 1 本**にしてある。
**存在しないコンポーネント名を書くと IDF はエラーで止まる**ので、
「両方書いてどちらかに当てる」という逃げは効かない。`driver` は v5.x を通して
残っている傘コンポーネントで、I2S が `esp_driver_i2s` に分割された版でも
そこへ依存する形になっている — ✅ **v5.5 でビルドが通ったので確認済み**
（`components/driver` と `components/esp_driver_i2s` が両方実在する）。

---

## ビルドと書き込み

```bash
# 1. 重み blob と demo_ids.h を用意する（リポジトリのルートで）
uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt \
    --out csrc/student_i8.bin --golden csrc/golden_i8.bin \
    --int8 --golden-from-quantized --report csrc/export_i8.json
uv run python scripts/gen_demo_ids.py                                # esp32/main/demo_ids.h

# 2. 手元のゲートを通す（ESP-IDF 不要）
bash scripts/check_esp32_template.sh

# 3. ビルド（ESP-IDF v5.5 で実測。**ESP32-S3 なら W8A8 + PIE が既定** = D-048）
cd esp32
idf.py build

# 4. パーティション表・アプリ・重み blob をまとめて焼く
idf.py -p /dev/tty.usbmodemXXXX flash monitor
```

⚠️ **`idf.py set-target esp32s3` は打たない。** `sdkconfig.defaults` が
`CONFIG_IDF_TARGET="esp32s3"` を持っているので不要で、しかも打つと
`-DSAAN_MODEL_BLOB` の付かない configure が走って「blob が無い」で止まる（`TESTING.md` で実測）。

⚠️ **PIE の既定が 2026-09-03 に逆になった**（D-048）。`-DSAAN_ENABLE_PIE=1` と書いてある
古い手順は「既定と同じ」なので無害だが、**W8A32（PIE 無し）で測りたいときは
`-DSAAN_ENABLE_PIE=0` を明示する**。ESP32-S3 以外の板では自動的に W8A32 に落ちる。

⚠️ **既定は QIO / D-cache 64 B 行**（M-86 / M-84）。**QEMU は QIO を受け付けない**ので、
`-DSAAN_QEMU=1` を付けたビルドは `esp32/CMakeLists.txt` が `sdkconfig.qemu`（DIO）を自動で重ねる。
⚠️ **`esptool.py write_flash` に `--flash_mode qio` を渡してはいけない** — ヘッダが QIO になり
ROM ローダが読めずブートループになる（M-86 で実際に踏んだ）。`@flash_args` のまま焼けば
bootloader が起動後に `qio_mode: Enabling default flash chip QIO` で切り替える。

`idf.py flash` は `esptool_py_flash_to_partition(flash "model" ...)` によって
重み blob も `model` パーティションへ一緒に焼く（トップの `CMakeLists.txt`）。

---

## 漢字対応ビルド（K-7 / K-A。**既定は無効**）

端末に辞書を載せて、**漢字かな交じり文をそのまま**受け付ける構成。
QEMU で完走し（M-76）、**2026-09-02〜03 に CoreS3 の実機でも動いた**（M-83 / M-86 / M-90）。

```bash
# 1. 辞書 blob を作る（438,750 entries = D-044。13,702,320 B。数分かかる）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin

# 2-a. DevKit（16 MB flash。音は I2S DAC）
cd esp32
idf.py -B build_kanji -DSDKCONFIG=build_kanji/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 build
idf.py -B build_kanji -p /dev/tty.usbmodemXXXX flash monitor

# 2-b. M5Stack CoreS3（内蔵スピーカーと画面。⚠️ 辞書 13.7 MB 込みで焼くのに約 4 分）
cd esp32/boards/m5unified
idf.py -B build_m5k -DSDKCONFIG=build_m5k/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -DSAAN_KANJI=1 -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict.bin build
cd build_m5k && esptool.py --chip esp32s3 --port /dev/cu.usbmodem* --baud 921600 write_flash @flash_args
```

```
かな> 今日は良い天気ですね。          ← **`!` は要らない**（経路は端末が決める）
経路: 辞書
漢字 G2P: 33 B -> 形態素 7 個 / ids 53 個 / 25.69 ms
```

| | サイズ | 枠 |
|---|---:|---:|
| app（DevKit。⚠️ K-7 当時の 2026-08-31 のビルド） | 359,584 B | 2,097,152 B（17.1%） |
| app（M5 CoreS3。重みを `.rodata` に埋めた漢字ビルド。M-90） | 約 1.43 MB | 2,883,584 B（factory 2.75 MB） |
| model（int8 v2。**DevKit のみ**。M5 は `.rodata`） | 654,032 B | 786,432 B |
| **dict** | **13,702,320 B** | **13,828,096 B**（99.1%） |

⚠️ **この構成は 16 MB flash が要る**（`partitions_16mb.csv` /
`boards/m5unified/partitions.csv`。`dict` の offset は両方 `0x2D0000` にそろえてあるので、
**同じ辞書イメージをどちらの板にも焼ける**）。
⚠️ **ホストと違う音素は 0.63%**（**n=1,495**。M-99 §4。⚠️ **n=298 では 0.32% に見える** = C-059）。
差は**辞書の枝刈り**で、`上毛`（コーゲ）が `上`（ジョー）+ `毛` に切り直されるといった誤読になる。
移植そのものは正確（素性が一致した文でラベル差 0 件）。
⚠️ **音は聴いていない**（G32）。

### 8 MB flash の板（✅ **実機で喋った**。M-104 = QEMU / M-105 = 実機）

✅ **8 MB でも載る。** 接続行列を**行ごとアフィン uint8**（セクション `matrixa`）にし、
エントリを絞る。**画面 + スピーカーを積んだ M5Unified 版でも動く。**

⚠️ **枠は app の大きさで変わる。板ごとに表と辞書が違う**（M-105 §4 / §4b）:

| | `esp32/partitions_8mb_kanji.csv` | `esp32/boards/m5unified/partitions_8mb.csv` |
|---|---:|---:|
| 想定 | **DevKit**（M5Unified 無し） | **M5Stack 系**（AtomS3 など） |
| app | 1,021,248 B | **1,438,576 B** |
| factory | 1,179,648 | **1,507,328**（余裕 68,752 = 4.6%） |
| dict | 7,143,424 | **6,815,744** |
| entries | 228,000 | **213,000** |
| blob | 7,123,088（余り 20,336） | **6,797,056**（余り 18,688） |
| **音素の誤り**（n=1,495） | 1.01% | **1.09%** |

⚠️ **DevKit 用の辞書を M5 版に焼くと 241,808 B 入らない。** 逆も余るだけで動くが、
**entries が減るぶん読みが落ちる**。**表と辞書は必ずセットで扱うこと。**

```bash
# 1. 8 MB 用の辞書 blob（⚠️ **--out を必ず別名にする**。csrc/k1_dict.bin は 16 MB 用）
uv run python scripts/k1/k1_build_dict.py --entries 228000 --matrix affine \
    --out csrc/k1_dict_8mb.bin                      # 7,123,088 B

# 2. C リーダが生 int16 と一致するか（全 1,896,129 要素）
make -C csrc matrixa

# 3. ビルド（⚠️ **-DSAAN_MODEL_RODATA=1 が要る** — 8 MB の表に model 行が無い）
cd esp32 && idf.py -B build_k8 -DSDKCONFIG=build_k8/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji8mb" \
    -DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1 \
    -DSAAN_DICT_BLOB=$PWD/../csrc/k1_dict_8mb.bin build
```

| | サイズ | 枠 |
|---|---:|---:|
| app（重みを `.rodata` に埋めた 8 MB ビルド） | **1,021,248 B** | 1,179,648 B（86.6%） |
| **dict** | **7,123,088 B** | **7,143,424 B**（99.7%。**余り 20,336 B**） |

M5Unified 版はこちら（**スピーカーと画面が生きる**）:

```bash
uv run python scripts/k1/k1_build_dict.py --entries 213000 --matrix affine \
    --out csrc/k1_dict_8mb_m5.bin                   # 6,797,056 B
cd esp32/boards/m5unified && idf.py -B build_m58 -DSDKCONFIG=build_m58/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3;sdkconfig.8mb" \
    -DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1 \
    -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict_8mb_m5.bin build
```

**実機で測った値**（⚠️ **16 MB の CoreS3 に 8 MB の表を焼いたもの**。M-105）:

| | DevKit 構成 | M5Unified 構成 |
|---|---:|---:|
| 起動直後の内部 DRAM | free 104,112 B | free **132,031 B**（PSRAM を使うぶん多い） |
| 漢字 G2P（53 ids） | — | **21.58 ms** |
| **定常 xRT** | **0.445** | **0.448** |
| アンダーラン | **0 / 14** | **0 / 22・0 / 19** |
| checksum | `0xa69a7ebbb5ccb05f` | `0xa69a7ebb…`（⚠️ 先頭 8 桁のみ） |
| 音 | 出さない（`SAAN_SKIP_I2S=1`） | **スピーカー有効** |

⚠️ **M5 版は USB Serial/JTAG のログが溢れて checksum の後半が落ちる**
（画面とスピーカーのタスクが同時に書く）。**ファームの欠陥ではない。**
⚠️ **アフィン行列の速度代償は +0.3%**（entries を 438,750 に揃えて実機で測った。M-105 §3。
20.08 → 20.14 ms / **PCM は bit 一致**）。
⚠️ **PSRAM 無しでも動く**（QEMU は octal PSRAM を持たない = AtomS3 の条件）。
⚠️ **`esp_partition_mmap` で足りる**（7.1 MB < ROM 実装の 8 MB 制限。下の M5 の話は当たらない）。
⚠️ **既定ではない。** 16 MB で使う理由は無い（音素の誤りが 0.63% → 1.01% に悪化するだけ）。
⚠️ **余りが 0.3% しかない。** entries を変えたら必ず**作って `stat`** すること
（214,000 で作ったら 2,944 B 超過した。概算 22.25 B/entry に対し実測 21.74）。
⚠️ **8 MB flash のチップそのものでは測っていない。** ⚠️ **音を人が聴いていない。**

### ⚠️ M5 構成では `esp_partition_mmap` で辞書を貼れない — **`esp_mmu_map` を使う**

`boards/m5unified/sdkconfig.defaults` は `CONFIG_SPI_FLASH_ROM_IMPL=y` で、ESP32-S3 では
`spi_flash_mmap` が **ROM の実装**にリンクされ、IDF はそれに **128 ページ = 8 MB** しか渡さない。
辞書は `0xD30000 / 64 KB` = **211 ページ**なので、vaddr が余っていても `ESP_ERR_NO_MEM` になる。
`saan_dict.c` が `esp_mmu_map`（`esp_mm`。ROM 実装と無関係）へ切り替えて解決した — 実機では
**PSRAM 8 MB と同居して連続 vaddr 23,724,032 B が空いていた**（M-90）。

⚠️ **QEMU では PSRAM が使えない**（octal PSRAM を持っていない）ので、QEMU 向けのビルドでは
漢字経路の作業領域を**合成用 arena から切り出す**。実機の M5 構成では Open JTalk の一時ヒープが
PSRAM に落ちる（component の `-include oj_heap_psram.h`）ので、**内部 DRAM は 1 発話で 3 KB しか減らない**
（起動直後 132,039 B → 1 発話後 129,155 B。M-90）。

---

## どちらの blob を焼くか

| blob | サイズ | 状態 |
|---|---:|---|
| **`csrc/student_i8.bin` (int8, **blob v2**)** | **654,032 B** | **既定**。**W8A8 + PIE はこれでないと効かない**。⚠️ リリース v0.2.0 の資産は v1（643,936 B）で、S4 以降のコアは `SAAN_ERR_VERSION` で拒む。`uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt --int8 --out csrc/student_i8.bin --golden csrc/golden_i8.bin --golden-from-quantized --report csrc/export_i8.json` で作る |
| `csrc/student.bin` (fp32) | 2,249,792 B | 参照・デバッグ用。`-DSAAN_MODEL_BLOB=$PWD/../csrc/student.bin` |

⚠️ **blob は git 管理外。** クローンしただけでは存在しない。
[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の
`saanotts-jp-v3-int8.bin` を落とすか、自分で書き出すこと（上記）。

⚠️ **かつて fp32 を既定にしていたが、それは int8 経路が確定していない時期の
保守的な選択だった。** flash と D-cache では int8 のほうが 3.5 倍有利で、
**W8A8 + PIE は fp32 blob では 1 命令も効かない**（`saan_conv1d_w` が `W.f32` で
早期 return する）。**しかも何のエラーも出ない**ので、`main.c` が起動時に
`<name>.scale` の有無で検査して止める（int8 は 52 個 / fp32 は 0 個）。

⚠️ int8 blob は**量子化で波形が変わる**。Python 参照との SNR 26.08 dB は
M-39 の PTQ 実測（≥ 25 dB）と同水準で、**劣化ではなく想定どおり**。

---

## 設計上の判断（踏み抜きやすい順）

### 1. 重みは flash に置き、SRAM にコピーしない

`esp_partition_mmap()` で `model` パーティションを読む。blob は 654,032 B
（int8 v2）〜 2,249,792 B（fp32）で、512 KB の SRAM には入らない。
コアは blob を書き換えないので read-only で足りる。

置き場は 2 つあり、`saan_model.h` の `saan_model_open()` の実装を CMake で切り替える:

| | 既定（`saan_model.c`） | `-DSAAN_MODEL_RODATA=1`（`saan_model_rodata.c`） |
|---|---|---|
| 置き場 | `model` パーティションを `esp_partition_mmap` | `scripts/blob_to_header.py` が `const uint8_t[]`（aligned(16)）にして app の `.rodata` |
| app サイズ（W8A8+PIE / QEMU 構成） | 285,440 B | **928,832 B**（blob ぶん増える） |
| モデルだけの差し替え | できる（`model` だけ焼き直す） | **できない**（app ごと再ビルド） |
| いつ使うか | DevKit | **`boards/m5unified/`（M5Stack）**。この表には `model` パーティション自体が無い |

どちらも QEMU で同じ checksum を出す（A-2 時点で `0x04de91103a0e49f9`。S3 以降は `0xa69a7ebbb5ccb05f`。起動直後の内部 DRAM free も 72.8 KB で同じ）。
fp32 blob は `blob_to_header.py` が**ビルド時に拒否する**（回帰: `scripts/test_blob_to_header.py`）。

⚠️ **かつてここに「CoreS3 では `CONFIG_SPIRAM=y` だと mmap が `ESP_ERR_NO_MEM` で落ちた（第三者報告・未再現）」
と書いていたが、原因は PSRAM ではなかった**（M-90）。`CONFIG_SPI_FLASH_ROM_IMPL=y` のとき
`spi_flash_mmap` が ROM 実装になり、IDF が渡すプールが **128 ページ = 8 MB** しか無いのが理由で、
`esp_mmu_map` を使えば PSRAM 有効のまま 13.7 MB の辞書も貼れた（上の「漢字対応ビルド」）。
**重みを `.rodata` に置くという M5 側の判断自体は変えていない。**

### 2. `EMBED_FILES` を使わない — **アライメントが無保証**

⚠️ **以下は ESP-IDF の公式 cmake を読んで得た事実。** v5.5 は手元にあるので
ソースは確認できた。**mmap の実挙動のほうは実機で確かめた**（下記）。

`idf_component_register(EMBED_FILES ...)` は `target_add_binary_data()` を
**ALIGN 引数なしで**呼び、`data_file_embed_asm.cmake` は `DATA_ALIGNMENT` が
未定義なら `.balign` を一切出さない。つまり **blob 先頭が 4 バイト境界に落ちる
保証が無い**。

本コアは payload を `const float*` へ直接キャストする（テンソル offset は
全部 16 の倍数なので、ずれるとしたら base だけ）。Xtensa は非アラインの
4 バイトロードで `LoadStoreAlignmentCause` になる。しかも**リンク結果次第で
たまたま揃うことがあり、無関係な変更で突然落ちる**種類の事故になる。

パーティションなら `esp_partition_mmap()` が 64 KB 境界（MMU ページ）に丸めて
マップするので、`partitions.csv` で offset を 64 KB 境界に置けば 16 バイト境界も
自動的に満たす（将来 PIE を使うときの `SOC_SIMD_PREFERRED_DATA_ALIGNMENT` = 16 も
これで足りる）。✅ **丸めの挙動は実機で確認できた** — CoreS3 の DevKit 構成で
`重み OK: 183 tensors / base 0x…` が出て合成まで通った（M-83 / M-86）。
それでも `saan_model.c` は横着せず**取得直後に 16 バイト境界を検査して落とす**。

⚠️ **ただし `esp_partition_mmap` にはもう 1 つ落とし穴がある** — `CONFIG_SPI_FLASH_ROM_IMPL=y`
の板では ROM 実装が使われ、IDF が渡すプールが **128 ページ = 8 MB** しか無い。
13.7 MB の辞書はここで落ちるので `saan_dict.c` は `esp_mmu_map` を使う（M-90。
上の「漢字対応ビルド」）。**重みの 654,032 B はこの上限に当たらない。**

どうしても埋め込みたいなら、トップの `CMakeLists.txt` で
`target_add_binary_data(${project_name}.elf "../csrc/student.bin" BINARY ALIGN 16)`
と **ALIGN を明示**すること。

### 3. arena は 176 KB を `.bss` に静的確保する

⚠️ **`saan_stream_arena_needed()` の戻り値を使ってはいけない。**
n_ids=350 に対し **302,816 B (295.7 KB)** を返す緩い上限で、実測の **1.88 倍**ある。

⚠️ **高水位（`st.peak_used`）もそのまま確保量にしてはいけない。**
n_ids=350 で使うのは 160,224 B だが、**init が通る最小 arena は 160,768 B**。
ALIGN16 の切り上げと確保順の差でわずかに上回る。

176 KB (180,224 B) の根拠は**実測**（`make -C csrc arena`、ホスト。2026-09-03）:

| 項目 | 値 |
|---|---:|
| `saan_stream_arena_needed(350)`（緩い上限） | 302,816 B |
| n_ids=350 で init も pull も通る最小 arena | **160,768 B** |
| 176 KB で通った最大 n_ids | **450** |
| 最初に clean fail した n_ids | **496**（`SAAN_ERR_ARENA`。1000 まで**クラッシュ 0 件**） |
| 実機の used / peak（53 ids・M5 構成） | **157,360 B**（M-89） |

設計上限は 350 ids（D-017 の `max_spec_length=700` 相当）なので 19,456 B の余裕。

⚠️ **かつては 208 KB (212,992 B) だった。** T2（S9 = 捨てる出力を計算しない）と
T4（`cdel` 6 本をリング 1 本に / iSTFT の `re`・`im`・`frm` を `w_e` と共用）で
a.used が 177,536 → 160,224 B（350 ids・ホスト）になったので下げた。
実機では**内部 DRAM の空きが 99,987 → 136,407 B**（最大ブロック 55,296 → 90,112 B）になり、
**漢字経路の作業領域 144,640 B を載せてもまだ 35,584 B 残る**（M-89。
`check_esp32_template.sh` のゲート 10 が静的に検査する）。
⚠️ **漢字ビルドだけ 204 KB に落としていた分岐は消えた**（176 KB ならその差が要らない）。

### 4. `saan_stream_init` の欠陥（**修正済み**）に対する二重防御

✅ **この欠陥は `saan_arena` の粘着フラグで修正済み**（`csrc/saanotts.c`）。
`make -C csrc arena` は「**init が SAAN_OK を返した後に落ちた サイズ: 0 / 111 点**」で
通る。以下は**なぜその修正が要ったか**の記録として残す。

`saan_alloc` は失敗しても `used` を進めずに NULL を返す。`saan_stream_init` は
確保を約 25 回するのに**各グループの最後の 1 個しか NULL 検査していなかった**。
そのため「大きい確保（`o1539` 49,248 B など）だけ失敗し、後続の小さい確保は成功」
が起きると、**init が `SAAN_OK` を返したまま壊れた状態**になり、後で
`saan_stream_pull` の中で NULL 書き込みになった。手元では SEGV、ESP32 では
**StoreProhibited パニック = ログも出さずに再起動**。

当時の実測（`make -C csrc arena`）: n_ids=350 / arena 150〜260 KB を 1 KB 刻みで
走らせると、**175〜191 KB の 15 サイズでクラッシュ**した。
180 / 186 / 192 KB はたまたま clean fail するので、**刻みが粗いと見逃す**。

実際に入れた修正（**1 箇所で済んだ**）— `saan_arena` に粘着フラグを足す:

```c
/* saanotts.h */
typedef struct { uint8_t *buf; size_t size, used, peak; int failed; } saan_arena;

/* saanotts.c */
void *saan_alloc(saan_arena *a, size_t n) {
    size_t need = ALIGN16(n);
    if (a->failed) return NULL;                                   /* ← 追加 */
    if (need < n || a->used + need > a->size) { a->failed = 1; return NULL; }
    ...
}
/* saan_arena_init / saan_arena_reset で a->failed = 0 */
```

これで各グループ末尾の既存の NULL 検査が正しく効くようになる。

**アプリ側の二重防御**（`main.c`）: init が `SAAN_OK` を返した後に `a.used` を、
**コアが計算する期待値 `saan_stream_arena_used(n_ids)`** と突き合わせる。

⚠️ **かつてここは定数だった**（`SAAN_ARENA_USED_FLOOR = 192,960 B` = ホストで測った
「正しい a.used の最小 194,640 と黙って失敗した最大 191,280 の中点」）。**定数は移植できない。**
T2 で a.used が 16,512 B 下がって据え置けず、ホストで測り直した 180,064 B を置いたら
**QEMU が「a.used が 179,296 B しかない」と言って拒否した**（2026-09-03）。差 768 B は
`sizeof(struct saan_stream_impl)` のポインタ幅（ホスト 64 bit / Xtensa 32 bit）。
**ホストで測った定数はターゲットの a.used と一致しない。** 各ターゲットが自分の
`sizeof` から計算する関数にして解決した。ホスト側は `make -C csrc arena` の §5 が
「関数の値 == 実測 a.used」を**陽性対照つき**（期待値を ±16 B ずらすと NG）で守る。

⚠️ **上の「クラッシュ帯 175〜191 KB」は当時の確保順の話で、いまの 176 KB とは無関係**
（現行コードは 111 点すべてで silent-fail 0）。**コアの確保順が変わったら
`make -C csrc arena` で測り直すこと。**

### 5. タスクスタックは 16 KB を明示する

`saan_irfft_1024` は自動変数 `zr[512] + zi[512]`（float）だけで **4,096 B** 使う。
arm64 / clang -O2 での実フレームは **4,224 B**（`sub sp,sp,#0x1000` + `sub sp,sp,#0x20` +
レジスタ退避 96 B。`otool -tv` で実測。⚠️ Xtensa では別の値になる）。

`CONFIG_ESP_MAIN_TASK_STACK_SIZE` の既定は **3,584 B**
（✅ `components/esp_system/Kconfig` で確認済み）。
確かなのは「iSTFT 1 回で 4 KB 超を使う」ほうで、これだけで小さいスタックは
足りない。合成は `app_main` ではなく専用タスク（`SAAN_TASK_STACK = 16384`）で回す。

さらに `float chunk[SAAN_CHUNK * SAAN_HOP]`（8,192 B）と int16 変換バッファは
**`static` にしてスタックから外してある**。M-42 が arena だけ見て 200 KB を
切っていたのと同じ間違いを、今度は FreeRTOS のタスクスタックで繰り返さないこと。

✅ **実機で測れた**: M5 CoreS3 の漢字ビルドで `タスクスタック残り 10,996 B（16,384 B 中）`
= **使ったのは 5,388 B**（M-90 の生ログ `reports/m90_cores3/device_m5_kanji.log`）。
16 KB は余っているが、**この値は漢字経路を通した後の高水位**なので、
詰めるなら自分の構成でこの行を見てから決めること。

### 6. 鳴らし始める前にプリロールする

**最初の `saan_stream_pull` だけ定常の約 6 倍かかる。**
受容野 36 + iSTFT 2 = 38 フレームの warmup で内部の `step_chunk` が複数回走るため。

| | 初回 pull | 満チャンク pull | 比 |
|---|---:|---:|---:|
| ホスト（n_ids=350。`make -C csrc arena` §4、2026-09-03） | 9.78 ms | 1.52 ms | 6.4 |
| **実機 CoreS3**（53 ids・M5 の漢字ビルド。M-90 の生ログ） | **244.65 ms** | **41.47 ms** | 5.9 |

鳴らし始めた直後から合成を始めると、その 1 回ぶんが確実にアンダーランになる。
`saan_audio_preroll_push()` で 4 チャンク（`SAAN_AUDIO_PREROLL_SAMPLES` = 8,192 sample
= 371 ms の音声・16 KB）先に計算してから `saan_audio_start()` を呼ぶ。
実機ではこれで **鳴らし始めまで 384 ms / アンダーラン 0**（M-90）。

音声出力は `main/saan_audio.h` の抽象 API で、実装は 2 つ（`saan_i2s.c` = DevKit の
I2S 直叩き / `boards/m5unified/main/saan_audio_m5.cpp` = M5.Speaker）。
float → int16 と checksum は **`saan_pcm.c` が唯一の実装**で、どちらもそれを呼ぶ。

### 7. `-std=c99` は component に足さなくてよい（**もう地雷ではない**）

newlib の `M_PI` は `__STRICT_ANSI__` の下で隠れることがあり、実際に
`xtensa-esp32s3-elf-gcc -std=c99` で `saanotts.c` / `saanotts_stream.c` が落ちた（M-54）。
**macOS の libc では `-std=c99` でも `M_PI` が見えるので手元では再現しない**種類の失敗で、
**クロスコンパイルするまで一度も検出されなかった。**

✅ **`csrc/saanotts_internal.h` で `M_PI` を定義して解決済み**なので、厳密 `-std=c99` でも
5 ファイルすべてコンパイルが通る（実測。CI の Linux job がこれを毎回踏む）。
IDF 既定の **gnu17 のまま**にしてあるが、足しても壊れない。

同じ理由で `SAAN_FFT_DOUBLE` と `SAAN_USE_NAIVE_DFT` は**定義しない**
（S3 の FPU は単精度のみ。naive DFT は double の `cos`/`sin` を使うが、
`-O2` では未定義のままデッドコード除去されることを `nm -u` で確認済み）。

### 8. コアの 4 ファイル + `g2p.c` + `line.c` は csrc から**直接参照**する

`components/saanotts_core/CMakeLists.txt` が相対パスで `csrc/` を指す。
**コピーもシンボリックリンクもしない** —「同じものを 2 か所に書かない」。

⚠️ **4 ファイルすべてが要る。** `saanotts_int8.c` に `saan_w` /
`saan_conv1d_w` / `saan_dwconv1d_w` / `saan_act_scratch_needed` があり、
`saanotts.c` と `saanotts_stream.c` の両方が参照する。3 ファイルではリンクできない。

⚠️ `g2p.c` と `line.c` は**コアを 1 行も参照しない独立の翻訳単位**だが、
`main.c` が `saan_g2p()` を、`saan_console.c` が `saan_line_feed()` を呼ぶので
ここに並べる。`csrc/Makefile` の `CORE` には入っていない
（`golden_test` 等はリンクしない）。**非対称なのは意図的。**

---

## 実機で最初に測ること

`main.c` が起動時と終了時にログへ出す。**この順で見る。**

| # | 見るもの | ログ行 | 判断 |
|---|---|---|---|
| 1 | 起動するか | `重み OK: N tensors` | ここで止まるなら partition / アライメント |
| 2 | 入力がどちらの経路に行ったか | `経路: かな` / `経路: 辞書` | 読み違いを見たとき「辞書が悪いのか判定が悪いのか」の切り分けに要る |
| 3 | **アンダーラン** | `アンダーラン N / M チャンク` | **0 が期待値**（M-88 以降。それ以前は末尾 pull で 1 出ていた） |
| 4 | **満チャンク pull の xRT** | `定常 xRT = X（満チャンク pull の中央値 / 92.88 ms）` | 要件は **≤ 0.5**。CoreS3 の実測は **0.446**（M-90） |
| 5 | **発話全体の比** | `合成合計 ... / 音声 ... → 合成/音声 X` | **定義に依らない量。版どうしを比べるならこれ**（xRT の定義は T1 で変わった = C-054） |
| 6 | 初回 pull と満チャンク pull の比 | `初回 pull ... / 満チャンク pull（…）: 中央値 ...` | 実機で 5.9 倍。プリロール量の根拠 |
| 7 | **内部 DRAM の残り** | `起動直後: 内部 DRAM free ... / 最大ブロック ...` | 176 KB の arena を引いた後どれだけ残るか（M5 の漢字ビルドで 132,039 B） |
| 8 | タスクスタックの残り | `タスクスタック残り N B` | 実機で 10,996 / 16,384 B |
| 9 | int16 クリップ | `int16 クリップ N sample` | 0 でないなら出力が飽和している |
| 10 | **PCM の checksum** | `出力 PCM: 27136 sample / FNV-1a 0x...` | 期待値は `TESTING.md`。**これが一致して初めて「移植できた」** |
| 11 | 実サンプルレート | ログに出ない | ❌ **未測定**。オシロか長時間録音で測る（S3 に APLL 無し） |

⚠️ **速度の報告は `SAAN_PROFILE=0` のビルドで。** 計測自体にコストがある。
内訳を見るときだけ `-DSAAN_PROFILE=1` を付け、**前の版の表と 1 行ずつ並べる**（C-055）。

### ⚠️ 実時間には間に合った。ただし「良い音で」ではない

M-43 の外挿（移植可能 C / fp32 で 2.47 × RT）は**どちらの向きにも外れていた。**
実機の W8A32（PIE 無し）は **4.28〜4.62**（M-83。DIO の漢字ビルド）で外挿より**遅く**、
W8A8+PIE は第三者報告 **1.554** → 自分で測って **0.926**（M-82）→ T1〜T5 と S5b を入れて
**0.446**（M-90）と外挿より**速い**。**差は積和ではなく、量子化のソフト除算 / GELU の `erff` /
テンソル検索 / 重みのコピー**だった（M-80 / M-85）。
⚠️ **xRT の定義は途中で変わっている**（M-82 / M-84 は末尾 pull 込みの平均、M-87 以降は
満チャンク pull の中央値 = C-054）。**並べるときは「合成合計 / 音声長」を見ること。**

⚠️ **「実時間に間に合った」は「ちゃんと喋れている」ではない。**
アンダーラン 0 は「pull の計算時間 < 音声長」を数えているだけで、
**M5.Speaker の DMA の実挙動も、音そのものも別の話**。聴取（G32）は誰もやっていない。

---

## 入力経路

### 経路は端末が決める（**`!` は要らない**）

`saan_g2p_classify()`（`csrc/g2p.c`）が 1 行を 3 値に分ける:

| 判定 | 条件 | どうなるか |
|---|---|---|
| **かな** | 凍結テーブルのトークナイザが行末まで通る | `saan_g2p()` → 合成 |
| **辞書** | 通らず、中間表現のマーク（`[ ] # ° _ ^ $ ? ?! ?. ?~`）も無い | `SAAN_KANJI` ビルドなら端末で形態素解析（K-7）。無いビルドは**喋らずに理由を出す** |
| **拒否** | 通らないのにマークが混じっている | **喋らない**。位置と文字を出す |

⚠️ **判定は手書きの文字集合ではない** — 「トークナイザが通るか」そのもの。
ホスト側 `scripts/kana_g2p.py` の `classify_route()` と同じ規則で、一致は
`uv run python scripts/k1/kb_route_parity.py`（held-out 298 文 + その中間表現 298 行）が測る。
**片方だけ直すとそこが落ちる。**
⚠️ 「中間表現 + `。`」を黙って辞書経路に回すと**それらしい音が出てしまう**ので、拒否は残してある。
ℹ️ `!` を前置すると辞書経路に**強制**できる（試験用に残してある）。

### 端末側 G2P（かな）

`csrc/g2p.c` + `csrc/g2p_table.h`（自動生成、877 B）。`main.c` は起動時に
`SAAN_DEMO_INTERMEDIATE`（かな中間表現 44 B）を `saan_g2p()` に通し、
`kSaanDemoIds` の錨と照合する（**入力ではなく答え合わせ**。食い違ったら `ESP_LOGE` で止まる）。

- `make -C csrc g2p` — Python (`scripts/kana_g2p.py`) と **ids が整数として完全一致**
  （自己完結ベクタ 2,789 件）。`make -C csrc g2p-corpus` ではコーパス全行込みで **26,235 / 26,235**
- ホスト stub で 53 ids が錨と一致し、合成結果が C 一括版と **bit 完全一致**
- 錨を 1 要素、中間表現を 1 文字だけ変えると**どちらも exit 1 で落ちる**

✅ **実機のレイテンシも測れた**: CoreS3 で **44 B → 53 ids が 0.102 ms**
（M-90 の生ログ `reports/m90_cores3/device_m5_kanji.log` の `G2P:` 行）。
合成 1 チャンク（92.88 ms の音声）に対して**無視できる**。
参考: 手元（M4 Max / arm64 clang -O2）の定常値は 44 B → 53 ids で 0.34〜0.54 us。

### 端末側 G2P（漢字。`-DSAAN_KANJI=1`）

✅ **実機で動いた**（M-83 / M-86 / M-90）。CoreS3 で **33 B が 25.69〜27.85 ms、84 B が 63.45〜66.30 ms**
（音声長の 1.7〜2.3%。⚠️ 計画の目安「1% 未満」には届いていない = G28）。
⚠️ **PIE の有無に関係しない**（G2P は CPU 律速で MAC を含まない。M-86）。
⚠️ **辞書が無いビルドでは喋らない** — 黙ってかな経路に流し込むと読めない文字が落ちる。

### シリアルからの自由入力（M-63 / D-040）

**起動しても勝手には喋らない。** 錨との照合だけして `かな> ` プロンプトを出し、
1 行受けて合成する（`-DSAAN_BOOT_SPEAK=1` で起動時に 1 回喋る。M5 構成は既定で有効）。

```
かな> きょ][おわよ][いて][んきです°ね     ← 突き合わせ用の基準の 1 行
かな> 今日は良い天気ですね。               ← 漢字ビルドなら同じ PCM が出る（M-90）
```

- 行編集は `csrc/line.c`（369 B）。**UTF-8 対応の BS / CRLF / ESC の吸い込み / 溢れ検出**。
  ⚠️ **矢印キーの ESC [ A の `[` は中間表現では上昇アクセント。** 吸わないと
  カーソルを動かしただけで**エラーも出さずに抑揚が変わる**
- 入出力は `esp32/main/saan_console.c`。**UART0 でも USB Serial/JTAG でも動く**
  （`esp32/sdkconfig.usb_serial_jtag` で切り替え。⚠️ `rm -f sdkconfig` を忘れると黙って無視される）
- 上限は **350 ids**（arena の限界ではなく学習分布の上限。D-040）。
  511 B 超・350 ids 超は**切り詰めず行ごと拒否**
- ✅ **実機の USB Serial/JTAG で通した**（M-83 以降）。⚠️ **DevKit の UART0 では未**
- ⚠️ **300 B 級の 1 行を一度に貼ると途中で欠ける** — 行バッファではなく
  USB Serial/JTAG ドライバの RX リング（既定 256 B）が溢れる。64 B ずつ 30 ms 間隔なら通る（M-84 §5）

⚠️ **この経路で 2 回、音では気づけない欠陥を出した**（M-63 の §3）。
どちらも状態機械ではなく**呼び出し側**の読み違いだったので、
「エコーすべきか」を状態機械が返すよう API を変え、`make -C csrc line` の
**G9 / G10** で固定した。

### まだやっていないこと

- ~~**PIE (SIMD) カーネル**~~ — ✅ **入り、既定になった**（M-57 / M-58 / M-62 / **D-048**）。
  ESP32-S3 ではフラグ無しで W8A8 + PIE。実機で **0.446× RT**（M-90）
- ~~**`saan_tf` のポインタ解決**~~ — ✅ **消した**（S1）。init 時に一度だけ解決して
  構造体に持つ形になり、`make -C csrc prof --expect-no-lookup` が
  「pull の中でテンソル検索 0 回」をゲートにしている
- **実サンプルレートの誤差** — ❌ **未測定**（S3 に APLL が無い）
- **DevKit の I2S 直叩き（`saan_i2s.c`）** — ❌ **実機で 1 度も鳴らしていない**。
  鳴らせたのは M5.Speaker 経由だけ（M-90）
- **GPIO 配線** — `saan_i2s.c` の `SAAN_I2S_GPIO_*` は**根拠のない仮置き**。
  自分のボードに合わせて変えること
- **聴取（G32）** — ❌ **未。人が要る**

---

## ファイル

| パス | 役割 |
|---|---|
| `CMakeLists.txt` | トップ。`model` パーティションへの blob 焼き込みと、`-DSAAN_QEMU=1` のときの `sdkconfig.qemu` 重ねもここ |
| `partitions.csv` | カスタムパーティション表（8 MB。既定では blob が入らない） |
| `partitions_16mb.csv` | **漢字対応版**（16 MB。`dict` 13,828,096 B = D-042 の予算） |
| `sdkconfig.defaults` | ターゲット / 最適化 / スタック / パーティション / **QIO** / **D-cache 64 B 行** |
| `sdkconfig.qemu` | **QEMU 用の上書き**（flash を DIO に戻す。QEMU は QIO を受け付けない。M-86） |
| `sdkconfig.kanji` | 漢字対応ビルドの上書き（16 MB flash + 表の差し替え） |
| `sdkconfig.usb_serial_jtag` | コンソールを native USB に切り替える差分 |
| `components/saanotts_core/CMakeLists.txt` | `csrc/` の 4 ファイル + `g2p.c` + `line.c` を直接参照。**S3 なら PIE を既定で有効**（D-048）。`SAAN_KANJI` で K トラックの 4 ファイル + Open JTalk 34 ファイルが増える |
| `components/saanotts_core/saan_port_esp32.h` | 配置の注入点（`SAAN_HOT_DATA` → `DRAM_ATTR` など。erf 表を内部 DRAM に載せる） |
| `components/saanotts_core/oj_heap_psram.c` と `csrc/oj_heap_psram.h` | 取り込んだ Open JTalk の一時ヒープを **PSRAM** に向ける（`-include`。ソースは 1 バイトも変えない） |
| `main/main.c` | arena・プリロール・合成ループ・計測ログ・経路の自動判定・タッチ再生・`SAAN_BUFFERED`・`SAAN_PROFILE` の表 |
| `main/saan_model.h` | `saan_model_open()` の宣言。実装は 2 つ（下） |
| `main/saan_model.c` | 実装 1: flash の `model` パーティションを mmap（16 バイト境界を検査） |
| `main/saan_model_rodata.c` | 実装 2: `.rodata` に埋めた blob（`-DSAAN_MODEL_RODATA=1`。`cmake/saan_model_rodata.cmake` がヘッダを生成） |
| `main/saan_audio.h` | 音声出力の抽象 API（7 関数）。実装は `saan_i2s.c`（DevKit）と `boards/m5unified/main/saan_audio_m5.cpp` |
| `main/saan_i2s.c` | `saan_audio.h` の I2S 直叩き実装。⚠️ **実機で鳴らしたことが無い** |
| `main/saan_pcm.{h,c}` | float→int16 と FNV-1a / \|max\| / Σx²（**唯一の実装**） |
| `main/saan_ui.h` / `saan_ui_null.c` | 画面の抽象 API と「何もしない」実装（M5 実装は `boards/m5unified/main/saan_ui_m5.cpp`） |
| `main/saan_console.{h,c}` | シリアルからの 1 行入力（UART0 / USB Serial/JTAG）。poll + タッチ |
| `main/saan_dict.{h,c}` | **K-7: `dict` パーティションを貼る。** `CONFIG_SPI_FLASH_ROM_IMPL=y` の板では `esp_partition_mmap` が 8 MB しか貼れないので **`esp_mmu_map`** に切り替える（M-90） |
| `main/saan_kanji.{h,c}` | **K-7: 漢字文 → 生徒インデックス**（端末の全段） |
| `main/demo_ids.h` | **自動生成**（`scripts/gen_demo_ids.py`）。中間表現 + 錨 ids |
| `cmake/saan_model_rodata.cmake` | blob → `const uint8_t[]` ヘッダの生成（DevKit と boards で共有） |
| **`boards/m5unified/`** | **M5Stack 向けプロジェクト**（CoreS3 / Core2。`README.md` を読む）。**漢字辞書も載る** |
| `pie_probe/` | PIE の前提検証（A〜C）と実機マイクロベンチ（D / E）。`README.md` を読む |
| `host_stub/` | IDF API の偽ヘッダ + 実装。**デバイスには載らない** |
| [`TESTING.md`](TESTING.md) | **実機を持っている人向けの手順**（焼き方・打つ 1 行・報告してほしいログ） |

ビルド時のフラグ:

| フラグ | 既定 | 何が変わるか |
|---|---|---|
| `-DSAAN_ENABLE_PIE=0/1` | **ESP32-S3 では 1**（D-048）。それ以外は 0 | W8A8 + PIE（整数 SIMD）。⚠️ int8 blob が要る。**W8A32 で測るには 0 を明示する** |
| `-DSAAN_W8A8_NOPIE=1` | 無効 | ⚠️ **陰性対照専用**（W8A8 のままスカラ） |
| `-DSAAN_QEMU=1` | 無効 | I2S への書き込みを外し、**flash を DIO に戻す**（`sdkconfig.qemu`）。⚠️ **音は出ない** |
| `-DSAAN_KANJI=1` | 無効 | **端末で漢字を扱う**（K-7）。⚠️ 16 MB flash と辞書 13.7 MB が要る |
| `-DSAAN_DICT_BLOB=<絶対パス>` | `csrc/k1_dict.bin` | 焼く辞書 blob（`SAAN_KANJI=1` のとき） |
| `-DSAAN_BOOT_SPEAK=1` | 無効（M5 構成と非対話ビルドでは有効） | 起動時に錨の 1 文を喋る（突き合わせ用） |
| `-DSAAN_MODEL_RODATA=1` | 無効（**M5 構成では常に有効**） | 重みを app の `.rodata` に埋める（`model` パーティションを焼かない） |
| `-DSAAN_MODEL_BLOB=<絶対パス>` | `csrc/student_i8.bin` | 焼く / 埋める重み blob |
| `-DSAAN_BUFFERED=1` | 無効 | 1 発話ぶんを貯めてから鳴らす（途切れない。待ちは合成時間） |
| `-DSAAN_PROFILE=1` | 無効 | 段別プロファイル（CCOUNT）を発話後に出す。⚠️ **速度の報告には 0 で**（計測にコストがある） |
| `-DSAAN_OJ_PSRAM=0` | 1（`SAAN_KANJI` のとき） | ⚠️ **陽性対照**。Open JTalk の一時ヒープを素の `calloc` に戻す（内部 DRAM が減るのを見る） |
| `-DSAAN_ARENA_HEAP=1` | 無効 | arena をヒープ（PSRAM 優先）から取る。ESP32（Core2）向け。⚠️ 遅い |

検査スクリプト（リポジトリのルートから）:

```bash
bash scripts/check_esp32_template.sh    # 10 ゲート全部（§10 = 静的 arena が漢字経路を収めるか）
uv run python scripts/check_partitions.py
uv run python scripts/check_partitions.py --file esp32/boards/m5unified/partitions.csv --rodata
cmake -P scripts/check_cmake_syntax.cmake
make -C csrc arena                       # arena の実測（✅ 通る）
make -C csrc prof                        # 段別プロファイラ。`--expect-no-lookup` がゲート（S1）
```
