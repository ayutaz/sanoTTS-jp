# M5Stack（M5Unified）で sanoTTS-jp を鳴らす

`../../main/main.c` をそのまま使い、音声出力を **M5Unified の `M5.Speaker`**、画面を **M5GFX** に
差し替えた ESP-IDF プロジェクト。推論コアは `csrc/` の相対参照（コピーしない）。

✅ **このリポジトリの実機測定はすべてこの構成で取った**（M5Stack CoreS3 = D-047。
M-82 / M-84 / M-87 / M-88 / M-89 / M-90）。**漢字辞書 13.7 MB も同じ 1 本のファームに載る**（M-90）。

出所: 構成と M5 層のコードは [nnn112358/SanoTTS-jp-M5StackCoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)
（MIT, Copyright (c) 2026 nnn112358）に倣い、本リポジトリの API（`saan_audio.h` / `saan_ui.h` / `saan_pcm.h`）に合わせた。

## ビルド

```sh
. ~/esp/esp-idf/export.sh
cd esp32/boards/m5unified

# CoreS3 / CoreS3 SE（ESP32-S3）。**W8A8 + PIE は既定で有効**（D-048）
idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" build
idf.py -B build_cores3 -p /dev/cu.usbmodem* flash monitor        # 終了は Ctrl+]

# CoreS3 + **漢字辞書**（K-A。16 MB を使い切る）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin     # ← リポジトリのルートで
idf.py -B build_m5k -DSDKCONFIG=build_m5k/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -DSAAN_KANJI=1 -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict.bin build
cd build_m5k && esptool.py --chip esp32s3 -p /dev/cu.usbmodem* --baud 921600 write_flash @flash_args
#   ⚠️ 辞書 13.7 MB 込みで**約 4 分**。⚠️ `--flash_mode qio` を渡さないこと（ブートループ。M-86）

# Core2 / Core2 AWS（ESP32）。PIE 命令が無いので自動的に W8A32
idf.py -B build_core2 -DSDKCONFIG=build_core2/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" build
```

| フラグ | 既定 | 意味 |
|---|---|---|
| `-DSAAN_ENABLE_PIE=0/1` | **ESP32-S3 では 1**（D-048）。それ以外は 0 | W8A8 + ESP32-S3 の整数 SIMD (PIE)。**S3 以外に 1 を渡すと CMake で止まる**。W8A32 で測るには **0 を明示** |
| `-DSAAN_KANJI=0/1` | **0** | 端末で漢字かな交じり文を読む（K-7）。**辞書パーティション 13.83 MB を使う** |
| `-DSAAN_DICT_BLOB=<絶対パス>` | `csrc/k1_dict.bin` | 焼く辞書 blob（`SAAN_KANJI=1` のとき） |
| `-DSAAN_BUFFERED=0/1` | **0** | 1 = 1 発話ぶんを PSRAM に貯めてから鳴らす（**途切れない**。待ちは合成時間） |
| `-DSAAN_BOOT_SPEAK=0/1` | **1** | 起動時に 1 文喋る（画面の文と同じ。checksum の突き合わせ用） |
| `-DSAAN_PROFILE=0/1` | **0** | 段別プロファイル（CCOUNT）を発話後に出す。**速度の報告には 0 で** |
| `-DSAAN_MODEL_BLOB=<絶対パス>` | `csrc/student_i8.bin` | 重み。⚠️ **blob v2（654,032 B）が要る** — リリース v0.2.0 の `saanotts-jp-v3-int8.bin` は v1 で `SAAN_ERR_VERSION` になる |

- `-D` の値は `build_*/` を消すまで CMake キャッシュに残る
- 初回ビルドで M5Unified / M5GFX を ESP-IDF Component Registry から取る（ネットワークが要る）
- 重みは app の `.rodata` に埋める（`model` パーティションは無い）。**モデルだけの差し替えはできない**
- **設定は `sdkconfig.defaults`（板に依らない）+ `sdkconfig.<板>`** の 2 段。
  `sdkconfig.cores3` は **QIO / D-cache 64 KB・64 B 行 / Quad PSRAM / USB Serial-JTAG コンソール**

### ⚠️ 辞書は `esp_partition_mmap` では貼れない（`esp_mmu_map` を使う）

