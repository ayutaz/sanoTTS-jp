# S-1 実装計画 — M5Unified 対応の取り込みと、1.55× RT を作り直す

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

作成 2026-09-02 / ブランチ `feat/s1-speed-m5`。

**Goal:** 第三者が M5Stack CoreS3 で報告した **W8A8+PIE 1.554× RT**（実時間に間に合っていない）を、
本リポジトリで再現できる形（M5Unified 対応）にしたうえで、積和以外の 4 つ（テンソル検索 / 活性化の量子化 /
GELU / 重みのコピー）を出力を保ったまま消し、実機の内訳で効果を測る。

**Architecture:** 音声出力と重みの置き場を抽象 API の後ろに隠し、DevKit（I2S 直叩き / パーティション mmap）と
M5Unified（`M5.Speaker` / `.rodata` 埋め込み）を同じ `main.c` で動かす。速度は S1〜S5 の順に 1 つずつ入れ、
各ステップでホスト（bit 一致 / SNR）→ QEMU（checksum）→ 実機（`SAAN_PROFILE=1` の表）の順に測る。

**Tech Stack:** C99（csrc）/ ESP-IDF v5.5 / M5Unified 0.2（C++、Component Registry）/ Python 3.12+（stdlib）/ QEMU esp32s3。

**Spec:** [`../research/s1-m5-cores3-speed.md`](../research/s1-m5-cores3-speed.md)（§4 仮説 / §5 順序 / §6 取り込み案）と
[`../measurements.md`](../measurements.md) M-80。

## Global Constraints

- **推論コアは 1 か所**（`csrc/`）。`esp32/` はコピーせず相対参照する（`esp32/components/saanotts_core/CMakeLists.txt` 冒頭）
- **同じカーネルを 2 回書かない**（`csrc/saanotts_internal.h`）。checksum の実装も 1 か所
- 出力を変えるステップは **「bit 同一」か「丸め水準」かを先に宣言**し、丸め水準なら `|max|` 完全一致 + Σx² 相対差 ≤ 1e-6 で示す（M-62 の基準）
- 数値は自己実測のみ M-番号にする。第三者の値は「報告値」（`docs/upstream-sanotts.md` と同じ扱い）
- **黙って遅い経路に落ちない**: 効かない構成（fp32 blob + W8A8 / v1 blob + v2 コア / S3 以外で PIE）は起動時か CMake で止める
- GPL の公式実装 `Ampixa/sanoTTS` のソースは読まない（D-032）。取り込むのは MIT の `nnn112358/SanoTTS-jp-M5StackCoreS3` のみ、出所を明記
- Python は `uv run`。ゲートは陽性対照つき（`writing-gates`）
- コミットは Task 単位。各コミットで `make -C csrc all-test` と `bash scripts/check_esp32_template.sh` が通る

## 板の分岐（A-0 で凍結）

| 板 | かなトラック | 速度 S1〜S8 の効果 | 記録 |
|---|---|---|---|
| ESP32-S3（CoreS3 / CoreS3 SE） | 喋る + checksum `0xa69a7ebbb5ccb05f` 一致（S3 以降。S2 まで `0x04de91103a0e49f9`） | **実機の内訳で測れる** | M-番号 |
| ESP32（Core2 / Basic / Fire） | 喋る + W8A32 checksum `0xe4b645c30835d42d` 一致（S3 以降。S2 まで `0x78c209af06affc01`） | 効果は QEMU の割合と nnn112358 さんへの依頼 | M-番号は「喋る」まで |

同定: `esptool.py chip_id`（USB 接続）か外見（カメラ穴 = CoreS3 / タッチの丸 3 つ = Core2 / 物理ボタン 3 つ = Basic）。

---

## ファイル構成（決定）

```
esp32/main/
  saan_audio.h            音声出力の抽象 API（旧 saan_i2s.h。7 関数）
  saan_i2s.c              実装 1: DevKit の I2S 直叩き（既存。API 名だけ変える）
  saan_pcm.{h,c}          float→int16 と checksum / |max| / Σx²（**唯一の実装**。両実装が呼ぶ）
  saan_ui.h               画面の抽象 API（init / show / status / poll_touch）
  saan_ui_null.c          実装 1: 何もしない（DevKit / QEMU / ホスト stub）
  saan_model.h            既存。saan_model_open() の宣言
  saan_model.c            実装 1: パーティション mmap（既存）
  saan_model_rodata.c     実装 2: 生成ヘッダ saan_model_blob.h を読む
  saan_console.{h,c}      readline → poll(timeout) に変更（タッチを見るため）
  main.c                  SAAN_BUFFERED を追加、saan_audio_* / saan_ui_* を呼ぶ
esp32/boards/m5unified/
  CMakeLists.txt          project。EXTRA_COMPONENT_DIRS=../../components、COMPONENTS=main
  main/CMakeLists.txt     ../../../main/{main.c,saan_console.c,saan_pcm.c,saan_model_rodata.c} + 下の 2 つ
  main/saan_audio_m5.cpp  実装 2: M5.Speaker（nnn112358 の saan_speaker.cpp 由来。MIT）
  main/saan_ui_m5.cpp     実装 2: M5GFX（同 saan_ui.cpp 由来）
  main/idf_component.yml  m5stack/m5unified ^0.2.0
  sdkconfig.defaults      共通（-O2 / 240 MHz / 16 MB / WDT off / IRAM→flash）
  sdkconfig.cores3        ESP32-S3 / Quad PSRAM 80 MHz / USB Serial-JTAG
  sdkconfig.core2         ESP32 / PSRAM / UART
  partitions.csv          16 MB / factory 3 MB / model パーティション無し
  README.md
scripts/
  blob_to_header.py       blob → const uint8_t[] ヘッダ（nnn112358 由来。MIT）
  test_blob_to_header.py  その回帰（int8 検出 / fp32 拒否の陽性対照 / SHA 一致）
  gen_erf_table.py        S3: erf の Hermite 表を csrc/erf_table.h に生成
csrc/
  saanotts_stream.c       S1: 解決済み重みを impl に持つ
  saanotts_int8.c         S2: 逆数乗算 + FPU 丸め / S4: 事前整列レイアウト / S5: PIE ループ
  saanotts.c              S3: saan_gelu が saan_erf_approx を呼ぶ
  erf_table.h             S3: 生成物（コミットする）
  erf_test.c              S3: erff との一致ゲート（陽性対照つき）
  prof_test.c             S1: --expect-no-lookup で LOOKUP 0 回を検査
scripts/export_c_weights.py   S4: blob v2（conv int8 を [cout][k][align16(cin)] で書く）
```

