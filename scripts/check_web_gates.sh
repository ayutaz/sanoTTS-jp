#!/usr/bin/env bash
# W トラック（ブラウザデモ）の受け入れゲート G-W1 〜 G-W7
#
#   bash scripts/check_web_gates.sh
#
# ⚠️ **ここで通るのは「node で確かめられること」だけ。**
#    node の wasm はブラウザの wasm ではない。**ブラウザでは 1 種類も測っていない**
#    （速度も音も。C-055 の「似た形のベンチは根拠にならない」がそのまま当てはまる）。
#    このスクリプトが答えるのは「wasm にした C コアが参照実装と同じ数を出すか」だけで、
#    出音・レイテンシ・モバイルは**すべて未検証**。
#
# ⚠️ **重みが無い環境では skip せずに落とす。** 「重みが無いので 0 件中 0 件一致 = OK」は
#    このリポジトリで実際に踏んだ形の空虚さ（C-028 / C-056）。落とす方を選ぶ。
#
# ゲートの中身:
#   G-W7  帰属ブロックが LICENSE-MODEL.md §3.1 と一字一句一致
#                                                          陽性対照: 1 行消すと落ちる
#   G-W1  wasm(fp32)         が golden.bin    と一致        陽性対照: 重みを壊すと落ちる
#   G-W2  wasm(int8 / W8A32) が golden_i8.bin と一致        陽性対照: 同上
#   G-W2b **ブラウザが通るストリーミング経路**が一括版と bit 一致
#                                                          陽性対照: stream_test の memcmp
#   G-W3  `-msimd128` の有無で PCM が bit 一致              陽性対照: 1 サンプルずらすと落ちる
#   G-W4  経路判定（saan_g2p_classify）と G2P が凍結ベクタと一致
#                                                          陽性対照: 手書き文字集合版が落ちる
#   G-W5  fp32 blob を W8A8 レーンに渡すと**拒否される**    陽性対照: 検査を外すと通ってしまう
#   G-W6  **`web/saan_web.c` を実際にビルドして走らせる**   陽性対照: 下記 3 つ
#
# ⚠️ **G-W1〜G-W5 は `web/build.sh` に依存しない**（`csrc/` を emcc で直接ビルドする）。
#    build.sh が壊れていてもコアの一致は測れるし、逆に build.sh が通っても
#    G-W1〜G-W5 が緑になるとは限らない（別のものを見ている）。
#    ⚠️ **G-W6 だけは build.sh を回す。** そうしないと出荷する `web/saan_web.c` が
#    どのゲートでもコンパイルすらされない（下記）。
#
# ⚠️ **G-W6 を足した理由（審査 2026-09-04）。** それまで `web/saan_web.c` は
#    **どのゲートでもコンパイルされていなかった**。`#error` を 1 行入れて
#    `bash scripts/check_web_gates.sh` を回すと **exit 0** で、しかも最後に
#    「OK web/saan_web.c が同じテンソル名で fp32 blob を検査している」まで出た
#    （**コンパイルできないファイルに grep が当たっていただけ**）。
#    arena の巻き戻し順序を逆にしても / `saan_pcm_reset()` を消しても / 350 の上限を
#    外しても、全ゲートが緑のまま Pages に出る状態だった。
#
# ⚠️ **G-W6 の陽性対照は 3 つとも実際に落ちることを確かめてある**:
#      1. `web/saan_web.c` に `#error` を入れる      → build.sh が落ちる
#      2. `saan_pcm_reset();` を消す                 → 統計窓つきの棒が落ちる
#         （**出荷 ABI からは 1 ビットも見えない**: 出る音は |max| 0.33 止まりで
#           クリップ 0 件 = 唯一の窓 `g_msg` のクリップ警告が一度も出ない。
#           だから統計を直接読む TU をゲートのときだけリンクする）
#      3. `LICENSE-MODEL.md` §3.1 から 1 行消す      → G-W7 が落ちる
set -u
cd "$(dirname "$0")/.."
ROOT="$PWD"
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

hdr() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
ok()  { printf '  OK  %s\n' "$1"; }
ng()  { printf '  NG! %s\n' "$1"; FAIL=1; }

PYTHON="${PYTHON:-uv run --no-project python}"
EMCC_WANT="6.0.9"           # ⚠️ 計画 §1 で凍結した版。数値はこれで測った

# ---------------------------------------------------------------- 0
# ⚠️ **依存ゼロなので前提の検査より先に回す。** 下の §1 は emcc / node / 重みが
#    無いと `exit 1` するので、後ろに置くと「重みが無い環境ではライセンスを 1 度も
#    検査しない」形になる。ここは md と html を読むだけで、何も要らない。
hdr "0. G-W7 帰属ブロックが LICENSE-MODEL.md §3.1 と一字一句一致（陽性対照つき）"

# ⚠️ **行番号で切り出さないこと。** `sed -n '68,89p'` と書くと、LICENSE-MODEL.md の
#    上に 1 行足された瞬間に**黙ってずれる**（ずれた先も 22 行あるので件数の検査も通る）。
#    見出し `### 3.1` を探し、その中の**最初のコードフェンス**の中身を取る。
LIC_MD="$TMP/lic_md.txt"
LIC_HTML="$TMP/lic_html.txt"
awk '/^### 3\.1 /{s=1;next} s&&/^### /{exit} s&&/^```/{if(b)exit;b=1;next} s&&b{print}' \
    LICENSE-MODEL.md > "$LIC_MD"
# `<pre class="verbatim">` … `</pre>` の中身（開き閉じと同じ行にある本文も拾う）
awk '/<pre class="verbatim">/{f=1;sub(/.*<pre class="verbatim">/,"")}
     f{if(index($0,"</pre>")){sub(/<\/pre>.*/,"");print;exit} print}' \
    web/index.html > "$LIC_HTML"

LIC_N="$(( $(wc -l < "$LIC_MD") ))"
HTML_N="$(( $(wc -l < "$LIC_HTML") ))"
# ⚠️ **空 == 空 で満点を取らせない。** 抽出が両方 0 行になると diff は一致する。
#    §3.1 のブロックは 22 行（実測）。20 行を下回ったら抽出そのものが壊れている。
if [ "$LIC_N" -lt 20 ] || [ "$HTML_N" -lt 20 ]; then
    ng "帰属ブロックの抽出が壊れている（LICENSE-MODEL.md $LIC_N 行 / web/index.html $HTML_N 行）"
    printf '      ⚠️ どちらかが 0 行だと diff は「一致」になる。**抽出の側を直すこと**\n'
# ⚠️ **HTML 実体参照を素通しで比べているので、`< > &` が入った瞬間に比較が嘘になる。**
#    今は 1 文字も無い（実測）。入ったらここで落として、エスケープを考えさせる。
elif grep -q '[<>&]' "$LIC_MD"; then
    ng "LICENSE-MODEL.md §3.1 に < > & が入った。**HTML では実体参照になるので素の比較が成立しない**"
