# saanoTTS-jp C99 推論コア
#   make test      一括版のゴールデンテスト（参照実装との一致）
#   make stream    ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make fft       逆実 FFT の検証（naive DFT との SNR / 速度）
#   make int8      int8 カーネルの検証
#   make int8-golden int8 コアが fake-quant 参照と一致するか（統合の主ゲート）
#   make int8-e2e  int8 経路の end-to-end（fp32 経路との波形 SNR。D-3c'-2）
#   make arena     arena ストレス（ESP32 の静的 arena を決める・c'-4）
#   make bench     レイテンシ測定（段別の内訳）
#   make prof      段別プロファイラ（回数・要素数。ゲートは --expect-no-lookup = S1 と
#                  --expect-steps 54 = T1 の 2 つ。時間の行はゲートではない）
#   make erf       GELU の erf 近似 vs libm erff（線形補間の陽性対照つき。S3）
#   make g2p       端末側 G2P の受け入れゲート（自己完結ベクタ 2,819 件）
#   make line      端末の行編集ステートマシン（**陽性対照つき**）
#   make k2        K-2/K-3 辞書リーダ + Viterbi + 未知語（MeCab との一致）
#   make k4        K-4 アクセント規則 4 段（Python 版との一致。陰性対照つき）
#   make k4b       K-4b NJD チェーン（ホストとの一致。段・規則ごとの陰性対照つき）
#   make k5        K-5 1 文あたりのピーク RAM（ヒープ + スタックの実測）
#   make k6        K-6 端末の全段をホストと突き合わせる（移植の正しさ + 枝刈りの代償）
#   make k7        K-7 ラベル → 生徒インデックス（陰性対照つき）
#   make g2p-corpus  同・コーパス全行（data/splits/corpus_*.tsv が要る）
#   make all-test  test + stream + fft + int8 + int8-golden + int8-e2e + arena + g2p + pad + line + erf
#
# ⚠️ **k2 は all-test に入れていない。** 辞書（pyopenjtalk / piper-plus）と
#    数 MB の生成ベクタが要るので、g2p-corpus と同じ扱いにしてある。
#    辞書がある環境でだけ `make -C csrc k2` を回すこと。
#    ⚠️ k4 / k4b も同じ（ベクタ生成に pyopenjtalk が要る）。
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
HDRS         := saanotts.h saanotts_stream.h saanotts_internal.h fft.h saanotts_int8.h saan_prof.h erf_table.h

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

# W8A8 のストリーミング（T2a）。切り替えがコンパイル時なので**別バイナリ**にする
# （dump_pcm_a32 / dump_pcm_a8 と同じ理由: 同じ名前で上書きするとどちらを測ったか分からない）
stream_test_a8: stream_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_INT8_ACT=1 -o $@ stream_test.c $(CORE) $(LDLIBS)

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
	# ⚠️ かつて `-D_DARWIN_C_SOURCE` を付けていたが **macOS 専用**で Linux では効かない。
	#    `arena_stress.c` が自前で `_POSIX_C_SOURCE` を立てるようにした。
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ arena_stress.c $(CORE) $(LDLIBS)

bench: bench.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ bench.c $(CORE) $(LDLIBS)

# 段別プロファイラ（saan_prof.h）。**ゲートではない**。W8A8 で回す（実機の構成に合わせる）。
# ⚠️ ホストの時間の内訳は実機の内訳ではない。回数と要素数だけが共通（prof_test.c の冒頭）
prof_test: prof_test.c $(CORE) $(HDRS) ../esp32/main/demo_ids.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_INT8_ACT=1 -DSAAN_PROFILE=1 -o $@ prof_test.c $(CORE) $(LDLIBS)

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

