# S2 計画 — CoreS3 のスタックチャンで漢字・カタカナ・ひらがなを **RTF ≤ 0.5・少メモリ**で喋る

作成 2026-09-03 / 最終更新 2026-09-03（T1〜T6 / T9〜T11 / S5b 完了）。
前計画 **S-1**（S1〜S5a まで完了、M-81 / M-82）は **§10「前史」に要約して畳み、ファイルは削除した**。
K 計画 [`k1-kanji-implementation-plan.md`](k1-kanji-implementation-plan.md)（K-8 の G28〜G31 完了、M-83）の続き。
両トラックを **1 台の CoreS3 で 1 本のファーム**に合流させ、そのうえで速度とメモリを詰める。

> ✅ **目標は達成した（2026-09-03）。** 満チャンク 1 pull の xRT **0.446**（要件 ≤ 0.5）/ 漢字・カタカナ・ひらがなが
> **`!` 無し**で M5 のスピーカーから喋る / 内部 DRAM の空き **132,039 B**（M-88 / M-89 / M-90）。
> **残っているのは G32（聴取。人が要る）と、余裕を積むための S7 / T12〜T17（どれも未着手）だけ。**
> ⚠️ **ざっとした聴取は済んでいる**（M-91 / M-93 = 実機 / M-96 = ブラウザ。どれも**1 名・対照なし・盲検なし**）。**残るのは対照つきの聴取**（`reports/k8_listen/` の 12 組 / `reports/d4_accent/`）。
> ⚠️ **発話全体の RTF は 0.541〜0.712 でまだ 0.5 を超える**（要件の分母が未定 = §7 #1）。

> **For agentic workers:** 各タスクは 1 コミット。ゲートは「何を実行して何が出れば合格か」と**陽性対照**（こう壊すと落ちる）を必ず持つ。
> 速度の判断は **実機の `-DSAAN_PROFILE=1` の表**だけで行う（QEMU の割合は実機と別物。M-80 → M-82 の教訓）。
> 数値は measured / estimate を区別する。**測るまで書かない。**

## 0. 目標と現在地

すべて **measured**。左が計画を書いた時点、右が到達値。

| | 計画時（2026-09-03 朝） | **到達（2026-09-03 夜）** | 目標 | 出典 |
|---|---|---|---|---|
| 満チャンク 1 pull の xRT | 0.926 → 0.861（64 B 行） | **0.446** | ≤ 0.5 | M-82 / M-84 → **M-90** |
| 定常 1 step（8 frames = 92.88 ms 音声、PROFILE=1） | 18,378,513 cyc（76.58 ms） | **11,659,500 cyc**（−36.6%） | ≤ 46 ms | M-82 §3 → **M-89** |
| 内部 DRAM free（M5 構成、起動直後） | 101,651 B（最大ブロック 55,296） | **132,039 B**（最大ブロック 86,016。**辞書と漢字経路を載せた状態で**） | 漢字を足して ≥ 40 KB | M-82 ログ → **M-90** |
| arena（静的確保 / 実測 used） | 212,992 / 195,808 B | **180,224 / 157,360 B** | 減らす | M-82 → **M-89** |
| 漢字 G2P | 27.85〜66.30 ms（DevKit・W8A32） | **5.51〜40.79 ms**（M5・W8A8+PIE） | 目安を絶対値で決め直す（§7 #4。**未決**） | M-83 → **M-90** |
| 漢字 + PIE + M5 スピーカーの 1 本のファーム | **存在しない** | **存在する**。かな行と漢字行が同じ `0xa69a7ebbb5ccb05f` | — | **M-90** |
| 入力 | かな行 / `!漢字行` の 2 経路 | **1 経路**（端末が 3 値で判定。`!` は試験用に残置） | — | **M-90 §1** |
| アンダーラン | 1/14 | **0**（4 文とも） | 0 | M-82 → **M-90** |
| 鳴らし始めまで | 719 ms | **384 ms**（M-90 = 出荷構成。かな構成は 432〜434 ms） | 要件なし（§7 #1） | M-82 → M-88 / M-89 → **M-90** |
| 発話全体の RTF（(init + Σpull) / 音声長） | 1.308 | **0.541〜0.712**（⚠️ **0.5 を超える**。文が長いほど下がる） | ⚠️ **分母が未定**（§7 #1） | M-82 → M-90 |

