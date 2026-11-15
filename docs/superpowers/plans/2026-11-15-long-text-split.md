# 長文分割 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 上限（形態素 44 個 / 350 ids）を超える漢字かな交じり文を、文末記号で区切って順に全部喋らせる。

**Architecture:** 区切りの規則と「全体を試す → 駄目なら区切る → 全区間を検査してから喋る」という手順を、`csrc/split.c` に **I/O を持たない C99** で 1 本だけ書く。ESP-IDF の `esp32/main/main.c` と Arduino の `arduino/src/SanoTTS.cpp` は、「文 → ids」と「ids → 鳴らす」の 2 つのコールバックを渡すだけにする。区間ごとの合成は今の `synth_once` / `openStream` をそのまま通すので、**区間の PCM は単独で合成したときと同じ経路になる**。

**Tech Stack:** C99（`csrc/`）/ ESP-IDF v5.5 + QEMU 9.0.0 / arduino-esp32（`scripts/build_arduino_lib.py` が逐語で取り込む）

**Spec:** [`docs/superpowers/specs/2026-11-15-nonkiai-followups-design.md`](../specs/2026-11-15-nonkiai-followups-design.md)

**Branch:** `feat/long-text-split`（`main` から）

## Global Constraints

- **上限内の入力は今と 1 命令も違う経路を通らない。** 全体の `to_ids` が OK かつ `n_ids ≤ 350` なら、`speak` を 1 回呼ぶだけ（PCM は `v1.2.0` と bit 一致）
- **上限は緩めない。** 区間ごとに今の上限（`SAAN_KANJI_MAX_INPUT_TOK` 44 / `SAAN_MAX_IDS` 350 / `SANOTTS_MAX_INPUT_BYTES` 512）をそのまま守る
- **全か無か。** 1 区間でも変換できない・上限を超えるなら、**1 区間も喋らない**（分布外の音を黙って出さない。今の拒否方針と同じ）
- **長さ以外の失敗では区切らない。** 全体の `to_ids` が「長すぎる」以外で失敗したら、その失敗をそのまま返す
- **かな経路は変えない**（かな中間表現は `。` を含まないので区切れない。今どおり拒否）
- **コア（`csrc/saanotts*.c`・`fft.c`）と `esp32/main/saan_kanji.c` は変えない**
- 区切りの開始記号は `。` `！` `？` `!` `?` の 5 つ。⚠️ **`.` と `．` は開始記号にしない**（`3.14` を切らないため）
- 区間の上限は **`SAAN_SPLIT_MAX_SPANS` = 64**

## Review Focus

1. **文末記号が 1 つも無い長文**（`、` だけで続く 60 形態素）→ 今と同じく拒否し、メッセージが「`。` で区切ること」と言う → Task 2 の `single_span_too_long` と Task 3 の QEMU ケース C
2. **疑問 EOS が区間の末尾に残る**（`？〜` / `?!` / `?.`）→ 4 種の疑問 EOS（G33）が区切りで壊れない → Task 1 のベクタ
3. **閉じ括弧の直前の `。`**（`「行こう。」と言った。`）→ 閉じ括弧は前の区間に付く → Task 1 のベクタ
4. **ちょうど 350 ids の全体**→ 区切らずに 1 回で喋る（境界の取り違え）→ Task 2 の `whole_at_limit`
5. **区間が 65 個以上**→ 何も喋らずに `SAAN_SPLIT_ERR_SPANS` → Task 2 の `too_many_spans`

---

### Task 1: 区切りの規則（`saan_split_next`）

**Files:**
- Create: `csrc/split.h`, `csrc/split.c`, `csrc/split_test.c`
- Modify: `csrc/Makefile`（`split_test` の規則・`split` ターゲット・`.PHONY`・`all-test`・`clean`）
- Modify: `.github/workflows/ci.yml`（`make -C csrc line` の直後に `make -C csrc split`）

