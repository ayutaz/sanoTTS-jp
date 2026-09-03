# sanoTTS-jp を M5Stack（M5Unified が動く板）で鳴らすプロジェクト。
#
#   cd esp32/boards/m5unified
#   idf.py -B build_cores3 -DSDKCONFIG=build_cores3/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" -DSAAN_ENABLE_PIE=1 build
#   idf.py -B build_core2  -DSDKCONFIG=build_core2/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.core2" build
#
# 漢字 + 辞書 + W8A8/PIE を 1 本にした CoreS3 向け（K-A / T9 + T10）:
#
#   uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin   # 13.7 MB
#   idf.py -B build_ka -DSDKCONFIG=build_ka/sdkconfig \
#          -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
#          -DSAAN_ENABLE_PIE=1 -DSAAN_KANJI=1 \
#          -DSAAN_DICT_BLOB=<絶対パス>/k1_dict.bin build
#
# ⚠️ **辞書は 16 MB flash の 0x2D0000 に焼く**（partitions.csv の dict 行。
#    DevKit の partitions_16mb.csv と同じ offset）。`idf.py flash` が
#    app と一緒に焼く（esptool_py_flash_to_partition）。
# ⚠️ **この板は esp_partition_mmap では辞書を貼れない**（sdkconfig.defaults の
#    CONFIG_SPI_FLASH_ROM_IMPL=y → ROM 実装の 128 ページ = 8 MB 上限）。
#    saan_dict.c が自動で esp_mmu_map に切り替える。詳細はそのファイルの冒頭。
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

# --- K-7: 辞書 blob（漢字対応ビルドだけ）------------------------------------
#
#   idf.py -DSAAN_KANJI=1 -DSAAN_DICT_BLOB=<絶対パス>/k1_dict.bin ... build
#
# ⚠️ **13 MB 以上ある。** リポジトリには入れない（生成物）。作り方:
#     uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin
# ⚠️ **存在検査は project() より前。** 実際に焼く指示
#    （esptool_py_flash_to_partition）は project() の後でないと関数が無い。
if(SAAN_KANJI)
    if(NOT DEFINED SAAN_DICT_BLOB)
        set(SAAN_DICT_BLOB "${SAAN_ESP32}/../csrc/k1_dict.bin")
    endif()
    if(NOT EXISTS "${SAAN_DICT_BLOB}")
        message(FATAL_ERROR
            "辞書 blob が無い: ${SAAN_DICT_BLOB}\n"
            "  uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin\n"
            "で作るか、-DSAAN_DICT_BLOB=<絶対パス> を渡すこと。")
    endif()
    get_filename_component(SAAN_DICT_BLOB "${SAAN_DICT_BLOB}" ABSOLUTE)
    set(SAAN_DICT_BLOB "${SAAN_DICT_BLOB}" CACHE INTERNAL "辞書 blob の絶対パス")
    file(SIZE "${SAAN_DICT_BLOB}" SAAN_DICT_BLOB_SIZE)
    message(STATUS "sanoTTS: dict blob ${SAAN_DICT_BLOB} (${SAAN_DICT_BLOB_SIZE} B) → dict パーティション")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(saanotts_m5)

# 辞書は `dict` パーティションへ（offset は partitions.csv の行から自動解決される）。
# ⚠️ **partitions.csv から dict 行を消すと、ここでビルドが止まる**
#    （"Partition dict not found"）。これが「表と焼くものがずれない」ゲート。
if(SAAN_KANJI)
    esptool_py_flash_to_partition(flash "dict" "${SAAN_DICT_BLOB}")
endif()