**PCM の checksum は M-82 から 1 bit も変わっていない**（W8A8+PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d`）。
T1〜T4 と S5b は bit 同一、T5 は S3 と bit 一致で、**速くなったのは波形を変えずに**である。

## 1. 調査で直った前提（M-82 §4 に書いた仮説のうち 4 つ）

2026-09-03 に 6 視点でコードと ELF（`build_cores3` の objdump / map）を読み直した結果。**全部コードから数えた値**で、cyc の内訳だけが estimate。

| M-82 §4 の前提 | 調査結果 | 影響 |
|---|---|---|
| 「1 step に約 90,000 回の dot」 | **約 109,400 回/step（pull 内）**（呼び出し箇所 × 層の T。審査でカウンタを置いて 109,357.71 を実測。prof の 4,724.19 は Σcout 4,715 + INIT の 193/21） | 固定費の見積りが 2 割増える |
| 「GELU 118 cyc/要素は表が flash にあるせい」 | 表の寄与は **≈2%（estimate）**。主因は `saan_erf_approx` が **要素ごとに call8**（インライン化されていない）、毎要素 l32r 11 本（FP 定数 9 + 表 2）+ wfr 12、戻り値のスタック往復、FP 比較分岐 2 回。要素あたり 78〜81 命令で CPI 1.5 | 表を DRAM に置くだけでは下がらない。**コード生成を直す**（T5） |
| 「MAC は flash 律速」 | 3 成分が**同じ桁**（estimate）: flash 行フィル ≈17,000 行/step（3.3〜5.1 M cyc）/ dot 固定費（≈3.7 M cyc）/ srs→float→madd の直列チェーン（3〜4 M cyc）。PIE 命令の発行自体は ≤ 10% | 1 つだけ直しても 46 ms に届かない。**P-0 で比を測ってから S7 か S5b を選ぶ** |
| 「xRT 0.926 が定常」 | 末尾の pull で **全フレームが出そろった後も step_chunk が 3 回回る**（21 step 中 3 = 14%）。満チャンク 1 step は 76.58 ms → **0.82**。「アンダーラン 1/14」もこの末尾 pull | 表示の定義を直す（T1）。**速度が上がったのではない**ので C-番号で記録する |

さらに 2 つ、計画に無かった大きな事実:

- **MAC の 37%・GELU の 42% が「捨てられる出力」の計算**。AC ブロックは窓 16 のうち中央 8 しか下流に渡さず、DEC ブロックは窓 14 のうち中央 8。1×1 conv / LN / GELU / per-frame 量子化はフレーム独立なので、**計算範囲を中央に絞っても bit 同一**（T2 = S9）。
- **TOKEN 11.3% は毎 step ±12 トークンのハローを丸ごと再計算**している（必要 87 フレーム分に対し 171 を計算）。トークン単位の持ち越し（S6）で bit 同一のまま減らせる（T3）。（「171 / 87」は conv 出力**列**（トークン位置）の数。正確には 169 vs 85）

## 2. 順序の原理

1. **bit 同一の削減を先に、まとめて 1 回の実機で測る**（T1〜T4）。checksum が `0xa69a7ebbb5ccb05f` のまま step が縮むことを実機で見る。
2. **丸め水準になりうる変更は別コミット**（T5 の GELU）。QEMU の checksum で bit 同一か丸め水準かを確定し、動いたら新基準を記録する。
3. **仮説を棄却する測定を同じ実機セッションに入れる**（T6 = P-0 マイクロベンチ）。結果で §5 の分岐を選ぶ。
4. **漢字の合流は速度と独立に進められる**（T9〜T13）が、内部 DRAM を食うので T2〜T4 でメモリを空けた**後**に載せる。
5. **G2P の速度（T14〜T17）は RTF に 1 ms も寄与しない**。効くのは「最初のチャンクが鳴るまでの遅れ」だけ。順番は最後。

## 3. タスク

各タスク: 目的 / 変更 / 期待効果 / bit / メモリ / ゲート / 陽性対照 / 実機 / 依存。

### T1. pull ループの早期終了と xRT の定義（ST-1 + PROF-1）— ✅ **完了**（a85008c + 4a1e791。実機は M-87 / M-88）

- **目的**: 全フレームが `obuf` に出そろった後に回る 3 step/発話を止める。表示 xRT を満チャンク step の値にする。
- **変更**: `csrc/saanotts_stream.c` の pull ループ `while (ofill < CH)` に「`emitted + ofill < n_frames`」を足す。`esp32/main/main.c` の `mean_rest` を満チャンク pull の中央値にし、pull ごとの ms をログ。`prof_report` で LOOKUP 行を INIT 側へ、DW 行の QUANT 重複を注記。
- **期待効果**: 発話あたり step 21 → 18（−14%、約 −230 ms。**estimate**）。定常 step は不変。末尾のアンダーラン判定が消える。
- **bit**: 同一（出力サンプル列は不変）。
- **メモリ**: arena（csrc）は 0。⚠️ `esp32/main/main.c` の pull の控え（`pull_us` / `pull_n`、128 本 × 5 B）で内部 DRAM の .bss が **+640 B**（審査で指摘。実装時は `uint32_t[256]` = 1,024 B だった。map の `.bss.pull_us` / `.bss.pull_n` で確認）。
- **⚠️ xRT の定義が変わった**（審査 major）: 旧 `mean_rest` = 2 回目以降の全 pull の平均で、末尾 pull の出力に寄与しない step 3 回を含む。新 = 満チャンク pull の中央値。**M-82（0.926）/ M-84（0.861）はこの新しい行と直接比べられない**し、firmware に残した「2 回目以降 mean」の行も T1 後は末尾の step が消えているので旧値の再現にならない。T1 をまたいで比べてよいのは定義に依らない **「合成合計 ms」（全 pull の dt の和。firmware が出す）** と `-DSAAN_PROFILE=1` の **STEP 行（1 step のサイクル）** だけ。firmware はこの旨を結果の直後に ESP_LOGW で出す。満チャンク pull が 0 回（n_frames ≤ 8）なら xRT は `n/a`（0.000 と出さない）。pull ごとの行は実時間ループの外（done: の後）で出す（UART 115200 の板で pull と I2S write の間に入らないように）。
- **ゲート**: `make -C csrc all-test`（stream G2: 一括版と 27,136 sample bit 一致）/ `./csrc/prof_test` の step_chunk 回数が 3 発話で 63 → 54。QEMU checksum 不変。
- **陽性対照**: ~~条件を 1 フレーム早く（`< n_frames − 1`）すると末尾 1 フレームが欠けて G2 が落ちる。~~ **実装時に測ったら `− 1` も `− 2` も落ちなかった**: pull の境目は常に `emitted + ofill = 8m + 2` で、fpush が n_frames に届く step が残り 9〜10 フレームを一度に吐くため。審査後に demo_ids の prefix 1..53（mod 8 の 8 残差すべて + T=2 / T=5）を一括版と memcmp で走査した実測（fp32、W8A8 も同形）: `− 1` 0/53 / `− 3` **5/53**（≡ 5 (mod 8) の 4/4 **と** T=2。「≡ 5 だけ」は誤りで、実装時の「49 件中 3 件」は短い発話を数えていなかった）/ **`− 8` 40/53**（demo 106 ≡ 2 は 98 で切れるが **≡ 3, 4 (mod 8) の 13 件は素通り**。残り r が 9〜10 のため）/ **`− 10` = `− (CH+2)` 53/53**。**全残差で落ちる陽性対照は `− (CH+2)` だけ**。`− 8` を使うなら ≡ 3, 4 を見逃すことを承知で。
- **実機**: 表の読み替えのみ。セッション 1 で pull ごとの ms を確認。
- **依存**: なし。
- [x] 実装（stream 早期終了 / prof_test `--expect-steps 54` / main.c 満チャンク xRT + pull ごとの ms / prof_report の INIT 側 LOOKUP と DW 注記）— ホストゲート通過。QEMU checksum は実行結果を StructuredOutput に記録
- [x] 審査の修正（fix）: main.c の pull ログを実時間ループの外へ / 控えを 640 B に / xRT `n/a` / 合成合計 ms の行と定義変更の ESP_LOGW / `make prof` の T1 ゲートが NG の理由を出す（`> /dev/null` をやめた）/ 陽性対照の記述を prefix 走査 53 件の実測に合わせた
- [x] **実機（M-87）**: **アンダーランが 1/14 → 0 になった**（M-82 / M-84 の「1 回」は末尾 pull の無駄な step そのものだった）。
  初回 pull 466 → 409 ms、鳴らし始め 719 → 634 ms。発話全体の合成/音声（定義に依らない量）は 53 ids で 1.287 → 0.997。
  ⚠️ **表示 xRT の定義が変わった**ので M-82 / M-84 の 0.926 / 0.861 とは並べられない（**C-054** に記録した）。

### T2. 有効範囲だけ計算する（S9）— ✅ **完了**（a20cb47。実機は M-88）

- **目的**: 捨てられる出力を計算しない。MAC −37% / GELU −42% / DW −43% / QUANT・LN −35〜45%（要素数は exact、時間は estimate）。
- **変更**: `saan_conv1d_w` / `saan_conv1d_i8a` / `saan_dwconv1d_*` / `saan_layernorm_c` / `saan_gelu` / `saan_relu` に出力範囲 `[t0, t1)` を渡せる形を足す（fp32 経路 `saan_conv1d` も同じ関数を通す。2 回書かない）。`ac_step_body`: c1 は `[2, W−2)`、c2 / LN / 残差は中央 CH だけ。`dec_step_body`: **dw の入力（pipe buf）だけ窓全部で、cdown / cup / dw の出力・pw1 / GELU / pw2 / 残差はすべて中央 CH だけ**（審査で訂正: g = cup(cdown(c)) は dw の出力に足すので中央 8 で足りる。DW −43% はこれで初めて出る）。`compute_tokens_body`: 各 conv で 2 ずつ縮む有効範囲だけ。
- **期待効果**: MAC 7.28 M → 約 4.56 M/step、GELU 21,664 → 12,544 要素。時間は「flash 行フィル分は減らない」ので 1 step 18.4 M → 13.4〜14.7 M cyc（**estimate**、xRT 0.82 → 0.60〜0.66）。
- **bit**: 同一（各出力要素の積和順・per-frame 量子化・per-frame LN は不変）。
- **メモリ**: `w_e` を `[304][CH]`、`w_g / w_r / w_full2` を `[C][CH]` に縮めて arena 約 −12 KB（estimate）。
- **前提（T2a、先にコミット）**: **stream G2 を多文化する。** 今の G2 は demo 1 文（106 frames ≡ 2 mod 8）だけで、n_frames mod CH で挙動が変わる壊れ方（末尾 step 数・ofill 最大）を検出できない。`csrc/stream_test.c` に held-out 24 文（`ids_heldout.bin`、残差 0〜7 を含む）× {fp32 `student.bin`, W8A8 `student_i8.bin`} の一括 vs ストリーミング memcmp を足す（HEAD で 24/24 一致することは審査で確認済み。数秒）。あわせて pull ごとの `ofill` 最大を測り `≤ CH+4`（式 2·CH − (SAAN_LATENCY mod CH) = 12）を assert。
- **ゲート**: stream G2（多文）bit 一致 / `./csrc/prof_test --expect-gelu 12544 --expect-dw 21280 --expect-mac-le 4600000`（**exact な期待値を `--expect-*` で持つ**。T2 前の 21,664 / 37,240 / 7,279,646 で落ちるのが陽性対照）/ QEMU checksum `0xa69a7ebbb5ccb05f`・`0xe4b645c30835d42d` 不変 / int8 / golden / fft は無変更で通る。
- **陽性対照**: c1 の範囲を `[3, W−2)` に 1 フレーム狭めると G2 が落ちる（中央 8 の先頭が壊れる）。
- **実機**: セッション 1 で MAC / GELU の cyc/step。
- **依存**: T1（step 回数が変わるので prof の「/step」を先に安定させる）。
- [x] **T2a**（stream G2 の多文化 / `SAAN_OBUF_HOPS` = 2·CH − (SAAN_LATENCY mod CH) / ofill の上限を「超えない **かつ** 届く」で assert / コアは書く前に `ofill >= SAAN_OBUF_HOPS` で SAAN_ERR_ARENA）— `make -C csrc stream` が fp32 / W8A32 / W8A8 の 3 レーン × held-out 24 文（int8 レーンは d̂ が違い残差 1 つが空くので文の prefix で補完）。実測: ofill 最大 12 は n_frames ≡ 4 (mod 8) の 4 文（h02 156 / h13 140 / h14 132 / h18 300 frames）、≡ 3 は 11、他は 10。陽性対照 (CH+2): ≡ 3・4 の 7 文が `pull: arena が足りない` で落ち、1 文の G2 は通ったまま（審査の指摘どおり）。⚠️ W8A8 レーンだけ G1 を `--g1-kb 208`（実機の静的 arena）で見る — W8A8 は 200 KB を超える（M-55。今 203.0 KB）
- [x] **T2**（S9。2026-09-03、feat/s1-speed-m5）— 範囲版カーネル `saan_conv1d_r` / `saan_dwconv1d_r` / int8 版 / `saan_conv1d_wr` / `saan_dwconv1d_wr`（**出力は圧縮 [C][t1−t0]**。fp32 / W8A32 / W8A8 の 3 経路とも [0,T) 版は薄いラッパ）。AC: c1 [2, W−2) / c2・LN・残差は中央 CH を `out` に直接。DEC: dw の入力だけ窓全部、cdown / cup / dw 出力 / pw1 / GELU / pw2 / 残差は中央 CH。DINP: 中央 CH を h_out に直接。TOKEN: 段ごとに 8 列ずつ縮む圧縮バッファを 3 本で回す。LN / GELU / ReLU に範囲版は要らない（入力が既に圧縮形）。**実測（`make -C csrc prof`、18 step/発話）**: GELU 21,664 → **12,544** / DW 37,240 → **21,280** / MAC 7,509,268 → **4,619,188**（−38.5%。計画の 7.28 M → 4.56 M は T1 前の 21 step 平均）/ QUANT 51,996 → 35,836 / LN 7,283 → 3,571 / RELU 7,283 → 4,979。arena: init 後の a.used 196,576 → **180,064 B**（ホスト、n_ids=1。−16,512 B。ターゲットは impl のポインタ幅で 179,296 B）、350 ids の最小 arena 195 → **179 KB**。QEMU checksum **不変**（PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d`、|max| と Σx² も一致）。ゲート: `--expect-gelu 12544 --expect-dw 21280 --expect-mac-le 4619188`（T2 前のコアで 3 つとも NG を確認）/ stream G2 3 レーン × 24 文 / **新設 `make -C csrc range`**（範囲版 vs [0,T) 版のランダム 2,000 形状 × 6 + LN の T 非依存、陽性対照つき。all-test に追加）。⚠️ **踏んだ穴 2 つ**: (1) `saan_layernorm_c` は clang が T=1 のときだけ c ループをベクトル化し `var += d*d` の融合が変わるので、同じ列でも T=1 と T>1 で 1 ulp 違った（token 最終段が 1 列になる #11 / #18 だけ G2 が落ちた）→ 列を連続の局所配列に写して T 非依存にした。(2) 雛形の `SAAN_ARENA_USED_FLOOR` はホストの定数で、ターゲットの a.used（179,296）と一致しない → 定数をやめて `saan_stream_arena_used(n_ids)` をコアに置き、雛形は `a.used == 関数` で検査、`make arena` §5 が関数と実測の一致を守る。陽性対照: c1 を [3, W−2) に狭めると G2 が落ちるのを見て戻した。
- [x] **実機（M-88）**: MAC 10,869,162 → **7,610,417 cyc**（要素 −42.3%）/ GELU 要素 21,664 → 12,544 / DW 要素 37,240 → 21,280。
  ⚠️ **cyc/MAC は 1.45 → 1.81 に上がった。これは正常** — 要素を 42% 減らしても**重みの flash 読み出しは減らない**（M-85: 259 cyc / 32 B 行、行数は範囲に依らない）ので、残った積和に固定費が集中する。
  **GELU / DW / QUANT / LN は要素数の削減がそのまま時間になった**（cyc/要素は不変）。**ISTFT と PIPE は変わらない**（出力フレーム数に比例する段で、窓の範囲とは無関係。今や STEP の 11.3%）。

