# saanoTTS-jp C99 推論コア
#   make test      一括版のゴールデンテスト（参照実装との一致）
#   make stream    ストリーミング版の受け入れ条件（Phase D-2 / D-029）
#   make fft       逆実 FFT の検証（naive DFT との SNR / 速度）
#   make int8      int8 カーネルの検証
#   make int8-golden int8 コアが fake-quant 参照と一致するか（統合の主ゲート）
#   make int8-e2e  int8 経路の end-to-end（fp32 経路との波形 SNR。D-3c'-2）
#   make arena     arena ストレス（ESP32 の静的 arena を決める・c'-4）
#   make bench     レイテンシ測定（段別の内訳）
#   make prof      段別プロファイラ（回数・要素数。ゲートは --expect-no-lookup = S1 /
#                  --expect-steps 54 = T1 / --expect-gelu・--expect-dw・--expect-mac-le = T2 の
#                  要素数 / --expect-token = T3。時間の行はゲートではない）
#   make range     S9 の出力範囲版カーネルが [0,T) 版と bit 一致（陽性対照つき。T2）
#   make erf       GELU の erf 近似 vs libm erff（線形補間の陽性対照つき。S3）
#   make g2p       端末側 G2P の受け入れゲート（自己完結ベクタ 2,819 件）
#   make line      端末の行編集ステートマシン + **行の経路判定**（どちらも陽性対照つき）
#   make kb-parity 経路判定がホスト (kana_g2p.classify_route) と一致するか。⚠️ pyopenjtalk が要る
#   make jdict     K-2/K-3 辞書リーダ + Viterbi + 未知語（MeCab との一致）
#   make accent    K-4 アクセント規則 4 段（Python 版との一致。陰性対照つき）
#   make njd-rules K-4b NJD チェーン（ホストとの一致。段・規則ごとの陰性対照つき）
#   make oj-heap   K-5 1 文あたりのピーク RAM（ヒープ + スタックの実測）
#   make kanji-e2e K-6 端末の全段をホストと突き合わせる（移植の正しさ + 枝刈りの代償）
#   make label-ids K-7 ラベル → 生徒インデックス（陰性対照つき）
#   ⚠️ この 6 つは 2026-09-03 に k2 / k4 / k4b / k5 / k6 / k7 から改名した
#      （工程番号ではなく中身で呼ぶ）。旧名は残していない。
#   make g2p-corpus  同・コーパス全行（data/splits/corpus_*.tsv が要る）
#   make all-test  test + stream + fft + int8 + int8-golden + int8-e2e + arena + g2p + pad + line + erf + range
#
# ⚠️ **jdict は all-test に入れていない。** 辞書（pyopenjtalk / piper-plus）と
#    数 MB の生成ベクタが要るので、g2p-corpus と同じ扱いにしてある。
#    辞書がある環境でだけ `make -C csrc jdict` を回すこと。
#    ⚠️ accent / njd-rules も同じ（ベクタ生成に pyopenjtalk が要る）。
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

# ⚠️ **`CFLAGS_EXTRA` はターゲットの同一性に入らない。**
#    `make int8_e2e_test CFLAGS_EXTRA=-DSAAN_INT8_ACT=1`（M-55 の再現）で建てた W8A8 版が
#    そのまま残り、次の `make int8-e2e` が**作り直さずに再利用する**。
#    結果、W8A32 のゲートが W8A8 を測って **28.11 → 24.23 dB で落ちる**（M-102 で実測）。
#    ⚠️ **フラグを変えて建てたら `rm -f int8_e2e_test` してから戻すこと。**
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

# S9（T2）: 出力範囲版カーネル vs [0,T) 版のランダム形状 bit 一致 + LayerNorm の T 非依存。
# ⚠️ 陽性対照つき（1 列ずらした比較が必ず不一致になる）。stream G2 は held-out の形しか通らない
range_test: range_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ range_test.c $(CORE) $(LDLIBS)

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
line_test: line_test.c line.c line.h g2p.c g2p.h g2p_table.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ line_test.c line.c g2p.c

# 経路判定（K-B）をホストから駆動する棒。**受け入れゲートではない** —
# ゲートは `make line` の G11。これは kb_route_parity.py がホスト判定と
# 突き合わせるための入出力で、端末が実際に走らせるコードをそのまま動かす。
route_tool: route_tool.c g2p.c g2p.h g2p_table.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ route_tool.c g2p.c

