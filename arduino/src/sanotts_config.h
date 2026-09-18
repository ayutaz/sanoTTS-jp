/* sanoTTS-jp — Arduino / PlatformIO ライブラリの **唯一の設定点**。
 *
 * ## なぜこのファイルが要るのか
 *
 * ⚠️ **Arduino のライブラリ仕様（1.5 format）には、ライブラリが独自の `-D` や
 *    インクルードパスを渡す手段が無い。** ESP-IDF 版で CMake が渡していたフラグ
 *    （`SAAN_INT8_ACT` / `SAAN_PIE` / `CHARSET_UTF_8` / `LABEL_IDS_EXTERNAL_SCRATCH` /
 *    `SAAN_PORT_HEADER`）は、**各翻訳単位の先頭でこのヘッダを読む**以外に届かない。
 *    `scripts/build_arduino_lib.py` が `src/core/` の .c の前置きとしてそれを入れている。
 *    （⚠️ このコメント内で `core/` に続けてアスタリスクを書かない。ブロックコメントの中の
 *      スラッシュ + アスタリスクは -Wcomment で警告になる。実際に踏んだ）
 *
 * ## 上書きのしかた
 *
 *   PlatformIO … platformio.ini の `build_flags = -DSANOTTS_ENABLE_KANJI=0`
 *   Arduino IDE … **このファイルを直接編集する**（IDE からフラグは渡せない）
 *
 * ⚠️ **`SANOTTS_*` はあなたが触る面、`SAAN_*` はコアの内部フラグ。** 混ぜないこと。
 *    `SAAN_*` を直接立てると、下の「2 つをセットでしか立てない」仕掛けを回避できてしまう。
 */
#ifndef SANOTTS_CONFIG_H
#define SANOTTS_CONFIG_H

/* ⚠️ **これが無いと `CONFIG_IDF_TARGET_ESP32S3` が見えず、下の PIE の判定が
 *    黙って false になる。** ビルドは成功し、音も出て、**2 倍以上遅いだけ**なので
 *    気づけない。実際に踏んだ（2026-09-13。`saanotts_int8.c.o` の `ee.` が 0 だった）。
 *    ESP-IDF 版は CMake が IDF_TARGET を見ていたので、この穴は無かった。
 *
 *    検出は `scripts/ci_arduino_build.sh` の G-AR6（ESP32-S3 向けビルドの
 *    `saanotts_int8.c.o` に PIE 命令が在るか。陰性対照は非 S3 で 0 件）。 */
#if defined(__has_include)
#  if __has_include("sdkconfig.h")
#    include "sdkconfig.h"
#  endif
#endif

/* --- 1. 漢字かな交じり文を端末で読むか ------------------------------------
 *
 * 既定は有効。⚠️ **辞書 13.7 MB を `dict` パーティションに焼く必要がある**が、
 * 焼いていなくても**起動は止まらない** — `saan_dict.c` が漢字経路だけ無効にして
 * **かな専用で続く**（D-063）。`SanoTTS::kanjiReady()` が false になる。
 *
 * ⚠️ 無効にすると app が約 700 KB 小さくなる（取り込んだ Open JTalk 14 本と
 *    辞書リーダが消える）。かな中間表現しか入れないなら 0 にしてよい。 */
#ifndef SANOTTS_ENABLE_KANJI
#define SANOTTS_ENABLE_KANJI 1
#endif

/* --- 2. W8A8 + PIE（ESP32-S3 の整数 SIMD）----------------------------------
 *
 * ⚠️ **これ無しでは実時間に間に合わない。** 同じ M5 CoreS3 で
 *    W8A32 は xRT 0.92〜1.09、W8A8 + PIE は **0.446**（M-86 / M-90）。
 *
 * ⚠️ **`SAAN_INT8_ACT` と `SAAN_PIE` を別々に立てられないようにしてある。**
 *    PIE カーネルは W8A8 経路（`saan_conv1d_i8a`）の中にしか無いので、
 *    `SAAN_PIE` だけを立てても **1 命令も出ない**のに「有効にしたつもり」になる。
 *    ESP-IDF 版の CMakeLists.txt が同じ不変条件を持っている。
 *
 * ⚠️ **ESP32-S3 以外では自動的に W8A32 に落ちる**（`ee.*` 命令が無い板では
 *    アセンブラが落ちるため）。ESP-IDF 版は FATAL_ERROR にしているが、
 *    ライブラリでは板を選ぶのは使う人なので自動で落とす。**手で 1 を立てた場合だけ止める。** */
#if !defined(SANOTTS_ENABLE_PIE)
#  if defined(CONFIG_IDF_TARGET_ESP32S3)
#    define SANOTTS_ENABLE_PIE 1
#  else
#    define SANOTTS_ENABLE_PIE 0
#  endif
#endif

#if SANOTTS_ENABLE_PIE && !defined(CONFIG_IDF_TARGET_ESP32S3)
#  error "SANOTTS_ENABLE_PIE=1 は ESP32-S3 専用（ee.* 命令が無い板ではアセンブラが落ちる）。0 にすると W8A32（移植可能 C）で動く"
#endif

#if SANOTTS_ENABLE_PIE
#  define SAAN_INT8_ACT 1
#  define SAAN_PIE      1
#endif

/* --- 3. 漢字に付いてくる内部フラグ ----------------------------------------
 *
 * `CHARSET_UTF_8`              … 取り込んだ Open JTalk が無いと `#error` で止まる
 * `LABEL_IDS_EXTERNAL_SCRATCH` … K-7 のトークン表 10,240 B を .bss ではなく arena から借りる
 *                                （⚠️ これが無いと内部 DRAM が 10 KB 減る） */
