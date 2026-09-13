#!/usr/bin/env bash
# G-AR3 / G-AR7 — **リリース .zip から引いて**ビルドできるか、そして
#                 **重みが本当にリンクされたか**。
#
# ## なぜソースツリーからのビルドでは足りないのか
#
# `ci_arduino_build.sh` は `symlink://` でソースツリーを直接引いている。配る形は
# .zip なので、**中身の取りこぼし・階層の間違い・重みライブラリが見つからない**は
# そちらでは 1 つも出ない。
#
# ## なぜ G-AR7（重みが在るか）が要るのか — **実際に踏んだ**
#
# `SanoTTS.h` が `<saanotts_jp_voice.h>` を `__has_include` で条件つきに include して
# いたとき、**arduino-cli は重みライブラリを最後まで足さなかった**（依存解決が
# 「足りないヘッダのエラー」を起点に動くので、条件つきだとエラーが出ない）。
# 結果、**コンパイルは通り、Flash も 379,721 B で普通に見え、実行時に初めて
# 「重みライブラリを入れること」と言う** — 入れてあるのに。
# → **ELF に `g_saan_model_blob` が居ることを直接見る。**
#
#   bash scripts/ci_arduino_zip.sh
#
# 要るもの: PlatformIO / arduino-cli / int8 の重み blob（/tmp/w に落としてある想定）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIO="${PIO:-$ROOT/.venv/bin/pio}"
WORK="${WORK:-${TMPDIR:-/tmp}/sanotts-arduino-zip}"
BLOB="${BLOB:-/tmp/w/saanotts-jp-v4-int8.bin}"
VER="${VER:-ci}"
PLATFORM="${SANOTTS_PIO_PLATFORM:-https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip}"

# 重み blob のバイト数。⚠️ **ELF の中の配列がこの大きさで在ることまで見る**
# （シンボルが在るだけなら 0 バイトの別物でも通ってしまう）。
EXPECT_BLOB_BYTES=654032

fail=0
ng() { echo "  NG  $*"; fail=1; }
ok() { echo "  OK  $*"; }

[ -f "$BLOB" ] || { echo "NG! 重み blob が無い: $BLOB"; echo "   gh release download v1.0.0 -R ayutaz/sanoTTS-jp -p saanotts-jp-v4-int8.bin -D /tmp/w"; exit 1; }

echo "=== .zip を組む ==="
rm -rf "$WORK"; mkdir -p "$WORK"
uv run --no-project --python 3.12 python "$ROOT/scripts/build_arduino_lib.py" \
    --zip "$WORK/dist" --version "$VER" --blob "$BLOB"
CODE_ZIP="$WORK/dist/sanoTTS-jp-arduino.zip"
VOICE_ZIP="$WORK/dist/sanoTTS-jp-voice-tsukuyomi-v4.zip"

# ⚠️ **中身も見る。** 大きさだけだと「空の src/ を詰めた .zip」でも通る。
for want in "SanoTTS-jp/src/SanoTTS.h" "SanoTTS-jp/src/core/saanotts.c" \
            "SanoTTS-jp/src/core/openjtalk/njd.c" "SanoTTS-jp/NOTICE.txt" \
            "SanoTTS-jp/extras/partitions/sanotts_16mb.csv"; do
    unzip -l "$CODE_ZIP" | grep -q " $want\$" \
        && ok "code.zip に $want が在る" || ng "code.zip に $want が無い"
done
for want in "SanoTTS-jp-voice-tsukuyomi-v4/src/saan_model_blob.h" \
            "SanoTTS-jp-voice-tsukuyomi-v4/src/saanotts_jp_voice.h" \
            "SanoTTS-jp-voice-tsukuyomi-v4/LICENSE-MODEL.md"; do
    unzip -l "$VOICE_ZIP" | grep -q " $want\$" \
        && ok "voice.zip に $want が在る" || ng "voice.zip に $want が無い"
done

blob_in_elf() {   # $1 = ELF
    local od sym
    od="$(find "$HOME/.platformio/packages" "$HOME/.arduino15" "${ARDUINO_DIRECTORIES_DATA:-/nonexistent}" \
          -name 'xtensa-esp32s3-elf-objdump' 2>/dev/null | head -1)"
    [ -n "$od" ] || { echo "no-objdump"; return; }
    sym="$("$od" -t "$1" | grep ' g_saan_model_blob$' || true)"
    [ -n "$sym" ] || { echo "missing"; return; }
    # 例: 3c055840 g     O .flash.rodata\t0009fad0 g_saan_model_blob
    local size sect
    size=$(echo "$sym" | awk '{print $(NF-1)}')
    sect=$(echo "$sym" | awk '{print $(NF-2)}')
    echo "$((16#$size)) $sect"
}

