# 重み blob → `const uint8_t[]` ヘッダ（.rodata 埋め込み）の生成。
#
# esp32/main/CMakeLists.txt と esp32/boards/*/main/CMakeLists.txt が共有する。
# **2 か所に同じ add_custom_command を書かない**ため（片方だけ直すとヘッダの形がずれる）。
#
#   saan_model_rodata_header(<component lib> <blob の絶対パス> <blob_to_header.py の絶対パス>)
#
# ⚠️ **生成物はビルドディレクトリに置く。** 4 MB のテキストをソースに置かない。
# ⚠️ **blob を差し替えたら自動で作り直る**（DEPENDS に blob とスクリプト）。手作業に
#    すると、古いヘッダのまま焼いて「差し替えたのに変わらない」という気づけない事故になる。
# ⚠️ fp32 blob はスクリプトが dtype を見て**ビルド時に拒否する**（W8A8/PIE が効かないため）。
function(saan_model_rodata_header TARGET BLOB SCRIPT)
    if(NOT EXISTS "${BLOB}")
        message(FATAL_ERROR "saan_model_rodata_header: blob が無い: '${BLOB}'"
                            "（SAAN_MODEL_BLOB を project() より前に CACHE で決めること）")
    endif()
    set(_hdr "${CMAKE_CURRENT_BINARY_DIR}/saan_model_blob.h")
    if(NOT DEFINED SAAN_PYTHON)
        set(SAAN_PYTHON uv run --no-project python)   # stdlib だけなので --no-project
    endif()
    add_custom_command(
        OUTPUT "${_hdr}"
        COMMAND ${SAAN_PYTHON} "${SCRIPT}" --blob "${BLOB}" --out "${_hdr}"
        DEPENDS "${BLOB}" "${SCRIPT}"
        COMMENT "sanoTTS: ${BLOB} → saan_model_blob.h (.rodata)"
        VERBATIM)
    add_custom_target(saan_blob_header DEPENDS "${_hdr}")
    add_dependencies(${TARGET} saan_blob_header)
    target_include_directories(${TARGET} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_compile_definitions(${TARGET} PRIVATE SAAN_MODEL_RODATA=1)
    message(STATUS "sanoTTS: 重みは **.rodata 埋め込み**（${BLOB}）。model パーティションは焼かない")
endfunction()