---

## Phase A — M5Unified 対応を本リポジトリに取り込む

### Task A-0: 板の同定と前提の凍結

**Files:**
- Modify: `docs/decisions.md`（D-047 を追記。D-046 は S1〜S5a の出力基準に使った）
- Modify: `docs/plan/s1-speed-implementation-plan.md`（この表の「板」列を確定値に）

- [ ] **Step 1: チップを読む**（ユーザーがスタックチャンを USB で繋いだら）

```bash
. ~/esp/esp-idf/export.sh && ls /dev/tty.usb* /dev/cu.usb* 2>/dev/null
esptool.py --port /dev/cu.usbmodem* chip_id      # 出力の "Chip is ESP32-S3 ..." / "Detecting chip type... ESP32" を記録
esptool.py --port /dev/cu.usbmodem* flash_id     # "Detected flash size: 16MB" を記録
```

- [ ] **Step 2: D-047 を書く**（chip / flash / PSRAM の種類と容量 / スピーカーの有無 / 板の名前。`esptool` の出力をそのまま貼る）
- [ ] **Step 3: commit** `decide(D-047): 実機の前提を凍結（<板名> / <chip> / <flash> / <psram>）`

⚠️ 板が繋がるまで A-1〜A-3 を先に進める（板に依存しない）。

### Task A-1: 音声出力の抽象 API と checksum の一本化

**Files:**
- Create: `esp32/main/saan_audio.h`, `esp32/main/saan_pcm.h`, `esp32/main/saan_pcm.c`, `esp32/main/saan_ui.h`, `esp32/main/saan_ui_null.c`
- Modify: `esp32/main/saan_i2s.c`（API 名と begin_utterance）, `esp32/main/main.c`, `esp32/main/CMakeLists.txt`, `esp32/host_stub/stubs.c`（`heap_caps_malloc/free` の stub）, `esp32/host_stub/esp_heap_caps.h`, `scripts/check_esp32_template.sh`（ゲート 7 の除外と 8 のソース一覧）
- Delete: `esp32/main/saan_i2s.h`

**Interfaces（Produces）:**

```c
/* saan_pcm.h — 純 C99。IDF に依存しない */
int16_t  saan_f32_to_i16(float x);          /* lrintf(x*32767) を飽和。統計を更新する */
uint32_t saan_pcm_clip_count(void);
uint64_t saan_pcm_checksum(void);           /* FNV-1a 64（LE 2 バイトずつ） */
uint32_t saan_pcm_samples(void);
int32_t  saan_pcm_absmax(void);
uint64_t saan_pcm_sqsum(void);
void     saan_pcm_reset(void);              /* 発話ごとに main.c が呼ぶ */

/* saan_audio.h — 実装は saan_i2s.c（DevKit）か saan_audio_m5.cpp（M5） */
#ifndef SAAN_AUDIO_PREROLL_SAMPLES
#define SAAN_AUDIO_PREROLL_SAMPLES 8192
#endif
bool saan_audio_setup(uint32_t sample_rate);
/* 発話の開始。n_samples ぶんの int16 を貯める場所を確保する。
 * ストリーミングなら SAAN_AUDIO_PREROLL_SAMPLES、貯める方式なら n_frames*SAAN_HOP。
 * DevKit 実装: n_samples <= SAAN_AUDIO_PREROLL_SAMPLES なら静的バッファ、それ以上は
 * heap_caps_malloc(SPIRAM 優先 → INTERNAL)。取れなければ false（**黙って切り詰めない**） */
bool saan_audio_begin_utterance(size_t n_samples);
bool saan_audio_preroll_push(const float *pcm, size_t n_samples);
bool saan_audio_start(void);
bool saan_audio_write_f32(const float *pcm, size_t n_samples);
/* 鳴らし終わるまで待ち、begin_utterance のバッファを返す */
void saan_audio_stop(void);

/* saan_ui.h — 実装は saan_ui_null.c（何もしない）か saan_ui_m5.cpp */
bool saan_ui_init(void);
void saan_ui_show(const char *title, const char *kana);      /* title は NULL 可 */
void saan_ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
bool saan_ui_poll_touch(void);                                /* 押された瞬間だけ true */
```

**Consumes:** `saan_stream_*`（csrc）。main.c の `synth_once` は `saan_i2s_*` → `saan_audio_*`、統計は `saan_pcm_*`。

- [x] **Step 1: ゲートを先に確認する（変更前の基準値）**

```bash
bash scripts/check_esp32_template.sh 2>&1 | grep -E 'bit 完全一致|NG!' 
```
Expected: `[厳密] C 一括版 → int16 と 27136 sample **bit 完全一致**` が student.bin / student_i8.bin の 2 回、NG 0。

