#!/usr/bin/env bash
# G-AR2 / G-AR6 — Arduino ライブラリが **3 構成でビルドでき、PIE が実際に効いているか**。
#
# ⚠️ **「ビルドが通った」は「同じ音が出る」の証拠にならない。** それを見るのは
#    G-AR4（`scripts/check_arduino_qemu.sh`。QEMU で PCM の checksum を突き合わせる）。
#    ここが見ているのは (a) 3 構成でリンクまで通るか (b) **PIE 命令が出ているか**だけ。
#
# ## なぜ G-AR6 が要るのか — **実際に踏んだ**
#
# `sanotts_config.h` が `sdkconfig.h` を読んでいなかったので `CONFIG_IDF_TARGET_ESP32S3`
# が見えず、W8A8 + PIE が黙って無効になっていた（2026-09-13）。**ビルドは成功し、
# 音も出て、2 倍以上遅いだけ**なので誰も気づけない。さらに Arduino の既定は `-Os` で、
# ESP-IDF 版の `-O2` と違うため PIE 命令が 74 → 67 に減る。
# → **命令数そのものを見る**。陰性対照は非 S3 ビルドで 0 件。
#
#   bash scripts/ci_arduino_build.sh
#
# 要るもの: PlatformIO（`uv sync --extra arduino`）とネットワーク（初回は toolchain を落とす）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIO="${PIO:-$ROOT/.venv/bin/pio}"
WORK="${WORK:-${TMPDIR:-/tmp}/sanotts-arduino-ci}"

# ⚠️ **公式の espressif32 では動かない。** arduino-esp32 2.0.17（ESP-IDF 4.4）で止まっており、
#    ESP_PARTITION_MMAP_DATA も driver/i2s_std.h も無い。3.x が要る。
PLATFORM="${SANOTTS_PIO_PLATFORM:-https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip}"

# ESP-IDF 版 `-O2` での実測値（`xtensa-esp32s3-elf-gcc -mlongcalls -O2 -std=c99
# -DSAAN_INT8_ACT=1 -DSAAN_PIE=1 -c csrc/saanotts_int8.c` の `ee.` の数。GCC 8.4 と 14.2 で同じ）。
# ⚠️ **S5b / T1〜T5 でカーネルを触ったらこの数は変わる。** 変えるときは
#    ESP-IDF 側の数え直しと一緒に直すこと（CLAUDE.md の「PIE カーネル」節）。
EXPECT_PIE=74

fail=0
ng() { echo "  NG  $*"; fail=1; }
ok() { echo "  OK  $*"; }

pie_count() {   # $1 = ビルドディレクトリ
    local obj od
    obj="$(find "$1/.pio" -name 'saanotts_int8*.o' | head -1)"
    [ -n "$obj" ] || { echo "-1"; return; }
    od="$(find "$HOME/.platformio/packages" -name 'xtensa-esp32s3-elf-objdump' | head -1)"
    [ -n "$od" ] || { echo "-2"; return; }
    "$od" -d "$obj" | grep -c 'ee\.' || true
}

# ⚠️ **標準出力でディレクトリを返さないこと。** ビルドの進捗も同じ経路に出るので、
#    `$(build …)` で受けると「最後の行」がログ行になって黙って壊れる（実際に踏んだ）。
build() {       # $1 = 名前  $2 = board  $3 = 追加 build_flags  $4 = 追加 lib_deps
    local name="$1" board="$2" flags="$3" deps="$4"
    local d="$WORK/$name"
    rm -rf "$d"; mkdir -p "$d/src"
    cp "$ROOT/arduino/examples/PcmCallback/PcmCallback.ino" "$d/src/main.cpp"
    {
        echo "[env:t]"
        echo "platform = $PLATFORM"
        echo "board = $board"
        echo "framework = arduino"
        echo "board_build.partitions = huge_app.csv"
        [ -n "$flags" ] && echo "build_flags = $flags"
        echo "lib_deps ="
        echo "    symlink://$ROOT/arduino"
        [ -n "$deps" ] && echo "    $deps"
    } > "$d/platformio.ini"
    echo "--- $name (board=$board flags='${flags:-なし}') ---"
    if "$PIO" run -d "$d" >"$d/build.log" 2>&1; then
        grep -E '^(RAM|Flash):' "$d/build.log" | sed 's/^/      /'
        ok "$name はビルドできた"
    else
        tail -25 "$d/build.log" | sed 's/^/      /'
        ng "$name のビルドが失敗した（$d/build.log）"
    fi
}

echo "=== G-AR1 生成物が csrc の逐語か ==="
uv run --no-project --python 3.12 python "$ROOT/scripts/build_arduino_lib.py" --self-test
uv run --no-project --python 3.12 python "$ROOT/scripts/build_arduino_lib.py" >/dev/null
uv run --no-project --python 3.12 python "$ROOT/scripts/build_arduino_lib.py" --check

echo
echo "=== G-AR2 3 構成でビルドできるか ==="

# (1) かな専用（辞書も Open JTalk も入らない最小構成）
build kana esp32-s3-devkitc-1 '-DSANOTTS_ENABLE_KANJI=0' ''
# (2) 漢字 + M5Unified（出荷に一番近い形）
build kanji-m5 esp32-s3-devkitc-1 '' 'm5stack/M5Unified@^0.2.7'
# (3) 非 S3（⚠️ **PIE が無い板。実時間には間に合わない**が、リンクは通るべき）
build non-s3 esp32dev '-DSANOTTS_ARENA_HEAP=1' ''

echo
echo "=== G-AR6 PIE が実際に効いているか（命令数）==="
for d in "$WORK/kana" "$WORK/kanji-m5"; do
    [ -d "$d/.pio" ] || { ng "$(basename "$d") のビルド成果物が無い"; continue; }
    n="$(pie_count "$d")"
    if [ "$n" = "$EXPECT_PIE" ]; then
        ok "$(basename "$d"): saanotts_int8.c.o の PIE 命令 ${n}（ESP-IDF -O2 と同じ）"
    else
        ng "$(basename "$d"): PIE 命令が ${n}（期待 $EXPECT_PIE）。"\
"sdkconfig.h を読めていないか、-O2 の pragma が消えている（どちらもビルドは通るので気づけない）"
    fi
done

# 陰性対照。⚠️ **これが 0 でないなら、上の「74」は板に依らない別の理由で出ている。**
if [ -d "$WORK/non-s3/.pio" ]; then
    n="$(pie_count "$WORK/non-s3")"
    if [ "$n" = "0" ]; then
        ok "non-s3（陰性対照）: PIE 命令 0（自動的に W8A32 に落ちている）"
    else
        ng "non-s3（陰性対照）: PIE 命令が ${n} 出ている。ee.* は ESP32-S3 専用のはず"
    fi
fi

echo
if [ "$fail" = 0 ]; then echo "OK  G-AR1 / G-AR2 / G-AR6 すべて通った"; else echo "NG! 上の NG を見ること"; fi
exit "$fail"
