#!/usr/bin/env bash
# リリース用の firmware 10 本を作り、**中身を抜いて照合する**。
#
#   . ~/esp/esp-idf/export.sh
#   bash scripts/build_release_firmware.sh <辞書と重みのディレクトリ> <出力ディレクトリ>
#
# **なぜ要るか。** v1.0.0 / v1.1.0 は手打ちで作られ、手順は測定記録に散っていた
# （M-124 に 3 本ぶんだけ）。次に焼き直す人は**残り 7 本を推測する**ことになる。
# 推測でビルドすると「通るが中身が違う」イメージを配る（M-124 §1 が実際に踏んだ:
# QEMU 用のビルドは DIO / 起動時発話あり / UART0 の 3 点で出荷構成と違っていた）。
#
# ⚠️ **構成はイメージから実測して決めた**（[M-149](../docs/measurements.md#m-149)）:
#    公開済み 10 本の 0x8000 からパーティション表を抜き、リポジトリの CSV と突き合わせた。
#
# ⚠️ **「ビルドが通った」を中身の証拠にしない**（M-124 §3）。各イメージについて
#    **model パーティション（在れば）と dict パーティションを dd で抜いて SHA-256 を照合**する。
#
# 入力ディレクトリに要るもの（前のリリースから `gh release download` すれば揃う）:
#   saanotts-jp-v4-int8.bin / k1-dict-438750.bin / k1-dict-228000-8mb.bin
#   k1-dict-213000-8mb-m5.bin / k1-dict-135000-4mb.bin / k1-dict-44000-2mb.bin
set -u
IN="${1:?入力ディレクトリ}"; OUT="${2:?出力ディレクトリ}"
IN="$(cd "$IN" && pwd)"; mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
B="${SAAN_BUILD_ROOT:-$(mktemp -d)}"; mkdir -p "$B"
ng=0; ok=0
say(){ printf '%s\n' "$*"; }
fail(){ printf '  \033[1mNG!\033[0m %s\n' "$*"; ng=$((ng+1)); }
pass(){ printf '  OK  %s\n' "$*"; ok=$((ok+1)); }

sha(){ shasum -a 256 "$1" | cut -d' ' -f1; }
# 位置とサイズを指定してイメージから抜き、期待ファイルと SHA-256 を比べる
region(){ # <image> <off> <len> <want-file> <label>
    local img=$1 off=$2 len=$3 want=$4 label=$5
    dd if="$img" bs=1 skip="$off" count="$len" of="$B/region.bin" 2>/dev/null
    if [ "$(sha "$B/region.bin")" = "$(sha "$want")" ]; then
        pass "$label が $(basename "$want") と一致（0x$(printf %x "$off") / $len B）"
    else
        fail "$label が $(basename "$want") と違う（0x$(printf %x "$off") / $len B）"
    fi
}

# name | dir | SDKCONFIG_DEFAULTS | extra cmake | dict file | flash | model-off:len
SPECS=(
"esp32s3-firmware-kanji-16mb.bin|.|sdkconfig.defaults;sdkconfig.kanji|-DSAAN_KANJI=1|k1-dict-438750.bin|16MB|0x210000:2d0000"
"esp32s3-firmware-kanji-16mb-usbjtag.bin|.|sdkconfig.defaults;sdkconfig.kanji;sdkconfig.usb_serial_jtag|-DSAAN_KANJI=1|k1-dict-438750.bin|16MB|0x210000:2d0000"
"esp32s3-firmware-kanji-8mb.bin|.|sdkconfig.defaults;sdkconfig.kanji8mb|-DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1|k1-dict-228000-8mb.bin|8MB|:130000"
"esp32s3-firmware-kanji-4mb.bin|.|sdkconfig.defaults;sdkconfig.kanji4mb|-DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1|k1-dict-135000-4mb.bin|4MB|:120000"
"esp32s3-firmware-kanji-2mb-budget.bin|.|sdkconfig.defaults;sdkconfig.kanji2mb|-DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1|k1-dict-44000-2mb.bin|4MB|:110000"
"esp32s3-firmware-w8a8-pie.bin|.|sdkconfig.defaults||none|8MB|0x210000:"
"esp32s3-firmware-w8a8-pie-usbjtag.bin|.|sdkconfig.defaults;sdkconfig.usb_serial_jtag||none|8MB|0x210000:"
"esp32s3-firmware-w8a32.bin|.|sdkconfig.defaults|-DSAAN_ENABLE_PIE=0|none|8MB|0x210000:"
"m5-cores3-firmware-kanji-16mb.bin|boards/m5unified|sdkconfig.defaults;sdkconfig.cores3|-DSAAN_KANJI=1|k1-dict-438750.bin|16MB|:2d0000"
"m5-cores3-firmware-kanji-8mb.bin|boards/m5unified|sdkconfig.defaults;sdkconfig.cores3;sdkconfig.8mb|-DSAAN_KANJI=1 -DSAAN_MODEL_RODATA=1|k1-dict-213000-8mb-m5.bin|8MB|:180000"
)

for spec in "${SPECS[@]}"; do
    IFS='|' read -r name dir defaults extra dictf flash regions <<<"$spec"
    moff="${regions%%:*}"; doff="${regions##*:}"
    say ""; say "=== $name ==="
    bd="$B/$(echo "$name" | tr -d '.')"
    args=(-B "$bd" -DSDKCONFIG="$bd/sdkconfig" -DSDKCONFIG_DEFAULTS="$defaults")
    [ "$dictf" != none ] && args+=(-DSAAN_DICT_BLOB="$IN/$dictf")
    # shellcheck disable=SC2086
    ( cd "$ROOT/esp32/$dir" && idf.py "${args[@]}" $extra build ) >"$bd.log" 2>&1 \
        || { fail "ビルドが落ちた（$bd.log）"; grep -m3 -iE "error" "$bd.log" | sed 's/^/      /'; continue; }
    elf=$(ls "$bd"/*.elf 2>/dev/null | head -1)
    ( cd "$bd" && esptool.py --chip esp32s3 merge_bin --fill-flash-size "$flash" \
        -o "$OUT/$name" @flash_args ) >>"$bd.log" 2>&1 \
        || { fail "merge_bin が落ちた（$bd.log）"; continue; }
    pass "ビルドと結合（app $(stat -f%z "${elf%.elf}.bin") B / イメージ $(stat -f%z "$OUT/$name") B）"
    # --- 中身の照合 ---
    [ -n "$moff" ] && region "$OUT/$name" "$((moff))" 654032 "$IN/saanotts-jp-v4-int8.bin" "model パーティション"
    if [ "$dictf" != none ]; then
        region "$OUT/$name" "$((0x$doff))" "$(stat -f%z "$IN/$dictf")" "$IN/$dictf" "dict パーティション"
    fi
done

say ""
if [ "$ng" -eq 0 ]; then say "firmware 10 本: OK $ok 件 / NG 0 件"; else say "NG $ng 件（OK $ok）"; fi
exit $([ "$ng" -eq 0 ] && echo 0 || echo 1)
