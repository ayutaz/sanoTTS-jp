# saanoTTS-jp C99 推論コア
#   make test      一括版のゴールデンテスト（参照実装との一致）
#   make stream    ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make fft       逆実 FFT の検証（naive DFT との SNR / 速度）
#   make int8      int8 カーネルの検証
#   make int8-golden int8 コアが fake-quant 参照と一致するか（統合の主ゲート）
#   make int8-e2e  int8 経路の end-to-end（fp32 経路との波形 SNR。D-3c'-2）
#   make arena     arena ストレス（ESP32 の静的 arena を決める・c'-4）
#   make bench     レイテンシ測定（段別の内訳）
#   make g2p       端末側 G2P の受け入れゲート（自己完結ベクタ 2,819 件）
#   make line      端末の行編集ステートマシン（**陽性対照つき**）
#   make k2        K-2 辞書リーダ + Viterbi（MeCab との一致。陰性対照つき）
#   make g2p-corpus  同・コーパス全行（data/splits/corpus_*.tsv が要る）
#   make all-test  test + stream + fft + int8 + int8-golden + int8-e2e + arena + g2p + line + k2
#
# ⚠️ **naive DFT は消していない。** `make CFLAGS_EXTRA=-DSAAN_USE_NAIVE_DFT test` で
#    切り替わる。FFT の検証基準として残してある（D-3a）。
# ⚠️ **ベクタ生成に使う Python。** 既定はプロジェクト環境（piper-plus への path 依存を
#    解決するので、**piper-plus のクローンが要る**）。持っていない人はプロジェクトを
#    経由しない環境を指すこと — 生成器は凍結テーブル（csrc/g2p_table.json）に落ちるので
#    stdlib だけで走る:
#
#      make -C csrc g2p PYTHON="uv run --no-project python"
#
# ⚠️ **既定のままだと「動いた」が piper-plus のおかげか分からない。**
#    実際に、これを分けるまで「piper-plus 無しで通った」と誤って観測した（C-041）。
PYTHON       ?= uv run python
CC           ?= cc
CFLAGS       ?= -std=c99 -O2 -Wall -Wextra
CFLAGS_EXTRA ?=
LDLIBS       := -lm
# ⚠️ **saanotts_int8.c はコアの一部。** `saan_w` / `saan_conv1d_w` /
#    `saan_dwconv1d_w` / `saan_act_scratch_needed` がここにあり、
#    saanotts.c と saanotts_stream.c の両方が参照する。外すとリンクできない。
#    ESP-IDF の component もこの 4 ファイル **+ g2p.c** を SRCS に並べること
#    （g2p.c はコアを参照しない独立の翻訳単位だが main.c が saan_g2p() を呼ぶ）。
CORE         := saanotts.c saanotts_stream.c fft.c saanotts_int8.c
HDRS         := saanotts.h saanotts_stream.h saanotts_internal.h fft.h saanotts_int8.h

# int8 ブロブと **fake-quant golden** は必ず一緒に作る。
# ⚠️ `--golden-from-quantized` が無いと golden は fp32 参照のままで、
#    突き合わせが「量子化誤差 26 dB」になり**層を 1 つ fp32 に置き忘れても検出できない**。
# ⚠️ `--report` を分けないと fp32 の記録（csrc/export.json）を潰す。
student_i8.bin golden_i8.bin &:
	cd .. && uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt \
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

# 知覚評価用の PCM ダンプ（M-55）。**W8A32 と W8A8 を別バイナリで作る** —
# 切り替えがコンパイル時なので、同じ名前で上書きすると**どちらを測ったか分からなくなる**
dump_pcm_a32: dump_pcm.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ dump_pcm.c $(CORE) $(LDLIBS)

dump_pcm_a8: dump_pcm.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_INT8_ACT=1 -o $@ dump_pcm.c $(CORE) $(LDLIBS)

# arena ストレス（c'-4）。fork で SEGV を観測するので POSIX が要る = **ホスト専用**
arena_stress: arena_stress.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -D_DARWIN_C_SOURCE -o $@ arena_stress.c $(CORE) $(LDLIBS)

bench: bench.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ bench.c $(CORE) $(LDLIBS)

# --- 端末側 G2P（かな中間表現 → 生徒インデックス列）------------------------
# ⚠️ **推論コアに依存しない独立の翻訳単位。** libm も要らないので $(LDLIBS) 無し。
# ⚠️ `g2p_table.h` は**生成物**（コミット済み）。piper-plus の phonemizer が
#    変わったら `uv run python scripts/gen_g2p_tables.py` を打ち直すこと。
#    打ち忘れは `make g2p` の G1（SHA-256 一致）が落として教えてくれる。
g2p_test: g2p_test.c g2p.c g2p.h g2p_table.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ g2p_test.c g2p.c

# --- 端末の行編集（シリアルからの自由入力）----------------------------------
# ⚠️ **推論コアにも G2P にも依存しない。** 入力バイト列 → 行 だけを見る。
#    ベクタが空虚でないことは line_test.c 自身が陽性対照で検査する
#    （わざと素直に書いた naive_feed() が同じベクタで落ちること）。
line_test: line_test.c line.c line.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ line_test.c line.c

