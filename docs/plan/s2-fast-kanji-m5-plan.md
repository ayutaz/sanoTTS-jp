# S2 計画 — CoreS3 のスタックチャンで漢字・カタカナ・ひらがなを **RTF ≤ 0.5・少メモリ**で喋る

作成 2026-09-03。前計画 [`s1-speed-implementation-plan.md`](s1-speed-implementation-plan.md)（S1〜S5a まで完了、M-81 / M-82）と
K 計画 [`k1-kanji-implementation-plan.md`](k1-kanji-implementation-plan.md)（K-8 の G28〜G31 完了、M-83）の続き。
両トラックを **1 台の CoreS3 で 1 本のファーム**に合流させ、そのうえで速度とメモリを詰める。

> **For agentic workers:** 各タスクは 1 コミット。ゲートは「何を実行して何が出れば合格か」と**陽性対照**（こう壊すと落ちる）を必ず持つ。
> 速度の判断は **実機の `-DSAAN_PROFILE=1` の表**だけで行う（QEMU の割合は実機と別物。M-80 → M-82 の教訓）。
> 数値は measured / estimate を区別する。**測るまで書かない。**

## 0. 目標と現在地

| | 現在地（**measured**） | 到達値 | 出典 |
|---|---|---|---|
| 定常 1 step（8 frames = 92.88 ms 音声） | **76.58 ms**（W8A8+PIE、M5 構成） | **≤ 46 ms**（RTF 0.5） | M-82 §3 |
| 表示 xRT（pull 2 回目以降の平均） | 0.926 | ≤ 0.5 | M-82 §2 |
| 内部 DRAM free（M5 構成、起動直後 / 1 発話後） | 101,651 / 99,315 B（最大ブロック 55,296） | 漢字経路を足しても **≥ 40 KB** を残す（estimate の目安） | M-82 ログ |
| arena（静的 .bss） | 212,992 B（used 195,808） | 減らす（下記 T2〜T4 で約 −50 KB の estimate） | M-82 |
| 漢字 G2P（DevKit 構成・W8A32） | 27.85〜66.30 ms / 33〜84 B | 目安を **絶対値**で決め直す（§7） | M-83 |
| 漢字 + PIE + M5 スピーカーの 1 本のファーム | **存在しない** | 存在して checksum が `0xa69a7ebbb5ccb05f` | — |
| 入力 | かな行 / `!漢字行` の 2 経路 | **1 経路**（自動判定） | — |

## 1. 調査で直った前提（M-82 §4 に書いた仮説のうち 4 つ）

2026-09-03 に 6 視点でコードと ELF（`build_cores3` の objdump / map）を読み直した結果。**全部コードから数えた値**で、cyc の内訳だけが estimate。

| M-82 §4 の前提 | 調査結果 | 影響 |
|---|---|---|
| 「1 step に約 90,000 回の dot」 | **109,152 回**（呼び出し箇所 × 層の T から数えた。MAC 区間の回数 Σcout = 4,715 が実機 4,724 と 0.2% 差で列挙は正しい） | 固定費の見積りが 2 割増える |
| 「GELU 118 cyc/要素は表が flash にあるせい」 | 表の寄与は **≈2%（estimate）**。主因は `saan_erf_approx` が **要素ごとに call8**（インライン化されていない）、FP 定数 13 個を毎要素 l32r+wfr、戻り値のスタック往復、FP 比較分岐 2 回。要素あたり ≈80 命令で CPI 1.5 | 表を DRAM に置くだけでは下がらない。**コード生成を直す**（T5） |
| 「MAC は flash 律速」 | 3 成分が**同じ桁**（estimate）: flash 行フィル ≈17,000 行/step（3.3〜5.1 M cyc）/ dot 固定費（≈3.7 M cyc）/ srs→float→madd の直列チェーン（3〜4 M cyc）。PIE 命令の発行自体は ≤ 10% | 1 つだけ直しても 46 ms に届かない。**P-0 で比を測ってから S7 か S5b を選ぶ** |
| 「xRT 0.926 が定常」 | 末尾の pull で **全フレームが出そろった後も step_chunk が 3 回回る**（21 step 中 3 = 14%）。満チャンク 1 step は 76.58 ms → **0.82**。「アンダーラン 1/14」もこの末尾 pull | 表示の定義を直す（T1）。**速度が上がったのではない**ので C-番号で記録する |

