#!/bin/sh
# G-1: W8A8 のパディング部が 0 で埋まっているかを 4 通りのビルドで検査する。
# ⚠️ **陽性対照つき。** 詳細は csrc/int8_pad_test.c の冒頭。
set -e
cd "$(dirname "$0")"
CC_="${CC:-cc}"
SRC="int8_pad_test.c saanotts.c saanotts_stream.c saanotts_int8.c fft.c"
BASE="-std=c99 -O2 -Wall -Wextra -DSAAN_INT8_ACT=1 -DSAAN_PIE_EMU=1"
OUT="${TMPDIR:-/tmp}/saan_pad"
mkdir -p "$OUT"

build_run() {   # $1=name  $2=extra flags
    # shellcheck disable=SC2086
    $CC_ $BASE $2 -o "$OUT/$1" $SRC -lm
    "$OUT/$1"
}

echo "== G-1 W8A8 パディング部のゼロ埋め =="
CLEAN=$(build_run clean ""                          | sed 's/.*checksum=//')
PW=$(build_run pw    "-DSAAN_PAD_POISON_W=1"        | sed 's/.*checksum=//')
PA=$(build_run pa    "-DSAAN_PAD_POISON_A=1"        | sed 's/.*checksum=//')
PB=$(build_run pb    "-DSAAN_PAD_POISON_W=1 -DSAAN_PAD_POISON_A=1" | sed 's/.*checksum=//')

bad=0
chk() {   # $1=label $2=got $3=want(same|diff) $4=why
    if [ "$3" = same ]; then
        [ "$2" = "$CLEAN" ] && echo "  OK  $1" || { echo "  NG! $1  ($2 != $CLEAN)"; bad=1; }
    else
        [ "$2" != "$CLEAN" ] && echo "  OK  $1" || { echo "  NG! $1  (差が出ない = 検査が空虚)"; bad=1; }
    fi
}
chk "G-1a 重みの隙間を 127 にしても出力不変 → 活性化側のゼロ埋めが効いている" "$PW" same
chk "G-1b 活性化の隙間を 127 にしても出力不変 → 重み側のゼロ埋めが効いている" "$PA" same
chk "G-1c 陽性対照: 両方汚すと出力が変わる（隙間は本当に読まれている）"       "$PB" diff

echo "  基準 checksum=$CLEAN"
if [ $bad -ne 0 ]; then echo "G-1: FAIL"; exit 1; fi
echo "G-1: PASS"
