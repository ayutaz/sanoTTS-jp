# ファーム本体は ../../../main のもの（コピーしない）。差し替えるのは音声出力と画面だけ。
#   saan_audio_m5.cpp … saan_audio.h の M5.Speaker 実装（saan_i2s.c の代わり）
#   saan_ui_m5.cpp    … saan_ui.h の M5GFX 実装（saan_ui_null.c の代わり）
#   saan_model_rodata.c … 重みは .rodata（saan_model.c の代わり）
#
# ⚠️ `driver`（傘）は要らない。コンソールが使うのは usb_serial_jtag と uart だけ。
#    傘を書くと全 esp_driver_* が IRAM を食う。
get_filename_component(SAAN_MAIN "${CMAKE_CURRENT_LIST_DIR}/../../../main" ABSOLUTE)

# --- K-7（漢字）: 辞書リーダと端末側 G2P の呼び出し口 ------------------------
# ⚠️ **../../../main のものをそのまま使う**（DevKit と同じ .c）。ここでコピーを
#    作ると「どちらが動いているか」が分からなくなる。
# ⚠️ `esp_partition` は saan_dict.c がパーティションを**探す**のに要る。
#    実際の貼り付けは esp_mmu_map（component `esp_mm`）で、こちらは
#    CONFIG_SPI_FLASH_ROM_IMPL=y でも 128 ページ制限に当たらない。
set(SAAN_MAIN_K "")
set(SAAN_REQ_K "")
if(SAAN_KANJI)
    set(SAAN_MAIN_K "${SAAN_MAIN}/saan_dict.c" "${SAAN_MAIN}/saan_kanji.c")
    set(SAAN_REQ_K esp_partition esp_mm)
endif()

idf_component_register(
    SRCS "${SAAN_MAIN}/main.c"
         "${SAAN_MAIN}/saan_console.c"
         "${SAAN_MAIN}/saan_pcm.c"
         "${SAAN_MAIN}/saan_model_rodata.c"
         "saan_audio_m5.cpp"
         "saan_ui_m5.cpp"
         ${SAAN_MAIN_K}
    INCLUDE_DIRS "." "${SAAN_MAIN}"
    REQUIRES saanotts_core esp_timer esp_driver_usb_serial_jtag esp_driver_uart
             ${SAAN_REQ_K}
)

# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の
#    「漢字モードのプロンプト」と saan_kanji.c がこれを見る。片方だけだと
#    プロンプトが出ない / 取り込んだ Open JTalk が `#error CHARSET` で落ちる。
if(SAAN_KANJI)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_KANJI=1 CHARSET_UTF_8)
endif()

include("${CMAKE_CURRENT_LIST_DIR}/../../../cmake/saan_model_rodata.cmake")
saan_model_rodata_header(${COMPONENT_LIB} "${SAAN_MODEL_BLOB}"
                         "${CMAKE_CURRENT_LIST_DIR}/../../../../scripts/blob_to_header.py")

# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の
#    「fp32 blob なのに W8A8 有効」ガードがこれを見る。片方だけ定義すると
#    ガードが黙って消えて、効いていないのに気づけない構成に戻る。
if(SAAN_ENABLE_PIE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_INT8_ACT=1)
endif()
if(SAAN_BOOT_SPEAK)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_BOOT_SPEAK=1)
    message(STATUS "sanoTTS: 起動時に 1 文喋る（画面に出す文と同じ。突き合わせ用）")
endif()
if(SAAN_BUFFERED)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_BUFFERED=1)
    message(STATUS "sanoTTS: 再生 = 貯めてから鳴らす（途切れない。待ちは合成時間）")
endif()
if(SAAN_PROFILE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PROFILE=1)
endif()

# --- ESP32（Core2 / Basic）は arena をヒープ（PSRAM）から取る --------------------
# ⚠️ dram0_0_seg が S3 より小さく、静的 208 KB が **65,368 B 溢れる**（Core2 で実測。
#    M5Unified + M5GFX 込み）。PSRAM の arena は遅い（未測定）。この板は「喋る」確認まで。
idf_build_get_property(_saan_target IDF_TARGET)
if(_saan_target STREQUAL "esp32")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_ARENA_HEAP=1)
    message(STATUS "sanoTTS: IDF_TARGET=esp32 → arena はヒープ（PSRAM 優先）。速度の測定には使わない")
endif()