**Interfaces:**
- Produces:
  ```c
  typedef struct { int32_t off; int32_t len; } saan_span;   /* text からのバイト位置 */
  typedef enum {
      SAAN_SPLIT_OK = 0,
      SAAN_SPLIT_END = 1,            /* もう区間が無い */
      SAAN_SPLIT_ERR_UTF8 = -1,      /* *err_byte に位置 */
  } saan_split_next_status;
  /* text[*pos..nbytes) の次の区間を返し、*pos を進める。 */
  saan_split_next_status saan_split_next(const char *text, size_t nbytes, size_t *pos,
                                         saan_span *out, int32_t *err_byte);
  ```

**規則（実装者が決められないので固定する）:**
- 区間の終わり = **開始記号**（`。！？!?`）に続く**終端の並び**の最後。終端の並びは開始記号・`．` `.` `〜`(U+301C) `～`(U+FF5E) `…` と、閉じ括弧 `」』）)】` のどれかが続く限り伸びる
- 区間の先頭の空白（` ` `\t` `\r` `\n` U+3000）は区間に含めず飛ばす
- 「中身」= 空白・終端の並びの文字・閉じ括弧のどれでもない文字。**中身を含まない区間は次の区間の頭にくっつける**（`。あ。` → 1 区間）。末尾に残った中身の無い断片は捨てる（空白だけ・記号だけ）
- 開始記号が無いまま文字列が終わったら、そこまでを 1 区間にする

- [ ] **Step 1: 失敗するテストを書く**（`csrc/split_test.c`。`setvbuf(stdout, NULL, _IONBF, 0)` を先頭で呼ぶ）

  **G-SP1 ベクタ**（入力 → 期待する区間の文字列。`naive` 列は Step 2 の陽性対照で使う）:

  | 名前 | 入力 | 期待 | naive が落ちる |
  |---|---|---|---|
  | `two` | `今日は晴れ。明日は雨。` | `今日は晴れ。` / `明日は雨。` | いいえ |
  | `q_bang` | `本当？！それは良い。` | `本当？！` / `それは良い。` | **はい** |
  | `q_wave` | `そうですか？〜いいえ。` | `そうですか？〜` / `いいえ。` | **はい** |
  | `q_dot` | `本当?.うん。` | `本当?.` / `うん。` | **はい** |
  | `closer` | `「行こう。」と言った。` | `「行こう。」` / `と言った。` | **はい** |
  | `run` | `あ。。。い` | `あ。。。` / `い` | **はい** |
  | `lead_punct` | `。あ。` | `。あ。` | **はい** |
  | `crlf` | `あ。\r\n  い。` | `あ。` / `い。` | **はい** |
  | `no_end` | `終わりなし` | `終わりなし` | いいえ |
  | `decimal` | `円周率は3.14です。` | `円周率は3.14です。` | いいえ |
  | `ascii` | `Hi! OK?` | `Hi!` / `OK?` | **はい** |
  | `empty` | （空） | （区間なし） | いいえ |
  | `blank` | `  　` | （区間なし） | **はい** |
  | `bad_utf8` | `あ\xffい。` | `SAAN_SPLIT_ERR_UTF8`、`err_byte == 3` | — |

  **G-SP2 性質**（擬似乱数、seed 固定、10,000 ケース。字母 `あ 漢 。 ！ ？ ? ! 〜 」 . 空白 \n` から長さ 0〜40）。全ケースで:
  - 区間は昇順で重ならない
  - 区間の外のバイトは空白だけ
  - どの区間にも中身が 1 文字以上ある
  - 区間の両端が UTF-8 の文字境界にある
  - **区間を空白を挟まずにつないだ列 == 元の列から区間外の空白を除いた列**（無損失）

  合否は「G-SP1 n=14 で 14/14」「G-SP2 n=10000 で 10000/10000」のように**分母つき**で出す。

- [ ] **Step 2: 陽性対照を同じファイルに置く**

  `static saan_split_next_status naive_next(...)` を `split_test.c` に書く。開始記号 1 文字ごとに切るだけで、終端の並び・閉じ括弧・中身の無い区間のくっつけ・先頭の空白飛ばしをしない実装にする。G-SP1 を naive にも流し、**上の表で「はい」の 9 件がちょうど落ち、「いいえ」の 4 件が通る**ことを要求する（1:1。合計件数ではなく名前で照合する）。食い違ったら NG で終了する。