さらに 2 つ、計画に無かった大きな事実:

- **MAC の 37%・GELU の 42% が「捨てられる出力」の計算**。AC ブロックは窓 16 のうち中央 8 しか下流に渡さず、DEC ブロックは窓 14 のうち中央 8。1×1 conv / LN / GELU / per-frame 量子化はフレーム独立なので、**計算範囲を中央に絞っても bit 同一**（T2 = S9）。
- **TOKEN 11.3% は毎 step ±12 トークンのハローを丸ごと再計算**している（必要 87 フレーム分に対し 171 を計算）。トークン単位の持ち越し（S6）で bit 同一のまま減らせる（T3）。

## 2. 順序の原理

1. **bit 同一の削減を先に、まとめて 1 回の実機で測る**（T1〜T4）。checksum が `0xa69a7ebbb5ccb05f` のまま step が縮むことを実機で見る。
2. **丸め水準になりうる変更は別コミット**（T5 の GELU）。QEMU の checksum で bit 同一か丸め水準かを確定し、動いたら新基準を記録する。
3. **仮説を棄却する測定を同じ実機セッションに入れる**（T6 = P-0 マイクロベンチ）。結果で §5 の分岐を選ぶ。
4. **漢字の合流は速度と独立に進められる**（T9〜T13）が、内部 DRAM を食うので T2〜T4 でメモリを空けた**後**に載せる。
5. **G2P の速度（T14〜T17）は RTF に 1 ms も寄与しない**。効くのは「最初のチャンクが鳴るまでの遅れ」だけ。順番は最後。

## 3. タスク

各タスク: 目的 / 変更 / 期待効果 / bit / メモリ / ゲート / 陽性対照 / 実機 / 依存。

### T1. pull ループの早期終了と xRT の定義（ST-1 + PROF-1）

- **目的**: 全フレームが `obuf` に出そろった後に回る 3 step/発話を止める。表示 xRT を満チャンク step の値にする。
- **変更**: `csrc/saanotts_stream.c` の pull ループ `while (ofill < CH)` に「`emitted + ofill < n_frames`」を足す。`esp32/main/main.c` の `mean_rest` を満チャンク pull の中央値にし、pull ごとの ms をログ。`prof_report` で LOOKUP 行を INIT 側へ、DW 行の QUANT 重複を注記。
- **期待効果**: 発話あたり step 21 → 18（−14%、約 −230 ms。**estimate**）。定常 step は不変。末尾のアンダーラン判定が消える。
- **bit**: 同一（出力サンプル列は不変）。
- **メモリ**: 0。
- **ゲート**: `make -C csrc all-test`（stream G2: 一括版と 27,136 sample bit 一致）/ `./csrc/prof_test` の step_chunk 回数が 3 発話で 63 → 54。QEMU checksum 不変。
- **陽性対照**: 条件を 1 フレーム早く（`< n_frames − 1`）すると末尾 1 フレームが欠けて G2 が落ちる。
- **実機**: 表の読み替えのみ。セッション 1 で pull ごとの ms を確認。
- **依存**: なし。

### T2. 有効範囲だけ計算する（S9）

- **目的**: 捨てられる出力を計算しない。MAC −37% / GELU −42% / DW −43% / QUANT・LN −35〜45%（要素数は exact、時間は estimate）。
- **変更**: `saan_conv1d_w` / `saan_conv1d_i8a` / `saan_dwconv1d_*` / `saan_layernorm_c` / `saan_gelu` / `saan_relu` に出力範囲 `[t0, t1)` を渡せる形を足す（fp32 経路 `saan_conv1d` も同じ関数を通す。2 回書かない）。`ac_step_body`: c1 は `[2, W−2)`、c2 / LN / 残差は中央 CH だけ。`dec_step_body`: cdown / cup / dw は窓全部（dw の入力に要る）、**pw1 / GELU / pw2 / 残差は中央 CH だけ**。`compute_tokens_body`: 各 conv で 2 ずつ縮む有効範囲だけ。
- **期待効果**: MAC 7.28 M → 約 4.56 M/step、GELU 21,664 → 12,544 要素。時間は「flash 行フィル分は減らない」ので 1 step 18.4 M → 13.4〜14.7 M cyc（**estimate**、xRT 0.82 → 0.60〜0.66）。
- **bit**: 同一（各出力要素の積和順・per-frame 量子化・per-frame LN は不変）。
- **メモリ**: `w_e` を `[304][CH]`、`w_g / w_r / w_full2` を `[C][CH]` に縮めて arena 約 −12 KB（estimate）。
- **ゲート**: stream G2 bit 一致 / `make -C csrc prof` の MAC 要素数 ≈ 4.56 M/step・GELU 12,544/step / QEMU checksum `0xa69a7ebbb5ccb05f`・`0xe4b645c30835d42d` 不変 / int8 / golden / fft は無変更で通る。
- **陽性対照**: c1 の範囲を `[3, W−2)` に 1 フレーム狭めると G2 が落ちる（中央 8 の先頭が壊れる）。
- **実機**: セッション 1 で MAC / GELU の cyc/step。
- **依存**: T1（step 回数が変わるので prof の「/step」を先に安定させる）。

