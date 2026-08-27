# saanoTTS-jp C99 推論コア
#   make test    ゴールデンテストをビルドして走らせる
#   make clean
CC      ?= cc
CFLAGS  ?= -std=c99 -O2 -Wall -Wextra
LDLIBS  := -lm

golden_test: golden_test.c saanotts.c saanotts.h
	$(CC) $(CFLAGS) -o $@ golden_test.c saanotts.c $(LDLIBS)

.PHONY: test clean
test: golden_test
	./golden_test student.bin golden.bin

clean:
	rm -f golden_test
