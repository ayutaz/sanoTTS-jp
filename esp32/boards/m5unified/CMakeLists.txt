# sanoTTS-jp を M5Stack（M5Unified が動く板）で鳴らすプロジェクト。
#
#   cd esp32/boards/m5unified
#   idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" -DSAAN_ENABLE_PIE=1 build
#   idf.py -B build_core2  -DSDKCONFIG=build_core2/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" build
#
# ⚠️ **推論コアも main.c もコピーしない。** ../../components（csrc/ を相対参照）と
#    ../../main の .c をそのまま使い、音声出力（M5.Speaker）と画面（M5GFX）だけ
#    この下の main/ に置く。本家を直せばここにも効く。
#
# 出所: 構成と M5 層は nnn112358/SanoTTS-jp-M5StackCoreS3（MIT）に倣った。
#   踏んだこと（同リポジトリ README「設計メモ」）:
#   - CONFIG_SPIRAM=y だと esp_partition_mmap が ESP_ERR_NO_MEM → 重みは .rodata 埋め込み
#   - .dram0.bss が溢れる → IRAM のコードを flash へ / COMPONENTS を main に絞る
#   - CoreS3 は Quad PSRAM（Octal にするとブートループ）
#   - M5.Speaker.playRaw はコピーしない → バッファ 3 枚回し
cmake_minimum_required(VERSION 3.16)

get_filename_component(SAAN_ESP32 "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(EXTRA_COMPONENT_DIRS "${SAAN_ESP32}/components")

# --- 重み blob（project() より前に決める。理由は esp32/CMakeLists.txt）-------------
if(NOT DEFINED SAAN_MODEL_BLOB)
    set(SAAN_MODEL_BLOB "${SAAN_ESP32}/../csrc/student_i8.bin")
endif()
if(NOT EXISTS "${SAAN_MODEL_BLOB}")
    message(FATAL_ERROR
        "重み blob が無い: ${SAAN_MODEL_BLOB}\n"
        "  https://github.com/ayutaz/sanoTTS-jp/releases/latest の saanotts-jp-v3-int8.bin を\n"
        "  落として -DSAAN_MODEL_BLOB=<その絶対パス> を渡すこと")
endif()
get_filename_component(SAAN_MODEL_BLOB "${SAAN_MODEL_BLOB}" ABSOLUTE)
set(SAAN_MODEL_BLOB "${SAAN_MODEL_BLOB}" CACHE INTERNAL "重み blob の絶対パス")
file(SIZE "${SAAN_MODEL_BLOB}" SAAN_MODEL_BLOB_SIZE)
message(STATUS "sanoTTS: model blob ${SAAN_MODEL_BLOB} (${SAAN_MODEL_BLOB_SIZE} B) → .rodata")

# ⚠️ **M5 は .rodata 埋め込みのみ。** PSRAM 有効だとパーティション mmap が落ちる（報告）。
set(SAAN_MODEL_RODATA 1 CACHE INTERNAL "M5 は .rodata 埋め込み")

# 画面に文を出してタッチで再生する UI なので、**既定で起動時に 1 文喋る**（本家は喋らない）
if(NOT DEFINED SAAN_BOOT_SPEAK)
    set(SAAN_BOOT_SPEAK 1)
endif()
set(SAAN_BOOT_SPEAK "${SAAN_BOOT_SPEAK}" CACHE INTERNAL "")

# --- ビルドするコンポーネントを絞る ---------------------------------------
# ⚠️ これが無いと IDF は全コンポーネントを含める（cmock だけで .bss 32 KB）。DIRAM は
#    341,760 B しか無く arena 208 KB でほぼ埋まる。main とその依存（saanotts_core /
#    M5Unified / M5GFX とそれらの REQUIRES）だけが解決される。
set(COMPONENTS main)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(saanotts_m5)
