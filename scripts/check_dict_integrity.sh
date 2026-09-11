#!/bin/bash
# D-063 の受け入れゲート（G34）: 辞書の SHA-256 検査が**効いている**か。
#
#     bash scripts/check_dict_integrity.sh
#
# ## なぜ要るのか
#
# 「一致した」は、`strcmp` が常に 0 を返しても成立する。**陽性対照が無い検査は空虚。**
# そこで **flash イメージの dict 領域を 1 ビットだけ反転**させて、
#   (B1) 不一致が検出されること
#   (B2) **起動が止まらず、かな専用で続くこと**（D-063 の B2）
# の両方を要求する。
#
# ## ⚠️ 見ていないもの
#
# - **実機**。QEMU だけ（flash モデルの違いは PCM を変えない = M-85 / M-86）
# - **速度**。QEMU の時間は実機の速度を予測しない（C-055）
# - **音**
#
# ⚠️ **CI では回らない**（ESP-IDF + QEMU + 13.7 MB の辞書が要る）。
#    `scripts/check_ci_coverage.py` の EXCLUDED に理由つきで登録してある。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DICT="${SAAN_DICT_BLOB:-$ROOT/csrc/k1_dict.bin}"
BUILD="${SAAN_D63_BUILD:-build_d63}"
QEMU="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin/qemu-system-xtensa"
OK_IMG=/tmp/d63_ok.bin
BAD_IMG=/tmp/d63_bad.bin
fail=0

ng() { printf '  NG! %s\n' "$1"; fail=1; }
ok() { printf '  OK  %s\n' "$1"; }

if [ ! -f "$DICT" ]; then
    echo "NG! 辞書が無い: $DICT"
    echo "   uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin"
    exit 2
fi
if [ ! -x "$QEMU" ]; then
    echo "⚠️ QEMU が無い（$QEMU）— このゲートは回せない"
    exit 2
fi
if [ -z "${IDF_PATH:-}" ]; then
    echo "⚠️ ESP-IDF が export されていない（. ~/esp/esp-idf/export.sh）— 回せない"
    exit 2
fi

echo "=== G34 辞書の SHA-256 検査（D-063）==="
echo "辞書: $DICT"
WANT="$(shasum -a 256 "$DICT" | cut -c1-64)"
echo "手元の digest: $WANT"

echo
echo "--- 1) ビルド（漢字 / QEMU） ---"
( cd "$ROOT/esp32" && idf.py -B "$BUILD" -DSDKCONFIG="$BUILD/sdkconfig" \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 -DSAAN_QEMU=1 -DSAAN_DICT_BLOB="$DICT" build ) > /tmp/d63_gate_build.log 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    echo "NG! ビルドが失敗した (rc=$rc)"; grep -iE "error:" /tmp/d63_gate_build.log | head -8; exit $rc
fi
# ⚠️ **ビルドログを見てはいけない。** `message(STATUS ...)` は CMake が再構成した
#    ときにしか出ないので、**増分ビルドでは必ず落ちる**（実際に踏んだ）。
#    見るのは `compile_commands.json` = **残る成果物**で、増分でも正しい値が入っている。
CC="$ROOT/esp32/$BUILD/compile_commands.json"
if [ ! -f "$CC" ]; then
    ng "compile_commands.json が無い（$CC）"
else
    # ⚠️ **JSON のエスケープを grep で書かない。** `-D...=\"<hex>\"` は
    #    compile_commands.json では `\\\"` になる。抽出して比べる方が壊れにくい。
    got="$(python3 -c '
import re, sys
m = re.search(r"SAAN_DICT_SHA256=\\\\*\"?([0-9a-f]{64})", open(sys.argv[1]).read())
print(m.group(1) if m else "")' "$CC")"
    if [ -z "$got" ]; then
        ng "SAAN_DICT_SHA256 が main に渡っていない（⚠️ **main/CMakeLists.txt は 2 つある**）"
    elif [ "$got" = "$WANT" ]; then
        ok "コンパイル時に焼かれた digest が手元のファイルと一致"
    else
        ng "焼かれた digest が手元のファイルと違う（file(SHA256) の経路が壊れている）"
        printf '      焼いた: %s\n      手元  : %s\n' "$got" "$WANT"
    fi