### T3. token ブロックをトークン単位のパイプに（S6）— ✅ **完了**（75cc8ff。実機は M-88）

- **目的**: TOKEN 11.3% を「新しく必要になったトークン分だけ」に。
- **変更**: `compute_tokens_body` の「毎チャンク ±12 幅で丸ごと再計算」を、フレーム側と同じステート保持型 pipe（3 段、pad 4、K トークンずつ進める）にし、出力を `[48][K + CH + 1]` のリングに持つ。`make_hf` は必要なトークンが揃うまで pipe を進める（ids は全部既知）。
- **期待効果**: TOKEN の MAC 1.31 M → 約 0.55 M/step（K=8。T2 併用で約 0.35 M）。時間 2.08 M cyc → 0.6〜0.9 M（**estimate**）。
- **bit**: 同一（token ブロックの出力はトークン位置に依存しない。フレーム側の pipe が M-42 G2 で同じ理屈を通している）。
- **メモリ**: `tok_buf / w1 / w2 / tok_out` 19,968 B → 約 12 KB（−8 KB、estimate）。
- **ゲート**: stream G2 / prof の TOKEN 要素数と回数 / QEMU checksum 不変。
- **陽性対照**: パイプ設計では「段の pad を 4 → 3 に」する（旧設計の「ハロー 11」に対応）。G2（多文）が落ちる。
- **実機**: セッション 1 の TOKEN 行。
- **依存**: T2。
- [x] **T3**（S6。2026-09-03、feat/s1-speed-m5）— `compute_tokens`（毎 step ±12 幅を 3 段まるごと再計算）を、フレーム側と**同じ** `pipe_t` と**同じ** `acblk_step`（旧 ac_step_body を frame / token 共通にした。2 回書かない）で回す 3 段パイプ `tok[3]`（pad 4、TOK_K = CH = 8 トークンずつ）にし、最終段の出力を群単位のリング `tok_ring [TOK_G=2][48][8]` に直接書く。`make_hf` は `[i0, i1]` が出そろう（出そろい末尾 = tok_pushed·K − 12）までパイプを進め、リングから引く。リングの深さは K + CH − 1 = 15 トークン → 2 群 16（1 チャンクが跨ぐトークンは最大 CH。足りなければ SAAN_ERR_SHAPE で止まる）。段の作業領域は `w_full` / `w_ch2` を借りる（make_hf の間は空いている）。**実測（`make -C csrc prof`、18 step/発話）**: TOKEN **0.78 → 0.50 回/step**、要素 22（再計算した窓の列数）→ **4 / step**（新しく計算した出力トークン数。意味が変わった）、MAC **4,619,188 → 4,200,628 / step**（−418,560 = −9.1%。TOKEN 分は 764,160 → 345,600 で −54.8%）、QUANT 35,836 → 33,772 / LN 3,571 → 2,779 / RELU 4,979 → 4,027。ホストの ns/step は TOKEN 76,147 → 36,817（実機の内訳ではない）。arena: 旧 tok_buf / tok_w1 / tok_w2 / tok_out 17,664 B → パイプ 3 × 3,072 + リング 3,072 = 12,288 B。init 後の a.used（n_ids=1）180,064 → **174,752 B**（ホスト、−5,312 B）/ ターゲット（QEMU の `arena used`）179,296 → **173,968 B**。`make -C csrc arena`: 350 ids の最小 arena 179 → **174 KB**（178,176 B）、§5 は 5 点とも一致。QEMU checksum **不変**（PIE `0xa69a7ebbb5ccb05f` |max| 9627 Σx² 74,264,237,672 / W8A32 `0xe4b645c30835d42d` |max| 9529 Σx² 74,155,591,505）。ゲート: stream G2 3 レーン × 24 文（fp32 / W8A32 24/24、W8A8 25/25）/ all-test 全通過 / `check_esp32_template.sh` 全通過 / prof に `--expect-token 4` を新設し `--expect-mac-le` を 4,200,628 に締めた（T2 のコアに同じ prof_test.c を掛けると TOKEN 21.94 と MAC 4,619,188 で両方 NG）。**陽性対照**: `TOK_PAD` を 3 にした一時ビルドで G2 が **fp32 0/24・W8A8 0/25**（27,136/27,136 sample が違う。max|Δ| 0.16）で落ちるのを見て戻した。⚠️ 計画の「−8 KB」は T2 前の 19,968 B からの見込みで、T2 後の 17,664 B からは −5,376 B。
- [x] **実機（M-88）**: TOKEN 2,323,399 → **730,908 cyc**（0.78 → 0.50 回/step。M-82 比 **−64.8%**）。

### T4. arena の詰め（MEM-1 + MEM-2）— ✅ **完了**（57bbaf5。実機は **M-89**）

- **目的**: 漢字経路と S7 のために内部 DRAM を空ける。
- **変更**: (a) `cdel[0..5]`（同じ c を 6 本の pipe に別々の遅延で持つ、12,800 B）を 1 本のリング `[40][16+CH]` に。(b) iSTFT の `re / im / frm`（8,224 B）を `w_e` と重ね合わせる（生存期間が重ならない）。(c) `step_chunk_body` の static `h_tmp / c_tmp`（3,712 B .bss）と 5 段の memcpy をポインタ交換に。~~(d) `obuf` を `(CH+2)`~~ **取り下げ**（審査: ofill の最大は n_frames ≡ 4 mod 8 の文で **12 = CH+4**。式は 2·CH − (SAAN_LATENCY mod CH)。(CH+2) だと 1/8 の文で 2 KB を隣のバッファに書き、G2（1 文）でも QEMU checksum でも検出できない）。この式を `saan_stream_arena_needed` とコメントに置く。⚠️ (a) のリング `[40][16+CH]` は **T2 で cdown/cup を中央だけにした後**の下限（窓全部なら 27 フレーム要る）。
- **期待効果**: arena −17,184 B（8,960 + 8,224）、.bss −3,712 B（**式で出した値**。`make -C csrc arena` で実測して置き換える）。PIPE 2.2% の 1/3 と memcpy 18.5 KB/step も消える（僅少）。
- **bit**: 同一（純粋なデータ移動）。
- **ゲート**: stream G2（多文）/ ofill ≤ CH+4 の assert / `make -C csrc arena`（下限を測り直し、`SAAN_ARENA_BYTES` と `SAAN_ARENA_USED_FLOOR` を更新。**`SAAN_ARENA_BYTES ≥ saan_kanji_workbytes() + 14,464` を静的ゲートに**）/ QEMU checksum 不変。
- **陽性対照**: リングの遅延位置を 1 つずらすと G2 が落ちる。
- **依存**: T2, T3（バッファ形が決まってから）。
- [x] **完了（M-89）**: `SAAN_ARENA_BYTES` 212,992 → **180,224 B**（−32,768）/ arena used 173,968 → **157,360 B** /
  ホストの最小 arena（350 ids）178,176 → **160,768 B** / **起動直後の内部 DRAM free 99,987 → 136,407 B（+36,420）**、最大ブロック 55,296 → **90,112 B**。
  副産物として **PIPE −13.1%**（`cdel` の push 6 + center 6 が push 1 + center 5 になり memcpy が減った）。STEP は −0.6% で、**T4 は速度ではなくメモリのための変更**。
  checksum 不変。`check_esp32_template.sh` に **§10**（`SAAN_ARENA_BYTES` が漢字経路の 144,640 B を残せるか）を新設した。