else
    if diff -u "$LIC_MD" "$LIC_HTML" > "$TMP/lic_diff"; then
        ok "web/index.html の帰属ブロックが LICENSE-MODEL.md §3.1 と一致（$LIC_N 行）"
    else
        ng "web/index.html の帰属ブロックが LICENSE-MODEL.md §3.1 と違う（1 行でも欠けると再配布の条件に違反する）"
        sed 's/^/      /' "$TMP/lic_diff"
    fi
    # 陽性対照: 1 行消したら**必ず**落ちること。
    # ⚠️ 消すのは 4 行目（「つくよみちゃんコーパス」の行）。空行や末尾ではなく
    #    **本文の行**を消す（空行を消しても落ちるが、それでは「本文を見ている」と言えない）
    sed '4d' "$LIC_MD" > "$TMP/lic_broken"
    if diff -q "$TMP/lic_broken" "$LIC_HTML" > /dev/null 2>&1; then
        ng "陽性対照: 帰属ブロックから 1 行消しても一致した（G-W7 は空虚）"
    else
        ok "陽性対照: 帰属ブロックから 1 行（4 行目「$(sed -n '4p' "$LIC_MD")」）消すと落ちる"
    fi
fi

# ---------------------------------------------------------------- 1
hdr "1. 前提（emcc / node / 重み）"

# --- emcc。CI では setup-emsdk が PATH に置く。手元は emsdk の直叩き ---------
# ⚠️ **emcc は EM_CONFIG を要る。** PATH に無いときだけ ~/emsdk を見に行く
EMCC="${EMCC:-}"
if [ -z "$EMCC" ]; then
    if command -v emcc > /dev/null 2>&1; then
        EMCC="$(command -v emcc)"
    elif [ -x "$HOME/emsdk/upstream/emscripten/emcc" ]; then
        EMCC="$HOME/emsdk/upstream/emscripten/emcc"
        export EM_CONFIG="${EM_CONFIG:-$HOME/emsdk/.emscripten}"
    fi
fi
if [ -z "$EMCC" ] || ! "$EMCC" --version > "$TMP/emv" 2>&1; then
    ng "emcc が見つからない（PATH か EMCC= で渡すこと。手元は ~/emsdk/upstream/emscripten/emcc）"
    printf '\033[1mNG: 前提が揃っていないので 1 つもゲートを回していない。\033[0m\n'
    exit 1
fi
EMCC_VER="$(sed -n '1s/.*replacement + linker emulating GNU ld) \([0-9.]*\).*/\1/p' "$TMP/emv")"
if [ "$EMCC_VER" = "$EMCC_WANT" ]; then
    ok "emcc ${EMCC_VER}（計画 §1 で凍結した版）: $EMCC"
else
    # ⚠️ 落とさない。ここで見たいのは一致であって版ではない。**ただし版が違えば
    #    記録した数値（M-94）は再現しない**ので、黙って進めない
    printf '  ⚠️  emcc %s（凍結は %s）: %s\n' "${EMCC_VER:-不明}" "$EMCC_WANT" "$EMCC"
    printf '      **記録した数値は %s で測ったもの**。版が違うまま数値を docs に書かないこと\n' "$EMCC_WANT"
fi

if command -v node > /dev/null 2>&1; then
    ok "node $(node --version)"
else
    ng "node が無い（wasm を回せない）"
    printf '\033[1mNG: 前提が揃っていないので 1 つもゲートを回していない。\033[0m\n'
    exit 1
fi

# --- 重み。⚠️ **無ければ落とす**（skip しない）------------------------------
NEED_BIN="csrc/student.bin csrc/student_i8.bin csrc/golden.bin csrc/golden_i8.bin"
MISSING=""
for f in $NEED_BIN; do [ -f "$f" ] || MISSING="$MISSING $f"; done
if [ -n "$MISSING" ]; then
    ng "重み・参照出力が無い:$MISSING"
    cat <<'MSG'
      ⚠️ **これは skip ではない。** ここが無いと G-W1 / G-W2 / G-W3 / G-W5 は
         「0 件中 0 件一致 = OK」になってしまうので、落とす方を選んでいる。
      リリース v0.3.0 から落として名前を合わせること:
         gh release download v0.3.0 -R <owner>/<repo> \
           --pattern 'saanotts-jp-v3-fp32.bin' --pattern 'golden-v3-fp32.bin' \
           --pattern 'saanotts-jp-v3-int8.bin' --pattern 'golden-v3-int8.bin' \
           --dir csrc --clobber
         mv csrc/saanotts-jp-v3-fp32.bin csrc/student.bin
         mv csrc/golden-v3-fp32.bin      csrc/golden.bin
         mv csrc/saanotts-jp-v3-int8.bin csrc/student_i8.bin
         mv csrc/golden-v3-int8.bin      csrc/golden_i8.bin
MSG
    printf '\033[1mNG: 重みが無いのでゲートを回していない。\033[0m\n'
    exit 1
fi
for f in $NEED_BIN; do
    printf '  OK  %-22s %s B\n' "$f" "$(( $(wc -c < "$f") ))"
done

# --- arena と ids の上限は **ソースから取る**（写さない）---------------------
# ⚠️ 定数を手で写すと、esp32 側を変えたときにここだけ古くなる。
#    `a.used` の期待値はさらに危険で、**ポインタ幅で変わる**ので棒の側で
#    `saan_stream_arena_used(n_ids)` を呼ぶ（下の pcm_probe.c）。
ARENA_EXPR="$(cc -E -dM -I esp32/host_stub -I esp32/main -I csrc esp32/main/main.c 2>/dev/null \
             | sed -n 's/^#define SAAN_ARENA_BYTES //p')"
MAXIDS_EXPR="$(cc -E -dM -I esp32/host_stub -I esp32/main -I csrc esp32/main/main.c 2>/dev/null \
              | sed -n 's/^#define SAAN_MAX_IDS //p')"
if [ -n "$ARENA_EXPR" ] && [ -n "$MAXIDS_EXPR" ]; then
    # ⚠️ **-D に式のまま渡さない。** `(176 * 1024)` は空白で 3 語に割れて
    #    emcc が `1024)` を入力ファイル扱いする（実際に踏んだ）。数に潰してから渡す
    ARENA_B="$(( ARENA_EXPR ))"
    MAXIDS_N="$(( MAXIDS_EXPR ))"
    ok "esp32/main/main.c から arena $ARENA_B B / SAAN_MAX_IDS $MAXIDS_N"
else
    ng "SAAN_ARENA_BYTES / SAAN_MAX_IDS を esp32/main/main.c から取れない"
    printf '\033[1mNG: 前提が揃っていない。\033[0m\n'
    exit 1
fi

CORE="csrc/saanotts.c csrc/saanotts_stream.c csrc/fft.c csrc/saanotts_int8.c"
# ⚠️ `-std=gnu17`。**c99 は `__STRICT_ANSI__` で newlib の M_PI / clock_gettime を隠す**
#    （check_esp32_template.sh §1 と同じ理由）。
# ⚠️ `-sNODERAWFS=1` で fopen がそのまま実ファイルに通る（棒が argv を素直に使える）。
# ⚠️ `-sALLOW_MEMORY_GROWTH=1` は **golden_test の一括版が 53 ids で 38.66 MB 取る**ため。
#    ブラウザに載せるのはストリーミング版なので、これは棒だけの都合。
EMFLAGS="-std=gnu17 -O2 -Wall -Wextra -Werror -sNODERAWFS=1 -sALLOW_MEMORY_GROWTH=1 -sENVIRONMENT=node -I csrc"

