#!/usr/bin/env bash
# G-SP4: 長文分割（D-070）が ESP-IDF の漢字ファームで効いているか（QEMU）。
#
#   bash scripts/check_split_qemu.sh --baseline   # 変更前のファームで A / B の checksum を控える
#   bash scripts/check_split_qemu.sh              # 判定
#
# ケース（1 回の QEMU 起動の中で `かな>` に順に打つ）:
#   A  上限内の 1 文        → checksum が基準と一致 / 「区間」の行が無い
#   B  上限内の 2 文        → 同上（**区切っていない**こと）
#   D  上限超えの 3 文      → 「区間 1/3」〜「区間 3/3」が出る / 区間 k の checksum が、
#                             その文を**単独で打ったとき**（S1 / S2 / S3）の checksum と一致
#   C  陽性対照: 2 文目だけ `、` で長く続く 3 文
#                           → 「区間 2/3」が長すぎると出る / **`出力 PCM` が 1 つも出ない**（全か無か）
#
# ⚠️ **ESP-IDF + QEMU + 辞書 13.7 MB が要る**ので CI では回らない（check_ci_coverage.py の EXCLUDED_SCRIPTS）。
# ⚠️ **ビルドログを見ない。** 判定は QEMU の UART の出力だけで行う（check_dict_integrity.sh と同じ理由）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-check}"
OUT="$ROOT/reports/split_qemu"
BUILD=build_split
DICT="$ROOT/csrc/k1_dict.bin"
DICT_SHA=f162c922074d76817298b34d8a8fd35f7d195f38540303485a76c956b5d84877   # v1.2.0 の k1-dict-438750.bin
QEMU="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin/qemu-system-xtensa"
mkdir -p "$OUT"

N_OK=0; N_NG=0
ok() { printf '  OK  %s\n' "$1"; N_OK=$((N_OK + 1)); }
ng() { printf '  NG! %s\n' "$1"; N_NG=$((N_NG + 1)); }

# --- 入力 ---------------------------------------------------------------------
A='今日は良い天気ですね。'
B='今日は良い天気ですね。明日も晴れるでしょう。'
S1='今日は朝から雨が降っていたので、駅まで歩いて行くのをやめてバスに乗りました。'
S2='バスの中はとても混んでいて、座る場所がなかったので、ずっと立ったままでした。'
S3='会社に着いたころには雨もやんで、空がすっかり明るくなっていました。'
D="$S1$S2$S3"
SL='雨が降り、風が吹き、雷が鳴り、川があふれ、道が崩れ、電車が止まり、バスも止まり、学校が休みになり、店が閉まり、町が静かになり、人は家にいた。'
C="$S1$SL$S3"

echo "--- 0) 前提 ---"
[ -f "$DICT" ] || { echo "辞書が無い: ${DICT}（gh release download v1.2.0 -p k1-dict-438750.bin）"; exit 2; }
got="$(shasum -a 256 "$DICT" | cut -d' ' -f1)"
[ "$got" = "$DICT_SHA" ] && ok "辞書は v1.2.0 の k1-dict-438750.bin" || { ng "辞書の SHA-256 が違う: $got"; exit 1; }
[ -x "$QEMU" ] || { echo "QEMU が無い: $QEMU"; exit 2; }
for v in A B D C; do
    n=$(printf '%s' "${!v}" | wc -c | tr -d ' ')
    # ⚠️ 端末の 1 行は 512 B まで（SAAN_CONSOLE_LINE_MAX）。超えると入力の段で切られ、分割を試験できない
    [ "$n" -lt 512 ] && ok "入力 $v は $n B（< 512）" || ng "入力 $v が $n B で 1 行の上限を超える"
done

echo
echo "--- 1) ビルド（漢字 QEMU 構成）---"
export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:$PATH"
# shellcheck disable=SC1091
. "$HOME/esp/esp-idf/export.sh" > /dev/null 2>&1
( cd "$ROOT/esp32" && idf.py -B "$BUILD" -DSDKCONFIG="$BUILD/sdkconfig" \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 -DSAAN_QEMU=1 -DSAAN_DICT_BLOB="$DICT" build ) > "$OUT/build.log" 2>&1 \
    && ok "ビルドできた" || { ng "ビルドが失敗（$OUT/build.log）"; exit 1; }
IMG="$OUT/flash16.bin"
( cd "$ROOT/esp32/$BUILD" && esptool.py --chip esp32s3 merge_bin \
    --fill-flash-size 16MB -o "$IMG" @flash_args ) > /dev/null 2>&1 \
    && ok "flash イメージ $(wc -c < "$IMG" | tr -d ' ') B" || { ng "merge_bin が失敗"; exit 1; }

