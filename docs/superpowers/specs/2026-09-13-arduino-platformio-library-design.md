# Arduino / PlatformIO ライブラリ — 設計

**日付**: 2026-09-13
**状態**: 承認済み（実装前）
**決定記録**: [D-065](../../decisions.md#d-065)（この設計の要約を置く）
**測定**: [M-137](../../measurements.md#m-137)（実装後に埋める）

---

## 1. 目的

`v1.0.0` で配っている ESP32-S3 の C99 コアを、**Arduino IDE と PlatformIO の両方から
`lib_deps` 1 行で使える形**にする。いま外の人が音を出すには ESP-IDF v5.5 を入れ、
`idf.py` に 5 つのフラグを渡し、辞書を自分で作る必要がある。

**非目的**:

- 新しい推論実装を書かない。**コアは 1 バイトも変えない**
- ESP-IDF 経路（`esp32/`）を置き換えない。**残す**。実機の測定はすべてそちら基準
- 音質・速度を変えない。**同じ入力から同じ PCM が出ることが受け入れ条件**

---

## 2. 決定事項

| # | 決めたこと | 誰が |
|---|---|---|
| D-a | **Arduino IDE と PlatformIO の両方**を対象にする | ユーザー |
| D-b | **PCM を返すのが主**。スピーカーは差し込み式（M5Unified / 汎用 I2S を同梱） | ユーザー |
| D-c | 重みは**既定で `.rodata` 埋め込み**、上級者はパーティション | ユーザー |
| D-d | ライブラリは**このリポジトリの `arduino/`** に置く | ユーザー |
| D-e | `src/` の実体は**生成物にし、git に置かない** | ユーザー |
| D-f | 配布は**リリース資産の .zip 2 本**（コード / 重み） | 下記 §3 の制約から必然 |
| D-g | フラグは **`arduino/src/sanotts_config.h` 1 枚**に集約する | 下記 §3 の制約から必然 |

---

## 3. 確定した外部制約

すべて公式仕様で確認済み。**推測で書いていない。**

| # | 事実 | 出典 | 設計への影響 |
|---|---|---|---|
| C-a | PlatformIO は git リポジトリの**サブディレクトリを指せない**。`library.json` は直下必須 | [pkg install](https://docs.platformio.org/en/latest/core/userguide/pkg/cmd_install.html) / [core#3887](https://github.com/platformio/platformio-core/issues/3887) | `lib_deps = <repo URL>` は**使えない** |
| C-b | `lib_deps` は **`.zip` / `.tar.gz` の直 URL を公式にサポート** | 同上（"Remote TAR or ZIP archive"） | **リリース .zip が唯一の配布経路** |
| C-c | Arduino は `src/` を**すべてのサブフォルダも含めて**コンパイルする | [library 1.5 spec](https://arduino.github.io/arduino-cli/1.5/library-specification/) | 漢字オフ時に Open JTalk 14 本を**自分で `#if` で消す**必要がある |
| C-d | Arduino ライブラリが**独自の `-D` やインクルードパスを渡す手段は仕様に無い** | 同上 | **設定ヘッダが唯一の設定点** |
| C-e | `extras/` はビルドから**完全に無視される** | 同上 | パーティション CSV の置き場に使える |
| C-f | `library.properties` に **`license` フィールドは無い**（`library.json` にはある） | 同上 | 重みのライセンスは同梱ファイルと README で示す |
| C-g | Arduino IDE のスケッチ内 `partitions.csv` は[公式にある](https://docs.espressif.com/projects/arduino-esp32/en/latest/tutorials/partition_table.html)が、[IDE 2.x で効かない報告がある](https://github.com/espressif/arduino-esp32/issues/10120) | 同上 | **漢字は PlatformIO を推奨**と明記する |

---

## 4. 成果物

### 4.1 リリース資産（2 本追加）

| .zip | 中身 | ライセンス |
|---|---|---|
| `sanoTTS-jp-arduino-<ver>.zip` | C99 コア + G2P + 辞書リーダ + Open JTalk + C++ ラッパー + examples | **MIT**（Open JTalk 部は同梱 `NOTICE-openjtalk.txt`） |
| `sanoTTS-jp-voice-tsukuyomi-v4-<ver>.zip` | 重み 654,032 B の `aligned(16)` C 配列 | **LicenseRef-sanoTTS-jp-Model-1.0** |

使い方:

```ini
[env:m5stack-cores3]
platform = espressif32
board = m5stack-cores3
framework = arduino
board_build.partitions = sanotts_16mb.csv          ; 漢字を使うときだけ
lib_deps =
    https://github.com/ayutaz/sanoTTS-jp/releases/download/v1.1.0/sanoTTS-jp-arduino-1.1.0.zip
    https://github.com/ayutaz/sanoTTS-jp/releases/download/v1.1.0/sanoTTS-jp-voice-tsukuyomi-v4-1.1.0.zip
    m5stack/M5Unified
```

### 4.2 リポジトリに足すもの

```
arduino/
  library.properties            手書き。Arduino IDE 用
  library.json                  手書き。PlatformIO 用
  src/
    SanoTTS.h / SanoTTS.cpp     手書き。公開 API
    SanoTTSSpeaker.h            手書き。抽象インターフェース
    SanoTTSSpeakerM5.h  / .cpp  手書き。__has_include(<M5Unified.h>) で自動的に消える
    SanoTTSSpeakerI2S.h / .cpp  手書き。汎用 I2S
    sanotts_config.h            手書き。★ 唯一の設定点
    core/                       ★ 生成物（.gitignore）
  examples/
    HelloKana/HelloKana.ino
    HelloKanji/HelloKanji.ino
    PcmCallback/PcmCallback.ino
  extras/partitions/            ビルド対象外。sanotts_16mb.csv / _8mb.csv / _4mb.csv
  README.md / README.en.md
  NOTICE.txt
scripts/build_arduino_lib.py    ★ 新規。生成器 + --check + --zip
```

`arduino/src/core/` を `.gitignore` に入れるので、**csrc/ の実体はリポジトリに一切二重化しない**。

### 4.3 既存ファイルの変更

| ファイル | 変更 |
|---|---|
| `.gitignore` | `arduino/src/core/`、`arduino/dist/` を足す |
| `.github/workflows/ci.yml` | `arduino` job を足す（G-AR1 / G-AR2 / G-AR3） |
| `.github/workflows/README.md` | job 数を 6 → 7 に。範囲を書く |
| `scripts/check_ci_coverage.py` | `SCRIPT_GLOBS` に `scripts/build_arduino_lib.py` を足す |
| `scripts/check_attribution.py` | 帰属ブロックの写しが **3 か所 → 4 か所**（`arduino/NOTICE.txt`）。陽性対照も 1 件増やす |
| `README.md` / `README.en.md` | 「Arduino / PlatformIO」節を足す |
| `docs/decisions.md` | **D-065** |
| `docs/measurements.md` | **M-137**（`build_measurements_index.py` で索引を再生成） |
| `CLAUDE.md` | スコープ節に W トラックと並べて 1 段落 |

---

## 5. 設計詳細

### 5.1 生成器 `scripts/build_arduino_lib.py`

3 つのモードを持つ。

```bash
uv run --no-project python scripts/build_arduino_lib.py            # arduino/src/core/ を作る
uv run --no-project python scripts/build_arduino_lib.py --check    # 生成物が csrc と一致するか（CI）
uv run --no-project python scripts/build_arduino_lib.py --zip dist # リリース .zip 2 本を組む
```

**生成規則は 1 つだけ**: 各出力ファイルは

```
<前置き>  ←  #include "<相対パス>/sanotts_config.h"  と、必要なら #if SANOTTS_ENABLE_KANJI
<csrc または esp32/main の該当ファイルの逐語バイト列>
<後置き>  ←  必要なら #endif
```

`--check` は**逆向きに検証する** — 生成済みファイルから前置き / 後置きを剥がした残りが、
元ファイルと **bit 一致**すること。⚠️ **陽性対照**: 元ファイルを 1 バイト変えると落ちる。

取り込むファイル:

| 由来 | ファイル | 条件 |
|---|---|---|
| `csrc/` | `saanotts.c` `saanotts_stream.c` `fft.c` `saanotts_int8.c` `g2p.c` + 各ヘッダ + `erf_table.h` `dan_table.h` `g2p_table.h` `token_table.h` `saan_prof.h` | 常に |
| `csrc/` | `jdict.c` `accent.c` `njd_rules.c` `label_ids.c` + ヘッダ | 漢字時のみ `#if` で囲う |
| `csrc/openjtalk/` | `*.c` `*.h`（14 + ルール表） | 漢字時のみ `#if` で囲う。⚠️ **さらに `oj_heap_psram.h` を前置き**して一時ヒープを PSRAM に向ける |
| `esp32/main/` | `saan_pcm.c/.h` `saan_kanji.c/.h` `saan_dict.c/.h` `saan_model.h` `saan_model_rodata.c` `saan_model.c` | `saan_kanji` / `saan_dict` は漢字時のみ |
| `esp32/components/saanotts_core/` | `saan_port_esp32.h` `oj_heap_psram.c` `oj_heap_psram.h` | 常に / 漢字時 |

⚠️ **Open JTalk への前置きは「改変」である。** `k4b_vendor.py --check` は `csrc/openjtalk/` を
見るので通るが、**`arduino/NOTICE.txt` に「前置き N 行・後置き 1 行を足した」と明記する**。
`--check` が毎回「巻いた中身は逐語」であることを証明する。

⚠️ **インクルードパスは足さない。** 引用形式 `#include "openjtalk/njd.h"` は
**インクルード元からの相対**で解決されるので、`src/core/` 以下の階層を csrc と同じに保てば
Arduino の `-I<lib>/src` だけで通る。**`build.flags` に `-I` を書かない**こと
（Arduino IDE では渡せず、PlatformIO だけ通って**片方でしか動かない**構成になる）。

### 5.2 `sanotts_config.h` — 唯一の設定点

CMake に散っているフラグの既定値をここに集約する。**CLAUDE.md が守ってきた不変条件を
構造として持ち込む**:

```c
/* 1. 漢字（既定オン。辞書が無ければ実行時にかな専用へ落ちる = D-063） */
#ifndef SANOTTS_ENABLE_KANJI
#  define SANOTTS_ENABLE_KANJI 1
#endif

/* 2. W8A8 + PIE。⚠️ 片方だけは絶対に立てない（CLAUDE.md「2 つを別々に定義できないようにしてある」） */
#if !defined(SANOTTS_ENABLE_PIE)
#  if defined(CONFIG_IDF_TARGET_ESP32S3)
#    define SANOTTS_ENABLE_PIE 1
#  else
#    define SANOTTS_ENABLE_PIE 0     /* S3 以外は自動的に W8A32。#error にしない */
#  endif
#endif
#if SANOTTS_ENABLE_PIE && !defined(CONFIG_IDF_TARGET_ESP32S3)
#  error "SANOTTS_ENABLE_PIE=1 は ESP32-S3 専用（ee.* 命令が無い板ではアセンブラが落ちる）"
#endif
#if SANOTTS_ENABLE_PIE
#  define SAAN_INT8_ACT 1
#  define SAAN_PIE      1
#endif

/* 3. 漢字に付いてくるもの */
#if SANOTTS_ENABLE_KANJI
#  define SAAN_KANJI                  1
#  define CHARSET_UTF_8               1
#  define LABEL_IDS_EXTERNAL_SCRATCH  1
#endif

/* 4. 配置（T5-G2。erf 表 1,032 B を内部 DRAM に置く） */
#ifndef SAAN_PORT_HEADER
#  define SAAN_PORT_HEADER "saan_port_esp32.h"
#endif

/* 5. 重みの置き場（既定 = .rodata 埋め込み） */
#ifndef SANOTTS_MODEL_FROM_PARTITION
#  define SANOTTS_MODEL_FROM_PARTITION 0
#endif

/* 6. 辞書の SHA-256。未定義なら saan_dict.c が起動時に「照合していない」と警告する */
/* #define SANOTTS_DICT_SHA256 "…" */
```

⚠️ **CMake との挙動の違いを 1 つだけ意図的に入れる。** CMake は S3 以外で
`SAAN_ENABLE_PIE=1` を `FATAL_ERROR` にしていたが、ライブラリでは板を選ぶのはユーザーなので
**既定は自動で W8A32 に落とす**。手で `SANOTTS_ENABLE_PIE=1` を立てた場合だけ `#error`。

⚠️ **`SANOTTS_*` と `SAAN_*` を分ける。** 前者はユーザーが触る面、後者はコアの内部フラグ。
混ぜると「ユーザーが `SAAN_PIE` だけ立てた」形が作れてしまう。

### 5.3 公開 API

```cpp
class SanoTTS {
public:
    bool begin();                                   // 重みを開く（+ 辞書があれば開く）
    bool ready() const;
    bool kanjiReady() const;                        // 辞書が使えるか（かな専用に落ちていれば false）

    using PcmCallback = std::function<void(const int16_t*, size_t)>;
    bool synthesize(const char* text, PcmCallback cb);   // PCM をチャンクで返す
    bool say(const char* text);                          // setSpeaker() された先へ流す

    void setSpeaker(SanoTTSSpeaker* s);
    void setLengthScale(float s_v);                 // 既定 1.0

    uint64_t checksum() const;                      // saan_pcm_checksum()。移植の検証用
    const char* lastError() const;
};
```

- `synthesize()` は `saan_stream_pull()` をそのまま回す。**ストリーミング特性
  （鳴らし始め 407〜433 ms）を落とさない**
- 入力は**かな中間表現でも漢字かな交じり文でもよい**。`saan_g2p_classify()` が 3 値で経路を決める
  （`esp32/main/main.c` の `speak_line()` と同じ判定を移す）
- **arena は `SAAN_ARENA_BYTES` = 176 KB の静的確保**（`main.c` と同じ）。
  `_Static_assert(SAAN_ARENA_BYTES >= SAAN_KANJI_WORKBYTES + SAAN_KANJI_T10_BSS_BYTES)` も移す
- `saan_stream_init()` 後に `a.used == saan_stream_arena_used(n_ids)` を確認する二重防御も移す

`SanoTTSSpeaker` は 3 メソッドだけ:

```cpp
class SanoTTSSpeaker {
public:
    virtual bool begin(uint32_t sampleRate) = 0;    // 22050
    virtual void write(const int16_t* pcm, size_t n) = 0;
    virtual void end() = 0;
};
```

### 5.4 重み

- 既定: voice ライブラリが `saan_model_blob.h`（`scripts/blob_to_header.py` の出力そのもの）
  と、それを 1 回だけ include する `.c` を持つ。公開ヘッダは小さい
  `saanotts_jp_voice.h`（`extern` 宣言 + バイト数 + SHA-256）だけ
- コード側は `core/saan_model_rodata.c` をそのまま使う。**新しい読み込み経路は書かない**
- `SANOTTS_MODEL_FROM_PARTITION 1` で `core/saan_model.c`（`esp_partition_mmap`）に切り替わる

### 5.5 辞書

- 常に `dict` パーティション（`esp_mmu_map`）。`extras/partitions/sanotts_16mb.csv` を同梱
- 無い / 壊れている → **D-063 の既存挙動で漢字経路だけ無効にし、かな専用で続く**
- SHA-256 は `sanotts_config.h` の `SANOTTS_DICT_SHA256` に書けるようにする。
  未定義なら `saan_dict.c:283` が既に「照合していない」と警告する（**追加実装なし**）
- 焼き方は `arduino/README.md` に `esptool` の 1 行を書く。PlatformIO は `extraScript` でも可

---

## 6. ゲート

| # | 何を見るか | 陽性対照 | どこで |
|---|---|---|---|
| **G-AR1** | 生成物の各ファイルが「前置き + 逐語 + 後置き」か | csrc を 1 バイト変えると落ちる | CI |
| **G-AR2** | PlatformIO で 3 構成がビルドできる（かな / 漢字 / 非 S3 = W8A32） | — | CI |
| **G-AR3** | arduino-cli で .zip からビルドできる | — | CI |
| **G-AR4** | ⚠️ **QEMU で PCM checksum が 16 MB 基準と bit 一致** | 重みを壊すと落ちる | **手元のみ** |
| **G-AR5** | 帰属ブロックが **4 か所**で一字一句一致 | 1 文字変えると落ちる | CI |

⚠️ **G-AR4 が唯一「同じ音が出る」を証明するゲート。** 他は全部「ビルドが通る」しか言っていない。
CI で回せないのは ESP-IDF + QEMU（約 2 GB）が要るため — `check_ci_coverage.py` の
`EXCLUDED_SCRIPTS` に理由つきで登録する。⚠️ **手で走らせるゲートはいずれ走らせなくなる。**

---

## 7. 残るリスク（実装では埋まらない）

1. ⚠️ **実機で鳴らしていない。板が無い。** 言えるのは QEMU の checksum 一致までで、
   **Arduino ビルドの xRT もアンダーランも未測定**。残タスク 11 と同じ扱いになる
2. ⚠️ **arduino-esp32 が使う ESP-IDF のバージョンが、実測した v5.5 と違う可能性がある。**
   ビルドが通るかは CI で分かるが、速度は別
3. ⚠️ **Arduino IDE で漢字を使うのは現実的でないかもしれない**（C-g）。
   app 1.44 MB + 辞書 13.7 MB にカスタムパーティションが要る
4. ⚠️ **リリース .zip を作るまで `lib_deps` の行は動かない。** ドキュメントに書く URL は
   **実在するタグを指すこと**。`check_release_assets.py` の対象に足す

---

## 8. 受け入れ条件

- [ ] G-AR1 〜 G-AR3 / G-AR5 が CI で緑
- [ ] G-AR4 を手元で 1 回通し、checksum を M-137 に記録
- [ ] `check_ci_coverage.py` / `check_doc_commands.py` / `check_doc_links.py` /
      `check_doc_counters.py` / `build_measurements_index.py --check` が緑
- [ ] `arduino/README.md` の手順を**書いた通りに実行して**通ることを確認
- [ ] D-065 と M-137 を書く