# 陽性対照の作り方（G-W1 / G-W2 共通）。
# ⚠️ **`ci.yml` の旧版 `dd ... seek=1200000` を写さないこと。** int8 blob は 654,032 B
#    しかないので seek が末尾を越え、dd は**ファイルを伸ばすだけ**で重みは 1 バイトも
#    壊れず PASS する。⚠️ 「どこか 4 バイト叩く」でも足りない: blob v2 は int8 conv 重みを
#    `align16(cin)` に 0 埋めしてあり、padding を叩くと出力が 1 bit も動かない。
#    → **真ん中 64 KB を 0x7f で塗り、大きさが変わっていないことも検査する。**
break_blob() {   # $1 = 元 blob / $2 = 壊した先
    local sz; sz="$(( $(wc -c < "$1") ))"
    cp "$1" "$2" || return 1
    head -c 65536 /dev/zero | tr '\0' '\177' \
        | dd of="$2" bs=1 seek="$(( sz / 2 ))" conv=notrunc 2> /dev/null
    [ "$(( $(wc -c < "$2") ))" -eq "$sz" ]     # 伸びていたら壊し方が間違っている
}

# ---------------------------------------------------------------- 2
hdr "2. G-W1 wasm(fp32) が golden.bin と一致（陽性対照つき）"
if $EMCC $EMFLAGS -o "$TMP/golden_test.js" csrc/golden_test.c $CORE -lm 2> "$TMP/b1"; then
    ok "golden_test を emcc でビルドできた（0 warning / 0 error）"
    if ( cd csrc && node "$TMP/golden_test.js" student.bin golden.bin > "$TMP/g1" 2>&1 ); then
        ok "wasm(fp32) が golden.bin と一致"
        grep -E '^\s+(OK|NG!)' "$TMP/g1" | sed 's/^/      /'
    else
        ng "wasm(fp32) が golden.bin と一致しない"; sed 's/^/      /' "$TMP/g1"
    fi
    # 陽性対照
    if break_blob csrc/student.bin "$TMP/broken_fp32.bin"; then
        if ( cd csrc && node "$TMP/golden_test.js" "$TMP/broken_fp32.bin" golden.bin > "$TMP/g1n" 2>&1 ); then
            ng "陽性対照: 壊した fp32 blob でゴールデンテストが通った（G-W1 は空虚）"
        else
            ok "陽性対照: 真ん中 64 KB を潰すと落ちる（大きさは $(( $(wc -c < csrc/student.bin) )) B のまま）"
        fi
    else
        ng "陽性対照: blob を壊せなかった（dd がファイルを伸ばした可能性）"
    fi
else
    ng "golden_test の emcc ビルドが通らない"; sed 's/^/      /' "$TMP/b1"
fi

# ---------------------------------------------------------------- 3
hdr "3. G-W2 wasm(int8 / W8A32) が golden_i8.bin と一致（陽性対照つき）"
# ⚠️ **同じバイナリで回す。** レーンの切り替えは blob の dtype であって、
#    W8A32 は `-DSAAN_INT8_ACT` を**付けない**構成そのもの（活性化は fp32）。
if [ -f "$TMP/golden_test.js" ]; then
    if ( cd csrc && node "$TMP/golden_test.js" student_i8.bin golden_i8.bin > "$TMP/g2" 2>&1 ); then
        ok "wasm(int8 / W8A32) が golden_i8.bin（fake-quant 参照）と一致"
        grep -E '^\s+(OK|NG!)' "$TMP/g2" | sed 's/^/      /'
    else
        ng "wasm(int8 / W8A32) が golden_i8.bin と一致しない"; sed 's/^/      /' "$TMP/g2"
    fi
    if break_blob csrc/student_i8.bin "$TMP/broken_i8.bin"; then
        if ( cd csrc && node "$TMP/golden_test.js" "$TMP/broken_i8.bin" golden_i8.bin > "$TMP/g2n" 2>&1 ); then
            ng "陽性対照: 壊した int8 blob でゴールデンテストが通った（G-W2 は空虚）"
        else
            ok "陽性対照: 真ん中 64 KB を潰すと落ちる（大きさは $(( $(wc -c < csrc/student_i8.bin) )) B のまま）"
        fi
    else
        ng "陽性対照: blob を壊せなかった（dd がファイルを伸ばした可能性）"
    fi
else
    ng "G-W1 のビルドが無いので G-W2 を回せない"
fi

# ---------------------------------------------------------------- 3b
hdr "3b. G-W2b ブラウザが通るストリーミング経路が一括版と bit 一致"

# ⚠️ **G-W1 / G-W2 はブラウザが通らない経路を見ている**（審査 2026-09-04）。
#    `csrc/golden_test.c` が呼ぶのは**一括版** `saan_synthesize`。
#    `web/saan_web.c` が使うのは `saan_stream_init` / `saan_stream_pull`（**ストリーミング版**）。
#    G-W1 / G-W2 が緑でも、ブラウザが通る経路は 1 行も検査されていなかった。
# ⚠️ **陽性対照は `csrc/stream_test.c` 自身が持っている**（G2 は SNR ではなく `memcmp`。
#    c-line の段別切り分けも出る）。ここで新しく書き足さないのは、
#    「同じカーネルを 2 回書かない」と同じ理由で**同じ判定を 2 か所に書かない**ため。
# ⚠️ **`--g1-kb 176` は W8A8 だけ**（`csrc/Makefile` の `stream` と同じ渡し方）。
#    M-55 の activation 作業領域で 200 KB を超えるのが分かっているレーンなので、
#    実機の静的 arena（`esp32/main/main.c` の 176 KB）を上限にする。
#    ⚠️ 既定の 200 KB を全レーンで緩めない（fp32 / W8A32 を黙って甘くしないため）。
if $EMCC $EMFLAGS -o "$TMP/stream_test.js" csrc/stream_test.c $CORE -lm 2> "$TMP/b2b" \
   && $EMCC $EMFLAGS -DSAAN_INT8_ACT=1 -o "$TMP/stream_test_a8.js" csrc/stream_test.c $CORE -lm 2>> "$TMP/b2b"; then
    # held-out 24 文（G2 多文）は `csrc/ids_heldout.bin` が要る。**コーパス由来で
    # git にもリリースにも無い**ので CI では 1 文しか回らない。
    # ⚠️ **その線引きを黙らせない。** 「通った」が何を測ったのか分からなくなる
    if [ -f csrc/ids_heldout.bin ]; then
        HELD="ids_heldout.bin"
        printf '      **多文レーンあり**: csrc/ids_heldout.bin（%s B）が在るので held-out 24 文も回す\n' \
               "$(( $(wc -c < csrc/ids_heldout.bin) ))"
    else
        HELD=""
        printf '      ⚠️ **多文レーンは回していない**: csrc/ids_heldout.bin が無い\n'
        printf '         （コーパス由来で **git にもリリースにも無い**。CI では構造的にここまで）。\n'
        printf '         回るのは golden の 1 文だけ = **残差 %% SAAN_CHUNK が 1 通りしか出ない**。\n'
        printf '         obuf を縮めた壊れ方は 1 文では捕まらない（csrc/stream_test.c の注記）\n'
    fi
    # レーン 3 本。⚠️ Makefile の `stream` と同じ引数の並びにする（片方だけ変えない）
    run_stream() {   # $1 = 表示名 / $2 = 棒 / $3.. = 引数
        local label="$1" js="$2"; shift 2
        if ( cd csrc && node "$js" "$@" > "$TMP/s_$label" 2>&1 ); then
            ok "$label: ストリーミング版が一括版と bit 一致（$(grep -c '^  OK' "$TMP/s_$label") 項目通過）"
            grep -E '^  OK  G[123]' "$TMP/s_$label" | sed 's/^/    /'
        else
            ng "$label: stream_test が落ちた"; sed 's/^/      /' "$TMP/s_$label"
        fi
    }
    # shellcheck disable=SC2086
    run_stream "fp32"  "$TMP/stream_test.js"    student.bin    golden.bin $HELD
    # shellcheck disable=SC2086
    run_stream "W8A32" "$TMP/stream_test.js"    student_i8.bin golden.bin $HELD
    # shellcheck disable=SC2086
    run_stream "W8A8"  "$TMP/stream_test_a8.js" --g1-kb 176 student_i8.bin golden.bin $HELD
