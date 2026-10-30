#!/usr/bin/env bash
# G-AR2 / G-AR6 / G-AR9 — Arduino ライブラリが **3 構成でビルドでき、PIE が実際に効いているか**。
# G-AR9 は同じことを **arduino-esp32 2.x（公式 espressif32 6.x / ESP-IDF 4.4）** の 4 構成で見る。
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

# ⚠️ **ここは重みを使わない**（`-DSANOTTS_NO_VOICE_LIB=1`）。重みを本当にリンクできるかは
#    `scripts/ci_arduino_zip.sh` の G-AR7 が .zip から引いて確かめる。
#    この 3 構成はカーネルと経路のコンパイルだけを見ている。
#
# 要るもの: PlatformIO（`uv sync --extra arduino`）とネットワーク（初回は toolchain を落とす）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIO="${PIO:-$ROOT/.venv/bin/pio}"
WORK="${WORK:-${TMPDIR:-/tmp}/sanotts-arduino-ci}"
# ⚠️ Windows（MSYS の bash）では PlatformIO が `/c/Users/...` を読めないので `C:/Users/...` に直す。
ROOT_URI="$ROOT"
command -v cygpath >/dev/null 2>&1 && ROOT_URI="$(cygpath -m "$ROOT")"

# 主に使う platform は pioarduino（arduino-esp32 3.x / ESP-IDF 5.5）。
PLATFORM="${SANOTTS_PIO_PLATFORM:-https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip}"
# ⚠️ **公式の espressif32 6.x（arduino-esp32 2.0.17 / ESP-IDF 4.4）でもビルドできることを
#    G-AR9 で見る。** 2.x で組まれた既存アプリに、プラットフォームを替えずに入れられるように（D-069）。
#    4.4 は mmap の型名と mbedtls の `_ret` が違うだけで、`saan_dict.c` / `saan_model.c` が
#    `ESP_IDF_VERSION_MAJOR < 5` のときだけ名前を寄せる（5.x では 1 トークンも変わらない）。
#    ⚠️ **2.x では `SanoTTSSpeakerI2S` が使えない**（`driver/i2s_std.h` が無い。ヘッダごと消える）。
PLATFORM_2X="${SANOTTS_PIO_PLATFORM_2X:-espressif32@6.12.0}"
# ⚠️ **2.x は別の PlatformIO の置き場で組む。** 2 つの platform は `tool-esptoolpy` /
#    `framework-arduinoespressif32` を**同じ名前で取り合う**ので、同じ `~/.platformio` で
#    交互に回すと、後から入った方の esptool が前の方の Python 環境で動かずに落ちる
#    （`No module named 'intelhex'`。実際に踏んだ）。
PIO_CORE_2X="${SANOTTS_PIO_CORE_2X:-$HOME/.platformio-2x}"

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
    # ⚠️ Windows では `.exe` が付くので名前を前方一致で探す（完全一致だと 0 件で -2 になる）。
    od="$(find "$HOME/.platformio/packages" "$PIO_CORE_2X/packages"                -name 'xtensa-esp32s3-elf-objdump*' 2>/dev/null | head -1)"
    [ -n "$od" ] || { echo "-2"; return; }
    "$od" -d "$obj" | grep -c 'ee\.' || true
}

