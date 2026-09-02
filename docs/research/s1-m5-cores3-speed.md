# S-1. 実機で初めて速度が出た — M5Stack CoreS3 の第三者報告と、1.55× RT の内訳

作成 2026-09-02。関連: [M-80](../measurements.md#m-80)（自己実測 / ホスト + QEMU）。

**進捗**: §5 の S1〜S3 と §6 の取り込みは実装済み（[M-81](../measurements.md#m-81) /
[`../plan/s1-speed-implementation-plan.md`](../plan/s1-speed-implementation-plan.md) の進捗表）。実機は板待ち。

## 0. 何が起きたか

**2026-09-02、第三者が本リポジトリの firmware を M5Stack CoreS3 に移植して実機で動かし、
速度を報告した**（[nnn112358/SanoTTS-jp-M5StackCoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)、
MIT。`components/saanotts_core/` は本リポジトリ `csrc/` の commit `2c61a8d` のコピー）。

**このプロジェクトで初めての実機の数値**であり、同時に**初めて「実時間に間に合っていない」ことが分かった。**

⚠️ **以下の表は第三者の報告値。私自身は再検証していない**（ボードが無い）。
[`upstream-sanotts.md`](../upstream-sanotts.md) と同じ扱いで、M-番号とは混ぜない。

| 報告値（CoreS3 / ESP-IDF v5.5.5 / -O2 / 240 MHz / 53 ids・106 frames・音声 1.231 s） | W8A32・PIE 無し | **W8A8 + PIE** |
|---|---:|---:|
| `saan_stream_init` | 68.95 ms | 23.19 ms |
| 初回 pull（warmup） | 2,546.98 ms | 766 ms |
| 2 回目以降の pull（1 チャンク = 92.88 ms の音声） | 448.95 ms | **144 ms** |
| **定常 xRT** | 4.834 | **1.554** |
| ストリーミングでの途切れ | 10 / 14 チャンク | 10 / 14 チャンク |
| 出力 PCM（FNV-1a / \|max\| / Σx²） | `0x78c209af06affc01` / 9529 / 74,155,592,149 | `0x04de91103a0e49f9` / 9744 / 74,374,063,946 |
| 静的 DIRAM / 起動直後の内部 DRAM free | — | 290,227 / 341,760 B ・ 102,895 B |

報告者は **checksum を本リポジトリの QEMU 記録（M-62）と突き合わせて「27,136 sample すべて bit 一致」**としている。
値は M-62 の表と一致する（`0x04de91103a0e49f9` / 9744 / 74,374,063,946）。
**移植が正しいことの根拠として使ってよいのはこの一致だけ**で、速度の数字はまだ誰も再現していない。

### 報告者が踏んだこと（本リポジトリに取り込むときの前提になる）

| 事象 | 対処（報告者） | 本リポジトリへの含意 |
|---|---|---|
| `CONFIG_SPIRAM=y`（8 MB Quad）だと `esp_partition_mmap` が `ESP_ERR_NO_MEM` | 重み blob を **ヘッダ化して app の `.rodata`** に埋める（`scripts/blob_to_header.py`） | `esp32/main/saan_model.c` のパーティション mmap 方式は **PSRAM 有効な板では動かない**（報告）。K トラックの辞書 13.7 MB もこの経路なので**要検討** |
| `.dram0.bss` が 10 KB 溢れる | 音声バッファをヒープ（PSRAM 優先）に / `FREERTOS_PLACE_*_INTO_FLASH` 等 / `set(COMPONENTS main)` | M5Unified + M5GFX が DRAM を食う。arena 208 KB との同居は**設定で解決済み**（報告） |
| `CONFIG_SPIRAM_MODE_OCT` でブートループ | CoreS3 は **Quad** | ボードごとに sdkconfig が要る |
| M5 の 22.05 → 44.1 kHz リサンプル | `SAAN_SPK_OUT_RATE 22050`（AW88298 は 22.05 kHz 対応） | 実サンプルレートの誤差は**未測定**（報告者も未測定） |
| `M5.Speaker.playRaw` はコピーしない | バッファ 3 枚回し / 再生完了を待ってから解放 | **本リポジトリの `saan_i2s.c` とは生存期間の契約が違う**。同じ API で 2 実装にするなら注意 |
| タッチとスピーカーが同じ I2C | `M5.update()` と描画は合成タスクからだけ | — |

## 1. 移植の中身（読んだ範囲。コードは全部読んだ）

| 場所 | 本リポジトリとの差 |
|---|---|
| `components/saanotts_core/*.c` | **`csrc/` と同一**（`M_PI` の `#ifndef` が無いだけ = 401cc3b の前のコピー）。**推論コアは 1 行も変わっていない** |
| `main/main.c` | `esp32/main/main.c` から K トラックと `SAAN_INTERACTIVE` の分岐を外し、`saan_i2s_*` → `saan_speaker_*`、画面（`saan_ui`）と `SAAN_BUFFERED` を追加 |
| `main/saan_speaker.cpp` | **M5Unified の `M5.Speaker`**（C++）。`saan_f32_to_i16()` と checksum は `saan_i2s.c` から逐語コピー |
| `main/saan_model.c` | パーティション mmap をやめ、生成ヘッダ `saan_model_blob.h`（`const uint8_t[]`、aligned(16)）を読む |
| `sdkconfig.defaults` | Quad PSRAM 80 MHz / USB Serial-JTAG / D-cache 64 KB / IRAM のコードを flash へ |
| `partitions.csv` | 16 MB / factory 3 MB / **`model` パーティション無し** |

**推論コアが同一なので、1.55× RT は「コアの速度」そのもの**である。M5 層（スピーカーのタスク、画面）は
合成の外側にいる（M5Unified の speaker task は priority 2、合成タスクは 5。S3 は 2 コア）。

## 2. 報告値をどう読むか（算術）

1 step（= `step_chunk` 1 回 = 8 フレーム = 92.88 ms の音声）に対して:

| | 式 | 値 |
|---|---|---:|
| 実機 W8A8+PIE の 1 step | 144 ms × 240 MHz | **34.6 M cycles** |
| 1 step の MAC 数（ホストで数えた。M-80） | — | **7,279,646** |
| サイクル / MAC | 34.6 M ÷ 7.28 M | **4.75** |
| PIE の理論値（`ee.vmulas.s8.accx` = 16 MAC / 命令、1 命令 / cycle と仮定） | 1 / 16 | 0.0625 |
| **理論値との比** | 4.75 ÷ 0.0625 | **76 倍** |
| 論文の 0.22× RT を 1 step に直すと | 92.88 ms × 0.22 × 240 MHz | 4.9 M cycles |

⚠️ 「4.75 cycles/MAC」は**スカラ C の積和より遅い**数字である。PIE が効いていないのではなく
（QEMU で 5 命令が出ていることは M-62 で確認済み）、**積和以外が支配的**だと読むしかない。
これは M-43 が「η（アプリ効率）」と呼んでいたもので、上流も
*"the float glue, not the MACs, dominates"* と書いている（[`upstream-sanotts.md`](../upstream-sanotts.md)）。

**どこに行っているかは測るまで分からない**ので、測った（次節）。

## 3. 自分で測ったこと（M-80。⚠️ ホストと QEMU。実機ではない）

### 3.1 静的事実（Xtensa GCC 14.2 / `-O2` / W8A8+PIE の `.o` を `nm -u` した）

| 関数 | 要素ごとに呼んでいるもの |
|---|---|
| `saan_quantize_act_i8p`（活性化の量子化） | **`__divsf3`**（ソフト浮動小数の除算。S3 の FPU に除算は無い）+ **`rintf`**（libm 呼び出し） |
| `saan_gelu` | **`erff`**（libm 呼び出し） |
| `saan_layernorm_c` | `__divsf3` ×2 + `sqrtf`（フレームごと） |
| `saan_tensor` | **`strncmp`**（ヘッダ 104 B × 最大 183 エントリを線形走査） |

### 3.2 1 step あたりの回数と要素数（ホストで数えた。**回数はプラットフォームに依らない**）

`make -C csrc prof`（新設。`csrc/saan_prof.h` の区間を W8A8 で回す）:

| 区間 | 回数 / step | 要素 / step | 何の数か |
|---|---:|---:|---|
| LOOKUP（`saan_w` / `saan_tf`） | **101.8** | **15,997** | 走査したヘッダのエントリ数。× 104 B = **1,663,688 B / step** の `strncmp` |
| QUANT | 43.3 | **50,998** | `__divsf3` + `rintf` を通る活性化の数 |
| WCOPY（重みの転置/コピー） | 4,724 | **489,304 B**（実機構成） | flash → スタック `wt` へ 1 バイトずつ写す量（⚠️ ホストの表では 171,863 B。PIE 無しなので ksz>1 の層しか写さない。実機構成は全 conv 層） |
| MAC | 4,724 | **7,279,646** | 積和の数（内訳: DEC 5 段が最大） |
| GELU | 6 | **21,664** | `erff` の呼び出し数 |
| DW（depthwise。PIE に載らない） | 5 | 37,240 | |
| TOKEN（token block の再計算） | 0.67 | 19 トークン幅 | 毎チャンク窓ごと計算し直している |
| 1 step で参照する重み（blob ヘッダから数えた） | — | **584,320 B** | duration を除く全テンソル（int8 544,292 + fp32 + scale） |

### 3.3 時間の内訳（**QEMU。サイクルではない**）

`idf.py -DSAAN_PROFILE=1 -DSAAN_QEMU=1 -DSAAN_ENABLE_PIE=1 -DSAAN_BOOT_SPEAK=1 build` →
`qemu-system-xtensa ... -icount shift=0`（命令数に比例する仮想時間）。checksum は `0x04de91103a0e49f9` のまま
（プロファイラは値を変えない）。

| 区間 | % STEP（icount） | % STEP（icount 無し） | ホスト（M4 Max）% |
|---|---:|---:|---:|
| **MAC** | **34.4** | 21.8 | 50.6 |
| **QUANT** | **21.1** | 30.0 | 7.3 |
| **GELU** | **14.3** | 25.3 | 8.4 |
| **WCOPY** | **10.5** | 5.3 | 6.2 |
| **LOOKUP** | **8.5** | 7.5 | 6.9 |
| DW | 5.5 | 4.0 | 2.9 |
| ISTFT | 3.2 | 2.5 | 1.7 |
| PIPE / LN / RELU | 2.3 | 2.2 | 2.9 |
| 段別: DEC / AC / HF(TOKEN) / HEAD / DINP | 59.8 / 17.0 / 10.2 / 7.7 / 1.1 | 68.6 / 12.3 / 8.2 / 6.4 / 1.0 | 43.6 / 26.0 / 16.1 / 10.0 / 1.8 |

**QEMU の 2 つの走らせ方とホストで順序は揺れるが、共通しているのは 1 つ**:
**QUANT + GELU + LOOKUP + WCOPY で命令数の 40〜55%** を使っていて、**MAC は 22〜50%** しかない。
実機ではこれに **flash からの読み出し**（重み 584 KB + ヘッダ走査 最大 1.66 MB / step。
80 MHz QIO の理論上限 40 MB/s で割っても **56 ms** = 報告値 144 ms の 39%）が上乗せされる。
⚠️ ヘッダ走査の flash 読み出し量は **D-cache のヒット率で 0.25〜1.66 MB の幅**がある
（1 段の 8 回の検索は連続していて 2 回目以降はキャッシュに残りうる。**実機でしか決まらない**）。

⚠️ **実機の内訳はまだ無い。** 同じ表を実機で取るための仕組みは入れた（`-DSAAN_PROFILE=1` で CCOUNT を使う）。

## 4. 仮説（順位付き）と、実機で何を見れば白黒つくか

| # | 仮説 | 根拠（測った） | 実機で見るもの |
|---|---|---|---|
| **H1** | 活性化の量子化で要素ごとにソフト除算と `rintf` を呼んでいる | 静的事実 + 50,998 要素/step + QEMU で 21〜30% | プロファイル表の QUANT |
| **H2** | GELU が要素ごとに `erff` を呼んでいる | 静的事実 + 21,664 要素/step + QEMU で 14〜25% | GELU |
| **H3** | テンソル検索を毎 step 102 回、ヘッダ 1.66 MB ぶん `strncmp` している。実機ではこれが flash から読まれる | 15,997 エントリ/step + QEMU で 8% | LOOKUP（実機は flash ぶん QEMU より大きいはず） |
| **H4** | 重み 489 KB/step を flash からスカラで 1 バイトずつスタックに写している | WCOPY 489,304 B/step + QEMU で 5〜10% | WCOPY |
| **H5** | PIE の内積ループが 16 MAC ごとに 5 命令 + 呼び出しごとの `ee.zero.accx` / `ee.srs.accx` + float の後処理 | `saan_dot_i8_pie` のコード | MAC の cyc/要素（理論 0.0625） |
| H6 | 重み 584 KB/step の flash 帯域そのもの | 算術（40 MB/s で 15 ms） | 全部直したあとに残る床 |
| H7 | token block を毎チャンク窓ごと計算し直している | TOKEN 0.67 回/step × 19 トークン幅 | TOKEN |

**H1〜H4 は全部「MAC ではない」ので、PIE をいくら磨いても消えない。**
そして **4 つとも出力を変えずに直せる**（H3 / H4 は bit 同一、H1 / H2 は丸め水準の差。§5）。

## 5. 直し方（順序つき）と受け入れ条件

**原則: 1 つ直すごとにホスト（`make -C csrc all-test`）→ QEMU（checksum）→ 実機（プロファイル表）の順で測る。
効果を「見込み」で足し合わせない。** 数字は実機の表が出るまで書かない。

| 順 | 何 | 出力への影響 | ゲート |
|---|---|---|---|
| **S1** | **テンソル検索を `saan_stream_init` で 1 回に**（解決済みポインタを `saan_stream_impl` に持つ） | **bit 同一** | all-test / QEMU checksum `0x04de91103a0e49f9` 不変 / プロファイルの LOOKUP が 0 回 |
| **S2** | **量子化の除算を逆数の乗算に、`rintf` を FPU の丸め命令に** | ⚠️ **丸め水準で変わる**（`x/s` と `x*(1/s)` は最終ビットが違いうる） | int8-e2e の SNR ゲート（平均 ≥ 27 / 最小 ≥ 25 dB）/ `\|max\|` と Σx² で丸め差の水準 / **新しい checksum を M-番号で記録** |
| **S3** | **GELU の `erff` を多項式近似に**（絶対誤差 1e-7 級。`expf` も呼ばない） | 丸め水準 | 同上 + ホストで `erff` 版との max\|Δ\| を陽性対照つきで測る |
| **S4** | **重みを blob の時点で `[cout][k][align16(cin)]` に並べ替えて 0 埋め**（exporter 変更、blob v2） | **bit 同一**（順序を変えない） | all-test / `export_c_weights.py` の一致ゲート / QEMU checksum |
| **S5** | PIE ループを `ee.vmulas.s8.accx.ld.ip`（ロード併合）に、重みをレジスタに置いたまま t を回す | **bit 同一**（整数） | pie_probe の bit 一致 / QEMU checksum |
| S6 | token block の出力をトークン単位で持ち越す（H7） | bit 同一（ハロー 12 は保つ） | stream_test G2（一括版と bit 一致） |
| S7 | `SAAN_CHUNK` 8 → 16（窓の再計算率 AC 2.0→1.5 倍、DEC 1.75→1.375 倍、flash 読み出しとヘッダ走査が半分） | bit 同一 | arena（`o1539` は 2 分割で 49 KB のまま）/ 遅延 +93 ms を requirements と照合 |
| S8 | **2 コア**（出力チャネルを 2 分割して並列） | bit 同一（各チャネルの計算は同じ） | 同上 + DRAM（タスク 2 本目のスタック） |

⚠️ **S2 / S3 は「ホストとターゲットは bit 一致しない。それは正常」（M-62）の水準に収まることを、
`\|max\|` 完全一致 + Σx² 相対差 1e-7 級で示す。** 収まらなければ近似の次数を上げる。
⚠️ **S7 / S8 は最後。** 先に S1〜S5 で 1 コアの床を出さないと、何倍になったか分からない。

## 6. M5Stack 対応を本リポジトリに取り込んだ形（✅ 実装済み。2026-09-02、Task A-1〜A-3）

報告者の構成をそのまま入れるのではなく、**`csrc/` を相対参照する本リポジトリの流儀**（コピーしない）に合わせた。
計画と各 Task のゲートは [`../plan/s1-speed-implementation-plan.md`](../plan/s1-speed-implementation-plan.md)。

| 何 | どこ | 検証 |
|---|---|---|
| 音声出力の抽象 API | `esp32/main/saan_audio.h`（7 関数 `saan_audio_*`）。float→int16 と checksum は **`saan_pcm.c` が唯一の実装** | host stub で C 一括版と bit 一致 ×2 / QEMU checksum 不変 |
| 実装 2 つ | `saan_i2s.c`（DevKit、既存）/ `esp32/boards/m5unified/main/saan_audio_m5.cpp`（M5.Speaker。報告者のコード由来、MIT） | M5 側は `idf.py build` まで（音は板が要る） |
| 重みの置き場 2 つ | `saan_model.c`（mmap）/ `saan_model_rodata.c`（`.rodata`。`-DSAAN_MODEL_RODATA=1`）。`scripts/blob_to_header.py` を取り込み、回帰 `scripts/test_blob_to_header.py`（fp32 拒否の陽性対照つき） | QEMU で両経路とも `0x04de91103a0e49f9`。app 285,440 → 928,832 B |
| ボードごとのプロジェクト | `esp32/boards/m5unified/`（`sdkconfig.cores3` / `sdkconfig.core2`、16 MB の `partitions.csv`、M5Unified は Component Registry） | cores3（PIE）1,344,432 B・.bss 235,600 B / core2 1,331,808 B・.bss 22,752 B でビルド通過 |
| 画面 | `saan_ui.h` + `saan_ui_null.c` / `saan_ui_m5.cpp`。タッチで直前の列を再合成（コンソールは poll 化） | QEMU の UART で 2 行入力 → M-63 と同じ checksum |
| `SAAN_BUFFERED` | 貯めてから鳴らす（途切れない。待ちは合成時間） | 板が要る |

⚠️ **ESP32（Core2 / Basic）は静的 arena 208 KB が `dram0_0_seg` に入らない**（65,368 B 溢れた）。
`SAAN_ARENA_HEAP=1` で PSRAM から取る。**遅い（未測定）ので速度の測定には使わない。**
⚠️ **S3 以外で `SAAN_ENABLE_PIE=1` を渡すと CMake で止める**（黙ってスカラに落ちない）。
⚠️ K トラック（辞書 13.7 MB のパーティション mmap）は PSRAM 有効な板で動かない可能性がある（§0 の報告）。**未対応。**

## 7. 未検証・限界

- **実機の内訳は無い。** §3.3 は QEMU（命令数に比例）とホスト。flash のストールと FPU の遅延は入っていない
- **1.55× RT 自体を私は再現していない**（ボードが無い）
- QEMU の CCOUNT の絶対値（`-icount shift=0` で 1 step = 811,001）は**サイクルでも命令数でもない**。
  **割合だけ**を使うこと
- プロファイラの区間の出入り自体にコストがある（`hout` の出力チャネル 1,539 本で WCOPY / MAC は
  1 step に 4,724 回入る）。**細かい区間ほど過大**。速度の報告は `SAAN_PROFILE=0` で
- 「flash 上限 40 MB/s で 56 ms」はデータシートの理論上限からの算術で、**実測ではない**