.PHONY: kb-parity
# ⚠️ **all-test には入れない**（pyopenjtalk が要る。k6 / k7 と同じ扱い）
kb-parity: route_tool
	cd .. && $(PYTHON) scripts/k1/kb_route_parity.py

# 自己完結ベクタ。mora テーブルだけから機械生成するので**コーパス本文を含まない**。
# ⚠️ `/csrc/*.bin` は .gitignore 済み。クリーン clone では毎回生成する
g2p_vectors.bin: ../scripts/gen_g2p_vectors.py ../scripts/kana_g2p.py
	cd .. && $(PYTHON) scripts/gen_g2p_vectors.py --closed \
	    --out csrc/g2p_vectors.bin --manifest csrc/g2p_vectors.json

# コーパス全行（23,000 行級）。中間表現と期待 ids だけで、原文も uid も入らない。
# ⚠️ `--closed` も必ず付ける。コーパス由来のベクタは**全件が正常系**で、
#    エラー期待が 1 件も無いと G2（allow/deny 両側）と G8（黙って落ちる件数）が
#    空虚になる（実測: コーパスだけだと `ー` drop 0 / `°` drop 0）。
g2p_vectors_corpus.bin:
	cd .. && $(PYTHON) scripts/gen_g2p_vectors.py --closed --corpus \
	    --out csrc/g2p_vectors_corpus.bin --manifest csrc/g2p_vectors_corpus.json

.PHONY: test golden stream fft int8 int8-golden int8-e2e int8-e2e-a8 lanes pad arena run-bench g2p g2p-corpus jdict-hard \
        line range all-test clean
# ⚠️ **`golden` は `test` の別名。** README / リリースノート v0.2.0 / .github/workflows/README.md が
#    `make -C csrc golden` と書いているのに、そのターゲットが**存在しなかった**
#    （配布済みの手順書にも入っている）。名前としても `golden`（ゴールデンテスト）の方が
#    中身を表すので、別名を足して手順が動くようにする。
golden: test

test: golden_test
	./golden_test student.bin golden.bin

# 3 レーン（fp32 / W8A32 / W8A8）× held-out 24 文（T2a）。
# ⚠️ 1 文（golden の in.ids = 106 frames ≡ 2 mod 8）だけでは n_frames mod CH の
#    残差 1 通りしか見ず、末尾 step 数・obuf の深さの壊れ方を捕まえられない
# ⚠️ W8A8 のレーンだけ G1 の上限を**実機の静的 arena**（esp32/main/main.c の SAAN_ARENA_BYTES =
#    176 KB。T4 で 208 → 176）にする。W8A8 は conv 1 本ぶんの activation 作業領域が乗る（M-55。
#    当時 201.1 KB、blob v2 で 203.0 KB、T2〜T4 後は 163.0 KB）。fp32 / W8A32 は D-029 の 200 KB のまま
#    （T4 後の実測 160.6 KB。上限は緩めていないだけで、きつくもしていない）
stream: stream_test stream_test_a8 student_i8.bin ids_heldout.bin
	./stream_test    student.bin    golden.bin ids_heldout.bin
	./stream_test    student_i8.bin golden.bin ids_heldout.bin
	./stream_test_a8 --g1-kb 176 student_i8.bin golden.bin ids_heldout.bin

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

# T2: §5 で「init 後の a.used == saan_stream_arena_used(n_ids)」を複数の n_ids で検査する。
# 雛形（esp32/main/main.c）はこの関数で init 後の a.used を検査するので、確保を変えて関数を
# 直し忘れると実機は 1 発話も喋らない（T2 で a.used が −16,512 B 動き、旧定数 192,960 では拒否された）
arena: arena_stress
	./arena_stress student.bin golden.bin

range: range_test
	./range_test

# ---------------------------------------------------------------- M-100
# jdict_open の入力検査。**辞書ファイルが要らない**ので all-test と CI に入る。
# ⚠️ **陽性対照は別バイナリ**（erf / range と同じ作法）。`-DJDICT_TEST_WEAK=1` で
#    検査を外したコアを建て、**同じ壊れた blob を通してしまう**ことを示す。
#    これが無いと「全部拒んだ」は「そもそも blob が組めていない」と区別できない。
jdict_hard_test: jdict_hard_test.c jdict.c jdict.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ jdict_hard_test.c jdict.c $(LDLIBS)