### T3. token ブロックをトークン単位のパイプに（S6）

- **目的**: TOKEN 11.3% を「新しく必要になったトークン分だけ」に。
- **変更**: `compute_tokens_body` の「毎チャンク ±12 幅で丸ごと再計算」を、フレーム側と同じステート保持型 pipe（3 段、pad 4、K トークンずつ進める）にし、出力を `[48][K + CH + 1]` のリングに持つ。`make_hf` は必要なトークンが揃うまで pipe を進める（ids は全部既知）。
- **期待効果**: TOKEN の MAC 1.31 M → 約 0.55 M/step（K=8。T2 併用で約 0.35 M）。時間 2.08 M cyc → 0.6〜0.9 M（**estimate**）。
- **bit**: 同一（token ブロックの出力はトークン位置に依存しない。フレーム側の pipe が M-42 G2 で同じ理屈を通している）。
- **メモリ**: `tok_buf / w1 / w2 / tok_out` 19,968 B → 約 12 KB（−8 KB、estimate）。
- **ゲート**: stream G2 / prof の TOKEN 要素数と回数 / QEMU checksum 不変。
- **陽性対照**: ハローを 11 にすると G2 が落ちる（受容野 12 に足りない）。
- **実機**: セッション 1 の TOKEN 行。
- **依存**: T2。

### T4. arena の詰め（MEM-1 + MEM-2）

- **目的**: 漢字経路と S7 のために内部 DRAM を空ける。
- **変更**: (a) `cdel[0..5]`（同じ c を 6 本の pipe に別々の遅延で持つ、12,800 B）を 1 本のリング `[40][16+CH]` に。(b) iSTFT の `re / im / frm`（8,224 B）を `w_e` と重ね合わせる（生存期間が重ならない）。(c) `step_chunk_body` の static `h_tmp / c_tmp`（3,712 B .bss）と 5 段の memcpy をポインタ交換に。(d) `obuf` を `(CH+4)` hop → `(CH+2)`。
- **期待効果**: arena −19 KB（8,960 + 8,224 + 2,048）、.bss −3,712 B（**式で出した値**。`make -C csrc arena` で実測して置き換える）。PIPE 2.2% の 1/3 と memcpy 18.5 KB/step も消える（僅少）。
- **bit**: 同一（純粋なデータ移動）。
- **ゲート**: stream G2 / `make -C csrc arena`（下限を測り直し、`SAAN_ARENA_BYTES` と `SAAN_ARENA_USED_FLOOR` を更新）/ QEMU checksum 不変。
- **陽性対照**: リングの遅延位置を 1 つずらすと G2 が落ちる。
- **依存**: T2, T3（バッファ形が決まってから）。

### T5. GELU のコード生成を直す（G-3 → G-4 → G-1 → G-2）。**別コミット**

