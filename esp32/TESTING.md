# ESP32-S3 実機テストのお願い

**速度はもう測れました。いま一番欲しいのは「聴いた感想」です。**

2026-09-02〜03 に M5Stack CoreS3（D-047）で自分の実機測定ができたので、
このページの主眼は「速度を測ってください」から**「音を聴いてください」と
「自分の板でも同じ数字が出るか」**に変わりました。

| | 何が知りたいか | 実機が要るか |
|---|---|---|
| **1** | ⭐ **音の感想**（読み間違い / 途切れ / 抑揚 / 金属的な尾） | ❌ **要りません**。[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` を聴くだけ |
| **2** | **別の ESP32-S3 の板でも同じ checksum と xRT が出るか** | ✅ 要る |
| **3** | **I2S DAC で実際に音が出るか**（`saan_i2s.c` は**まだ誰も鳴らしていない**。M5 の内蔵スピーカーでは鳴っている） | ✅ 要る（DAC も） |
| **4** | **実サンプルレートの誤差**（ESP32-S3 に APLL が無く 22,050 Hz は分数分周の近似） | ✅ 要る（オシロか長時間録音） |

**私の実測（M5Stack CoreS3 / W8A8+PIE / 漢字辞書込み。M-90）:**

| | 値 |
|---|---:|
| 満チャンク 1 pull の xRT | **0.446**（要件 RTF ≤ 0.5） |
| 発話全体（合成合計 / 音声長） | 0.541〜0.712 |
| アンダーラン | **0** |
| 鳴らし始めまで | 384 ms |
| 起動直後の内部 DRAM free | 132,039 B |
| 漢字 G2P | 33 B で 25.69 ms |

⚠️ **音は誰も聴いていません**（G32）。checksum は合っているので波形は設計どおりですが、
**「ちゃんと喋れているか」は聴くまで分かりません。**

**所要 15〜30 分。**⚠️ 最初に [ライセンス](../LICENSE-MODEL.md)を読んでください
（重みは MIT ではありません）。

✅ **この手順書は 2026-08-30 に、新規 clone + リリースから落とした blob で
1 行ずつなぞって直しました**（`set-target` で落ちる / USB 切り替えが黙って
無視される、の 2 件を修正）。それでも詰まったら
[Issue](https://github.com/ayutaz/sanoTTS-jp/issues/new) に投げてください。

---

## 用意するもの

| | |
|---|---|
| **ESP32-S3 ボード** | 内部 SRAM 512 KB / flash **8 MB 以上**。PSRAM は不要。⚠️ **漢字版も試すなら 16 MB（N16R8 / CoreS3 など）** |
| **I2S DAC**（任意） | MAX98357A / PCM5102 など。⚠️ **無くても速度は測れます**。M5Stack なら内蔵スピーカーで鳴ります |
| ESP-IDF | **v5.5 で動作確認済み**。⚠️ **焼くだけなら不要**（下記「A. 焼くだけ」）|

⚠️ **音を出さなくてもこのテストは成立します。** 速度と checksum は I2S 無しでも測れます。
DAC が無い方は下の **「DAC が無い場合」** へ。

⚠️ **ESP32（S3 でない）でも動きますが PIE が無いので遅いです**（W8A32）。
M5Stack Core2 / Basic はこちら。

---

## 手順

**3 通りあります。M5Stack をお持ちなら M が一番早いです。**

---

## A. 焼くだけ（ESP-IDF 不要）

**焼けるイメージは 6 本あります。**
**全部 [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest)（v0.3.0）に入っています。**

✅ **v0.3.0 の配布イメージは S1〜S5b / T1〜T5 込みの現行コア**です。下の「期待値」の
**新しい値**の側になります。⚠️ **v0.2.0 以前を焼いてある板は入れ替えてください**
（`!` の前置が要り、コンソールが UART0 で、int8 blob も現行コアが拒む v1 です）。

⚠️ **コンソールの口が 2 通りあります。** CoreS3 / AtomS3 のように **USB-シリアル変換を
持たない板（native USB）は `-usbjtag` の方**を焼いてください。UART0 版を焼くと
**起動はするのに `かな>` に何を打っても届きません**（M-83 §1 で実際に踏みました）。

| イメージ | flash | 入力 | コンソール |
|---|---|---|---|
| **`m5-cores3-firmware-kanji-16mb.bin`** | **16 MB 必須** | **漢字も** | USB Serial/JTAG。**内蔵スピーカーで鳴ります**（M-90） |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | **16 MB 必須** | **漢字も** | USB Serial/JTAG |
| `esp32s3-firmware-kanji-16mb.bin` | **16 MB 必須** | **漢字も** | UART0 |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | 8 MB 以上 | かな | USB Serial/JTAG |
| **`esp32s3-firmware-w8a8-pie.bin`** | 8 MB 以上 | かな | UART0 |
| `esp32s3-firmware-w8a32.bin` | 8 MB 以上 | かな | UART0。**PIE の比較対照**（遅い） |

**漢字版でもかな入力は通ります**ので、16 MB の板なら漢字版 1 本で足ります。
⚠️ **`!` の前置は要りません**（v0.3.0 から端末が経路を自分で判定します）。
残してあるのは辞書経路を強制する試験用です。

```bash
pip install esptool

# M5Stack CoreS3 / スタックチャン（⚠️ 先に元のファームを退避してください）
esptool.py --chip esp32s3 -p <ポート> read_flash 0 0x1000000 backup.bin
esptool.py --chip esp32s3 -p <ポート> write_flash 0x0 m5-cores3-firmware-kanji-16mb.bin

# 8 MB ボード（かな入力・USB-シリアル変換あり）
esptool.py --chip esp32s3 -p <ポート> write_flash 0x0 esp32s3-firmware-w8a8-pie.bin

# 16 MB ボード（漢字も読める・native USB）
esptool.py --chip esp32s3 -p <ポート> write_flash 0x0 esp32s3-firmware-kanji-16mb-usbjtag.bin
```

いずれも bootloader + パーティション表 + アプリ + **重み blob**（漢字版はさらに**辞書**）が
全部入っています。⚠️ **`--flash_mode qio` を足さないでください**（ヘッダが QIO になると
ROM ローダが読めずブートループになります。M-86 で実際に踏みました）。

そのあと **115200 baud** のシリアル端末で開きます（`screen /dev/tty.usbmodemXXXX 115200`
/ `minicom` / PuTTY など。ESP-IDF があるなら `idf.py monitor` でも可）。

⚠️ **配布イメージ（v0.1.1 / v0.2.0）はコンソール入力が UART0 です。** USB-UART ブリッジの無い板
（M5Stack CoreS3 / AtomS3 など native USB だけの板）では、**ログは USB に出るのに `かな>` に打った文が
届きません**（2026-09-02 に CoreS3 で実測。起動と重み・辞書の mmap までは通る。M-83）。その板では
「M. M5Stack で試す」か、`sdkconfig.usb_serial_jtag` を重ねてソースからビルドしてください。
次のリリースでは USB Serial/JTAG 入力のイメージも配ります。

⚠️ **I2S の GPIO は BCLK=5 / WS=6 / DOUT=7 の仮置き**です。
**DAC を鳴らしたいならソースから作り直してください**（B へ）。
**速度の測定だけなら DAC は不要**なので、この firmware のままで足ります。

⚠️ **漢字版（v0.2.0）は PIE 無効でビルドしてあります**
（`build_rel_kanji` の compile_commands に `SAAN_PIE` が無いことで確認）。
**PIE の比較には使えません。** 漢字版で見たいのは「**G2P に何 ms かかるか**」の方です。

そのまま [「喋らせる」](#喋らせる対話入力)へ進んでください。

---

## B. ソースからビルドする場合（DevKit）

### 1. モデルを取る

**リポジトリをクローンしただけでは重みは入っていません**（git 管理外）。

⚠️ **リリースの `saanotts-jp-v3-int8.bin`（643,936 B）は形式 v1 で、S4（2026-09-02）以降の
ソースでは起動時に `SAAN_ERR_VERSION` で止まります。** v2 の資産は次のリリースで上げます。
それまでは [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の
**`saanotts-jp-v3-stage4.pt`** から自分で書き出してください（要 numpy + torch。
piper-plus のクローンは要りません = [`../README.md`](../README.md) の「最小セットアップ」）:

```bash
# ⚠️ **リポジトリのルートで打つこと**（スクリプトが src/ を相対で読む）
uv run --no-project python scripts/export_c_weights.py \
    --ckpt /絶対パス/saanotts-jp-v3-stage4.pt --int8 \
    --out csrc/student_i8.bin --golden csrc/golden_i8.bin \
    --golden-from-quantized --report csrc/export_i8.json     # → 654,032 B（blob v2）
```

⚠️ `saanotts-jp-v3-stage4.pt` は **PyTorch 用**で、ESP32 に直接は焼けません
（上の 1 本で `.bin` に変換します）。

### 2. 配線（音を出す場合のみ）

⚠️ **`main/saan_i2s.c` の `SAAN_I2S_GPIO_*` は根拠のない仮置き**です。
自分のボードに合わせて書き換えてください。
⚠️ **この I2S 直叩き経路は実機でまだ 1 度も鳴らしていません。**
鳴った / 鳴らなかったのどちらでも、報告そのものが価値があります。

### 3. ビルドして焼く

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git
cd sanoTTS-jp/esp32

# ★ これ 1 本。**ESP32-S3 なら W8A8 + PIE は既定で有効**（D-048）
idf.py -DSAAN_MODEL_BLOB=/絶対パス/student_i8.bin build

idf.py -p <ポート> flash monitor
```

⚠️ **`-DSAAN_ENABLE_PIE=1` はもう要りません**（2026-09-03 に既定が逆になりました = D-048）。
**W8A32（PIE 無し）と比べたいときだけ `-DSAAN_ENABLE_PIE=0` を明示**してください。
起動ログに `W8A8 + PIE 有効 / int8 blob を確認` が出れば有効です。

⚠️ **`idf.py set-target esp32s3` は打たないでください。** `sdkconfig.defaults` が
`CONFIG_IDF_TARGET="esp32s3"` を持っているので**不要**で、しかも打つと
`-DSAAN_MODEL_BLOB` が付いていないぶん **blob が見つからず configure に失敗します**
（エラー自体は「blob が無い」と正しく出ますが、1 行目でつまずくので混乱します。実測）。

⚠️ **ポート名**: macOS は `/dev/tty.usbmodem…` か `/dev/cu.usbserial…`、
Linux は `/dev/ttyUSB0` か `/dev/ttyACM0`。`idf.py -p` を省くと自動検出を試みます。

`idf.py flash` がアプリと**重み blob を両方**焼きます（blob は `model`
パーティション、3 MB 確保してあります）。

⚠️ **既定は flash QIO / D-cache 64 B 行**です（M-86 / M-84）。同じコードでも
DIO だと **15% 遅く**なるので、起動ログに `qio_mode: Enabling default flash chip QIO` が
出ているか確認してください。

#### DAC が無い場合

**そのままで測れるはずです。** I2S は DAC がつながっていなくても DMA が回るので、
配線しなくても速度は出ます（⚠️ **これ自体は未検証**。DevKit を持っていないため）。

**もし I2S で止まったら**、書き込みだけ外す逃げ道があります
（**変換と合成は通る**ので測定値は有効）:

```bash
idf.py -DSAAN_QEMU=1 -DSAAN_MODEL_BLOB=/絶対パス/student_i8.bin build
```

⚠️ 名前は `QEMU` ですが、やっているのは **`i2s_channel_write` を no-op にすること**と
**flash を DIO に戻すこと**（`sdkconfig.qemu`。QEMU が QIO を受け付けないため）です。
⚠️ **速度を測る目的でこれを付けないでください** — DIO は QIO より 15% 遅く出ます。
**止まったのが I2S かどうか**自体は、有益な報告になります。
---

## M. M5Stack（スタックチャンの中身）で試す

**M5Stack CoreS3 / Core2 / Basic なら配線も DAC も要りません**（内蔵スピーカーと画面を使う）。
ソースからビルドします（ESP-IDF v5.5 が要る。初回は M5Unified を Component Registry から取るのでネットワークも）。

✅ **私はこの経路で測りました**（CoreS3。M-82 / M-84 / M-87 / M-88 / M-89 / M-90）。
**漢字辞書も同じ 1 本のファームに載ります**（M-90）。

### 板の見分け方

| 外見 | 板 | チップ | この手順で分かること |
|---|---|---|---|
| 画面の上にカメラ穴、下に物理ボタン無し | **CoreS3 / CoreS3 SE** | ESP32-S3 | **速度も漢字も全部** |
| 画面の下に丸い印が 3 つ（タッチ）、物理ボタン無し | Core2 | ESP32 | 喋ること + W8A32 の checksum（PIE 無し。**遅い**） |
| 画面の下に物理ボタン 3 つ | Basic / Gray / Fire | ESP32 | 同上（ボタン A で再生） |

確実なのは USB で繋いで `esptool.py chip_id`（`Chip is ESP32-S3` か `ESP32-D0WD` か）。

⚠️ **焼く前に元の firmware を丸ごと吸い出しておくこと**（スタックチャンなら戻せなくなります）:

```bash
esptool.py --chip esp32s3 -p /dev/cu.usbmodem* read_flash 0 0x1000000 ~/stackchan_backup_16MB.bin
```

### ビルドして焼く

```bash
. ~/esp/esp-idf/export.sh
cd esp32/boards/m5unified

# CoreS3（ESP32-S3）: W8A8 + PIE は**既定で有効**（D-048）
idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -DSAAN_MODEL_BLOB=<int8 blob v2 の絶対パス> build
idf.py -B build_cores3 -p /dev/cu.usbmodem* flash monitor       # 終了は Ctrl+]

# CoreS3 + **漢字辞書**（16 MB を使い切る。⚠️ 焼くのに約 4 分）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin      # リポジトリのルートで
idf.py -B build_m5k -DSDKCONFIG=build_m5k/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -DSAAN_KANJI=1 -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict.bin build
cd build_m5k && esptool.py --chip esp32s3 -p /dev/cu.usbmodem* --baud 921600 write_flash @flash_args

# Core2 / Basic（ESP32）: PIE が無いので自動的に W8A32。⚠️ arena が PSRAM に行くので速度の測定には使えない
idf.py -B build_core2 -DSDKCONFIG=build_core2/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" \
    -DSAAN_MODEL_BLOB=<int8 blob v2 の絶対パス> build
idf.py -B build_core2 -p /dev/cu.usbserial* flash monitor
```

⚠️ **`esptool.py write_flash` に `--flash_mode qio` を渡さないでください**（ブートループ。M-86）。
`@flash_args` のまま焼けば bootloader が起動後に QIO へ切り替えます。

起動すると画面に「今日は良い天気ですね。」を出して 1 回喋り、下段に `xRT` と途切れ回数が出ます。
**画面をタッチ（Basic はボタン A）で同じ文をもう一度**、シリアルの `かな> ` に打てばその文を喋ります。
途切れない再生が要るなら `-DSAAN_BUFFERED=1`（貯めてから鳴らす。待ちは合成時間）。

### 期待値（CoreS3 / 起動時の 1 文）

⚠️ **ソースからビルドしたとき**（S3 = GELU の erf 近似を入れた 2026-09-02 以降）の値です。
配布イメージ（v0.2.0 まで）は旧コアなので下の「報告してほしいもの」の旧値になります。

| 構成 | `出力 PCM ... FNV-1a` | `\|max\|` | `Σx²` |
|---|---|---:|---:|
| W8A8 + PIE（**既定**） | **`0xa69a7ebbb5ccb05f`** | 9627 | 74,264,237,672 |
| W8A32（`-DSAAN_ENABLE_PIE=0`。Core2 もこちら） | **`0xe4b645c30835d42d`** | 9529 | 74,155,591,505 |

一致すれば G2P → 合成 → int16 まで QEMU の記録と bit 一致しています。**音が鳴っただけでは証拠になりません。**

**私の実測（CoreS3 / W8A8+PIE / 漢字辞書込み。M-90）:**

```
満チャンク pull（n=8、初回を除く 12 回）: 中央値 41.47 ms / 平均 42.76 ms
定常 xRT = 0.447（満チャンク pull の中央値 / 92.88 ms）
アンダーラン 0 / 14 チャンク
出力 PCM: 27136 sample / FNV-1a 0xa69a7ebbb5ccb05f
起動直後: 内部 DRAM free 132039 B / 最大ブロック 86016 B
```

### 報告してほしいもの（M5）

下の「報告してほしいもの」に加えて、**`-DSAAN_PROFILE=1` で焼き直したときの表**
（`----- 段別プロファイル -----` から `1 step = ... cyc` まで）を丸ごと。
1 チャンクの時間がどの段（QUANT / GELU / MAC / TOKEN / DW …）に行っているかが、
実機でしか取れない数字です（[`../docs/research/s1-m5-cores3-speed.md`](../docs/research/s1-m5-cores3-speed.md)）。
⚠️ **速度の報告は `SAAN_PROFILE=0` のビルドで。** 計測自体にコストがあります。

---

## 喋らせる（対話入力）

**起動しても勝手には喋りません**（M5 構成は既定で 1 回喋ります）。
錨との照合だけして `かな> ` プロンプトが出ます。**1 行打って Enter** で喋ります。

**`!` は要りません。** 端末が `saan_g2p_classify()` で経路を決めます:

| 打った行 | 経路 | どうなるか |
|---|---|---|
| `きょ][おわよ][いて][んきです°ね` | **かな** | そのまま合成 |
| `今日は良い天気ですね。` / `コンニチハ` | **辞書** | 漢字対応ビルドなら端末が形態素解析して合成。**そうでなければ喋らずに理由を出す** |
| `きょ][おわ。` | **拒否** | 中間表現の記号が混じっているのに読めない → **喋りません**（位置と文字を出す） |

⚠️ **`idf.py monitor` の中でそのまま打てます**（貼り付けも可）。
抜けるのは **Ctrl-]**。⚠️ ターミナルが UTF-8 で送る設定になっていること。
ℹ️ 行頭の `!` は**辞書経路への強制**として残してあります（試験用）。