### T5. GELU のコード生成を直す（G-3 → G-4 → G-1 → G-2）。**別コミット** — ✅ **完了**（35edcc3 / 9a79f88 / 0a809d0 / 2bb1d3a、**修正 df357ce**）

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
- **陽性対照**: ~~クランプを `3.9999f`~~ → **`3.9f`**（審査: `kSaanErfV[127]` も `[128]` も float で 1.0f なので 3.9999 では 1 点も動かない。3.9f で 229,753 点が不一致）。NaN は旧実装が UB なので「有限値で一致」をゲートにする。**(0,4) の全 float 走査は 2.4 s** で済むので格子より走査を使う。
- **依存**: なし（T1〜T4 と独立。ただし別コミット）。
- [x] **完了。ただし途中で 1 回遅くしている**（**M-87 / C-055**）: G-3 の「符号を x から戻す」を `memcpy` による符号ビット OR にしたところ、
  GCC 14.2 for Xtensa が FP → 整数 → FP をレジスタ（`rfr` / `wfr`）ではなく**スタック経由の store → load** に落とし、
  **GELU が 118 → 211 cyc/要素に倍増**した（STEP +14.7%）。**QEMU でも pie_probe の E 節でも見えなかった**
  （QEMU は命令数比例、E3 は旧形のインラインを測っていた）。三項演算子 `x < 0.0f ? -y : y` に戻して **73.09 cyc/要素**
  （M-84 比 −38.2%。pie_probe E3 の 74.5 と一致）。クランプ・事前スケール表・インライン化・DRAM 配置はそのまま。
  **出力は S3 と bit 一致**（`make -C csrc erf` の [-4,4] 全 float 走査 §2a と本番 `saan_gelu` 経路 §2b、QEMU checksum）。
  → 教訓は **C-055**（命令数が減っても速くなったとは言えない / マイクロベンチは「同じ形」でしか本番を予測しない / PROFILE=1 の表を 1 行ずつ前の版と並べる）。

### T6. マイクロベンチ 2 本（P-0 + GELU）。**測定であって変更ではない** — ✅ **実機で測った（M-85）**

- **目的**: MAC 1.61 cyc/MAC の内訳（flash 行フィル / dot 固定費）と、GELU の改善幅を、実機で数字にする。§5 の分岐を決める。
- **変更**: `esp32/pie_probe` に **D 節**を足す。本物の blob を `.rodata` に埋め、decoder の連続 ≥ 131 KB を cin=48 / k=1 / cout=2,730 の行列と見なして、同じ `saan_conv1d_i8a` を **D1** hot（cout=16 を繰り返し = 全部キャッシュヒット = 固定費の床）/ **D2** DRAM ストリーム（memcpy した 131 KB）/ **D3** flash ストリーム / **D4** PSRAM ストリーム（`CONFIG_SPIRAM=y` 変種）/ **D5** D3 を T=16 で、の 5 条件で回し CCOUNT を取る。GELU は同じ 21,664 要素を「flash 表 / DRAM 表 × インライン無 / 有」の 4 条件。
- **出るもの**: cyc/行（D3 − D2）/ cyc/dot の床（D1）/ PSRAM の差（D4）/ T 倍増の効き（D5）。
- **ゲート**: D2 / D3 / D4 の y が memcmp 一致 + **重みを 1 バイト壊した陰性対照が不一致** / 5 回の min-max 幅 < 1%（CCOUNT は決定的）。
- **メモリ**: probe のみ（本番ファーム不変）。
- **実機**: ✅ 2026-09-03 に CoreS3 で実測（QIO 80 MHz / 240 MHz / D-cache 64 KB・32 B 行 / PSRAM 無しの pie_probe）。**M-85**:

| 条件 | cyc/dot | cyc/MAC | 読み |
|---|---:|---:|---|
| D1 hot（全部キャッシュヒット） | **78.0** | 1.626 | **dot の固定費の床**。flash が無くても 1.6 cyc/MAC |
| D2 DRAM ストリーム | 77.5 | 1.615 | DRAM のストリームは無償（D1 と同じ） |
| D3 flash ストリーム | **126.1** | 2.627 | flash の待ち = **48.6 cyc/dot = 259 cyc / 32 B 行 = D3 の 38.5%** |
| D5 flash、T=16 | 99.6 | 2.076 | 行の再利用 2 倍で flash 分が dot あたり半分（S7 の効き） |
| E1 現行 GELU | 112.4 cyc/要素 | | |
| E2 表を DRAM に | 113.4 | | **効かない**（M-84 と一致） |
| E3 erf をインライン | **74.5** | | −34%（T5 の G-1 がこれ） |
| E4 両方 | 74.5 | | |

  **flash 律速と固定費律速の両方が成立**（cinp=48 では固定費 61.5% / flash 38.5%）。§5 の分岐は「両方」。
- **依存**: なし。

### 実機セッション 1（1 回の接続でやること）— ✅ **やった**（M-84 / M-85 / M-87）

1. `esp32/boards/m5unified` を T1〜T4 で焼く（`-DSAAN_ENABLE_PIE=1`）。**checksum `0xa69a7ebbb5ccb05f` 不変**を先に確認 → `SAAN_PROFILE=0` で満チャンク xRT（n=3）→ `SAAN_PROFILE=1` の表 → **M-84**。
2. T5 を重ねて焼く。checksum が動いたか（動けば丸め水準の記録）→ GELU 行。
3. `pie_probe` D 節（T6）→ **M-85**。
4. `CONFIG_ESP32S3_DATA_CACHE_LINE_64B=y` の A/B（F-1。設定 1 行。⚠️ PSRAM 上の DMA リングの整列を先に確認）。
5. 最後に **元のファーム（T1〜T5 の M5 ビルド）を焼き戻す**。

⚠️ **1 回では終わらなかった。** 手順 2 で T5 が **遅くなっている**ことが分かり（M-87）、修正 → 再測 → T2/T3 → 再測（M-88）→ T4 → 再測（M-89）と
**5 回焼いている**。速度の変更は「1 回の接続でまとめて測る」計画が立たない — **入れた直後に前の版と 1 行ずつ並べる**しかない（C-055）。

### T7〜T8. 分岐（§5）。P-0 の結果を見てから 1 つ選ぶ — **T7b（S5b）だけ入れた。T7a（S7）は未着手**

- **T7b S5b（weight-stationary の PIE）** ✅ **完了**（e8a7494。bit 同一）: 重み行を q レジスタに常駐させ o → k → t で回す。
  **dot の 95.1% / MAC の 76.3% を覆う**。実機は **M-90**（0.494 → **0.446**）。
  ⚠️ **S5b 単独の寄与は分離していない**（S5b と漢字構成をまとめて焼いた）。分けるなら pie_probe の D6 / D7 を実機で読む（未実施）。
- **T7a S7（`SAAN_CHUNK` 8 → 16）** ❌ **未着手**。**要件は S7 無しで満たした**ので、これは余裕を積むための選択肢に落ちた。
  採るなら §5 の注意（obuf を 28 hop に / 音声遅延 +93 ms = §7 #2）がそのまま生きている。

### T9. M5 に辞書パーティションと漢字コードを載せる（P1 + P2）— ✅ **完了**（96257d9 = K-A。実機は **M-90**）

- **目的**: 漢字 + PIE + M5 スピーカーの 1 本のファームを作る。
- **変更**: `esp32/boards/m5unified/partitions.csv` を 4 行に: `nvs 0x9000/0x6000`、`phy_init 0xF000/0x1000`、`factory 0x10000/0x2C0000`（2,883,584 B）、`dict(data,0x41) 0x2D0000/0xD30000`（13,828,096 B。DevKit 16 MB 表と同じ offset なのでリリースの `k1-dict-438750.bin` をそのまま焼ける）。終端 0x1000000 ちょうど。`main/CMakeLists.txt` の SRCS に `saan_dict.c` / `saan_kanji.c`、REQUIRES に `esp_partition`、`SAAN_KANJI=1 CHARSET_UTF_8`。トップ `CMakeLists.txt` に `esptool_py_flash_to_partition(flash "dict" …)`（`esp32/CMakeLists.txt:87-104` と同形）。
- **期待効果**: app 見込み 1,413,804 B（現行 1,353,440 + 漢字コード +60 KB。**estimate**）に対し factory の余裕 1.47 MB。
- **bit**: n/a（同一ターゲット・同一構成の 2 経路なので **bit 一致を要求できる**）。
- **メモリ**: flash +13.8 MB。DRAM 静的 +18 KB（estimate。T10 で −14.5 KB）。
- **ゲート**: `uv run python scripts/check_partitions.py --file esp32/boards/m5unified/partitions.csv --rodata` / 実機の起動ログ `辞書 OK … 438750` / `!今日は良い天気ですね。` と `きょ][おわよ][いて][んきです°ね` が同じ 53 ids → **同じ `0xa69a7ebbb5ccb05f`**。
- **陽性対照**: `check_partitions.py` に dict 行の終端が 16 MB を超える表を渡すと落ちる（既存）。
- **⚠️ 先に決めること（審査で判明）**: M5 の `sdkconfig.defaults` は `CONFIG_SPI_FLASH_ROM_IMPL=y` で、ESP32-S3 では `spi_flash_mmap` が **ROM の legacy 実装**にリンクされる（map: `spi_flash_mmap = 0x40000bac`）。IDF はそれに **128 ページ = 8 MB のプールしか渡さない**ので、辞書 211 ページは vaddr がいくら余っていても `ESP_ERR_NO_MEM` になる（第三者報告の症状はこれで説明できる。DevKit は IDF 実装なので通った）。対処は (a) `CONFIG_SPI_FLASH_ROM_IMPL=n`（IRAM = DRAM が減る。`size -A` と起動直後 free を T9 の収支に入れる）か (b) `saan_dict.c` を `esp_mmu_map()`（esp_mm、ROM_IMPL と無関係）で貼る経路にする。**(b) を採る**（DRAM を食わない）。その後で IDF 実装の切り分け規則（vaddr 不足 = `ESP_ERR_NOT_FOUND` / `calloc` 失敗 = `ESP_ERR_NO_MEM`）が効く。vaddr は 32 MB 窓に対し PSRAM 8 MB + app 1.3 MB + dict 13.8 MB で **9.96 MB 余る**（算術）。
- **メモリの正味**: 粗 +18.0 KB（.bss 15,560 + .data 2,480）− arena 4,096（SAAN_KANJI で 208 → 204 KB）= **+13.9 KB**。T10 後は −0.5 KB。⚠️ arena 204 KB は W8A8+PIE の 350 ids（≈197,632 + 4.2 KB）に対し余裕 ≈7 KB。
- **依存**: T2〜T4（DRAM を空けてから）。
- [x] **完了（M-90）**: **`esp_partition_mmap` ではなく `esp_mmu_map` を使った**（審査の (b)）。起動ログ
  `連続空き vaddr 23,724,032 B (ESP_OK)` → `esp_mmu_map OK: vaddr 0x3c960000` → `辞書 OK: 見出し語 355768 / エントリ 438750 / 行列 1377x1377`。
  **PSRAM 8 MB と同居して 23.7 MB の連続 vaddr が空いている。**
  flash は app **1.43 MB**（factory 2.75 MB）+ 辞書 13.83 MB = **16 MB ちょうど**。
  かな行と漢字行が **同じ `0xa69a7ebbb5ccb05f`**（ゲート通過）。