# 自己完結ベクタ。mora テーブルだけから機械生成するので**コーパス本文を含まない**。
# ⚠️ `/csrc/*.bin` は .gitignore 済み。クリーン clone では毎回生成する
g2p_vectors.bin:
	cd .. && $(PYTHON) scripts/gen_g2p_vectors.py --closed \
	    --out csrc/g2p_vectors.bin --manifest csrc/g2p_vectors.json

# コーパス全行（23,000 行級）。中間表現と期待 ids だけで、原文も uid も入らない。
# ⚠️ `--closed` も必ず付ける。コーパス由来のベクタは**全件が正常系**で、
#    エラー期待が 1 件も無いと G2（allow/deny 両側）と G8（黙って落ちる件数）が
#    空虚になる（実測: コーパスだけだと `ー` drop 0 / `°` drop 0）。
g2p_vectors_corpus.bin:
	cd .. && $(PYTHON) scripts/gen_g2p_vectors.py --closed --corpus \
	    --out csrc/g2p_vectors_corpus.bin --manifest csrc/g2p_vectors_corpus.json

.PHONY: test stream fft int8 int8-golden int8-e2e int8-e2e-a8 lanes pad arena run-bench g2p g2p-corpus \
        line all-test clean
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

# G-1: W8A8 のパディング部のゼロ埋め（**陽性対照つき**）。
# ⚠️ 4 通りのビルドを比べる検査なので、単一バイナリでは書けない。
# ⚠️ `SAAN_PIE_EMU=1` が必須 — 無いとホストは隙間を 1 バイトも読まず、
#    4 通りが全部同じ checksum を出して**空虚に通る**（実際に踏んだ）。
pad:
	./run_pad_gate.sh

# W8A8 の end-to-end SNR（PIE の前提。⚠️ **既定のゲートは満たさない** — M-55）
# ⚠️ SNR は知覚に対応しない。判断は `make lanes` の SCOREQ で行うこと
int8-e2e-a8: student_i8.bin ids_heldout.bin
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_INT8_ACT=1 -o int8_e2e_test_a8 \
	    int8_e2e_test.c $(CORE) $(LDLIBS)
	-./int8_e2e_test_a8 student.bin student_i8.bin golden.bin ids_heldout.bin \
	    ../reports/d3c2_int8_e2e_w8a8.json

# fp32 / W8A32 / W8A8 の 3 レーンを WAV に書き出して SCOREQ で比べる（M-55）。
# ⚠️ **陽性対照つき**（同じ SNR の白色雑音で SCOREQ が下がることを先に示す）
lanes: dump_pcm_a32 dump_pcm_a8 student_i8.bin ids_heldout.bin
	mkdir -p ../reports/lanes/fp32 ../reports/lanes/w8a32 ../reports/lanes/w8a8
	./dump_pcm_a32 student.bin ids_heldout.bin ../reports/lanes/fp32
	./dump_pcm_a32 --ref student.bin student_i8.bin ids_heldout.bin ../reports/lanes/w8a32
	./dump_pcm_a8  --ref student.bin student_i8.bin ids_heldout.bin ../reports/lanes/w8a8
	cd .. && uv run --extra eval python scripts/d3_lane_scoreq.py --dir reports/lanes

run-bench: bench
	./bench --weights student.bin --json ../reports/d3b_latency_fft.json --label fft

# 端末側 G2P。Python (scripts/kana_g2p.py) と ids が**整数として完全一致**するか。
g2p: g2p_test g2p_vectors.bin
	./g2p_test g2p_vectors.bin --min-vectors 2700

# 端末の行編集。**入力ファイルを要らないので all-test に入れる**
line: line_test
	./line_test

# コーパス全行。`data/splits/corpus_*.tsv` を要求するので **all-test には入れない**
# （クリーン clone では走らない。`test`/`stream`/`arena` が student.bin を要るのと同じ理由）。
g2p-corpus: g2p_test g2p_vectors_corpus.bin
	./g2p_test g2p_vectors_corpus.bin --min-vectors 23000

# `arena` も all-test に入れる。かつては saan_alloc の失敗が粘着せず
# 「init が SAAN_OK を返した後に落ちる」欠陥で必ず落ちていたが、
# saan_arena に粘着フラグ failed を足して解消した（111 点中 0 点が silent-fail）。
# `g2p` も入れてある（自己完結ベクタ 2,819 件のみ。コーパス版は本文 TSV が要るので別）。
all-test: test stream fft int8 int8-golden int8-e2e arena g2p pad line

clean:
	rm -f golden_test stream_test fft_test int8_test int8_e2e_test bench arena_stress \
	      g2p_test line_test


# ---------------------------------------------------------------- K-2
# 辞書リーダ + Viterbi。参照は MeCab そのもの（未知語を含む文は除く = K-3）。
# ベクタは大きい（blob 込みで数 MB）ので **生成物**。リポジトリには入れない。
k2_vectors.bin:
	$(PYTHON) ../scripts/k1/k2_gen_vectors.py --out $@

k2_test: k2_test.c k1dict.c k1dict.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ k2_test.c k1dict.c $(LDLIBS)

.PHONY: k2
k2: k2_test k2_vectors.bin
	./k2_test k2_vectors.bin