- [x] **Step 2: `saan_pcm.{h,c}` を作る**。`saan_i2s.c` の `saan_f32_to_i16` と 4 統計 + `saan_i2s_pcm_reset` の中身を**移動**（コピーではなく。`saan_i2s.c` から消す）。`s_clips` も移す。
- [x] **Step 3: `saan_audio.h` を作り、`saan_i2s.c` を上の API 名に改名**。`saan_audio_begin_utterance` を追加（静的 `s_preroll[SAAN_AUDIO_PREROLL_SAMPLES]` を使い、超えるときだけ `heap_caps_malloc`。`saan_audio_stop` で free）。`SAAN_SKIP_I2S` の分岐はそのまま。
- [x] **Step 4: `saan_ui.h` + `saan_ui_null.c`**（4 関数とも何もしない。`poll_touch` は false）。
- [x] **Step 5: `main.c` を書き換える**: `#include "saan_audio.h" "saan_pcm.h" "saan_ui.h"`。`synth_once` の先頭で `saan_pcm_reset()` → `saan_ui_status("合成中…")`。プリロール前に `saan_audio_begin_utterance(SAAN_AUDIO_PREROLL_SAMPLES)`。`done:` で `saan_audio_stop()`。統計は `saan_pcm_*`。`tts_task` で `saan_audio_setup(SAAN_SR)` の直後に `saan_ui_init()`。`SAAN_BUFFERED` はこの Task では**入れない**（A-3）。
- [x] **Step 6: host stub を直す**: `esp_heap_caps.h` に `void *heap_caps_malloc(size_t, unsigned); void heap_caps_free(void *);` と `MALLOC_CAP_SPIRAM 0x4` を足し、`stubs.c` に `malloc/free` で実装。
- [x] **Step 7: `esp32/main/CMakeLists.txt` の SRCS** に `saan_pcm.c saan_ui_null.c` を足す。`check_esp32_template.sh` のゲート 7 の除外を `saan_model_*|saan_audio_*|saan_pcm_*|saan_ui_*|saan_f32_to_i16|saan_stub_*|saan_console_*|saan_dict_*|saan_kanji_*`、ゲート 8 のソース一覧に `saan_pcm.c saan_ui_null.c` を足す。
- [x] **Step 8: ゲート**

```bash
bash scripts/check_esp32_template.sh 2>&1 | grep -E 'bit 完全一致|NG!|手元のゲート'
grep -rn 'saan_i2s_pcm\|saan_f32_to_i16' esp32/main/*.c | grep -v saan_pcm.c | grep -c 'int16_t saan_f32_to_i16'   # → 0（定義が 1 か所）
```
Expected: bit 完全一致 ×2 / NG 0 / 定義は `saan_pcm.c` だけ。

- [x] **Step 9: QEMU で checksum**（既存の手順。`-DSAAN_QEMU=1 -DSAAN_ENABLE_PIE=1 -DSAAN_BOOT_SPEAK=1`）→ `0x04de91103a0e49f9` が出ること。
- [x] **Step 10: commit** `refactor(esp32): 音声出力を saan_audio API に、checksum を saan_pcm.c に一本化（bit 同一）`

### Task A-2: 重みを `.rodata` に埋める経路

**Files:**
- Create: `scripts/blob_to_header.py`（nnn112358 の `scripts/blob_to_header.py` を取り込む。冒頭に出所と MIT を書く）, `scripts/test_blob_to_header.py`, `esp32/main/saan_model_rodata.c`
- Modify: `esp32/CMakeLists.txt`（`SAAN_MODEL_RODATA` なら `esptool_py_flash_to_partition(model)` を呼ばない）, `esp32/main/CMakeLists.txt`（`SAAN_MODEL_RODATA` で SRCS を切替 + ヘッダ生成の `add_custom_command`）, `.github/workflows/ci.yml`（docs job に `test_blob_to_header.py`）, `.gitignore`

**Interfaces:**
- Produces: `saan_model_open(saan_weights *w)`（宣言は既存 `saan_model.h`。実装が 2 つになるだけ）。生成ヘッダは `${CMAKE_CURRENT_BINARY_DIR}/saan_model_blob.h` に `const uint8_t g_saan_model_blob[SAAN_MODEL_BLOB_BYTES] __attribute__((aligned(16)))` と `SAAN_MODEL_BLOB_SHA256` / `SAAN_MODEL_BLOB_DTYPE`。
- CMake: `-DSAAN_MODEL_RODATA=1`（既定 0 = mmap）。

- [x] **Step 1: `scripts/test_blob_to_header.py` を書く**（失敗するテスト）。stdlib だけで最小の SAAN v1 blob を 2 つ作る（`.scale` テンソルを持つ = int8 / 持たない = fp32）。検査: (a) int8 → 生成成功、`#define SAAN_MODEL_BLOB_SHA256 "<sha>"` が `hashlib.sha256(blob)` と一致、バイト列が `0x..,` で全部並ぶ（数を数える）、`aligned(16)` を含む。(b) fp32 → exit 1（**陽性対照**）。(c) `--allow-fp32` で通る。
- [x] **Step 2: 走らせて落ちることを確認** `uv run --no-project --python 3.12 python scripts/test_blob_to_header.py` → `blob_to_header.py` が無いので落ちる。
- [x] **Step 3: `scripts/blob_to_header.py` を置く**（出所ヘッダを付けて取り込む）。Step 1 を再実行 → 通る。
- [x] **Step 4: `saan_model_rodata.c`**（nnn112358 の `saan_model.c` 相当。`#include "saan_model_blob.h"` は**この翻訳単位だけ**。16 B 境界 assert → `saan_weights_open`。ログに SHA-256 と dtype）。
- [x] **Step 5: CMake**。`esp32/main/CMakeLists.txt`:

```cmake
if(SAAN_MODEL_RODATA)
    set(SAAN_MODEL_SRC "saan_model_rodata.c")
else()
    set(SAAN_MODEL_SRC "saan_model.c")
endif()
# ... idf_component_register(SRCS "main.c" ${SAAN_MODEL_SRC} "saan_i2s.c" "saan_pcm.c" "saan_ui_null.c" "saan_console.c" ${SAAN_MAIN_K} ...)
if(SAAN_MODEL_RODATA)
    set(SAAN_BLOB_HEADER "${CMAKE_CURRENT_BINARY_DIR}/saan_model_blob.h")
    set(SAAN_BLOB_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/../../scripts/blob_to_header.py")
    add_custom_command(OUTPUT "${SAAN_BLOB_HEADER}"
        COMMAND uv run --no-project python "${SAAN_BLOB_SCRIPT}" --blob "${SAAN_MODEL_BLOB}" --out "${SAAN_BLOB_HEADER}"
        DEPENDS "${SAAN_MODEL_BLOB}" "${SAAN_BLOB_SCRIPT}" VERBATIM)
    add_custom_target(saan_blob_header DEPENDS "${SAAN_BLOB_HEADER}")
    add_dependencies(${COMPONENT_LIB} saan_blob_header)
    target_include_directories(${COMPONENT_LIB} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_MODEL_RODATA=1)
endif()
```
`esp32/CMakeLists.txt` の `esptool_py_flash_to_partition(flash "model" ...)` を `if(NOT SAAN_MODEL_RODATA)` で囲う。`scripts/check_cmake_syntax.cmake` に `add_custom_command / add_custom_target / add_dependencies / target_include_directories / target_compile_definitions` のスタブを足す。
- [x] **Step 6: QEMU で rodata 経路の checksum**

```bash
cd esp32 && idf.py -B build_rodata -DSDKCONFIG=build_rodata/sdkconfig -DSAAN_QEMU=1 -DSAAN_ENABLE_PIE=1 \
    -DSAAN_BOOT_SPEAK=1 -DSAAN_MODEL_RODATA=1 build
# merge_bin → qemu（既存手順）。ログに "ヘッダ埋め込み (.rodata = flash) 643936 B / dtype int8" と 0x04de91103a0e49f9
```
- [x] **Step 7: ci.yml の docs job に** `uv run --no-project --python 3.12 python scripts/test_blob_to_header.py` を足す。`bash scripts/check_esp32_template.sh` 通過。
- [x] **Step 8: commit** `feat(esp32): 重みを .rodata に埋める経路（PSRAM 有効な板で mmap が落ちる報告への対応）`

### Task A-3: `esp32/boards/m5unified/` と、main.c の `SAAN_BUFFERED` / タッチ

**Files:**
- Create: `esp32/boards/m5unified/{CMakeLists.txt,sdkconfig.defaults,sdkconfig.cores3,sdkconfig.core2,partitions.csv,README.md}`, `esp32/boards/m5unified/main/{CMakeLists.txt,idf_component.yml,saan_audio_m5.cpp,saan_ui_m5.cpp}`
- Modify: `esp32/main/main.c`（`SAAN_BUFFERED` と、対話ループのタッチ）, `esp32/main/saan_console.{h,c}`（`saan_console_poll`）, `esp32/main/CMakeLists.txt`（`SAAN_BUFFERED` を渡す）, `.gitignore`（`/esp32/boards/*/build*/`, `managed_components/`, `dependencies.lock`）, `scripts/check_partitions.py`（`--file` で新表）

**Interfaces:**

```c
/* saan_console.h（変更） */
#define SAAN_CONSOLE_PENDING  (-3)
/* timeout_ms 待って 1 バイトも来なければ PENDING。行が完成したら長さ（>=0）。
 * 行の状態は呼び出しをまたいで持ち越す（CRLF の LF 吸い込み含む） */
int  saan_console_poll(const char **out, uint32_t timeout_ms);
void saan_console_prompt(void);          /* "\r\nかな> " を出す。行を受けたあとに main.c が呼ぶ */
```
`saan_console_readline` は削除（呼ぶ側は main.c だけ）。

`main.c` の `SAAN_BUFFERED`（既定 0）: 1 なら `saan_audio_begin_utterance(n_frames * SAAN_HOP)` して全チャンクを `preroll_push`、`start` で一気に鳴らす（nnn112358 の `synth_once` と同じ構造）。対話ループ: `saan_console_poll(&line, 20)` が PENDING のとき `saan_ui_poll_touch() && g_last_n_ids > 0` なら直前の列を `synth_once`。`g_last_n_ids` は G2P 失敗時に 0 にする。

- [x] **Step 1: `saan_console_poll` を実装**（`port_read1(c, timeout_ms)` に `pdMS_TO_TICKS` を渡す。nnn112358 の `saan_console.c` の差分どおり）。`SAAN_INTERACTIVE=0` では従来どおりコンパイル外。
- [x] **Step 2: `main.c`** に `SAAN_BUFFERED` と タッチ再生、`g_last_n_ids` / `g_last_text`、`saan_ui_show(SAAN_DEMO_TEXT, SAAN_DEMO_INTERMEDIATE)`（起動時）と `saan_ui_show(NULL, text)`（入力時）、`saan_ui_status("xRT %.2f  途切れ %d/%d", ...)`。
- [x] **Step 3: DevKit 構成の回帰**: `bash scripts/check_esp32_template.sh`（bit 一致 ×2）と QEMU（`0x04de91103a0e49f9`）。QEMU の UART に `きょ][おわよ][いて][んきです°ね` を流して同じ checksum（M-63 の T1 と同じ手順）。
- [x] **Step 4: `esp32/boards/m5unified/` を作る**。`saan_audio_m5.cpp` は nnn112358 の `saan_speaker.cpp` を `saan_audio_*` 名に合わせ、`saan_f32_to_i16` と統計を**削って `saan_pcm.h` を呼ぶ**（重複ゼロ）。`saan_ui_m5.cpp` は `saan_ui.cpp` そのまま（`saan_model.h` の `SAAN_MODEL_ORIGIN_*` は `saan_model_rodata.c` 側に持たせるか、UI 側の文字列にする）。`CMakeLists.txt`:

```cmake
cmake_minimum_required(VERSION 3.16)
get_filename_component(SAAN_ESP32 "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(EXTRA_COMPONENT_DIRS "${SAAN_ESP32}/components")
if(NOT DEFINED SAAN_MODEL_BLOB)
    set(SAAN_MODEL_BLOB "${SAAN_ESP32}/../csrc/student_i8.bin")
endif()
if(NOT EXISTS "${SAAN_MODEL_BLOB}") message(FATAL_ERROR "...") endif()
set(SAAN_MODEL_BLOB "${SAAN_MODEL_BLOB}" CACHE INTERNAL "")
set(SAAN_MODEL_RODATA 1 CACHE INTERNAL "M5 は .rodata 埋め込みのみ（PSRAM と mmap が競合）")
if(NOT DEFINED SAAN_BOOT_SPEAK) set(SAAN_BOOT_SPEAK 1) endif()
if(NOT DEFINED SAAN_ENABLE_PIE)
    if("$ENV{IDF_TARGET}" STREQUAL "esp32s3" OR IDF_TARGET STREQUAL "esp32s3") set(SAAN_ENABLE_PIE 1) else() set(SAAN_ENABLE_PIE 0) endif()
endif()
set(COMPONENTS main)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(saanotts_m5)
```
`components/saanotts_core/CMakeLists.txt` に **`if(SAAN_ENABLE_PIE AND NOT IDF_TARGET STREQUAL "esp32s3") message(FATAL_ERROR ...)`** を足す（S3 以外で PIE を黙って無効にしない）。
- [x] **Step 5: ビルドが通る**（ネットワークが要る: 初回に M5Unified を取る）

```bash
cd esp32/boards/m5unified && idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" build
idf.py -B build_core2 -DSDKCONFIG=build_core2/sdkconfig -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" build
```
Expected: 両方 `Project build complete`。`.dram0.bss` と app サイズをログから控える（M-79 の形）。`-DSAAN_ENABLE_PIE=1` を core2 に渡すと CMake で止まる（陽性対照）。
- [x] **Step 6: `scripts/check_partitions.py --file esp32/boards/m5unified/partitions.csv`** が通る（model 行無しを許す修正が要れば入れる）。`check_doc_links.py` 通過。
- [x] **Step 7: commit** `feat(esp32): M5Unified 対応（boards/m5unified）。SAAN_BUFFERED とタッチ再生を main.c に足した`

### Task A-4: 実機（ユーザーのスタックチャン）

**Files:**
- Modify: `docs/measurements.md`（M-81）, `docs/research/s1-m5-cores3-speed.md`（§7 の「実機の内訳は無い」を更新）

- [ ] **Step 1: 焼く**（A-0 の板に合わせる）

```bash
cd esp32/boards/m5unified
idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -p /dev/cu.usbmodem* flash monitor        # 起動時に 1 文喋る。ログの「出力 PCM」を控える
```
- [ ] **Step 2: 突き合わせ**（S3 以降のコア）: ESP32-S3 なら `0xa69a7ebbb5ccb05f / 9627 / 74,264,237,672`（W8A8+PIE）。ESP32 なら `-DSAAN_ENABLE_PIE=0` で `0xe4b645c30835d42d / 9529 / 74,155,591,505`。**一致しなければ速度は測らない**（移植が壊れている）。
- [ ] **Step 3: 速度**（`SAAN_PROFILE=0` のビルド）: 定常 xRT / 初回 pull / アンダーラン。S3 なら報告値 1.554 と比べる。
- [ ] **Step 4: 内訳**（`-DSAAN_PROFILE=1` で焼き直す）: 表を丸ごと控える。**これが S1〜S5 の基準線**。
- [ ] **Step 5: M-82 を書く**（再現コマンド・ログ原文・表。M-81 は S1〜S3 のホスト / QEMU 記録に使った）。`docs/README.md` の索引を M-82 に。
- [ ] **Step 6: commit** `feat(M-82): 実機で初めて自己実測した（<板> / xRT / 内訳）`

### Task A-5: 手順書

- [x] `esp32/TESTING.md` に「M5Stack（スタックチャン）で試す」節（A-4 のコマンドと、板の同定法、期待 checksum の表）
- [x] `README.md` の「C. ESP32-S3 で喋らせる」に板の選択（DevKit / M5）を 1 行ずつ
- [x] `docs/research/s1-m5-cores3-speed.md` §6 を「実装済み（A-1〜A-3）」に書き換え
- [x] `check_doc_counters.py` / `check_doc_links.py` 通過 → commit `docs: M5 の手順を TESTING.md と README に足した`

---

## Phase B — 速度（1 つずつ。ホスト → QEMU → 実機）

**各 Task 共通のゲート**（`make -C csrc all-test` に含まれるもの + 追加）:

| ゲート | コマンド | 何を守るか |
|---|---|---|
| stream G2 | `make -C csrc stream` | ストリーミング = 一括版と bit 一致 |
| int8-golden | `make -C csrc int8-golden` | int8 コア = fake-quant 参照（Pearson ≥ 0.98 / SNR ≥ 40 dB） |
| int8-e2e | `make -C csrc int8-e2e` | W8A32 = fp32 に対し 平均 ≥ 27 / 最小 ≥ 25 dB |
| int8-e2e-a8 | `make -C csrc int8-e2e-a8` | W8A8 の SNR（⚠️ 既定ゲートは満たさない = M-55。**変更前後の値を並べて劣化していないこと**） |
| pad | `make -C csrc pad` | パディングの毒テスト |
| QEMU checksum | 既存手順 | 同一ターゲットの bit 一致（丸め水準の変更は新値を記録） |
| 実機の表 | `-DSAAN_PROFILE=1` | 対応する区間が消えた / 1 step のサイクル |