### ★ まずこの 1 行を打ってください

```
かな> きょ][おわよ][いて][んきです°ね
```

**これが基準の発話です**（「今日は良い天気ですね。」）。
下の「報告してほしいもの」の期待値は**すべてこの 1 行を打ったときの値**です。

**漢字対応ビルドなら、次の 2 行が同じ checksum になるはずです**（M-90 で実測）:

```
かな> きょ][おわよ][いて][んきです°ね     → 0xa69a7ebbb5ccb05f
かな> 今日は良い天気ですね。              → 0xa69a7ebbb5ccb05f（同値）
```

⚠️ **打たずに測りたい場合**（コンソールに触れない・自動で流したい）は
`-DSAAN_BOOT_SPEAK=1` を足すと、起動時に同じ文を 1 回喋ります（値も同じ）。

### 記号

| 記号 | 意味 |
|---|---|
| `[` | アクセントの上昇 |
| `]` | 下降核 |
| `#` | 句境界 |
| `°` | 無声化（直前が平母音のときだけ効く） |
| `?` `?!` `?.` `?~` | 疑問の終端 |

⚠️ **辞書を積んでいないビルドは漢字・カタカナ・句読点を喋れません**（`。`も不可）。
その場合は**喋らずに理由を出します**（黙って無音にはしません）。
ホスト側で中間表現に直してから打ってください:

```bash
uv run python scripts/to_intermediate.py "電源を入れてください。"
# → で[んげんおい[れてくださ]い
```

（この 1 行を端末に貼り付ける。`--ids` を足すと期待 ids も出るので、
端末のログの `G2P: ... -> N ids` と突き合わせられます。）

⚠️ **`to_intermediate.py` はホスト側で、piper-plus のクローンが要ります**
（OpenJTalk。手順は [`../README.md`](../README.md) の「フルセットアップ」）。
**このテストには必須ではありません** — 上の基準の 1 行を打つだけなら不要です。

⚠️ **アクセント記号を省いても喋りますが、抑揚は平板になります。**

### ⚠️ どちらの USB ポートに挿すか

ESP32-S3 の DevKit には USB ポートが 2 つあります。
**既定のコンソールは UART0** なので、「UART」と書かれた側
（CP2102 などの USB-シリアル変換）に挿してください。
**「USB」と書かれた native 側に挿すと、ログは見えるのに入力が届きません。**

⚠️ **M5Stack CoreS3 / AtomS3 には UART ブリッジがありません。** `boards/m5unified` の
`sdkconfig.cores3` は最初から USB Serial/JTAG なので、そちらは何もしなくて構いません。

DevKit を native 側 1 本で済ませたい場合はこちら:

```bash
rm -f sdkconfig          # ⚠️ **これが要る。理由は下**
idf.py -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.usb_serial_jtag" \
       -DSAAN_MODEL_BLOB=<int8 blob v2> build
```

⚠️ **`rm -f sdkconfig` を忘れると、この指定は黙って無視されます。**
`SDKCONFIG_DEFAULTS` は `sdkconfig` を**新規に作るときだけ**効くので、既に一度
ビルドしていると**ビルドは成功するのにコンソールは UART0 のまま**になります。
「切り替えたのに入力が届かない」という、原因の分からない形で詰まります（実測）。

切り替わったかは必ず確認してください:

```bash
grep CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y sdkconfig    # 出れば OK
```

⚠️ **長い行を一度に貼らないでください。** 300 B 級を一度に送ると
USB Serial/JTAG ドライバの RX リング（既定 256 B）が溢れて**途中で欠けます**。
64 B ずつ 30 ms 間隔なら通ります（M-84 §5）。

### 編集キー

| キー | 動作 |
|---|---|
| BS / DEL | 1 **文字**消す（ひらがな 3 バイトをまとめて） |
| Ctrl-U / Ctrl-C | 行を捨てる |
| ↑ ↓ ← → | 何も起きません（**`[` を挿入しないよう吸っています**） |