fi

echo
echo "--- 2) flash イメージ ---"
( cd "$ROOT/esp32/$BUILD" && esptool.py --chip esp32s3 merge_bin \
    --fill-flash-size 16MB -o "$OK_IMG" @flash_args ) > /dev/null 2>&1
[ -f "$OK_IMG" ] && ok "$(wc -c < "$OK_IMG" | tr -d ' ') B" || { ng "merge_bin が失敗"; exit 1; }

# ⚠️ **真ん中を壊す。** 先頭を壊すと jdict_open の検査に引っかかって
#    **SHA-256 に到達する前に落ちる**（= SHA-256 を試験していない）。
python3 - "$OK_IMG" "$BAD_IMG" <<'PY'
import pathlib, sys
DICT_OFF, BLOB_LEN = 0x2D0000, 13702320
b = bytearray(pathlib.Path(sys.argv[1]).read_bytes())
pos = DICT_OFF + BLOB_LEN // 2
b[pos] ^= 0x01
pathlib.Path(sys.argv[2]).write_bytes(bytes(b))
print(f"  --  陽性対照: offset 0x{pos:08x} の 1 ビットを反転した")
PY

run_qemu() {   # $1 = image, $2 = 出力ログ
    "$QEMU" -nographic -machine esp32s3 -m 4M \
        -drive file="$1",if=mtd,format=raw > "$2" 2>&1 &
    local pid=$!
    local i=0
    while [ $i -lt 120 ]; do
        grep -aq "対話モード" "$2" && break
        sleep 1; i=$((i + 1))
    done
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    grep -aq "対話モード" "$2"
}

echo
echo "--- 3) A) 正しい辞書 ---"
if run_qemu "$OK_IMG" /tmp/d63_gate_A.log; then
    grep -aq "辞書の SHA-256 一致" /tmp/d63_gate_A.log \
        && ok "SHA-256 が一致した" || ng "一致のログが出ていない"
    grep -aq "辞書 OK: 見出し語" /tmp/d63_gate_A.log \
        && ok "漢字経路が生きている" || ng "漢字経路が死んでいる（正しい辞書なのに）"
    grep -aq "漢字・カタカナの文はそのまま入力する" /tmp/d63_gate_A.log \
        && ok "案内が漢字を受け付けると言っている" || ng "案内が出ていない"
else
    ng "A) 対話モードに到達しなかった"
fi

echo
echo "--- 4) B) **陽性対照**: 1 ビット壊した辞書 ---"
if run_qemu "$BAD_IMG" /tmp/d63_gate_B.log; then
    grep -aq "辞書の SHA-256 が合わない" /tmp/d63_gate_B.log \
        && ok "不一致を検出した" \
        || ng "**1 ビット壊しても検出しなかった = 検査が空虚**"
    grep -aq "辞書 OK: 見出し語" /tmp/d63_gate_B.log \
        && ng "壊れた辞書で漢字経路が生きている" || ok "漢字経路が無効になった"
    grep -aq "かな入力だけ" /tmp/d63_gate_B.log \
        && ok "かな専用で続いた（D-063 の B2）" || ng "かな専用に落ちていない"
    grep -aq "漢字対応ビルドだが、辞書が使えない" /tmp/d63_gate_B.log \
        && ok "案内が漢字を勧めていない" \
        || ng "辞書が死んでいるのに漢字を勧めている（打ってから拒否される）"
    grep -aqE "錨と完全一致" /tmp/d63_gate_B.log \
        && ok "かなの G2P は動いている" || ng "かなの G2P も壊れた"
else
    ng "B) 対話モードに到達しなかった（**起動が止まった** = B2 に反する）"
fi

echo
if [ "$fail" = 0 ]; then
    echo "OK  G34 通過（陽性対照つき: 1 ビット壊すと漢字経路だけが無効になる）"
    echo "⚠️ 見ていないもの: **実機** / **速度**（QEMU の時間は実機を予測しない = C-055）/ **音**"
else
    echo "NG! G34 が落ちた"
fi
exit "$fail"
