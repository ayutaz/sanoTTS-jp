# PIE プローブ（A〜C: 正しさ / D〜E: 実機のマイクロベンチ。README.md）
#
#   cd esp32/pie_probe
#   idf.py -B build_probe -DSDKCONFIG=build_probe/sdkconfig -DSAAN_QEMU=1 build     # QEMU 用
#   idf.py -B build_probe_dev -DSDKCONFIG=build_probe_dev/sdkconfig build            # 実機用
#   PSRAM（D4）: -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.psram" を足す
#
# ⚠️ `idf.py set-target` は要らない（sdkconfig.defaults が CONFIG_IDF_TARGET を持つ）。
cmake_minimum_required(VERSION 3.16)

# --- D 節の重み blob（.rodata 埋め込み）。project() より前に決める -----------------
# ⚠️ 理由は esp32/CMakeLists.txt と同じ: IDF は project() の中で main/ を処理するので、
#    後ろに置くと main/CMakeLists.txt から見た変数が空になる。
# ⚠️ **無ければ D 節を skip してビルドは通す**（A〜C / E は blob を要らない）。
#    skip はプローブの出力に「D skip」と出る（黙って PASS にはならない）。
if(NOT DEFINED SAAN_MODEL_BLOB)
    set(SAAN_MODEL_BLOB "${CMAKE_CURRENT_LIST_DIR}/../../csrc/student_i8.bin")
endif()
get_filename_component(SAAN_MODEL_BLOB "${SAAN_MODEL_BLOB}" ABSOLUTE)
if(EXISTS "${SAAN_MODEL_BLOB}")
    set(SAAN_PROBE_HAVE_BLOB 1 CACHE INTERNAL "D 節の blob が在るか")
    set(SAAN_MODEL_BLOB "${SAAN_MODEL_BLOB}" CACHE INTERNAL "重み blob の絶対パス（main/ が参照する）")
    file(SIZE "${SAAN_MODEL_BLOB}" _saan_blob_size)
    message(STATUS "pie_probe: D 節の重み = ${SAAN_MODEL_BLOB} (${_saan_blob_size} B) → .rodata")
else()
    set(SAAN_PROBE_HAVE_BLOB 0 CACHE INTERNAL "D 節の blob が在るか")
    message(WARNING "pie_probe: 重み blob が無い (${SAAN_MODEL_BLOB})。**D 節は skip** になる。\n"
                    "  csrc/student_i8.bin を置くか -DSAAN_MODEL_BLOB=<絶対パス> を渡すこと"
                    "（入手は https://github.com/ayutaz/sanoTTS-jp/releases/latest）")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(pie_probe)