- [ ] **Step 3: 落ちることを確かめる**

  `csrc/split.c` は `return SAAN_SPLIT_END;` だけのスタブにする。
  Run: `make -C csrc split`
  Expected: G-SP1 の `two` ほかが NG。**陽性対照（naive）は 9/9 落ちて OK と出る**（スタブでもテスト側の naive は動く）。

- [ ] **Step 4: `saan_split_next` を実装する**

  UTF-8 は 1〜4 バイトを手で復号する（`g2p.c` と同じく外部ライブラリなし）。

- [ ] **Step 5: 通ることを確かめる**

  Run: `make -C csrc split && make -C csrc all-test`
  Expected: `G-SP1 n=14 で 14/14` / `G-SP2 n=10000 で 10000/10000` / `陽性対照 naive: 想定どおり 9/9 落ちた`。all-test が緑。

- [ ] **Step 6: ゲートを数えるゲートを回す**

  Run: `uv run --no-project python scripts/check_ci_coverage.py && uv run --no-project python scripts/check_ci_coverage.py --self-test`
  Expected: `split` が「CI で回る」側に出る（ci.yml に足していなければここで落ちる）。

- [ ] **Step 7: Commit**

  ```bash
  git add csrc/split.h csrc/split.c csrc/split_test.c csrc/Makefile .github/workflows/ci.yml
  git commit -m "feat(split): 長文の区切り規則 saan_split_next と G-SP1/G-SP2（陽性対照 naive 9/9）"
  ```

---

### Task 2: 手順（`saan_split_say`）

**Files:**
- Modify: `csrc/split.h`, `csrc/split.c`, `csrc/split_test.c`

**Interfaces:**
- Consumes: Task 1 の `saan_span` / `saan_split_next`
- Produces:
  ```c
  #define SAAN_SPLIT_MAX_SPANS 64
  #define SAAN_SPLIT_CB_TOO_LONG (-1000)      /* to_ids が「長すぎて変換できない」ときに返す */
  #define SAAN_SPLIT_CB_TOO_MANY_IDS (-1001)  /* saan_split_say が bad_code に入れる（n_ids > max_ids） */
  /* 0 = OK（*n_ids に書く）/ SAAN_SPLIT_CB_TOO_LONG / それ以外の負値 = 変換できない */
  typedef int (*saan_split_to_ids_fn)(void *ctx, const char *seg, size_t len, int32_t *n_ids);
  /* 直前の to_ids が作った列を合成して鳴らす。0 = OK。index は 0 始まり */
  typedef int (*saan_split_speak_fn)(void *ctx, int32_t index, int32_t n_spans);
  typedef enum {
      SAAN_SPLIT_SAID = 0,
      SAAN_SPLIT_ERR_TO_IDS = -2,    /* 全体が長さ以外で失敗。bad_code にそのコード */
      SAAN_SPLIT_ERR_SEGMENT = -3,   /* 区間 bad_index が変換できない / 上限超え。何も喋っていない */
      SAAN_SPLIT_ERR_SPANS = -4,     /* 区間が SAAN_SPLIT_MAX_SPANS を超えた。何も喋っていない */
      SAAN_SPLIT_ERR_SPEAK = -5,     /* 区間 bad_index の speak が失敗。それより前は喋った */
      SAAN_SPLIT_ERR_UTF8 = -1,      /* saan_split_next と同じ値 */
  } saan_split_status;
  typedef struct { int32_t n_spans; int32_t bad_index; int bad_code; saan_span bad_span; } saan_split_result;
  saan_split_status saan_split_say(const char *text, size_t nbytes, int32_t max_ids,
                                   saan_split_to_ids_fn to_ids, saan_split_speak_fn speak,
                                   void *ctx, saan_split_result *res);
  const char *saan_split_strerror(saan_split_status s);
  ```