jdict_hard_test_weak: jdict_hard_test.c jdict.c jdict.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DJDICT_TEST_WEAK=1 -o $@ jdict_hard_test.c jdict.c $(LDLIBS)

# ⚠️ **ASan も建てる。** 「宣言長を見る前にヘッダを読む」越境は返り値に出ないので、
#    返り値の検査だけでは捕まらない（レビューで実際に見つかった）。
#    **陽性対照は「検査を外すと ASan が鳴る」こと** — 強い版で 0 件、weak で 2 件。
jdict_hard_test_asan: jdict_hard_test.c jdict.c jdict.h
	$(CC) -std=gnu17 -O1 -g -fsanitize=address -I. -o $@ jdict_hard_test.c jdict.c $(LDLIBS)

jdict_hard_test_asan_weak: jdict_hard_test.c jdict.c jdict.h
	$(CC) -std=gnu17 -O1 -g -fsanitize=address -DJDICT_TEST_WEAK=1 -I. -o $@ jdict_hard_test.c jdict.c $(LDLIBS)

jdict-hard: jdict_hard_test jdict_hard_test_weak jdict_hard_test_asan jdict_hard_test_asan_weak
	./jdict_hard_test
	@echo ""
	./jdict_hard_test_weak
	@echo ""
	@echo "=== G-H4: 範囲外読み（ASan）==="
	@./jdict_hard_test_asan > jdict_asan.log 2>&1; \
	 n=$$(grep -c heap-buffer-overflow jdict_asan.log || true); \
	 if [ "$$n" = "0" ]; then echo "  OK  検査ありは越境 0 件"; \
	 else echo "  NG  越境 $$n 件"; grep -m2 '#0 ' jdict_asan.log; exit 1; fi
	@./jdict_hard_test_asan_weak > jdict_asan_weak.log 2>&1; \
	 n=$$(grep -c heap-buffer-overflow jdict_asan_weak.log || true); \
	 if [ "$$n" != "0" ]; then echo "  OK  陽性対照: 検査を外すと越境 $$n 件（$$(grep -m1 -o 'jdict.c:[0-9]*' jdict_asan_weak.log)）"; \
	 else echo "  NG  陽性対照が鳴らない（ASan が効いていないか、壊し方が越境を作れていない）"; exit 1; fi

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

# T5-G3: 「S3 実装（erf_test.c 内の凍結コピー）との全格子 bit 一致」の**陽性対照**。
# クランプの上限を 4.0 からずらすと |x| ≥ 4 の値が 1.0f から動いて落ちる。
# ⚠️ 3.9999f では落ちない（erf(3.9999) は float でまだ 1.0f。計画の値は実測で外れた）ので 3.9f
# ⚠️ T5 検証で §2a（[-4,4] の float 全走査）/ §2b（本番 saan_gelu() のインライン経路）を足した。
#    --expect-grid-fail は **3 本すべてが落ちる**ことを要求する（1 本でも 0 なら効いていない）。
#    erf_test / erf_test_clamp は全走査で各 12 s 程度（M4 Max）
erf_test_clamp: erf_test.c $(CORE) $(HDRS)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DSAAN_ERF_TEST_CLAMP=3.9f -o $@ erf_test.c $(CORE) $(LDLIBS)

erf: erf_test erf_test_linear erf_test_clamp
	./erf_test
	./erf_test_linear --expect-fail
	./erf_test_clamp --expect-grid-fail

