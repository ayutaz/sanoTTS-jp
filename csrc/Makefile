# saanoTTS-jp C99 推論コア
#   make test     一括版のゴールデンテスト（参照実装との一致）
#   make stream   ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make all-test 両方
CC      ?= cc
CFLAGS  ?= -std=c99 -O2 -Wall -Wextra
LDLIBS  := -lm
CORE    := saanotts.c saanotts_stream.c

golden_test: golden_test.c saanotts.c saanotts.h
	$(CC) $(CFLAGS) -o $@ golden_test.c saanotts.c $(LDLIBS)

stream_test: stream_test.c $(CORE) saanotts.h saanotts_stream.h saanotts_internal.h
	$(CC) $(CFLAGS) -o $@ stream_test.c $(CORE) $(LDLIBS)

.PHONY: test stream all-test clean
test: golden_test
	./golden_test student.bin golden.bin

stream: stream_test
	./stream_test student.bin golden.bin

all-test: test stream

clean:
	rm -f golden_test stream_test
