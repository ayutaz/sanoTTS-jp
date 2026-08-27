# saanoTTS-jp C99 推論コア
#   make test      一括版のゴールデンテスト（参照実装との一致）
#   make stream    ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make fft       逆実 FFT の検証（naive DFT との SNR / 速度）
#   make int8      int8 カーネルの検証
#   make int8-golden int8 コアが fake-quant 参照と一致するか（統合の主ゲート）
#   make int8-e2e  int8 経路の end-to-end（fp32 経路との波形 SNR。D-3c'-2）
#   make arena     arena ストレス（ESP32 の静的 arena を決める・c'-4）
#   make bench     レイテンシ測定（段別の内訳）
#   make all-test  test + stream + fft + int8 + int8-golden + int8-e2e + arena
#
# ⚠️ **naive DFT は消していない。** `make CFLAGS_EXTRA=-DSAAN_USE_NAIVE_DFT test` で
#    切り替わる。FFT の検証基準として残してある（D-3a）。
CC           ?= cc
CFLAGS       ?= -std=c99 -O2 -Wall -Wextra
CFLAGS_EXTRA ?=
LDLIBS       := -lm
# ⚠️ **saanotts_int8.c はコアの一部。** `saan_w` / `saan_conv1d_w` /
#    `saan_dwconv1d_w` / `saan_act_scratch_needed` がここにあり、
#    saanotts.c と saanotts_stream.c の両方が参照する。外すとリンクできない。
#    ESP-IDF の component もこの 4 ファイルを SRCS に並べること。
CORE         := saanotts.c saanotts_stream.c fft.c saanotts_int8.c
HDRS         := saanotts.h saanotts_stream.h saanotts_internal.h fft.h saanotts_int8.h

# int8 ブロブと **fake-quant golden** は必ず一緒に作る。
# ⚠️ `--golden-from-quantized` が無いと golden は fp32 参照のままで、
#    突き合わせが「量子化誤差 26 dB」になり**層を 1 つ fp32 に置き忘れても検出できない**。
# ⚠️ `--report` を分けないと fp32 の記録（csrc/export.json）を潰す。
student_i8.bin golden_i8.bin &:
	cd .. && uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt \
	    --int8 --out csrc/student_i8.bin --golden csrc/golden_i8.bin \
	    --golden-from-quantized --report csrc/export_i8.json

# held-out の音素ID列（int8-e2e が 1 文ではなく 24 文で測るのに使う）
ids_heldout.bin:
	cd .. && uv run python scripts/export_c_ids.py --limit 24 --out csrc/ids_heldout.bin

golden_test: golden_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ golden_test.c $(CORE) $(LDLIBS)

stream_test: stream_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ stream_test.c $(CORE) $(LDLIBS)

fft_test: fft_test.c fft.c fft.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ fft_test.c fft.c $(LDLIBS)

int8_test: int8_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ int8_test.c $(CORE) $(LDLIBS)

int8_e2e_test: int8_e2e_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ int8_e2e_test.c $(CORE) $(LDLIBS)

# arena ストレス（c'-4）。fork で SEGV を観測するので POSIX が要る = **ホスト専用**
arena_stress: arena_stress.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -D_DARWIN_C_SOURCE -o $@ arena_stress.c $(CORE) $(LDLIBS)

bench: bench.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ bench.c $(CORE) $(LDLIBS)

.PHONY: test stream fft int8 int8-golden int8-e2e arena run-bench all-test clean
test: golden_test
	./golden_test student.bin golden.bin

stream: stream_test
	./stream_test student.bin golden.bin

fft: fft_test
	./fft_test

int8: int8_test student_i8.bin
	./int8_test student.bin student_i8.bin golden.bin ../reports/d3c_int8.json

# int8 統合の**主ゲート**: int8 コアが PyTorch の fake-quant と一致するか。
# ⚠️ 40 dB 台に落ちたら層の載せ忘れ。波形 SNR 25 dB のゲートだけでは見逃す
int8-golden: golden_test student_i8.bin golden_i8.bin
	./golden_test student_i8.bin golden_i8.bin

int8-e2e: int8_e2e_test student_i8.bin ids_heldout.bin
	./int8_e2e_test student.bin student_i8.bin golden.bin ids_heldout.bin \
	    ../reports/d3c2_int8_e2e.json

arena: arena_stress
	./arena_stress student.bin golden.bin

run-bench: bench
	./bench --weights student.bin --json ../reports/d3b_latency_fft.json --label fft

# `arena` も all-test に入れる。かつては saan_alloc の失敗が粘着せず
# 「init が SAAN_OK を返した後に落ちる」欠陥で必ず落ちていたが、
# saan_arena に粘着フラグ failed を足して解消した（111 点中 0 点が silent-fail）。
all-test: test stream fft int8 int8-golden int8-e2e arena

clean:
	rm -f golden_test stream_test fft_test int8_test int8_e2e_test bench arena_stress
