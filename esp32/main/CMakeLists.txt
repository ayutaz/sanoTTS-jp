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

idf_component_register(
    SRCS "main.c" "saan_model.c" "saan_i2s.c" "saan_console.c" ${SAAN_MAIN_K}
    INCLUDE_DIRS "."
    REQUIRES saanotts_core driver esp_partition esp_timer
)

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
# `saan_i2s_pcm_checksum()` をホスト（esp32/host_stub）と突き合わせられる。
# ⚠️ **コア component と同じ条件で main 側にも渡す。** main.c の
#    「fp32 blob なのに W8A8 有効」ガードがこれを見る。片方だけ定義すると
#    ガードが黙って消えて、**効いていないのに気づけない**構成に戻る。
if(SAAN_ENABLE_PIE)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_INT8_ACT=1)
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
