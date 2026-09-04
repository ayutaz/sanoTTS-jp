#!/usr/bin/env bash
# W-1: web/saan_web.c と C99 コア・漢字経路を WebAssembly に落とす。
#
#   EM_CONFIG=/path/to/.emscripten PATH="/path/to/emsdk/upstream/emscripten:$PATH" bash web/build.sh
#
# 出るもの（web/dist/）:
#   saan_web_w8a32.mjs / .wasm   W8A32（重みだけ int8。**品質ゲートを通るレーン**）
#   saan_web_w8a8.mjs  / .wasm   W8A8（活性化も int8。**実機の出荷構成そのもの**）
#
# ⚠️ **2 本とも同じ blob を読む。** レーンはビルド時に決まる（`SAAN_INT8_ACT`）ので、
#    JS は `_saan_web_lane()` で自分がどちらか確かめられる。
#
# ⚠️ **`-msimd128` は速度だけの選択。** held-out 24 文で SIMD の有無に関わらず
#    PCM が bit 一致することを実測してある（W8A8 / W8A32 とも 24/24）。
#
# ⚠️ **ここで出す .wasm のサイズも node の速度も、ブラウザの数字ではない。**
#    ブラウザでは誰も測っていない（計画 §8 / C-055）。
set -eu

# --- 場所 -------------------------------------------------------------------
# ⚠️ 相対パスで呼ばれてもいいように、スクリプトの位置からリポジトリ根を出す。
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
OUT="$HERE/dist"

# --- emcc ------------------------------------------------------------------
# ⚠️ **CI と手元で場所が違う。** 環境変数で上書きできるようにする。
#    `EM_CONFIG` は emcc 自身が読むので、ここでは触らずそのまま渡す
#    （export されていれば効く。emsdk_env.sh を通していないときはこれが要る）。
EMCC="${EMCC:-emcc}"
if ! command -v "$EMCC" >/dev/null 2>&1 && [ ! -x "$EMCC" ]; then
    echo "emcc が見つからない: $EMCC" >&2
    echo "  例: EM_CONFIG=\$HOME/emsdk/.emscripten PATH=\"\$HOME/emsdk/upstream/emscripten:\$PATH\" bash web/build.sh" >&2
    exit 1
fi

# ⚠️ **版を固定する理由は再現性。** 速度・サイズの実測はこの版で取ってある
#    （計画 §1: setup-emsdk の version 既定は `latest`）。
#    落とさず警告に留めるのは、版が上がったときに「まずビルドは通るのか」を見たいから。
EMCC_WANT="${EMCC_WANT:-6.0.9}"
EMCC_VER="$("$EMCC" --version 2>/dev/null | head -1 | sed -n 's/.*replacement + linker emulating GNU ld) \([0-9][0-9.]*\).*/\1/p')"
if [ -z "$EMCC_VER" ]; then
    echo "⚠️ emcc の版を読み取れなかった（$EMCC --version の形が変わった？）" >&2
elif [ "$EMCC_VER" != "$EMCC_WANT" ]; then
    echo "⚠️ emcc $EMCC_VER で作る。**実測は $EMCC_WANT で取った**ので、サイズも速度も再現しない" >&2
fi