# 3 レーン（fp32 / W8A32 / W8A8）× held-out 24 文（T2a）。
# ⚠️ 1 文（golden の in.ids = 106 frames ≡ 2 mod 8）だけでは n_frames mod CH の
#    残差 1 通りしか見ず、末尾 step 数・obuf の深さの壊れ方を捕まえられない
# ⚠️ W8A8 のレーンだけ G1 の上限を **208 KB**（実機の静的 arena。esp32/main/main.c の
#    SAAN_ARENA_BYTES）にする。W8A8 は 200 KB を超えることが分かっている（M-55: conv 1 本ぶんの
#    activation 作業領域。当時 201.1 KB、blob v2 の今は 203.0 KB）。fp32 / W8A32 は D-029 の 200 KB のまま
stream: stream_test stream_test_a8 student_i8.bin ids_heldout.bin
	./stream_test    student.bin    golden.bin ids_heldout.bin
	./stream_test    student_i8.bin golden.bin ids_heldout.bin
	./stream_test_a8 --g1-kb 208 student_i8.bin golden.bin ids_heldout.bin

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

# S3: GELU の erf 近似が libm の erff と一致するか（**陽性対照つき**: 線形補間は落ちる）
erf_test: erf_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ erf_test.c $(CORE) $(LDLIBS)

erf_test_linear: erf_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_ERF_TEST_LINEAR=1 -o $@ erf_test.c $(CORE) $(LDLIBS)

erf: erf_test erf_test_linear
	./erf_test
	./erf_test_linear --expect-fail

# 2 行目がゲート（T1）: demo_ids.h の 106 frames は 3 発話で step_chunk 54 回（18/発話）。
# T1 前は 63 で落ちる（全フレームが obuf に出そろった後に 3 step 余分に回っていた）
prof: prof_test student_i8.bin
	./prof_test student_i8.bin --reps 20 --expect-no-lookup
	./prof_test student_i8.bin --reps 3 --expect-no-lookup --expect-steps 54 > /dev/null
	@echo "  OK  step_chunk 54 回 / 3 発話（T1。詳細は ./prof_test student_i8.bin --reps 3 --expect-steps 54）"

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
all-test: test stream fft int8 int8-golden int8-e2e arena g2p pad line erf