else
    ng "stream_test の emcc ビルドが通らない"; sed 's/^/      /' "$TMP/b2b"
fi

# ---------------------------------------------------------------- 4
hdr "4. G-W3 -msimd128 の有無で PCM が bit 一致（陽性対照つき）"
# 棒を書き出す。**リポジトリには置かない**（ゲートの中でしか使わない）
cat > "$TMP/pcm_probe.c" <<'PROBE'
/* check_web_gates.sh が書き出す棒。golden の in.ids をストリーミング版で合成し、
 * 生の float PCM を出す。
 *
 * ⚠️ **一括版 saan_synthesize は使わない**（53 ids で 38.66 MB 取る）。
 *    ブラウザに載せるのもストリーミング版なので、測る対象もそちらに揃える。
 * ⚠️ **arena は静的配列 + _Alignas(16)。** emcc の malloc は 8 B 境界しか返さない
 *    （実測 6/6 で mod16 == 8）。16 B 前提のテンソルが黙ってずれる。
 * ⚠️ **`saan_stream_pull` の n_out はフレーム数。サンプル数は n * SAAN_HOP。**
 *    ここを取り違えると **106 サンプルだけ**比べて「bit 一致」と言えてしまう
 *    （このゲートを書く途中で実際に踏んだ: 27,136 のはずが 106 だった）。
 * ⚠️ a.used は定数と比べない。`saan_stream_arena_used(n_ids)` をその発話の n_ids で呼ぶ
 *    （sizeof(impl) がポインタ幅で変わる）。
 */
#include "saanotts.h"
#include "saanotts_stream.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#if !defined(GW_ARENA_BYTES) || !defined(GW_MAX_IDS)
#error "GW_ARENA_BYTES / GW_MAX_IDS を -D で渡すこと（esp32/main/main.c から取る）"
#endif

static _Alignas(16) unsigned char g_arena[GW_ARENA_BYTES];

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(2);
    }
    fclose(f);
    *size = (size_t)n;
    return b;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s blob golden out.raw\n", argv[0]); return 2; }

    size_t wsz = 0, gsz = 0;
    void *wbuf = slurp(argv[1], &wsz), *gbuf = slurp(argv[2], &gsz);
    saan_weights W, G;
    if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) { fprintf(stderr, "blob 不正\n"); return 2; }
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) { fprintf(stderr, "golden 不正\n"); return 2; }

#if GW_FP32_GUARD
    /* ⚠️ **esp32/main/main.c と同じ検査**（G-W5）。W8A8 でビルドしても blob が fp32 なら
     *    `saan_conv1d_w` が f32 経路に落ちるだけで**黙って動く** = 速度が変わらない理由が
     *    分からない、という最悪の壊れ方をする。int8 blob だけが `<name>.scale` を持つ。 */
    {
        uint32_t dt0 = 0, d0[4] = {0};
        uint64_t nb0 = 0;
        if (!saan_tensor(&W, "duration.blocks.0.c1.weight.scale", &dt0, d0, &nb0)) {
            fprintf(stderr, "拒否: W8A8 レーンに fp32 blob が渡された\n");
            return 3;
        }
    }
#endif

    uint32_t dt = 0;
    uint64_t nb = 0;
    const void *p = saan_tensor(&G, "in.ids", &dt, NULL, &nb);
    if (!p || dt != 0u) { fprintf(stderr, "golden に in.ids が無い\n"); return 2; }
    const int n_ids = (int)(nb / sizeof(float));
    /* ⚠️ **main.c と同じ順序で上限を自分で拒否する。**
     *    saan_kanji_to_ids も saan_stream_init も 350 を強制しない */
    if (n_ids > GW_MAX_IDS) { fprintf(stderr, "ids が多すぎる: %d\n", n_ids); return 2; }
    int32_t *ids = (int32_t *)malloc(sizeof(int32_t) * (size_t)n_ids);
    if (!ids) return 2;
    for (int i = 0; i < n_ids; ++i) ids[i] = (int32_t)((const float *)p)[i];

    saan_arena A;
    saan_arena_init(&A, g_arena, sizeof g_arena);
    saan_stream st;
    saan_status s = saan_stream_init(&st, &W, &A, ids, n_ids, SAAN_S_V);
    if (s != SAAN_OK) { fprintf(stderr, "init: %s\n", saan_strerror(s)); return 2; }
    const size_t want = saan_stream_arena_used(n_ids);
    if (A.used != want) {
        fprintf(stderr, "a.used %zu != saan_stream_arena_used(%d) %zu — 確保が黙って抜けている\n",
                A.used, n_ids, want);
        return 2;
    }

    FILE *out = fopen(argv[3], "wb");
    if (!out) { fprintf(stderr, "書けない: %s\n", argv[3]); return 2; }
    static float pcm[SAAN_CHUNK * SAAN_HOP];
    long total = 0;
    uint64_t h = 0xcbf29ce484222325ull;              /* FNV-1a 64 */
    for (;;) {
        int32_t n = 0;                               /* ← **フレーム数** */
        s = saan_stream_pull(&st, pcm, &n);
        if (s != SAAN_OK) { fprintf(stderr, "pull: %s\n", saan_strerror(s)); return 2; }
        if (n <= 0) break;
        const size_t ns = (size_t)n * SAAN_HOP;
        fwrite(pcm, sizeof(float), ns, out);
        const unsigned char *b = (const unsigned char *)pcm;
        for (size_t i = 0; i < ns * sizeof(float); ++i) { h ^= b[i]; h *= 0x100000001b3ull; }
        total += (long)ns;
    }
    fclose(out);

    printf("n_ids=%d frames=%d samples=%ld a.used=%zu checksum=0x%016llx lane=%s simd=%s\n",
           n_ids, st.n_frames, total, A.used, (unsigned long long)h,
#if SAAN_INT8_ACT
           "W8A8",
#else
           "W8A32/fp32",
#endif
#ifdef __wasm_simd128__
           "on"
#else
           "off"
#endif
    );
    free(wbuf); free(gbuf); free(ids);
    return 0;
}
PROBE

PROBE_D="-DGW_ARENA_BYTES=$ARENA_B -DGW_MAX_IDS=$MAXIDS_N -DGW_FP32_GUARD=0"
build_probe() {   # $1 = 出力 / $2… = 追加フラグ
    local out="$1"; shift
    $EMCC $EMFLAGS $PROBE_D "$@" -o "$out" "$TMP/pcm_probe.c" $CORE -lm 2> "$TMP/pb"
}
run_probe() {     # $1 = 棒 / $2 = 出力 raw
    ( cd csrc && node "$1" student_i8.bin golden_i8.bin "$2" )
}