### T10. 漢字経路の DRAM を PSRAM と arena へ（P4 + P3）— ✅ **完了**（96257d9 = K-A。実機は **M-90**）

- **変更**: (a) `label_ids.c` の `static char tok[640][16]`（10,240 B）と `saan_kanji.c` の `s_lab / s_tok / s_key`（4,224 B）を G2P 中に借りている arena（204 KB − 130,176 B = 78 KB 余り）へ。(b) Open JTalk の `calloc / strdup / free` を **`csrc/openjtalk/*.c` だけに `-include oj_heap_psram.h`** で `heap_caps_calloc(…, MALLOC_CAP_SPIRAM)` → 内部フォールバックに向ける（K-5 の `oj_heap_probe.h` と同じ手口。取り込んだ C は 1 バイトも変えない = `k4b_vendor.py --check` は通る）。
- **期待効果**: 静的 DRAM −14,464 B（**measured** の .bss 内訳から）。一時ヒープ（ホスト値で最大 97,325 B）が内部 DRAM を先に食って断片化させるのを避ける（**headroom の確保**）。⚠️ 審査: M5 構成は `SPIRAM_USE_MALLOC=y` なので内部が尽きれば既に PSRAM に落ちる。NULL 事故（`mecab2njd` は WARNING で途中 return、`make_label` は size=0。エラーは返らない）は (b) では防げない。**ゲートは「最長 held-out 文で ids 数・ラベル数が K-6 の参照値と一致」**にする。⚠️ (a) は arena の余りを Viterbi に渡す `layout()` の分を 14,464 B 減らすので、Viterbi に渡る実バイト数を起動ログに出す。
- **bit**: 同一。⚠️ G2P の時間は PSRAM アクセスで**伸びる可能性**（estimate 1.5〜3×。セッション 2 で測る）。
- **ゲート**: `make -C csrc kanji-e2e / k7` 不変 / `size -A` で `.dram0.bss` −14,464 B / 実機で G2P 前後の `heap_caps_get_free_size(INTERNAL)` が減らず `(SPIRAM)` が減る。
- **陽性対照**: `-include` を外すと内部 DRAM が減る。
- **依存**: T9。
- [x] **完了（M-90 §3）**: 辞書 + 漢字経路を載せた状態で**起動直後の内部 DRAM free 132,039 B**（最大ブロック 86,016）。
  辞書 mmap 後 131,495 B、**1 発話後 129,155 B** = **Open JTalk の一時ヒープは PSRAM に落ちている**（発話しても内部は 3 KB しか減らない）。
  漢字経路の作業領域 144,640 B は arena 180,224 B から切り出し、Viterbi に 84,736 B 渡っている。

### T11. 入力 1 経路（K-I1）+ ホストとの対 — ✅ **完了**（a746b20 + **3e5cf8e**。実機は **M-90 §1**）

- **変更**（審査で書き直し）: **判定関数を別に書かない。かな G2P のトークン化（`saan_g2p` の検証パス）が行末まで通るか**を判定そのものにする（手書きの文字集合は凍結テーブルとずれる: ぁぃぇぉゃゅょゎゐゑゕゖ は単独ではモーラになれず、`_ ^ $` はマーク側）。規則は **3 値**: (1) トークン化が通る → かな経路 (2) 通らず、行にマーク（`[ ] # °`・`_ ^ $`・`?~` 系）が 1 つも無い → 辞書経路 (3) 通らず、マークがある → **位置つきで拒否**（今の `ERR_UNKNOWN` を維持。「中間表現 + `。`」が黙って辞書経路に回るのを防ぐ）。ログに「経路: かな / 辞書 / 拒否」。`!`（辞書強制）は試験用に残す。カタカナだけの行（`コンニチハ`）は辞書経路 → 辞書に無ければ `jdict_unk_guess` で平板。⚠️ **長い行**: USB Serial/JTAG ドライバの RX リングは既定 256 B で、300 B のかな行を一度に送ると欠ける（M-84）。`usb_serial_jtag_driver_config_t.rx_buffer_size` を `SAAN_CONSOLE_LINE_MAX`（512）以上にする。**ホスト側 `scripts/to_intermediate.py` は `kana_g2p.intermediate_to_phonemes` の同じトークナイザで判定**し、辞書経路の参照値は「`jt.run_frontend(text)`（生 NJD）+ 端末に載っている 4 段（`scripts/k1/g2p_ablate.py` と同じ関数）」と明示する（審査: `run_frontend(..., predict_nani=False, use_sudachi_kanji_yomi=False)` では `process_odori_features` が無条件に入り「odori 無し」と両立しない）。
- **⚠️ 決めること**: `text2mecab`（端末は vendored 済みだが**未呼び出し**、ホストの pyopenjtalk は呼ぶ）をどちらに揃えるか。ASCII・半角を含む文で食い違う。K-6 の G17 は「素性が一致した文」だけを見ているので**未検出**。
- **bit**: 出力が変わる（新しい挙動）。純ひらがな行は**かな経路のまま**（D-040 を守る。辞書経路に回すと「こんにちわ」等の読みが変わる）。
- **ゲート**: `make -C csrc line` に判定の陽性 / 陰性対照（各カテゴリ 1 文字・空行・記号だけ）/ M-63 の入力が経路「かな」で checksum 不変 / `今日は良い天気ですね。`（`!` 無し）が経路「辞書」で同 checksum / ホスト判定と端末判定が held-out 298 文 + 中間表現 298 行で 596/596。
- **依存**: T9。
- [x] **完了（M-90 §1）**: 3 値の判定を入れ、**`!` が要らなくなった**。ホスト判定と端末判定が **596/596** 一致。
  実機で `きょ][おわよ][いて][んきです°ね`（かな経路）と `今日は良い天気ですね。`（辞書経路）が **同じ PCM**、
  `コンニチハ` も辞書経路で読めた。
- [x] **修正 3e5cf8e**: **半角 `?` をマークから外した**。`?` をマーク扱いにしていたため `本当なんでしょうか?` のような
  普通の疑問文が「マークがあるのにトークン化が通らない」= 拒否に落ち、**held-out 2,333 行のうち 45 行（1.93%）が喋れなかった** → **0.00%**。
  ⚠️ **3 値判定は「拒否」を持つぶん、記号 1 つの分類ミスが黙って発話不能に化ける。** ゲート（`make -C csrc kb-parity`）は
  ホストと端末の一致だけを見ていて**両方が同じ理由で拒否する**ケースを捕まえられなかった。
- ⚠️ **`text2mecab` をどちらに揃えるかは未決のまま**（上の「決めること」）。ASCII・半角を含む文で食い違いうる。

### T12. 再生の先読みを M5 のキューに合わせる（P6）— ❌ **未着手**

- **事実**: M5.Speaker のキューはチャンネルあたり `wavinfo[2]`（再生中 1 + 待ち 1）。プリロール 371 ms は最初の 1 スロットを占めるだけで、定常では常に 1〜2 チャンク（93〜186 ms）しか先読みが無い。**xRT ≤ 0.5 になっても先読みは増えない**（`playRaw` でブロック）。main.c の「アンダーラン」は `dt > 音声長` の回数で、**実際に途切れたかは測っていない**。
- **変更**: (a) `SAAN_SPK_MAXBUF` を 4096 にして 2 pull を 1 `playRaw` に束ねる（S7 なら CHUNK 16 と一致）。(b) 定常中に `M5.Speaker.isPlaying() == 0` を検出して「実途切れ」を別に数える。(c) xRT が 0.7 を切ったらプリロールを 4096 sample に落として発話開始 −(2 × 満チャンク step) ≈ −130〜−150 ms（estimate。186 ms は削る音声長であって短縮量ではない。先読みが 186 ms 減る代償も併記）。(b) の `isPlaying()==0` は DMA リング（≈93 ms）の分だけ早く 0 になるので **上界**。連続時間が 93 ms を超えた回数を「推定」として別に出し、陽性対照は「わざと 200 ms sleep を 1 回入れて 1 回数えられる」。
- **ゲート**: 実機で同じ文 n=3。PCM checksum 不変・実途切れ 0・鳴らし始めまでの ms。
- **依存**: セッション 1 の結果（xRT）。
- ⚠️ **入れていない。`SAAN_SPK_MAXBUF` は 2048 のまま**（`esp32/boards/m5unified/main/saan_audio_m5.cpp`）。
  したがって **M-88〜M-90 の「アンダーラン 0」は今も `dt > 音声長` の回数**であって、**実際に途切れたかは測っていない**。
  (b) の実途切れカウンタが無いままなので、**G32（聴取）が「途切れていない」を言える唯一の手段**である。

