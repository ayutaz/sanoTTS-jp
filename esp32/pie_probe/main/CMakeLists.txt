idf_component_register(SRCS "probe.c"
                       INCLUDE_DIRS "." "../../../csrc")
# PIE カーネルを有効にする。⚠️ これを外すと probe は「PIE 未使用」と表示して
# **スカラ経路を測ってしまう**ので、表示で必ず確認すること
target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PIE=1 SAAN_INT8_ACT=1)
target_sources(${COMPONENT_LIB} PRIVATE
    "../../../csrc/saanotts_int8.c"
    "../../../csrc/saanotts.c"
    "../../../csrc/saanotts_stream.c"
    "../../../csrc/fft.c")
