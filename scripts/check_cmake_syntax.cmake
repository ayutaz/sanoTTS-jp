# esp32/ の CMakeLists.txt を **ESP-IDF なしで**構文検査する（c'-4）。
#
# ⚠️ **これは構文検査であって configure ではない。** 通っても
#    「idf.py build が通る」とは言えない。ESP-IDF も xtensa toolchain も
#    この環境に無いので、そこは検証していない。
#
#   cmake -P scripts/check_cmake_syntax.cmake
#
# 検出できるもの: 括弧の不整合、未閉のクォート、引数を取り違えたコマンド、
#                 存在しないパス（csrc / blob）を指していること。
# 検出できないもの: IDF のコマンド名や引数の綴り、component の依存解決、
#                   Kconfig オプションの実在。

cmake_minimum_required(VERSION 3.16)

get_filename_component(REPO "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(ESP32_DIR "${REPO}/esp32")

# --- IDF の偽物を作る ------------------------------------------------------
set(FAKE_IDF "${CMAKE_CURRENT_LIST_DIR}/../reports/_fake_idf")
file(MAKE_DIRECTORY "${FAKE_IDF}/tools/cmake")
file(WRITE "${FAKE_IDF}/tools/cmake/project.cmake"
     "# ESP-IDF の project.cmake の偽物（構文検査用）\n")
set(ENV{IDF_PATH} "${FAKE_IDF}")

# --- IDF のコマンドをスタブにする -------------------------------------------
# ⚠️ 引数の**綴りは検査していない**。ここで受けているのは「呼べる」ことだけ。
function(idf_component_register)
    message(STATUS "  idf_component_register(${ARGN})")
endfunction()
function(esptool_py_flash_to_partition)
    message(STATUS "  esptool_py_flash_to_partition(${ARGN})")
endfunction()
function(idf_build_get_property)
endfunction()
function(target_compile_options)
endfunction()
function(target_add_binary_data)
endfunction()
# `project()` は script モードで使えないので上書きする
function(project)
    message(STATUS "  project(${ARGN})")
endfunction()

set(FAILED 0)
foreach(F "${ESP32_DIR}/CMakeLists.txt"
          "${ESP32_DIR}/main/CMakeLists.txt"
          "${ESP32_DIR}/components/saanotts_core/CMakeLists.txt")
    if(NOT EXISTS "${F}")
        message(SEND_ERROR "無い: ${F}")
        set(FAILED 1)
        continue()
    endif()
    message(STATUS "include ${F}")
    # CMAKE_CURRENT_LIST_DIR が include 先で正しくなるので相対パスも検査できる
    include("${F}")
endforeach()

if(FAILED)
    message(FATAL_ERROR "SYNTAX NG")
endif()
message(STATUS "SYNTAX OK")