⚠️ 長すぎる入力（511 B 超、または 350 ids 超）は**切り詰めず行ごと拒否**します。
先頭だけ喋ると「端末とホストで同じ列」という入力仕様が黙って崩れるためです。
---

## 報告してほしいもの

**[Issue](https://github.com/ayutaz/sanoTTS-jp/issues/new) に
`idf.py monitor` のログを丸ごと**貼ってもらえれば十分です
（ボード名と ESP-IDF のバージョンも書いてもらえると助かります）。
特に見たいのはこの 5 行:

```
I (xxx) saanotts: 定常 xRT = ?.???（満チャンク pull の中央値 / 92.88 ms）
I (xxx) saanotts: 合成合計 ???.?? ms ... / 音声 ?.??? s → 合成/音声 ?.???
I (xxx) saanotts: アンダーラン N / M チャンク
I (xxx) saanotts: 出力 PCM: 27136 sample / FNV-1a 0x????????????????
I (xxx) saanotts: 起動直後: 内部 DRAM free ????? B / 最大ブロック ????? B
```

### 期待値と、それぞれが何を意味するか

| ログ | 期待 | 外れたときの意味 |
|---|---|---|
| **`定常 xRT`** | **≤ 0.5**（CoreS3 の実測 0.446） | 要件は RTF ≤ 0.5。1.0 を超えたら実時間合成に間に合っていない。⚠️ **この定義は満チャンク 1 pull / そのチャンクの音声長**（C-054） |
| **`合成/音声`** | 0.54〜0.72 | **定義に依らない量。** 版どうしを比べるならこれ。warmup 38 フレームが初回 pull に乗るのでまだ 0.5 を超える |
| `アンダーラン` | **0** | 0 でなければ pull の計算が音声長に追いついていないか、I2S 側の問題 |
| **`出力 PCM ... FNV-1a`** | ソースから（S3 以降）: **`0xa69a7ebbb5ccb05f`**（W8A8+PIE）/ **`0xe4b645c30835d42d`**（W8A32）<br>配布イメージ（v0.2.0 まで）: `0x04de91103a0e49f9` / `0x78c209af06affc01` | 上の**基準の 1 行を打ったとき**の値。⚠️ 一致しなくても即バグではない（下記） |
| `内部 DRAM free` | 数十 KB〜130 KB 残る | 0 に近ければ arena か PSRAM 設定を見直す。CoreS3 の実測は 132,039 B |
| `W8A8 + PIE 有効 / int8 blob を確認` | **出ること**（ESP32-S3 なら既定） | 出なければ W8A32 でビルドされています（`-DSAAN_ENABLE_PIE=0` を付けていないか確認） |
| `qio_mode: Enabling default flash chip QIO` | **出ること** | 出ないと DIO のままで **15% 遅い**（M-86） |

⚠️ **FNV-1a が一致しなくても慌てないでください。** QEMU と実機で
コンパイラが同じなら一致するはずですが、最適化やライブラリ差で float の丸めが
変われば変わります。**そのときは同じ行の `|max| 9627 / Σx² 74264237672`（W8A8+PIE）を
見てください** — これが近ければ丸め差、大きく外れていれば本当のバグです。

### PIE がどれだけ効くかを見たいなら

**ESP32-S3 では W8A8 + PIE が既定**なので、**比較対照のほうを明示**します:

```bash
# A: W8A32（PIE 無し / 移植可能 C）
idf.py -B build_a -DSDKCONFIG=build_a/sdkconfig -DSAAN_ENABLE_PIE=0 \
       -DSAAN_MODEL_BLOB=<int8 blob v2> build
idf.py -B build_a -DSDKCONFIG=build_a/sdkconfig -p <ポート> flash monitor

# B: W8A8 + PIE（既定）
idf.py -B build_b -DSDKCONFIG=build_b/sdkconfig \
       -DSAAN_MODEL_BLOB=<int8 blob v2> build
idf.py -B build_b -DSDKCONFIG=build_b/sdkconfig -p <ポート> flash monitor
```

⚠️ **`-DSDKCONFIG=` を build ディレクトリごとに分けてください。** 省くと
両方が `esp32/sdkconfig` を共有し、**片方の設定がもう片方に漏れます**
（同じ理由で上の USB 切り替えも壊れます）。

私の実測（CoreS3）では W8A32 が **4.28〜4.62**（M-83。漢字ビルド / DIO）、
W8A8 + PIE が **0.446**（M-90）。**PIE 無しでは実時間に間に合いません**（これが D-048 の根拠）。

### さらに余力があれば: **漢字対応ビルド**（16 MB ボードの方だけ）

**端末が漢字かな交じり文をそのまま読む**構成です。
✅ **2026-09-02〜03 に CoreS3 の実機で動きました**（M-83 / M-86 / M-90）。

⚠️ **N16R8 / CoreS3 など 16 MB flash が要ります。** 8 MB のボードでは辞書 13.7 MB が入りません。

ℹ️ **焼くだけで良いなら [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) に
現行コードのイメージがあります**（v0.3.0。`!` の前置は要りません）。
**M5Stack なら `m5-cores3-firmware-kanji-16mb.bin`**、DevKit なら
`esp32s3-firmware-kanji-16mb-usbjtag.bin`（native USB）か `…-kanji-16mb.bin`（UART0）です。

```bash
# リポジトリのルートで辞書を作る（13,702,320 B。数分かかります）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin

cd esp32
idf.py -B build_kanji -DSDKCONFIG=build_kanji/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 -DSAAN_MODEL_BLOB=<int8 blob v2> build
idf.py -B build_kanji -p <ポート> flash monitor      # ⚠️ 辞書 13.7 MB 込みで約 4 分
```

```
かな> 今日は良い天気ですね。      ← **`!` は要りません**（最新のソースから作った場合）
```

見たいログ:

```
I (xxx) saan_dict: 辞書 OK: 見出し語 355768 / エントリ 438750 / 行列 1377x1377
I (xxx) saanotts: 経路: 辞書
I (xxx) saanotts: 漢字 G2P: 33 B -> 形態素 7 個 / ids 53 個 / ??.?? ms
I (xxx) saanotts: 出力 PCM: 27136 sample / FNV-1a 0x????????????????
```

| 見るところ | 期待 | 意味 |
|---|---|---|
| `辞書 OK` の行 | 出ること | mmap が通った。出なければ 16 MB 版の表を焼けていない |
| `形態素 N 個` | 7 | ホストと同じ切り方になった |
| 漢字 G2P | **25.69〜27.85 ms**（33 B） | 私の実測（M-83 / M-86 / M-90）。文長にほぼ線形（約 0.8 ms/B） |
| **FNV-1a** | ソースから（S3 以降）: **`0xa69a7ebbb5ccb05f`**（PIE）/ `0xe4b645c30835d42d`（W8A32）<br>配布イメージ v0.2.0: `0x78c209af06affc01` | **`きょ][おわよ][いて][んきです°ね` と打ったときと同じ値になるはず**（M-86 / M-90 で実測） |

⚠️ **ホストと違う読みになる文が普通にあります**（**音素の 0.63%**。n=1,495。M-99 §4。
⚠️ n=298 で測ると 0.32% に見える = C-059）。
端末の辞書は枝刈りしてあるので、`上毛`（コーゲ）が `上`（ジョー）+ `毛` のように
切り直されます。**地名と固有名詞で起きやすい**ので、そういう文で崩れ方を見てもらえると
とても助かります。

⚠️ **音を聴いた人が 1 人もいません。** 漢字経路とかな経路で同じ文を鳴らして、
**差が聞き取れるか**を教えてもらえるのが一番価値があります（checksum は一致するので、
**理屈のうえでは差はゼロ**です。それが耳でも確かめられるか）。
---

## 先に知っておいてほしいこと

| | |
|---|---|
| **起動しても勝手には喋りません**（DevKit） | `かな> ` に 1 行打つと喋ります。起動時に 1 回喋らせたいなら `-DSAAN_BOOT_SPEAK=1`。**M5 構成は既定で喋ります** |
| **`!` はもう要りません** | 経路（かな / 辞書 / 拒否）は端末が自分で決めます。⚠️ **配布イメージ v0.2.0 は古いコア**なので、あちらは `!` が要ります |
| **辞書を積まないビルドでは漢字を扱えません** | 漢字→かなはホスト側（OpenJTalk）。端末が受け取るのは「ひらがな + アクセント記号」です。⚠️ **辞書を積んだ漢字版は実機で動きます**（M-83 / M-90）が、**16 MB flash が要ります** |
| **これは PoC です** | 製品品質ではありません。既知の制約は [`MODEL_CARD.md`](../MODEL_CARD.md) |
| **音質は「教師の 64%」** | ⚠️ 較正されていない予測器のスコアの比です。人が聴いた評価は 1 名しかしていません |
| **サンプルレート誤差は未測定** | ESP32-S3 に APLL が無く、22,050 Hz は分数分周の近似です。ずれるとピッチがずれます |

## 手元で既に確かめてあること（実機で疑わなくてよい範囲）

**QEMU で出荷ファームを起動から合成完了まで通し**（M-62）、**CoreS3 の実機でも通しました**
（M-83 / M-86 / M-88 / M-89 / M-90）。

- `model` パーティションの mmap と 16 バイト境界 ✅（実機も）
- 端末側 G2P が 53 ids を出し、ホストの答えと**完全一致** ✅（実機で 0.102 ms）
- `saan_stream_init` / arena 180,224 B / 合成 106 frames = 27,136 sample ✅（実機の used 157,360 B）
- **PIE カーネルがスカラ実装と bit 完全一致**（同一ターゲット・全 27,136 sample） ✅
- **シリアルからの自由入力** ✅ QEMU の UART0（M-63）と**実機の USB Serial/JTAG**（M-83 以降）
- **漢字経路**（辞書 13.7 MB の mmap → 形態素解析 → 合成）✅ QEMU（M-76）と**実機**（M-83 / M-86 / M-90）
- **かな行と漢字行が同じ PCM になる** ✅ 実機で bit 一致（M-90）
- **速度**: 満チャンク xRT **0.446** / アンダーラン **0** ✅ 実機（M-90）

⚠️ **確かめていないのは次の 4 つです**:
1. **音**（聴取 G32）— **誰も聴いていません**
2. **DevKit の I2S 直叩き**（`saan_i2s.c`）— 実機で 1 度も鳴らしていません
3. **実サンプルレートの誤差** — オシロか長時間録音が要ります
4. **CoreS3 以外の ESP32-S3 の板** — 同じ checksum と xRT が出るか

---

## 詰まったら

| 症状 | 見るところ |
|---|---|
| `重み blob が無い` でビルドが止まる | `-DSAAN_MODEL_BLOB` に**絶対パス**を渡す |
| `fp32 blob が焼かれている` で起動が止まる | int8 blob を指しているか確認（`-i8` の付いた方） |
| `SAAN_ERR_VERSION` で起動が止まる | **blob が形式 v1** です。リリースの `saanotts-jp-v3-int8.bin`（643,936 B）は v1 で、S4（2026-09-02）以降のコアは受け付けません。`scripts/export_c_weights.py --int8` で v2（654,032 B）を作ってください |
| `辞書 OK` が出ない / `esp_partition_mmap` が `ESP_ERR_NO_MEM` | `CONFIG_SPI_FLASH_ROM_IMPL=y` の板では ROM 実装が 8 MB しか貼れません。`saan_dict.c` は自動で `esp_mmu_map` に切り替えます（M-90）。それでも出ないなら 16 MB 版の表を焼けていません |
| 漢字を打っても「辞書を持たない」と言われる | `-DSAAN_KANJI=1` を付けてビルドしていません（既定は無効） |
| `G2P の出力が demo_ids.h の錨と一致しない` | ⚠️ **意図的に止めています**。テーブルか実装がずれている状態なので報告してください |
| 起動すらしない | **8 MB 以上の flash が要ります**（`model` に 3 MB 確保）。4 MB のボードでは入りません |
| `mode:QIO` のあと `ets_loader.c 78` → `rst:0x7 (TG0WDT_SYS_RST)` を繰り返す | `esptool.py write_flash` に **`--flash_mode qio` を渡しています**。渡さないでください（ヘッダが QIO になると ROM ローダが読めません）。`@flash_args` はいつも `dio` で、QIO 対応の板では bootloader が起動後に `qio_mode: Enabling default flash chip QIO` で切り替えます（M-86 で踏んだ） |
| 同じコードなのに xRT が 15% 遅い（例 0.92 → 1.09） | flash が **DIO** で動いています。起動ログに `qio_mode: Enabling default flash chip QIO` が出ているか確認してください（`esp32/sdkconfig.defaults` は QIO。M-86） |
| 焼いた firmware で `重み OK: 183 tensors` が出ない | `write_flash 0x0` で**イメージ全体**を焼いたか確認してください（`0x10000` に app だけ焼くと重みが入りません） |
| ログは出るが `かな> ` に打っても反応しない | **挿しているポートが違います**。「UART」側に挿すか、上の `sdkconfig.usb_serial_jtag` を使ってください |
| 打った文字が画面に出ない | 511 B を超えています（**溢れたらエコーを止める**のが「もう入らない」の合図です） |

詳細な設計判断は [`README.md`](README.md)、実測値は
[`../docs/measurements.md`](../docs/measurements.md) の **M-62 / M-63**（QEMU）と
**M-83 / M-84 / M-86 / M-88 / M-89 / M-90**（CoreS3 の実機）にあります。
速度の作り直しの経緯は [`../docs/research/s1-m5-cores3-speed.md`](../docs/research/s1-m5-cores3-speed.md)。
