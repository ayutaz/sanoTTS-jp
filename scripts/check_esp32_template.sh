#!/usr/bin/env bash
# esp32/ 雛形の手元ゲート（c'-4）
#
# ⚠️ **ここで通るのは「ホストで確かめられること」だけ。**
#    ⚠️ **かつて「ESP-IDF も xtensa toolchain もこの環境に無い」と書いていたが誤り**
#    （導入していなかっただけ。C-033）。v5.5 は導入済みで、`idf.py build` は通り、
#    QEMU で出荷ファームが完走する（M-54 / M-62）。**それはこのスクリプトの外**で、
#    ここは ESP-IDF 無しで走る検査だけを持つ。
#    **flash / 実 SRAM / 実レイテンシ / I2S の実サンプルレートは今も未検証。**
#
#   bash scripts/check_esp32_template.sh
set -u
cd "$(dirname "$0")/.."
ROOT="$PWD"
# ⚠️ g2p.c と line.c も含める。esp32/main/ が saan_g2p() / saan_line_feed() を
#    呼ぶので、「ホスト専用 API を参照していない」ゲートの対象に入っていないといけない
CORE="saanotts.c saanotts_stream.c fft.c saanotts_int8.c g2p.c line.c"
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

hdr() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
ok()  { printf '  OK  %s\n' "$1"; }
ng()  { printf '  NG! %s\n' "$1"; FAIL=1; }

# ---------------------------------------------------------------- 1
hdr "1. コアが IDF 既定の方言 (-std=gnu17) で警告 0"
# ⚠️ **-std=c99 では判定しない。** IDF の既定は gnu17 で、-std=c99 だと
#    newlib の M_PI が __STRICT_ANSI__ で隠れる差を見逃す
#    （csrc/saanotts.c と csrc/saanotts_stream.c が M_PI を無条件に使う）。
for f in $CORE; do
    if cc -std=gnu17 -O2 -Wall -Wextra -Werror -c "csrc/$f" -o "$TMP/${f%.c}.o" 2>"$TMP/w"; then
        ok "csrc/$f  0 warning / 0 error"
    else
        ng "csrc/$f"; sed 's/^/      /' "$TMP/w"
    fi
done

# ---------------------------------------------------------------- 2
hdr "2. コアにホスト専用 API が無い（c'-4 の実質的な主ゲート）"
nm -u "$TMP"/*.o 2>/dev/null | sed 's/^ *//' | grep -v '^/' | grep -v '^$' | sort -u > "$TMP/undef"
BANNED='^_(malloc|calloc|realloc|free|fopen|fclose|fread|fwrite|fseek|ftell|open|read|write|close|mmap|munmap|stat|pthread_[a-z_]*|exit|_exit|abort|getenv|system|time|clock|clock_gettime|gettimeofday|printf|fprintf|puts|putchar)$'
if grep -qE "$BANNED" "$TMP/undef"; then
    ng "ホスト専用 API を参照している:"; grep -E "$BANNED" "$TMP/undef" | sed 's/^/      /'
else
    ok "malloc / free / fopen / mmap / POSIX / printf を 1 つも参照していない"
fi
# double 版の cos/sin が出たら naive DFT が消えていない = S3 でソフト浮動小数に落ちる
if grep -qE '^_(cos|sin)$' "$TMP/undef"; then
    ng "double の cos/sin を参照している（naive DFT が -O2 で消えていない）"
else
    ok "double の cos/sin なし（naive DFT は -O2 で除去されている）"
fi
echo "  未定義シンボル一覧（saan_* を除く）:"
grep -v '^_saan_' "$TMP/undef" | sed 's/^/      /'

