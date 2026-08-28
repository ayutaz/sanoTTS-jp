# ESP32-S3 プロジェクト雛形（c'-4）

`csrc/` の C99 推論コアを ESP-IDF アプリとして起動し、ストリーミング API で
合成しながら I2S に流す雛形。

---

## ⚠️ 一度もビルドしていない

**これを最初に読むこと。**

この雛形を書いた環境に **ESP-IDF も xtensa toolchain も無い**
（`idf.py` 無し / `IDF_PATH` 空 / `~/.espressif` 無し）。数 GB の
インストールはユーザー承認が要るのでしていない。

したがって次のことは **1 つも検証していない**:

| # | 未検証なこと | いつ分かるか |
|---|---|---|
| 1 | `idf.py build` が通るか | toolchain が入った日 |
| 2 | flash に焼けるか / 起動するか | 実機が来た日 |
| 3 | 実際の SRAM 消費（IDF + FreeRTOS + I2S DMA 込みの free heap） | 実機 |
| 4 | 実際の xRT とアンダーラン — **M-43 の 2.47 × RT は外挿であって実測ではない** | 実機 |
| 5 | I2S の実サンプルレート誤差（**ESP32-S3 に APLL が無い**） | 実機 + オシロか長時間録音 |
| 6 | flash から mmap した重みが D-cache を thrash しないか | 実機 |
| 7 | `sdkconfig.defaults` のオプション名が実在するか | `idf.py menuconfig` |
| 8 | `esp32/main` が呼ぶ **IDF API の綴りが本物と一致するか** — ホスト stub は自作 | `idf.py build` |

**「たぶん動く」とは書かない。「未検証」と書く。**
`idf.py build` が一発で通ることは期待していない。**通らなかった箇所を直す**のが
実機初日の最初の作業。

### では何を確かめたのか

`bash scripts/check_esp32_template.sh` が通ることだけ。中身は 9 つ:

| # | ゲート | 何が言えるか |
|---|---|---|
| 1 | コア 4 ファイル **+ `g2p.c`** が `-std=gnu17 -O2 -Wall -Wextra -Werror` で警告 0 | IDF 既定の方言でコンパイルは通る |
| 2 | `nm -u` に malloc / free / fopen / mmap / POSIX / printf が **1 つも無い** | コアはベアメタルに載る形をしている |
| 3 | csrc のホスト専用 API はすべてテストコード側 | コアとテストの境界が守られている |
| 4 | blob の全テンソル offset が 16 の倍数 | **base さえ 16 バイト境界なら**全テンソルが揃う |
| 5 | CMake 3 本の構文が通る | 括弧・パスの誤りは無い（**configure ではない**） |
| 6 | `partitions.csv` の境界・重なり・容量 | blob が入る配置になっている |
| 7 | `main.c` が呼ぶ `saan_*` が csrc のヘッダに実在 | typo は無い |
| 8 | ホスト stub ビルド + **C コアと bit 完全一致** | アプリ側ロジックが正しい（下記） |
| 9 | IDF API の棚卸し | 実機で最初に照合する一覧 |

ゲート 8 が実質の主検証。`esp32/host_stub/` に IDF API の偽ヘッダを置いて
`esp32/main/*.c` をそのままホストでビルドし、I2S に書いたはずの int16 を
`csrc/` の一括版 `saan_synthesize` の出力と突き合わせる:

```
OK  [厳密] C 一括版 → int16 と 27136 sample **bit 完全一致**   （fp32 blob）
OK  [厳密] C 一括版 → int16 と 27136 sample **bit 完全一致**   （int8 blob）
```

これで **フレーム数とサンプル数の取り違え / 端数チャンクの落とし /
プリロールの順序 / int16 変換 / arena サイズ / 下限ガード** は全部潰れている。
残るのは IDF と実機の側だけ。

⚠️ ホスト stub のヒープ・スタック値は stub が 0 を返しているだけで、
**実機の値ではない**。

---

## 前提

| 項目 | 値 |
|---|---|
| ターゲット | **ESP32-S3**（内部 SRAM 512 KB / 8 MB flash を想定） |
| ESP-IDF | **v5.x を想定**。新 I2S ドライバ `driver/i2s_std.h` を使う |
| 音声出力 | I2S DAC（MAX98357A / PCM5102 など）22.05 kHz / 16 bit / mono |

⚠️ **どのマイナーバージョンで通るかは未検証**（`idf.py` がこの環境に無い）。