### Task S1: テンソル検索を init で 1 回に（bit 同一）

**Files:**
- Modify: `csrc/saanotts_stream.c`, `csrc/prof_test.c`（`--expect-no-lookup`）, `csrc/Makefile`（`prof` に付ける）

**Interfaces（内部）:**

```c
typedef struct { saan_wref c1w, c2w; const float *c1b, *c2b, *ng, *nb; } saan_acblk_w;
typedef struct { saan_wref dw, p1w, p2w, cdw, cuw; const float *p1b, *p2b, *cdb, *cub, *gm; } saan_decblk_w;
/* struct saan_stream_impl に追加 */
saan_acblk_w  tokw[3], acw[5];
saan_decblk_w decw[5];
saan_wref ow, iw, hdw, how;
const float *ib, *hdb, *hob, *emb, *pos;
/* stream_init_body の末尾で呼ぶ。1 つでも欠けたら SAAN_ERR_MISSING */
static saan_status resolve_weights(saan_stream *st);
```

- [x] **Step 1: 失敗する検査を足す**: `prof_test.c` に `--expect-no-lookup`（LOOKUP の `cnt` が INIT の外で 0 でなければ exit 1）。現状の LOOKUP は 101.81 回/step なので**落ちる**ことを確認: `make -C csrc prof_test && ./csrc/prof_test csrc/student_i8.bin --expect-no-lookup` → exit 1。
- [x] **Step 2: `resolve_weights`** を書き、`ac_step_body / dec_inp_step_body / dec_step_body / compute_tokens_body / make_hf_body / step_chunk_body` の `saan_w / saan_tf` 呼び出しを impl のフィールド参照に置き換える。**計算順序は 1 つも変えない。**
- [x] **Step 3: ゲート**: `make -C csrc all-test`（stream G2 が bit 一致）/ `./csrc/prof_test csrc/student_i8.bin --expect-no-lookup` → exit 0 / QEMU checksum `0x04de91103a0e49f9` 不変。
- [ ] **Step 4: 実機**（板があれば）`SAAN_PROFILE=1` の表で LOOKUP が 0 回、1 step のサイクル。⚠️ 板待ち
- [x] **Step 5: commit** `perf(S1): テンソル検索を init で 1 回に（bit 同一。step 内 LOOKUP 102 → 0 回）`

### Task S2: 活性化の量子化からソフト除算と libm 呼び出しを消す（丸め水準）

**Files:**
- Modify: `csrc/saanotts_int8.c`（`saan_quantize_act_i8p`）, `esp32/pie_probe/main/probe.c`（丸めの一致テスト C）

**Interfaces（内部）:**

```c
/* 最近接偶数丸めで int32 に。Xtensa FP なら round.s、それ以外は (int32_t)rintf(v) */
static inline int32_t saan_rint_i32(float v);
```
`saan_quantize_act_i8p`: `const float inv = 127.0f / amax; sx[t] = amax / 127.0f;`（`sx` は従来どおり `amax/127`。逆量子化の意味を変えない）→ `int32_t q = saan_rint_i32(x * inv); clamp ±127`。

- [x] **Step 1: 丸めのテスト（pie_probe に C 節）**: 入力 `{0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 3.4999998, 126.5, -126.5, 127.49, 1e-8, -0.0}` で `saan_rint_i32` と `(int32_t)rintf` が全一致。QEMU で通す。**先にホストで `round.s` の定義を `#if defined(__XTENSA__) && defined(__XTENSA_FP__)` に閉じる**。
- [x] **Step 2: 静的ゲート**: `xtensa-esp32s3-elf-objdump -d saanotts_int8.o` で `saan_quantize_act_i8p` の中に `call*` が `memset` 以外に無いこと（`__divsf3` / `rintf` が消えた）。
- [x] **Step 3: SNR ゲート**: `make -C csrc int8-e2e-a8` の平均/最小を変更前（v3 W8A8: 平均 24.24 / 最小 21.94 dB）と並べ、**下がっていない**（±0.1 dB）。`make -C csrc all-test` 通過（W8A32 経路は触らないので bit 一致のまま）。
- [x] **Step 4: QEMU checksum**: 新しい値を控え、`|max|` が 9744 のまま、Σx² の相対差 ≤ 1e-6 であることを確認。**新値を M-番号で記録**（S2 以降の基準に）。
- [ ] **Step 5: 実機**の表で QUANT の cyc/要素。⚠️ 板待ち
- [x] **Step 6: commit** `perf(S2): 活性化の量子化を逆数乗算 + round.s に（丸め水準。QEMU checksum <新値>）`

### Task S3: GELU の `erff` を Hermite 表に（丸め水準）

**Files:**
- Create: `scripts/gen_erf_table.py`, `csrc/erf_table.h`（生成物）, `csrc/erf_test.c`
- Modify: `csrc/saanotts.c`（`saan_gelu` → `saan_erf_approx`）, `csrc/saanotts_internal.h`（宣言）, `csrc/Makefile`（`erf` ターゲット、`all-test` に追加）, `.github/workflows/ci.yml`（csrc job に `make -C csrc erf`）

**Interfaces:**

```c
/* saanotts_internal.h */
float saan_erf_approx(float x);   /* |x| >= 3.90625 で ±1。区間 h=1/32 の 3 次 Hermite（値と導関数の表） */
```
表: `static const float kErfV[126]`, `kErfD[126]`（x = i/32, i=0..125。`erf(x)` と `2/sqrt(pi)*exp(-x^2)`）。理論誤差 h⁴/384·max|erf⁗| ≈ 1.1e-8。