# --- ソース -----------------------------------------------------------------
# ⚠️ **esp32/main/saan_kanji.c と saan_pcm.c はそのままリンクする。**
#    どちらも ESP-IDF の include を 1 本も持たない（実測）。**書き直さないこと** —
#    saan_pcm.h は「ここが唯一の実装」と明記していて、丸めを 2 か所に書くと
#    checksum の突き合わせが黙って無意味になる。
SRC="
$HERE/saan_web.c
$ROOT/csrc/saanotts.c
$ROOT/csrc/saanotts_stream.c
$ROOT/csrc/saanotts_int8.c
$ROOT/csrc/fft.c
$ROOT/csrc/g2p.c
$ROOT/csrc/jdict.c
$ROOT/csrc/accent.c
$ROOT/csrc/njd_rules.c
$ROOT/csrc/label_ids.c
$ROOT/esp32/main/saan_kanji.c
$ROOT/esp32/main/saan_pcm.c
"
# 取り込んだ Open JTalk（.c は 14 本。.h が 20 本ある）。
# ⚠️ **上流 + PATCHES と一致するかは `uv run python scripts/k1/k4b_vendor.py --check` が見る。**
for f in "$ROOT"/csrc/openjtalk/*.c; do SRC="$SRC $f"; done

# --- フラグ -----------------------------------------------------------------
# ⚠️ **`-std=c99` にしないこと。** `__STRICT_ANSI__` が立って `clock_gettime` /
#    `strdup` が隠れ、取り込んだ Open JTalk がリンクできない。`gnu17` を使う。
# ⚠️ **`-DCHARSET_UTF_8` が無いと openjtalk が `#error` で止まる**（SAAN_KANJI だけでは足りない）。
# ⚠️ **`-DK7_EXTERNAL_SCRATCH=1` を写さないこと。** 効かないマクロで、実体は
#    `LABEL_IDS_EXTERNAL_SCRATCH`。web はどちらも定義しない（wasm はメモリに余裕があるので
#    K-7 のトークン表を arena へ移す理由が無い。saan_web.c の静的検査がその前提で書いてある）。
CFLAGS="-O2 -std=gnu17 -Wall -Wextra -msimd128 -DCHARSET_UTF_8"
INC="-I $ROOT/csrc -I $ROOT/csrc/openjtalk -I $ROOT/esp32/main"

# ⚠️ **`-pthread` / `SharedArrayBuffer` は構造的に使えない。** GitHub Pages は
#    HTTP ヘッダを設定できないので COOP/COEP が張れない（計画 §5-13）。
# ⚠️ `-sFILESYSTEM=0`: blob は JS が fetch して HEAPU8 に置く。wasm 側は FS を触らない。
# ⚠️ `-sALLOW_MEMORY_GROWTH=1`: 辞書 13.7 MB + PCM で必ず伸びる。
#    **伸びると JS 側の HEAPF32 が detach する**ので、JS は毎回 Module から読み直すこと。
LDFLAGS="
-sMODULARIZE=1
-sEXPORT_ES6=1
-sENVIRONMENT=web
-sFILESYSTEM=0
-sALLOW_MEMORY_GROWTH=1
-sEXPORTED_RUNTIME_METHODS=HEAPU8,HEAPF32,HEAP32,UTF8ToString,stringToUTF8,lengthBytesUTF8
"
# ⚠️ スタックは既定（64 KB）より広く取る。`saan_irfft_1024` が自動変数だけで 4 KB 使い、
#    その上に Open JTalk の NJD / JPCommon が積む。
#    ⚠️ **必要量は測っていない**（wasm はスタック溢れを黙って隣のメモリに書く形では
#    壊れず trap するが、それでも「足りるか」は測っていない）。
LDFLAGS="$LDFLAGS -sSTACK_SIZE=4194304"

mkdir -p "$OUT"

build_lane() {
    lane="$1"; extra="$2"
    echo "--- $lane ($extra) ---"
    # shellcheck disable=SC2086
    "$EMCC" $CFLAGS $extra $INC $SRC $LDFLAGS -o "$OUT/saan_web_$lane.mjs"
}

# W8A32 = 重みだけ int8（fp32 blob も受ける）。**品質ゲートを通るレーン。**
build_lane w8a32 ""
# W8A8 = 活性化も int8。**実機の出荷構成そのもの**（D-048）。
# ⚠️ fp32 blob を渡すと saan_web_init() が拒否する（渡っても黙って動いてしまうため）。
build_lane w8a8 "-DSAAN_INT8_ACT=1"

echo
echo "--- 出来たもの（バイト）---"
for f in "$OUT"/saan_web_w8a32.mjs "$OUT"/saan_web_w8a32.wasm \
         "$OUT"/saan_web_w8a8.mjs  "$OUT"/saan_web_w8a8.wasm; do
    printf '%12d  %s\n' "$(wc -c < "$f")" "${f#"$ROOT"/}"
done
echo
echo "⚠️ node の値はブラウザの値ではない（C-055）。ブラウザの実測は M-95（Chrome 152）。"
echo "⚠️ 音は誰も聴いていない / モバイルと Safari は未測定。"