⚠️ `main/CMakeLists.txt` の `REQUIRES` は **`driver` 1 本**にしてある。
**存在しないコンポーネント名を書くと IDF はエラーで止まる**ので、
「両方書いてどちらかに当てる」という逃げは効かない。`driver` は v5.x を通して
残っている傘コンポーネントで、I2S が `esp_driver_i2s` に分割された版でも
そこへ依存する形になっている**はず** — ⚠️ **この環境に IDF が無いので未確認**。
ビルドが落ちたらここを最初に疑う。

---

## ビルドと書き込み

```bash
# 1. 重み blob と demo_ids.h を用意する（リポジトリのルートで）
uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt   # csrc/student.bin
uv run python scripts/gen_demo_ids.py                                # esp32/main/demo_ids.h

# 2. 手元のゲートを通す（ESP-IDF 不要）
bash scripts/check_esp32_template.sh

# 3. ビルド（**ここから先は未検証**）
cd esp32
idf.py set-target esp32s3
idf.py build

# 4. パーティション表・アプリ・重み blob をまとめて焼く
idf.py -p /dev/tty.usbmodemXXXX flash monitor
```

`idf.py flash` は `esptool_py_flash_to_partition(flash "model" ...)` によって
重み blob も `model` パーティションへ一緒に焼く（トップの `CMakeLists.txt`）。

---

## どちらの blob を焼くか

| blob | サイズ | 状態 |
|---|---:|---|
| `csrc/student.bin` (fp32) | 2,249,792 B | **既定**。ホスト stub で C コアと bit 一致を確認 |
| `csrc/student_i8.bin` (int8) | 643,936 B | ホスト stub で C コアと bit 一致 / Python 参照と SNR 26.08 dB |

切り替え:

```bash
idf.py -DSAAN_MODEL_BLOB=$PWD/../csrc/student_i8.bin build
```

⚠️ 既定を fp32 にしてあるのは**保守的な選択**であって、int8 が動かないからではない。
int8 経路は c'-1 / c'-2 で通るようになったばかり（このタスクの実行時点では
まだコミットされていない作業ツリーの状態）。**flash と D-cache のためには
int8 のほうが 3.5 倍有利**なので、c'-1 / c'-2 が確定したら既定を切り替えること。

⚠️ int8 blob は**量子化で波形が変わる**。Python 参照との SNR 26.08 dB は
M-39 の PTQ 実測（≥ 25 dB）と同水準で、**劣化ではなく想定どおり**。

---

## 設計上の判断（踏み抜きやすい順）

### 1. 重みは flash に置き、SRAM にコピーしない

`esp_partition_mmap()` で `model` パーティションを読む。blob は 643,936 B
（int8）〜 2,249,792 B（fp32）で、512 KB の SRAM には入らない。
コアは blob を書き換えないので read-only で足りる。

### 2. `EMBED_FILES` を使わない — **アライメントが無保証**

⚠️ **以下は ESP-IDF の公式 cmake を読んで得た事実で、この環境には IDF が無いので
再確認していない。実機初日に一次ソースで確かめること。**

`idf_component_register(EMBED_FILES ...)` は `target_add_binary_data()` を
**ALIGN 引数なしで**呼び、`data_file_embed_asm.cmake` は `DATA_ALIGNMENT` が
未定義なら `.balign` を一切出さない。つまり **blob 先頭が 4 バイト境界に落ちる
保証が無い**。

本コアは payload を `const float*` へ直接キャストする（テンソル offset は
全部 16 の倍数なので、ずれるとしたら base だけ）。Xtensa は非アラインの
4 バイトロードで `LoadStoreAlignmentCause` になる。しかも**リンク結果次第で
たまたま揃うことがあり、無関係な変更で突然落ちる**種類の事故になる。

パーティションなら `esp_partition_mmap()` が 64 KB 境界（MMU ページ）に丸めて
マップするので、`partitions.csv` で offset を 64 KB 境界に置けば 16 バイト境界も
自動的に満たす（将来 PIE を使うときの `SOC_SIMD_PREFERRED_DATA_ALIGNMENT` = 16 も
これで足りる）。⚠️ **この丸めの挙動も IDF ソース由来で、この環境では未確認。**
だから `saan_model.c` は横着せず**実行時に 16 バイト境界を検査して落とす**。
`saan_model.c` は取得直後に **16 バイト境界を検査して落とす**。

どうしても埋め込みたいなら、トップの `CMakeLists.txt` で
`target_add_binary_data(${project_name}.elf "../csrc/student.bin" BINARY ALIGN 16)`
と **ALIGN を明示**すること。