# ---------------------------------------------------------------- 3
hdr "3. csrc に残っているホスト専用 API の在処（監査表）"
printf '  %-22s %-6s %s\n' "ファイル" "役割" "ホスト専用 API"
for f in csrc/*.c; do
    b="$(basename "$f")"
    case "$b" in
        saanotts.c|saanotts_stream.c|fft.c|saanotts_int8.c|g2p.c|line.c) role="コア" ;;
        *) role="テスト" ;;
    esac
    hits="$(grep -cE '\b(malloc|calloc|realloc|free|fopen|fclose|fread|fwrite|fseek|ftell|printf|fprintf|exit|clock_gettime|mmap)\s*\(' "$f")"
    lines="$(grep -nE '\b(malloc|calloc|realloc|free|fopen|fclose|fread|fwrite|fseek|ftell|printf|fprintf|exit|clock_gettime|mmap)\s*\(' "$f" | cut -d: -f1 | head -4 | paste -sd, -)"
    if [ "$hits" = "0" ]; then
        printf '  %-22s %-6s %s\n' "$b" "$role" "なし"
    else
        printf '  %-22s %-6s %s 箇所 (行 %s%s)\n' "$b" "$role" "$hits" "$lines" \
               "$([ "$hits" -gt 4 ] && echo ' …')"
        if [ "$role" = "コア" ]; then
            # vsnprintf / snprintf だけは許容（テンソル名の組み立て）
            other="$(grep -nE '\b(malloc|calloc|realloc|free|fopen|fclose|fread|fwrite|fseek|ftell|printf|fprintf|exit|clock_gettime|mmap)\s*\(' "$f" | grep -vcE '\b(v?snprintf)\s*\(')"
            [ "$other" != "0" ] && ng "$b（コア）に $other 箇所のホスト専用 API"
        fi
    fi
done
echo "  ⚠️ コアの vsnprintf / snprintf は saan_tf のテンソル名組み立て。newlib にある"

# ---------------------------------------------------------------- 4
hdr "4. 重み blob のレイアウト（アライメント）"
uv run python - <<'PY' || FAIL=1
import struct, pathlib, sys
HDR = 64 + 4 + 4 + 16 + 8 + 8
bad = 0
for name in ("csrc/student.bin", "csrc/student_i8.bin"):
    p = pathlib.Path(name)
    if not p.exists():
        print(f"  NG! 無い: {name}"); bad = 1; continue
    b = p.read_bytes()
    magic = b[:4]; ver, n = struct.unpack_from("<II", b, 4)
    off_bad = payload = 0
    for i in range(n):
        e = 16 + i * HDR
        off, nb = struct.unpack_from("<QQ", b, e + 64 + 8 + 16)
        if off % 16: off_bad += 1
        payload += nb
    okmagic = magic == b"SAAN" and ver == 1
    print(f"  {'OK ' if okmagic else 'NG!'} {name}: magic={magic.decode(errors='replace')} "
          f"v{ver} / {n} tensors / file {len(b):,} B / payload {payload:,} B")
    print(f"  {'OK ' if off_bad == 0 else 'NG!'}   全テンソルの offset が 16 の倍数"
          f"（違反 {off_bad} 件）")
    if not okmagic or off_bad: bad = 1
print("  ⚠️ offset が 16 の倍数なので、**blob の先頭さえ 16 バイト境界なら**")
print("     全テンソルが 16 バイト境界に載る。ずれるとしたら base だけ")
sys.exit(bad)
PY

# ---------------------------------------------------------------- 5
hdr "5. CMake の構文（ESP-IDF 不要）"
if cmake -P scripts/check_cmake_syntax.cmake > "$TMP/cm" 2>&1; then
    ok "esp32 の CMakeLists.txt 3 本が構文として通る"
else
    ng "CMake 構文"; sed 's/^/      /' "$TMP/cm"
fi
echo "  ⚠️ **構文検査であって configure ではない。** 通っても idf.py build が通るとは言えない"

# ---------------------------------------------------------------- 6
hdr "6. partitions.csv"
if uv run python scripts/check_partitions.py > "$TMP/pt" 2>&1; then
    sed -n '2,$p' "$TMP/pt" | sed 's/^/  /'
else
    ng "partitions.csv"; sed 's/^/      /' "$TMP/pt"
fi

# ---------------------------------------------------------------- 7
hdr "7. main.c が呼ぶ saan_* が csrc のヘッダに実在するか（typo 検出）"
grep -ohE '\bsaan_[a-z0-9_]+\s*\(' esp32/main/*.c \
  | sed 's/[[:space:]]*($//; s/($//' | tr -d '(' | sort -u > "$TMP/used"
MISS=0
while read -r sym; do
    case "$sym" in
        saan_model_*|saan_audio_*|saan_pcm_*|saan_ui_*|saan_f32_to_i16|saan_stub_*|saan_console_*) continue ;;  # 雛形自身の関数
        # K トラックの端末側（esp32/main/saan_dict.c / saan_kanji.c）。csrc ではなく main の関数
        saan_dict_*|saan_kanji_*) continue ;;
    esac
    if grep -qE "\b$sym\b" csrc/*.h; then
        printf '  OK  %-28s csrc のヘッダにある\n' "$sym"
    else
        ng "$sym が csrc のどのヘッダにも無い（typo か、内部関数を呼んでいる）"
        MISS=1
    fi
done < "$TMP/used"
[ "$MISS" = "0" ] && ok "esp32/main/*.c が呼ぶコア API はすべて実在"

# ---------------------------------------------------------------- 8
hdr "8. ホスト stub ビルド + C コアとの突き合わせ"
if cc -std=gnu17 -O2 -Wall -Wextra -Werror \
     -I esp32/host_stub -I esp32/main -I csrc \
     -o "$TMP/hoststub" \
     esp32/main/main.c esp32/main/saan_model.c esp32/main/saan_i2s.c \
     esp32/main/saan_pcm.c esp32/main/saan_ui_null.c \
     esp32/host_stub/stubs.c esp32/host_stub/host_main.c \
     csrc/saanotts.c csrc/saanotts_stream.c csrc/fft.c csrc/saanotts_int8.c \
     csrc/g2p.c \
     -lm 2>"$TMP/hw"; then
    ok "esp32/main の 5 ファイル + stub が 0 warning / 0 error でビルドできる"
else
    ng "ホスト stub ビルド"; sed 's/^/      /' "$TMP/hw"
fi
mkdir -p reports/esp32_hoststub
for blob in student.bin student_i8.bin; do
    [ -f "csrc/$blob" ] || continue
    out="reports/esp32_hoststub/${blob%.bin}.wav"
    if "$TMP/hoststub" "csrc/$blob" csrc/golden.bin "$out" > "$TMP/hs" 2>&1; then
        ok "csrc/$blob: $(grep -c '  OK ' "$TMP/hs") チェック通過 → $out"
        grep -E '^\s+(OK|NG!|\[参考\]|blob の dtype)' "$TMP/hs" | sed 's/^/      /'
    else
        ng "csrc/$blob でホスト stub が落ちた"; tail -20 "$TMP/hs" | sed 's/^/      /'
    fi
done

# ---------------------------------------------------------------- 9
hdr "9. ESP-IDF API の棚卸し（**このスクリプトでは実在を確認しない**）"
echo "  esp32/main が呼ぶ IDF の識別子。ホスト stub は自作なので、ここでは綴りを検証しない。"
echo "  ✅ ただし **v5.5 で実際にビルドが通り QEMU で動いた**ので、綴り自体は確認済み（M-54 / M-62）:"
grep -ohE '\b(i2s_[a-z0-9_]+|esp_partition_[a-z0-9_]+|esp_timer_[a-z0-9_]+|heap_caps_[a-z0-9_]+|xTaskCreate|vTaskDelete|uxTaskGetStackHighWaterMark|esp_err_to_name|ESP_LOG[IWED]|I2S_[A-Z0-9_]+|ESP_PARTITION_[A-Z0-9_]+|MALLOC_CAP_[A-Z0-9_]+|portMAX_DELAY|CONFIG_[A-Z0-9_]+)\b' \
  esp32/main/*.c esp32/main/*.h | sort -u | sed 's/^/      /'

# ---------------------------------------------------------------- 結果
printf '\n'
if [ "$FAIL" = "0" ]; then
    printf '\033[1m手元のゲートはすべて通った。\033[0m\n'
else
    printf '\033[1mNG: 通っていないゲートがある。\033[0m\n'
fi
cat <<'MSG'

⚠️ **手元では判定できないもの（ゲートにしていない）**:
   1. flash に焼けるか / 実機で起動するか
   2. 実際の SRAM 消費（IDF + FreeRTOS + I2S DMA を含む free heap）
   3. 実際の xRT とアンダーランの有無 — M-43 の 2.47 x RT は**外挿**であって実測ではない
   4. I2S の実サンプルレート誤差（ESP32-S3 に APLL が無い）
   5. flash から mmap した重みが D-cache を thrash しないか
   6. 実機の UART からかなが届くか（QEMU の擬似シリアルでは届いた。M-63）

   ✅ **ここから外れたもの**（このスクリプトの外で検証済み）:
      idf.py build（M-54）/ QEMU での起動と合成完走（M-62）/
      sdkconfig.defaults のオプション名 / IDF API の綴り（ビルドが通った）
MSG
exit "$FAIL"