**手順（固定）:**
1. `to_ids(全体)`。0 かつ `n ≤ max_ids` → `speak(0, 1)` → `SAID`（`n_spans = 1`）
2. 0 かつ `n > max_ids`、または `SAAN_SPLIT_CB_TOO_LONG` → 区切る。それ以外の負値 → `ERR_TO_IDS`
3. 区間を全部数える（> 64 なら `ERR_SPANS`）。**各区間に `to_ids` を 1 回ずつ呼んで検査する**（失敗 or `n > max_ids` → `ERR_SEGMENT`。区間が 1 個しか無い場合もここで落ちる）
4. 全部通ったら、区間 k ごとに `to_ids(区間 k)` → `speak(k, N)`。speak が失敗したら `ERR_SPEAK` で止める
- 区間の表（64 × 8 B = 512 B）は関数のスタックに置く

- [ ] **Step 1: 失敗するテストを書く**（`split_test.c` に G-SP3 として追加）

  偽の `to_ids` は「`len` バイトなら `n_ids = 2·len + 3`、`len > ctx->too_long_bytes` なら `SAAN_SPLIT_CB_TOO_LONG`、`ctx->fail_text` と一致したら `ctx->fail_code`」とする。偽の `speak` は `ctx->speak_fail_at` で失敗する。両方とも**呼ばれた順に `(種別, 区間の文字列 or index/N)` を記録**する。各ケースで記録列を**完全一致**で比べる:

  | 名前 | 条件 | 期待する戻り値 | 期待する記録列 |
  |---|---|---|---|
  | `whole_fits` | `あ。い。`、max 350 | `SAID`, n_spans 1 | `to_ids「あ。い。」`, `speak 0/1` |
  | `whole_at_limit` | 全体の n_ids が**ちょうど** max（max = 2·len+3 に設定） | `SAID`, n_spans 1 | `to_ids 全体`, `speak 0/1` |
  | `split_by_ids` | `あ。い。う。`、max を全体の n_ids − 1 に | `SAID`, n_spans 3 | `to_ids 全体`, `to_ids「あ。」`, `「い。」`, `「う。」`, `to_ids「あ。」`, `speak 0/3`, `to_ids「い。」`, `speak 1/3`, `to_ids「う。」`, `speak 2/3` |
  | `split_by_cb` | 同じ文、`too_long_bytes` を全体長 − 1 に | 同上 | 同上 |
  | `segment_bad` | 2 区間目だけ `fail_code = -7` | `ERR_SEGMENT`, bad_index 1, bad_code −7, `bad_span` が「い。」 | `speak` を**含まない** |
  | `segment_over` | 2 区間目だけ長い（n_ids > max） | `ERR_SEGMENT`, bad_index 1, bad_code `SAAN_SPLIT_CB_TOO_MANY_IDS` | `speak` を含まない |
  | `single_span_too_long` | `、` だけで続く長い 1 文（区切りなし） | `ERR_SEGMENT`, bad_index 0 | `speak` を含まない |
  | `other_error` | 全体で `fail_code = -7` | `ERR_TO_IDS`, bad_code −7 | `to_ids 全体` だけ（**区切らない**） |
  | `speak_fails` | 3 区間、`speak_fail_at = 1` | `ERR_SPEAK`, bad_index 1 | `speak 0/3`, `speak 1/3` で終わり、`speak 2/3` を含まない |
  | `too_many_spans` | `あ。` × 65、全体は長すぎ | `ERR_SPANS` | `speak` を含まない |
  | `bad_utf8` | 全体が長すぎ + 途中に `\xff` | `ERR_UTF8` | `speak` を含まない |

- [ ] **Step 2: 落ちることを確かめる**

  `saan_split_say` は `return SAAN_SPLIT_ERR_TO_IDS;` のスタブ。
  Run: `make -C csrc split`
  Expected: G-SP3 の 11 件がすべて NG（`other_error` も記録列が違うので落ちる）。

- [ ] **Step 3: `saan_split_say` を上の手順どおりに実装する**

- [ ] **Step 4: 通ることを確かめる**

  Run: `make -C csrc split`
  Expected: `G-SP3 n=11 で 11/11`。