### T13. CoreS3 向け「焼くだけ」イメージと手順（P7）— ❌ **未着手**

- **変更**: `build_cores3` で `esptool.py --chip esp32s3 merge_bin --fill-flash-size 16MB -o m5-cores3-firmware-kanji-pie-16mb.bin @flash_args`。`esp32/TESTING.md` M 節に: 先に `read_flash 0 0x1000000 backup.bin`（D-047 の手順）、`write_flash 0x0`、コンソールは USB Serial/JTAG、見るログ行、期待 checksum。
- **⚠️ 公開はユーザーと**（D-045: latest だけで完結。README の資産表に名前を書く前にアップロードしないと `check_release_assets` が赤）。
- **依存**: セッション 2 で通ってから。
- ⚠️ **リリース資産の blob は今も v1 で、S4 以降のコアは `SAAN_ERR_VERSION` で拒む**（前史 §10-3 の S4 Step 6）。
  **v2 の blob と CoreS3 イメージは同じリリースで出す**のが筋（§7 #6）。

### 実機セッション 2 — ✅ **やった**（**M-90**。計画は「→ M-86」と書いていたが、M-86 は DevKit 構成の漢字ビルドに使った）

1. T9〜T11 の M5 ビルドを焼く。起動ログ（PSRAM 8 MB / 辞書 OK / W8A8+PIE 有効 / 内部 DRAM free）→ mmap の結果（通った / `esp_err_to_name`）。
2. かな行と漢字行（`!` 無し）で **同じ `0xa69a7ebbb5ccb05f`**。M5 スピーカーから音が出ることを確認（ユーザー）。
3. M-83 の 4 文で G28 を再測（PSRAM ヒープの影響）。**最長 held-out 文（98 文字 / ラベル 214 本）**を打ち、落ちない・短くならないことを確認。
4. 内部 DRAM free の前後 → **M-86**。

### T14〜T17. 漢字 G2P の速度とメモリ（K トラック）。RTF には効かない — ❌ **4 つとも未着手**

⚠️ **着手の前に §7 #4（G28 の基準）を決めること。** 実測は M5 構成で **5.51〜40.79 ms/文**（M-90）で、
「音声長の 1%」なら未達、「合成時間の 1%」（`requirements.md` の分母）なら達成済み。**分母が決まらないと目標が無い。**

| | 変更 | 期待（estimate） | ゲート |
|---|---|---|---|
| **T14 K-S5** | `saan_prof.h` に K_ 区分（ENC / SEL0 / UNK / LINK / FEAT / M2N / PRON / K4B / ACC / JPC / LABEL / K7 / HEAP）を足し、1 文ごとに M-82 と同じ表。要素数は select0 の bit 数・strtab のバイト数・printf 回数・heap 回数 | 測定。「Open JTalk 側が 65〜70%」は**差分による推定**で未測定 | 区間の合計が総時間の ±5%（漏れ検出）/ `SAAN_PROFILE=0` でコード不変 |
| **T15 K-S1〜S4** | `select0` を byte 単位に（8 − popcnt8）/ NUL 区切り表（keyesc 20,880 B・pos6tab 32,612 B）に u16 索引（blob の任意区画、余り 125,776 B 内。旧 blob も読める）/ unk 40 件を起動時に小表へ / `child_of` の rank1 を先頭 1 回に | 辞書側 −25〜−30%（−7〜−17 ms）。（estimate）辞書側は CPU（線形走査）律速の見込み: 触れる行は 69〜100 KB で、M-85 の 259 cyc/行なら 2.3〜3.4 ms 分。**T14 の表を見てから T15 / T16 の順序を決める** | `make -C csrc jdict`（MeCab 1,918/1,918 不変・LOUDS の陰性対照）/ k6 / k7 一致数不変 / QEMU checksum 不変 / 実機 G28 |
| **T16 K-S6** | Open JTalk の `calloc/strdup/free` を arena の bump allocator に（free は no-op、文ごとにリセット。先に `oj_heap_total()` で総確保量を測る）/ `label_ids` の snprintf → memcpy、`label_ids_token_id` → 表 / `saan_kanji.c` の snprintf ×6/ノード → strncpy / **PATCH**: `jpcommon_label.c` の `append_format(vsnprintf)` を手書き整形に（`k4b_vendor.py` の PATCHES に登録、G24 のラベル bit 一致で示す） | −4〜−11 ms（**T14 の表を見てから順序を決める**） | k5 G24（ラベル bit 一致・陽性対照 MAXBUFLEN=64）/ k6 / k7 / `k4b_vendor.py --check` |
| **T17 K-M1** | `LABEL_IDS_MAX_TOKENS` 640 → 256、`TOK_MAX` 16 → 12 / `feat 96×320` 固定 → NUL 詰め込み / `accent_node_t` 524 → ~364 B / Viterbi 48 → 32 KB | 作業領域 130,176 → 約 80 KB | k7 G25〜G27 + M-79 の陽性対照（`LABEL_IDS_MAX_TOKENS=128` で 273/298）/ k4 G12/G13 / k6 G17 / Viterbi 32 KB で 298/298 |

## 4. 期待の合算（当時の **estimate**。**下の「実際」で答え合わせ済み**）

| カーネル（段ではない。TOKEN の積和は MAC 行に含まれる） | 現在（M-84、64 B 行） | T1〜T5 後の見込み | 根拠 |
|---|---:|---:|---|
| MAC（token 込み） | 44.1 ms | 30〜34 | 計算 −37%（token −50% 込み）。flash 行フィルは M-85 の 259 cyc/行 × 8,500 行 ≈ 9 ms が残る |
| GELU | 10.7 | 2〜3 | 要素 −42% × 112 → 60〜75 cyc（M-85 E3） |
| QUANT + DW + LN + RELU | 9.8 | 6〜7 | 範囲 −35〜45% |
| ISTFT + PIPE + 区間外 | 6.8 | 6.8 | 不変 |
| **1 step** | **71.4** | **45〜51**（xRT 0.49〜0.55） | |

**46 ms は T1〜T5 + 64 B 行でぎりぎり（estimate）。** 確実に切るには §5 の両方（S7 で flash 分 −4〜5 ms、dot 固定費の削減）が要る。

### 実際（**measured**）

| | 見込み | 実際 | 出典 |
|---|---|---|---|
| 1 step（PROFILE=1） | 45〜51 ms | **11,724,417 cyc**（T1〜T3 + T5 修正）→ **11,659,500 cyc**（+T4） | M-88 / M-89 |
| 満チャンク 1 pull（PROFILE=0） | — | **46.2 ms → 45.9 ms → 41.4 ms**（+S5b） | M-88 / M-89 / M-90 |
| MAC | 30〜34 ms | 7,610,417 cyc（要素 −42.3%。**cyc/MAC は 1.45 → 1.81 に上がった**） | M-88 |
| GELU | 2〜3 ms | 917,504 cyc（73.14 cyc/要素 × 12,544） | M-88 |
| QUANT + DW + LN + RELU | 6〜7 ms | 750,077 + 618,261 + 117,572 cyc | M-88 |
| ISTFT + PIPE | 不変 | 917,143 + 403,536 → **+T4 で PIPE 350,645**（−13.1%） | M-88 / M-89 |

**見込みは当たった**（1 step は範囲の中、xRT 0.497 で要件を満たした）。
⚠️ **ms への換算は 240 MHz の算術**で、実測は cyc（PROFILE=1）と満チャンク pull の ms（PROFILE=0）の 2 つだけ。
⚠️ **§5 の「両方要る」は結果として要らなかった** — S7 無し・S5b ありで 0.446 に届いた。

## 5. 分岐 — M-85 は「両方要る」と出たが、**S5b だけで足りた**

⚠️ **結果**: **T7b（S5b）は入れた**（e8a7494。M-90 で 0.494 → **0.446**）。**T7a（S7）は入れていない**し、**要らなかった**
（T1〜T5 が計算そのものを −42% 減らした後は、flash 38.5% という比率も M-85 当時のものではない）。
Q-OUTER / K-MERGE / W-DRAM / F-2 / F-4 も**採っていない**。以下は当時の判断材料としてそのまま残す。

M-85 の読み: flash 38.5% / 固定費 61.5%（cinp=48）。

順序: **T7a S7**（bit 同一・実装が軽い・flash 分を半減）→ **T7b S5b**（bit 同一、固定費のうちロード分だけ）→ 足りなければ **Q-OUTER の spike**（QACC 外積形 = 直列チェーンを 16 dot に 1 回に。blob v3）。K-MERGE（出力が変わる）は最後。
⚠️ S7 は **obuf を 2·CH − (36 mod CH) = 28 hop に**（審査: CH=16 で ofill が 28 に達する。(CH+4)=20 のままだと 8 KB を隣に書き、ホストの 25 文が黙って通る）。arena は T2〜T4 後で +55 KB + obuf 8 KB（estimate）。