G3_OK=1
for lane in a32 a8; do
    case "$lane" in
        a32) act=""                   ; label="W8A32" ;;
        a8)  act="-DSAAN_INT8_ACT=1"  ; label="W8A8"  ;;
    esac
    if ! build_probe "$TMP/p_$lane.js" $act; then
        ng "$label 棒（simd 無し）のビルドが通らない"; sed 's/^/      /' "$TMP/pb"; G3_OK=0; continue
    fi
    if ! build_probe "$TMP/p_${lane}_simd.js" $act -msimd128; then
        ng "$label 棒（-msimd128）のビルドが通らない"; sed 's/^/      /' "$TMP/pb"; G3_OK=0; continue
    fi
    l1="$(run_probe "$TMP/p_$lane.js"      "$TMP/$lane.raw")"      || { ng "${label}（simd 無し）が走らない"; G3_OK=0; continue; }
    l2="$(run_probe "$TMP/p_${lane}_simd.js" "$TMP/${lane}_simd.raw")" || { ng "${label}（-msimd128）が走らない"; G3_OK=0; continue; }
    printf '      %s\n      %s\n' "$l1" "$l2"
    # ⚠️ **空ファイル同士の一致で満点を取らせない。** 53 ids = 106 frames = 27,136 sample
    #    → 27,136 × 4 B = 108,544 B。ここが違ったら比較の前に落とす
    sz="$(( $(wc -c < "$TMP/$lane.raw") ))"
    if [ "$sz" -ne 108544 ]; then
        ng "$label の PCM が 108,544 B でない（$sz B）— 53 ids / 27,136 sample のはず"; G3_OK=0; continue
    fi
    if cmp -s "$TMP/$lane.raw" "$TMP/${lane}_simd.raw"; then
        ok "$label: -msimd128 の有無で PCM が bit 一致（$sz B / 27,136 sample）"
    else
        ng "$label: -msimd128 で PCM が変わった"; G3_OK=0
    fi
done

# 陽性対照: 1 サンプル（4 B）ずらした列と比べると**必ず落ちる**こと。
# ⚠️ 長さも揃える。長さ違いで differ になったのでは「ずらしを検出した」と言えない
if [ "$G3_OK" = "1" ] && [ -f "$TMP/a32.raw" ]; then
    tail -c +5 "$TMP/a32.raw" > "$TMP/shift.raw"
    head -c "$(( $(wc -c < "$TMP/shift.raw") ))" "$TMP/a32.raw" > "$TMP/head.raw"
    if cmp -s "$TMP/shift.raw" "$TMP/head.raw"; then
        ng "陽性対照: 1 サンプルずらしても一致した（cmp が効いていない）"
    else
        ok "陽性対照: 1 サンプル（4 B）ずらすと同じ長さでも一致しない"
    fi
else
    ng "陽性対照を回せない（G-W3 の本体が落ちている）"
fi

# ---------------------------------------------------------------- 5
hdr "5. G-W4 経路判定と G2P が凍結ベクタと一致（陽性対照つき）"
# ⚠️ **JS に経路判定を書かない**という決定（計画 §2）を守れているかは、ここでは測れない。
#    ここが測るのは「wasm にした saan_g2p_classify / saan_g2p が端末と同じ答えを出すか」。
if [ ! -f csrc/g2p_vectors.bin ]; then
    printf '  --  csrc/g2p_vectors.bin が無いので生成する（%s）\n' "$PYTHON"
    if ! $PYTHON scripts/gen_g2p_vectors.py --closed \
            --out csrc/g2p_vectors.bin --manifest csrc/g2p_vectors.json > "$TMP/gen" 2>&1; then
        ng "g2p_vectors.bin を生成できない"; sed 's/^/      /' "$TMP/gen"
    fi
fi
if [ -f csrc/g2p_vectors.bin ]; then
    if $EMCC $EMFLAGS -o "$TMP/g2p_test.js" csrc/g2p_test.c csrc/g2p.c 2> "$TMP/b4"; then
        if ( cd csrc && node "$TMP/g2p_test.js" g2p_vectors.bin --min-vectors 2700 > "$TMP/g4" 2>&1 ); then
            ok "wasm の saan_g2p が凍結ベクタ $(sed -n '1s/.*: \([0-9]*\) 件.*/\1/p' "$TMP/g4") 件と一致"
            grep -E '陰性対照|SHA-256|G7' "$TMP/g4" | sed 's/^/      /' | head -6
        else
            ng "wasm の G2P がベクタと一致しない"; tail -30 "$TMP/g4" | sed 's/^/      /'
        fi
    else
        ng "g2p_test の emcc ビルドが通らない"; sed 's/^/      /' "$TMP/b4"
    fi
else
    ng "csrc/g2p_vectors.bin が無い（G-W4 の G2P 側を回していない）"
fi
# 経路判定（K-B / G11）。**陽性対照は line_test 自身が持っている**（naive_classify）
if $EMCC $EMFLAGS -o "$TMP/line_test.js" csrc/line_test.c csrc/line.c csrc/g2p.c 2> "$TMP/b4b"; then
    if ( cd csrc && node "$TMP/line_test.js" > "$TMP/g4b" 2>&1 ); then
        ok "wasm の saan_g2p_classify（かな / 辞書 / 拒否）が全項目通過"
        grep -E '^OK  (csrc/line.c|saan_g2p_classify)' "$TMP/g4b" | sed 's/^/      /'
    else
        ng "wasm の経路判定 / 行編集が落ちた"; tail -30 "$TMP/g4b" | sed 's/^/      /'
    fi
else
    ng "line_test の emcc ビルドが通らない"; sed 's/^/      /' "$TMP/b4b"
fi

# ---------------------------------------------------------------- 6
hdr "6. G-W5 fp32 blob を W8A8 レーンに渡すと拒否される（陽性対照つき）"
# 検査ありの棒
if $EMCC $EMFLAGS -DGW_ARENA_BYTES="$ARENA_B" -DGW_MAX_IDS="$MAXIDS_N" \
        -DGW_FP32_GUARD=1 -DSAAN_INT8_ACT=1 \
        -o "$TMP/p_guard.js" "$TMP/pcm_probe.c" $CORE -lm 2> "$TMP/b5"; then
    ( cd csrc && node "$TMP/p_guard.js" student.bin golden.bin "$TMP/x.raw" > "$TMP/g5" 2>&1 )
    rc=$?
    if [ "$rc" = "3" ] && grep -q "拒否" "$TMP/g5"; then
        ok "W8A8 レーンに fp32 blob を渡すと拒否される（exit 3 / $(tr -d '\n' < "$TMP/g5")）"
    else
        ng "W8A8 レーンが fp32 blob を受け入れた（exit $rc）"; sed 's/^/      /' "$TMP/g5"
    fi
    # ⚠️ **「全部拒否する」実装で満点を取らせない。** int8 blob は通ること
    if ( cd csrc && node "$TMP/p_guard.js" student_i8.bin golden_i8.bin "$TMP/y.raw" > "$TMP/g5b" 2>&1 ); then
        ok "同じ棒に int8 blob を渡すと通る（allow 側: $(tr -d '\n' < "$TMP/g5b")）"
    else
        ng "int8 blob まで拒否している（検査が広すぎる）"; sed 's/^/      /' "$TMP/g5b"
    fi
else
    ng "検査ありの棒がビルドできない"; sed 's/^/      /' "$TMP/b5"
fi
# 陽性対照: 検査を外すと **fp32 blob が黙って通ってしまう**こと
if [ -f "$TMP/p_a8.js" ]; then
    if ( cd csrc && node "$TMP/p_a8.js" student.bin golden.bin "$TMP/z.raw" > "$TMP/g5c" 2>&1 ); then
        ok "陽性対照: 検査を外すと fp32 blob が W8A8 レーンで黙って通る（= 検査が効いている証拠）"
    else
        ng "陽性対照: 検査を外しても通らない — 落ちている理由が別にある（G-W5 は空虚）"
        sed 's/^/      /' "$TMP/g5c"
    fi