# 2 行目がゲート（T1 / T2 / T3）: demo_ids.h の 106 frames は 3 発話で step_chunk 54 回（18/発話）。
# T1 前は 63 で落ちる（全フレームが obuf に出そろった後に 3 step 余分に回っていた）。
# T2（S9）の要素数/step: GELU 12,544 = 5×304×8 + 48×8 / DW 21,280 = 5×76×7×8。T2 前は 21,664 / 37,240
# で落ちる（陽性対照は HEAD~ のコアに同じ prof_test.c を掛けて確認した）。
# T3（S6）: TOKEN 4 / step（= 9 群 × 8 トークン ÷ 18 step。各トークンを 1 回だけ計算）/ MAC ≤ 4,200,628
# （実測。T2 の 4,619,188 から −418,560 = TOKEN の 764,160 → 345,600）。T3 前のコアは TOKEN 22 / step で落ちる
# （同じく HEAD~ のコアで NG を確認）。⚠️ MAC の上限は実測値そのものなので、増える変更は必ずここで止まる
.PHONY: prof
prof: prof_test student_i8.bin
	./prof_test student_i8.bin --reps 20 --expect-no-lookup
	@./prof_test student_i8.bin --reps 3 --expect-no-lookup --expect-steps 54 \
	    --expect-gelu 12544 --expect-dw 21280 --expect-mac-le 4200628 --expect-token 4 > prof_gate.log \
	    || { tail -n 8 prof_gate.log; rm -f prof_gate.log; false; }
	@grep -E '^  (OK|NG)' prof_gate.log; rm -f prof_gate.log

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
all-test: test stream fft int8 int8-golden int8-e2e arena g2p pad line erf range jdict-hard

