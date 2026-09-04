# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクトの目的

arXiv:2608.21378 "sanoTTS: The Smallest Real-Time Neural TTS on a General-Purpose Microcontroller"
のレシピを **日本語**に適用し、piper-plus (MB-iSTFT-VITS2) を教師とした
蒸留生徒モデルを作る。

**公式実装 `github.com/Ampixa/sanoTTS` は存在する（GPL-3.0）。**
⚠️ 本リポジトリは MIT なので、**そのソースコードを読んでも書き写してもいけない**
（GPL が伝播して MIT のまま配布できなくなる）。**D-032 として凍結**し、
hook が `gh api .../contents/*.c` / `git clone` / `uv add sanotts` を deny する。
数値・ハイパーパラメータ・アーキテクチャ構成は著作権の対象外なので参照してよく、
**ドキュメントの取得は hook を通る。**

したがって本リポジトリは**論文本文の数値からの clean-room 再実装**であり、
論文に書かれていないハイパーパラメータ（`L_c` の `λ₂, λ_n, λ_Δ, λ_s`）はチューニング対象。

⚠️ **かつて「公式リポジトリは 404」と記録していたが、綴り間違い**（`saanotts` ではなく
`sanoTTS`。sano = ネパール語で「小さい」）**による誤りだった**（C-024）。
公式実装から得た事実は [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) に集約する。

## ドキュメントの読み方