else
    ng "陽性対照の棒（G-W3 の W8A8）が無い"
fi
# web/saan_web.c が同じテンソル名を書いているか（**grep。実行ではない**）
# ⚠️ **これ単独では何の証拠にもならない。** かつてここは「コンパイルできないファイルに
#    grep が当たっているだけ」で OK を出していた（`#error` を入れても緑のまま）。
#    **出荷バイナリで実際に拒否されるか**は G-W6 が見る。ここに残してあるのは、
#    ずれたときに「どちらの側がずれたか」が 1 行で分かるから。
if [ -f web/saan_web.c ]; then
    if grep -q 'duration\.blocks\.0\.c1\.weight\.scale' web/saan_web.c; then
        ok "web/saan_web.c が同じテンソル名を書いている（grep。実際に拒否するかは G-W6）"
    else
        ng "web/saan_web.c に fp32 blob の検査が無い（main.c と同じ検査を入れること）"
    fi
else
    ng "web/saan_web.c が無い（G-W6 が回せない）"
fi

# ---------------------------------------------------------------- 7
hdr "7. G-W6 web/saan_web.c を実際にビルドして走らせる（陽性対照つき）"

# ⚠️ **ここまでのゲートは web/saan_web.c を 1 度もコンパイルしていない。**
#    `#error` を入れても G-W1〜G-W5 は全部 OK で exit 0 だった（審査 2026-09-04）。
#    出荷するのは `web/build.sh` が作る `web/dist/*.mjs` なので、**それを建てて叩く**。
#
# ⚠️ **node 向けに組み直さない。** `-sENVIRONMENT=web` の module は `.wasm` を fetch で
#    取りに行くので node からは読めないが、公式フック `instantiateWasm` で直に渡せば
#    **出荷するバイナリそのもの**を検査できる。組み直すと「別のものが通った」になる。
#
# ⚠️ **`web/dist/` を上書きする。** git 管理外（`.gitignore`）で、Pages は
#    `.github/workflows/pages.yml` が毎回作り直すので実害は無い。
#    ⚠️ 逆に、ここで作り直さないと**手元に残った古い wasm を検査して緑になる**。

GW6_BUILD="$TMP/build_sh.log"
CAPDIR="$TMP/emcc_argv"
mkdir -p "$CAPDIR"

# ⚠️ **ソースの一覧を写さない。** build.sh の `SRC` をここにコピーすると、向こうが
#    1 本足したときにこちらだけ古くなる。emcc を包んで **build.sh が実際に渡した argv**
#    を記録し、その argv をそのまま使い回す（下の統計窓つきの棒）。
#    ⚠️ 包みが見つからなければ**落ちる**（黙って別のフラグで建て直さない）。
cat > "$TMP/emcc_capture.sh" <<'CAP'
#!/bin/sh
i=0
while [ -e "$SAAN_CAPDIR/argv.$i" ]; do i=$((i+1)); done
printf '%s\n' "$@" > "$SAAN_CAPDIR/argv.$i"
exec "$SAAN_REAL_EMCC" "$@"
CAP
chmod +x "$TMP/emcc_capture.sh"

# ⚠️ **`SAAN_REAL_EMCC="$EMCC" EMCC=包み` と 1 行に並べて書かない。** 同じ前置きの中で
#    `$EMCC` が古い値を指すか新しい値を指すかは読み手に分からず（shellcheck も SC2097/SC2098 で
#    警告する）、**包みが自分自身を exec して無限再帰する**形が「動いているように見える」まで
#    気づけない。先に別名へ写してから包みを被せる。
GW6_REAL_EMCC="$EMCC"
export SAAN_CAPDIR="$CAPDIR" SAAN_REAL_EMCC="$GW6_REAL_EMCC"
if EMCC="$TMP/emcc_capture.sh" bash web/build.sh > "$GW6_BUILD" 2>&1; then
    ok "web/build.sh が通った（= web/saan_web.c が実際にコンパイルされた）"
    grep -E '^ +[0-9]+ +web/dist/' "$GW6_BUILD" | sed 's/^/    /'
    GW6_BUILT=1
else
    ng "web/build.sh が落ちた（web/saan_web.c がコンパイルできない）"
    tail -25 "$GW6_BUILD" | sed 's/^/      /'
    GW6_BUILT=0
fi

if [ "$GW6_BUILT" = "1" ]; then
    # --- 統計窓つきの棒 ---------------------------------------------------
    # ⚠️ **`saan_pcm_reset()` を消しても出荷 ABI からは 1 ビットも見えない。**
    #    `web/saan_web.c` が統計を出す口は `g_msg` のクリップ警告だけで、
    #    それは `saan_pcm_clip_count() > 0` のときにしか出ない。実測すると出る音は
    #    |max| 0.33 止まり（10 通りの入力で最大 0.3325）＝**クリップは 1 件も起きない**
    #    ので、その窓は永久に開かない。→ ゲートのときだけ統計を読む TU を足す。
    # ⚠️ **これはリポジトリに置かない**（出荷物に無い口を増やさない）。
    cat > "$TMP/gw_pcm_probe.c" <<'PROBE2'
/* check_web_gates.sh が G-W6 のときだけリンクする「統計窓」。出荷物には入らない。
 * saan_pcm.c の統計（FNV-1a checksum / sample 数 / |max|）を JS から読めるようにする。 */
#include <emscripten/emscripten.h>
#include <stdint.h>
#include "saan_pcm.h"

/* ⚠️ 64 bit をそのまま返さない（BigInt には -sWASM_BIGINT が要る）。上下 32 bit に割る */
EMSCRIPTEN_KEEPALIVE int gw_pcm_sum_lo(void)  { return (int)(uint32_t)(saan_pcm_checksum() & 0xffffffffu); }
EMSCRIPTEN_KEEPALIVE int gw_pcm_sum_hi(void)  { return (int)(uint32_t)(saan_pcm_checksum() >> 32); }
EMSCRIPTEN_KEEPALIVE int gw_pcm_samples(void) { return (int)saan_pcm_samples(); }
EMSCRIPTEN_KEEPALIVE int gw_pcm_absmax(void)  { return (int)saan_pcm_absmax(); }
PROBE2

    CAPFILE="$(grep -l 'saan_web_w8a32\.mjs' "$CAPDIR"/argv.* 2>/dev/null | head -1)"
    GW6_PROBE=0
    if [ -z "$CAPFILE" ]; then
        ng "build.sh が emcc に渡した argv を捕まえられなかった（呼び出しの形が変わった？）"
        printf '      ⚠️ **ソース一覧を写して代用しない。** 写すと build.sh とずれる\n'
    else
        GW_ARGS=()
        while IFS= read -r gw_a; do GW_ARGS+=("$gw_a"); done < "$CAPFILE"
        GW_NEW=(); gw_skip=0
        for gw_a in "${GW_ARGS[@]}"; do
            if [ "$gw_skip" = 1 ]; then gw_skip=0; continue; fi
            if [ "$gw_a" = "-o" ]; then gw_skip=1; continue; fi
            GW_NEW+=("$gw_a")
        done
        if [ "${#GW_NEW[@]}" -lt 5 ]; then
            ng "捕まえた argv が短すぎる（${#GW_NEW[@]} 語）— 別の呼び出しを掴んでいる"
        elif "$EMCC" "${GW_NEW[@]}" "$TMP/gw_pcm_probe.c" -o "$TMP/gw_probe.mjs" \
                 > "$TMP/gw_probe.log" 2>&1; then
            ok "統計窓つきの棒を **build.sh と同じ ${#GW_NEW[@]} 語のフラグ**で建てた（一覧は写していない）"
            GW6_PROBE=1
        else
            ng "統計窓つきの棒がビルドできない"; tail -20 "$TMP/gw_probe.log" | sed 's/^/      /'
        fi
    fi

    if [ "$GW6_PROBE" = "1" ]; then
        # --- 棒（node 側）。**リポジトリには置かない** ---------------------
        cat > "$TMP/gate_web.mjs" <<'HARNESS'
