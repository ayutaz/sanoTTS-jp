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

# ⚠️ **コア 4 ファイルすべてが要る。** saanotts_int8.c には saan_w / saan_conv1d_w /
#    saan_dwconv1d_w / saan_act_scratch_needed があり、saanotts.c と
#    saanotts_stream.c の両方がこれを参照する。3 ファイルではリンクできない
#    （csrc/Makefile の CORE と同じ並び。片方だけ増やさないこと）。
#
# ⚠️ **g2p.c はコアではなく独立した翻訳単位**（推論コアを 1 行も参照しない）が、
#    esp32/main/main.c が saan_g2p() を呼ぶのでここに並べる。csrc/Makefile の
#    CORE には入れない（golden_test 等はリンクしないため）。**非対称なのは意図的。**
idf_component_register(
    SRCS "${SAAN_CSRC}/saanotts.c"
         "${SAAN_CSRC}/saanotts_stream.c"
         "${SAAN_CSRC}/fft.c"
         "${SAAN_CSRC}/saanotts_int8.c"
         "${SAAN_CSRC}/g2p.c"
    INCLUDE_DIRS "${SAAN_CSRC}"
    REQUIRES ""
)

# ℹ️ **かつて「-std=c99 を足すな」と書いていたが、もう地雷ではない。**
#    newlib の M_PI が __STRICT_ANSI__ の下で隠れる問題は実在し、実際に
#    `xtensa-esp32s3-elf-gcc -std=c99` で saanotts.c:395 が落ちた（M-54）。
#    **csrc/saanotts_internal.h で M_PI を定義して解決済み**なので、
#    厳密 -std=c99 でも 5 ファイルすべてコンパイルが通る（実測）。
#    IDF 既定の gnu17 のままにしておくが、足しても壊れない。
#
# ⚠️ **SAAN_FFT_DOUBLE と SAAN_USE_NAIVE_DFT は定義しない。**
#    ESP32-S3 の FPU は単精度のみ。naive DFT は double の cos/sin を使うが、
#    -O2 では未定義のままデッドコード除去される（`nm -u` で _cos/_sin が
#    出ないことを手元で確認済み）。定義するとソフト浮動小数に落ちる。
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra)

# --- PIE（ESP32-S3 の整数 SIMD）-------------------------------------------
#
# ⚠️ **現在このファームは PIE を使っていない。** 有効にするには次の 2 つを
#    **両方**定義する（片方だけでは効かない）:
#
#      SAAN_INT8_ACT=1  … activation も int8 にする（W8A8）。PIE の前提。
#                         W8A32（既定）は積和が fp32 なので整数 SIMD が効かない
#      SAAN_PIE=1       … saan_conv1d_i8a の内積を ee.vmulas.s8.accx にする
#
#    正しさは QEMU で検証済み（MAC の 99.40% を PIE 化、bit 完全一致。M-57 / M-58）。
#
# ⚠️ **有効化には判断が要る**（未決）:
#      - SNR が fp32 比 25.88 → 23.24 dB に落ちる
#        （⚠️ ただし **SCOREQ では差が無い**。陽性対照つきで確認済み = M-55）
#      - arena ピークが 196.9 → 201.1 KB になり、**自主基準 200 KB を 1.1 KB 超える**
#        （SRAM 512 KB の 39%。下の g_arena 208 KB には収まる）
#      - ⚠️ **速度は一度も測っていない。** QEMU はサイクル精度ではない。実機が要る
#
# 有効にするときはこの 2 行のコメントを外す:
# target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_INT8_ACT=1)
# target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PIE=1)