- **目的**: 118 cyc/要素 → 35〜50（estimate）。
- **変更**（この順で 1 つずつ。どれが checksum を動かすか切り分けるため）:
  - **G-3** 分岐 2 つを消す: 符号は `bits(y) | (bits(x) & 0x80000000)`（`x<0 ? −y : y` と全有限値で一致。`x = −0.0` で erf が −0.0 になるが `1.0f + (−0.0f) = 1.0f` で GELU 出力は同一）。クランプは `ax = (ax < 4.0f) ? ax : 4.0f` にして表を引く（`kSaanErfV[128] = 0.999999985f` は float で 1.0f。t=1 の Hermite 基底は (0,0,1,0) を正確に出す）。
  - **G-4** 導関数表を `h = 2^-5` で事前スケール（2^-5 倍は float で正確。生成器は **float32 で掛けてから** `%.9g`）。
  - **G-1** `saan_erf_approx` を `static inline` にして `saan_gelu` に展開（`erf_test.c` 向けの外部ラッパは残す）。
  - **G-2** `SAAN_HOT_DATA` / `SAAN_HOT_CODE` マクロ（既定空）を `csrc/saanotts_internal.h` に置き、`esp32/components/saanotts_core/saan_port_esp32.h`（新規、`esp_attr.h` を include して `DRAM_ATTR` / `IRAM_ATTR` に定義）を CMake の `-DSAAN_PORT_HEADER` で注入。表 1,032 B を DRAM に。
- **期待効果**: GELU 2.56 M → 約 0.9 M cyc（T2 併用で約 0.5 M。**estimate**）。
- **bit**: **式は 1 文字も変えないので bit 同一のはず**だが、IDF は gnu17 で `-ffp-contract=fast` のため、インライン化で madd.s への縮約が変わると**丸め水準で動く**。⚠️ ホストでは検出できない（丸めが違う）。**QEMU の checksum で判定**し、動いたら |max| 一致 + Σx² 相対差（1e-7 級）+ W8A8 の fp32 比 SNR 分布で丸め水準を示して新基準を M-番号に。
- **メモリ**: 内部 DRAM −1,032 B（G-2）。
- **ゲート**: `make -C csrc erf`（max|Δ| ≤ 2e-7、線形補間の陽性対照）+ **旧実装との全格子 bit 一致チェックを 1 本足す**（x = ±0.0 / ±4.0 / ±1e30 / 節点 / 中点）/ all-test / QEMU checksum。
- **陽性対照**: 全格子チェックは、G-3 のクランプを `3.9999f` にすると落ちる。
- **依存**: なし（T1〜T4 と独立。ただし別コミット）。

### T6. マイクロベンチ 2 本（P-0 + GELU）。**測定であって変更ではない**

- **目的**: MAC 1.61 cyc/MAC の内訳（flash 行フィル / dot 固定費）と、GELU の改善幅を、実機で数字にする。§5 の分岐を決める。
- **変更**: `esp32/pie_probe` に **D 節**を足す。本物の blob を `.rodata` に埋め、decoder の連続 ≥ 131 KB を cin=48 / k=1 / cout=2,730 の行列と見なして、同じ `saan_conv1d_i8a` を **D1** hot（cout=16 を繰り返し = 全部キャッシュヒット = 固定費の床）/ **D2** DRAM ストリーム（memcpy した 131 KB）/ **D3** flash ストリーム / **D4** PSRAM ストリーム（`CONFIG_SPIRAM=y` 変種）/ **D5** D3 を T=16 で、の 5 条件で回し CCOUNT を取る。GELU は同じ 21,664 要素を「flash 表 / DRAM 表 × インライン無 / 有」の 4 条件。
- **出るもの**: cyc/行（D3 − D2）/ cyc/dot の床（D1）/ PSRAM の差（D4）/ T 倍増の効き（D5）。
- **ゲート**: D2 / D3 / D4 の y が memcmp 一致 + **重みを 1 バイト壊した陰性対照が不一致** / 5 回の min-max 幅 < 1%（CCOUNT は決定的）。
- **メモリ**: probe のみ（本番ファーム不変）。
- **実機**: セッション 1。結果を **M-85** に。
- **依存**: なし。

### 実機セッション 1（1 回の接続でやること）

1. `esp32/boards/m5unified` を T1〜T4 で焼く（`-DSAAN_ENABLE_PIE=1`）。**checksum `0xa69a7ebbb5ccb05f` 不変**を先に確認 → `SAAN_PROFILE=0` で満チャンク xRT（n=3）→ `SAAN_PROFILE=1` の表 → **M-84**。
2. T5 を重ねて焼く。checksum が動いたか（動けば丸め水準の記録）→ GELU 行。
3. `pie_probe` D 節（T6）→ **M-85**。
4. `CONFIG_ESP32S3_DATA_CACHE_LINE_64B=y` の A/B（F-1。設定 1 行。⚠️ PSRAM 上の DMA リングの整列を先に確認）。
5. 最後に **元のファーム（T1〜T5 の M5 ビルド）を焼き戻す**。