### 3. arena は 208 KB を `.bss` に静的確保する

⚠️ **`saan_stream_arena_needed()` の戻り値を使ってはいけない。**
n_ids=350 に対し **340,016 B (332 KB)** を返す緩い上限で、512 KB の SRAM の
65% を占めてしまう。

⚠️ **CLAUDE.md / M-42 の「197 KB」もそのまま確保量にしてはいけない。**
あれは高水位（`st.peak_used` = 197,424 B）で、**init が通る最小 arena は
197,632 B**。ALIGN16 の切り上げと確保順の差でわずかに上回る。

208 KB (212,992 B) の根拠は**実測**（`make -C csrc arena`、ホスト）:

| 項目 | 値 |
|---|---:|
| `saan_stream_arena_needed(350)`（緩い上限） | 340,016 B |
| n_ids=350 で init も pull も通る最小 arena | **197,632 B** |
| 208 KB で通る最大 n_ids | **520** |
| n_ids ≥ 560 | `SAAN_ERR_ARENA` で**きれいに失敗** |
| n_ids 1〜1000（23 点）でのクラッシュ | **0 件** |

設計上限は 350 ids（D-017 の `max_spec_length=700` 相当）なので 15,360 B の余裕。

### 4. `saan_stream_init` の**未修正の欠陥**に対する二重防御

⚠️ **これは既知の未修正欠陥で、このタスクでは直していない**
（`csrc/saanotts.c` と `csrc/saanotts_stream.c` は c'-1 / c'-2 が並行で
編集中だったため触っていない）。

`saan_alloc` は失敗しても `used` を進めずに NULL を返す。`saan_stream_init` は
確保を約 25 回するのに**各グループの最後の 1 個しか NULL 検査していない**。
そのため「大きい確保（`o1539` 49,248 B など）だけ失敗し、後続の小さい確保は成功」
が起きると、**init が `SAAN_OK` を返したまま壊れた状態**になり、後で
`saan_stream_pull` の中で NULL 書き込みになる。手元では SEGV、ESP32 では
**StoreProhibited パニック = ログも出さずに再起動**。

実測（`make -C csrc arena`）: n_ids=350 / arena 150〜260 KB を 1 KB 刻みで
走らせると、**175〜191 KB の 15 サイズでクラッシュ**する。
180 / 186 / 192 KB はたまたま clean fail するので、**刻みが粗いと見逃す**。

修正案（**1 箇所で済む**）— `saan_arena` に粘着フラグを足す:

```c
/* saanotts.h */
typedef struct { uint8_t *buf; size_t size, used, peak; int failed; } saan_arena;

/* saanotts.c */
void *saan_alloc(saan_arena *a, size_t n) {
    size_t need = ALIGN16(n);
    if (a->failed) return NULL;                                   /* ← 追加 */
    if (need < n || a->used + need > a->size) { a->failed = 1; return NULL; }
    ...
}
/* saan_arena_init / saan_arena_reset で a->failed = 0 */
```

これで各グループ末尾の既存の NULL 検査が正しく効くようになる。

**雛形側の二重防御**（`main.c`）: init が `SAAN_OK` を返した後に `a.used` を検査する。

| 状態 | `a.used`（実測） |
|---|---:|
| 正しく init できた | 194,640 B (n_ids=1) 〜 198,768 B (n_ids=520) |
| 黙って確保に失敗 | 178,992 / 185,136 / **191,280** B |

閾値は `SAAN_ARENA_USED_FLOOR = 192,960 B`（中点）。誤検知も見逃しも無い。
⚠️ **コアの確保順が変わったら `make -C csrc arena` で測り直すこと。**

⚠️ arena を 208 KB にしていれば**クラッシュ帯 175〜191 KB には入らない**ので、
この防御は保険。それでも欠陥自体は直すべき。

### 5. タスクスタックは 16 KB を明示する

`saan_irfft_1024` は自動変数 `zr[512] + zi[512]`（float）だけで **4,096 B** 使う。
arm64 / clang -O2 での実フレームは **4,224 B**（`sub sp,sp,#0x1000` + `sub sp,sp,#0x20` +
レジスタ退避 96 B。`otool -tv` で実測。⚠️ Xtensa では別の値になる）。