/* check_web_gates.sh が書き出す G-W6 の棒。**リポジトリには置かない。**
 * argv: distDir probeMjs probeWasm modelI8 modelFp32 dictOrDash */
import { readFileSync, existsSync } from 'node:fs';

const [distDir, probeMjs, probeWasm, modelI8Path, modelFp32Path, dictArg] = process.argv.slice(2);
const modelI8 = readFileSync(modelI8Path);
const modelFp32 = readFileSync(modelFp32Path);
const haveDict = dictArg !== '-' && existsSync(dictArg);
const dict = haveDict ? readFileSync(dictArg) : null;

/* 錨（M-62 / M-90 と同じ 1 文）。かなで書いても漢字で書いても同じ PCM が出るはず */
const KANA = 'きょ][おわよ][いて][んきです°ね';
const KANJI = '今日は良い天気ですね。';
const OTHER = 'あした][はあめ';
const MIXED = 'きょ][おわよ][いて][んきです°ね。';   /* 中間表現 + 句点 = 拒否されるべき */
/* ⚠️ 297 B で 461 ids。**かな経路では 350 ids に届かない**（512 B の行上限で最大 343 ids。
 *    実測）ので、ids 上限の拒否は**辞書経路でしか踏めない** */
const LONG_KANJI = '今日は良い天気ですね。'.repeat(9);

let fail = 0;
const ok = (m) => console.log(`  OK  ${m}`);
const ng = (m) => { console.log(`  NG! ${m}`); fail = 1; };
const note = (m) => console.log(`  --  ${m}`);

/* 生の float PCM の FNV-1a 64。⚠️ **saan_pcm.c の checksum とは別物**
 * （あちらは int16 に直した列を食う）。ここは「2 つの経路が同じ列か」だけを見る */
function fnv(bytes) {
  let h = 0xcbf29ce484222325n;
  const P = 0x100000001b3n, M64 = 0xffffffffffffffffn;
  for (let i = 0; i < bytes.length; i++) h = ((h ^ BigInt(bytes[i])) * P) & M64;
  return '0x' + h.toString(16).padStart(16, '0');
}

async function load(mjs, wasm) {
  const bin = readFileSync(wasm);
  const factory = (await import(mjs)).default;
  /* ⚠️ -sENVIRONMENT=web の module は .wasm を fetch で取りに行く。node は file:// を
   *    fetch できないので公式フックで直に渡す。**出荷するバイナリそのもの**を検査する */
  const M = await factory({
    instantiateWasm(imports, cb) {
      WebAssembly.instantiate(bin, imports).then((r) => cb(r.instance));
      return {};
    },
  });
  M.__put = (buf) => {
    const p = M._saan_web_alloc(buf.length);
    if (!p) throw new Error('saan_web_alloc が NULL を返した');
    M.HEAPU8.set(buf, p);      /* ⚠️ HEAPU8 は毎回 Module から読む（伸びると detach する） */
    return p;
  };
  M.__init = (model, withDict) => {
    const mp = M.__put(model);
    let dp = 0, dl = 0;
    if (withDict && dict) { dp = M.__put(dict); dl = dict.length; }
    return M._saan_web_init(mp, model.length, dp, dl);
  };
  M.__synth = (text) => {
    const n = M.lengthBytesUTF8(text) + 1;
    const p = M._saan_web_alloc(n);
    M.stringToUTF8(text, p, n);
    const rc = M._saan_web_synth(p, n - 1);
    const ns = M._saan_web_n_samples();
    const ptr = M._saan_web_pcm();
    let sum = '(なし)';
    if (rc === 0 && ns > 0) {
      const f32 = M.HEAPF32.subarray(ptr >> 2, (ptr >> 2) + ns).slice();
      sum = fnv(new Uint8Array(f32.buffer));
    }
    return { rc, ns, sum, ids: M._saan_web_n_ids(),
             route: M.UTF8ToString(M._saan_web_route()),
             msg: M.UTF8ToString(M._saan_web_message()),
             arena: M._saan_web_arena_used(), sr: M._saan_web_sample_rate() };
  };
  return M;
}

/* --- 出荷 dist の 2 レーン ------------------------------------------------- */
const lanes = {};
for (const [name, file, want] of [['W8A32', 'saan_web_w8a32', 0], ['W8A8', 'saan_web_w8a8', 1]]) {
  const M = await load(`${distDir}/${file}.mjs`, `${distDir}/${file}.wasm`);
  const rc = M.__init(modelI8, true);
  if (rc !== 0) { ng(`${name}: init rc=${rc} ${M.UTF8ToString(M._saan_web_message())}`); continue; }

  const lane = M._saan_web_lane();
  if (lane === want) ok(`${name}: saan_web_lane() が ${lane} を申告（W8A32=0 / W8A8=1）`);
  else ng(`${name}: saan_web_lane() が ${lane}（期待 ${want}）— レーンを取り違えている`);

  const a = M.__synth(KANA);
  if (a.rc === 0 && a.ids === 53 && a.ns === 27136 && a.route === 'かな' && a.sr === 22050)
    ok(`${name}: かな中間表現 → route=${a.route} / 53 ids / 27,136 sample / ${a.sr} Hz  ${a.sum}`);
  else ng(`${name}: かな経路が期待と違う rc=${a.rc} route=${a.route} ids=${a.ids} ns=${a.ns} sr=${a.sr} ${a.msg}`);

  const bad = M.__synth(MIXED);
  if (bad.rc < 0) ok(`${name}: 中間表現 + 句点 は拒否（rc=${bad.rc}）`);
  else ng(`${name}: 拒否されるべき「中間表現 + 句点」が通った（rc=${bad.rc}）— それらしい音が出る`);

  /* ⚠️ 同じ入力が同じ PCM を返さないなら状態が漏れている（arena / g_pcm / stream） */
  const a1 = M.__synth(KANA), b1 = M.__synth(OTHER), a2 = M.__synth(KANA);
  if (a1.sum === a2.sum && b1.rc === 0 && b1.sum !== a1.sum)
    ok(`${name}: A→B→A で PCM が bit 一致（2 発話目が 1 発話目を汚さない）`);
  else ng(`${name}: A→B→A で PCM が変わった（${a1.sum} / ${b1.sum} / ${a2.sum}）`);

  lanes[name] = { sum: a.sum };

  if (haveDict) {
    const k = M.__synth(KANJI);
    if (k.rc === 0 && k.route === '辞書' && k.ids === 53 && k.ns === 27136 && k.sum === a.sum)
      ok(`${name}: 漢字文 → route=${k.route} / 53 ids / PCM が かな経路と bit 一致`);
    else ng(`${name}: 漢字 == かな が不成立 rc=${k.rc} route=${k.route} ids=${k.ids} ns=${k.ns} ${k.sum} vs ${a.sum} ${k.msg}`);

    const lg = M.__synth(LONG_KANJI);
    if (lg.rc < 0 && lg.msg.indexOf('ids が') >= 0)
      ok(`${name}: ids 350 超え は拒否（${lg.msg.slice(0, 30)}…）`);
    else ng(`${name}: ids 350 超えが通った rc=${lg.rc} ids=${lg.ids} msg=${lg.msg}`);
  }
}