### T7〜T8. 分岐（§5）。P-0 の結果を見てから 1 つ選ぶ

### T9. M5 に辞書パーティションと漢字コードを載せる（P1 + P2）

- **目的**: 漢字 + PIE + M5 スピーカーの 1 本のファームを作る。
- **変更**: `esp32/boards/m5unified/partitions.csv` を 4 行に: `nvs 0x9000/0x6000`、`phy_init 0xF000/0x1000`、`factory 0x10000/0x2C0000`（2,883,584 B）、`dict(data,0x41) 0x2D0000/0xD30000`（13,828,096 B。DevKit 16 MB 表と同じ offset なのでリリースの `k1-dict-438750.bin` をそのまま焼ける）。終端 0x1000000 ちょうど。`main/CMakeLists.txt` の SRCS に `saan_dict.c` / `saan_kanji.c`、REQUIRES に `esp_partition`、`SAAN_KANJI=1 CHARSET_UTF_8`。トップ `CMakeLists.txt` に `esptool_py_flash_to_partition(flash "dict" …)`（`esp32/CMakeLists.txt:87-104` と同形）。
- **期待効果**: app 見込み 1,413,804 B（現行 1,353,440 + 漢字コード +60 KB。**estimate**）に対し factory の余裕 1.47 MB。
- **bit**: n/a（同一ターゲット・同一構成の 2 経路なので **bit 一致を要求できる**）。
- **メモリ**: flash +13.8 MB。DRAM 静的 +18 KB（estimate。T10 で −14.5 KB）。
- **ゲート**: `uv run python scripts/check_partitions.py --file esp32/boards/m5unified/partitions.csv --rodata` / 実機の起動ログ `辞書 OK … 438750` / `!今日は良い天気ですね。` と `きょ][おわよ][いて][んきです°ね` が同じ 53 ids → **同じ `0xa69a7ebbb5ccb05f`**。
- **陽性対照**: `check_partitions.py` に dict 行の終端が 16 MB を超える表を渡すと落ちる（既存）。
- **⚠️ mmap が落ちたら**: `esp_err_to_name` の値で切り分ける。IDF v5.5 では vaddr 不足は `ESP_ERR_NOT_FOUND`、`ESP_ERR_NO_MEM` は内部ヒープの `calloc` 失敗（第三者報告の症状はこちら）。vaddr は 32 MB 窓に対し PSRAM 8 MB + app 1.3 MB + dict 13.8 MB で **9.2 MB 余る**（算術）。
- **依存**: T2〜T4（DRAM を空けてから）。

### T10. 漢字経路の DRAM を PSRAM と arena へ（P4 + P3）

- **変更**: (a) `k7_label2ids.c` の `static char tok[640][16]`（10,240 B）と `saan_kanji.c` の `s_lab / s_tok / s_key`（4,224 B）を G2P 中に借りている arena（204 KB − 130,176 B = 78 KB 余り）へ。(b) Open JTalk の `calloc / strdup / free` を **`csrc/openjtalk/*.c` だけに `-include saan_oj_alloc.h`** で `heap_caps_calloc(…, MALLOC_CAP_SPIRAM)` → 内部フォールバックに向ける（K-5 の `k5_alloc.h` と同じ手口。取り込んだ C は 1 バイトも変えない = `k4b_vendor.py --check` は通る）。
- **期待効果**: 静的 DRAM −14,464 B（**measured** の .bss 内訳から）。一時ヒープ（ホスト値で最大 97,325 B）が内部 DRAM から消え、**長文で calloc が NULL を返して黙って短く喋る事故**（`mecab2njd` は WARNING で途中 return、`make_label` は size=0。エラーは返らない）を構造的に防ぐ。
- **bit**: 同一。⚠️ G2P の時間は PSRAM アクセスで**伸びる可能性**（estimate 1.5〜3×。セッション 2 で測る）。
- **ゲート**: `make -C csrc k6 / k7` 不変 / `size -A` で `.dram0.bss` −14,464 B / 実機で G2P 前後の `heap_caps_get_free_size(INTERNAL)` が減らず `(SPIRAM)` が減る。
- **陽性対照**: `-include` を外すと内部 DRAM が減る。
- **依存**: T9。

### T11. 入力 1 経路（K-I1）+ ホストとの対

