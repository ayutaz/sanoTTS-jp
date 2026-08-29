# ESP32-S3 実機テストのお願い

**このプロジェクトに残っている未検証項目は「速度」だけです。**
ESP32-S3 の実機が 1 枚あれば決着します。手元では QEMU までしか行けません
（QEMU はサイクル精度ではないので、速度を測れない）。

**所要 15〜30 分。**⚠️ 最初に [ライセンス](../LICENSE-MODEL.md)を読んでください
（重みは MIT ではありません）。

---

## 用意するもの

| | |
|---|---|
| **ESP32-S3 ボード** | 内部 SRAM 512 KB / flash **8 MB 以上**。PSRAM は不要 |
| **I2S DAC**（任意） | MAX98357A / PCM5102 など。⚠️ **無くても速度は測れます** |
| ESP-IDF | **v5.5 で動作確認済み**。他のマイナーは未検証 |

⚠️ **音を出さなくてもこのテストは成立します。** 知りたいのは
「1 チャンクの計算が実時間に間に合うか」だけで、それは I2S 無しでも測れます。
DAC が無い方は下の**「音を出さない場合」**へ。

---

## 手順

### 1. モデルを取る

**リポジトリをクローンしただけでは重みは入っていません**（git 管理外）。
[リリース](https://github.com/ayutaz/sanoTTS-jp/releases/latest)から
**`saanotts-jp-v3-int8.bin`**（643,936 B）を落としてください。

⚠️ `saanotts-jp-v3-stage4.pt` は **PyTorch 用**です。ESP32 では読めません。

### 2. 配線（音を出す場合のみ）

⚠️ **`main/saan_i2s.c` の `SAAN_I2S_GPIO_*` は根拠のない仮置き**です。
自分のボードに合わせて書き換えてください。

### 3. ビルドして焼く

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git
cd sanoTTS-jp/esp32
idf.py set-target esp32s3

# ★ ここが本命。W8A8 + PIE（ESP32-S3 の整数 SIMD）を有効にする
idf.py -DSAAN_ENABLE_PIE=1 \
       -DSAAN_MODEL_BLOB=/絶対パス/saanotts-jp-v3-int8.bin build

idf.py -p /dev/ttyUSB0 flash monitor
```

`idf.py flash` がアプリと**重み blob を両方**焼きます（blob は `model`
パーティション、3 MB 確保してあります）。

#### 音を出さない場合

`-DSAAN_QEMU=1` を足すと I2S ペリフェラルへの書き込みだけを外します
（**変換と合成は通る**ので測定値は有効）。

```bash
idf.py -DSAAN_ENABLE_PIE=1 -DSAAN_QEMU=1 \
       -DSAAN_MODEL_BLOB=/絶対パス/saanotts-jp-v3-int8.bin build
```

---

## 報告してほしいもの

`idf.py monitor` の**ログを丸ごと**貼ってもらえれば十分です。
特に見たいのはこの 4 行:

```
I (xxx) saanotts: 定常 xRT = ?.???        ← ★ 最重要。1.0 未満なら実時間に間に合う
I (xxx) saanotts: アンダーラン N / M チャンク
I (xxx) saanotts: 出力 PCM: 27136 sample / FNV-1a 0x????????????????
I (xxx) saanotts: 終了時: 内部 DRAM free ????? B
```

### 期待値と、それぞれが何を意味するか

| ログ | 期待 | 外れたときの意味 |
|---|---|---|
| **`定常 xRT`** | **< 1.0** | ⚠️ **これが全部**。1.0 を超えたら実時間合成に間に合っていない。論文は同じ ESP32-S3 で 0.22 を申告 |
| `アンダーラン` | 0 が理想 | xRT < 1.0 なら 0 になるはず。ならなければ I2S 側の問題 |
| **`出力 PCM ... FNV-1a`** | **`0x04de91103a0e49f9`** | **QEMU での実測値**。⚠️ 一致しなくても即バグではない（下記） |
| `内部 DRAM free` | 数十 KB 残る | 0 に近ければ arena を削る必要がある |
| `W8A8 + PIE 有効 / int8 blob を確認` | **出ること** | 出なければ PIE が効いていない構成です |

⚠️ **FNV-1a が一致しなくても慌てないでください。** QEMU と実機で
コンパイラが同じなら一致するはずですが、最適化やライブラリ差で float の丸めが
変われば変わります。**そのときは同じ行の `|max| 9744 / Σx² 74374063946` を見てください** —
これが近ければ丸め差、大きく外れていれば本当のバグです。

### できれば両方測ってほしい

**PIE がどれだけ効いたか**が直接わかります（手元では原理的に測れません）。

```bash
# A: 最適化なし（W8A32 / 移植可能 C）
idf.py -B build_a -DSAAN_MODEL_BLOB=<int8 blob> build && idf.py -B build_a -p PORT flash monitor
# B: W8A8 + PIE
idf.py -B build_b -DSAAN_ENABLE_PIE=1 -DSAAN_MODEL_BLOB=<int8 blob> build && idf.py -B build_b -p PORT flash monitor
```

外挿では A は **2.47× 実時間**（間に合わない）と予想しています。
⚠️ **この外挿が当たっているかどうかも、まだ誰も確かめていません。**

---

## 先に知っておいてほしいこと

| | |
|---|---|
| **喋る文は 1 つだけ** | 「今日は良い天気ですね。」がビルド時に焼き込まれています。⚠️ **シリアルから文字列を入れる経路はまだありません** |
| **漢字は端末で扱えません** | 漢字→かなはホスト側（OpenJTalk）。端末が受け取るのは「ひらがな + アクセント記号」です（辞書 102 MB を積めないため） |
| **これは PoC です** | 製品品質ではありません。既知の制約は [`MODEL_CARD.md`](../MODEL_CARD.md) |
| **音質は「教師の 64%」** | ⚠️ 較正されていない予測器のスコアの比です。人が聴いた評価は 1 名しかしていません |
| **サンプルレート誤差は未測定** | ESP32-S3 に APLL が無く、22,050 Hz は分数分周の近似です。ずれるとピッチがずれます |

## 手元で既に確かめてあること（実機で疑わなくてよい範囲）

QEMU 上で出荷ファームを**起動から合成完了まで通してあります**（M-62）。

- `model` パーティションの mmap と 16 バイト境界 ✅
- 端末側 G2P が 53 ids を出し、ホストの答えと**完全一致** ✅
- `saan_stream_init` / arena 212,992 B / 合成 106 frames = 27,136 sample ✅
- **PIE カーネルがスカラ実装と bit 完全一致**（同一ターゲット・全 27,136 sample） ✅

⚠️ **確かめていないのは「速度」と「実機の I2S」だけ**です。
（QEMU の esp32s3 は I2S DMA を捌かないので、そこだけは通せませんでした。）

---

## 詰まったら

| 症状 | 見るところ |
|---|---|
| `重み blob が無い` でビルドが止まる | `-DSAAN_MODEL_BLOB` に**絶対パス**を渡す |
| `fp32 blob が焼かれている` で起動が止まる | `saanotts-jp-v3-int8.bin` を指しているか確認（`-i8` の付いた方） |
| `G2P の出力が demo_ids.h の錨と一致しない` | ⚠️ **意図的に止めています**。テーブルか実装がずれている状態なので報告してください |
| 起動すらしない | `partitions.csv` は 8 MB flash 前提です。4 MB のボードでは `model` が入りません |

詳細な設計判断は [`README.md`](README.md)、実測値は
[`../docs/measurements.md`](../docs/measurements.md) の M-62 にあります。
