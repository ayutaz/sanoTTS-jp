# ⚠️ **REQUIRES に存在しないコンポーネント名を書くと IDF はエラーで止まる。**
#    「どちらでも通るように両方書く」は効かない。
#
#    `driver` は v5.x を通してある傘コンポーネントで、v5.3 以降では
#    分割後の `esp_driver_i2s` などに依存する形で残っている（はず）。
#    したがって **`driver` 1 本にしてある**のが保守的。
#    ⚠️ この環境に IDF が無いので**確認していない**。v5.3 以降で
#    `esp_driver_i2s` を直接書きたくなったらそのとき差し替えること。
idf_component_register(
    SRCS "main.c" "saan_model.c" "saan_i2s.c"
    INCLUDE_DIRS "."
    REQUIRES saanotts_core driver esp_partition esp_timer
)