| P-0 の結果 | 採るもの | 捨てるもの |
|---|---|---|
| **(D3 − D2) が MAC の 1/3 以上**（flash 律速） | **T7a S7** `SAAN_CHUNK 8 → 16`（hout は T=8 × 2 回で `o1539` 49 KB のまま。再生側 `SAAN_SPK_MAXBUF / SAAN_I2S_MAXBUF` を `SAAN_CHUNK*SAAN_HOP` に）。arena は T2〜T4 後で約 +55 KB（estimate。`make -C csrc arena` で測る）。**音声遅延 +93 ms** → ユーザー判断（§7）。**F-1** 64 B 行（設定 1 行）。**F-4** flash 120 MHz は `esptool.py flash_id` でチップの HPM 対応を見てから | W-DRAM（DRAM 常駐は最大 57 KB で −2 ms、S7 と DRAM を争う）/ F-2 `SPIRAM_RODATA`（同じ 4 bit × 80 MHz。D4 で差が無ければ捨てる） |
| **D1 が MAC の 1/2 以上**（固定費律速） | **T7b S5b**（weight-stationary。cinp ≤ 128 の層で重み行を q レジスタに 1 回ロード。利得上限 −2〜−8 ms）→ 足りなければ **Q-OUTER**（QACC 外積形、blob v3。⚠️ 20 bit レーンの溢れ、QEMU の命令実装を spike で先に確認） | K-MERGE（タップ併合。量子化粒度が変わる = 出力変化。SCOREQ 測り直しが要る。最後の手段） |
| 両方が同格 | S7 → S5b の順（S7 は bit 同一で実装が軽い） | |

## 6. docs の更新箇所

✅ **書いた**（計画時の見込みと実際の番号がずれているので、実際の方を書く）:

| 計画 | 実際 |
|---|---|
| M-84（T1〜T5 後の実機表） | **M-84** = D-cache 64 B 行の A/B（0.926 → 0.861）。T1〜T5 の実機は **M-87**（T1 + T5、GELU の退行）/ **M-88**（+T2 + T3、**要件達成**）/ **M-89**（+T4） |
| M-85（P-0 / GELU マイクロベンチ） | **M-85**（そのまま） |
| M-86（漢字 + PIE の M5） | **M-86** = 漢字 + PIE の **DevKit** 構成（QIO / 64 B 行）。**M5 は M-90** |
| C-054 / D-048 / D-049 / D-050 | **C-054**（M-82 §4 の仮説 4 つ）/ **C-055**（命令数は速さの証拠にならない）/ **D-047**（板の同定）/ **D-048**（W8A8+PIE を既定に）は書いた。⚠️ **D-049（RTF の分母）と G28 の基準は未決** |

- checksum の基準値: `esp32/TESTING.md` / `esp32/boards/m5unified/README.md` / CLAUDE.md ほかの期待値表 — **T1〜T5 と S5b は全部 bit 同一だったので変更なし**（`0xa69a7ebbb5ccb05f` / `0xe4b645c30835d42d` のまま）。
- 索引: `docs/README.md`（**M-1〜M-90 / D-001〜D-048 / C-001〜C-055**）、CLAUDE.md の現在地、README（日英）の表。`requirements.md` §6.2 の分母は**未決のまま**（§7 #1）。
- `check_doc_counters.py` が見る数字（M / D / C の最大番号）。

## 7. 決めてもらうこと（ユーザー判断）

| # | 何 | 状態 | 材料 |
|---|---|---|---|
| 1 | **RTF ≤ 0.5 の分母**を「満チャンク step / チャンク音声長」で確定するか。初回 pull と鳴らし始めまでは別要件にするか | ⚠️ **未決（D-049 は空き番）**。**満チャンクの定義では 0.446 で達成、発話全体では 0.541〜0.712 で未達**（M-90）。ファームは両方をログに出す | requirements §6.2 に遅延の数値要件が無い。鳴らし始めまで **384 ms**（M-90。かな構成は 432〜434 ms = M-88 / M-89） |
| 2 | **S7 の音声遅延 +93 ms** を対話用途（スタックチャン）で許容するか | ⏸ **要らなくなった**（S7 無しで要件を満たした）。S7 を余裕のために採るなら生き返る | パイプ 38 フレームは CH に依らず、S7 で増えるのは出力 8 フレーム分 |
| 3 | **W8A8+PIE を出荷ファームの既定にするか** | ✅ **決めた = D-048**（ESP32-S3 では既定。W8A32 は xRT 4.28〜4.62 で実時間に間に合わない） | M-55（知覚的に無料）/ M-89（arena も収まる） |
| 4 | **G28 の基準**: 「音声長の 1%」（K 計画。未達）か「合成時間の 1%」（**requirements.md の分母**）か「≤ 20 ms/文」の絶対値か | ⚠️ **未決**。実測は M5 構成で **5.51〜40.79 ms/文**（M-90） | 1% は初回遅延にもアンダーランにも直接対応しない。分母が 2 つある |
| 5 | **入力自動判定で純ひらがな行をかな経路（平板）のままにする**か | ✅ **そうした**（T11 / M-90）。純ひらがな行はかな経路のまま = D-040 を守った | 辞書経路に回すと「こんにちわ」等の読みが変わる |
| 6 | **次のリリース**の中身（v2 blob / USB-JTAG 入力イメージ / CoreS3 イメージ） | ⚠️ **未決**。⚠️ **今のリリース資産の blob は v1 で、S4 以降のコアは拒む** | D-045（latest だけで完結） |

## 8. 何を測っていないか（この計画の限界）

**2026-09-03 の到達後に書き直した。** 消した行は「もう測った」もの、残した行は今も測っていないもの。

- ⚠️ **G32 の対照つき聴取が最後の 1 つ。** ⚠️ **ざっとした聴取は済んでいる**（M-91 / M-93 = 実機 / M-96 = ブラウザ。どれも**1 名・対照なし・盲検なし**）。**残るのは対照つきの聴取**（`reports/k8_listen/` の 12 組 / `reports/d4_accent/`）。
  checksum が M-82 から同一なので**波形は 1 bit も変わっていない**が、
  「途切れて聞こえないか」「漢字経路の読みが妥当か」は聴くまで分からない。
  ⚠️ **T12 (b) の実途切れカウンタを入れていない**ので、`アンダーラン 0` は `dt > 音声長` の回数であって**実際に途切れたかではない**。
- ⚠️ **発話全体の RTF は 0.5 を超える**（0.541〜0.712。M-90）。warmup 38 フレームが初回 pull に乗る。**要件の分母が未定**（§7 #1）。
- ⚠️ **S5b の寄与を分離していない**（S5b + 漢字構成をまとめて焼いた。M-90 §5）。pie_probe の D6 / D7 を実機で読むのが本筋。
- 実機の PROFILE 表は **53 ids の 1 文**。350 ids では TOKEN の span 分布・`make_hf` の O(n_ids) 探索・arena の ids 比例分が変わる
  （arena peak だけは 782 frames でも測ってある = 158,928 B。M-89）。
- **実サンプルレートの誤差は未測定**（ファーム自身が警告を出す。M-90 §5）。M-90 の checksum 3 つ（84 / 41 / 29 ids）は**初出で参照値が無い**。
- **DevKit 構成（`esp32/`）では T1〜T5 / S5b を測っていない**。同じコアなので同じ削減が効くはず（**estimate**）。
- 第三者報告の `ESP_ERR_NO_MEM`（PSRAM 有効 + mmap）は**直接は再現していない**。
  ⚠️ ただし **原因は特定できた**: M5 構成は `CONFIG_SPI_FLASH_ROM_IMPL=y` で、ESP32-S3 では `spi_flash_mmap` が ROM 実装にリンクされ、
  IDF はそれに **128 ページ = 8 MB** しか渡さない。辞書は **211 ページ**なので貼れない。`esp_mmu_map` に替えて通した（M-90 §4）。

## 9. 審査で直した点（2026-09-03、3 名。全部コードで検算済み）

| 箇所 | 誤り | 直し |
|---|---|---|
| T1 陽性対照 | `< n_frames − 1` は本番条件と等価で**落ちない** | `− 8`（demo が 98/106 で切れる）。⚠️ 再審査で `− 8` も **≡ 3, 4 (mod 8) を見逃す**と分かった（prefix 53 件で 40/53）。全残差で落ちるのは `− (CH+2)` = `− 10`（53/53） |
| T1 メモリ / xRT | 「メモリ 0」/ 新 xRT を M-82 と並べる | main.c の .bss +640 B（控え 1,024 → 640 B に縮めた）/ **定義が違うので並べない**。T1 前後は「合成合計 ms」と STEP 行で比べる |
| T4 (d) | obuf は `(CH+2)` で足りる | **足りない**。最大 ofill = 2·CH − (36 mod CH) = 12 = CH+4。取り下げ |
| S7 | obuf の言及なし | CH=16 で **28 hop** 要る（ホスト 25 文が黙って通る壊れ方） |
| T2 | dw / cdown / cup は窓全部 | dw の**入力**だけ窓全部。出力は中央 CH（DW −43% はこれで出る） |
| §1 | dot 109,152 / l32r 13 / 「171 vs 87 フレーム」 | 109,358 / l32r 11 / conv 出力**列** 169 vs 85 |
| §0 | 76.58 ms = PROFILE=0 の値のように読める | PROFILE=1。PROFILE=0 は算術 75.4 ms |
| §4 | TOKEN（段）と MAC（カーネル）を足して二重計上 | カーネルだけの表に組み直し |
| T5 陽性対照 | クランプ 3.9999f で落ちる | 落ちない（V[127] も 1.0f）。3.9f |
| T6 | pie_probe の sdkconfig が DIO / 160 MHz / 32 KB | cores3 相当（QIO / 240 / 64 KB）を重ねた。実測は M-85 |
| T9 | mmap は vaddr が余るので通る | M5 は `SPI_FLASH_ROM_IMPL=y` → ROM 実装の **128 ページ制限**で落ちる。`esp_mmu_map` に |
| T9 | DRAM +18 KB | 正味 +13.9 KB（arena −4 KB） |
| T10 (b) | NULL 事故を構造的に防ぐ | 防がない（M5 は既に PSRAM フォールバック）。headroom の確保 |
| T11 | 手書きの文字集合で判定 | かな G2P のトークン化で判定、3 値（拒否を維持） |
| T11 | 参照値 `run_frontend(…)` + odori 無し | 両立しない。生 NJD + 端末の 4 段 |
| T12 (c) | −186 ms | −(2 × step) ≈ −130〜150 ms |
| §7 #4 | G28 の分母は 1 つ | requirements（合成時間）と K 計画（音声長）で違う |
| 不足 | stream G2 が 1 文だけ | **held-out 24 文 × {fp32, W8A8} を all-test に**（T2a） |
| 不足 | prof の期待値が目視 | `--expect-gelu / --expect-dw / --expect-mac-le` |