# --- 2) QEMU に順に打つ -----------------------------------------------------------
# $@ = 打つ行。ログは ${LOG}。プロンプトが (打った行数 + 1) 回出るまで待つ
LOG="$OUT/qemu.log"
run_session() {
    local fifo="$OUT/uart.fifo"
    rm -f "$fifo"; mkfifo "$fifo"
    "$QEMU" -nographic -machine esp32s3 -m 4M -drive file="$IMG",if=mtd,format=raw \
        < "$fifo" > "$LOG" 2>&1 &
    local pid=$!
    exec 3> "$fifo"
    local want=1 t
    for line in "$@"; do
        t=0
        while [ "$(grep -Eac '^.{0,8}かな> ' "$LOG")" -lt "$want" ]; do
            sleep 1; t=$((t + 1))
            [ $t -gt 600 ] && { echo "  プロンプト $want 回目が来ない"; kill "$pid"; exec 3>&-; return 1; }
        done
        printf '%s\r' "$line" >&3
        want=$((want + 1))
    done
    t=0
    while [ "$(grep -Eac '^.{0,8}かな> ' "$LOG")" -lt "$want" ]; do
        sleep 1; t=$((t + 1)); [ $t -gt 600 ] && break
    done
    exec 3>&-
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    rm -f "$fifo"
}

# 入力 k（1 始まり）に対応するログの区間 = k 回目と k+1 回目のプロンプトの間
chunk() {
    awk -v k="$1" '$0 ~ /^.?.?.?.?.?.?.?.?かな> / { n++ } n == k' "$LOG"
}
sums() { grep -ao 'FNV-1a 0x[0-9a-f]*' | sed 's/FNV-1a //'; }

echo
if [ "$MODE" = "--baseline" ]; then
    echo "--- 2) 基準値を採る（A / B）---"
    run_session "$A" "$B" || { ng "QEMU が最後まで行かなかった"; exit 1; }
    a="$(chunk 1 | sums)"; b="$(chunk 2 | sums)"
    [ -n "$a" ] && [ -n "$b" ] || { ng "checksum が出ていない（${LOG}）"; exit 1; }
    printf 'A %s\nB %s\n' "$a" "$b" > "$OUT/baseline.txt"
    ok "基準値を $OUT/baseline.txt に保存: A $a / B $b"
    exit 0
fi

[ -f "$OUT/baseline.txt" ] || { echo "基準値が無い。先に --baseline を変更前のツリーで走らせること"; exit 2; }
echo "--- 2) QEMU（A B D S1 S2 S3 C）---"
run_session "$A" "$B" "$D" "$S1" "$S2" "$S3" "$C" || { ng "QEMU が最後まで行かなかった"; exit 1; }

echo
echo "--- 3) 判定 ---"
base_a="$(awk '$1=="A"{print $2}' "$OUT/baseline.txt")"
base_b="$(awk '$1=="B"{print $2}' "$OUT/baseline.txt")"
for k in 1 2; do
    want="$base_a"; name=A; [ $k -eq 2 ] && { want="$base_b"; name=B; }
    got="$(chunk $k | sums)"
    [ "$got" = "$want" ] && ok "$name: checksum が基準と一致（${got}）" || ng "$name: checksum ${got}（基準 ${want}）"
    chunk $k | grep -aq '区間 ' && ng "$name: 区切っている（上限内なのに）" || ok "$name: 区切っていない"
done

d="$(chunk 3)"
for k in 1 2 3; do
    printf '%s\n' "$d" | grep -aq "区間 $k/3" && ok "D: 区間 $k/3 を喋った" || ng "D: 区間 $k/3 の行が無い"
done
d_sums=( $(printf '%s\n' "$d" | sums) )
for k in 1 2 3; do
    single="$(chunk $((k + 3)) | sums)"
    [ -n "$single" ] && [ "${d_sums[$((k - 1))]:-}" = "$single" ] \
        && ok "D: 区間 $k の checksum が単独で打った S$k と一致（${single}）" \
        || ng "D: 区間 $k の checksum ${d_sums[$((k - 1))]:-無し}（単独 S$k は ${single:-無し}）"
done
[ "${#d_sums[@]}" -eq 3 ] && ok "D: 出力 PCM はちょうど 3 回" || ng "D: 出力 PCM が ${#d_sums[@]} 回"

c="$(chunk 7)"
printf '%s\n' "$c" | grep -aq '区間 2/3' && ok "C: 区間 2/3 が駄目だと言った" || ng "C: 区間 2/3 のメッセージが無い"
n_c="$(printf '%s\n' "$c" | sums | wc -l | tr -d ' ')"
[ "$n_c" -eq 0 ] && ok "C: 何も喋っていない（全か無か）" || ng "C: $n_c 回喋った（1 区間でも駄目なら何も喋らないはず）"

echo
echo "$((N_OK + N_NG)) 件中 $N_OK 件 OK"
[ "$N_NG" -eq 0 ]