check_blob() {   # $1 = 名前  $2 = ELF
    local r n sect
    r="$(blob_in_elf "$2")"
    case "$r" in
        no-objdump) ng "$1: objdump が見つからず重みを検査できなかった"; return;;
        missing)    ng "$1: **ELF に g_saan_model_blob が無い** — 重みライブラリが"\
"引かれていない（コンパイルは通るが実行時に音が出ない形）"; return;;
    esac
    n="${r% *}"; sect="${r#* }"
    [ "$n" = "$EXPECT_BLOB_BYTES" ] \
        && ok "$1: 重み $n B が ELF に在る" \
        || ng "$1: 重みが ${n} B（期待 $EXPECT_BLOB_BYTES）"
    # ⚠️ **flash に在ることまで見る。** 内部 RAM に置くと 654 KB でリンクが落ちるが、
    #    落ちない置き方をしてしまったときに気づけるようにしておく。
    case "$sect" in
        *rodata*) ok "$1: 重みは ${sect}（flash）に在る";;
        *)        ng "$1: 重みが ${sect} に在る。**.rodata（flash）でないと内部 RAM を食う**";;
    esac
}

echo
echo "=== G-AR7 PlatformIO で .zip から引いてビルドする ==="
D="$WORK/pio"; mkdir -p "$D/src"
cp "$ROOT/arduino/examples/PcmCallback/PcmCallback.ino" "$D/src/main.cpp"
cp "$ROOT/arduino/extras/partitions/sanotts_16mb.csv" "$D/"
cat > "$D/platformio.ini" <<EOF
[env:cores3]
platform = $PLATFORM
board = m5stack-cores3
framework = arduino
board_build.partitions = sanotts_16mb.csv
lib_deps =
    file://$CODE_ZIP
    file://$VOICE_ZIP
EOF
if "$PIO" run -d "$D" >"$D/build.log" 2>&1; then
    grep -E '^(RAM|Flash):' "$D/build.log" | sed 's/^/      /'
    ok "PlatformIO は .zip からビルドできた"
    check_blob "PlatformIO" "$D/.pio/build/cores3/firmware.elf"
else
    tail -25 "$D/build.log" | sed 's/^/      /'
    ng "PlatformIO の .zip ビルドが失敗した（$D/build.log）"
fi

echo
echo "=== G-AR3 arduino-cli で .zip から引いてビルドする（Arduino IDE の経路）==="
if ! command -v arduino-cli >/dev/null 2>&1; then
    echo "  --  arduino-cli が無いので飛ばした（⚠️ **Arduino IDE の経路は未検証**）"
else
    export ARDUINO_DIRECTORIES_DATA="${ARDUINO_DIRECTORIES_DATA:-$WORK/acli/data}"
    export ARDUINO_DIRECTORIES_USER="${ARDUINO_DIRECTORIES_USER:-$WORK/acli/user}"
    CFG="$WORK/acli/arduino-cli.yaml"
    mkdir -p "$WORK/acli"
    arduino-cli config init --dest-file "$CFG" --overwrite >/dev/null
    arduino-cli config set library.enable_unsafe_install true --config-file "$CFG" >/dev/null
    arduino-cli config add board_manager.additional_urls \
        https://espressif.github.io/arduino-esp32/package_esp32_index.json --config-file "$CFG" >/dev/null
    arduino-cli core update-index --config-file "$CFG" >/dev/null 2>&1
    arduino-cli core install esp32:esp32 --config-file "$CFG" >/dev/null 2>&1
    arduino-cli lib install --config-file "$CFG" --zip-path "$CODE_ZIP" "$VOICE_ZIP" >/dev/null 2>&1
    SK="$WORK/acli/sk/PcmCallback"; mkdir -p "$SK"
    cp "$ROOT/arduino/examples/PcmCallback/PcmCallback.ino" "$SK/"
    if arduino-cli compile --config-file "$CFG" \
            -b esp32:esp32:esp32s3:PartitionScheme=huge_app \
            --build-path "$WORK/acli/build" --clean "$SK" >"$WORK/acli/build.log" 2>&1; then
        tail -2 "$WORK/acli/build.log" | sed 's/^/      /'
        ok "arduino-cli は .zip からビルドできた"
        check_blob "arduino-cli" "$WORK/acli/build/PcmCallback.ino.elf"
    else
        tail -25 "$WORK/acli/build.log" | sed 's/^/      /'
        ng "arduino-cli の .zip ビルドが失敗した（$WORK/acli/build.log）"
    fi
fi

echo
if [ "$fail" = 0 ]; then echo "OK  G-AR3 / G-AR7 すべて通った"; else echo "NG! 上の NG を見ること"; fi
exit "$fail"