#if SANOTTS_ENABLE_KANJI
#  define SAAN_KANJI                 1
#  define CHARSET_UTF_8              1
#  define LABEL_IDS_EXTERNAL_SCRATCH 1
#endif

/* --- 4. 配置（T5-G2）-------------------------------------------------------
 *
 * erf 表 1,032 B（`core/erf_table.h`）を内部 DRAM に置く。flash の `.rodata` にあると、
 * 1 step に 584 KB 流れる重みのストリームと D-cache を争う（M-82 §4）。
 * ⚠️ **配置が変わるだけで値は変わらない**（PCM は bit 同一）。 */
#ifndef SAAN_PORT_HEADER
#define SAAN_PORT_HEADER "saan_port_esp32.h"
#endif

/* --- 5. 重みの置き場 -------------------------------------------------------
 *
 * 既定 0 … `.rodata` 埋め込み。重みライブラリ（SanoTTS-jp-voice-tsukuyomi-v4）の
 *           `saan_model_blob.h` を `core/saan_model_rodata.c` が include する。
 *           **追加の焼き込み手順が要らない。** app が 654,032 B 大きくなる。
 *      1 … flash の `model` パーティションを mmap（`core/saan_model.c`）。
 *           app が小さく、重みだけ差し替えられる。partitions.csv と esptool が要る。
 *
 * ⚠️ **fp32 の blob を渡さないこと。** W8A8 + PIE は fp32 blob では 1 命令も効かない
 *    （`saan_conv1d_w` が `W.f32` で早期 return する）。配っているのは int8。 */
#ifndef SANOTTS_MODEL_FROM_PARTITION
#define SANOTTS_MODEL_FROM_PARTITION 0
#endif

/* 重みライブラリが入っているか。
 *
 * ⚠️ **`SanoTTS.h` は無条件に `<saanotts_jp_voice.h>` を include する。**
 *    条件つきにすると Arduino IDE / arduino-cli の依存解決が走らず、
 *    **入れてあるのに使われない**（SanoTTS.h の長い ⚠️ を読むこと）。
 *    ここの `__has_include` は「無条件 include が既に通った後」に評価されるので、
 *    重みライブラリが在れば 1 になる。
 *
 * ⚠️ 0 のときは `SanoTTSModelStub.c` が `saan_model_open()` を
 *    「重みが無い」を返す実装で埋めるので**リンクは通る**（`begin()` が false）。
 *    これはパーティション構成と CI のカーネル検査（`SANOTTS_NO_VOICE_LIB=1`）のため。 */
#ifndef SANOTTS_HAVE_VOICE
#  if SANOTTS_MODEL_FROM_PARTITION || defined(SANOTTS_NO_VOICE_LIB)
#    define SANOTTS_HAVE_VOICE 0
#  elif defined(__has_include)
#    if __has_include(<saanotts_jp_voice.h>)
#      define SANOTTS_HAVE_VOICE 1
#    else
#      define SANOTTS_HAVE_VOICE 0
#    endif
#  else
#    define SANOTTS_HAVE_VOICE 0
#  endif
#endif

/* --- 6. 辞書の SHA-256 -----------------------------------------------------
 *
 * ESP-IDF 版はビルド時に CMake が `file(SHA256 ...)` で焼いていた（D-063）。
 * Arduino にはそれが無いので、**書きたければここに書く**:
 *
 *     #define SANOTTS_DICT_SHA256 "3f5a…（64 桁）"
 *
 * ⚠️ **書かなくても動くが、黙っては飛ばさない** — `core/saan_dict.c` が起動時に
 *    「⚠️ 辞書の SHA-256 を照合していない」と警告を出す。
 * ⚠️ 不一致なら漢字経路だけ無効にして**かな専用で続く**（起動は止まらない）。 */
#ifdef SANOTTS_DICT_SHA256
#define SAAN_DICT_SHA256 SANOTTS_DICT_SHA256
#endif

/* --- 7. arena をどこから取るか ---------------------------------------------
 *
 * 既定 0 … `.bss` に静的確保（136 KB）。**malloc しない**（断片化させない・失敗しない）。
 *      1 … 起動時にヒープから取る（PSRAM 優先 → 内部 DRAM）。
 *
 * ⚠️ **ESP32（S3 でない）は `dram0_0_seg` が小さく、静的 136 KB が入らない**
 *    ことがある（M5Stack Core2 で 208 KB 版が 65,368 B 溢れた）。その板では 1 にする。
 * ⚠️ **PSRAM の arena は遅い**（未測定）。速度を測る構成では使わないこと。 */
#ifndef SANOTTS_ARENA_HEAP
#define SANOTTS_ARENA_HEAP 0
#endif

/* --- 8. 受け付ける入力の最大バイト数 ---------------------------------------
 *
 * ids バッファの寸法を決める（`saan_g2p_capacity()` と同じ式 `2 * バイト数 + 3`）。
 * ⚠️ **実際に喋れる上限は `SanoTTS::kMaxIds` = 350 ids** で、こちらの方が先に効く。
 *    350 は arena の限界ではなく**学習分布の上限**（D-017 の max_spec_length=700 相当）。 */
#ifndef SANOTTS_MAX_INPUT_BYTES
#define SANOTTS_MAX_INPUT_BYTES 512
#endif

#endif /* SANOTTS_CONFIG_H */