- **変更**: 行の全文字が「かな経路のアルファベット」（ひらがな U+3041–U+3096・ー・っ・ん・`[ ] # °`・`? ?! ?. ?~`）ならかな経路、1 文字でも外（漢字・カタカナ・。、・数字・ASCII）なら辞書経路。1 パスで決定的。ログに「経路: かな / 辞書」を必ず出す。`!`（辞書強制）は試験用に残す。**ホスト側 `scripts/to_intermediate.py` に同じ判定関数**を持たせ、辞書経路の参照値は `run_frontend(t, predict_nani=False, use_sudachi_kanji_yomi=False)` かつ odori 無し（C-049 の 2.00% は既知の代償）。
- **⚠️ 決めること**: `text2mecab`（端末は vendored 済みだが**未呼び出し**、ホストの pyopenjtalk は呼ぶ）をどちらに揃えるか。ASCII・半角を含む文で食い違う。K-6 の G17 は「素性が一致した文」だけを見ているので**未検出**。
- **bit**: 出力が変わる（新しい挙動）。純ひらがな行は**かな経路のまま**（D-040 を守る。辞書経路に回すと「こんにちわ」等の読みが変わる）。
- **ゲート**: `make -C csrc line` に判定の陽性 / 陰性対照（各カテゴリ 1 文字・空行・記号だけ）/ M-63 の入力が経路「かな」で checksum 不変 / `今日は良い天気ですね。`（`!` 無し）が経路「辞書」で同 checksum / ホスト判定と端末判定が held-out 298 文 + 中間表現 298 行で 596/596。
- **依存**: T9。

### T12. 再生の先読みを M5 のキューに合わせる（P6）

- **事実**: M5.Speaker のキューはチャンネルあたり `wavinfo[2]`（再生中 1 + 待ち 1）。プリロール 371 ms は最初の 1 スロットを占めるだけで、定常では常に 1〜2 チャンク（93〜186 ms）しか先読みが無い。**xRT ≤ 0.5 になっても先読みは増えない**（`playRaw` でブロック）。main.c の「アンダーラン」は `dt > 音声長` の回数で、**実際に途切れたかは測っていない**。
- **変更**: (a) `SAAN_SPK_MAXBUF` を 4096 にして 2 pull を 1 `playRaw` に束ねる（S7 なら CHUNK 16 と一致）。(b) 定常中に `M5.Speaker.isPlaying() == 0` を検出して「実途切れ」を別に数える。(c) xRT が 0.7 を切ったらプリロールを 4096 sample に落として発話開始 −186 ms。
- **ゲート**: 実機で同じ文 n=3。PCM checksum 不変・実途切れ 0・鳴らし始めまでの ms。
- **依存**: セッション 1 の結果（xRT）。

### T13. CoreS3 向け「焼くだけ」イメージと手順（P7）

- **変更**: `build_cores3` で `esptool.py --chip esp32s3 merge_bin --fill-flash-size 16MB -o m5-cores3-firmware-kanji-pie-16mb.bin @flash_args`。`esp32/TESTING.md` M 節に: 先に `read_flash 0 0x1000000 backup.bin`（D-047 の手順）、`write_flash 0x0`、コンソールは USB Serial/JTAG、見るログ行、期待 checksum。
- **⚠️ 公開はユーザーと**（D-045: latest だけで完結。README の資産表に名前を書く前にアップロードしないと `check_release_assets` が赤）。
- **依存**: セッション 2 で通ってから。

### 実機セッション 2

1. T9〜T11 の M5 ビルドを焼く。起動ログ（PSRAM 8 MB / 辞書 OK / W8A8+PIE 有効 / 内部 DRAM free）→ mmap の結果（通った / `esp_err_to_name`）。
2. かな行と漢字行（`!` 無し）で **同じ `0xa69a7ebbb5ccb05f`**。M5 スピーカーから音が出ることを確認（ユーザー）。
3. M-83 の 4 文で G28 を再測（PSRAM ヒープの影響）。**最長 held-out 文（98 文字 / ラベル 214 本）**を打ち、落ちない・短くならないことを確認。
4. 内部 DRAM free の前後 → **M-86**。

### T14〜T17. 漢字 G2P の速度とメモリ（K トラック）。RTF には効かない

