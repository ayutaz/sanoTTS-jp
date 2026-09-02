# M5Stack（M5Unified）で sanoTTS-jp を鳴らす

`../../main/main.c` をそのまま使い、音声出力を **M5Unified の `M5.Speaker`**、画面を **M5GFX** に
差し替えた ESP-IDF プロジェクト。推論コアは `csrc/` の相対参照（コピーしない）。

出所: 構成と M5 層のコードは [nnn112358/SanoTTS-jp-M5StackCoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)
（MIT, Copyright (c) 2026 nnn112358）に倣い、本リポジトリの API（`saan_audio.h` / `saan_ui.h` / `saan_pcm.h`）に合わせた。

## ビルド

```sh
. ~/esp/esp-idf/export.sh
cd esp32/boards/m5unified

# CoreS3 / CoreS3 SE（ESP32-S3）。W8A8 + PIE を有効にする
idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" -DSAAN_ENABLE_PIE=1 build
idf.py -B build_cores3 -p /dev/cu.usbmodem* flash monitor        # 終了は Ctrl+]

# Core2 / Core2 AWS（ESP32。PIE 無し = W8A32）
idf.py -B build_core2 -DSDKCONFIG=build_core2/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" build
```

| フラグ | 既定 | 意味 |
|---|---|---|
| `-DSAAN_ENABLE_PIE=0/1` | **0** | W8A8 + ESP32-S3 の整数 SIMD (PIE)。**S3 以外に 1 を渡すと CMake で止まる** |
| `-DSAAN_BUFFERED=0/1` | **0** | 1 = 1 発話ぶんを PSRAM に貯めてから鳴らす（**途切れない**。待ちは合成時間） |
| `-DSAAN_BOOT_SPEAK=0/1` | **1** | 起動時に 1 文喋る（画面の文と同じ。checksum の突き合わせ用） |
| `-DSAAN_PROFILE=0/1` | **0** | 段別プロファイル（CCOUNT）を発話後に出す。**速度の報告には 0 で** |
| `-DSAAN_MODEL_BLOB=<絶対パス>` | `csrc/student_i8.bin` | 重み。リリースの `saanotts-jp-v3-int8.bin` と同じもの |

- `-D` の値は `build_*/` を消すまで CMake キャッシュに残る
- 初回ビルドで M5Unified / M5GFX を ESP-IDF Component Registry から取る（ネットワークが要る）
- 重みは app の `.rodata` に埋める（`model` パーティションは無い）。**モデルだけの差し替えはできない**

## 使い方

画面は 3 段: 上段に文（漢字）、中段にかな中間表現、下段にステータス
（`合成中…` → `xRT 1.55  途切れ 10/14`）。**画面をタッチすると直前の文をもう一度喋る。**
シリアルの `かな> ` に かな中間表現を 1 行打つとその文を喋る（`../../TESTING.md`「喋らせる」）。

## 期待値（移植が正しいことの機械的な証拠）

| 構成 | FNV-1a | \|max\| | Σx² |
|---|---|---:|---:|
| W8A8 + PIE（S3） | `0x04de91103a0e49f9` | 9744 | 74,374,063,946 |
| W8A32 / PIE 無し | `0x78c209af06affc01` | 9529 | 74,155,592,149 |

起動時の 1 文（`きょ][おわよ][いて][んきです°ね`）でこの値が出れば、G2P → 合成 → int16 まで
QEMU の記録（M-62）と bit 一致している。**「音が鳴った」は証拠にならない。**
⚠️ 速度（xRT）はこのリポジトリでは**まだ自己実測していない**（第三者の報告値 1.554× RT）。