- [ ] **Step 5: 検査の順序が守られていることを壊して確かめる（陽性対照。実行したものだけ記録する）**

  一時的に手順 3 の検査ループを消して（区間ごとに検査せずにいきなり喋る）`make -C csrc split` を走らせ、**`segment_bad` と `segment_over` が落ちる**ことを見る。見たら元に戻す。落ちたケース名をコミットメッセージに書く。落ちなければテストが弱いので Step 1 に戻る。

- [ ] **Step 6: Commit**

  ```bash
  git add csrc/split.h csrc/split.c csrc/split_test.c
  git commit -m "feat(split): saan_split_say（全体 → 区切り → 全区間検査 → 発話）と G-SP3 11 件"
  ```

---

### Task 3: ESP-IDF ファームに入れ、QEMU で通す

**Files:**
- Modify: `esp32/components/saanotts_core/CMakeLists.txt`（`SRCS` に `"${SAAN_CSRC}/split.c"`）
- Modify: `esp32/main/main.c`（`speak_kanji`）
- Create: `scripts/check_split_qemu.sh`
- Modify: `scripts/check_ci_coverage.py`（`EXCLUDED_SCRIPTS` に `scripts/check_split_qemu.sh`。理由: 「ESP-IDF + QEMU + 辞書 13.7 MB が要る」）

**Interfaces:**
- Consumes: Task 2 の `saan_split_say` / `saan_split_strerror` / `SAAN_SPLIT_CB_TOO_LONG`
- Produces（`main.c` の中だけ）:
  ```c
  typedef struct { const saan_weights *w; int32_t n_ids; } kanji_split_ctx;
  static int  kanji_to_ids_cb(void *ctx, const char *seg, size_t len, int32_t *n_ids); /* saan_kanji_to_ids を包む。ERR_TOO_LONG → SAAN_SPLIT_CB_TOO_LONG */
  static int  kanji_speak_cb(void *ctx, int32_t index, int32_t n_spans);            /* "区間 %d/%d" を出して synth_once */
  ```
  `speak_kanji` は G2P のログと高水位のログを `kanji_to_ids_cb` に移し、`saan_split_say(text, nbytes, SAAN_MAX_IDS, ...)` を 1 回呼ぶだけにする。`n_spans == 1` のときは "区間" の行を**出さない**（ログの互換）。失敗時は `saan_split_strerror` と `bad_index + 1` / `n_spans`、`ERR_SEGMENT` なら区間の先頭 30 バイトを出す。区切りが無くて落ちたら「`。` で区切ること」と出す。**区切って喋ったときは `g_last_n_ids = 0` にする**（タッチでの「もう一度」が最後の区間だけを鳴らすのを防ぐ。`n_spans == 1` のときは今どおり）。

- [ ] **Step 1: 基準値を `main` で採る**（実装の前に。比較する相手が要る）

  `git stash` などで変更の無い `main` に戻し、`bash scripts/check_split_qemu.sh --baseline` を走らせる。ケース A と B の checksum を `reports/split_qemu/baseline.txt` に保存する（git 管理外）。
  前提: `csrc/k1_dict.bin` が `gh release download v1.2.0 -R ayutaz/sanoTTS-jp -p k1-dict-438750.bin` の中身と同じであること（スクリプトの先頭で SHA-256 を `SHA256SUMS.txt` と照合する）。

- [ ] **Step 2: 失敗するゲートを書く（`scripts/check_split_qemu.sh`）**

  ビルドは `check_dict_integrity.sh` と同じ漢字 QEMU 構成（`sdkconfig.defaults;sdkconfig.kanji`、`-DSAAN_KANJI=1 -DSAAN_QEMU=1`）。QEMU の標準入力に FIFO をつなぎ、ログに `かな>` が出るたびに 1 行（末尾 `\r`）を書く。ケース:

  | ケース | 入力 | 判定 |
  |---|---|---|
  | **A 上限内の 1 文** | `今日は良い天気ですね。` | `出力 PCM` の checksum が `baseline.txt` と一致 / "区間" の行が**無い** |
  | **B 上限内の 2 文** | `今日は良い天気ですね。明日も晴れるでしょう。` | 同上（**区切っていない**こと） |
  | **D 上限超えの 3 文** | 1 文 20 形態素前後の 3 文（合計で形態素 44 超）。具体文はスクリプトに固定 | `区間 1/3`〜`区間 3/3` が出る。区間 k の checksum が、**区間 k の文を単独で入力したとき**（同じ QEMU 起動の中で続けて入力）の checksum と一致 |
  | **C 陽性対照: 区切れない長文** | 2 文目だけ `、` で 50 形態素以上続ける 3 文 | `区間 2/3` が長すぎると出る / **`出力 PCM` の行が 1 つも出ない**（全か無か） |

  合否は `ok` / `ng` を数え、最後に `n 件中 n 件` を出す。`ng` が 1 つでもあれば exit 1。