| | 変更 | 期待（estimate） | ゲート |
|---|---|---|---|
| **T14 K-S5** | `saan_prof.h` に K_ 区分（ENC / SEL0 / UNK / LINK / FEAT / M2N / PRON / K4B / ACC / JPC / LABEL / K7 / HEAP）を足し、1 文ごとに M-82 と同じ表。要素数は select0 の bit 数・strtab のバイト数・printf 回数・heap 回数 | 測定。「Open JTalk 側が 65〜70%」は**差分による推定**で未測定 | 区間の合計が総時間の ±5%（漏れ検出）/ `SAAN_PROFILE=0` でコード不変 |
| **T15 K-S1〜S4** | `select0` を byte 単位に（8 − popcnt8）/ NUL 区切り表（keyesc 20,880 B・pos6tab 32,612 B）に u16 索引（blob の任意区画、余り 125,776 B 内。旧 blob も読める）/ unk 40 件を起動時に小表へ / `child_of` の rank1 を先頭 1 回に | 辞書側 −25〜−30%（−7〜−17 ms）。**辞書側は flash 律速ではなく CPU（線形走査）律速**: 触れる行は 69〜100 KB で 2〜3 ms 分しか無い | `make -C csrc k2`（MeCab 1,918/1,918 不変・LOUDS の陰性対照）/ k6 / k7 一致数不変 / QEMU checksum 不変 / 実機 G28 |
| **T16 K-S6** | Open JTalk の `calloc/strdup/free` を arena の bump allocator に（free は no-op、文ごとにリセット。先に `k5_total()` で総確保量を測る）/ `k7_label2ids` の snprintf → memcpy、`k7_token_id` → 表 / `saan_kanji.c` の snprintf ×6/ノード → strncpy / **PATCH**: `jpcommon_label.c` の `append_format(vsnprintf)` を手書き整形に（`k4b_vendor.py` の PATCHES に登録、G24 のラベル bit 一致で示す） | −4〜−11 ms（**T14 の表を見てから順序を決める**） | k5 G24（ラベル bit 一致・陽性対照 MAXBUFLEN=64）/ k6 / k7 / `k4b_vendor.py --check` |
| **T17 K-M1** | `K7_MAX_TOKENS` 640 → 256、`TOK_MAX` 16 → 12 / `feat 96×320` 固定 → NUL 詰め込み / `k4_node_t` 524 → ~364 B / Viterbi 48 → 32 KB | 作業領域 130,176 → 約 80 KB | k7 G25〜G27 + M-79 の陽性対照（`K7_MAX_TOKENS=128` で 273/298）/ k4 G12/G13 / k6 G17 / Viterbi 32 KB で 298/298 |

## 4. 期待の合算（**全部 estimate。M-84 で置き換える**）

| 段 | 現在（M-82） | T1〜T5 後の見込み | 根拠 |
|---|---:|---:|---|
| MAC | 48.9 ms | 36〜40 | 計算 −37%、flash 行フィル（15〜20 ms）は不変 |
| GELU | 10.7 | 2〜3 | 要素 −42% × 118 → 35〜50 cyc |
| TOKEN | 8.7 | 3〜4 | 1.31 M → 0.35 M MAC |
| その他（QUANT / LN / DW / PIPE / ISTFT） | 8.3 | 6〜7 | 範囲 −35〜45% |
| **1 step** | **76.6** | **48〜54**（xRT 0.52〜0.58） | |

**46 ms には T1〜T5 だけでは届かない見込み。** 残りは §5 の分岐（flash 律速なら S7 で −8〜−12 ms、固定費律速なら S5b / Q-OUTER）。

## 5. 分岐（P-0 = M-85 の結果で選ぶ）

| P-0 の結果 | 採るもの | 捨てるもの |
|---|---|---|
| **(D3 − D2) が MAC の 1/3 以上**（flash 律速） | **T7a S7** `SAAN_CHUNK 8 → 16`（hout は T=8 × 2 回で `o1539` 49 KB のまま。再生側 `SAAN_SPK_MAXBUF / SAAN_I2S_MAXBUF` を `SAAN_CHUNK*SAAN_HOP` に）。arena は T2〜T4 後で約 +55 KB（estimate。`make -C csrc arena` で測る）。**音声遅延 +93 ms** → ユーザー判断（§7）。**F-1** 64 B 行（設定 1 行）。**F-4** flash 120 MHz は `esptool.py flash_id` でチップの HPM 対応を見てから | W-DRAM（DRAM 常駐は最大 57 KB で −2 ms、S7 と DRAM を争う）/ F-2 `SPIRAM_RODATA`（同じ 4 bit × 80 MHz。D4 で差が無ければ捨てる） |
| **D1 が MAC の 1/2 以上**（固定費律速） | **T7b S5b**（weight-stationary。cinp ≤ 128 の層で重み行を q レジスタに 1 回ロード。利得上限 −2〜−8 ms）→ 足りなければ **Q-OUTER**（QACC 外積形、blob v3。⚠️ 20 bit レーンの溢れ、QEMU の命令実装を spike で先に確認） | K-MERGE（タップ併合。量子化粒度が変わる = 出力変化。SCOREQ 測り直しが要る。最後の手段） |
| 両方が同格 | S7 → S5b の順（S7 は bit 同一で実装が軽い） | |

