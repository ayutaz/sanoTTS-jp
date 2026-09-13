#!/usr/bin/env bash
# G-AR4 — **Arduino ビルドの PCM が ESP-IDF ビルドと bit 一致するか**（QEMU）。
#
# ⚠️ **これが唯一「同じ音が出る」を証明するゲート。** G-AR1/2/3/6/7 は全部
#    「ビルドが通るか」「PIE 命令が在るか」しか言っていない。フラグが 1 つ違えば
#    **ビルドは通り、音も出て、波形だけが違う**。
#
# ## 何と突き合わせるのか
#
# `esp32/main/saan_pcm.c` の FNV-1a 64（`saan_pcm_checksum()`）。ESP-IDF 版が
# 実機と QEMU で出している **v4 の**基準値（docs/measurements.md M-124 / M-130 / M-132）:
#
#     W8A8 + PIE : 0x390bf4b2aef8f2ec / 27,136 sample
#     W8A32      : 0x9cbe622a4a53af7e / 27,648 sample
#
# ⚠️ **v3 の値と混ぜないこと。** v3 は W8A8+PIE `0xa69a7ebbb5ccb05f` /
#    W8A32 `0xe4b645c30835d42d` で、**重みが違うので当然違う**。
#    CLAUDE.md の「PIE カーネル」節にあるのは v3 の値で、**実際にこれで一度
#    間違えた**（2026-09-13。一致しているのに「違う」と出た）。
#
# ⚠️ **2 つとも一致して初めて「フラグが効いている」と言える。** W8A8 だけ見ると、
#    PIE が無効でも W8A8 なら同じ値が出る（PIE はスカラ実装と bit 一致するため）。
#    W8A32 側は `-DSANOTTS_ENABLE_PIE=0` の**陽性対照**として回す。
#
# ⚠️ **ホストと比べてはいけない。** float の丸めが違うので必ずずれる。
#    bit 一致を主張してよいのは**同じターゲット上の 2 構成**だけ。
#
# ## 要るもの（CI では回らない理由）
#
#   - ESP-IDF の QEMU（`~/.espressif/tools/qemu-xtensa/.../qemu-system-xtensa`）
#   - 重み blob（リリース資産 saanotts-jp-v4-int8.bin）
#   - PlatformIO
#
#   bash scripts/check_arduino_qemu.sh
#
# ⚠️ **手で走らせるゲートはいずれ走らせなくなる。**
#    `arduino/src/sanotts_config.h` か `scripts/build_arduino_lib.py` の前置きを
#    触ったら回すこと。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIO="${PIO:-$ROOT/.venv/bin/pio}"
WORK="${WORK:-${TMPDIR:-/tmp}/sanotts-arduino-qemu}"
BLOB="${BLOB:-/tmp/w/saanotts-jp-v4-int8.bin}"
PLATFORM="${SANOTTS_PIO_PLATFORM:-https://github.com/pioarduino/platform-espressif32/releases/download/55.03.311/platform-espressif32.zip}"

# ESP-IDF 版が実機と QEMU で出している基準（CLAUDE.md / M-90 / M-62）。
EXPECT_W8A8="0x390bf4b2aef8f2ec";  EXPECT_W8A8_N=27136
EXPECT_W8A32="0x9cbe622a4a53af7e"; EXPECT_W8A32_N=27648

fail=0
ng() { echo "  NG  $*"; fail=1; }
ok() { echo "  OK  $*"; }

QEMU="$(find "$HOME/.espressif/tools/qemu-xtensa" -name 'qemu-system-xtensa' 2>/dev/null | head -1)"
[ -n "$QEMU" ] || { echo "NG! qemu-system-xtensa が無い（ESP-IDF の QEMU を入れること）"; exit 1; }
[ -f "$BLOB" ] || { echo "NG! 重み blob が無い: $BLOB"; exit 1; }


rm -rf "$WORK"; mkdir -p "$WORK"
uv run --no-project --python 3.12 python "$ROOT/scripts/build_arduino_lib.py" \
    --zip "$WORK/dist" --version qemu --blob "$BLOB" >/dev/null
CODE_ZIP="$WORK/dist/sanoTTS-jp-arduino-qemu.zip"
VOICE_ZIP="$WORK/dist/sanoTTS-jp-voice-tsukuyomi-v4-qemu.zip"