# ⚠️ **標準出力でディレクトリを返さないこと。** ビルドの進捗も同じ経路に出るので、
#    `$(build …)` で受けると「最後の行」がログ行になって黙って壊れる（実際に踏んだ）。
build() {       # $1 = 名前  $2 = board  $3 = 追加 build_flags  $4 = 追加 lib_deps  $5 = platform（省略時 $PLATFORM）
    local name="$1" board="$2" flags="$3" deps="$4" platform="${5:-$PLATFORM}"
    local d="$WORK/$name"
    rm -rf "$d"; mkdir -p "$d/src"
    cp "$ROOT/arduino/examples/PcmCallback/PcmCallback.ino" "$d/src/main.cpp"
    {
        echo "[env:t]"
        echo "platform = $platform"
        echo "board = $board"
        echo "framework = arduino"
        echo "board_build.partitions = huge_app.csv"
        [ -n "$flags" ] && echo "build_flags = $flags"
        echo "lib_deps ="
        echo "    symlink://$ROOT_URI/arduino"
        [ -n "$deps" ] && echo "    $deps"
    } > "$d/platformio.ini"
    echo "--- $name (platform=$platform board=$board flags='${flags:-なし}') ---"
    local core="${PLATFORMIO_CORE_DIR:-}"
    [ "$platform" = "$PLATFORM_2X" ] && core="$PIO_CORE_2X"
    if ( [ -n "$core" ] && export PLATFORMIO_CORE_DIR="$core"; "$PIO" run -d "$d" ) >"$d/build.log" 2>&1; then
        grep -E '^(RAM|Flash):' "$d/build.log" | sed 's/^/      /'
        ok "$name はビルドできた"
        # ⚠️ **暗黙の関数宣言は「ビルドが通った」の中に隠れる。** ESP-IDF 4.4 に無い API
        #    （`esp_partition_munmap` など）を呼んでも C では警告で済み、関数が使われなければ
        #    リンクも通る。**このライブラリの中で 1 件でも出たら落とす**（フレームワーク側の
        #    警告は見ない。`-Werror` を全体に掛けると他人のコードで落ちる）。
        local imp
        imp="$(grep -E 'arduino[/\]src[/\].*implicit declaration of function' "$d/build.log" || true)"
        if [ -n "$imp" ]; then
            echo "$imp" | sed 's/^/      /'
            ng "$name: このライブラリに暗黙の関数宣言がある（その版の ESP-IDF に無い API を呼んでいる）"
        fi
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
build kana esp32-s3-devkitc-1 '-DSANOTTS_NO_VOICE_LIB=1 -DSANOTTS_ENABLE_KANJI=0' ''
# (2) 漢字 + M5Unified（出荷に一番近い形）
build kanji-m5 esp32-s3-devkitc-1 '-DSANOTTS_NO_VOICE_LIB=1' 'm5stack/M5Unified@^0.2.7'
# (3) 非 S3（⚠️ **PIE が無い板。実時間には間に合わない**が、リンクは通るべき）
build non-s3 esp32dev '-DSANOTTS_NO_VOICE_LIB=1 -DSANOTTS_ARENA_HEAP=1' ''

echo
echo "=== G-AR9 arduino-esp32 2.x（公式 espressif32 6.x / ESP-IDF 4.4）でもビルドできるか ==="
# (4) かな専用
build kana-2x esp32-s3-devkitc-1 '-DSANOTTS_NO_VOICE_LIB=1 -DSANOTTS_ENABLE_KANJI=0' '' "$PLATFORM_2X"
# (5) 漢字 + M5Unified + **辞書の SHA-256 照合**（mbedtls 2.x の `_ret` 経路をコンパイルさせる）
#     ⚠️ SHA-256 を渡さないと `#ifdef SAAN_DICT_SHA256` の中が 1 行もコンパイルされず、
#        2.x で壊れる箇所を素通りする（実際に最初の 1 回はそこを見ていなかった）。
build kanji-m5-2x esp32-s3-devkitc-1     "-DSANOTTS_NO_VOICE_LIB=1 '-DSANOTTS_DICT_SHA256=\"$(printf '0%.0s' $(seq 64))\"'"     'm5stack/M5Unified@^0.2.7' "$PLATFORM_2X"
# (6) 重みを model パーティションから読む構成（`saan_model.c` の mmap 経路）
build part-2x esp32-s3-devkitc-1 '-DSANOTTS_MODEL_FROM_PARTITION=1 -DSANOTTS_ENABLE_KANJI=0' '' "$PLATFORM_2X"
# (7) 非 S3
build non-s3-2x esp32dev '-DSANOTTS_NO_VOICE_LIB=1 -DSANOTTS_ARENA_HEAP=1' '' "$PLATFORM_2X"

echo
echo "=== G-AR6 PIE が実際に効いているか（命令数）==="
for d in "$WORK/kana" "$WORK/kanji-m5" "$WORK/kana-2x" "$WORK/kanji-m5-2x"; do
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
for d in "$WORK/non-s3" "$WORK/non-s3-2x"; do
    [ -d "$d/.pio" ] || continue
    n="$(pie_count "$d")"
    if [ "$n" = "0" ]; then
        ok "$(basename "$d")（陰性対照）: PIE 命令 0（自動的に W8A32 に落ちている）"
    else
        ng "$(basename "$d")（陰性対照）: PIE 命令が ${n} 出ている。ee.* は ESP32-S3 専用のはず"
    fi
done

echo
if [ "$fail" = 0 ]; then echo "OK  G-AR1 / G-AR2 / G-AR6 / G-AR9 すべて通った"; else echo "NG! 上の NG を見ること"; fi
exit "$fail"