clean:
	rm -f golden_test stream_test stream_test_a8 fft_test int8_test int8_e2e_test bench arena_stress k2_test k4_test \
	      g2p_test line_test k4b_test k4b_stderr.log k5_mem_test k5_stderr.log k6_test k6_stderr.log k7_test k7_stderr.log \
	      k5_labels*.txt k5_mem_ctrl k5_worst.bin *.o openjtalk/*.o
	rm -rf k5_ctrl


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

k4_vectors.bin:
	$(PYTHON) ../scripts/k1/k4_gen_vectors.py --out $@

k4_test: k4_test.c k4_accent.c k4_accent.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ k4_test.c k4_accent.c $(LDLIBS)

.PHONY: k4
k4: k4_test k4_vectors.bin
	./k4_test k4_vectors.bin

# ---------------------------------------------------------------- K-4b
# NJD チェーン（mecab2njd + 取り込んだ Open JTalk + chaining 前の独自ルール）。
# ⚠️ **取り込み元は pyopenjtalk-plus**。素の pyopenjtalk と 22 / 34 ファイル
#    食い違うので、取り違えると G14a が 75 / 600 まで落ちる（C-048）。
#    上流との一致は `uv run python scripts/k1/k4b_vendor.py --sdist <tgz> --check`。
# ⚠️ **k2 / k4 と同じく all-test には入れない**（ベクタ生成に pyopenjtalk が要る）。
OJT_SRC := $(wildcard openjtalk/*.c)

k4b_vectors.bin:
	$(PYTHON) ../scripts/k1/k4b_gen_vectors.py --out $@

k4b_test: k4b_test.c k4b_njd.c k4b_njd.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ k4b_test.c k4b_njd.c $(OJT_SRC) $(LDLIBS)

.PHONY: k4b
# ⚠️ stderr を別ファイルに逃がす。**本番の 1 周では警告は 0 件**（ホストと一致）。
#    出るのは陰性対照の周回だけ（段を抜いた状態で njd_set_unvoiced_vowel を
#    通すため）。本番だけ回すなら `./k4b_test k4b_vectors.bin --solo`。
k4b: k4b_test k4b_vectors.bin
	@./k4b_test k4b_vectors.bin 2>k4b_stderr.log; rc=$$?; \
	 [ $$rc -eq 0 ] || { echo "--- stderr ---"; cat k4b_stderr.log; exit 1; }

# ---------------------------------------------------------------- K-5
# 1 文あたりのピーク RAM。**取り込んだ C は改変しない** —
# ヒープは `-include k5_alloc.h` のマクロ差し替え、スタックは自前スタックの
# 塗り潰しで測る。
# ⚠️ **MAXBUFLEN は 2 か所に書かない。** 取り込んだソースから読む
#    （ここに 1024 と直書きすると、詰めた後に必ずずれる = C-042 の形）。
K5_MAXBUFLEN := $(shell awk '/define MAXBUFLEN/{print $$3; exit}' openjtalk/jpcommon_label.c)

k5_mem_test: k5_mem_test.c k5_alloc.c k5_alloc.h k4b_njd.c k4b_njd.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -DK5_MAXBUFLEN=$(K5_MAXBUFLEN) -c k5_mem_test.c -o k5_mem_test.o
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -c k5_alloc.c -o k5_alloc.o
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -c k4b_njd.c -o k4b_njd.o
	@for f in $(OJT_SRC); do \
	   $(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk -I. \
	       -include k5_alloc.h -c $$f -o $${f%.c}.o || exit 1; \
	 done
	$(CC) -o $@ k5_mem_test.o k5_alloc.o k4b_njd.o $(OJT_SRC:.c=.o) $(LDLIBS)

# ⚠️ **ここに古い `k5:` があったが消した。** 下の 293 行目あたりの定義に
#    黙って上書きされていて（`make` が overriding commands の warning を出す）、
#    残っていたのは**陽性対照 `k5_mem_ctrl` も G22/G24 も無い弱い版**だった。
#    ⚠️ 二重定義は「直したつもりで死んだ recipe を直す」事故になる。
# ホストのラベル（basis）。**経路は k5_mem_test と同じ** = make_label(run_njd_from_mecab)
k5_labels_host.txt: k4b_vectors.bin
	cd .. && $(PYTHON) scripts/k1/k5_gen_labels.py \
	    --vectors csrc/k4b_vectors.bin --out csrc/k5_labels_host.txt

# ⚠️ **陽性対照。** `MAXBUFLEN` を 64 に落とした版を別に作り、
#    G24 の diff が本当に差を捕まえられることを先に示す。
#    これが無いと「一致した」は「diff が効いていない」と区別できない。
k5_ctrl/jpcommon_label.c: openjtalk/jpcommon_label.c
	@mkdir -p k5_ctrl
	sed 's/#define MAXBUFLEN $(K5_MAXBUFLEN)/#define MAXBUFLEN 64/' $< > $@
	@grep -q 'define MAXBUFLEN 64' $@ || { echo "NG: 陽性対照の置換が空振り"; exit 1; }

k5_mem_ctrl: k5_mem_test.c k5_alloc.c k4b_njd.c k5_ctrl/jpcommon_label.c $(OJT_SRC)
	@mkdir -p k5_ctrl
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk -I. \
	    -DK5_MAXBUFLEN=64 -o $@ k5_mem_test.c k5_alloc.c k4b_njd.c \
	    k5_ctrl/jpcommon_label.c \
	    $(filter-out openjtalk/jpcommon_label.c,$(OJT_SRC)) $(LDLIBS)

.PHONY: k5
k5: k5_mem_test k5_mem_ctrl k4b_vectors.bin k5_worst.bin k5_labels_host.txt
	@./k5_mem_test k4b_vectors.bin k5_labels.txt 2>k5_stderr.log || \
	  { echo "--- stderr ---"; cat k5_stderr.log; exit 1; }
	@echo ""
	@echo "=== G22（最長文 100 文）==="
	@./k5_mem_test k5_worst.bin k5_labels_worst.txt 2>>k5_stderr.log | \
	   sed -n '/ヒープ/,/占める分/p'
	@echo ""
	@echo "=== G24: ラベルがホストと bit 一致するか ==="
	@if diff -q k5_labels_host.txt k5_labels.txt >/dev/null; then \
	   echo "  OK  ホストと完全一致（$$(grep -c '^# case' k5_labels.txt) 文 / $$(grep -vc '^# case' k5_labels.txt) 本）"; \
	 else echo "  NG  ホストと食い違う"; diff k5_labels_host.txt k5_labels.txt | head -6; exit 1; fi
	@./k5_mem_ctrl k4b_vectors.bin k5_labels_ctrl.txt 2>/dev/null >/dev/null || true
	@if diff -q k5_labels_host.txt k5_labels_ctrl.txt >/dev/null; then \
	   echo "  NG  **陽性対照が落ちない**。MAXBUFLEN=64 でも一致した = diff が効いていない"; exit 1; \
	 else echo "  OK  陽性対照: MAXBUFLEN=64 だと $$(diff k5_labels_host.txt k5_labels_ctrl.txt | grep -c '^<') 行が食い違う"; fi
	@echo ""
	@echo "  打ち切り警告（本番）: $$(grep -c 'Label buffer exceeded' k5_stderr.log) 件"
	@echo ""
	@echo "OK  G22 / G23 / G24 通過"

# ---------------------------------------------------------------- K-6
# 端末の全段（K-2 辞書 → K-4b NJD → K-4 の 4 段 → ラベル）をホストと比べる。
# ⚠️ **合否は「移植の正しさ」だけに置く**（素性が一致した文でラベルが一致するか）。
#    辞書の枝刈りの代償は**測って出すだけで、閾値は置かない** —
#    D-043 の 0.60% は同形異音語 14 文から出た数で、枝刈りの話ではない（C-050）。
# ⚠️ k2 / k4 / k4b と同じく all-test には入れない（辞書と pyopenjtalk が要る）。
k6_vectors.bin:
	cd .. && $(PYTHON) scripts/k1/k6_gen_vectors.py --out csrc/k6_vectors.bin

k6_test: k6_test.c k1dict.c k4_accent.c k4b_njd.c $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ k6_test.c k1dict.c k4_accent.c k4b_njd.c $(OJT_SRC) $(LDLIBS)

.PHONY: k6
k6: k6_test k6_vectors.bin
	@./k6_test k6_vectors.bin 2>k6_stderr.log || \
	  { echo "--- stderr ---"; cat k6_stderr.log; exit 1; }

# ---------------------------------------------------------------- K-7
# ラベル → 生徒インデックス（`k7_label2ids.c`）。K-6 のベクタをそのまま使う。
# ⚠️ **合否は G25（移植の正しさ）だけ**。通しの一致（G26）は枝刈りの分だけ
#    必ず食い違うので**数を出すだけ**（C-050 と同じ扱い）。
k7_table.h:
	cd .. && $(PYTHON) scripts/gen_k7_table.py

k7_test: k7_test.c k1dict.c k4_accent.c k4b_njd.c k7_label2ids.c k7_table.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ k7_test.c k1dict.c k4_accent.c k4b_njd.c k7_label2ids.c \
	    $(OJT_SRC) $(LDLIBS)

.PHONY: k7
k7: k7_test k6_vectors.bin
	@./k7_test k6_vectors.bin 2>k7_stderr.log || \
	  { echo "--- stderr ---"; cat k7_stderr.log; exit 1; }