`CONFIG_ESP_MAIN_TASK_STACK_SIZE` の既定は 3,584 B と言われているが、
⚠️ **この環境に IDF が無いので Kconfig の既定値そのものは確認していない**。
確かなのは「iSTFT 1 回で 4 KB 超を使う」ほうで、これだけで小さいスタックは
足りない。合成は `app_main` ではなく専用タスク（`SAAN_TASK_STACK = 16384`）で回す。

さらに `float chunk[SAAN_CHUNK * SAAN_HOP]`（8,192 B）と int16 変換バッファは
**`static` にしてスタックから外してある**。M-42 が arena だけ見て 200 KB を
切っていたのと同じ間違いを、今度は FreeRTOS のタスクスタックで繰り返さないこと。

⚠️ 16 KB が適切かは**未測定**。実機で `uxTaskGetStackHighWaterMark` を見て詰める
（`main.c` が終了時にログへ出す）。

### 6. I2S を enable する前にプリロールする

**最初の `saan_stream_pull` だけ定常の約 6 倍かかる**
（ホスト実測 n_ids=350 で 12.21 ms vs 2.04 ms、比 6.0）。受容野 36 +
iSTFT 2 = 38 フレームの warmup で内部の `step_chunk` が複数回走るため。

`i2s_channel_enable()` の直後から合成を始めると、その 1 回ぶんが確実に
アンダーランになる。`saan_i2s_preroll_push()` で 4 チャンク（371 ms・16 KB）
先に計算してから `saan_i2s_start()` を呼ぶ。

### 7. `-std=c99` を component に足さない

newlib の `M_PI` は `__STRICT_ANSI__` の下で隠れることがあり、
`csrc/saanotts.c:412` と `csrc/saanotts_stream.c:398` が `M_PI` を無条件に使う
（Hann 窓の生成）。IDF 既定の **gnu17 のまま**にする。

⚠️ `csrc/Makefile` が `-std=c99` なのを見て IDF 側にも写す、という自然な操作が
地雷になる。**macOS の libc では `-std=c99` でも `M_PI` が見えるので手元では
再現しない**種類の失敗。

同じ理由で `SAAN_FFT_DOUBLE` と `SAAN_USE_NAIVE_DFT` は**定義しない**
（S3 の FPU は単精度のみ。naive DFT は double の `cos`/`sin` を使うが、
`-O2` では未定義のままデッドコード除去されることを `nm -u` で確認済み）。

### 8. コアの 4 ファイル + `g2p.c` は csrc から**直接参照**する

`components/saanotts_core/CMakeLists.txt` が相対パスで `csrc/` を指す。
**コピーもシンボリックリンクもしない** —「同じものを 2 か所に書かない」。

⚠️ **4 ファイルすべてが要る。** `saanotts_int8.c` に `saan_w` /
`saan_conv1d_w` / `saan_dwconv1d_w` / `saan_act_scratch_needed` があり、
`saanotts.c` と `saanotts_stream.c` の両方が参照する。3 ファイルではリンクできない。

⚠️ `g2p.c` は**コアを 1 行も参照しない独立の翻訳単位**だが、`main.c` が
`saan_g2p()` を呼ぶのでここに並べる。`csrc/Makefile` の `CORE` には入っていない
（`golden_test` 等はリンクしない）。**非対称なのは意図的。**

---

## 実機で最初に測ること

`main.c` が起動時と終了時にログへ出す。**この順で見る。**

| # | 見るもの | ログ行 | 判断 |
|---|---|---|---|
| 1 | 起動するか | `重み OK: N tensors` | ここで止まるなら partition / アライメント |
| 2 | **アンダーラン** | `アンダーラン N / M チャンク` | **0 でないのが想定どおり**（下記） |
| 3 | **定常 xRT** | `定常 xRT = X` | **1.0 を超えたら実時間に間に合っていない** |
| 4 | 初回 pull と定常 pull の比 | `初回 pull ... / 2 回目以降 mean ...` | ホストでは 6.0 倍。プリロール量の根拠 |
| 5 | **内部 DRAM の残り** | `終了時: 内部 DRAM free ...` | 208 KB の arena を引いた後どれだけ残るか |
| 6 | タスクスタックの残り | `タスクスタック残り N B` | 16 KB を詰められるか |
| 7 | int16 クリップ | `int16 クリップ N sample` | 0 でないなら出力が飽和している |
| 8 | 実サンプルレート | ログに出ない | **オシロか長時間録音で測る**（S3 に APLL 無し） |

### ⚠️ 「雛形が動いた」＝「実時間で喋れた」ではない