`sdkconfig.defaults` の `CONFIG_SPI_FLASH_ROM_IMPL=y` により、ESP32-S3 では `spi_flash_mmap` が
**ROM の実装**にリンクされ、IDF はそれに **128 ページ = 8 MB** しか渡さない。辞書は
`0xD30000 / 64 KB` = **211 ページ**なので、vaddr が余っていても `ESP_ERR_NO_MEM` になる。
`../../main/saan_dict.c` が `esp_mmu_map`（`esp_mm`）へ切り替えて解決した。実機では
**PSRAM 8 MB と同居して連続 vaddr 23,724,032 B が空いていた**（M-90）。

⚠️ 第三者が報告した「`CONFIG_SPIRAM=y` だと `esp_partition_mmap` が落ちる」は**これが正体**で、
PSRAM そのものが原因ではなかった。**重みを `.rodata` に埋める判断は変えていない**
（`model` パーティションを持たない構成のまま）。

## 使い方

画面は 3 段: 上段に文（漢字）、中段にかな中間表現、下段にステータス
（`合成中…` → `xRT 0.45  途切れ 0/14`）。**画面をタッチすると直前の文をもう一度喋る。**

シリアルの `かな> ` に 1 行打つとその文を喋る。**`!` は要らない** — 端末の
`saan_g2p_classify()` が「かな中間表現 / 漢字かな交じり文 / 拒否」の 3 値で経路を決める
（`../../TESTING.md`「喋らせる」）。

```
かな> きょ][おわよ][いて][んきです°ね      → 経路: かな
かな> 今日は良い天気ですね。               → 経路: 辞書（漢字ビルドのみ）。同じ PCM が出る
かな> コンニチハ                           → 経路: 辞書
```

⚠️ **300 B 級の 1 行を一度に貼ると途中で欠ける** — USB Serial/JTAG ドライバの RX リング
（既定 256 B）が溢れる。64 B ずつ 30 ms 間隔なら通る（M-84 §5）。

## 期待値（移植が正しいことの機械的な証拠）

| 構成 | FNV-1a | \|max\| | Σx² |
|---|---|---:|---:|
| W8A8 + PIE（**既定**。ESP32-S3） | `0xa69a7ebbb5ccb05f` | 9627 | 74,264,237,672 |
| W8A32（`-DSAAN_ENABLE_PIE=0`。Core2 もこちら） | `0xe4b645c30835d42d` | 9529 | 74,155,591,505 |

起動時の 1 文（`きょ][おわよ][いて][んきです°ね`）でこの値が出れば、G2P → 合成 → int16 まで
QEMU の記録（M-81。S3 = erf 近似を入れた後の値）と bit 一致している。
**checksum は M-82 以降 1 bit も動いていない** — S1〜S5b と T1〜T5 は全部「出力を変えない」変更だった。
⚠️ 配布イメージ v0.2.0 までの旧コアは `0x04de91103a0e49f9` / `0x78c209af06affc01`（M-62）。
⚠️ **「音が鳴った」は証拠にならない。**

## 実測（CoreS3 / W8A8+PIE / 漢字辞書込み。M-90）

| | 値 |
|---|---:|
| 満チャンク 1 pull の xRT | **0.446**（要件 RTF ≤ 0.5） |
| 発話全体（合成合計 / 音声長） | 0.541〜0.712（4 文） |
| アンダーラン | **0** |
| 初回 pull / 鳴らし始めまで | 244.65 ms / 384 ms |
| 起動直後の内部 DRAM free | **132,039 B**（最大ブロック 86,016） |
| arena（静的確保 / 実測 peak） | 180,224 B / 157,360 B（M-89） |
| かな G2P（44 B → 53 ids） | 0.102 ms |
| 漢字 G2P（33 B → 53 ids） | 25.69 ms |
| タスクスタック残り | 10,996 / 16,384 B |
| flash | app 1.43 MB（factory 2.75 MB）+ 辞書 13.83 MB = 16 MB ちょうど |

⚠️ **音は聴いていない**（G32）。checksum が同じなので波形は設計どおりだが、
**途切れの有無も読み違いの重大さも、聴くまで分からない。**
⚠️ **実サンプルレートの誤差は未測定**（ESP32-S3 に APLL が無い。ファーム自身が警告を出す）。