- [x] **Step 1: `erf_test.c`（失敗するテスト）**: 乱数 1,000,000 点（[-5,5] 一様）+ 境界値で `max|saan_erf_approx - erff| <= 2e-7`、**陽性対照**: `-DSAAN_ERF_TEST_LINEAR`（線形補間に落とす）で `> 2e-7` になって落ちること。`make -C csrc erf` は両方回して「本番 OK / 陽性対照 NG!」で合格。
- [x] **Step 2: `gen_erf_table.py`** → `csrc/erf_table.h`（ヘッダに生成コマンドと sha を書く。`gen_fft_tables.py` と同じ流儀）。
- [x] **Step 3: `saan_erf_approx` を実装**し `saan_gelu` から呼ぶ。`make -C csrc erf` 通過。
- [x] **Step 4: 静的ゲート**: `nm -u saanotts.o` に `erff` が無い。
- [x] **Step 5: SNR / checksum**: S2 と同じ（int8-e2e-a8 の平均/最小が下がらない。**W8A32 の `int8-e2e` も**丸め水準で動くので、平均 ≥ 27 / 最小 ≥ 25 を満たすこと）。QEMU の新 checksum を記録。⚠️ **fp32 経路（golden test）も動く** → `make -C csrc test` の SNR が 40 dB 以上のままであることを確認（現状 117 dB）。
- [x] **Step 6: commit** `perf(S3): GELU の erff を 3 次 Hermite 表に（max|Δ| <= 2e-7。陽性対照つき）`

### Task S4: blob v2 — 重みを事前に `[cout][k][align16(cin)]` で書く（bit 同一）

**Files:**
- Modify: `scripts/export_c_weights.py`（`VERSION = 2`、conv int8 のレイアウト）, `csrc/saanotts.c`（`saan_weights_open` は version 2 のみ。1 は `SAAN_ERR_VERSION`）, `csrc/saanotts_int8.h/.c`（`saan_wref` に `int cinp`、`saan_conv1d_i8a` が事前整列を直接読む、`saan_conv1d_i8`（W8A32）も新レイアウトを読む）, `csrc/int8_test.c`（2c の突き合わせを新レイアウトで）, `csrc/int8_pad_test.c`, `esp32/pie_probe/main/probe.c`, `csrc/Makefile`, `scripts/check_release_assets.py` / `README.md` の資産表（形式 v2 を明記）

**Interfaces:**

```c
typedef struct {
    const float  *f32;
    const int8_t *q;       /* v2: [cout][ksz][cinp] で 0 埋め済み。16 B 境界 */
    const float  *scale;
    int32_t cinp;          /* align16(cin)。fp32 なら 0 */
} saan_wref;
```
`saan_conv1d_i8a(..., const int8_t *W, ...)` の `W` は v2 レイアウトを前提にし、`wt` スクラッチと転置ループを削除。PIE は `W + (o*ksz + k)*cinp` を直接 `ee.vld.128.ip` に渡す（**blob 内の各 int8 テンソルの offset は 16 の倍数**。`check_esp32_template.sh` のゲート 4 が見ている）。
exporter: `q2d [cout, cin*k]` → `q3 = q2d.reshape(cout, cin, k).transpose(0, 2, 1)` → 末尾を `cinp` に 0 埋め → `w.add(full, q3p, DT_I8)`（dims は `(cout, cin, k)` のまま。nbytes = cout·k·cinp）。1-D/2-D（`duration.proj` など）は `[cout][cinp]`。

- [x] **Step 1: 失敗するテスト**: `int8_test.c` 2c に「blob の int8 テンソルは `nbytes == cout*ksz*align16(cin)`」を足す → v1 blob で落ちる。
- [x] **Step 2: exporter を v2 に**し `make -C csrc student_i8.bin golden_i8.bin`（教師 ckpt が要る。手元）。`student.bin` も再生成（fp32 は中身不変・version だけ 2）。
- [x] **Step 3: コア**を書き換え、`make -C csrc all-test` 全通過（**int8-e2e の SNR と stream の bit 一致は v1 と同じ値**。積和の順序を変えていないので `dump_pcm` の出力は v1 と bit 同一 → `cmp` で確認）。
- [x] **Step 4: v1 blob を渡すと止まる**（陰性対照）: 旧 `student_i8.bin` を退避しておき `saan_weights_open` が `SAAN_ERR_VERSION` を返す。
- [x] **Step 5: QEMU**: checksum が S3 の値のまま / `pie_probe` PASS / `prof` の WCOPY 0 回。
- [ ] **Step 6: リリース**: 資産 `saanotts-jp-v3-int8.bin` を v2 で上げ直す（D-045 = latest のみ）。`README.md` の資産表に「形式 SAAN v2（v0.3.0 以降のコア）」。`check_release_assets.py` 通過。⚠️ **外向きの作業なので main へのマージ時にユーザーと行う**（このブランチでは手元の blob だけ v2）
- [x] **Step 7: commit** `perf(S4): blob v2 — 重みを [cout][k][align16(cin)] で持ち、実行時の転置を無くした（bit 同一）`

### Task S5: PIE ループの改良（bit 同一）

**Files:**
- Modify: `csrc/saanotts_int8.c`（`saan_dot_i8_pie` と `saan_conv1d_i8a` の t ループ）, `esp32/pie_probe/main/probe.c`