| ファイル | 役割 |
|---|---|
| **このファイル** | 実装時の要点だけ。**コードを書く前に必ず読む** |
| [`docs/measurements.md`](docs/measurements.md) | **数値の一次ソース**。全項目に再現コマンド付き。食い違ったらここが正 |
| [`docs/decisions.md`](docs/decisions.md) | 決定の理由と**訂正履歴**（同じ間違いを繰り返さないため） |
| [`docs/plan/s2-fast-kanji-m5-plan.md`](docs/plan/s2-fast-kanji-m5-plan.md) | 速度 T1〜T5 / 64 B 行 / M5 への漢字搭載。**残りは聴取だけ** |
| [`docs/plan/web-demo-plan.md`](docs/plan/web-demo-plan.md) | **いちばん新しい計画**。W トラック（GitHub Pages のランタイムデモ）。W-0〜W-8 / 受け入れゲート 8 本（G-W1〜G-W7 + G-W2b）。⚠️ **成果物は今も ESP32**（D-050）で Web は入口。実測は M-94 |
| [`docs/plan/phase0-1-implementation-plan.md`](docs/plan/phase0-1-implementation-plan.md) | かなトラックの作業計画。B-0〜B-12 と Phase 0〜D。**ほぼ履歴**（§10 の残りは全部片づいた） |
| [`README.md`](README.md) の「はじめかた」 | **外の人向けの入口**。セットアップ → 合成 → 実機 |
| [`esp32/TESTING.md`](esp32/TESTING.md) | **実機を持っている人への依頼**（焼く・喋らせる・報告） |
| [`docs/requirements.md`](docs/requirements.md) | 要件定義。入力仕様・受け入れ条件 |
| [`docs/research/k1-kanji-katakana-ondevice.md`](docs/research/k1-kanji-katakana-ondevice.md) | **K-1**。端末で漢字を扱えるかの実測。B-0 の否定的結論のうち 4 つが崩れた |
| [`docs/plan/k1-kanji-implementation-plan.md`](docs/plan/k1-kanji-implementation-plan.md) | K トラックの実装計画 K-0〜K-8。**実機まで完走した**（M-83 / M-90）。残りは聴取と、エントリ数・接続行列の判断 |
| [`docs/research/sanotts-jp-feasibility.md`](docs/research/sanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し |
| [`docs/README.md`](docs/README.md) | 索引と現在地 |

**現状（2026-09-03）**: 2 つのトラックが走っている。**どちらも実機で動き、速度の要件も満たした。**
**残っているのは聴取（G32）だけで、それは人を待っている。**

**(1) かな中間表現の生徒モデル（完了）**
Phase 0 / A / B / C / D-1 / D-2 / D-3a-d 完了、
検証タスク **B-0 〜 B-12** と **D-4 / E-1 / E-2 / E-2b** も全部完了。
**端末でかなの自由入力ができ**（M-63 / D-040）、**焼くだけの firmware も配布している**（M-67。漢字版は v0.2.0）。
**W8A8 + PIE は ESP32-S3 で既定になった**（D-048。W8A32 で測るなら `-DSAAN_ENABLE_PIE=0`）。

⚠️ **経緯**: 2026-09-01〜02 に**第三者の実機報告が 2 件**来て、実時間に間に合っていないと分かった
（CoreS3 で W8A8+PIE 1.554× RT / AtomS3 で 1.718。checksum は M-62 と一致。**どちらも未再現・S1 前の値**）。
1 step の内訳を取ると、活性化の量子化 / GELU / テンソル検索 / 重みのコピーが MAC と同等以上だった（M-80）。
**S1〜S5a**（M-81 / D-046）→ **S2 計画の T1〜T5 + 64 B キャッシュ行**（[`docs/plan/s2-fast-kanji-m5-plan.md`](docs/plan/s2-fast-kanji-m5-plan.md)）
→ **S5b** と削り、要件に届いた:

| | M-82（9/2 夜） | **最新（9/3）** |
|---|---:|---:|
| 満チャンク 1 pull の xRT | 0.926 ⚠️ 旧定義 | **0.446**（M-90。要件 RTF ≤ 0.5 達成） |
| 1 step | 18,378,513 cyc | **11,659,500 cyc**（M-89。−36.6%。S5b 前の値） |
| 鳴らし始めまで | 719 ms | **384 ms**（M-90 = 出荷構成。⚠️ かな構成は M-89 で 432 ms / M-88 で 434 ms） |
| アンダーラン | 1/14 | **0**（全文。M-87 以降） |
| arena（静的確保 / 実測 used） | 212,992 / 195,808 B | **180,224 / 157,360 B**（M-89） |
| 内部 DRAM の空き（起動直後） | 99,987 B | **132,039 B**（M-90。辞書 + 漢字込み。最大ブロック 86,016） |

**PCM の checksum は M-82 から 1 bit も変わっていない**（W8A8+PIE `0xa69a7ebbb5ccb05f` /
W8A32 `0xe4b645c30835d42d`）= **T1〜T5 も S5b も 64 B 行も波形を変えていない**。
⚠️ **発話全体で見ると 0.54〜0.71 でまだ 0.5 を超える**（M-88 の 3 文 0.550〜0.693 / M-90 の 4 文 0.541〜0.712。
warmup 38 フレームが初回 pull に乗るため、文が長いほど 0.5 に近づく）。
**要件がどちらの分母かは書かれていなかったので、両方を記録してある**（M-88 §1 / C-054）。
**分母は未決で、D-049 で決める**（[`docs/requirements.md`](docs/requirements.md) §6.2）。
⚠️ **音は聴いていない**（G32）。
⚠️ **S3（GELU の erf 近似）で基準 checksum が変わった**: W8A8+PIE `0x04de91103a0e49f9` → **`0xa69a7ebbb5ccb05f`**
（|max| 9744 → 9627）、W8A32 `0x78c209af06affc01` → **`0xe4b645c30835d42d`**（|max| 9529 同一・Σx² 相対差 8.7e-9）。
W8A8 の |max| が動くのは量子化の境界で 1 値が変わると下流に伝播するためで、品質（fp32 比 SNR 24 文）は不変。
**配布イメージ v0.2.0 までは旧値のまま。**
**S4 で blob は v2 になった**（int8 conv 重みを `[cout][k][align16(cin)]` で 0 埋め。654,032 B）。
✅ **リリース資産 `saanotts-jp-v3-int8.bin`（v0.3.0）も v2**（version フィールド = 2 / SHA-256 が手元の v2 と bit 一致）。
⚠️ **「まだ v1」と書いてあったのは誤りで、C-057 で訂正した。** v0.2.0 以前の資産は今も v1 なので、
古い blob を掴んでいる人には `SAAN_ERR_VERSION` が出る。

**(2) 端末で漢字を扱う（K トラック・完了）**
B-0 / D-009 の「G2P は端末に載らない」を**測り直したら 4 つの根拠が崩れた**（K-1 調査）。
**K-0 〜 K-8 完了。スタックチャン（M5 CoreS3）で漢字・カタカナ・ひらがなを喋った**（M-90）。
**`!` の印は要らない** — 端末が 3 値（かな / 辞書 / 拒否）で経路を決める（`saan_g2p_classify`。
ホストの `to_intermediate.py` と **596/596 一致**）。**同じ文をかなで書いても漢字で書いても PCM が bit 一致する。**
辞書 13.7 MB は **`esp_mmu_map`** で貼る（`esp_partition_mmap` は ROM 実装の 128 ページ = 8 MB 制限に当たる。M-90 §4）。
→ [`docs/research/k1-kanji-katakana-ondevice.md`](docs/research/k1-kanji-katakana-ondevice.md)（結論）
／ [`docs/plan/k1-kanji-implementation-plan.md`](docs/plan/k1-kanji-implementation-plan.md)（計画）

設計値は D-016 〜 D-050 として凍結（⚠️ **D-049 は欠番** = RTF の分母用に予約）。実行はすべて手元の M4 Max（D-027）、実機はユーザーの M5 CoreS3（D-047）。

⚠️ **成果物は `runs/v3/stage4.pt`**（Stage 3 = 80,000 step。D-037）。
`runs/v2` は M-49 など過去の測定の再現用に残してある。**混同しないこと。**

**(1) の残りは深い聴取だけ。** ざっとした聴取は済んだ（M-91。ユーザーが「音は正しかった」。⚠️ 1 名・対照なし）。
残るのは **`reports/k8_listen/` の 12 組**（枝刈りで読みが変わるペア）と
**アクセントの過剰強調** `magnitude_ratio` 1.193（`reports/d4_accent/`）の確認。
**P-1 は M-57 / M-58、P-2（β）は M-60 / D-038、E-1 は M-50 / D-034、E-2 は M-49 / D-033、E-2b は M-52 で決着**。
**E-2c は中止**（結果がどちらでも打つ手が変わらない。D-036）。**S5b と D-048 も入った**（M-90 / D-048）。

**(2) K トラックの現在地**（`docs/plan/k1-kanji-implementation-plan.md`）:

| | 状態 |
|---|---|
| K-0 前提の凍結 | ✅ **D-042**（N16R8 / OTA 無し / 辞書 SHA-256）。⚠️ **エントリ数は D-044 で 438,750 に引き上げ** |
| K-1 ホスト側エンコーダ | ✅ blob **13,702,320 B**（438,750 entries・全部込み。D-044）。G1〜G5 |
| K-2 C リーダ + Viterbi | ✅ **MeCab と 1,918/1,918 文一致**。G6〜G8 |
| K-3 未知語ノード生成 | ✅ **経路なし 0 件**。MeCab と **1,977/1,977 一致** |
| K-4 アクセント規則 126 行の C 移植 | ✅ **Python 版と 2,333/2,333 一致** |
| K-4b NJD チェーン統合 | ✅ ホストと **635/635**（NJD ノード 10,206 件）。M-69 |
| K-5 メモリの詰め | ✅ 1 文ピーク **268,941 → 104,589 B**（−61.1%）。M-71 |
| K-6 ホストとの一致 | ✅ **素性が一致した 244 文でラベル差 0 件**（陰性対照 44 件）。M-74 |
| **K-7 ESP32 / QEMU** | ✅ **漢字文から合成まで完走**。3 経路が bit 一致。M-76 |
| K-8 実機（G28〜G31） | ✅ **CoreS3 で実測**（M-83）: 漢字 G2P 27.85〜66.30 ms（音声長の 1.7〜2.3%。⚠️ 目安 1% 未達）/ checksum が QEMU と一致。**W8A8+PIE 版も bit 一致**（M-86）。⚠️ **G32 聴取だけ未** |
| K-A/K-B M5 への搭載 | ✅ **スタックチャンで喋った**（M-90）。辞書 + 漢字 + W8A8/PIE + T1〜T5 + S5b が 1 本のファームに載る。xRT 0.446 / 内部 DRAM の空き 132 KB。**`!` は要らない** |

**端末で漢字が喋れる**（実機。M-90）。有効化は `idf.py -DSAAN_KANJI=1`（**既定は無効**。16 MB flash と辞書パーティションが要る）。

✅ **K トラックは CoreS3 で実機確認した**（M-83 / M-86 / M-90）。checksum は QEMU と一致
（v0.2.0 コード `0x78c209af06affc01` / 現行 W8A32 `0xe4b645c30835d42d` / 現行 W8A8+PIE `0xa69a7ebbb5ccb05f`）。
漢字 G2P は 5.51〜66.30 ms（入力 15〜84 B。M-90 / M-83）で、**PIE には無関係**（CPU 律速で MAC を含まない。M-86）。
⚠️ QEMU の `xRT 0.661` は**使えない数字**。実機は **W8A32 で 4.28〜4.62**（DIO。M-83）、**W8A8+PIE で 0.446**（M-90）。
⚠️ 一致はすべて「OpenJTalk と同じ出力か」であって正しさではなく、**聴取はゼロ**。

⚠️ **ホストと違う音素は 0.32%**（n=298・438,750 entries。M-77）。
⚠️ **文単位で数えると 14.77% になる**（一致 254/298 = 違うのは 44 文）。
~~15.44%~~ は **C-058 で訂正した**（46/298 に相当し、どの動作点にも無い数だった）。
**1 音素の差も全崩れも同じ 1 件**になるので、判断には**編集距離の方**を使うこと（D-044 はそれで決めた）。
**食い違いは全部が辞書の枝刈り**で、
移植の誤りではない（「素性が同じなのにラベルが違う」は 3 つの動作点すべてで **0 件**）。
⚠️ **計画にあった「0.60% 以下」は取り下げた**（C-050）。あれは同形異音語
（Sudachi）の 14 文から取った数で、**枝刈りの代償ではなかった**。

⚠️ **端末に載らない後処理が 3 段ある**（C-049 / M-70）。ホスト既定は 7 段だが、
`predict_nani_reading`（ONNX）/ `modify_kanji_yomi`（Sudachi）/
`process_odori_features` は載らない。**中間表現で 2.00%** [1.15, 3.45] の文が食い違う。

⚠️ **枝刈りで落ちた語は無音で消えるのではなく、切り直されて誤読される**（C-051）:
`上毛`（コーゲ）→ `上`（ジョー）+ `毛`。未知語になるのは**辞書にも無い語**だけ
（端末 37 / 5,015 token = 0.74%、ホストでも 10 件 = 0.20%）。
未知語のフォールバック `jdict_unk_guess` は入れてあり、**37 件中 13 件**を読める形にした。

✅ **動作点は決めた**（D-044 / M-77）: **438,750 entries / 接続行列は int16 のまま。**

| entries | 行列 | blob | 枠の余り | **音素の誤り** |
|---:|---|---:|---:|---:|
| 370,863（旧 D-042） | int16 | 12,153,280 | 1,674,816 | 0.55% |
| **438,750 ← これ** | **int16** | **13,702,320** | **125,776** | **0.32%** |
| 528,750 | uint8 | 13,781,959 | 46,137 | 0.22% |

**決め手は測り方を変えたこと。** 「文の 17.79% が違う」は
「**音素の 0.55% が違う**」だった。文単位だと 1 音素の差も全崩れも同じ 1 件になる。
⚠️ **接続行列 uint8 は採らない。** 単独では価値が無く（使う先が 528,750 だけ）、
MeCab 一致が 0.18% 落ちる。将来採るなら**行ごとアフィン**（対称は 0.53% 落ちる）。
⚠️ **RAM は増えない**（辞書は mmap / Viterbi の作業領域はエントリ数に依存しない）。

**438,750 は生の行列で入る上限**でもある（C-047）。**枠を使い切ってはいない**（余り 125,776 B）。

⚠️ **P-1 の前提が 2 つとも間違っていた**（2026-08-28）:
1. **「toolchain 待ち」ではなかった**（C-033）。入れていなかっただけで、
   ESP-IDF v5.5 はその日のうちに導入できた。**本物の待ちは実機ボードだけ**
   （⚠️ その板も 2026-09-02 に同定して測り終えた。D-047 / M-82 以降）
2. **GCC は PIE へ自動ベクトル化しない**（M-53 / C-034）。⚠️ 一度
   「W8A8 への書き換えが本体」と書いたが**誤り**で、**W8A8 は既に実装済み**
   （`saan_conv1d_i8a`）。有効なのは実測の方で、**W8A8 の int32 積和ループでも
   PIE 命令は 0 件**。**intrinsic かアセンブリを書くしかないことが確定した**

⚠️ **`Gγ` は容量律速ではない**（M-52 / n=200）。params を 43% 増やしても gap は
**+0.0006** [−0.013, +0.015] しか動かない。効くのは学習量で、Stage 3 の steps を
20k → 40k にすると **−0.1090**（ノイズ床 0.0812 超え）。
**decoder を作り直すのではなく Stage 3 を延ばすこと。40k でもまだ下がる余地がある。**

**品質・速度・メモリはすべて要件に届いた（M-88 / M-89 / M-90）。音も 1 度は聴かれた（M-91）。**
残るのは**対照つきの聴取**（`reports/k8_listen/` 12 組 / `reports/d4_accent/`）だけ。

| 指標 | 生徒 | 教師 | 教師比 | 論文の英語 embedded 比 |
|---|---:|---:|---:|---:|
| SCOREQ synthetic/nr | **1.2716** | 1.9732 | **0.6444** [0.6044, 0.6843] | 0.5427 |
| **DNSMOS OVRL** | **2.1730** | 2.7270 | **0.7969** [0.7756, 0.8173] | — |
| **かな CER** | **0.1671** | 0.1351 | 差 **+0.0320** [−0.031, +0.087] p=0.31 | — |
| アクセント符号一致 | 37/37 = 1.000 | — | — | — |

⚠️ **この 3 件は 2026-08-30 に訂正した**（M-61 / C-038）。旧記録
（SCOREQ 比 0.6613 / DNSMOS 比 0.8385 / CER 差 +0.0034）は**生徒だけ前後 0.3 秒の
パディングが無い**音声を、パディング済みの教師と比べていた。
**「教師側の値が過去の記録と一致した」は、生徒側の前処理が揃っている証拠にならない。**
数値を報告する前に `scripts/release_metrics.py` の G1（前処理の一致）を通すこと。

**2026-08-29 に成果物を v2 → v3 に差し替えた**（M-59 / D-037）。
Stage 3 の step 数を 40k → 80k にしただけで、**SCOREQ +0.0653** [+0.022, +0.108] /
**DNSMOS OVRL +0.0660** [+0.006, +0.124] / アクセント 35/36 → **37/37** /
int8 最小 SNR 23.27 → **25.72 dB** と改善した
（旧 v2: SCOREQ 比 0.6113 / DNSMOS 比 0.7727 / かな CER 0.1776）。
⚠️ **かな CER は改善していない**（−0.0105 [−0.037, +0.018]、検出できない）。
⚠️ **160k は無駄**（最終品質は v3 と有意差なし）。**80k が最適点。**

⚠️ **n=24。** 絶対値は日本語で較正されていないので論文の 2.54 と比べない（D-020）。
**DNSMOS の日本語の天井は実人間 2.7881 / 教師 2.7299（教師/人間 0.979）**（M-50）。
⚠️ **教師の別セット (T_b5) は 2.8634 で実人間を上回る**（比 1.027）。天井は測ったが**低い**。

**ギャップの内訳は測ってある**（M-49 / n=200、鎖分解）:
**decoder 0.395 / acoustic 0.283 / duration 0.052**（和 = 全体 0.729）。
40 次元 c-line 自体のコストは **0.024** しかない。
⚠️ **論文流の置換定義だと acoustic 0.508 > decoder 0.395 で順序が逆**になる。
⚠️ **かな CER の劣化は decoder の段だけで起きる**（+0.039、acoustic/duration は 0）。

**アクセント型も再現できている**（M-44 / D-030 は v2、**v3 は M-59**）:
**v3 は教師ゲート通過 37 ペアすべてで符号一致 = 1.000** [1.000, 1.000]、
30 群すべての同定に成功（2 メンバー 26/26 / 3 メンバー 4/4）。
⚠️ **`magnitude_ratio` 1.193** — 生徒の起伏が教師より **19% 大きい**。
過剰強調の可能性があり**聴取でしか判断できない**。
（旧 v2: 符号一致 35/36 = 0.972 [0.897, 1.000]、3 メンバー群の同定 4/4）。
⚠️ **chance は 0.5 ではない**（経験的ヌル 0.614）。**聴取は未実施。**

**C99 コアは ESP32-S3 の SRAM に載った**: ストリーミング化で
**1,258 KB → 197 KB**、一括版と **bit 完全一致**（M-42）。FFT 化で手元 0.022× RT（M-43）。
**PIE カーネルも書けて QEMU で bit 一致まで確認した**（MAC の 99.40%。M-57 / M-58）。

**2026-08-30、QEMU で出荷ファームを起動から合成完了まで通した**（M-62）。
重み mmap（183 tensors / 16 B 境界）・端末側 G2P（53 ids が錨と完全一致）・
arena・合成 27,136 sample・int16 変換まで動き、
**PIE はスカラ実装と全 27,136 sample で bit 一致**した（陰性対照 PIE 命令 0 vs 5）。
⚠️ **当時は `-DSAAN_ENABLE_PIE=1` を明示したときだけ有効だったが、2026-09-03 に
ESP32-S3 では既定で有効にした**（D-048。W8A32 で測るなら `-DSAAN_ENABLE_PIE=0`）。
既定 blob も **int8 に切り替えた**（W8A8 は fp32 blob では 1 命令も効かないため、
`main.c` が起動時に検査して止める）。実機テストの手順は `esp32/TESTING.md`。

**同日、シリアルからの自由入力を足した**（M-63 / D-040）。**起動しても勝手には喋らず**、
錨との照合だけして `かな> ` プロンプトで **かな中間表現を 1 行**受ける
（起動時に喋らせるなら `-DSAAN_BOOT_SPEAK=1`。非対話ビルドでは既定で有効）。
QEMU の UART に実際に打ち込み、**基準の 1 行から `-DSAAN_BOOT_SPEAK=1` と同じ checksum**
（`0x04de91103a0e49f9`）が出ることを確認した。
⚠️ **「起動時の 1 発話は消さない」と書いたが、その日のうちに撤回した**（C-039）。
挙げた根拠 2 つのうち 1 つは G2P だけで足り、もう 1 つは**同じ節の中で自分が潰していた**。
漢字混じり文からの変換は **`uv run python scripts/to_intermediate.py "文"`**（ホスト）。
⚠️ **端末では正規化も推定もしない**（`。` も拒否）。端末だけの規則を 1 つ足した時点で
「ホストと端末で同じ列」という入力仕様の目的が崩れる。
⚠️ **この経路で音では気づけない欠陥を 2 回出した**（CRLF で空行 / 行頭 1 文字が
エコーされない）。どちらも**呼び出し側**の読み違いで、`make -C csrc line` の
G9 / G10 で固定した。

⚠️ **ホストとターゲットは bit 一致しない。それは正常**（float の丸めが違う）。
`|max|` 一致 + `Σx²` 相対差 1.6e-7 で丸め差と切り分ける。
**bit 一致を主張してよいのは同じターゲット上の 2 構成を比べたときだけ。**

✅ **速度は要件を満たした**（M-88 で 0.497 → M-90 で **0.446**。CoreS3 / W8A8+PIE / 満チャンク 1 pull）。
⚠️ **速度の主張は「その形のコードを実機で測った数字」だけ**（C-055）。**3 つとも根拠にならない**:
QEMU のサイクル（命令数に比例。M-80 → M-82）/ **objdump の命令数**（store → load の依存は命令数に出ない）/
**「似た形」のマイクロベンチ**（pie_probe E 節は旧形の写しで、新形を測っていなかった）。
実際にこれで **GELU が 118 → 211 cyc/要素に倍増**したまま通り、1 step が 14.7% 遅くなった（M-87）。
速度の判断は必ず**実機の `-DSAAN_PROFILE=1` の表**で行い、**マージ直後に前の版と 1 行ずつ並べる**（GELU 行だけ見れば 10 秒）。

⚠️ **`uv sync` は piper-plus のクローンを要求する**（`[tool.uv.sources]` が絶対パス）。
外の人が**重みだけで音を出す / ゲートを回す**経路は別にある（D-041 / M-65。README の
「最小セットアップ」）: `uv venv` + torch/numpy/soundfile を入れ、
`uv run --no-project python scripts/synthesize_student.py --intermediate "..."`。
かな→音素の表は `csrc/g2p_table.json` に凍結してあり、**OpenJTalk が要らない**
（SHA-256 検証つき。live との突き合わせは `uv run python scripts/kana_g2p.py`）。

```bash
uv sync --extra eval
uv run python scripts/phase0_verify_teacher.py     # 教師の疎通（6 チェック）
uv run python scripts/kana_g2p.py                  # 変換器 10 ケース + **凍結テーブルのドリフト検査**
uv run python scripts/test_losses.py               # 損失の性質（26 項目）
uv run python scripts/test_labelpack.py            # パック往復 + ゲート発火
uv run python scripts/test_discriminator.py        # 判別器（23 チェック）
uv run python .claude/hooks/test_guard_bash.py     # hook の回帰（94 ケース + commit ガード）
uv run python scripts/test_sanitize_reports.py     # 本文検出ゲート（16 ケース・陽性/陰性対照）
uv run python scripts/check_doc_counters.py        # 索引の M/D/C 番号 + **引用アンカー**
                                                   #   （陽性対照つき。C-042 / C-052）
uv run python scripts/check_doc_links.py           # md の相対リンクが実在するか（C-052）
uv run python scripts/check_release_assets.py      # 表の資産がリリースに在るか（要ネットワーク）
uv run python scripts/k1/k0_verify_dict.py         # 使う辞書が D-042 の凍結物か（陰性対照 2 種。C-045）
uv run python scripts/test_k1_dict.py              # K-1 辞書エンコーダ（G1〜G5。陰性対照つき）
make -C csrc jdict                                    # K-2/K-3 辞書リーダ + Viterbi + 未知語
                                                   #   （G6〜G11。⚠️ 辞書が要る）
make -C csrc accent                                    # K-4 アクセント規則 4 段（G12/G13）
make -C csrc njd-rules                                   # K-4b NJD チェーン（G14a/G14b/G14c。
                                                   #   段ごと・規則ごとの陰性対照つき）
make -C csrc oj-heap                                    # K-5 1 文ピーク RAM（G22〜G24。陽性対照つき）
make -C csrc kanji-e2e                                    # K-6 端末の全段 vs ホスト（G17/G17b〜d）
make -C csrc label-ids                                    # K-7 ラベル → 生徒インデックス（G25〜G27 +
                                                   #   **G25b/G25c**: 表を arena に置いても同じ列か）
make -C csrc kb-parity                             # **K-B 経路判定**が端末とホストで一致するか
                                                   #   （⚠️ pyopenjtalk が要るので all-test の外）
make -C csrc prof                                  # 段別プロファイラ（回数・要素数）。ゲートは
                                                   #   **--expect-no-lookup / --expect-steps 54 /
                                                   #   --expect-gelu 12544 / --expect-dw 21280 /
                                                   #   --expect-mac-le 4200628 / --expect-token 4**（S1 / T1〜T3）
make -C csrc erf                                   # GELU の erf 近似 vs libm erff（線形補間の陽性対照つき。S3）
make -C csrc range                                 # **S9（T2）の範囲版カーネル**が [0,T) 版と bit 一致か
                                                   #   （陽性対照つき。all-test に入っている）
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata ヘッダ（fp32 拒否の陽性対照。A-2）
bash scripts/check_esp32_template.sh               # esp32/ 雛形をホストで検査（**§10 = arena ≥ 漢字経路
                                                   #   の作業領域 + 14,464 B。陽性対照つき**）
make -C csrc all-test                              # C99 コア全ゲート（golden / stream / fft /
                                                   #   int8 / int8-golden / int8-e2e / arena /
                                                   #   g2p / pad / line / erf / **range**）
                                                   #   ⚠️ stream は **held-out 24 文 × 3 レーン**を見る
```

**ESP32 向けのビルドと QEMU 検証**（ESP-IDF v5.5。M-54 / M-56 / M-76 / M-90）:

```bash
export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:$PATH"   # ⚠️ 3.14 では venv が壊れる
. ~/esp/esp-idf/export.sh
export PATH="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin:$PATH"

# DevKit 構成（8 MB）。既定で **QIO + D-cache 64 B 行 + W8A8/PIE**（D-048 / M-84 / M-86）
cd esp32 && idf.py set-target esp32s3 && idf.py build

# ★ スタックチャン（M5 CoreS3）で漢字も喋る 1 本（M-90）。16 MB flash + 辞書パーティション
uv run python scripts/k1/k1_build_dict.py --out csrc/k1_dict.bin      # 13,702,320 B
cd esp32/boards/m5unified && idf.py -B build_m5k -DSDKCONFIG=build_m5k/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" \
    -DSAAN_KANJI=1 -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict.bin build
cd build_m5k && esptool.py --chip esp32s3 --port /dev/cu.usbmodem2101 \
    --baud 921600 write_flash @flash_args      # 辞書込みで約 4 分
# ⚠️ **`--flash_mode qio` を渡さないこと**（ヘッダが QIO になって ROM ローダが読めず
#    ブートループする。M-86）。`@flash_args` をそのまま使う

# QEMU（漢字構成）。⚠️ QEMU の flash モデルは QIO を受け付けないので
# `-DSAAN_QEMU=1` が sdkconfig.qemu で DIO に戻す（値は変わらない。M-85 / M-86）
cd esp32 && idf.py -B build_kanji -DSDKCONFIG=build_kanji/sdkconfig \
    -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.kanji" \
    -DSAAN_KANJI=1 -DSAAN_QEMU=1 build
cd build_kanji && esptool.py --chip esp32s3 merge_bin \
    --fill-flash-size 16MB -o /tmp/flash16.bin @flash_args
qemu-system-xtensa -nographic -machine esp32s3 -m 4M \
    -drive file=/tmp/flash16.bin,if=mtd,format=raw
# → `かな>` に `今日は良い天気ですね。` と打つ。**`!` は要らない**（経路は端末が決める。M-90）

cd esp32/pie_probe && idf.py qemu    # PIE の bit 一致（A/B/C 節）+ 重みの置き場所 / GELU（D/E 節。M-85）
```

⚠️ **PSRAM は QEMU では使えない**（octal PSRAM を持っていない）。
漢字経路の作業領域は**合成用 arena から切り出して**いるので無くても動く。
⚠️ **辞書の mmap は `esp_mmu_map`**（`esp_partition_mmap` は `CONFIG_SPI_FLASH_ROM_IMPL=y` の
M5 構成で 128 ページ = 8 MB しか貼れず、13.7 MB の辞書が `ESP_ERR_NO_MEM` になる。M-90 §4）。

## アーキテクチャ（固定仕様）

3つの決定的な生徒を **40次元の明示的な潜在インターフェース (c-line)** で繋ぐ。
このインターフェースを省いた merged モデルは論文の対照実験で崩壊している
(SCOREQ 1.06、訓練行の丸暗記) ので、**factored 構成は必須**。

```
音素ID x ─▶ Duration Dα (36,164) ─▶ Acoustic Aβ (199,536) ─▶ iSTFT Decoder Gγ (331,308) ─▶ 22.05kHz PCM
                width 32              width 48, 40ch出力         width 76, 1024pt iSTFT/hop 256
```

- 論文（英語・語彙 157）で 567,008 params / int8 blob 2個 679,832 B / 45 MMAC per audio-second
- **日本語は語彙 57 なので 559,008 params**（D-016）。⚠️ **MMAC は 1 も減らない**
  （埋め込みは表引き）。生徒は教師の音素IDを直接使えないので
  `src/saanotts_jp/vocab.py` の `TEACHER_TO_STUDENT` を通すこと
- 学習専用の `z→c` エンコーダ `Eρ` (14,952) は**デプロイ時に実行されない**
  （acoustic が c を直接出す）。パラメータ数の勘定に入れないこと
- z-line 版（1.4 M 級, quality tier）は `Eρ` を省き 192ch の z を直接ターゲットにし、
  hinge adversary を追加する。⚠️ **作っていない** — 567 K が先に目標へ届いたため

**実測（M-39 / M-41 / M-42）**: int8 blob **624,692 B**（payload。論文 679,832 B の −8.1%。⚠️ **blob v2（S4）はファイル 654,032 B**、事前整列の padding +10,096 B。M-81）。
C99 コアは golden test を Pearson 1.000000 で通過し、**ストリーミング版は
実行時メモリ 197 KB**（SRAM 512 KB の 38%）で一括版と **bit 完全一致**。
FFT 化で手元 **0.022× RT**（M-43）。
⚠️ **ESP32 への外挿では移植可能 C の fp32 は 2.47× RT で実時間に間に合わない**
（実測 η_host=0.364 を転移した値。η=1 の下限だけ見ると 0.93 で「あと少し」に誤読する）。
論文の 0.22× RT も **fp32 では達成不可能**で、**int8 + PIE が必須**。
✅ **「int8 + PIE が必須」は実機で裏づけられた**: 同じ CoreS3 で **W8A32 は 0.92〜1.09**（QIO。M-86）、
**W8A8+PIE は 0.446**（M-90）。⚠️ ただし **M-43 の 0.088× RT という外挿は実機の 18 倍外れた**。
差は積和ではなく量子化 / GELU / テンソル検索 / 重みのコピーだった（M-80）。**外挿で速度を語らない。**

## 教師モデルの扱い

教師は `~/Documents/piper-plus` の piper-plus。

**ONNX からは蒸留できない。** ONNX export は `output` と `durations` しか出さず、
必要な潜在 `z` が取れない。`.ckpt` を PyTorch で読み `SynthesizerTrn.infer()` を呼ぶこと。

`src/python/piper_train/vits/models.py:1002` の `infer()` が
`InferOutput(audio, attn, y_mask, (z, z_p, m_p, logs_p), durations)` を返し、
これが `yT` / `zT` / `dT` にそのまま対応する。

ラベル生成は必ず決定的に（論文 §II）:

```python
out = model.infer(x, x_lengths,
                  lid=torch.tensor([0]),   # ja=0。焼き込まれていないので必須
                  noise_scale=0.0,         # z_p = m_p になる
                  noise_scale_w=0.0,       # SDP が決定的になる
                  length_scale=1.0,
                  prosody_features=torch.zeros(1, len(ids), 3),  # D-014。下記
                  speaker_embeddings=None)   # ← None。理由は下記
```

**`speaker_embeddings` は `None` を渡す。** この ckpt は `num_speakers=1` で
`spk_proj` / `emb_g` を state_dict に 1 件も持たず、**何を渡しても bit 完全に無視される**
（None / `spk_tsukuyomi.npy` / ランダム 192次元 の 3 通りで audio が bit 一致することを実測）。
話者は重みに焼き込まれている。`eval/spk_tsukuyomi.npy` は SECS 評価専用でモデル入力ではない。

**EMA を明示的に適用すること。** `ema_generator_state`
(decay 0.9995 / num_updates 11000 / shadow 53 params) は `load_state_dict` では適用されない。
`piper_train.export_onnx.apply_ema_shadow_params(model.dec, ...)` を
**`remove_weight_norm()` より前に**呼ぶ（`remove_weight_norm()` が weight_g/weight_v を
融合してしまうため）。適用有無で `yT` の SNR は **14.5 dB** しかない
（`zT` / `dT` は bit 一致）。**適用件数を assert すること** —
`applied == len(shadow) and skipped == 0`。順序を間違えると 53 個中 23 個だけ当たり、
**EMA が半分載った第三の重み**になる（D-023）。

**`prosody_features` は本プロジェクトでは一律ゼロ**（D-014）。デバイスは A1/A2/A3 を
供給できないので、教師と生徒の条件を揃える。held-out 24 文の UTMOS は
実 prosody 1.730 / zeros 1.740 で**有意差なし (p=0.72)**。

⚠️ **ゼロは「prosody 無し」ではない。** `prosody_proj(0) = bias`（非ゼロ）が
concat されるため、None / ゼロ / 実 prosody で総フレーム数が 3 通りとも変わる。
**3 つのうちどれかで一貫させる**という決定であって、ゼロが中立なのではない。

**チャネルごとの `μ_T`, `σ_T` をラベルパックと一緒に保存すること。**
`L_c` のチャネル正規化項と、推論時の摩擦音ノイズ注入 `σT_k` の両方で必要になる。

piper-plus との整合で効いてくる点:

- `inter_channels = 192` は論文の教師潜在と一致。hop 256 / 22.05 kHz → 86.13 fps も
  論文の decoder と一致するので、潜在インターフェースはそのまま移植できる
- piper-plus は multilingual (`lid`) + zero-shot 話者埋め込み (CAM++) + 韻律 (A1/A2/A3) で
  条件づけられている。ラベル生成時にすべて固定する
- 公開 base ckpt (`ayousanz/piper-plus-base`) は piper-plus v2.0 では
  `MBiSTFTGenerator.cond` の FiLM 化により size mismatch する (256 vs 512)。
  使うなら piper-plus 側を `v1.13.0` に checkout する
- 音素表は ckpt 側の `phoneme_id_map` / `num_symbols` をそのまま使う。
  現行コードの `get_phoneme_id_map()` は 185 symbol を返すので、
  173 symbol の公開 ckpt とは合わない

## 日本語固有の設計判断

英語版をそのまま移すと壊れる、または判断が必要な箇所。

**G2P が本プロジェクトの主目的。** ブラウザは piper-plus の WebAssembly で既に解決済みなので、
**ESP32 で動かなければこのプロジェクトに意味がない**（2026-08-26 ユーザー判断）。
したがって「音素ID入力に割り切る」は逃げ道にならず、**オンデバイス G2P の成立が
プロジェクト全体の成否を決める**。1.4 M quality tier は成果物ではなく、
蒸留レシピが日本語で機能するかを速く確かめるための足場としてのみ意味を持つ。

NAIST-JDIC は実測 102 MB でそのままでは載らないが、**内訳を見ると枝刈りの余地が大きい**:

```
sys.dic         103,082,017 B   lexsize = 788,923 エントリ (131 B/エントリ)
  ├ feature 文字列  67,242,425 B  ← 全体の 65.2%。品詞・活用形など TTS に不要な情報が大半
  ├ darts trie      23,216,752 B
  └ token            12,622,768 B
matrix.bin        3,792,262 B    char.bin 262,496 B
```

TTS に必要なのは **読み・アクセント型・アクセント結合規則・最小限の品詞**だけ。
エントリ数の枝刈りと feature 文字列の削減は独立に効く:

| 枝刈り後の語数 | 素の概算 | feature を TTS 用に削れば |
|---:|---:|---:|
| 10,000 | 1.2 MB | 〜0.6 MB |
| 30,000 | 3.7 MB | 〜1.8 MB |
| 60,000 | 7.5 MB | 〜3.7 MB |

⚠️ **この路線は B-0 で不成立と判定済み**（D-009）。枝刈り辞書を実際にビルドすると
線形概算の **1.50〜2.74 倍**になり（C-009）、必要精度を出すには 40 MiB 要った。
**解決したのは辞書ではなく入力仕様の変更**（下記「入力仕様」節、端末側 1,786 B）。
上の表は「なぜ辞書路線を捨てたか」の記録として残してある。

**ピッチアクセント。** piper-plus の日本語 duration predictor は OpenJTalk の
A1/A2/A3 を `prosody_dim=16` で注入しているが、論文の duration student は音素IDしか見ない。
まずは音素列に既に入っているアクセント記号 (`#` 句境界 / `[` 上昇 / `]` 下降核) で足りるか試す。
不足なら生徒 duration net に A1/A2/A3 の3スカラーを足す（width 32 なので増分は数百 params）。
**この良し悪しはアクセント型のミニマルペア（橋/箸/端、雨/飴）を評価セットに入れないと検出できない。**

✅ **記号だけで足りた**（M-44 / D-030）。**A1/A2/A3 の追加は不要**なので D-014 と
入力仕様 D-010 / D-011 をそのまま維持できる。評価は
`uv run python scripts/d4_accent_pairs.py --ckpt runs/v3/stage4.pt`（18 秒）。
⚠️ **「A と B の音が違う」は再現の証拠にならない** — `[` `]` `#` は教師で実フレームを
持つので、アクセントを無視するモデルでも音は変わる。**判定は必ず教師との向きの一致**
（`cos > 0`）で行い、**chance を 0.5 と書かない**（経験的ヌル 0.614）。

**摩擦音と無声化母音。** 論文は SCOREQ 4.09 の裏で sibilant が whistly になる欠陥を見逃した
（音素クラス別の 2–8 kHz スペクトル平坦度で初めて検出: 教師 0.689 → 生徒 0.590）。
日本語は摩擦音が多いうえ**無声化母音 `A/I/U/E/O` が音響的にほぼ摩擦雑音**で、
「です」「ます」「した」など高頻度語に出る。式7 のノイズ注入集合を拡張する:

```
S_ja = {s, sh, ts, ch, z, j, h, hy, f, I, U}
```

⚠️ 当初 `A` `E` `O`（無声化母音）も入れていたが、**コーパス 23,271 行の実測で 1 度も出現しない**
ため除外した。日本語の母音無声化は狭母音 `i` `u` にほぼ限られる。

`β` は聴取で決める（論文も同様）。**音素クラス別スペクトル平坦度プローブは
最初から評価パイプラインに入れること** — 集約スコアだけでは検出できない。
設定は `src/saanotts_jp/flatness.py` に凍結済み
（`n_fft=1024 / hop=256 / guard=0 / power=1`、教師ベースライン付き）。
`devoiced (I,U) vs vowel` は AUC 0.8466 (n=495) で、`S_ja` に `I` `U` を入れる判断を支える。
⚠️ **SFM は必ず帯域内 RMS と併記する。** `geminate` の基準値は閉鎖区間の
int16 量子化床を測っていて、教師の性質ではない（M-27）。

**評価の主指標は SCOREQ synthetic/nr、UTMOS を併記する（D-020）。**
`scoreq==1.0.1` は PyPI にあり導入済み。ラッパは `src/saanotts_jp/scoreq_metric.py`
を通すこと（`scoreq.Scoreq` を直接呼ぶと torchaudio 2.13 が torchcodec を要求して落ちる）。
⚠️ **`data_domain="natural"` は使わない** — 伝送劣化モデルで、合成音声を実人間より
高く採点し UTMOS と無相関になる。

| 指標 | 教師 | 実人間 | 教師/人間 |
|---|---:|---:|---:|
| SCOREQ synthetic/nr | 2.0488 | 2.4983 | **0.820** |
| UTMOS | 1.7479 | 2.3047 | **0.758** |

**評価は「教師比」と「人間音声比」の両方で報告する（D-013）。**
UTMOS は日本語でスケールが圧縮されており、**実人間の日本語音声ですら 2.305 しか出ない**。
天井を測らずに絶対値を英語と比べると判断を誤る（実際に誤った、C-012）。
人間音声の分母は**教師の元コーパス**（つくよみちゃんコーパス）を使う。

論文はベトナム語・インドネシア語について
"we report only the ratio to the corresponding teacher because absolute SCOREQ values
are not calibrated for comparisons across languages" と明言している。
日本語の SCOREQ / UTMOS 絶対値を英語モデルと比較しないこと。
WER は日本語では分かち書きが問題になるので **CER** を主指標にする。

**UTMOS も日本語では較正されていない。** 著者は東大猿渡研だが、学習データは
VoiceMOS Challenge 2022 の main track = BVCC（英語）/ OOD track = BC2019（中国語）で、
**日本語は含まれない**。UTMOS も SCOREQ と同様に教師比で報告すること。

**`clip_[1,80]` と length scale `s_v`。** `s_v` は英語 1.08 / ベトナム語・インドネシア語 1.16
で言語ごとに較正されている。日本語は実測して決める。
上限 80 フレーム ≒ 0.93 秒が長音や語末の引き延ばしで飽和しないか `dT` のヒストグラムで確認する。

## 蒸留データ

**音声データは不要。テキストのみ。** ラベルは全部教師の決定的推論から出る。

論文の実績: 512 行では汎化せず (diverse24 で 1.72、narrow set では 3.07 に見えて **1.35 の過大評価**)、
14,343 行で 2.54。**1万行以上を目標にする。**

日本語で必ずカバーすべき多様性軸（英語版には無い）:
漢字混じり / ひらがなのみ / カタカナ語 / 英数字混在 / 数詞と助数詞（1つ・1個・1人で読みが変わる）/
日付・時刻・金額 / 約物 / 疑問文（`?` `?!` `?.` `?~` の4種の EOS トークンがある）/
アクセント型ミニマルペア。**テンプレート文は使わない。**

## このプロジェクトの skill / hook

`.claude/` に品質ガードを置いてある。**内容は過去の実際の事故から作られている。**

| 種類 | 名前 | いつ効くか |
|---|---|---|
| skill | `recording-measurements` | 数値・サイズ・**能力の有無**の主張を docs に書く前 |
| skill | `teacher-inference` | 教師モデルを呼ぶとき（6 つの沈黙する失敗を防ぐ） |
| skill | `student-training` | 生徒・損失・学習ループを触るとき（語彙写像 / iSTFT / λ / PAD） |
| skill | `evaluating-quality` | SCOREQ / UTMOS / 平坦度で品質を測る・報告するとき |
| skill | `verifying-reports` | サブエージェントや過去セッションの報告を docs に転記する前 |
| skill | `writing-gates` | **テスト・アサーション・受け入れゲート・ベンチを書くとき**（空虚に通るゲートを防ぐ） |
| テスト | `make -C csrc njd-rules` / `oj-heap` / `kanji-e2e` / `label-ids` / `kb-parity` | **K トラックの受け入れゲート**（`kb-parity` は**経路の 3 値判定**がホストと一致するか = K-B）。⚠️ どれも辞書と pyopenjtalk が要るので `all-test` には**入れていない** |
| テスト | `scripts/k1/k4b_vendor.py --check` | **取り込んだ Open JTalk が上流 + PATCHES と一致するか**。⚠️ 表に無い改変は落ちる |
| テスト | `scripts/check_partitions.py --file <csv>` | パーティション表（8 MB / 16 MB の両方）|
| テスト | `scripts/check_doc_counters.py` | **索引の M/D/C 番号 + 引用アンカー**。⚠️ 番号は書いた瞬間から古くなる（C-042）。⚠️ **番号が「ずれる」と「入れ替わる」は別の壊れ方**で、後者は主張と番号の対応を見ないと捕まらない（C-052） |
| テスト | `scripts/check_doc_links.py` | **md の相対リンクが実在するか**（陽性対照つき）。⚠️ **外部 URL は見ない** |
| テスト | `scripts/check_release_assets.py` | **ドキュメントの表に名前がある資産が、実際にそのタグに在るか**。⚠️ **ネットワークが要る**。⚠️ 見るのは名前だけで**中身は見ない**（C-052） |
| テスト | `make -C csrc erf` | **GELU の erf 近似が libm と 2e-7 で一致**（S3）。線形補間に落とした**陽性対照**が落ちることで、しきい値が効いていると言える。`all-test` と CI に入っている |
| テスト | `make -C csrc prof` | 段別プロファイラ（回数・要素数）。ゲートは **`--expect-no-lookup`**（pull 中のテンソル検索 0 回。S1）と **`--expect-steps 54` / `--expect-gelu 12544` / `--expect-dw 21280` / `--expect-mac-le 4200628` / `--expect-token 4`**（T1〜T3 で減った量を実測値そのままで固定してある。増える変更はここで止まる）。⚠️ **ホストの時間は実機の内訳ではない**（C-055） |
| テスト | `make -C csrc range` | **S9（T2）の範囲版カーネル**が `[0,T)` 版とランダム形状で bit 一致するか（**陽性対照つき**: 1 列ずらすと必ず落ちる）。`all-test` に入っている |
| テスト | `scripts/check_esp32_template.sh` | `esp32/` 雛形をホストで検査（10 節）。**§10 は `SAAN_ARENA_BYTES ≥ SAAN_KANJI_WORKBYTES + SAAN_KANJI_T10_BSS_BYTES`** を両方ソースから取って比べ、足りない arena ではコンパイルが止まることを**陽性対照**で示す。⚠️ **`T10_BSS_BYTES` は `0u`**（`esp32/main/main.c:157`。T10 で .bss に移す予定だった 14,464 B は結局 arena に置いたので二重計上になる）。**「+ 14,464 B」と書いていたのは古い** — 残っているのは陽性対照のリテラルだけ |
| テスト | `scripts/check_web_gates.sh` | **W トラックの受け入れゲート 8 本**（G-W7 / G-W1 / G-W2 / **G-W2b** / G-W3 / G-W4 / G-W5 / **G-W6**）。⚠️ **全部に陽性対照**。**G-W6 が出荷する `web/dist/*.mjs` を実際に叩く** — これを入れるまで `web/saan_web.c` は**どのゲートでもコンパイルされていなかった**（`#error` を入れても exit 0。M-94 §11）。⚠️ **辞書 13.7 MB が要る部分**（漢字 == かな）は CI で回らず、**skip せず「回せなかった」と出る** |
| テスト | `scripts/test_blob_to_header.py` | blob → `.rodata` ヘッダ変換（SHA-256 一致 / **fp32 拒否の陽性対照**）。CI の docs job |
| テスト | `scripts/check_partitions.py --rodata` | `model` 行の無い表（`esp32/boards/*`）。app が 1.5 MB + blob ぶんあるか |
| CI | `.github/workflows/ci.yml` | push / PR で **6 job**（docs / golden / csrc / python / release-assets / **web**）。⚠️ **かつて「4 job」と書いてあったが、数えたら違った**（job は増える）。**新規 clone だけで通るゲートに限ってある**。範囲は [`.github/workflows/README.md`](.github/workflows/README.md) |
| CI | `.github/workflows/pages.yml` | **W トラックの配置**（wasm を焼いて `_site/` を Pages へ）。⚠️ **`scripts/check_ci_coverage.py` は `ci.yml` しか読まない**ので、ここのゲートは誰も監査しない |
| テスト | `scripts/test_sanitize_reports.py` | **本文検出ゲート自身の回帰**（16 ケース）。⚠️ 「0 箇所」が空虚でないことを陽性対照で保証する（C-028） |
| hook | `.claude/hooks/guard_bash.py` | Bash 実行前。piper-plus への書き込み / `pip install` / uv 非経由の python / **本番ラベルパックの破棄** / **既存パックへの再生成** / **公式実装 (GPL-3.0) のソース取得** / **staged なコーパス本文を含む `git commit`** を deny（**94 ケース + commit ガード 6 件**の回帰テスト付き） |
| 宣言 | `settings.json` の `permissions.deny` | Edit/Write ツールでの piper-plus 改変を禁止 |

hook を変えたら必ず回帰テストを通すこと（誤検知があると全 Bash が止まる）:

```bash
uv run python .claude/hooks/test_guard_bash.py     # 83/83 + commit ガード 6 件
```

⚠️ **誤検知は 5 回踏んでいる**（C-011 で 3 回、C-020 で 1 回、C-025 で 1 回）。C-020 と C-025 はどちらも **C-015 で直したはずの「走査範囲を 1 コマンドに閉じる」が別の場所に残っていた**再発。
**「このコマンドの引数」を見る判定は、必ず先にコマンド単位へ切り出してから行うこと。**

## 開発環境のルール

### Python は必ず `uv` を使う

```bash
uv sync                       # 環境構築（初回・依存変更時）
uv run python scripts/xxx.py  # 実行
uv add <pkg>                  # 依存追加（pip install しない）
```

- **`pip install` を直接使わない。** `uv add` で `pyproject.toml` と `uv.lock` に記録する
- piper-plus は `[tool.uv.sources]` の **path 依存 (editable)** で参照する。
  **piper-plus のリポジトリは読み取り専用**（checkout / commit / 編集の禁止）
- uv 環境には M-1.1 の stale な `piper_train` が存在しないので、
  `sys.path.insert` は不要（既存スクリプトのものは冗長だが害はない）

環境差の影響は実測済み。piper-plus venv (py3.13.9/torch2.11) と
uv 環境 (py3.14.0/torch2.13) で **教師ラベルは bit 完全一致**する（M-15）。

### 学習とラベル生成は手元の M4 Max で行う（D-027）

実測すると手元で完結する。**vast.ai は不要**（D-012 の実行環境部分を撤回）。

| 工程 | device | 実測 |
|---|---|---|
| ラベル生成 train 20,894 文 | **CPU** | 116 ms/文 → **約 40 分** / 4.5 GB |
| 学習 4 段（各 20k step） | **MPS** | 58 / 81 / 58 / 39 ms/step → 約 1.3 時間 |

```bash
uv run python scripts/gen_teacher_labels.py --split train   --out data/pack
uv run python scripts/gen_teacher_labels.py --split heldout --out data/pack_heldout
uv run python scripts/train_student.py --run runs/v1 --all --steps 20000
uv run python scripts/synthesize_student.py --ckpt runs/v1/stage4.pt \
    --texts data/splits/corpus_heldout.tsv --limit 24 --out reports/student_wav
```

⚠️ **ラベル生成は CPU。MPS を使わない。** CPU と MPS は bit 一致しない
（M-21、SNR 97〜106 dB）。CPU 生成は M-15 で piper-plus venv と bit 完全一致が
確認済みで、**未検証の CUDA parity ゲートも回避できる**。40 分なら CPU で困らない。

⚠️ **ラベルは一度だけ生成し、SHA-256 と生成環境を manifest に固定する**（D-015）。
hook が本番パック `data/pack` の破棄と再生成を deny する。

⚠️ **リモート（vast.ai）の手順書は 2026-09-03 に削除した**（D-027 の追記）。
使う理由（λ の並列探索 / 1.4 M z-line）がどちらも消え、走らせた記録も無いのに
**未通過の CUDA parity ゲートを含む手順**を残す方が危なかった。
再びリモートで回すなら `deploy/vastai_bootstrap.sh` と `scripts/b4_device_parity.py --device cuda`、
[`docs/requirements.md`](docs/requirements.md) §8.4 から組み直すこと。

## piper-plus の参照点

| 用途 | パス（`~/Documents/piper-plus` 相対） |
|---|---|
| 教師の `infer()` | `src/python/piper_train/vits/models.py:1002` |
| 韻律 A1/A2/A3 の注入 | `src/python/piper_train/vits/models.py:870` (`_prepare_prosody_input`) |
| MB-iSTFT decoder | `src/python/piper_train/vits/mb_istft.py` |
| 日本語音素表 | `src/python_run/piper_plus/phonemize/jp_id_map.py` |
| 音素→PUA マップ（C++ と一致必須） | `src/python/jp_phoneme_map.py` |
| UTMOS22 / Whisper WER / PESQ / STOI | `scripts/audio_quality_metrics.py` （SCOREQ は本リポジトリの `src/saanotts_jp/scoreq_metric.py`） |
| MOS リスニング調査 | `tools/benchmark/` (`docs/benchmark-mos.md`) |
| 日本語評価文のシード | `scripts/evaluation/evaluation_texts_ja.txt` |
| データセットのライセンス台帳 | `data-sources.yml` |

piper-plus の Python 環境は `uv` workspace（`.venv/`, Python 3.13, torch 2.11）。
教師を動かすスクリプトは piper-plus 側の venv で実行するのが早い:

```bash
~/Documents/piper-plus/.venv/bin/python <script>
```

## スコープ

**ターゲットは ESP32。ブラウザは対象外**（piper-plus の WebAssembly で既に解決済みのため、
そこを再実装しても価値が無い — 2026-08-26 ユーザー判断）。

⚠️ **2026-09-03、W トラックを足した（D-050）。この行は撤回していない。**
`csrc/` の C99 コアと `esp32/main/saan_kanji.c` を**書き換えずに** wasm にして、
GitHub Pages で「漢字文を打つと喋る」デモを配る（[`docs/plan/web-demo-plan.md`](docs/plan/web-demo-plan.md)）。
**成果物は今も ESP32 の 567 K で、Web は触れる入口**でしかない
（piper-plus の代わりを作るのではなく、**実機に載っているそのコード**を動かす。arena も同じ 180,224 B）。
実測は M-94（node）/ **M-95（Chrome 152）** / **M-96（聴取）**。**ブラウザで PCM が node と bit 一致**し、短文が **0.008〜0.019 ×RT** で合成でき、**両レーンとも聴いてもらって「問題なかった」/ 途切れ無し**。⚠️ **1 名・対照なし・盲検なし / モバイルと Safari は未測定**。
⚠️ 上流も WASM デモを配っているが、**D-032（GPL ソースを読まない）は維持**する。

したがって:
- **成果物は 567 K の embedded tier**（論文で SCOREQ 2.54 / ESP32-S3 で 0.22× RT）
- 1.4 M quality tier は**成果物ではなく検証用の足場**。蒸留レシピが日本語で機能するかを
  速く確かめ、567 K との品質差を測るためだけに作る
- **オンデバイス日本語 G2P が成立しなければプロジェクトの意味が無い**（上記「G2P」節）

⚠️ **「PoC であり生成物を配布しない」は着手時（D-006 / 2026-08-26）の前提で、既に古い。**
教師コーパスのライセンス（つくよみちゃんコーパス `CC-BY-4.0`、MOE-Speech `CC-BY-SA-4.0`、
どちらも当時 `verified: false`）を着手のブロッカーにしない、という判断だった。

**その「公開する段になったら再確認」は済んでいる。** 方針は **D-035**（初期リリースは
現行素材のまま配布する）で変わり、**D-039** で重みを **MIT ではない専用ライセンス**
（[`LICENSE-MODEL.md`](LICENSE-MODEL.md) = `LicenseRef-sanoTTS-jp-Model-1.0`）にした。
**いま実際に配っているもの**: Releases の重み・blob・サンプル音声・firmware と、
**GitHub Pages のデモ**（D-050。⚠️ Pages に置くことも再配布なので §3.1 の帰属ブロックと
§3.2 の用途制限をページ本文に載せている）。

## 教師 `.ckpt`（確定）

**`ayousanz/piper-plus-zero-shot-tsukuyomi` (private) の
`epoch=499-step=22000.ckpt` (927 MB) を教師にする。**

2026-08-26 に HF API で全 repo を調査して確定。公開 repo の
`piper-plus-tsukuyomi-chan` / `piper-plus-css10-ja-6lang` は **ONNX しか無く、
ONNX からは潜在 `z` が取れないので蒸留に使えない**（`docs/research/` §2.2）。

この ckpt を選んだ根拠 — `config.json` が公開 canonical モデルと一致する:

```
num_speakers: 1   num_languages: 6   phoneme_id_map: 185 entries
quality: "dataset-tsukuyomi-finetune-6lang"
```

（ローカル `~/Documents/piper-plus/models/tsukuyomi.onnx` は公開版
`tsukuyomi-chan-6lang-fp16.onnx` と SHA-256 が一致することを確認済み:
`5289e9b6eaf21080803b7fe1c4dc85b5491d4c216121207a41df18dd5f68e5d7`）

同 repo の **`eval/spk_tsukuyomi.npy` が話者埋め込みの参照ファイル**で、
ラベル生成時に話者を固定するのにそのまま使える。

### 互換性は実測で解決済み (2026-08-26)

**この ckpt は piper-plus v2.0 (HEAD) にそのままロードできる。`v1.13.0` への checkout は不要。**

ckpt の `hyper_parameters` 実測値:

```
num_symbols=173  num_speakers=1  num_languages=6
inter_channels=192  hidden_channels=192  gin_channels=512
spk_embed_dim=192  use_zero_shot=True  prosody_dim=16
resblock=2  upsample_rates=(4,4)  upsample_initial_channel=256  use_sdp=True
```

- `spk_embed_dim=192` なので v2.0 の 192 次元チェックを通る。
  公開 ONNX の `speaker_embedding[B, 256]` は**別系統のエクスポートで、この ckpt とは無関係**だった
- `dec.cond.weight` が `(512, 512, 1)` で `cond_layers` も存在 → **Multi-scale FiLM 適用済み** =
  v2.0 のアーキテクチャ。`normalize_checkpoint_state_dict` の `cond_migrated` は 0
- `load_state_dict` は **missing 0 / unexpected 0**
- `eval/spk_tsukuyomi.npy` は shape `(192,)` / L2 ノルム 1.0 で、`speaker_embeddings` にそのまま渡せる

再現: `scripts/phase0_verify_teacher.py`（5 チェックすべて PASS）

```
zT latent   : (1, 192, 37)   ← 192ch、論文の教師潜在と一致
yT audio    : (1, 1, 9472)   ← 37 frames × hop 256 == 9472 sample
audio / z ともに二回実行で bit 完全一致（決定的）
```

### ⚠️ 音素表の落とし穴（実測で判明）

1. **`num_symbols=173` だが `config.json` の `phoneme_id_map` は 185 entry ある。**
   ID 173 以上を渡すと埋め込みの範囲外になる。先頭 173 だけが有効。
2. **拗音・破擦音 (`ch` `sh` `ts` `ky` など) は `phoneme_id_map` に生の文字列で入っていない。**
   PUA (U+E000〜) にエンコードされている（`phoneme_id_map["ch"]` は KeyError）。
   **canonical な変換表は `piper_plus_g2p.encode.pua` の `TOKEN2CHAR` / `CHAR2TOKEN` (99 entry)。**
3. **`src/python/jp_phoneme_map.py` の `get_phoneme_id_map()` を使ってはいけない。**
   58 entry / max id 57 しか返さず、**実測で 54 音素の id が ckpt と食い違う**
   （`a` は jp_phoneme_map で 7、ckpt では 10）。使うと音素ラベルが黙って総取り違えになる。

他の候補（今回は不採用）:
- `piper-jp-en-model/tsukuyomi-v4-*` — ja-en bilingual、`num_symbols=97`、別系統
- `piper-plus-tsukuyomi-chan-all/lightning_logs/version_3/*` — WavLM 300epoch (1.96 GB)
- `piper-plus-base/model.ckpt` — 571話者 multilingual、単一話者ではない

## ⚠️ 環境の落とし穴: stale な `piper_train`

`.venv/lib/python3.13/site-packages/piper_train/` に **v1.13.0 相当の古いコピー**が実在し、
これは別ディストリビューション `piper_plus_workspace-1.12.0` の所有物なので
`pip install -e src/python` をやり直しても消えない。setuptools の editable finder が
`sys.meta_path` に **append**（insert ではない）されるため、標準の `PathFinder` が先に走って
古い方が解決される。

```python
# NG: site-packages の v1.13.0 相当が読まれる
import piper_train.vits.models as M   # → .venv/lib/.../site-packages/piper_train/...

# OK
import sys; sys.path.insert(0, "~/Documents/piper-plus/src/python")
# または PYTHONPATH=~/Documents/piper-plus/src/python
```

**教師を触るスクリプトは必ず `__file__` を assert して掴んだ実体を検証すること**
（`scripts/phase0_verify_teacher.py` がその実装例）。

## 入力仕様（確定）

**中間表現「ひらがな + アクセント記号 + 無声化マーク」**。
要件定義は [`docs/requirements.md`](docs/requirements.md)、決定の経緯は D-010 / D-011。
⚠️ **「漢字は端末で扱わない」は 2026-09 に半分だけ古くなった。** K トラック（`-DSAAN_KANJI=1`）を
入れた板は**漢字文をそのまま受ける**（M-90）。端末は 3 値で経路を決め、**かな行と漢字行から同じ PCM が出る**。
この節の中間表現は**その両方の共通の中間表現**であって、蒸留・学習・ホスト側の経路は今も全部これ。

```
今日は良い天気ですね。  →  きょ][おわよ][いて][んきです°ね
[ 上昇 / ] 下降核 / # 句境界 / ° 無声化
```

- 端末側は **テーブル 877 B のみ**（mora 195 件 780 B + 記号 10 件 40 B + `ん` 異音 21 件 57 B。
  `csrc/g2p_table.h`。⚠️ **951 B / 913 B と書いていたのは誤り** = C-042）
- ホスト側で漢字文から中間表現を生成する（オフライン・OpenJTalk 使用）
- held-out で表現可能 96.40% / 往復一致 **100%** / 教師出力と **bit 完全一致**

**アクセントと無声化を規則で推定してはいけない。** ひらがなのみだと
フル 103 MB 辞書を積んでもアクセント一致は **15%**、無声化の規則推定は 170 箇所を過剰適用した。

## 符号化規則は canonical と同一にする

教師は学習時に **トークン間に `_` の intersperse padding が入った列**を見ている。
これを飛ばして自前で音素→ID を組むと **発話が約 2.4 倍速になる**
（実測 17.7 mora/s、正常は 7.6〜8.4 mora/s。C-007）。**例外は出ない。**

厳密な関係式（全 23,297 発話で成立、`scripts/b4_length_hist.py` が assert する）:

```
len(ids) == 2 * n_phonemes + 3 + (PAD 音素の数)
```

⚠️ **`2 * n_tokens + 3` ではない。** 1 モーラが 1〜2 音素になる（`きょ` → `ky` `o`）。
PAD 項は `#` 句境界などで**音素そのものが PAD になる**件数で、canonical 規則
「PAD の後ろに PAD を挟まない」により 1 個で済む（C-019）。

疎通確認だけなら canonical 関数がそのまま使える（`language_id_map` を必ず渡す）:

```python
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody
ids, prosody = text_to_phoneme_ids_and_prosody(
    text, phoneme_id_map, language="ja", language_id_map=lim)
```

⚠️ **ただしラベル生成では使わない。** この関数は漢字文を直接受けるので、
デバイスが作る入力（かな中間表現）と経路が違ってしまう。本番は次節の経路。
`scripts/phase0_verify_teacher.py` が canonical 側の実装例。

## ラベル生成の経路（確定・D-014）

```
漢字文 ──[ホスト・OpenJTalk]──▶ 中間表現 ──[kana_g2p]──▶ 音素ID ──▶ 教師 ──▶ ラベル
                                              ↑ デバイスと同じ変換器
```

**生徒が学ぶ入力とデバイスが作る入力を一致させる。** 実装は
`scripts/gen_teacher_labels.py`。以下は必ず守ること:

1. **PAD 規則を canonical と同一にする** — 「その音素自身が PAD なら後ろに挟まない」。
   外すと音素ID一致が 86% → **0%** になる。**例外は出ない**（M-20）
2. **`prosody_features` はゼロ** — デバイスが供給できないので教師と条件を揃える。
   UTMOS に有意差が無いことを実測済み（p=0.72）
3. **保存前に 13 個のゲートを通す** — 特に **G7（intersperse padding）**と
   **G12（発話速度 4–12 mora/s）**。自前で音素化すると 2.4 倍速になるが、
   音として再生できるので他のゲートは全部通る

**この経路にしたことで、旧 B-1（かな無し行 5.36% が中国語音素になる問題）と
旧 B-2（prosody の無警告ズレ）は構造的に消えた。**

4. **教師の FT テキスト 102 uid を除外する** — `data/splits/exclusions_teacher_ft.txt`。
   `jsut/voiceactress100` と `jsut/repeat500` は教師の学習テキストそのもの（D-024）
5. **長さは `max_spec_length=700`（8.13 秒）で切る** — 4.31% が該当。
   `max_phoneme_ids=400` は 0.11% しか効かない（D-017）

⚠️ **入力サニタイズは別途必要。** 未知語は誤読ではなく無音で脱落する（下記）。
記号も同じ壊れ方をする: `〜`(U+301C) は疑問 EOS `?~` にならず**黙って消えていた**。
`kana_g2p.normalize_input()` で U+FF5E に寄せて塞いだ。

## 残っているタスク（2026-09-04 更新。**対照つきの聴取と、判断が 2 つ**）

**Phase 0 / A / B / C / D-1〜D-3d、検証タスク B-0 〜 B-12 / D-4 / E-1 / E-2 / E-2b、
K-0 〜 K-8、速度の S1〜S5b と T1〜T5 は全部決着した。** 設計値は D-016 〜 D-050 として凍結（⚠️ **D-049 は欠番** = RTF の分母用に予約）。
現在地は [`docs/README.md`](docs/README.md)。

| # | 何 | 種類 | ゲート |
|---|---|---|---|
| **1** | **対照つきの聴取** — ⚠️ **ざっとした聴取は済んでいる**（M-91 / M-93 = 実機 / M-96 = ブラウザ。どれも**1 名・対照なし・盲検なし**）。残るのは `reports/k8_listen/` の 12 組（枝刈りの誤読）と `reports/d4_accent/`（アクセントの過剰強調 `magnitude_ratio` 1.193） | **人が要る**（私は音を聞けない） | **G32** |
| 2 | RTF の分母（満チャンク 1 pull = 0.446 で達成 / 発話全体 = 0.54〜0.71 で未達） | 判断 | **未決。D-049 で決める**（`docs/requirements.md` §6.2） |
| ~~3~~ | ~~リリース資産の blob を v1 → v2 に上げる~~ → ✅ **v0.3.0 で既に v2 だった**（誤りだった。C-057） | — | — |
| ~~4~~ | ~~配布イメージを USB Serial/JTAG 入力でも配る~~ → ✅ **v0.3.0 で配っている**（`esp32s3-firmware-kanji-16mb-usbjtag.bin` / `esp32s3-firmware-w8a8-pie-usbjtag.bin` の実在をリリースで確認）。⚠️ **v0.2.0 以前のイメージは UART0 のまま**なので、CoreS3 / AtomS3 では入れ替えが要る（M-83） | — | — |
| 5 | K トラックのエントリ数・接続行列（今は 438,750 / int16） | 判断 | D-044 を見直すか |
| ~~6~~ | ~~GitHub Pages の有効化~~ → ✅ **2026-09-04 に有効化された**（`build_type: workflow`。⚠️ **手作業だった** — `configure-pages` の `enablement: true` は既定トークンでは効かない）。⚠️ **`pages.yml` は `main` への push でしか走らない**ので、URL が開くのはマージ後 | — | — |
| ~~7~~ | ~~ブラウザでの実測と聴取~~ → ✅ **測った**（**M-95** Chrome 152）**+ 聴いてもらった**（**M-96** 両レーンとも「問題なかった」/ 途切れ無し）。⚠️ **1 名・対照なし・盲検なし / モバイルと Safari は未測定** | — | — |

**素材はそろっている。** 端末の ids はホストの生徒モデルにそのまま入るので、
**端末の音とホストの音は実機なしで直接比べられる**（M-78）:

| 何を聴くか | 場所 | 聴かないと分からない理由 |
|---|---|---|
| 枝刈りの誤読 | `reports/k8_listen/`（12 組。`k8_audio_gap.py --wav-dir` で再生成） | **SCOREQ は誤読を罰しない**（M-78: 差 −0.0127、CI が 0 を跨ぐ） |
| アクセントの過剰強調 | `reports/d4_accent/student` と `teacher`（各 64 本） | `magnitude_ratio` **1.193** = 生徒の起伏が教師より 19% 大きい（M-59） |
| 未知語フォールバック | 専用セットは無い（`jdict_unk_guess` の出力を作るところから） | 「無音より誤読の方が良い」は**測っていない仮定**のまま |
| 生徒の音そのもの | `reports/student_wav_v3/`（held-out 24 文） | 集約スコアは摩擦音の欠陥を見逃す（論文が実際に見逃した） |

⚠️ **実機のスピーカーでしか分からないものが残る**（G32 の本体）: **途切れ・音量・実サンプルレートの誤差**。
checksum が一致しても、M5.Speaker の DMA の実挙動は別（M-90 §5）。

⚠️ **聴取が決定を覆す可能性がある。** D-044（438,750 entries）は**「音素の 0.32%」だけで決めた**。
⚠️ **聴取者は今のところ 1 名**で、v2↔v3 の差は 7 試行で聴き分けられなかった（C-037）。

**かなトラック / K トラックに実装として書くものは残っていない。** 1〜5 は「聴く」「決める」。
⚠️ **6 と 7 は W トラック**（D-050）で、**6 はコードでは解決できない**（GitHub の設定画面）。

⚠️ **これから板を買うなら N16R8。** 8 MB では K トラックの辞書（13.7 MB）が入らない。
16 MB なら**かなトラックの構成もそのまま焼ける**（`partitions.csv` は 5.06 MB）。
**別々に取りに行くと 2 回焼き直しになる。** 実測に使っているのはユーザーの M5 CoreS3（16 MB / PSRAM 8 MB Quad。D-047）。

### 決着した問いと、そこに残る警告

- **PIE カーネル（P-1）** ✅ `saan_conv1d_i8a` の内積を `ee.vmulas.s8.accx` に。**MAC の 99.40%** を覆い、
  QEMU でも実機でもスカラ実装と **bit 一致**（M-57 / M-58 / M-62、陰性対照つき）。**S5b で weight-stationary 化**して
  dot の 95.1% を覆う（M-90）。⚠️ **残る 0.60% は depthwise で原理的に載らない**（チャネル方向のギャザー）。
  **ESP32-S3 では既定で有効**（D-048）。数え方:

```bash
export PATH="$HOME/.espressif/tools/xtensa-esp-elf/esp-14.2.0_20241119/xtensa-esp-elf/bin:$PATH"
xtensa-esp32s3-elf-gcc -mlongcalls -O2 -std=c99 -Wall -DSAAN_INT8_ACT=1 -DSAAN_PIE=1 -c csrc/saanotts_int8.c -o /tmp/i8.o
xtensa-esp32s3-elf-objdump -d /tmp/i8.o | grep -c "ee\."     # PIE 命令数（S5b 後は 74。フラグ無しなら 0）
```

- **`β`（式7）** ✅ **β=0 で確定、式7 は不要**（M-60 / D-038）。⚠️ 上流（英語）は 6.0（`docs/upstream-sanotts.md`）。
- **int8** ✅ blob 2,249,792 → 643,936 B（**−71.4%**）、fp32 経路に対し held-out 24 文で平均 **25.88 dB**（M-45）。
  ⚠️ **最小 23.27 dB / 9 文が 25 dB 未満**。⚠️ **W8A32 では実行時 RAM は減らない**（flash だけ）。
- **E-1 DNSMOS** ✅ 教師比 **OVRL 0.7969**（v3。M-61 / D-034）。⚠️ **合否に使わず併記プローブに留める**。理由が 4 つある:
  **陽性対照 G6 が FAIL**（ハードクリップ SNR 10.5 dB で 4 スコアとも下がらない）/
  **日本語で高ピッチを罰する**（先行研究で平均 log F0 と r=−0.788。つくよみちゃんは高ピッチ女声）/
  **アクセント誤りに完全に無反応**（人間 4.00→2.16 で DNSMOS 3.82→3.83。**D-4 の代わりにならない**）/
  上流の「金属的アーティファクトは SCOREQ で高・DNSMOS で低」は**再現しなかった**。
  ⚠️ `speechmos` は UTMOS の torch.hub checkout と**同名パッケージで衝突する**（同一プロセスで両方 import できない）。
- **E-2 decoder の教師初期化** ✅ **定義できない**（27/28 は形状が合うが意味が対応せず、`hout (1539,48,1)` は候補ゼロ）。
  代わりに鎖分解した（M-49 / D-033。内訳は上の「ギャップの内訳」）。**E-2b で容量律速でないことも確定**（M-52）。
  **E-2c は中止**（結果がどちらでも打つ手が変わらない。D-036）。
- **アクセント型（D-4）** ✅ v3 は **37/37 で符号一致**（M-59 / D-030）。記号 `[ ] #` だけで足り、A1/A2/A3 は要らない。
  ⚠️ 残るのは (a) **聴いていない** (b) `]` 単独の AUC（教師 0.6526 / 生徒 0.5895）だけペアコントラストと食い違う。
- **優先度を下げたもの**: `λ_Δ/λ_s/λ_T` の探索（初期値のまま目標に届いた）/ 1.4 M z-line（上限を測る必要が薄れた）/
  学習の延長（Stage 2 はまだ下がる余地あり。⚠️ Stage 3 の 160k は無駄で 80k が最適点。M-59）。

## ⚠️ 未知語は誤読ではなく「無音で消える」

`unk.dic` の未知語エントリは**読み・アクセントを一切持たない**（feature が 7 列しかなく
`read` / `pron` / `acc` / `chain` が無い）ため、未知語は `njd_set_pronunciation.c` の規則で
`、`（読点）に置換される。**例外も警告も出ず、語が丸ごと音声から消える。**

⚠️ **「辞書に無い語」と「未知語ノードになる語」は別物**（C-051）。
**枝刈りで落ちた語は未知語にならず、より短い既知語に切り直されて誤読される**:
`上毛`（コーゲ）→ `上`（ジョー）+ `毛`。無音で消えるのは**辞書にも無い語**だけで、
端末で 37 / 5,015 token = **0.74%**（ホストでも 10 件 = 0.20%。M-75）。
**枝刈りの代償（文の 18%）はフォールバックでは 1 mm も減らない。**

✅ 端末側にはフォールバックを入れてある（`jdict_unk_guess`。M-75）。
表層が全部かなら**それ自身が読み**、そうでなければ**1 文字ずつ辞書を引いて繋ぐ**、
アクセントは**平板に逃げる**。37 件中 **13 件**が読める形になった。
⚠️ 残り 24 件は ASCII と、**辞書に単独の見出しが無い漢字**（`勃` `筑`）。
1 文字の見出し語を全部確保しても改善しない。

⚠️ **かつてここに 齟齬 / 蜃気楼 / 氷点下 を実例として挙げていたが、
フル辞書では 3 語とも正しく読まれる**（C-044。`齟齬` → `s o g o`、
`蜃気楼` → `sh i N k i r o o`）。**あれは枝刈り辞書での観測だった。**

**フル辞書で実際に消えるのは外字・幽霊漢字**（彁 / 妛 / 椦）、
**サロゲートペア漢字**（𡈽 / 𩸽）、**歴史的カタカナ**（ヷヸヹ）:

```
彁 → 無音     𩸽 → 無音     ヷヸヹ → 無音
躑躅 → ツツジ  髑髏 → ドクロ  鱲子 → カラスミ   ← フル辞書なら読める
```

実文での未知語率は **held-out 全 2,333 文で 83/38,269 token = 0.22%（文の 3.34%）**、
中身は**ほぼカタカナの外国固有名詞の断片**（`ーヌ` `ーヴァ` `ーリャ`）。
**枝刈りすると激増する**ので、ホスト側 G2P に倒しても入力サニタイズは別途必要。
再現: `uv run python scripts/k1/silent_deletion.py`
