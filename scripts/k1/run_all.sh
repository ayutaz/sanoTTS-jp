#!/bin/sh
K1_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# saanoTTS-jp: OpenJTalk オンデバイス化の RAM 実測。全部を頭から再現する。
set -e
SP=${K1_WORK:-$K1_ROOT/.k1work}
DIC_VENV=$K1_ROOT/.venv/lib/python3.14/site-packages/pyopenjtalk/dictionary
DIC_PP=${PIPER_PLUS_ROOT:-$HOME/Documents/piper-plus}/build/share/open_jtalk/dic

echo "### 0. ソース探索 (探索範囲の記録つき)"
uv run --no-project python "$SP/find_sources.py" | head -14

echo; echo "### 1. コーパス抽出"
uv run --no-project python "$SP/prep_corpus.py"

echo; echo "### 2. 計測バイナリのビルド"
sh "$SP/build_probe.sh"

echo; echo "### 3. Q1(b) mmap 実測 (陽性対照つき)"
uv run python "$SP/q1_memory.py" 2>&1 | grep -v '^warning:'
uv run python "$SP/q1b_memory.py" 2>&1 | grep -v '^warning:'

echo; echo "### 4. Q2/Q3/Q4 本計測"
"$SP/saan_probe" "$DIC_VENV" "$SP/heldout_text.txt" > "$SP/probe_venv.tsv" 2> "$SP/probe_venv.err"
"$SP/saan_probe" "$DIC_PP"   "$SP/heldout_text.txt" > "$SP/probe_pp.tsv"   2> "$SP/probe_pp.err"
uv run --no-project python "$SP/analyze2.py"
uv run --no-project python "$SP/analyze3.py"
uv run --no-project python "$SP/heap_breakdown.py"

echo; echo "### 5. pyopenjtalk との突き合わせ (ゲート)"
uv run python "$SP/crosscheck2.py" 2>&1 | grep -v '^warning:'

echo; echo "### 6. Q4 文長スイープ"
uv run --no-project python "$SP/make_sweep.py"
"$SP/saan_probe" "$DIC_VENV" "$SP/sweep_text.txt" > "$SP/sweep.tsv" 2> "$SP/sweep.err"
uv run --no-project python "$SP/analyze_sweep.py"

echo; echo "### 7. ESP32-S3 の sizeof (クロスコンパイル実測)"
PATH="$HOME/.espressif/tools/xtensa-esp-elf/esp-14.2.0_20241119/xtensa-esp-elf/bin:$PATH" \
  xtensa-esp32s3-elf-gcc -mlongcalls -O2 -std=c99 -c "$SP/sizeof_probe.c" -o "$SP/sizeof_probe_xtensa.o"
PATH="$HOME/.espressif/tools/xtensa-esp-elf/esp-14.2.0_20241119/xtensa-esp-elf/bin:$PATH" \
  xtensa-esp32s3-elf-nm --print-size --size-sort "$SP/sizeof_probe_xtensa.o" | grep saan_sizeof

echo; echo "### 8. C プロセス単体での sys.dic 常駐ページ (vmmap)"
"$SP/saan_pages" "$DIC_VENV" "$SP/heldout_text.txt" 103131410 > "$SP/pages.tsv" 2> "$SP/pages.err"
cat "$SP/pages.err"