- [x] **Step 1: pie_probe に形状を足す**（既存の 7 形状で cin=16 〜 304 を覆っている。追加不要）（cin=48 ksz=1 T=8 の hout 形状 / cin=304 / cin=76→80 k=1 / cin=48 k=5 / cin=12→16）。現状 PASS を確認。
- [x] **Step 2: ロード併合**: 内積ループを `ee.vld.128.ip q1` → `ee.vmulas.s8.accx.ld.ip q0, pa, 16, q0, q1` の形に（2 命令/16 MAC）。`k` の端数（cinp/16 が奇数）を扱う。pie_probe PASS。
- [ ] **Step 3: 重みをレジスタに保持**（**S5b。実機の MAC cyc/要素を見てから**）: `cinp <= 128`（q0..q7 に収まる）の層は出力チャネル o の重み行を 1 回ロードし、t を回して活性化だけロード。**pie_probe の bit 一致** + `objdump` で `ee.` 命令数を記録。
- [x] **Step 4: QEMU checksum 不変**（`0xa69a7ebbb5ccb05f`）/ - [ ] 実機の MAC cyc/要素（板待ち）
- [x] **Step 5: commit**（S5a） `perf(S5): PIE の内積をロード併合にし、重み行をレジスタに保持（bit 同一）`

---

## Phase C — 判断と後続

- [ ] **D-048**: 出荷ファームの既定を W8A8+PIE にするか（M-55 の「知覚的に無料」+ 実機の xRT）。残りタスク #4 の決着
- [ ] S1〜S5 後の実機の表を M-番号に。1 コアの床を確定
- [ ] S6（token block の持ち越し）/ S7（`SAAN_CHUNK` 16）/ S8（2 コア）を、床を見てから起票
- [ ] QACC 外積形（16 出力同時）は 20 bit 溢れの解析を spike として別起票
- [ ] K トラック × PSRAM: `esp_partition_mmap` が落ちる報告を実機で切り分け（`esp_mmu_map_dump_mapped_blocks`）

## 進捗（2026-09-02）

| Task | 状態 | 記録 |
|---|---|---|
| A-1 音声 API / saan_pcm | ✅ `1371332` | host stub bit 一致 ×2 / QEMU `0x04de91103a0e49f9` |
| A-2 .rodata 経路 | ✅ `74a88a3` | QEMU 両経路 `0x04de91103a0e49f9` / app 285,440 → 928,832 B |
| A-3 boards/m5unified | ✅ `78a55af` | cores3（PIE）1,344,432 B・.bss 235,600 / core2 1,331,808 B・.bss 22,752（arena は PSRAM） |
| A-5 手順書 | ✅ `12ea114` | |
| **A-0 / A-4 実機** | ⏳ **板待ち**（ユーザーのスタックチャン。種類は未同定） | |
| **S1 検索を init で 1 回に** | ✅ | pull 中の LOOKUP **42,280 → 0 回**（20 発話）/ all-test bit 一致 / QEMU checksum 不変・PIE 5 命令 / QEMU icount の 1 step **811,001 → 741,973**（−8.5%。⚠️ サイクルではない） |
| **S2 量子化の除算と rintf を消す** | ✅ | 要素ごとの `__divsf3` + `rintf` 呼び出し → `mul.s` + `round.s`（フレームごとの除算 2 回だけ残る）/ W8A8 e2e 平均 24.24 → **24.21 dB**・最小 21.94 不変 / QEMU checksum **`0x04de91103a0e49f9` のまま**（この 1 文では量子化値が 1 つも変わらなかった。丸め水準の宣言どおり、他の文では変わりうる）/ pie_probe C 節: round.s == rintf（22 値、陽性対照つき） |
| **S3 GELU の erff を Hermite 表に** | ✅ | erff との max\|Δ\| 1.19e-7（陽性対照 1.18e-4 は落ちる）/ golden fp32 SNR 118.97 dB / W8A32・W8A8 の e2e SNR 不変 / **QEMU 基準 checksum が変わった**: W8A8+PIE `0xa69a7ebbb5ccb05f`（\|max\| 9627）/ W8A32 `0xe4b645c30835d42d`（\|max\| 9529 同一・Σx² 8.7e-9）/ QEMU icount 1 step **557,152**（S1 前 811,001、−31%）。記録は M-81 |
| **S4 blob v2（事前整列）** | ✅ | **bit 同一**（S3 と 24 文 cmp 0/24、QEMU 両構成の checksum 不変）/ blob 643,936 → 654,032 B / WCOPY 0 / v1 int8 blob は SAAN_ERR_VERSION で拒否 / QEMU icount 1 step 557,152 → **454,548**（S1 前から −44%）。⚠️ リリース資産の v2 化はマージ時 |
| **S5a PIE ループ（ロード併合 + loopnez）** | ✅ | **bit 同一**（pie_probe 7 形状 / QEMU checksum 不変）/ 16 MAC あたり 5 → 2 命令 / QEMU icount 1 step 454,548 → **412,619**（S1 前から −49%） |
| S5b 重み行をレジスタに保持 | ⏳ 実機の表を見てから | |
| **次にやること** | **A-0 / A-4（板の同定 → 焼く → checksum → xRT → `SAAN_PROFILE=1` の表）** | 期待 checksum: W8A8+PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d` |

## 実行の順序と依存

```
A-1 → A-2 → A-3 → (板が繋がったら) A-0 → A-4 → A-5
S1 → S2 → S3 → S4 → S5      ← A-3 の後ならいつでも。実機の表は A-4 以降
```
A-1〜A-3 と S1〜S3 は板が無くても完了できる（ホスト + QEMU で bit 一致 / SNR まで）。

## Self-Review

- Spec coverage: S-1 §5 の S1〜S5 → Task S1〜S5 / §6 の 5 行 → A-1〜A-3 / M-80 の「実機の内訳は無い」→ A-4。S6〜S8 は Phase C（床を測ってから）
- Placeholder: 「TBD」無し。A-3 の `saan_audio_m5.cpp` は既存ファイル（MIT）の名前合わせと `saan_pcm.h` への置き換えで、内容は特定済み
- Type consistency: `saan_audio_*` 7 関数 / `saan_pcm_*` 6 関数 + `saan_f32_to_i16` / `saan_ui_*` 4 関数 / `saan_console_poll` / `saan_wref.cinp` を全 Task で同じ名前で使っている
