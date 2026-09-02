# ESP32-S3 プロジェクト雛形（c'-4）

`csrc/` の C99 推論コアを ESP-IDF アプリとして起動し、ストリーミング API で
合成しながら I2S に流す雛形。

---

## ⚠️ 実機は第三者が動かした（M5Stack CoreS3、2026-09-02）。私は未再現

**これを最初に読むこと。**
実機を持っている方への依頼は [`TESTING.md`](TESTING.md) にまとめてある。

**2026-09-02、第三者がこの雛形を M5Stack CoreS3 に移植して動かし、速度を報告した**
（[`../docs/research/s1-m5-cores3-speed.md`](../docs/research/s1-m5-cores3-speed.md)）:
**W8A8+PIE で定常 1.554× RT / W8A32 で 4.834× RT。実時間に間に合っていない。** checksum は QEMU の記録と一致。
その構成は **[`boards/m5unified/`](boards/m5unified/README.md)** として本リポジトリに取り込んだ
（`main/` と `csrc/` を相対参照。音声出力は `M5.Speaker`、重みは `.rodata`）。
同時に、1 step の内訳で MAC と同等以上だった 4 つ（テンソル検索 / 量子化のソフト除算 /
GELU の `erff` / 重みのコピー）を削る **S1〜S5a** を入れた（M-81。QEMU 命令数比で −49%。**実機は未測定**）。
⚠️ **S3 で基準 checksum が変わった**: W8A8+PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d`
（v0.2.0 までの配布イメージは旧値 `0x04de91103a0e49f9` / `0x78c209af06affc01`）。
⚠️ **blob は v2**（654,032 B）。リリース資産は v1 のままで、このコアは `SAAN_ERR_VERSION` で拒む。

**2026-08-30 に QEMU で出荷ファームを起動から合成完了まで通した**（M-62）。
同日、**シリアルから かなを自由入力できるようにし、QEMU の UART に実際に打ち込んだ**（M-63）。
ESP-IDF v5.5 でビルドが通り、`saanotts_jp.bin` = 284,912 B
（W8A8+PIE 版は 286,272 B / コンソールを native USB にすると 271,872 B。M-66）。
**v0.1.1 からは焼くだけの merge 済みイメージ 2 種も配布している**（各 2.8 MB。M-67）。

**2026-08-31、漢字対応ビルドも QEMU で合成まで完走した**（K-7 / M-76）。
`!今日は良い天気ですね。` を UART に打ち込むと、端末が自分で形態素解析して
ids 53 個を作り、**凍結してあるかな中間表現と同じ PCM**（`0x78c209af06affc01`）が出た。
有効化は `idf.py -DSAAN_KANJI=1`（**既定は無効**。16 MB flash と 12 MB の辞書が要る）。
**v0.2.0 からは焼くだけの 16 MB イメージも配布している**（`esp32s3-firmware-kanji-16mb.bin`）。

⚠️ **しかし実機（ESP32-S3 ボード）が無いので、実機では動かしたことが無い。**
⚠️ **QEMU はサイクル精度ではないので、速度は一切測れていない。**

| # | 未検証なこと | いつ分かるか |
|---|---|---|
| ~~1~~ | ~~`idf.py build` が通るか~~ | ✅ **通った**（M-54 / v5.5） |
| ~~2~~ | ~~起動するか~~ | ✅ **QEMU で起動・合成完了**（M-62） |
| 3 | 実際の SRAM 消費（IDF + FreeRTOS + **I2S DMA 込み**の free heap） | ⚠️ 配布 firmware（W8A8+PIE）の QEMU 起動直後で 68,460 B（M-67）。**I2S DMA を含まない**ので実機で再確認 |
| **4** | **実際の xRT とアンダーラン** — M-43 の 2.47 × RT は外挿 | ⚠️ **第三者報告: W8A8+PIE 1.554 / W8A32 4.834**（CoreS3。途切れ 10/14 チャンク）。**S1〜S5a 後は未測定** |
| 5 | I2S の実サンプルレート誤差（**ESP32-S3 に APLL が無い**） | 実機 + オシロか長時間録音 |
| 6 | flash から mmap した重みが D-cache を thrash しないか | 実機（QEMU にはキャッシュ挙動が無い） |
| ~~7~~ | ~~`sdkconfig.defaults` のオプション名が実在するか~~ | ✅ ビルドが通った（M-54） |
| ~~8~~ | ~~`esp32/main` が呼ぶ IDF API の綴り~~ | ✅ ビルドが通り QEMU で実行された（M-62） |
| **9** | **実機の I2S**（QEMU は DMA を捌かないので通せなかった） | ⚠️ M5.Speaker 経由では鳴った（報告）。**DevKit の `saan_i2s.c`（I2S 直叩き）は未** |

**「たぶん動く」とは書かない。「未検証」と書く。**

### QEMU で取れたもの（M-62）

| 項目 | 値 |
|---|---|
| `model` mmap / 重み | offset `0x00210000` / **183 tensors** / base `0x3c040000`（16 B 境界 OK） |
| 端末側 G2P | 44 B → **53 ids**、`demo_ids.h` の錨と**完全一致** / 0.602 ms |
| 合成 | 106 frames / 27,136 sample / arena used 194,848 B |
| **PIE の bit 一致** | **スカラ実装と 27,136 sample すべて一致**（陰性対照つき） |
| **自由入力**（M-63） | UART0 に打ち込んで合成。基準の 1 行から**起動時に喋らせたときと同じ checksum** |

⚠️ **ホストとターゲットは bit 一致しない。それは正常**（float の丸めが違う）。
`|max|` は一致し `Σx²` の相対差は 1.6e-7 なので丸め差と切り分けられる。
**bit 一致を主張してよいのは「同じターゲット上の 2 構成」を比べたときだけ。**

⚠️ **ビルドを通したときに実際にバグが 1 件出た** — `M_PI` は C99 標準ではないので
newlib の厳密 `-std=c99` で `saanotts.c` / `saanotts_stream.c` が落ちた。
ホストの clang では見えるので**クロスコンパイルするまで一度も検出されなかった**
（`csrc/saanotts_internal.h` で定義して解決。M-54）。

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

✅ **ESP-IDF v5.5 でビルドが通ることを実測した**（2026-08-30）。それ以外の
マイナーバージョンは未検証。

⚠️ `main/CMakeLists.txt` の `REQUIRES` は **`driver` 1 本**にしてある。
**存在しないコンポーネント名を書くと IDF はエラーで止まる**ので、
「両方書いてどちらかに当てる」という逃げは効かない。`driver` は v5.x を通して
残っている傘コンポーネントで、I2S が `esp_driver_i2s` に分割された版でも
そこへ依存する形になっている — ✅ **v5.5 でビルドが通ったので確認済み**
（`components/driver` と `components/esp_driver_i2s` が両方実在する）。

---

## ビルドと書き込み

```bash
# 1. 重み blob と demo_ids.h を用意する（リポジトリのルートで）
uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt \
    --out csrc/student_i8.bin --golden csrc/golden_i8.bin \
    --int8 --golden-from-quantized --report csrc/export_i8.json