- [ ] **Step 3: 落ちることを確かめる**

  Run: `bash scripts/check_split_qemu.sh`
  Expected: A・B は OK（基準値どおり）、**D は NG**（今は「長すぎる」で拒否されるので "区間" の行が出ない）、C は「出力 PCM が出ない」は OK だが「区間 2/3」のメッセージが無くて NG。

- [ ] **Step 4: `main.c` と CMake を変える**（上の Interfaces どおり）

- [ ] **Step 5: 通ることを確かめる**

  Run: `bash scripts/check_split_qemu.sh && make -C csrc all-test && bash scripts/check_esp32_template.sh`
  Expected: `4 ケース n 件中 n 件`。A・B の checksum が基準値と一致。

- [ ] **Step 6: `check_ci_coverage.py` を回す**

  Run: `uv run --no-project python scripts/check_ci_coverage.py`
  Expected: `check_split_qemu.sh` が除外表に理由つきで出る。

- [ ] **Step 7: Commit**

  ```bash
  git add esp32/components/saanotts_core/CMakeLists.txt esp32/main/main.c scripts/check_split_qemu.sh scripts/check_ci_coverage.py
  git commit -m "feat(esp32): 上限を超える漢字文を文ごとに分けて喋る（QEMU 4 ケース・陽性対照つき）"
  ```

---

### Task 4: Arduino ライブラリに入れる

**Files:**
- Modify: `scripts/build_arduino_lib.py`（`manifest()` の「常に入る .c」に `"split.c"`、ヘッダの列に `"split.h"`）
- Modify: `arduino/src/SanoTTS.h`, `arduino/src/SanoTTS.cpp`

**Interfaces:**
- Consumes: Task 2 の `saan_split_say`
- Produces: `SanoTTS::say` / `SanoTTS::synthesize` の**公開シグネチャは変えない**。中で `saan_split_say(text, nbytes, kMaxIds, ...)` を呼ぶ。`toIds` は `SANOTTS_MAX_INPUT_BYTES` 超えと `SAAN_KANJI_ERR_TOO_LONG` を `SAAN_SPLIT_CB_TOO_LONG` として返す（`private: int toIdsCode(const char*, size_t, int32_t*)` を足し、今の `bool toIds` はそれを包む）。`synthesize` の speak コールバックは今のループ（`openStream` → pull → `cb`）をそのまま 1 区間ぶん回す。`say` は区間ごとに今のプリロール → 再生を回す
- ⚠️ `toIds` の「入力が長すぎる（SANOTTS_MAX_INPUT_BYTES を超えた）」は**全体に対しては出さなくなる**（区切って区間ごとに見る）。区間が 512 B を超えたら `ERR_SEGMENT` のメッセージで出す

- [ ] **Step 1: 失敗するゲートを確かめる**

  `build_arduino_lib.py` の manifest にだけ `split.c` / `split.h` を足した状態で
  Run: `uv run --no-project python scripts/build_arduino_lib.py --check`
  Expected: NG（`arduino/src/core/split.c` がまだ生成されていない）。

- [ ] **Step 2: 生成して `--check` を通す**

  Run: `uv run --no-project python scripts/build_arduino_lib.py && uv run --no-project python scripts/build_arduino_lib.py --check`
  Expected: G-AR1 OK（陽性対照 7 件も OK）。

- [ ] **Step 3: `SanoTTS.cpp` を変える**（上の Interfaces どおり）

