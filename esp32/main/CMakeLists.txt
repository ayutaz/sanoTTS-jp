# ⚠️ **REQUIRES に存在しないコンポーネント名を書くと IDF はエラーで止まる。**
#    「どちらでも通るように両方書く」は効かない。
#
#    `driver` は v5.x を通してある傘コンポーネントで、v5.3 以降では
#    分割後の `esp_driver_i2s` などに依存する形で残っている（はず）。
#    したがって **`driver` 1 本にしてある**のが保守的。
#    ✅ **v5.5 でビルドが通ることを実測した**（2026-08-30）。
set(SAAN_MAIN_K "")
if(SAAN_KANJI)
    set(SAAN_MAIN_K "saan_dict.c" "saan_kanji.c")
endif()

# --- 重みの置き場（2 つ。saan_model.h の saan_model_open() の実装を切り替える）-------
#
#     idf.py -DSAAN_MODEL_RODATA=1 build
#
#   既定 0 … saan_model.c        flash の `model` パーティションを esp_partition_mmap
#   1      … saan_model_rodata.c  scripts/blob_to_header.py が ${SAAN_MODEL_BLOB} を
#                                 const uint8_t[]（aligned(16)）のヘッダにして .rodata に埋める
#
# ⚠️ **M5 構成（boards/m5unified）は 1 にしてある。**
#    ~~PSRAM を有効にした板は 1 にする（CONFIG_SPIRAM=y だと esp_partition_mmap が
#    ESP_ERR_NO_MEM で落ちる）~~ → **原因は PSRAM ではなかった**（M-90。esp32/README.md §1 で撤回）。
#    `CONFIG_SPI_FLASH_ROM_IMPL=y` の板では spi_flash_mmap が ROM 実装になり、
#    IDF が渡すプールが 128 ページ = 8 MB しか無いのが理由。重み 654,032 B はこの上限に
#    当たらないが、M5 側はパーティション表に `model` 行を持たないので .rodata に置く。
#    （辞書 13.7 MB = 211 ページの方は esp_mmu_map で貼っている。saan_dict.c）
# ⚠️ 1 のとき app は blob ぶん（643,936 B）大きくなり、モデルだけの差し替えはできない。
if(SAAN_MODEL_RODATA)
    set(SAAN_MODEL_SRC "saan_model_rodata.c")
else()
    set(SAAN_MODEL_SRC "saan_model.c")
endif()

idf_component_register(
    SRCS "main.c" ${SAAN_MODEL_SRC} "saan_i2s.c" "saan_pcm.c" "saan_ui_null.c"
         "saan_console.c" ${SAAN_MAIN_K}
    INCLUDE_DIRS "."
    # ⚠️ `esp_mm` は saan_dict.c の **もう一方の経路**（esp_mmu_map）のため。
    #    DevKit は CONFIG_SPI_FLASH_ROM_IMPL=n なので既定では esp_partition_mmap の
    #    ままだが、`-DSAAN_DICT_MMU=1` で切り替えられるようにここに置いておく
    #    （REQUIRES を書かないと include が見つからず、切り替えた瞬間に落ちる）。
    REQUIRES saanotts_core driver esp_partition esp_mm esp_timer
)

if(SAAN_MODEL_RODATA)
    # blob → ヘッダの生成は esp32/cmake/saan_model_rodata.cmake（boards/ の main と共有）
    include("${CMAKE_CURRENT_LIST_DIR}/../cmake/saan_model_rodata.cmake")
    saan_model_rodata_header(${COMPONENT_LIB} "${SAAN_MODEL_BLOB}"
                             "${CMAKE_CURRENT_LIST_DIR}/../../scripts/blob_to_header.py")
endif()

# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の
#    「漢字モードのプロンプト」がこれを見る。片方だけだとプロンプトが出ない。
if(SAAN_KANJI)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_KANJI=1 CHARSET_UTF_8)
endif()

# --- QEMU 用: I2S ペリフェラルへの書き込みだけを外す -------------------------
#
#     idf.py -DSAAN_QEMU=1 build
#
# ⚠️ **音は一切出ない。実機ではこれを付けないこと。**
# QEMU の esp32s3 マシンは I2S DMA を捌かないので `i2s_channel_write` が返らず、
# **プリロール完了後に停止する**（実測）。float→int16 変換は通したままなので、
# `saan_pcm_checksum()` をホスト（esp32/host_stub）と突き合わせられる。
# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の
#    「fp32 blob なのに W8A8 有効」ガードがこれを見る。片方だけ定義すると
#    ガードが黙って消えて、**効いていないのに気づけない**構成に戻る。
# ⚠️ D-048: ESP32-S3 では既定で有効（コア component の CMakeLists.txt が SAAN_ENABLE_PIE を立てる）。
# main 側は arena の見積り（W8A8 の scratch）に使うだけなので、同じ既定をここでも組む。
if(NOT DEFINED SAAN_ENABLE_PIE)
    idf_build_get_property(_saan_main_target IDF_TARGET)
    if(_saan_main_target STREQUAL "esp32s3")
        set(SAAN_ENABLE_PIE 1)
    endif()
endif()
if(SAAN_ENABLE_PIE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_INT8_ACT=1)
endif()

# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の表の出力がこれを見る。
if(SAAN_PROFILE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_PROFILE=1)
endif()

# --- arena をヒープ（PSRAM 優先）から取る ---------------------------------------
#
#     idf.py -DSAAN_ARENA_HEAP=1 build
#
# 既定は .bss に静的確保（208 KB。速度に直結するので内部 DRAM）。**ESP32（S3 でない）は
# dram0_0_seg が小さく静的 208 KB が入らない**（M5Stack Core2 で 65,368 B 溢れた）ので、
# その板では 1 にして PSRAM から取る。⚠️ PSRAM の arena は**遅い**（未測定）。速度の測定には使わない。
if(SAAN_ARENA_HEAP)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_ARENA_HEAP=1)
    message(STATUS "sanoTTS: arena はヒープ（PSRAM 優先）。**速度の測定には使わない**")
endif()

# --- 再生方式（既定はストリーミング = xRT 測定用）------------------------------
#
#     idf.py -DSAAN_BUFFERED=1 build    1 発話ぶんを貯めてから鳴らす（途切れない。待ちは合成時間）
if(SAAN_BUFFERED)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_BUFFERED=1)
    message(STATUS "sanoTTS: 再生 = 貯めてから鳴らす（途切れない。xRT は測れる）")
endif()

if(SAAN_QEMU)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_SKIP_I2S=1)
    message(STATUS "sanoTTS: SAAN_SKIP_I2S=1（QEMU 用。**音は出ない**）")
endif()

# --- 起動時に 1 文喋る（既定は喋らない）-------------------------------------
#
#     idf.py -DSAAN_BOOT_SPEAK=1 build
#
# 対話入力が入ったので、既定では起動時に喋らない（`かな>` に 1 行打てば同じ列になる。
# **bit 単位で同じ**ことは M-63 の T1 で確認済み）。
# ⚠️ **コンソールに触れない環境で測りたいとき**や、`idf.py qemu` をそのまま流して
#    checksum を突き合わせる回帰確認のためにフラグを残してある。
if(SAAN_BOOT_SPEAK)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_BOOT_SPEAK=1)
    message(STATUS "sanoTTS: 起動時に 1 文喋る（突き合わせ用）")
endif()
