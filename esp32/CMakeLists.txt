# sanoTTS-jp — ESP32-S3 の雛形（c'-4）
#
# ⚠️ **実機では一度も動かしていない。** ビルドは通る（2026-08-28 / ESP-IDF v5.5 / M-54）。
# 速度は QEMU では測れないので、実機（ESP32-S3 ボード）が来るまで未検証。
#    手元で確認したのは CMake の構文とホスト stub ビルドだけ。詳細は README.md。
#
#   idf.py set-target esp32s3
#   idf.py build
#   idf.py -p /dev/tty.usbmodemXXXX flash monitor

cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(saanotts_jp)

# --- 重み blob を flash の `model` パーティションに焼く ---------------------
#
# ⚠️ **app に EMBED_FILES で埋めない。** idf_component_register(EMBED_FILES ...) は
#    target_add_binary_data() を **ALIGN 引数なしで**呼び、
#    tools/cmake/scripts/data_file_embed_asm.cmake は DATA_ALIGNMENT が未定義なら
#    .balign を一切出さない = **blob 先頭のアライメントが無保証**。
#    本コアは payload を const float* に直接キャストする（テンソル offset は
#    全部 16 の倍数なので、ずれるとしたら base だけ）。Xtensa の非アライン
#    4 バイトロードは LoadStoreAlignmentCause で落ちる。しかも**リンク結果次第で
#    たまたま揃うことがあり、無関係な変更で突然落ちる**種類の事故になる。
#
#    パーティションなら esp_partition_mmap() が 64 KB 境界に丸めてマップするので、
#    partitions.csv で offset を 64 KB 境界に置けば 16 バイト境界も自動的に満たす
#    （将来 PIE を使うときの SOC_SIMD_PREFERRED_DATA_ALIGNMENT = 16 もこれで足りる）。
#
# **既定は int8 の student_i8.bin (643,936 B)。**
#    ⚠️ かつて fp32 の student.bin (2,249,792 B) を既定にしていたが、これは
#    「int8 経路がまだ確定していない」時期の保守的な選択だった。int8 は
#    **flash と D-cache で 3.5 倍有利**で、リリースで配布しているのもこちら。
#    ⚠️ **W8A8 + PIE は fp32 blob では 1 命令も効かない**（`saan_conv1d_w` が
#    `W.f32` で早期 return する）。main.c が起動時にこれを検査して止める。
#    fp32 に戻すには -DSAAN_MODEL_BLOB=<...>/student.bin を渡す。
if(NOT DEFINED SAAN_MODEL_BLOB)
    set(SAAN_MODEL_BLOB "${CMAKE_CURRENT_LIST_DIR}/../csrc/student_i8.bin")
endif()

if(NOT EXISTS "${SAAN_MODEL_BLOB}")
    message(FATAL_ERROR
        "重み blob が無い: ${SAAN_MODEL_BLOB}\n"
        "  ⚠️ blob は git 管理外なので、クローンしただけでは存在しない。\n"
        "  ⚠️ `idf.py set-target esp32s3` でこれが出たなら、**set-target は不要**。\n"
        "     sdkconfig.defaults が CONFIG_IDF_TARGET=\"esp32s3\" を持っているので、\n"
        "     そのまま build に -DSAAN_MODEL_BLOB=<絶対パス> を付けて打てばよい。\n"
        "  (a) リリースから取る（推奨）:\n"
        "      https://github.com/ayutaz/sanoTTS-jp/releases/latest\n"
        "      saanotts-jp-v3-int8.bin を落として -DSAAN_MODEL_BLOB=<その絶対パス>\n"
        "  (b) 自分で書き出す（教師 ckpt と runs/v3/stage4.pt が要る）:\n"
        "      uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt \\\n"
        "          --out csrc/student_i8.bin --golden csrc/golden_i8.bin \\\n"
        "          --int8 --golden-from-quantized --report csrc/export_i8.json")
endif()

file(SIZE "${SAAN_MODEL_BLOB}" SAAN_MODEL_BLOB_SIZE)
message(STATUS "sanoTTS: model blob ${SAAN_MODEL_BLOB} (${SAAN_MODEL_BLOB_SIZE} B)")

# partitions.csv の `model` パーティションに焼く。offset はパーティション名から
# 自動解決される（components/esptool_py/project_include.cmake の定義）。
esptool_py_flash_to_partition(flash "model" "${SAAN_MODEL_BLOB}")
