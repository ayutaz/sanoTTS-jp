# saanoTTS-jp C99 推論コア
#   make test      一括版のゴールデンテスト（参照実装との一致）
#   make stream    ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make fft       逆実 FFT の検証（naive DFT との SNR / 速度）
#   make int8      int8 カーネルの検証
#   make bench     レイテンシ測定（段別の内訳）
#   make all-test  test + stream + fft + int8
#
# ⚠️ **naive DFT は消していない。** `make CFLAGS_EXTRA=-DSAAN_USE_NAIVE_DFT test` で
#    切り替わる。FFT の検証基準として残してある（D-3a）。
CC           ?= cc
CFLAGS       ?= -std=c99 -O2 -Wall -Wextra
CFLAGS_EXTRA ?=
LDLIBS       := -lm
CORE         := saanotts.c saanotts_stream.c fft.c
HDRS         := saanotts.h saanotts_stream.h saanotts_internal.h fft.h

student_i8.bin:
	cd .. && uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt \
	    --int8 --out csrc/student_i8.bin --golden /tmp/saan_g_i8.bin

golden_test: golden_test.c saanotts.c fft.c $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ golden_test.c saanotts.c fft.c $(LDLIBS)

stream_test: stream_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ stream_test.c $(CORE) $(LDLIBS)

fft_test: fft_test.c fft.c fft.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ fft_test.c fft.c $(LDLIBS)

int8_test: int8_test.c saanotts_int8.c saanotts.c fft.c $(HDRS) saanotts_int8.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ int8_test.c saanotts_int8.c saanotts.c fft.c $(LDLIBS)

bench: bench.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ bench.c $(CORE) $(LDLIBS)

.PHONY: test stream fft int8 run-bench all-test clean
test: golden_test
	./golden_test student.bin golden.bin

stream: stream_test
	./stream_test student.bin golden.bin

fft: fft_test
	./fft_test

int8: int8_test student_i8.bin
	./int8_test student.bin student_i8.bin golden.bin ../reports/d3c_int8.json

run-bench: bench
	./bench --weights student.bin --json ../reports/d3b_latency_fft.json --label fft

all-test: test stream fft int8

clean:
	rm -f golden_test stream_test fft_test int8_test bench
