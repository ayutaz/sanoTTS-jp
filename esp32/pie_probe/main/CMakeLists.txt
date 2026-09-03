set(PROBE_SRCS "probe.c")
if(SAAN_PROBE_HAVE_BLOB)
    list(APPEND PROBE_SRCS "probe_blob.c")   # 生成ヘッダ（配列の定義）を include する唯一の翻訳単位
endif()

idf_component_register(SRCS ${PROBE_SRCS}
                       INCLUDE_DIRS "." "../../../csrc")
# PIE カーネルを有効にする。⚠️ これを外すと probe は「PIE 未使用」と表示して
# **スカラ経路を測ってしまう**ので、表示で必ず確認すること
target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PIE=1 SAAN_INT8_ACT=1)
target_sources(${COMPONENT_LIB} PRIVATE
    "../../../csrc/saanotts_int8.c"
    "../../../csrc/saanotts.c"
    "../../../csrc/saanotts_stream.c"
    "../../../csrc/fft.c")

# --- D 節: 本物の blob を .rodata に埋める（出荷ファームの M5 構成と同じ仕組み）------------
if(SAAN_PROBE_HAVE_BLOB)
    include("${CMAKE_CURRENT_LIST_DIR}/../../cmake/saan_model_rodata.cmake")
    saan_model_rodata_header(${COMPONENT_LIB} "${SAAN_MODEL_BLOB}"
                             "${CMAKE_CURRENT_LIST_DIR}/../../../scripts/blob_to_header.py")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PROBE_HAVE_BLOB=1)
endif()

# --- QEMU 用: cyc の再現幅（<1%）を合否に入れない -------------------------------------
#
#     idf.py -DSAAN_QEMU=1 build
#
# ⚠️ QEMU の CCOUNT はサイクルではない。D / E の cyc は**実機でだけ**読む。
#    実機向けビルドではこれを付けないこと（付けると幅の NG が消える）。
if(SAAN_QEMU)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_QEMU=1)
    message(STATUS "pie_probe: SAAN_QEMU=1（cyc は意味が無い。bit 一致と陰性対照だけがゲート）")
endif()
