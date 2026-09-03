# sanoTTS-jp

***日本語** · [English](README.en.md)*

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)

**559 K パラメータの日本語 TTS を、$3 のマイコン（ESP32-S3）で動かす試み。**

[arXiv:2608.21378](https://arxiv.org/abs/2608.21378) "sanoTTS" の蒸留レシピを日本語に適用し、
[piper-plus](https://github.com/ayutaz/piper-plus)（MB-iSTFT-VITS2）を教師として、
Duration / Acoustic / iSTFT Decoder の 3 つの小さな生徒に蒸留する。

✅ **2026-09-03、M5Stack CoreS3（スタックチャン）で漢字かな交じり文をそのまま喋らせた**（M-90）。
辞書 13.7 MB を載せた 1 本のファームで、シリアルに **`今日は良い天気ですね。` と打つだけ**
（`!` の前置は要らない。経路は端末が自分で決める）。**満チャンク 1 pull の xRT は 0.446 で、
要件 RTF ≤ 0.5 を満たす**。⚠️ **音は誰も聴いていない** — 残っているのはそれだけ。

**速度は作り直して要件に届いた。** 第三者の実機報告（**どちらも S1 前の値**）は
AtomS3 **1.718** / CoreS3 **1.558** でリアルタイム未達だった
（[AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results) /
[CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) / [動画](https://x.com/nnn112358/status/2095071771355725970)）。
1 step の内訳を取ると積和ではなく量子化・GELU・テンソル検索・重みのコピーが支配的で（M-80）、
それを削る **S1〜S5a**（M-82: 0.926）→ **T1〜T5 と 64 B キャッシュ行**（M-88: 0.497、アンダーラン 0）→
**S5b**（M-90: 0.446）と積み上げた。**波形は M-82 以来 1 bit も変わっていない**（checksum が同一）。
⚠️ **発話全体で見ると 0.54〜0.71 でまだ 0.5 を超える**（warmup 38 フレームが初回 pull に乗るため。
要件の分母がどちらかは決まっていない）。

## 日本語でやると何が難しいか

英語版をそのまま移すと壊れる。この 3 つが日本語固有の壁だった。

| 壁 | どう解いたか |
|---|---|
| **G2P が載らない** — 漢字を読むには辞書が要る。NAIST-JDIC は実測 **102 MB** | **入力仕様を変えた。** 端末は「ひらがな + アクセント記号」だけを受け取り、**877 B のテーブル**で音素に変換する。漢字→かなはホスト側でオフラインに行う。⚠️ **後にこの前提を測り直したら崩れ、実装まで通った** — 辞書を TTS 専用の形式にすると 1 エントリ 130 B → **28 B** になり、16 MB ボードに **438,750 entries** が載る（D-044）。**QEMU では漢字文から合成まで完走した**（K-7 / M-76）。✅ **実機でも動いた** — CoreS3 で checksum が QEMU と一致し（M-83）、**M5 のスピーカーで喋った**（M-90） |
| **ピッチアクセント** — 「箸／橋／端」は音素列が同じで高低だけが違う。集約スコアでは検出できない | ミニマルペア 15 群を評価セットに入れた。教師との**符号一致 37/37** |
| **無声化母音** — 「です」「した」の `i` `u` が音響的にほぼ摩擦雑音になる | 音素クラス別スペクトル平坦度で分離を確認（AUC 0.847）してから、ノイズ注入の対象集合に入れた |

**一番効いたのは 1 つ目**で、これは辞書を小さくしたのではなく**問題の切り分け方を変えた**もの。
論文にも公式実装にも対応物が無い。

## 現在地

| 軸 | 状態 |
|---|---|
| **品質** | 教師の **64%**（SCOREQ 比 0.644）。論文の英語版が報告する比 0.5427 を上回る |
| **アクセント** | ミニマルペア 37 ペアで教師との符号一致 **37/37** |
| **メモリ** | **157 KB**（実機の arena used。静的確保は 176 KB = 180,224 B）— ESP32-S3 の SRAM 512 KB の 34%。実機の内部 DRAM の空きは **136,407 B**（かな構成 / M-89）・**132,039 B**（辞書込みの漢字構成 / M-90）。重みは int8 で 654,032 B（flash。blob v2 = 事前整列で +10,096 B。v1 は 643,936 B） |
| **速度** | ✅ **要件を満たした。** 手元の CoreS3（W8A8 + PIE。**S3 では既定** = D-048）で**満チャンク 1 pull の xRT 0.446**（漢字構成 4 文とも。M-90）。かな構成は 0.494（M-89）/ 0.497（M-88）。アンダーラン **0**、鳴らし始めまで **384 ms**（M-90 の出荷構成。かな構成は 432〜434 ms = M-88 / M-89）、1 step は 18.38 M → **11.66 M cyc**。⚠️ **発話全体で見ると 0.54〜0.71** でまだ 0.5 超（warmup 38 フレーム分。要件の分母は未定）。第三者の報告値は **S1 前**のもの（AtomS3 **1.718** / CoreS3 **1.558**。[AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results/blob/main/results/atom_s3_2026-09-01.md) / [CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3/blob/main/docs/measurements.md)） |
| **実機の音声出力** | ✅ **[`esp32/boards/m5unified/`](esp32/boards/m5unified/README.md) が CoreS3 のスピーカーで喋る**（M-90。画面つき）。先に第三者が M5Unified 経路で成功しており、60% を先読みして発話開始まで **1,781 ms** / 追い越し **0 回**だった（[実装](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) / [動画](https://x.com/nnn112358/status/2095071771355725970)）。⚠️ **音は誰も聴いていない** |
| **漢字を端末で読む** | ✅ **CoreS3 で実機確認**（M-83: checksum が QEMU と一致 / 漢字 G2P 27.85〜66.30 ms）。✅ **M5 のスピーカー構成にも載った**（M-90。辞書 13.7 MB を `esp_mmu_map`、W8A8+PIE で xRT 0.446）。**`!` は要らない** — 端末が経路を 3 値で判定する。⚠️ 音は未聴取 |

⚠️ **すべて n=24〜200 の予測器スコアで、人が聴いた評価は 1 名 1 回しかない**
（β の決定。M-60 / D-038）。「教師比 0.644」は「教師の音を 100 としたとき 64」という
意味ではなく、較正されていない予測器のスコアの比。日本語では**実人間の音声ですら
SCOREQ 2.50 / UTMOS 2.30** しか出ないので、**絶対値を英語の論文と比べてはいけない**。

**なぜ遅かったかは測った。** 1 step の内訳を取ると **MAC は 3 割で、活性化の量子化（ソフト除算）・
GELU の `erff`・毎 step 102 回のテンソル検索・重みのコピー 489 KB/step が残りを占めていた**（M-80）。
以降はすべて**手元の CoreS3 の実測**（D-047。W8A8+PIE、`esp32/boards/m5unified/`）:

| いつ | 入れたもの | 満チャンク xRT | 1 step |
|---|---|---:|---:|
| 2026-09-02 | **S1〜S5a**（量子化・GELU・テンソル検索・重みコピーを削る。M-81 / D-046） | 0.926 | 18,378,513 cyc |
| 2026-09-03 | **64 B キャッシュ行**（M-84） | 0.861 | 17,125,414 |
| 2026-09-03 | **T1**（末尾 pull の早期終了）+ **T5 修正**（GELU のコード生成。M-87 / C-055） | — | 16,638,110 |
| 2026-09-03 | **T2**（捨てる出力を計算しない）+ **T3**（token のパイプ化）→ ✅ **要件達成**（M-88） | **0.497** | 11,724,417 |
| 2026-09-03 | **T4**（arena の詰め。M-89） | 0.494 | 11,659,500 |
| 2026-09-03 | **S5b**（weight-stationary の PIE）+ 辞書 + 漢字経路（M-90） | **0.446** | — |

**checksum は M-82 以来 3 文とも同一**（W8A8+PIE `0xa69a7ebbb5ccb05f` / W8A32 `0xe4b645c30835d42d`）
= **T1〜T5 と S5b は波形を 1 bit も変えていない**。
⚠️ **QEMU の命令数も割合も実機の速度を予測しない** — 命令数が減ったのに GELU が 2 倍遅くなった実例がある（C-055）。
経緯は [`docs/research/s1-m5-cores3-speed.md`](docs/research/s1-m5-cores3-speed.md) と
[`docs/plan/s2-fast-kanji-m5-plan.md`](docs/plan/s2-fast-kanji-m5-plan.md)。

**実機で通してあること**（すべて CoreS3。D-047）:

| いつ | 何 |
|---|---|
| 2026-09-02 | **漢字経路が動いた**（M-83。K-8 の G28〜G31）。`!今日は良い天気ですね。` → 53 ids → checksum が QEMU と一致。漢字 G2P **27.85〜66.30 ms** |
| 2026-09-03 | **要件 RTF ≤ 0.5 を満たした**（M-88 / M-89。かな構成）。アンダーラン **0**、鳴らし始めまで 434 → 432 ms、内部 DRAM の空き **136,407 B** |
| 2026-09-03 | **スタックチャンが漢字・カタカナ・ひらがなを喋った**（M-90）。辞書 13.7 MB を `esp_mmu_map` で貼り、xRT **0.446**、鳴らし始めまで **384 ms**、内部 DRAM の空き **132,039 B**。`今日は良い天気ですね。` と `きょ][おわよ][いて][んきです°ね` が**同じ PCM** になる |

**QEMU で先に通してあること:**

| いつ | 何 |
|---|---|
| 2026-08-30 | 出荷ファームを**起動 → 重み mmap → 端末側 G2P → 合成 → int16 まで完走**。**PIE はスカラ実装と 27,136 sample すべて bit 一致**（陰性対照つき。M-62） |
| 2026-08-31 | **端末が漢字かな交じり文をそのまま読む経路**も完走（K-7 / M-76）。**v0.2.0 で焼くだけの 16 MB イメージを配布している** |

**第三者の独立した実機報告が 2 件ある**（⚠️ **どちらも S1 前 = 2026-09-01〜02 の値**で、
上の作り直しは入っていない。**未再現**）。ESP32-S3 の PIE（SIMD）を使った int8 カーネルは
`ee.vmulas.s8.accx`（16 レーンの int8 積和）で内積を置き換え、MAC の **99.4%** を覆う。

- **AtomS3:** W8A8 + PIE を 2 回測定して定常 xRT **1.718**。I2S は無効だが、
  27,136 sample の PCM checksum と振幅統計が QEMU 基準に完全一致した
  （[測定記録](https://github.com/magatsux2019/sanotts-atoms3-results/blob/main/results/atom_s3_2026-09-01.md)）
- **CoreS3:** M5Unified の内蔵スピーカー、m5stack-avatar、リップシンクを含む構成で
  定常 xRT **1.558**。60% を先読みして発話開始まで **1,781 ms**、途切れ **0 回**を確認した
  （[実装と測定](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) /
  [動画](https://x.com/nnn112358/status/2095071771355725970)）

⚠️ **本リポジトリの `saan_i2s`（DevKit の I2S 直叩き）は引き続き実機未検証。**
実機で音を出しているのは M5Unified 経路（`esp32/boards/m5unified/`）だけ。
⚠️ 公式実装が同じ ESP32-S3 で申告する **0.22× 実時間**にはまだ届いていない。

> 🙏 **追加の ESP32-S3 実機測定と、何より「聴いた感想」を歓迎します。**
> 手順は [`esp32/TESTING.md`](esp32/TESTING.md)。**DAC が無くても測れます。**

## はじめかた

**入口は 4 つ。A / B / D は piper-plus も教師モデルも要らない**（新規 clone で実測）。

| | やりたいこと | 要るもの | 所要 |
|---|---|---|---|
| **A** | **音を聴く** | [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` だけ | 1 分 |
| **B** | **好きな文を合成する** | + 最小セットアップ + `saanotts-jp-v3-stage4.pt` | 10 分 |
| **C** | **ESP32-S3 で喋らせる** | ボード（DAC は任意）。**焼くだけなら ESP-IDF は不要** | 15〜30 分 |
| **D** | **コードのゲートを回す** | 最小セットアップだけ | 5 分 |

### どのリリースに何が入っているか

**最新の [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) に全部入っている**（14 本）。
`releases/latest` で足りる。

| 資産 | どこ | 中身 |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 合成音の WAV |
| `saanotts-jp-v3-stage4.pt` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | PyTorch の重み（2,744,874 B） |
| `saanotts-jp-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | C99 コア用の int8 blob（**654,032 B / 形式 v2**）。⚠️ **v0.2.0 の v1（643,936 B）は現行コアが `SAAN_ERR_VERSION` で拒む** |
| `saanotts-jp-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 参照・デバッグ用の fp32 blob |
| `golden-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | `make -C csrc int8-golden` 用の参照出力（`csrc/golden_i8.bin` に置く） |
| `golden-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | `make -C csrc golden`（= `test`）用（`csrc/golden.bin` に置く） |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **かな入力**のファーム（8 MB 以上・焼くだけ） |
| `esp32s3-firmware-w8a32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 同・最適化なし（**PIE の比較対照**） |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **漢字入力**のイメージ（**16 MB 必須**・焼くだけ） |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 同・**コンソールが USB Serial/JTAG**（native USB だけの板はこちら） |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | かな入力・**USB Serial/JTAG**（8 MB 以上） |
| `m5-cores3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **M5Stack CoreS3 / スタックチャン**。漢字 + 内蔵スピーカー + 画面（**16 MB 必須**） |
| `k1-dict-438750.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | 辞書 blob 単体（13,702,320 B） |

⚠️ **モデルの重みは v0.1.0 / v0.1.1 / v0.2.0 で bit 同一**（再学習していない）。
どのタグから落としても同じ。

> **かつて v0.2.0 は重みを載せていなかった。** その結果 `releases/latest` が
> v0.2.0 になった瞬間に**この表のリンク 5 本が全部壊れた**（C-052）。
> いまは `scripts/check_release_assets.py` が**この表を読んで**、
> 名前が挙がっている資産が実際にそのタグに在るかを CI で検査している。

### 最小セットアップ（B / D）— piper-plus も教師も要らない

⚠️ **`uv sync` は使わない。** `pyproject.toml` の `[tool.uv.sources]` が
piper-plus への**絶対パス**を指しているので、持っていない人は
`error: Distribution not found at: file://...` で止まる（実測）。
生徒の推論に要るのは **torch / numpy / soundfile の 3 つだけ**なので、
プロジェクトを経由しない venv を作る:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

### B. 好きな文を合成する

[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) から
`saanotts-jp-v3-stage4.pt`（2.7 MB）を落として、**かな中間表現**を渡す。

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v3-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav（22.05 kHz / 1.2 秒）「今日は良い天気ですね。」
```

```
[ アクセント上昇 / ] 下降核 / # 句境界 / ° 無声化
```

⚠️ **漢字から中間表現を作るにはフルセットアップが要る**（OpenJTalk）。
かなを直接書けば不要:

```bash
uv run python scripts/to_intermediate.py "電源を入れてください。"   # ← フルセットアップ側
#   → で[んげんおい[れてくださ]い
```

⚠️ `--text "漢字混じり文"` でも合成できるが、**そちらは教師 ckpt（private）が要る**
（音素 ID を教師の `phoneme_id_map` 経由で組むため）。`--intermediate` は教師も
OpenJTalk も呼ばず、**同じ入力に対して WAV がバイト単位で一致する**（M-64 / M-65）。

⚠️ **モデルの重みは MIT ではない。** 使う前に [`LICENSE-MODEL.md`](LICENSE-MODEL.md) を読むこと。

### C. ESP32-S3 で喋らせる

手順は [`esp32/TESTING.md`](esp32/TESTING.md)。焼いたあとシリアルで:

```
かな> きょ][おわよ][いて][んきです°ね        ← かな中間表現
かな> 今日は良い天気ですね。                  ← 漢字版のビルドなら、そのまま打つ
```

**入力に印を付ける必要はない**（現行のソースからのビルド）。端末が行を見て
**かな / 辞書 / 拒否の 3 値**を決める（`saan_g2p_classify()`）:
凍結テーブルのトークナイザが行末まで通れば**かな経路**、通らず中間表現の記号
（`[ ] # ° _ ^ $`）が無ければ**辞書経路**、通らないのに記号が混じっていれば
**拒否して喋らない**（「中間表現 + `。`」がそれらしい音で通ってしまうのを防ぐため）。
⚠️ **半角 `?` は記号の判定から外してある** — 外すまでは `本当なんでしょうか?` のような
疑問文が拒否されていた（held-out 2,333 行のうち 45 行 = 1.93%。M-90）。
判定はホスト側 `scripts/kana_g2p.py` の `classify_route()` と同じ規則で、
一致は `make -C csrc kb-parity`（held-out 298 文 + その中間表現 298 行 = **596/596**。M-90）が検査する。
⚠️ **`!` の前置は残っているが、辞書経路を強制する試験用**で、普段は要らない。

**ファームは 2 種類ある。**

| | 焼くもの | 受け付ける入力 | flash |
|---|---|---|---|
| かな | `esp32s3-firmware-w8a8-pie.bin` | かな中間表現のみ | 8 MB 以上 |
| **漢字** | `esp32s3-firmware-kanji-16mb.bin` | **漢字かな交じり文**も（⚠️ v0.2.0 のイメージは `!` が要る） | **16 MB 必須** |

✅ **かな経路も漢字経路も実機で動いている**（すべて CoreS3。D-047）。かな構成は
**満チャンク xRT 0.494**、アンダーラン 0（M-89）。漢字を載せた M5 構成は **0.446**（M-90）で、
`今日は良い天気ですね。` と `きょ][おわよ][いて][んきです°ね` が**同じ PCM**（`0xa69a7ebbb5ccb05f`）になる。
第三者の報告（**S1 前**）は AtomS3 **1.718**（n=2、I2S 無効）/ CoreS3 **1.558**（60% の先読みで途切れ 0 回。
[実装](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)）。
⚠️ **配布イメージ（v0.1.1 / v0.2.0）は S1 前のコードで、コンソール入力が UART0**。
CoreS3 / AtomS3 のような native USB だけの板では起動はするが**入力が届かない**（M-83）。
**要件を満たす速度と `!` 不要の判定はソースからビルドしたときだけ**（`esp32/TESTING.md`）。
⚠️ ESP32-S3 では **W8A8 + PIE が既定**になった（D-048）。W8A32 で測るなら `-DSAAN_ENABLE_PIE=0` を明示する。

**板は 2 通り。**

| 板 | どう焼くか | 音の出口 |
|---|---|---|
| ESP32-S3 DevKit / AtomS3 + I2S DAC | 上のイメージを焼く（`esp32/TESTING.md` A） | 外付け DAC（配線が要る。⚠️ `saan_i2s` は**実機未検証**。AtomS3 の報告は I2S 無効で速度だけ） |
| **M5Stack CoreS3 / Core2 / Basic**（スタックチャンの中身） | **ソースからビルド**（`esp32/TESTING.md` の「M. M5Stack で試す」/ [`esp32/boards/m5unified/`](esp32/boards/m5unified/README.md)） | 内蔵スピーカー。画面に文が出て、タッチで再生。**漢字もこの構成で喋る**（M-90） |

> 漢字を受け付けない理由をかつて「辞書が載らないため」と書いていたが、**測り直したら崩れ、
> 実装まで通った**。辞書を TTS 専用のバイナリにすると 16 MB ボードに **438,750 entries** が載り、
> **QEMU の UART に `!今日は良い天気ですね。` と打ち込むと端末が自分で形態素解析して
> 合成まで完走する**（[M-76](docs/measurements.md)）。しかも出た音は
> **凍結してあるかな中間表現と bit 一致**した（`0x78c209af06affc01`）。
>
> - MeCab と **1,977/1,977 文で一致**（未知語込み）
> - アクセント規則 126 行の C 移植が Python 版と **2,333/2,333 一致**
> - NJD チェーンがホストと **635/635**、ラベル → 音素ID が **298/298**
> - 1 文あたりのピーク RAM **104,589 B**、辞書 13,702,320 B（438,750 entries）
>
> ⚠️ **ホストと違う音素が 0.32% ある**（n=298。辞書を枝刈りしているため。M-77）。
> ✅ **2026-09-02 に CoreS3 で実機確認**（M-83。checksum は QEMU と一致、漢字 G2P 27.85〜66.30 ms）。
> ✅ **2026-09-03、M5 のスピーカー構成にも載って喋った**（M-90。W8A8+PIE で xRT 0.446、`!` 不要）。
> ✅ **音も聴いた** — ユーザーが「音は正しかった」と報告（M-91）。⚠️ **1 名・対照なし・盲検なし**なので「破綻していない」以上は言えない。
> ⚠️ **v0.2.0 の配布イメージは UART0 入力**なので native USB だけの板では操作できない（ソースからなら動く）。
> （[K-1 調査](docs/research/k1-kanji-katakana-ondevice.md) / [実装計画](docs/plan/k1-kanji-implementation-plan.md)
> ／ ソースから作るなら [`esp32/README.md`](esp32/README.md) の「漢字対応ビルド」）

### D. コードのゲートを回す

```bash
make -C csrc line                                       # 端末の行編集（**陽性対照つき**）
make -C csrc fft                                        # 逆 FFT（naive DFT の 1,435 倍）
make -C csrc g2p PYTHON="uv run --no-project python"    # オンデバイス G2P（2,819 ベクタ）
make -C csrc erf                                        # GELU の erf 近似 vs libm（陽性対照つき）
make -C csrc range                                      # 出力範囲つきカーネル（S9）が全域版と bit 一致（陽性対照つき）
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata ヘッダ（fp32 拒否の陽性対照）
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **`PYTHON=...` を省くと `uv run python` になり、piper-plus を要求する。**
（この区別を付けるまで「piper-plus 無しで通った」と誤って観測した。C-041）

⚠️ **`make -C csrc all-test` は通らない** — golden との突き合わせに `csrc/*.bin`
（重みの書き出し）が要る。落とした `.pt` から
`scripts/export_c_weights.py` で書き出せば通る。

### フルセットアップ（漢字→かな変換 / 学習 / ラベル生成）

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ uv sync の前
uv sync
```

⚠️ **教師 checkpoint（private）はこれでも入らない。** 要るのは
**ラベル生成と学習をやり直すときだけ**で、漢字→かな変換は piper-plus のソースだけで動く。

### まだできないこと

| | 理由 |
|---|---|
| **ラベル生成・学習をやり直す** | 教師 checkpoint が private リポジトリにある |
| **音の良し悪しを人の耳で言う** | ⚠️ **聴取はどれも 1 名・対照なし**（β の決定 = M-60 / 実機の漢字経路 = M-91）。**「破綻していない」までは確かめた**が、`reports/k8_listen/` の 12 組（枝刈りで読みが変わるペア）と `reports/d4_accent/` のミニマルペアは**まだ聴かれていない**。**これが今いちばん足りないもの** |
| **発話全体を実時間に収める** | ⚠️ 満チャンク 1 pull は **0.446** で要件を満たすが、warmup 38 フレームが初回 pull に乗るので**発話全体では 0.54〜0.71**（M-88〜M-90）。要件の分母がどちらかは決まっていない |
| **配布イメージだけで最新の速度を出す** | ⚠️ v0.2.0 のファームは **S1 前のコード**で、コンソール入力も UART0。要件を満たす速度と `!` 不要の判定は**ソースからビルドしたときだけ** |
| **リリースの int8 blob を最新のコアに渡す** | ⚠️ `saanotts-jp-v3-int8.bin` は形式 **v1** のままで、S4 以降のコアは `SAAN_ERR_VERSION` で拒む。`scripts/export_c_weights.py --int8` で v2 を作る |

## アーキテクチャ

```
漢字かな交じり文
   │  ホスト側・オフライン（OpenJTalk）  ┊  端末側・辞書 13.7 MB（-DSAAN_KANJI=1）
   ▼                                     ┊  438,750 entries を flash に mmap
かな中間表現   きょ][おわよ][いて][んきです°ね     [ 上昇 / ] 下降核 / # 句境界 / ° 無声化
   │  端末側・877 B のテーブルのみ        ┊  どちらの経路かは saan_g2p_classify() が決める
   ▼                                     ┊  （かな / 辞書 / 拒否。両経路とも同じ音素IDに合流）
音素ID ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
            33 K params      195 K            331 K
                        └─ 40 次元の潜在インターフェース（c-line）で接続
```

**3 つの生徒を明示的な潜在インターフェースで繋ぐのが要点。** これを省いて
テキスト→波形を 1 本のネットで学ぶと、論文の対照実験では訓練文を丸暗記して
未知の文が読めなくなる。

- **C99 推論コア**（`csrc/`）— 依存は libm のみ。`malloc` を呼ばず arena を使う。
  ストリーミング版は一括版と **bit 完全一致**（27,136 sample）
- **オンデバイス G2P**（`csrc/g2p.c`）— テーブル **877 B** / コード 1,549 B（ESP32-S3 実測）/ **作業メモリ 0 B**
- **端末での自由入力** — シリアルに 1 行打つと喋る（`csrc/line.c` 369 B）。
  かな中間表現でも漢字かな交じり文でもよく、`saan_g2p_classify()` が経路を決める
  （辞書を持たないビルドは漢字を**喋らずに理由を出す**）。
  ホストで先に変換するなら `uv run python scripts/to_intermediate.py "文"`
- **蒸留の全経路** — 教師ラベル生成 → 4 段の学習 → 評価（SCOREQ / UTMOS / DNSMOS /
  かな CER / 音素クラス別スペクトル平坦度）

## 他のプロジェクトで使えそうなもの

- **`csrc/`** — 依存なしの C99 推論コア。MCU に載る TTS のデコーダとして単体で読める
- **`csrc/g2p.c` + `scripts/kana_g2p.py`** — かな中間表現の設計と C 実装。
  **日本語 TTS を組み込みに載せるときの G2P 問題への 1 つの答え**
- **`csrc/line.c`** — 369 B の UTF-8 対応行編集。マイコンのシリアルで日本語を打たせるなら、
  **矢印キーが記号を挿入する / BS が UTF-8 を割る**の 2 つは必ず踏む
- **`esp32/components/saanotts_core/saan_dict.c`** — **8 MB を超える flash パーティションの mmap**。
  `CONFIG_SPI_FLASH_ROM_IMPL=y` の板では `esp_partition_mmap` が ROM 実装に落ちて
  **128 ページ = 8 MB しか貼れず** `ESP_ERR_NO_MEM` になる。`esp_mmu_map` なら貼れる（M-90）
- **`docs/measurements.md`** — 全項目に再現コマンドを付けた実測記録。
  ESP32 のメモリ収支、esp-dsp のサイクル数からの外挿、日本語での MOS 予測器の較正など

## 公式実装との関係

公式実装 [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS) は存在する（**GPL-3.0**）。
本リポジトリは MIT で、**そのソースコードを参照せずに**論文本文の数値と piper-plus の実装から
独立に書いた。公式実装は英語・ネパール語・ヒンディー語・ベトナム語・インドネシア語・中国語に
対応しており、**日本語は含まれていない**。

公式リポジトリの**公開ドキュメントに記載された実測値**（ESP32-S3 で 0.22× 実時間など）は、
本リポジトリの外挿値との突き合わせに使っている。**コードは参照していない**
（この線引きは `docs/decisions.md` の D-032 として凍結し、hook で機械的に強制している）。

## ドキュメント

**数値が食い違ったら [`docs/measurements.md`](docs/measurements.md) が正。**

| | |
|---|---|
| [`docs/README.md`](docs/README.md) | 索引と現在地 |
| [`docs/measurements.md`](docs/measurements.md) | **実測値の一次ソース** M-1〜M-91。全項目に再現コマンド付き |
| [`docs/decisions.md`](docs/decisions.md) | 決定 D-001〜D-048 と**訂正履歴 C-001〜C-056** |
| [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) | 公式実装から得た事実（⚠️ すべて上流の申告値・未再現） |
| [`docs/plan/`](docs/plan/) | 作業計画と残りのタスク |
| [`docs/release-notes/`](docs/release-notes/) | 各リリースで何が変わったか（**訂正も残してある**） |
| [`esp32/README.md`](esp32/README.md) | ESP32-S3 のビルドと設計判断 |
| [`esp32/TESTING.md`](esp32/TESTING.md) | **実機で動かす手順**（焼き方・喋らせ方・報告してほしい 4 行） |
| [`MODEL_CARD.md`](MODEL_CARD.md) | モデルの中身・評価・既知の制約 |
| [`CLAUDE.md`](CLAUDE.md) | 実装時の要点。AI エージェント向けの運用ルールでもある |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **貢献のしかた**。一番ありがたいのは実機の速度と**聴いた感想** |
| [`.github/workflows/README.md`](.github/workflows/README.md) | **CI に入れているもの/入れていないもの**（⚠️ 品質・速度・音は 1 つも見ていない） |

## このリポジトリの進め方

**このプロジェクトは AI エージェント（Claude Code）が大半を書いている。**
そのための規律をリポジトリ内に明文化してある。

- **推測を数値として書かない。** 測っていないことは「未測定」と書く
- **訂正履歴を消さない。** 「1 コマンド打てば分かることを、打たずに推論した」種類の誤りが
  **56 件**記録してある。同じ間違いを繰り返さないための資料
- **決着したリスクも消さない。** 消すと同じ疑問が再燃する
- **n が小さいときは n と CI を必ず併記する**（n=3 の差を結論にして反証されたことがある）
- **ゲートは「落ちる壊し方」を言えないと書かない。** テストが緑のまま欠陥が潜んでいた例が
  12 件あり、`.claude/skills/writing-gates/` にまとめてある

`.claude/hooks/guard_bash.py` が、教師リポジトリへの書き込み・`pip install`・
本番ラベルパックの破棄・GPL ソースの取得・コーパス本文を含むコミットを機械的に止める
（回帰 94 ケース）。

## ライセンス

⚠️ **コードとモデルでライセンスが違う。**

| 対象 | ライセンス |
|---|---|
| このリポジトリの**コードとドキュメント** | [MIT](LICENSE) |
| **配布されるモデルの重み**（[Releases](https://github.com/ayutaz/sanoTTS-jp/releases)） | **[`LICENSE-MODEL.md`](LICENSE-MODEL.md)** — MIT **ではない** |

重みは つくよみちゃんコーパスを素材に含む教師からの蒸留物で、そのコーパスの条件が

- **帰属表示を必須**とし、**出力の用途に禁止事項**を課し、**義務が下流に伝播する**

ため、MIT を名乗ることができない。使う前に
[`LICENSE-MODEL.md`](LICENSE-MODEL.md) と [`MODEL_CARD.md`](MODEL_CARD.md) を読むこと。

**コーパス本文は配布しない。** 素材ごとの一次ソースは [`NOTICE.md`](NOTICE.md)。