run_one() {   # $1 = 名前  $2 = 追加 build_flags  $3 = 期待する checksum  $4 = 期待する sample 数
    local name="$1" flags="$2" want="$3" want_n="$4"
    local d="$WORK/$name"
    mkdir -p "$d/src"
    # ⚠️ **かな中間表現を使う**（辞書を焼かずに済む。ESP-IDF 側の基準値も
    #    同じ 1 行から出ている = CLAUDE.md の「基準の 1 行」）。
    cat > "$d/src/main.cpp" <<'INO'
#include <Arduino.h>
#include <SanoTTS.h>
SanoTTS tts;
static void sink(const int16_t* p, size_t n, void* u) { (void)p; (void)n; (void)u; }
void setup() {
  Serial.begin(115200);
  delay(200);
  if (!tts.begin()) { Serial.printf("SAAN_ERR %s\n", tts.lastError()); return; }
  if (!tts.synthesize("きょ][おわよ][いて][んきです°ね", sink)) {
    Serial.printf("SAAN_ERR %s\n", tts.lastError()); return;
  }
  Serial.printf("SAAN_CHECKSUM 0x%016llx %u\n",
                (unsigned long long)tts.checksum(), (unsigned)tts.samples());
}
void loop() {}
INO
    cat > "$d/platformio.ini" <<EOF
[env:qemu]
platform = $PLATFORM
board = esp32-s3-devkitc-1
framework = arduino
board_build.partitions = huge_app.csv
board_build.flash_mode = dio
build_flags = $flags
lib_deps =
    file://$CODE_ZIP
    file://$VOICE_ZIP
EOF
    echo "--- $name (${flags:-フラグ無し}) ---"
    if ! "$PIO" run -d "$d" >"$d/build.log" 2>&1; then
        tail -20 "$d/build.log" | sed 's/^/      /'
        ng "$name のビルドが失敗した"
        return
    fi

    # ⚠️ **QEMU の flash モデルは QIO を受け付けない**（M-85 / M-86）。
    #    board_build.flash_mode = dio にしてある。値は変わらない。
    # ⚠️ **esptool を呼ばない。** PlatformIO が既に bootloader / partitions /
    #    boot_app0 / firmware を結合した `firmware.factory.bin` を作っている。
    #    残るのは QEMU が要求する flash 容量までの 0xFF 埋めだけ。
    #    （esptool.py の shebang は `env python` で、この環境には `python` が無い。
    #      `uv run` を通さない python は hook も止める）
    local merged="$d/flash.bin"
    local factory="$d/.pio/build/qemu/firmware.factory.bin"
    [ -f "$factory" ] || { ng "$name: firmware.factory.bin が無い"; return; }
    # 8 MB ぶんの 0xFF を作ってから先頭に factory を上書きする。
    #   ⚠️ `tr` で 0xFF を作る（`dd if=/dev/zero` だと 0x00 になり、
    #      消去済み flash と違う値になる。今回は読むだけなので害は無いが、揃えておく）
    ( head -c 8388608 /dev/zero | LC_ALL=C tr '\000' '\377' ) > "$merged"
    dd if="$factory" of="$merged" bs=1 conv=notrunc status=none

    # ⚠️ **タイムアウトを付ける。** 合成が落ちると QEMU は戻ってこない。
    ( "$QEMU" -nographic -machine esp32s3 -m 8M \
        -drive file="$merged",if=mtd,format=raw >"$d/qemu.log" 2>&1 & echo $! > "$d/pid" ) || true
    local pid; pid="$(cat "$d/pid")"
    local i=0
    while [ $i -lt 180 ]; do
        grep -q "SAAN_CHECKSUM\|SAAN_ERR" "$d/qemu.log" 2>/dev/null && break
        sleep 1; i=$((i+1))
    done
    kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true

    local got
    got="$(grep -o 'SAAN_CHECKSUM 0x[0-9a-f]\{16\} [0-9]*' "$d/qemu.log" | head -1 || true)"
    if [ -z "$got" ]; then
        echo "      （QEMU のログ末尾）"; tail -12 "$d/qemu.log" | sed 's/^/      /'
        ng "$name: QEMU から checksum が出なかった（$d/qemu.log）"
        return
    fi
    local sum samples
    sum="$(echo "$got" | awk '{print $2}')"
    samples="$(echo "$got" | awk '{print $3}')"
    if [ "$sum" = "$want" ]; then
        ok "$name: checksum $sum が ESP-IDF の基準と bit 一致"
    else
        ng "$name: checksum $sum が基準 $want と違う。"\
"**フラグが ESP-IDF 版と食い違っている**（ビルドは通り、音も出るので他のゲートでは出ない）"
    fi
    # ⚠️ **サンプル数も見る。** checksum だけだと、たまたま合う可能性を
    #    否定できないうえ、「どちらの構成の基準と比べたか」を取り違えても気づけない
    #    （W8A8 と W8A32 は長さが 512 sample 違う）。
    [ "$samples" = "$want_n" ] \
        && ok "$name: ${samples} sample（基準と同じ）" \
        || ng "$name: ${samples} sample（期待 ${want_n}）"
}

echo "=== G-AR4 Arduino ビルドの PCM が ESP-IDF ビルドと bit 一致するか ==="
run_one w8a8  ''                        "$EXPECT_W8A8"  "$EXPECT_W8A8_N"
# ⚠️ **陽性対照。** これが W8A8 と同じ値を出したら、フラグが効いていない。
run_one w8a32 '-DSANOTTS_ENABLE_PIE=0'  "$EXPECT_W8A32" "$EXPECT_W8A32_N"

echo
if [ "$fail" = 0 ]; then
    echo "OK  G-AR4 2 構成とも ESP-IDF の基準と bit 一致"
else
    echo "NG! 上の NG を見ること。**一致しないうちはマージしない。**"
fi
exit "$fail"