---

## 10. 前史 — **S-1 計画（2026-09-02）の要約**

> ⚠️ **元ファイル `s1-speed-implementation-plan.md` はこの節に畳んで削除した**（2026-09-03）。
> A-0〜A-5 と S1〜S5a は全部完了し、残った **S5b と D-048 はこの S2 計画で決着した**（§5 / §7 #3）。
> 一次記録は **M-80 / M-81 / M-82 / D-046 / D-047**、調査は [`../research/s1-m5-cores3-speed.md`](../research/s1-m5-cores3-speed.md)。

**起票の理由**: 第三者が M5Stack CoreS3 で **W8A8+PIE 1.554× RT**（実時間に間に合っていない）と報告した（2026-09-02。**私は未再現**）。
独立した 2 件目（AtomS3、PSRAM 無し、配布 firmware そのまま）も **1.718× RT**。どちらも checksum は M-62 と bit 一致 = **値は正しく、時間だけが遅い**。
内訳を取ると（M-80）、**活性化の量子化 / GELU / テンソル検索 / 重みのコピーが MAC と同等以上**で、**PIE を磨いても消えない**と分かった。
ブランチは `feat/s1-speed-m5`（S2 計画も同じブランチ）。

### 10-1. 引き継いだ原則（S2 でもそのまま守っている）

- **推論コアは `csrc/` の 1 か所。** `esp32/` はコピーせず相対参照する
- **同じカーネルを 2 回書かない。** checksum の実装も 1 か所（`esp32/main/saan_pcm.c`）
- **出力を変えるステップは「bit 同一」か「丸め水準」かを先に宣言する**（**D-046**）。
  丸め水準なら `|max|` 完全一致 + Σx² 相対差 ≤ 1e-6 + W8A8 の fp32 比 SNR 分布で示す
- **数値は自己実測だけ M-番号にする。** 第三者の値は「報告値」（`docs/upstream-sanotts.md` と同じ扱い）
- **黙って遅い経路に落ちない。** 効かない構成（fp32 blob + W8A8 / v1 blob + v2 コア / ESP32-S3 以外で PIE）は起動時か CMake で止める
- GPL の公式実装 `Ampixa/sanoTTS` のソースは読まない（D-032）。取り込んだのは MIT の
  `nnn112358/SanoTTS-jp-M5StackCoreS3` だけで、ファイル冒頭に出所を書いてある
- 各コミットで `make -C csrc all-test` と `bash scripts/check_esp32_template.sh` が通る

### 10-2. Phase A — M5Unified 対応の取り込み（**A-0〜A-5 完了**）

| Task | やったこと | 記録 |
|---|---|---|
| **A-1** | 音声出力を抽象 API `saan_audio_*`（7 関数）にし、float→int16 と 4 統計（checksum / clip / \|max\| / Σx²）を `saan_pcm.c` の**唯一の実装**に集約。画面も `saan_ui_*`（4 関数）で抽象化 | `1371332`。host stub bit 一致 ×2 / QEMU `0x04de91103a0e49f9` |
| **A-2** | 重みを `.rodata` に埋める経路（`blob_to_header.py` + `saan_model_rodata.c`。`-DSAAN_MODEL_RODATA=1`）。**PSRAM 有効な板で mmap が落ちる**という報告への対応 | `74a88a3`。app 285,440 → 928,832 B。ゲート `test_blob_to_header.py`（**fp32 blob を拒む陽性対照**つき。CI の docs job） |
| **A-3** | `esp32/boards/m5unified/`（M5.Speaker / M5GFX / タッチ再生 / `SAAN_BUFFERED`）。`saan_console_readline` を `saan_console_poll(timeout)` に | `78a55af`。cores3 1,344,432 B・.bss 235,600 / core2 1,331,808 B・.bss 22,752 |
| **A-5** | `esp32/TESTING.md` の M5 節 / README の板の選択 | `12ea114` |
| **A-0** | **板の同定** = ESP32-S3（QFN56 rev v0.2）/ 16 MB / USB-Serial-JTAG = **CoreS3 系** | **D-047** |
| **A-4** | **実機で初めて自己実測した**: checksum が QEMU と完全一致 / 定常 xRT **0.926**（第三者報告 1.554 から −40%）/ アンダーラン 1/14 / 内訳 MAC 63.9%（1.61 cyc/MAC）・GELU 14.0%（118 cyc/要素）・TOKEN 11.3% | **M-82** |

**板ごとの期待 checksum**（S3 以降のコア）: ESP32-S3 = `0xa69a7ebbb5ccb05f`（W8A8+PIE）/ ESP32 = `0xe4b645c30835d42d`（W8A32）。
**一致しなければ速度は測らない**（移植が壊れている）。同定は `esptool.py chip_id`、または外見（カメラ穴 = CoreS3 / タッチの丸 3 つ = Core2 / 物理ボタン 3 つ = Basic）。

### 10-3. Phase B — 速度 S1〜S5a（**全部完了**。ホスト → QEMU → 実機の順）

**各 Task で「bit 同一か丸め水準か」を先に宣言した**（D-046）。丸め水準は S2 と S3 の 2 つだけ。

| Task | 変更 | 出力 | 実測 |
|---|---|---|---|
| **S1** | テンソル検索を init で 1 回に（`resolve_weights`。計算順序は 1 つも変えない） | **bit 同一** | pull 中の LOOKUP **42,280 → 0 回**（20 発話）。ゲート `./csrc/prof_test --expect-no-lookup`（S1 前は 101.81 回/step で落ちる）。QEMU icount 1 step 811,001 → 741,973 |
| **S2** | 活性化の量子化から**ソフト除算と `rintf` 呼び出し**を消す（逆数乗算 + `round.s`） | **丸め水準**（宣言どおり） | W8A8 e2e 平均 24.24 → 24.21 dB・最小 21.94 不変。QEMU checksum は**この 1 文では動かなかった**。pie_probe C 節で `round.s == rintf`（22 値、陽性対照つき） |
| **S3** | GELU の `erff` を **3 次 Hermite 表**（h=1/32）に | **丸め水準。基準 checksum が動いた** | `erff` との max\|Δ\| **1.19e-7**（陽性対照の線形補間 1.18e-4 は落ちる）。**新基準**: W8A8+PIE `0x04de91103a0e49f9` → **`0xa69a7ebbb5ccb05f`**（\|max\| 9744 → 9627）/ W8A32 `0x78c209af06affc01` → **`0xe4b645c30835d42d`**（\|max\| 9529 同一・Σx² 相対差 8.7e-9）。fp32 golden 118.97 dB。icount 557,152（S1 前比 −31%） |
| **S4** | **blob v2** — 重みを `[cout][k][align16(cin)]` で書き、実行時の転置を無くす | **bit 同一** | 24 文 cmp 0/24 / QEMU 両構成の checksum 不変。blob 643,936 → **654,032 B**。WCOPY 0 回。**v1 の int8 blob は `SAAN_ERR_VERSION` で拒否**（陰性対照）。icount 454,548（−44%） |
| **S5a** | PIE の内積をロード併合（`ee.vld.128.ip` + `ee.vmulas.s8.accx.ld.ip`）+ `loopnez` | **bit 同一** | pie_probe 7 形状 / QEMU checksum 不変。16 MAC あたり 5 → **2 命令**。icount **412,619**（S1 前比 **−49%**） |

⚠️ **この −49% は QEMU の命令数比であって速度ではない**（C-055）。実機で測ったのは M-84 以降。
⚠️ **S4 の宿題が 1 つ残っている**: **リリース資産 `saanotts-jp-v3-int8.bin` はまだ v1** で、S4 以降のコアは `SAAN_ERR_VERSION` で拒む。
外向きの作業なので**マージ時にユーザーと**上げ直す（§7 #6）。

### 10-4. S-1 が S2 に引き継いだもの（**全部決着した**）

| 引き継ぎ | 決着 |
|---|---|
| **S5b**（重み行をレジスタに保持） | ✅ **入れた**（§5 の T7b。e8a7494。bit 同一、M-90 で 0.494 → 0.446） |
| **D-048**（出荷ファームで W8A8+PIE を既定にするか） | ✅ **既定にした**（ESP32-S3 のみ。W8A32 は xRT 4.28〜4.62） |
| S6（token の持ち越し）/ S7（`SAAN_CHUNK` 16）/ S8（2 コア） | S6 = **T3 で入れた**。**S7 / S8 は未着手**（要件を満たしたので余裕のための選択肢） |
| QACC 外積形（Q-OUTER）の spike | ❌ 未着手（20 bit 溢れの解析が要る） |
| K トラック × PSRAM: `esp_partition_mmap` が落ちる報告の切り分け | ✅ **切り分けた**（`CONFIG_SPI_FLASH_ROM_IMPL=y` → ROM 実装の 128 ページ制限。`esp_mmu_map` で解決。M-90 §4） |