clean:
	rm -f golden_test stream_test stream_test_a8 fft_test int8_test int8_e2e_test bench arena_stress jdict_test accent_test \
	      erf_test erf_test_linear erf_test_clamp prof_test range_test \
	      jdict_hard_test jdict_hard_test_weak jdict_hard_test_asan jdict_hard_test_asan_weak jdict_asan*.log \
	      g2p_test line_test route_tool njd_rules_test njd_rules_stderr.log oj_heap_test oj_heap_stderr.log kanji_e2e_test kanji_e2e_stderr.log label_ids_test label_ids_stderr.log \
	      oj_labels*.txt oj_heap_ctrl oj_worst.bin oj_worst_texts.txt *.o openjtalk/*.o
	rm -f *vectors*.bin
	rm -rf oj_heap_ctrl_src *.dSYM


# ---------------------------------------------------------------- K-2
# 辞書リーダ + Viterbi。参照は MeCab そのもの（未知語を含む文は除く = K-3）。
# ベクタは大きい（blob 込みで数 MB）ので **生成物**。リポジトリには入れない。
# ⚠️ **依存を書く。** かつては前提が 1 つも無く、生成器や blob 形式を直しても
#    `make jdict` が**古い blob を測って通った**（M-100 §4。`.k1work/` に version 1 の
#    ベクタが実在し、現行リーダがそのまま 1977/1977 OK を出していた）。
jdict_vectors.bin: ../scripts/k1/k2_gen_vectors.py ../scripts/k1/k9_fit_8mb.py \
                   ../src/saanotts_jp/jdict.py
	$(PYTHON) ../scripts/k1/k2_gen_vectors.py --out $@

jdict_test: jdict_test.c jdict.c jdict.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ jdict_test.c jdict.c $(LDLIBS)

.PHONY: jdict matrixa
jdict: jdict_test jdict_vectors.bin
	./jdict_test jdict_vectors.bin

# --- matrixa（行ごとアフィン uint8 の接続行列。D-051 の ①）--------------------
# ⚠️ **2 本とも同じ逆量子化値**を持つ。違うのは**持ち方だけ**:
#      _deq = 生 int16（セクション `matrix`） / _aff = `matrixa`
#    リーダが正しければ jdict_trans は全要素で一致する。
# ⚠️ **辞書が要る**ので all-test には入れていない（jdict / accent と同じ扱い）。
jdict_vectors_deq.bin: ../scripts/k1/k2_gen_vectors.py ../scripts/k1/k9_fit_8mb.py \
                       ../src/saanotts_jp/jdict.py
	$(PYTHON) ../scripts/k1/k2_gen_vectors.py --matrix-mode affine --out $@

jdict_vectors_aff.bin: ../scripts/k1/k2_gen_vectors.py ../src/saanotts_jp/jdict.py
	$(PYTHON) ../scripts/k1/k2_gen_vectors.py --matrix-affine --out $@

matrixa_test: matrixa_test.c jdict.c jdict.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ matrixa_test.c jdict.c $(LDLIBS)

.PHONY: matrixa
matrixa: matrixa_test jdict_vectors_deq.bin jdict_vectors_aff.bin
	./matrixa_test jdict_vectors_deq.bin jdict_vectors_aff.bin
	@echo ""
	@echo "=== 参考: 同じベクタで MeCab との一致（量子化の代償）==="
	./jdict_test jdict_vectors_aff.bin || true

accent_vectors.bin: ../scripts/k1/k4_gen_vectors.py
	$(PYTHON) ../scripts/k1/k4_gen_vectors.py --out $@

accent_test: accent_test.c accent.c accent.h
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -o $@ accent_test.c accent.c $(LDLIBS)

.PHONY: accent
accent: accent_test accent_vectors.bin
	./accent_test accent_vectors.bin

# ---------------------------------------------------------------- K-4b
# NJD チェーン（mecab2njd + 取り込んだ Open JTalk + chaining 前の独自ルール）。
# ⚠️ **取り込み元は pyopenjtalk-plus**。素の pyopenjtalk と 22 / 34 ファイル
#    食い違うので、取り違えると G14a が 75 / 600 まで落ちる（C-048）。
#    上流との一致は `uv run python scripts/k1/k4b_vendor.py --sdist <tgz> --check`。
# ⚠️ **k2 / k4 と同じく all-test には入れない**（ベクタ生成に pyopenjtalk が要る）。
OJT_SRC := $(wildcard openjtalk/*.c)

njd_rules_vectors.bin: ../scripts/k1/k4b_gen_vectors.py
	$(PYTHON) ../scripts/k1/k4b_gen_vectors.py --out $@

njd_rules_test: njd_rules_test.c njd_rules.c njd_rules.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ njd_rules_test.c njd_rules.c $(OJT_SRC) $(LDLIBS)

.PHONY: njd-rules
# ⚠️ stderr を別ファイルに逃がす。**本番の 1 周では警告は 0 件**（ホストと一致）。
#    出るのは陰性対照の周回だけ（段を抜いた状態で njd_set_unvoiced_vowel を
#    通すため）。本番だけ回すなら `./njd_rules_test njd_rules_vectors.bin --solo`。
njd-rules: njd_rules_test njd_rules_vectors.bin
	@./njd_rules_test njd_rules_vectors.bin 2>njd_rules_stderr.log; rc=$$?; \
	 [ $$rc -eq 0 ] || { echo "--- stderr ---"; cat njd_rules_stderr.log; exit 1; }

# ---------------------------------------------------------------- K-5
# 1 文あたりのピーク RAM。**取り込んだ C は改変しない** —
# ヒープは `-include oj_heap_probe.h` のマクロ差し替え、スタックは自前スタックの
# 塗り潰しで測る。
# ⚠️ **MAXBUFLEN は 2 か所に書かない。** 取り込んだソースから読む
#    （ここに 1024 と直書きすると、詰めた後に必ずずれる = C-042 の形）。
OJ_MAXBUFLEN := $(shell awk '/define MAXBUFLEN/{print $$3; exit}' openjtalk/jpcommon_label.c)

oj_heap_test: oj_heap_test.c oj_heap_probe.c oj_heap_probe.h njd_rules.c njd_rules.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -DOJ_MAXBUFLEN=$(OJ_MAXBUFLEN) -c oj_heap_test.c -o oj_heap_test.o
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -c oj_heap_probe.c -o oj_heap_probe.o
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -c njd_rules.c -o njd_rules.o
	@for f in $(OJT_SRC); do \
	   $(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk -I. \
	       -include oj_heap_probe.h -c $$f -o $${f%.c}.o || exit 1; \
	 done
	$(CC) -o $@ oj_heap_test.o oj_heap_probe.o njd_rules.o $(OJT_SRC:.c=.o) $(LDLIBS)

# ⚠️ **ここに古い `k5:` があったが消した。** 下の 293 行目あたりの定義に
#    黙って上書きされていて（`make` が overriding commands の warning を出す）、
#    残っていたのは**陽性対照 `oj_heap_ctrl` も G22/G24 も無い弱い版**だった。
#    ⚠️ 二重定義は「直したつもりで死んだ recipe を直す」事故になる。
# ホストのラベル（basis）。**経路は oj_heap_test と同じ** = make_label(run_njd_from_mecab)
# ⚠️ **かつて生成規則が無かった**（M-100 §14）。`clean` は消すのに作り直せず、
#    **M-71 の再現コマンド `make -C csrc oj-heap` が clean な checkout から必ず失敗する**
#    状態だった（C-040 と同じ形）。復元が正しいかは、G22 のヒープ最大が
#    **M-71 の 97,325 B** を再現するかで判定する。
# ⚠️ oj_worst_texts.txt は**コーパス本文**なので git に入れない（.gitignore）。
oj_worst_texts.txt: ../scripts/k1/oj_worst_texts.py
	$(PYTHON) ../scripts/k1/oj_worst_texts.py --out $@

oj_worst.bin: oj_worst_texts.txt ../scripts/k1/k4b_gen_vectors.py
	$(PYTHON) ../scripts/k1/k4b_gen_vectors.py --only-texts oj_worst_texts.txt --out $@

oj_labels_host.txt: njd_rules_vectors.bin
	cd .. && $(PYTHON) scripts/k1/k5_gen_labels.py \
	    --vectors csrc/njd_rules_vectors.bin --out csrc/oj_labels_host.txt

# ⚠️ **陽性対照。** `MAXBUFLEN` を 64 に落とした版を別に作り、
#    G24 の diff が本当に差を捕まえられることを先に示す。
#    これが無いと「一致した」は「diff が効いていない」と区別できない。
oj_heap_ctrl_src/jpcommon_label.c: openjtalk/jpcommon_label.c
	@mkdir -p oj_heap_ctrl_src
	sed 's/#define MAXBUFLEN $(OJ_MAXBUFLEN)/#define MAXBUFLEN 64/' $< > $@
	@grep -q 'define MAXBUFLEN 64' $@ || { echo "NG: 陽性対照の置換が空振り"; exit 1; }

oj_heap_ctrl: oj_heap_test.c oj_heap_probe.c njd_rules.c oj_heap_ctrl_src/jpcommon_label.c $(OJT_SRC)
	@mkdir -p oj_heap_ctrl_src
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk -I. \
	    -DOJ_MAXBUFLEN=64 -o $@ oj_heap_test.c oj_heap_probe.c njd_rules.c \
	    oj_heap_ctrl_src/jpcommon_label.c \
	    $(filter-out openjtalk/jpcommon_label.c,$(OJT_SRC)) $(LDLIBS)

.PHONY: oj-heap
oj-heap: oj_heap_test oj_heap_ctrl njd_rules_vectors.bin oj_worst.bin oj_labels_host.txt
	@./oj_heap_test njd_rules_vectors.bin oj_labels.txt 2>oj_heap_stderr.log || \
	  { echo "--- stderr ---"; cat oj_heap_stderr.log; exit 1; }
	@echo ""
	@echo "=== G22（最長文 100 文）==="
	@./oj_heap_test oj_worst.bin oj_labels_worst.txt 2>>oj_heap_stderr.log | \
	   sed -n '/ヒープ/,/占める分/p'
	@echo ""
	@echo "=== G24: ラベルがホストと bit 一致するか ==="
	@if diff -q oj_labels_host.txt oj_labels.txt >/dev/null; then \
	   echo "  OK  ホストと完全一致（$$(grep -c '^# case' oj_labels.txt) 文 / $$(grep -vc '^# case' oj_labels.txt) 本）"; \
	 else echo "  NG  ホストと食い違う"; diff oj_labels_host.txt oj_labels.txt | head -6; exit 1; fi
	@./oj_heap_ctrl njd_rules_vectors.bin oj_labels_ctrl.txt 2>/dev/null >/dev/null || true
	@if diff -q oj_labels_host.txt oj_labels_ctrl.txt >/dev/null; then \
	   echo "  NG  **陽性対照が落ちない**。MAXBUFLEN=64 でも一致した = diff が効いていない"; exit 1; \
	 else echo "  OK  陽性対照: MAXBUFLEN=64 だと $$(diff oj_labels_host.txt oj_labels_ctrl.txt | grep -c '^<') 行が食い違う"; fi
	@echo ""
	@echo "  打ち切り警告（本番）: $$(grep -c 'Label buffer exceeded' oj_heap_stderr.log) 件"
	@echo ""
	@echo "OK  G22 / G23 / G24 通過"

# ---------------------------------------------------------------- K-6
# 端末の全段（K-2 辞書 → K-4b NJD → K-4 の 4 段 → ラベル）をホストと比べる。
# ⚠️ **合否は「移植の正しさ」だけに置く**（素性が一致した文でラベルが一致するか）。
#    辞書の枝刈りの代償は**測って出すだけで、閾値は置かない** —
#    D-043 の 0.60% は同形異音語 14 文から出た数で、枝刈りの話ではない（C-050）。
# ⚠️ k2 / k4 / k4b と同じく all-test には入れない（辞書と pyopenjtalk が要る）。
# ⚠️ **依存を書く**（M-100 §4）。blob 形式や生成器を直しても古いベクタを測って通っていた。
kanji_e2e_vectors.bin: ../scripts/k1/k6_gen_vectors.py ../src/saanotts_jp/jdict.py
	cd .. && $(PYTHON) scripts/k1/k6_gen_vectors.py --out csrc/kanji_e2e_vectors.bin

kanji_e2e_test: kanji_e2e_test.c jdict.c accent.c njd_rules.c $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ kanji_e2e_test.c jdict.c accent.c njd_rules.c $(OJT_SRC) $(LDLIBS)

.PHONY: kanji-e2e
kanji-e2e: kanji_e2e_test kanji_e2e_vectors.bin
	@./kanji_e2e_test kanji_e2e_vectors.bin 2>kanji_e2e_stderr.log || \
	  { echo "--- stderr ---"; cat kanji_e2e_stderr.log; exit 1; }

# ---------------------------------------------------------------- K-7
# ラベル → 生徒インデックス（`label_ids.c`）。K-6 のベクタをそのまま使う。
# ⚠️ **合否は G25（移植の正しさ）だけ**。通しの一致（G26）は枝刈りの分だけ
#    必ず食い違うので**数を出すだけ**（C-050 と同じ扱い）。
token_table.h:
	cd .. && $(PYTHON) scripts/gen_token_table.py

label_ids_test: label_ids_test.c jdict.c accent.c njd_rules.c label_ids.c token_table.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -Iopenjtalk \
	    -o $@ label_ids_test.c jdict.c accent.c njd_rules.c label_ids.c \
	    $(OJT_SRC) $(LDLIBS)

# T10(a): トークン表を **.bss ではなく呼び出し側の領域**に置いた構成
# （ESP32 は合成 arena から切り出す）。**同じ列が出ること**を示すためだけの版。
# ⚠️ これが無いと「端末では置き場が違う」経路がホストで一度も走らない。
label_ids_test_ext: label_ids_test.c jdict.c accent.c njd_rules.c label_ids.c token_table.h $(OJT_SRC)
	$(CC) $(CFLAGS) $(CFLAGS_EXTRA) -DCHARSET_UTF_8 -DK7_EXTERNAL_SCRATCH=1 -Iopenjtalk \
	    -o $@ label_ids_test.c jdict.c accent.c njd_rules.c label_ids.c \
	    $(OJT_SRC) $(LDLIBS)

.PHONY: label-ids
label-ids: label_ids_test label_ids_test_ext kanji_e2e_vectors.bin
	@./label_ids_test kanji_e2e_vectors.bin 2>label_ids_stderr.log || \
	  { echo "--- stderr ---"; cat label_ids_stderr.log; exit 1; }
	@echo ""
	@echo "=== G25b: トークン表を arena に置いた構成でも同じ列か（T10(a)）==="
	@./label_ids_test kanji_e2e_vectors.bin --dump-ids k7_ids_bss.txt >/dev/null 2>>label_ids_stderr.log
	@./label_ids_test_ext kanji_e2e_vectors.bin --dump-ids k7_ids_ext.txt >/dev/null 2>>label_ids_stderr.log || \
	  { echo "  NG  外部置き場の版が落ちた"; cat label_ids_stderr.log; exit 1; }
	@if diff -q k7_ids_bss.txt k7_ids_ext.txt >/dev/null; then \
	   echo "  OK  .bss 版と arena 版の ids が完全一致（$$(wc -l < k7_ids_bss.txt) 文）"; \
	 else echo "  NG  置き場を変えたら列が変わった"; \
	      diff k7_ids_bss.txt k7_ids_ext.txt | head -6; exit 1; fi