## 6. docs の更新箇所

- `docs/measurements.md`: **M-84**（T1〜T5 後の実機表。満チャンク xRT の新定義を併記）/ **M-85**（P-0 / GELU マイクロベンチ）/ **M-86**（漢字 + PIE の M5、DRAM 収支、G28 再測）。
- `docs/decisions.md`: **C-054**「M-82 の xRT 0.926 は末尾 pull で膨張。満チャンク step は 0.82。dot は 109,152 回。GELU の原因は表ではなくコード生成」/ **D-048**（W8A8+PIE の既定化）/ **D-049**（RTF の分母と初回遅延・アンダーランの要件）/ 必要なら **D-050**（G28 の基準を絶対値に）。
- checksum の基準値（T5 で動いたとき）: `esp32/TESTING.md` / `esp32/boards/m5unified/README.md` / CLAUDE.md ほか 11 ファイルの期待値表。
- 索引: `docs/README.md`（M-1〜M-86）、CLAUDE.md の現在地、README（日英）の表、`requirements.md` §6.2（初回遅延の数値要件を足すなら）。
- `check_doc_counters.py` が見る数字（M / D / C の最大番号）。

## 7. 決めてもらうこと（ユーザー判断）

| # | 何 | 材料 | いつ要るか |
|---|---|---|---|
| 1 | **RTF ≤ 0.5 の分母**を「満チャンク step / チャンク音声長」で確定するか。初回 pull（466 ms）と鳴らし始めまで（719 ms）は別要件にするか | requirements §6.2 に遅延の数値要件が無い（調査 [0]） | M-84 を書く前 |
| 2 | **S7 の音声遅延 +93 ms** を対話用途（スタックチャン）で許容するか | パイプ 38 フレームは CH に依らず、S7 で増えるのは出力 8 フレーム分 | P-0 が flash 律速と出たとき |
| 3 | **D-048** W8A8+PIE を出荷ファームの既定にするか | M-55（知覚的に無料）/ M-82（0.926）/ M5 構成は既に有効 | セッション 1 の後 |
| 4 | **G28 の基準**: 「音声長の 1%」のままか「≤ 20 ms/文」の絶対値か | 1% は初回遅延にもアンダーランにも直接対応しない | T14 の前 |
| 5 | **入力自動判定で純ひらがな行をかな経路（平板）のままにする**か | D-040。辞書経路に回すと読みが変わる | T11 |
| 6 | **次のリリース**の中身（v2 blob / USB-JTAG 入力イメージ / CoreS3 イメージ） | D-045 | T13 の後 |

## 8. 何を測っていないか（この計画の限界）

- **cyc の内訳は全部 estimate**（回数だけが exact）。LX7 の FP レイテンシ・D-cache hit レイテンシ・flash 行フィルの実 cyc はリポジトリにも IDF にも無い。M-85 が最初の実測になる。
- 実機の表は **53 ids の 1 文**。350 ids では TOKEN の span 分布・`make_hf` の O(n_ids) 探索・arena の ids 比例分が変わる。
- 「アンダーラン 1/14」が**可聴か**は誰も聴いていない（G32 と同じく聴取ゼロ）。
- M5 + 漢字の実ビルドは**存在しない**。DRAM の見込み（+18 KB → −14.5 KB）は DevKit ビルド 2 本の差分から転用した estimate。
- 第三者報告の `ESP_ERR_NO_MEM`（PSRAM 有効 + mmap）は**未再現**。IDF ソースの読解から「内部ヒープ枯渇」が最もらしいが、T9 を実機で試すまで「落ちない」とは書けない。