uv run python scripts/gen_demo_ids.py                                # esp32/main/demo_ids.h

# 2. 手元のゲートを通す（ESP-IDF 不要）
bash scripts/check_esp32_template.sh

# 3. ビルド（✅ ESP-IDF v5.5 で通ることを実測済み。⚠️ 実機での起動は未検証）
cd esp32
idf.py set-target esp32s3
idf.py build

# 4. パーティション表・アプリ・重み blob をまとめて焼く
idf.py -p /dev/tty.usbmodemXXXX flash monitor
```

`idf.py flash` は `esptool_py_flash_to_partition(flash "model" ...)` によって
重み blob も `model` パーティションへ一緒に焼く（トップの `CMakeLists.txt`）。

---

## 漢字対応ビルド（K-7。**既定は無効**）

端末に辞書を載せて、**漢字かな交じり文をそのまま**受け付ける構成。
QEMU で合成まで完走している（M-76）。⚠️ **実機では未検証。**

```bash
# 1. 辞書 blob を作る（438,750 entries = D-044。13,702,320 B）
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin

# 2. 16 MB 版のパーティション表でビルド
cd esp32
idf.py -B build_kanji -DSDKCONFIG=build_kanji/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 build

# 3. 焼く（app / model / dict がまとめて焼かれる）
idf.py -B build_kanji -p /dev/tty.usbmodemXXXX flash monitor
```

```
かな> !今日は良い天気ですね。
形態素 7 個 / ids 53 個
```

| | サイズ | 枠 |
|---|---:|---:|
| app | 359,584 B | 2,097,152 B（17.1%） |
| model（int8 v2） | 654,032 B | 786,432 B |
| **dict** | **13,702,320 B** | **13,828,096 B**（99.1%） |

⚠️ **16 MB flash が要る**（`partitions_16mb.csv`）。8 MB のボードには載らない。
⚠️ **ホストと違う音素は 0.32%**（n=298。M-77）。差は**辞書の枝刈り**で、
`上毛`（コーゲ）が `上`（ジョー）+ `毛` に切り直されるといった誤読になる。
移植そのものは正確（素性が一致した文でラベル差 0 件）。
⚠️ **PSRAM は使っていない。** N16R8 には 8 MB あるが **QEMU が octal PSRAM を
持っていない**ので、作業領域（130,176 B）は**合成用 arena から切り出している**。
実機で PSRAM を有効にすれば `kj_alloc()` がそちらを優先する。
⚠️ **速度は測れていない。音も聞いていない。**

---

## どちらの blob を焼くか

| blob | サイズ | 状態 |
|---|---:|---|
| **`csrc/student_i8.bin` (int8, **blob v2**)** | **654,032 B** | **既定**。**W8A8 + PIE はこれでないと効かない**。⚠️ リリース v0.2.0 の資産は v1（643,936 B）で、S4 以降のコアは `SAAN_ERR_VERSION` で拒む。`uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt --int8 --out csrc/student_i8.bin --golden csrc/golden_i8.bin --golden-from-quantized --report csrc/export_i8.json` で作る |
| `csrc/student.bin` (fp32) | 2,249,792 B | 参照・デバッグ用。`-DSAAN_MODEL_BLOB=$PWD/../csrc/student.bin` |

⚠️ **blob は git 管理外。** クローンしただけでは存在しない。
[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の
`saanotts-jp-v3-int8.bin` を落とすか、自分で書き出すこと（上記）。

⚠️ **かつて fp32 を既定にしていたが、それは int8 経路が確定していない時期の
保守的な選択だった。** flash と D-cache では int8 のほうが 3.5 倍有利で、
**W8A8 + PIE は fp32 blob では 1 命令も効かない**（`saan_conv1d_w` が `W.f32` で
早期 return する）。**しかも何のエラーも出ない**ので、`main.c` が起動時に
`<name>.scale` の有無で検査して止める（int8 は 52 個 / fp32 は 0 個）。

⚠️ int8 blob は**量子化で波形が変わる**。Python 参照との SNR 26.08 dB は
M-39 の PTQ 実測（≥ 25 dB）と同水準で、**劣化ではなく想定どおり**。

---

## 設計上の判断（踏み抜きやすい順）

### 1. 重みは flash に置き、SRAM にコピーしない

`esp_partition_mmap()` で `model` パーティションを読む。blob は 654,032 B
（int8）〜 2,249,792 B（fp32）で、512 KB の SRAM には入らない。
コアは blob を書き換えないので read-only で足りる。

置き場は 2 つあり、`saan_model.h` の `saan_model_open()` の実装を CMake で切り替える:

| | 既定（`saan_model.c`） | `-DSAAN_MODEL_RODATA=1`（`saan_model_rodata.c`） |
|---|---|---|
| 置き場 | `model` パーティションを `esp_partition_mmap` | `scripts/blob_to_header.py` が `const uint8_t[]`（aligned(16)）にして app の `.rodata` |
| app サイズ（W8A8+PIE / QEMU 構成） | 285,440 B | **928,832 B**（blob ぶん増える） |
| モデルだけの差し替え | できる（`model` だけ焼き直す） | **できない**（app ごと再ビルド） |
| いつ使うか | DevKit | **PSRAM を有効にした板**。CoreS3 では `CONFIG_SPIRAM=y` だと mmap が `ESP_ERR_NO_MEM` で落ちた（第三者の実機報告。未再現） |

どちらも QEMU で同じ checksum を出す（A-2 時点で `0x04de91103a0e49f9`。S3 以降は `0xa69a7ebbb5ccb05f`。起動直後の内部 DRAM free も 72.8 KB で同じ）。
fp32 blob は `blob_to_header.py` が**ビルド時に拒否する**（回帰: `scripts/test_blob_to_header.py`）。

### 2. `EMBED_FILES` を使わない — **アライメントが無保証**

⚠️ **以下は ESP-IDF の公式 cmake を読んで得た事実。** v5.5 は手元にあるので
ソースは確認できるが、**mmap の実挙動は実機でしか確かめられない**。

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

### 4. `saan_stream_init` の欠陥（**修正済み**）に対する二重防御

✅ **この欠陥は `saan_arena` の粘着フラグで修正済み**（`csrc/saanotts.c:82`）。
`make -C csrc arena` は「**init が SAAN_OK を返した後に落ちた サイズ: 0 / 111 点**」で
通る。以下は**なぜその修正が要ったか**の記録として残す。

`saan_alloc` は失敗しても `used` を進めずに NULL を返す。`saan_stream_init` は
確保を約 25 回するのに**各グループの最後の 1 個しか NULL 検査していない**。
そのため「大きい確保（`o1539` 49,248 B など）だけ失敗し、後続の小さい確保は成功」
が起きると、**init が `SAAN_OK` を返したまま壊れた状態**になり、後で
`saan_stream_pull` の中で NULL 書き込みになる。手元では SEGV、ESP32 では
**StoreProhibited パニック = ログも出さずに再起動**。

実測（`make -C csrc arena`）: n_ids=350 / arena 150〜260 KB を 1 KB 刻みで
走らせると、**175〜191 KB の 15 サイズでクラッシュ**する。
180 / 186 / 192 KB はたまたま clean fail するので、**刻みが粗いと見逃す**。

実際に入れた修正（**1 箇所で済んだ**）— `saan_arena` に粘着フラグを足す:

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

`CONFIG_ESP_MAIN_TASK_STACK_SIZE` の既定は **3,584 B**
（✅ `components/esp_system/Kconfig` で確認済み）。
確かなのは「iSTFT 1 回で 4 KB 超を使う」ほうで、これだけで小さいスタックは
足りない。合成は `app_main` ではなく専用タスク（`SAAN_TASK_STACK = 16384`）で回す。

さらに `float chunk[SAAN_CHUNK * SAAN_HOP]`（8,192 B）と int16 変換バッファは
**`static` にしてスタックから外してある**。M-42 が arena だけ見て 200 KB を
切っていたのと同じ間違いを、今度は FreeRTOS のタスクスタックで繰り返さないこと。

⚠️ 16 KB が適切かは**未測定**。実機で `uxTaskGetStackHighWaterMark` を見て詰める
（`main.c` が終了時にログへ出す）。

### 6. 鳴らし始める前にプリロールする

**最初の `saan_stream_pull` だけ定常の約 6 倍かかる**
（ホスト実測 n_ids=350 で 12.21 ms vs 2.04 ms、比 6.0。CoreS3 の報告値も 766 ms vs 144 ms）。
受容野 36 + iSTFT 2 = 38 フレームの warmup で内部の `step_chunk` が複数回走るため。

鳴らし始めた直後から合成を始めると、その 1 回ぶんが確実にアンダーランになる。
`saan_audio_preroll_push()` で 4 チャンク（371 ms・16 KB）先に計算してから
`saan_audio_start()` を呼ぶ。

音声出力は `main/saan_audio.h` の抽象 API で、実装は 2 つ（`saan_i2s.c` = DevKit の
I2S 直叩き / `boards/m5unified/main/saan_audio_m5.cpp` = M5.Speaker）。
float → int16 と checksum は **`saan_pcm.c` が唯一の実装**で、どちらもそれを呼ぶ。

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

### 8. コアの 4 ファイル + `g2p.c` + `line.c` は csrc から**直接参照**する

`components/saanotts_core/CMakeLists.txt` が相対パスで `csrc/` を指す。
**コピーもシンボリックリンクもしない** —「同じものを 2 か所に書かない」。

⚠️ **4 ファイルすべてが要る。** `saanotts_int8.c` に `saan_w` /
`saan_conv1d_w` / `saan_dwconv1d_w` / `saan_act_scratch_needed` があり、
`saanotts.c` と `saanotts_stream.c` の両方が参照する。3 ファイルではリンクできない。

⚠️ `g2p.c` と `line.c` は**コアを 1 行も参照しない独立の翻訳単位**だが、
`main.c` が `saan_g2p()` を、`saan_console.c` が `saan_line_feed()` を呼ぶので
ここに並べる。`csrc/Makefile` の `CORE` には入っていない
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

## 入力経路と、まだやっていないこと

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

⚠️ **既定のビルドでは漢字を端末で扱わない**（D-010 / D-011）。中間表現を作るのは
ホスト側（OpenJTalk）。**任意の文からは `scripts/to_intermediate.py` が作る**
（`gen_demo_ids.py` は錨 1 件専用）。

✅ **漢字対応ビルドもある**（K-7 / M-76。下の「漢字対応ビルド」節）。
`idf.py -DSAAN_KANJI=1` で有効になり、`!` を前置した行を漢字かな交じり文として
端末側で解析する。**既定は無効**（16 MB flash と 12 MB の辞書が要るため）。

### シリアルからの自由入力（M-63 / D-040）

**起動しても勝手には喋らない。** 錨との照合だけして `かな> ` プロンプトを出し、
**かな中間表現を 1 行**受けて合成する（`-DSAAN_BOOT_SPEAK=1` で起動時に 1 回喋る）。

```
かな> こんにちわ
かな> きょ][おわよ][いて][んきです°ね     ← 突き合わせ用の基準の 1 行
```

- 行編集は `csrc/line.c`（369 B）。**UTF-8 対応の BS / CRLF / ESC の吸い込み / 溢れ検出**。
  ⚠️ **矢印キーの ESC [ A の `[` は中間表現では上昇アクセント。** 吸わないと
  カーソルを動かしただけで**エラーも出さずに抑揚が変わる**
- 入出力は `esp32/main/saan_console.c`。**コンソールが UART0 でも
  USB Serial/JTAG でも動く**（`esp32/sdkconfig.usb_serial_jtag` で切り替え）
- 漢字・カタカナ・句読点は**位置と文字を出して拒否**する。端末では黙って落とさない
- 上限は **350 ids**（arena の限界 520 ではなく学習分布の上限。D-040）。
  511 B 超・350 ids 超は**切り詰めず行ごと拒否**

⚠️ **QEMU では通ったが、実機の UART では 1 度も試していない。**
「本当に 1 バイトずつ取れるか」「端末上のエコーの見た目」は QEMU では判定できない。

⚠️ **この経路で 2 回、音では気づけない欠陥を出した**（M-63 の §3）。
どちらも状態機械ではなく**呼び出し側**の読み違いだったので、
「エコーすべきか」を状態機械が返すよう API を変え、`make -C csrc line` の
**G9 / G10** で固定した。

### そのほか

- ~~**PIE (SIMD) カーネル**~~ — ✅ **入った**（M-57 / M-58 / M-62）。
  `idf.py -DSAAN_ENABLE_PIE=1 build` で有効（既定は無効）。
  ⚠️ **速度は未測定**／**出荷構成にするかは未決**
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
| `partitions.csv` | カスタムパーティション表（8 MB。既定では blob が入らない） |
| `partitions_16mb.csv` | **漢字対応版**（16 MB。`dict` 13,828,096 B = D-042 の予算） |
| `sdkconfig.defaults` | ターゲット / 最適化 / スタック / パーティション |
| `sdkconfig.kanji` | 漢字対応ビルドの上書き（16 MB flash + 表の差し替え） |
| `components/saanotts_core/CMakeLists.txt` | `csrc/` の 4 ファイル + `g2p.c` + `line.c` を直接参照。`SAAN_KANJI` で K トラックの 4 ファイル + Open JTalk 34 ファイルが増える |
| `main/main.c` | arena・プリロール・合成ループ・計測ログ・タッチ再生・`SAAN_BUFFERED`・`SAAN_PROFILE` の表 |
| `main/saan_model.h` | `saan_model_open()` の宣言。実装は 2 つ（下） |
| `main/saan_model.c` | 実装 1: flash の `model` パーティションを mmap（16 バイト境界を検査） |
| `main/saan_model_rodata.c` | 実装 2: `.rodata` に埋めた blob（`-DSAAN_MODEL_RODATA=1`。`cmake/saan_model_rodata.cmake` がヘッダを生成） |
| `main/saan_audio.h` | 音声出力の抽象 API（7 関数）。実装は `saan_i2s.c`（DevKit）と `boards/m5unified/main/saan_audio_m5.cpp` |
| `main/saan_i2s.c` | `saan_audio.h` の I2S 直叩き実装 |
| `main/saan_pcm.{h,c}` | float→int16 と FNV-1a / \|max\| / Σx²（**唯一の実装**） |
| `main/saan_ui.h` / `saan_ui_null.c` | 画面の抽象 API と「何もしない」実装（M5 実装は `boards/m5unified/main/saan_ui_m5.cpp`） |
| `main/saan_console.{h,c}` | シリアルからの 1 行入力（UART0 / USB Serial/JTAG）。poll + タッチ |
| `cmake/saan_model_rodata.cmake` | blob → `const uint8_t[]` ヘッダの生成（DevKit と boards で共有） |
| **`boards/m5unified/`** | **M5Stack 向けプロジェクト**（CoreS3 / Core2。`README.md` を読む） |
| `main/saan_dict.{h,c}` | **K-7: `dict` パーティションの mmap**（64 KB 境界を検査） |
| `main/saan_kanji.{h,c}` | **K-7: 漢字文 → 生徒インデックス**（端末の全段） |
| `sdkconfig.usb_serial_jtag` | コンソールを native USB に切り替える差分 |

ビルド時のフラグ:

| フラグ | 既定 | 何が変わるか |
|---|---|---|
| `-DSAAN_ENABLE_PIE=1` | 無効 | W8A8 + PIE（整数 SIMD）。⚠️ int8 blob が要る |
| `-DSAAN_W8A8_NOPIE=1` | 無効 | ⚠️ **陰性対照専用**（W8A8 のままスカラ） |
| `-DSAAN_QEMU=1` | 無効 | I2S への書き込みだけ外す。⚠️ **音は出ない** |
| `-DSAAN_KANJI=1` | 無効 | **端末で漢字を扱う**（K-7）。⚠️ 16 MB flash と 12 MB の辞書が要る |
| `-DSAAN_BOOT_SPEAK=1` | 無効（非対話ビルドでは有効） | 起動時に錨の 1 文を喋る（突き合わせ用） |
| `-DSAAN_MODEL_RODATA=1` | 無効 | 重みを app の `.rodata` に埋める（PSRAM 有効な板。`model` パーティションを焼かない） |
| `-DSAAN_BUFFERED=1` | 無効 | 1 発話ぶんを貯めてから鳴らす（途切れない。待ちは合成時間） |
| `-DSAAN_PROFILE=1` | 無効 | 段別プロファイル（CCOUNT）を発話後に出す。⚠️ **速度の報告には 0 で**（計測にコストがある） |
| `-DSAAN_ARENA_HEAP=1` | 無効 | arena をヒープ（PSRAM 優先）から取る。ESP32（Core2）向け。⚠️ 遅い |
| `main/demo_ids.h` | **自動生成**（`scripts/gen_demo_ids.py`）。中間表現 + 錨 ids |
| `host_stub/` | IDF API の偽ヘッダ + 実装。**デバイスには載らない** |
| [`TESTING.md`](TESTING.md) | **実機を持っている人向けの手順**（配線・焼き方・報告してほしい 4 行） |

検査スクリプト（リポジトリのルートから）:

```bash
bash scripts/check_esp32_template.sh    # 9 ゲート全部
uv run python scripts/check_partitions.py
cmake -P scripts/check_cmake_syntax.cmake
make -C csrc arena                       # arena の実測（✅ 通る）
```
