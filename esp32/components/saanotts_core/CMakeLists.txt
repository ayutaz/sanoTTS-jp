# sanoTTS-jp の C99 推論コアを ESP-IDF の component として登録する。
#
# ⚠️ **csrc/ をコピーもシンボリックリンクもしない。** 相対パスで直接参照する。
#    同じものを 2 か所に置くと、片方だけ直したときに golden test（bit 一致）が
#    どちらを見ているのか分からなくなる。csrc/saanotts_internal.h が
#    「同じカーネルを 2 回書かない」と書いているのと同じ理由。

get_filename_component(SAAN_CSRC "${CMAKE_CURRENT_LIST_DIR}/../../../csrc" ABSOLUTE)

if(NOT EXISTS "${SAAN_CSRC}/saanotts.c")
    message(FATAL_ERROR
        "csrc が見つからない: ${SAAN_CSRC}\n"
        "esp32/ はリポジトリ直下に置くこと（esp32/../csrc が推論コア）")
endif()

# ⚠️ **4 ファイルすべてが要る。** saanotts_int8.c には saan_w / saan_conv1d_w /
#    saan_dwconv1d_w / saan_act_scratch_needed があり、saanotts.c と
#    saanotts_stream.c の両方がこれを参照する。3 ファイルではリンクできない
#    （csrc/Makefile の CORE と同じ並び。片方だけ増やさないこと）。
idf_component_register(
    SRCS "${SAAN_CSRC}/saanotts.c"
         "${SAAN_CSRC}/saanotts_stream.c"
         "${SAAN_CSRC}/fft.c"
         "${SAAN_CSRC}/saanotts_int8.c"
    INCLUDE_DIRS "${SAAN_CSRC}"
    REQUIRES ""
)

# ⚠️ **`-std=c99` を足さないこと。** newlib の M_PI は __STRICT_ANSI__ の下で
#    隠れることがあり、csrc/saanotts.c と csrc/saanotts_stream.c は M_PI を
#    無条件に使う（Hann 窓の生成）。IDF 既定の gnu17 のままにする。
#    csrc/Makefile が -std=c99 なのを見てここにも写す、が地雷。
#    macOS の libc では -std=c99 でも M_PI が見えるので**手元では再現しない**。
#
# ⚠️ **SAAN_FFT_DOUBLE と SAAN_USE_NAIVE_DFT は定義しない。**
#    ESP32-S3 の FPU は単精度のみ。naive DFT は double の cos/sin を使うが、
#    -O2 では未定義のままデッドコード除去される（`nm -u` で _cos/_sin が
#    出ないことを手元で確認済み）。定義するとソフト浮動小数に落ちる。
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra)