/* 2 レーンの PCM が違うこと = 同じ wasm を 2 回読んでいない陽性対照 */
if (lanes.W8A32 && lanes.W8A8) {
  if (lanes.W8A32.sum !== lanes.W8A8.sum)
    ok('陽性対照: 2 レーンの PCM は違う（同じ wasm を 2 回読んでいない）');
  else
    ng('陽性対照: W8A32 と W8A8 の PCM が同じ — 片方しかビルドできていない可能性');
}

/* --- fp32 blob をレーンに渡す（G-W5 を**出荷バイナリで**確かめ直す）--------- */
{
  const M8 = await load(`${distDir}/saan_web_w8a8.mjs`, `${distDir}/saan_web_w8a8.wasm`);
  const rc8 = M8.__init(modelFp32, false);
  if (rc8 === -4) ok('出荷 W8A8: fp32 blob を拒否（rc=-4 = SAAN_WEB_ERR_LANE）');
  else ng(`出荷 W8A8 が fp32 blob を受け入れた（rc=${rc8}）— 速度が変わらない理由が分からなくなる`);

  /* ⚠️ **「全部拒否する」実装で満点を取らせない。** W8A32 は同じ blob を受けること */
  const M32 = await load(`${distDir}/saan_web_w8a32.mjs`, `${distDir}/saan_web_w8a32.wasm`);
  const rc32 = M32.__init(modelFp32, false);
  const f = rc32 === 0 ? M32.__synth(KANA) : null;
  if (rc32 === 0 && f.rc === 0 && f.ids === 53 && f.ns === 27136)
    ok('出荷 W8A32: 同じ fp32 blob は受ける（allow 側）');
  else ng(`出荷 W8A32 が fp32 blob を拒否した（init rc=${rc32}）— 検査が広すぎる`);

  /* 辞書なし構成で漢字文を打つと、拒否されて理由が出ること */
  if (rc32 === 0) {
    const k = M32.__synth(KANJI);
    if (k.rc === -12) ok('辞書なし構成: 漢字文は拒否され理由が message に出る（rc=-12）');
    else ng(`辞書なし構成で漢字文が rc=${k.rc}（期待 -12 = SAAN_WEB_ERR_NODICT）: ${k.msg}`);
  }
}

/* --- saan_pcm_reset() の陽性対照（統計窓つきの棒）-------------------------- */
{
  const P = await load(probeMjs, probeWasm);
  const rc = P.__init(modelI8, false);
  if (rc !== 0) { ng(`統計窓つきの棒: init rc=${rc}`); }
  else {
    const a1 = P.__synth(KANA);
    const s1 = [P._gw_pcm_sum_hi() >>> 0, P._gw_pcm_sum_lo() >>> 0, P._gw_pcm_samples(), P._gw_pcm_absmax()];
    P.__synth(OTHER);
    const a2 = P.__synth(KANA);
    const s2 = [P._gw_pcm_sum_hi() >>> 0, P._gw_pcm_sum_lo() >>> 0, P._gw_pcm_samples(), P._gw_pcm_absmax()];
    const hex = (s) => `0x${s[0].toString(16).padStart(8, '0')}${s[1].toString(16).padStart(8, '0')}`;
    if (a1.ns !== 27136 || a2.ns !== 27136 || s1[2] !== 27136) {
      ng(`統計窓つきの棒: 1 発話が 27,136 sample でない（pcm ${a1.ns}/${a2.ns} / stats ${s1[2]}）`);
    } else if (s1[0] === s2[0] && s1[1] === s2[1] && s2[2] === 27136) {
      ok(`saan_pcm_reset(): A→B→A で checksum ${hex(s1)} / samples ${s2[2]} / |max| ${s2[3]} が 1 発話目と一致`);
    } else {
      ng(`saan_pcm_reset() が効いていない: checksum ${hex(s1)} → ${hex(s2)} / `
         + `samples ${s1[2]} → ${s2[2]}（統計が「1 + 2 + 3 発話目」になっている）`);
    }
  }
}

if (!haveDict) {
  note('⚠️ **辞書が無いので「漢字 == かな」と「ids 350 超えの拒否」は回せなかった。**');
  note('   これは skip ではない: csrc/k1_dict.bin（13,702,320 B）を置けば回る。');
  note('   ⚠️ かな経路は 512 B の行上限で最大 343 ids にしか届かない（実測）ので、');
  note('      **ids 350 の拒否は辞書経路でしか踏めない**。辞書なしでは構造的に測れない。');
}

process.exit(fail);
HARNESS
        # ⚠️ 辞書は 13.7 MB。**無いときは skip と書かない**（棒の側が「回せなかった」と出す）
        GW6_DICT="-"
        if [ -f csrc/k1_dict.bin ]; then
            GW6_DICT="$ROOT/csrc/k1_dict.bin"
            printf '      辞書あり: csrc/k1_dict.bin（%s B）→ 漢字 == かな も回す\n' \
                   "$(( $(wc -c < csrc/k1_dict.bin) ))"
        fi
        if node "$TMP/gate_web.mjs" "$ROOT/web/dist" "$TMP/gw_probe.mjs" "$TMP/gw_probe.wasm" \
                "$ROOT/csrc/student_i8.bin" "$ROOT/csrc/student.bin" "$GW6_DICT" > "$TMP/g6" 2>&1; then
            cat "$TMP/g6"
        else
            cat "$TMP/g6"
            ng "G-W6 の棒が落ちた（上の NG! を見ること）"
        fi
    fi
fi

# ---------------------------------------------------------------- 結果
printf '\n'
if [ "$FAIL" = "0" ]; then
    printf '\033[1mG-W1 〜 G-W7 はすべて通った（陽性対照も落ちるべきところで落ちた）。\033[0m\n'
else
    printf '\033[1mNG: 通っていないゲートがある。\033[0m\n'
fi
cat <<'MSG'

⚠️ **ここで判定していないもの（ゲートにしていない）**:
   1. **ブラウザ**。node の wasm はブラウザの wasm ではない。速度も出音も未測定
   2. **音**。checksum が合っても AudioContext のリサンプルを経た出音は別物
   3. **モバイル**。1 種類も測っていない
   4. Pages が `.bin` に `Content-Encoding` を付けるか（だから `.gz` を置く）
   5. **`web/main.js`**。JS は 1 行も走らせていない（G-W6 が叩くのは wasm の凍結 ABI だけ）。
      ⚠️ **main.js が `saan_web_message()` の表示を消しても、ここは緑のまま**
   6. 漢字経路の**全段一致**（K-7 の G25 相当）— `kanji_e2e_vectors.bin` 19 MB が
      git にもリリースにも無い。G-W6 が見ているのは「漢字 == かな の PCM が bit 一致」まで
   7. held-out 24 文の SNR — `ids_heldout.bin` がコーパス由来で配れない
      （在れば G-W2b の多文レーンだけは回る。上の出力に在る／無いを書いてある）
MSG
exit "$FAIL"