M-43 の外挿（実測 η_host = 0.364 を転移）では、移植可能 C / fp32 は
**2.47 × RT**。1 チャンク（音声 92.88 ms）の計算に約 229 ms かかる勘定になる。
**DMA を何段積んでもスループット不足は埋まらない。**

つまり **アンダーランが出るのが期待値**で、この雛形の役目は
「それを正しく観測してログに出すこと」。実時間化には **int8 + PIE** が要る、
というのが M-43 の結論であり c'-3 の課題。

---

## この雛形がやっていないこと

### 端末側 G2P は**入った**（が、実機では動かしていない）

`csrc/g2p.c` + `csrc/g2p_table.h`（自動生成）。`main.c` は
`SAAN_DEMO_INTERMEDIATE`（かな中間表現 44 B）を `saan_g2p()` に通し、
**その出力を合成に使う**。`kSaanDemoIds` は入力ではなく**答え合わせの錨**で、
食い違ったら合成せずに `ESP_LOGE` で止まる。

⚠️ **手元で確かめたのはホスト stub までで、実機では 1 度も走らせていない。**
ホストでは以下を確認した:

* `make -C csrc g2p` — Python (`scripts/kana_g2p.py`) と **ids が整数として完全一致**
  （自己完結ベクタ 2,789 件）。`make -C csrc g2p-corpus` ではコーパス全行込みで
  **26,235 / 26,235**
* ホスト stub で `saan_g2p()` の 53 ids が `demo_ids.h` の錨と一致し、
  合成結果が C 一括版と **bit 完全一致**
* 錨を 1 要素、中間表現を 1 文字だけ変えると**どちらも exit 1 で落ちる**ことを確認

⚠️ **実機のレイテンシは未測定。** 手元（M4 Max / arm64 clang -O2）の定常値は
**44 B → 53 ids で 0.34〜0.54 us、450 B → 543 ids で 4.7 us**（20 万回ループ・
結果を volatile に足して最適化除去を防いだ値、2 回実行のばらつき込み）。
合成 1 チャンク（92.88 ms の音声）に対して**無視できる**。
⚠️ ホスト stub のログに出る `0.05 ms` は**冷えた 1 回目**で、定常値ではない。
⚠️ ESP32-S3 の値ではない。

⚠️ **漢字は端末で扱わない**（D-010 / D-011）。中間表現を作るのはホスト側
（OpenJTalk）で、`scripts/gen_demo_ids.py` がそれをやっている。
**「文字列を渡せば喋る」ところまでは行っていない。**

### そのほか

- **PIE (SIMD) カーネル** — c'-3。移植可能 C のまま
- **`saan_tf` のポインタ解決** — コアは毎チャンク `saan_tf()` を多数回呼び、
  1 回ごとに `vsnprintf` + 183 エントリの線形 `strncmp`（ヘッダ 19,048 B）を走る。
  ホストの 0.022 × RT にはこのコストが既に含まれているが、ESP32 では 64 KB の
  D-cache を重みと奪い合う。**実機で遅かったときの改修の第一候補**
  （init 時に一度だけ解決して構造体に持つ）。⚠️ 未検証
- **GPIO 配線** — `saan_i2s.c` の `SAAN_I2S_GPIO_*` は**根拠のない仮置き**。
  自分のボードに合わせて変えること

---

## ファイル

| パス | 役割 |
|---|---|
| `CMakeLists.txt` | トップ。`model` パーティションへの blob 焼き込みもここ |
| `partitions.csv` | カスタムパーティション表（既定では blob が入らない） |
| `sdkconfig.defaults` | ターゲット / 最適化 / スタック / パーティション |
| `components/saanotts_core/CMakeLists.txt` | `csrc/` の 4 ファイル + `g2p.c` を直接参照 |
| `main/main.c` | arena・プリロール・合成ループ・計測ログ |
| `main/saan_model.{h,c}` | flash mmap → `saan_weights`（16 バイト境界を検査） |
| `main/saan_i2s.{h,c}` | I2S 設定 / プリロール / float→int16 |
| `main/demo_ids.h` | **自動生成**（`scripts/gen_demo_ids.py`）。中間表現 + 錨 ids |
| `host_stub/` | IDF API の偽ヘッダ + 実装。**デバイスには載らない** |

検査スクリプト（リポジトリのルートから）:

```bash
bash scripts/check_esp32_template.sh    # 9 ゲート全部
uv run python scripts/check_partitions.py
cmake -P scripts/check_cmake_syntax.cmake
make -C csrc arena                       # arena の実測（既知の欠陥で現状 exit 1）
```