- [ ] **Step 4: ビルドと PIE の命令数を確かめる**

  Run: `bash scripts/ci_arduino_build.sh`
  Expected: G-AR2 3 構成 OK / G-AR6 PIE 命令 74（非 S3 で 0）/ G-AR9 2.x の 4 構成 OK。

- [ ] **Step 5: QEMU で PCM が変わっていないことを確かめる**

  Run: `bash scripts/check_arduino_qemu.sh`
  Expected: G-AR4 が W8A8+PIE `0x390bf4b2aef8f2ec` / 27,136 sample と W8A32 `0x9cbe622a4a53af7e` / 27,648 sample のまま（上限内の 1 文 = 区切らない経路）。
  ⚠️ **Arduino の長文経路は QEMU でも実機でも鳴らしていない**ことを Task 5 の記録に書く（手順の正しさは Task 2 のホストのゲートだけが見ている）。

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/build_arduino_lib.py arduino/src/SanoTTS.h arduino/src/SanoTTS.cpp
  git commit -m "feat(arduino): say/synthesize も上限を超える文を区切って喋る（G-AR1/2/4/6/9 緑）"
  ```

---

### Task 5: 実機で測って記録する

**Files:**
- Modify: `esp32/TESTING.md`（長文の確かめ方を 1 節）
- Modify: `docs/measurements.md`（M-151）/ `docs/decisions.md`（D-070）
- Modify: `CLAUDE.md`（テスト表に `make -C csrc split` と `check_split_qemu.sh` の行）、`docs/README.md` / `CONTRIBUTING.md` / `.github/workflows/README.md`（ゲート一覧。`grep -rln "make -C csrc qeos" --include='*.md' .` で載せる場所を数える）
- Modify: `arduino/README.md`（上限の説明）

- [ ] **Step 1: 実機で測る（人: CoreS3 に焼く。約 4 分）**

  CLAUDE.md の「スタックチャン（M5 CoreS3）で漢字も喋る 1 本」の手順でビルドして焼き、シリアルに Task 3 のケース A と D を打つ。記録するもの:
  - ケース A の checksum が `v1.2.0` の実機の値と一致するか
  - ケース D の区間ごとの「アンダーラン n / m チャンク」（**区間内は 0 であること**）
  - **文と文のつなぎ目の無音**: 区間 k の `saan_audio_stop` から区間 k+1 の `saan_audio_start` までの時間をログに出し、**0.8 s 以下**であること（D-049 の「鳴らし始めまで ≤ 0.8 s」と同じ基準）。つなぎ目は今の `synth_once` のプリロール（約 0.4 s）で決まる

  ⚠️ 0.8 s を超えたら合格にしない。満たさなかったと記録して、次の区間を前もって G2P する案を別の計画にする。

- [ ] **Step 2: 記録を書く**

  M-151 に実機の表（入力・区間数・区間ごとの UR・つなぎ目 ms・checksum）と、**見ていないもの**（Arduino の長文経路は鳴らしていない / Web は対象外 / 聴いていない）を書く。D-070 に規則（開始記号 5 つ・`.` を切らない・全か無か・長さ以外では区切らない）とその理由を書く。

- [ ] **Step 3: ドキュメントのゲートを回す**

  Run: `uv run python scripts/check_doc_counters.py && uv run python scripts/check_doc_links.py && uv run --no-project python scripts/check_doc_commands.py && uv run --no-project python scripts/check_doc_claims.py && uv run --no-project python scripts/build_measurements_index.py --check`
  Expected: すべて OK（索引は `build_measurements_index.py` で生成し直してから）。

- [ ] **Step 4: Commit して PR を作る**

  ```bash
  git add esp32/TESTING.md docs/measurements.md docs/decisions.md CLAUDE.md docs/README.md CONTRIBUTING.md .github/workflows/README.md arduino/README.md
  git commit -m "docs: 長文分割の実機測定（M-151）と規則（D-070）"
  git push -u origin feat/long-text-split
  gh pr create --base main --title "feat: 上限を超える漢字文を文ごとに分けて喋る（D-070 / M-151）"
  ```
