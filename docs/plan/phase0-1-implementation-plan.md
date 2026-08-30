# sanoTTS-jp 実装計画

- 作成 2026-08-26 / 最終更新 2026-08-27（Phase D-3a/b/c 完了時点）
- 対象: `arXiv:2608.21378` の蒸留レシピの日本語適用（PoC・非配布）
- 上位ドキュメント: [`../README.md`](../README.md)（現在地）/ [`../decisions.md`](../decisions.md)（確定事項）/ [`../../CLAUDE.md`](../../CLAUDE.md)
- 本文書の位置づけ: **§0 が現行のロードマップ。§1 以降は検証タスク B-* と各 Phase の詳細。**
  未確定のものは `⚠️ 未検証`、済んだものは `✅` を付ける。反証された主張は採用しない。

---

## 0. 現行ロードマップ

```
✅ Phase 0   教師の動作確定             決定的推論が bit 再現、EMA 適用、6 チェック PASS
✅ B-0〜B-12 検証タスク完走             D-016〜D-031 として凍結（下表）
✅ Phase A   ラベル生成の設計確定         入力は中間表現 / prosody は zeros（D-014）
✅ Phase B   ラベル一括生成              train 20,790 / heldout 2,314 発話。SHA-256 固定（M-35）
✅ Phase C   本学習                     v2 で SCOREQ 教師比 0.611（論文の英語比 0.5427 超）
✅ Phase 5   β スイープ                 β=0 と 2 が候補（M-40）→ **聴取で β=0 に確定**（M-60 / D-038）
✅ Phase 6   int8 量子化                blob 624,692 B（論文 679,832 B の −8.1%）（M-39）
✅ Phase D-1 C99 コア + golden test      Pearson 1.000000 / SNR 117.5 dB（M-41）
✅ Phase D-2 ストリーミング化            1,258 KB → **196.9 KB**。一括版と bit 一致（M-42）
✅ Phase D-3a FFT 化                    naive の **1,435 倍** / SNR 138.7 dB（M-43）
✅ Phase D-3b レイテンシ初測定           手元 **0.023× RT**。iSTFT 88% → 0.9%（M-43）
✅ Phase D-3c int8 カーネル（C 版）      逆量子化と 100 dB 超で一致。**PIE は M-57 で実装済み**
✅ Phase D-3c'-1/2 int8 end-to-end      fp32 比 平均 25.88 dB。ブロブ −71.4%。RAM は不変（M-45）
✅ Phase D-3c'-4 ESP-IDF 雛形            ⚠️ **一度もビルドしていない**（M-46。toolchain は M-54 で導入済み）
✅ D-4      アクセント型ミニマルペア       符号一致 35/36（v2）→ **v3 では 37/37**（M-44 / M-59）
✅ B-12     教師の事前学習との重複検査      **看板の 24 文は汚染ゼロ**。陽性対照 200/200（M-47）
✅ 敵対的検証 空虚に通るゲート 2 件 + silent failure 1 件を修正（M-48）
✅ E-1      DNSMOS                     生徒/教師 0.7727（v2）→ **v3 は 0.7969**。上流の主張は再現せず（M-50 / D-034 / M-61）
                                        ⚠️ **陽性対照 G6 は FAIL**。下がらない＝劣化が無い、ではない
✅ E-2      decoder の教師初期化          **定義できない**。代わりにギャップを分解（M-49 / D-033）
                                        decoder 0.395 / acoustic 0.283 / duration 0.052
✅ E-2b     `Gγ` の幅スイープ           **容量律速ではない**。params +43% で Δ +0.0006（M-52）
                                        効くのは学習量: 20k→40k で −0.1090（床 0.0812 超え）
✅ ライセンス再調査                       一次ソースで**記録 3 件を訂正**。継承の障害は最初から無かった
                                        （D-035 / C-029〜C-031）
✅ ESP-IDF v5.5 導入                     C99 コア 5 ファイルが厳密 `-std=c99` で S3 向けに通過
                                        ⚠️ `M_PI` の移植性バグを 1 件発見（M-54 / C-033）
⏹  中止     E-2c W=56 が無料か           結果がどちらでも打つ手が変わらない（D-036）
✅ P-1      W8A8 + PIE カーネル        **実装完了**。QEMU で bit 完全一致（MAC の **99.40%**。M-57 / M-58）
                                        ⚠️ **速度は未測定**（QEMU はサイクル精度でない）
                                        ⚠️ **出荷ファームでは未有効化** — W8A8 を採る決定が要る
✅ P-2      `β` の聴取決定              **β=0 で確定。式7 は不要**（M-60 / D-038）。⚠️ 聴取者 1 名
✅ v0.1.0   モデルの公開                 重み・int8 ブロブ・golden・サンプルを配布（D-039）
                                        ⚠️ 公開前の検算で **v3 の品質値 3 件を訂正**（M-61 / C-038）
▶  今        Phase D-3d 実機             ⚠️ **ESP32-S3 ボード待ち**（これが唯一の本物の待ち）
```

⚠️ **公式実装 `Ampixa/sanoTTS` が実在すると判明した**（C-024。それまで「404」と誤記録）。
GPL-3.0 なので**ソースは読まない**が、公開ドキュメントの実測値は
[`../upstream-sanotts.md`](../upstream-sanotts.md) に集約した。
上流は ESP32-S3 で **0.22× RT を実測**と申告しており、
**「fp32 では届かない / int8 + PIE が必須」といううちの結論と整合する。**

**プロジェクトの主目的（ESP32 で日本語 TTS が動く）に対する現在地**:

| 軸 | 状態 |
|---|---|
| 品質 | ✅ SCOREQ 教師比 **0.6444** / DNSMOS 教師比 **0.7969** / かな CER は教師と有意差なし（差 +0.0320、p=0.31）/ アクセント **37/37**（すべて v3。**M-61 で訂正**）。⚠️ n=24 / 聴取は 1 名 |
| メモリ | ✅ **196.9 KB / SRAM 512 KB の 38%**（stack 込み、M-42 / M-43） |
| **速度** | ⚠️ **ここだけ未達。** fp32 のまま移植すると **2.47× RT**。PIE カーネルは書けて QEMU で bit 一致まで確認したが（M-57 / M-58）、**速度は一度も測っていない**（QEMU はサイクル精度でない）。**実機待ち** |

⚠️ **速度が唯一の未達項目。** 実測 η_host=0.364 を転移すると移植可能 C の fp32 は
**2.47× RT で実時間に間に合わない**（η=1 の下限 0.93 だけ見ると「あと少し」と誤読する）。
論文の 0.22× RT も **fp32 では達成不可能**で、**int8 + PIE が必須**だと検算できた。

### この節までに凍結した設計値（すべて実測。詳細は `../measurements.md`）

| 項目 | 値 | 決定 |
|---|---|---|
| デプロイ語彙 | **57**（論文の英語は 157）。合計 **559,008 params** | D-016 |
| 長さフィルタ | `max_spec_length = 700`（4.31% 除外）。ID 長は実質効かない | D-017 |
| `_` PAD | **フレームの 53.76% を占める**。特別扱いしない | D-018 |
| `s_v` | **1.2187**（`ceil` vs `round+clip` の規約差の吸収） | D-019 |
| 評価の主指標 | **SCOREQ synthetic/nr** + UTMOS 併記。`natural` は使わない | D-020 |
| DNSMOS | **合否ゲートにせず併記プローブ**（陽性対照 G6 が FAIL するため） | D-034 |
| ギャップの帰属 | decoder 0.395 / acoustic 0.283 / duration 0.052。⚠️ **定義依存** | D-033 |
| λ | `λ_n`/`λ₂` は閉形式で実行時算出。`λ_Δ=0.86` / `λ_s=0.19` / `λ_T=0.27` | D-021 |
| iSTFT | `center=True` + `length = T*256` | D-022 |
| `yT` | EMA 適用版 | D-023 |
| コーパス | 教師 FT テキスト 102 uid を除外。疑問 EOS 47 行を追加 | D-024 / D-016 |
| 判別器 | `scales=3, base_channels=16` = 94,755 params | D-025 |
| 平坦度プローブ | `n_fft=1024 / hop=256 / guard=0 / power=1` | M-27 |
| 実行環境 | **手元の M4 Max**（ラベル生成 CPU / 学習 MPS）。vast.ai は不要だった | D-027 |
| `Eρ` の扱い | Stage 2 で**凍結**、Stage 3 で decoder と一緒に学習 | D-028 |
| ストリーミング | ステート保持 / CHUNK=8。**196.9 KB で一括版と bit 一致** | D-029 |
| アクセント | 記号 `[ ] #` のみ。**A1/A2/A3 は足さない**（符号一致 35/36） | D-030 |
| ESP32 の配置 | 重みは flash の `model` パーティション、arena は **208 KB を静的確保** | D-031 |
| CER | **かな CER**（参照・仮説の両方を読みに落とす）。表記 CER は符号が逆転する | C-023 |
| 重みの受け渡し | 自己記述形式 **SAAN v1**（名前・shape・dtype・offset をヘッダに持つ） | M-41 |

### Phase A: ラベル生成の設計を固める ✅ 決着（D-014）

**生徒が学ぶ入力と、デバイスが実際に作る入力を一致させる**。詳細は
[`phase-a-decisions.md`](phase-a-decisions.md)。

```
漢字文 ──[ホスト・OpenJTalk]──▶ 中間表現 ──[kana_g2p 1,786 B]──▶ 音素ID ──▶ 教師
                                              ↑ デバイスと同じ変換器
```

| # | 決定 | 根拠 |
|---|---|---|
| **A-1** | **入力は中間表現に統一** | 漢字経路との不一致 4 件はすべて漢字経路側のバグ（`埼玉県秩父市` が中国語音素になる、M-19）。**B-1 が構造的に消えた** |
| **A-2** | **prosody は zeros** | 実 prosody 1.730 vs zeros 1.740、**有意差なし** (p=0.72)。デバイスは prosody を供給できないので条件を揃える（M-20） |
| **A-3** | パック形式 fp16 + int16、13 ゲート | σ がチャネル間で 241.8 倍偏る → `μ_T`/`σ_T` を同梱（M-20） |

### Phase B: ラベル一括生成 ✅ 完了（M-35）

**手元の CPU で 47.2 分**（135 ms/文）。vast.ai は使わなかった（D-027）。

| split | 採用 | 棄却 | サイズ |
|---|---:|---:|---:|
| train | **20,790** | 103 | 5.5 GB |
| heldout | **2,314** | 9 | 627 MB |

`SHA256SUMS` の **330 ファイル全件一致**（D-015 の固定完了）。パック全体ゲート G13 は 0 件。

⚠️ **A-3 の見積もり 4.42 GB に対し実測 5.5 GB（+24% 過小）**。C-014 でサンプルの
偏りを直したはずだがまだ足りなかった。原因は未特定。

⚠️ **ラベル生成は CPU で行う。MPS を使わない**（CPU と bit 一致しない、M-21）。
CPU 生成は M-15 で piper-plus venv と bit 完全一致が確認済み。
hook が `data/pack` の破棄と再生成を deny する。

### Phase C: 生徒の実装と本学習 ✅ 完了（M-36 / M-37）

**手元の M4 Max（MPS）で 2 回回した。** vast.ai は使わなかった（D-027）。

| run | step 配分 | 所要 | SCOREQ 教師比 |
|---|---|---:|---:|
| v1 | 各 5,000 | 15 分 | 0.387 |
| **v2** | S1 20k / S2 60k / S3 40k / S4 60k | 2.7 時間 | **0.611** [0.568, 0.658] |

**v2 が論文の英語 embedded の教師比 0.5427 を上回った**（CI が 0.5427 を含まない）。

#### step 配分の知見（M-37）

| Stage | step | epoch | 収束 |
|---|---:|---:|---|
| 1 Duration | 20,000 | 7.7 | val 0.0322 → **0.0237** |
| 2 Acoustic | 60,000 | 2.9 | val 0.660 → **0.430**。まだ下がる余地あり |
| 3 Decoder | 40,000 | 15.4 | **20,000 step で SNR +8.91 dB に飽和**。40,000 は半分無駄 |
| 4 Joint | 60,000 | 2.9 | val 0.158 → **0.152** |

判別器の損失は全区間 0.47〜0.49 で一定 = **LSGAN は均衡**（発散も崩壊もなし）。

⚠️ Stage 4 終盤で train 0.126 / val 0.140 の乖離。次の run では過学習を監視する。

#### 設計判断が実データで裏付けられた

| 判断 | 予測 | 実測 |
|---|---|---|
| **λ₂ は残差から実行時算出**（D-021） | 学習が進むほど**上がる** | 0.644 → **2.537**。固定値を焼いていたら後半で L2 が効かなくなっていた |
| **`Eρ` は Stage 2 で凍結**（D-028） | 自明解を避ける | 全記録で **`c_rank` = 40/40** |

⚠️ **1.4 M z-line は作っていない。** 「上限を測ってから 567 K を判断する」計画だったが、
**567 K が先に目標へ届いた**ので必要性が薄れた。やるなら差分の確認として。

⚠️ **学習曲線の 3 水準（512 / 5,000 / 20,946 行）も未実施。**
論文が埋めていないデータ量の飽和点は測れていない。

### Phase D: C99 コア + ESP32 実機

参照実装が 404 なので**推論コアは自前で書く**。

#### D-1 ✅ 完了: C99 コア + ゴールデンテスト（M-41）

`csrc/saanotts.{h,c}` / `csrc/golden_test.c`。**依存は libm だけ**、
malloc をコアで呼ばず arena を渡す。重みは自己記述形式 SAAN v1 で読む。

| 項目 | Pearson | SNR |
|---|---:|---:|
| `d_hat`（整数） | **53/53 完全一致** | — |
| `log_d` / `c` / **`pcm`** | 1.000000 | 125.9 / 131.4 / **117.5 dB** |

**受け入れ条件（Pearson ≥ 0.98）を大きく超えた。** `cc -Wall -Wextra` で警告 0。

⚠️ `d_hat` の完全一致は運任せの部分がある。C の `roundf` は half-away-from-zero、
`torch.round` は **half-to-even**。ちょうど .5 のときだけ割れる。入力を変えたら再確認。

#### D-2 ✅ 完了: ストリーミング化（M-42）

**一括版 1,258 KB → 196.9 KB（−84%）。ESP32-S3 の SRAM 512 KB に対し 38%。**
G1〜G4 すべて達成（`make -C csrc stream` で判定）。
⚠️ **数値は D-3 統合後のもの**（FFT の stack 4.2 KB を含め、作業領域を実寸に詰めた）。

| # | 条件 | 結果 |
|---|---|---|
| G1 | ピーク < 200 KB | ✅ **196.9 KB**（実用最大 350 ids、FFT stack 込み） |
| G2 | 一括版と bit 完全一致 | ✅ 27,136 sample すべて |
| G3 | RAM が O(1) | ✅ ids 比例分を除いて 194.3 KB で一定 |
| G4 | golden test 継続 | ✅ Pearson 1.000000 |

⚠️ **`tok_h` も O(1) にする必要があった。** 発話全体で持つと 192 B/id で、
350 ids のとき 243 KB になり G1 を満たさなかった。token block（受容野 ±12 トークン）も
チャンク化して解決。

⚠️ **実装で 4 つの罠を踏んだ**（すべて「動くが値が違う」）。詳細は M-42。
中心は「**conv が時刻を混ぜる箇所すべてで、発話外をゼロにしないと一括版と一致しない**」。

#### D-2 の設計と実績（記録）

**着手前の見立てと、実際にどうなったか**:

| 項目 | 着手前 | 実測（M-42） |
|---|---:|---:|
| ピーク RAM | 1,258 KB（SRAM の 246%） | **196.9 KB（38%）** |
| mag + cos + sin | 637 KB | o1539 として 49 KB（CH=8 ぶん） |
| iSTFT の acc + wsq | 218 KB | 12 KB（リング 1,536 sample） |
| pcm | 106 KB | 0（呼び出し側に渡す） |
| **予測の精度** | 「97 KB になる」と見積もった | **実際は 197 KB。2 倍外した** |
| ⚠️ 見落とし | arena しか数えていなかった | **FFT の stack 4.2 KB が計測外**だった（D-3a の照合で発覚） |

⚠️ **見積もりを 2 倍外した。** 段ごとの作業領域（`w_e` 19 KB、`o1539` 49 KB、
`cdel` 13 KB など）を数え落としていた。**メモリ見積もりは実測でしか当たらない**
という C-009 / C-014 / M-35 と同じ教訓がまた出た。

**方式（D-029）**: ステート保持型。各段が入力を `[C][2*pad + CHUNK]` に溜め、
**バッファ全体に conv を掛けて中央 CHUNK フレームだけ**を下流へ渡す。
中央の計算に必要な入力は全部バッファ内にあるので、**カーネルを一括版と共有したまま
bit 一致する**。ハロー再計算方式は同じメモリで計算 10 倍になるので採らなかった。

| 段 | pad | 由来 |
|---|---:|---|
| AcBlock × 5 | 4 | c1 k=5 (±2) + c2 k=5 (±2) |
| decoder inp | 1 | k=3 |
| dw ブロック × 5 | 3 | k=7 |
| token block（±12 トークン） | — | `tok_h` を O(1) にするため後から追加 |

⚠️ **受容野は実測で確定した。** 旧記述の「acoustic ±10」は `c1` だけ数えた誤りで、
正しくは **±20**。しかも**閾値 1e-6 で測ると decoder が ±13 に見える**（端の寄与が
小さいだけ）。**閾値 0 で測ること。**

⚠️ **`tok_h` も O(1) にする必要があった。** 発話全体で持つと 192 B/id で、
350 ids のとき 243 KB になり G1 を満たさなかった。**着手時点では見落としていた。**

⚠️ **代償はレイテンシ 38 フレーム = 0.44 秒**（パイプライン 36 + iSTFT 2）。
対話用途では効く。

**完了条件（D-029）と結果**:

| # | 条件 | 結果 |
|---|---|---|
| G1 | ピーク RAM < 200 KB | ✅ **196.9 KB**（実用最大 350 ids = D-017 の上限、stack 込み） |
| G2 | 一括版と **bit 完全一致**（`memcmp`） | ✅ **27,136 sample すべて** |
| G3 | 発話長に対して RAM が **O(1)** | ✅ ids 比例分（8 B/id）を除いて 194.3 KB で一定 |
| G4 | golden test が通り続ける | ✅ Pearson 1.000000 |

⚠️ **G1 の測定を一度甘く出した。** テスト文（53 ids）だけで 185 KB を見て OK に
していたが、実用最大長では 243 KB あった。**メモリは実用最大長で測る。**

**踏んだ 4 つの罠**（すべて「動くが値が違う」。詳細は M-42）:

1. 段の出力の**発話外に bias 由来の非ゼロ**が残る（一括版は配列外＝ゼロ）
2. **ブロック内部の c1→c2 の間**も同じ問題
3. **iSTFT リングの衝突** — `out_pos` を N/2 から始めると `[0,512)` が pop されない
4. **時刻が負のフレームも iSTFT に push**していた

原則: **conv が時刻を混ぜる箇所すべてで発話外をゼロにする**（1x1 の後は不要）。

⚠️ 切り分けで **c-line を PyTorch の golden と比べて 7e-07 に悩んだが、
それは一括版 vs PyTorch の差と同値**だった。**ストリーミングの検証は一括版と比べる。**

#### D-3 の結果（a/b/c/c' 完了、**残りは d = 実機のみ**）

| # | 作業 | 結果 |
|---|---|---|
| **D-3a** | FFT 化 | ✅ **1,435 倍** / SNR 138.7 dB / テーブル 6,144 B。golden・G1〜G3 維持 |
| **D-3b** | レイテンシ | ✅ 手元 **0.022× RT**（無駄チャンク 48 も削除）。iSTFT 88% → **0.9%**、いまは **decoder が 60%** |
| **D-3c** | int8 カーネル | ✅ 逆量子化と 100 dB 超で一致。⚠️ **移植可能 C なので手元では速くならない** |
| **D-3c'** | **PIE 最適化** | ✅ **完了**（M-57 / M-58）。`ee.vmulas.s8.accx` のアセンブリで **MAC の 99.40%**、QEMU で **bit 完全一致**。⚠️ **速度は未測定**／**出荷ファームでは未有効化** |
| **D-3d** | 実機 | ⚠️ ハードウェア待ち |

**ESP32-S3 への外挿（⚠️ 実機は未実行）**:

| 実装 | 合計 × RT |
|---|---:|
| **移植可能 C / fp32（η=1 の下限）** | **0.93** |
| **同（実測 η_host=0.364 を転移）** | **2.47** ← **実時間に間に合わない** |
| esp-dsp 級 asm / fp32 | 0.31 |
| esp-dsp 級 PIE / int16 | 0.088 |
| **esp-dsp 級 PIE / int8** | **0.032** |

**論文の 0.22× RT は fp32 では達成不可能**（必要 η が 144%）。
int8 なら η 14% で足りる。**論文が int8 blob を配っていることと整合する。**

⚠️ **η=1 の 0.93 だけ見ると「あと少し」と誤読する。** 実測 η を転移した 2.47 が実態。

##### D-3c' の計画（残りの本体）

**目的: ESP32-S3 で実時間に間に合わせる。** 現状 2.47× RT → **目標 < 0.5× RT**
（論文の 0.22 に並ぶ必要はない。実用上は音声より速く作れれば足りる）。

**⚠️ 手元では速度を検証できない。** Apple の SIMD は fp32 向きで、int8 にしても
速くならない（実測 0.86〜1.01 倍）。**PIE の効果は ESP32 実機でしか測れない。**
したがって D-3c' は「**書けるところまで書き、正しさだけ手元で保証する**」作業になる。

| # | 作業 | 手元で検証できること | できないこと |
|---|---|---|---|
| ~~**c'-1**~~ | ~~`duration.proj.weight` を int8 経路に載せる~~ **完了 (M-45)** | fp32 で bit 一致（125.91/131.37/117.50 dB が桁まで不変） | — |
| ~~**c'-2**~~ | ~~int8 経路の end-to-end 統合（W8A32）~~ **完了 (M-45)** | **主ゲート**: fake-quant golden と pcm **115.91 dB**／波形 SNR 24 文平均 **25.88 dB** 最小 **23.27 dB** | 速度 |
| **c'-3** | PIE intrinsic 版のカーネル | **正しさのみ**（C 版と bit 一致 or 高 SNR） | ⚠️ **速度は実機待ち** |
| **c'-4** | ESP-IDF プロジェクト雛形 | ✅ CMake 構文 / ホスト stub ビルドで **C コアと bit 一致** / コアにホスト専用 API が無いこと / arena の実測（M-46） | ⚠️ **当時の記述**。`idf.py build` は M-54 で通り、QEMU 完走は M-62。⚠️ **実 SRAM・実 xRT・I2S 実レートは今も未検証** |

⚠️ **c'-3 は「動くか分からないコードを書く」ことになる。** このプロジェクトの
実測主義に反するので、**実機が手に入るまで着手しない**という判断もありうる。
c'-1 と c'-2 は手元で完結するので先にやる。

**c'-1 の詳細**（D-3c の照合で判明 → M-45 で解消）: `duration.proj.weight` は 52 個の int8
テンソルの 1 つなのに `saan_conv1d` を通らず `saanotts.c` の**インライン内積**で
使われていた。`nn.Conv1d(32,1,1)` なので conv 化するだけで意味論が一致する。
**先に fp32 のまま置換して積和順序が変わらないことを単独で確かめた**（先に int8 化すると切り分け不能）。

**c'-2 のゲート文言を実測に合わせて訂正した（M-45）**:

- ⚠️ **「波形 SNR ≥ 25 dB」は 1 文ごとの判定としては達成できない。** 量子化そのものの
  性質で、正しい実装でも held-out **24 文中 9 文**が 23.27〜24.99 dB に入る。
  判定は「**24 文の平均 ≥ 25 dB かつ最小 ≥ 23 dB**」（`csrc/int8_e2e_test.c` に凍結）
- **主ゲートは 25 dB ではない。** c'-2 が本当に制御しているのは
  「**C の int8 経路が PyTorch の fake-quant と一致するか**」。
  `make -C csrc int8-golden` で **pcm ≥ 100 dB**（実測 115.91 dB）を見る。
  ⚠️ この相手（`csrc/golden_i8.bin`）は**この作業まで存在しなかった** —
  `--int8` を付けても golden は fp32 参照のままで、層を 1 つ fp32 に置き忘れても
  量子化誤差 26 dB に紛れて検出できなかった
- ⚠️ **d̂ は fp32 側に固定して測る。** 固定しないと held-out 24 文中 15 文で
  フレーム数が変わり、波形 SNR がそもそも定義できない

**W8A32 と W8A8 の選択**（D-3c / 照合で決着）:
- **既定は W8A32**（重みだけ int8 / activation は fp32）。
  M-45 で `SAAN_INT8_ACT`（既定 0）というコンパイル時スイッチにした（散文だけだったのを実体化）
- 理由は「W8A8 が危険だから」**ではない** — end-to-end の差は 2.59 dB で
  層ごとの差と同じ（**30 層直列でも蓄積しない**、照合で実測）
- 理由は「**flash が 1 バイトも減らず**（重みのビット幅だけで決まる）、
  速度利得もホストでは 0.86 倍しかない」から。実機で足りなければ切り替える
- **M-45 で end-to-end の代償も実測**: W8A8 は波形 SNR 平均 25.88 → **23.24 dB**、
  さらに activation の作業領域 4.2 KB で **G1 が 196.9 → 201.1 KB** と 200 KB を超える。
  ⚠️ W8A8 の正しさは現在の fake-quant golden では検証できない（golden の activation は fp32）

#### D-3 の当初計画（記録）

**目的: ESP32-S3 で実時間合成が成立するかを判定する。**
品質（M-37）とメモリ（M-42）は決着したので、**残るのは速度だけ**。

**順序が重要**。まとめて変えると切り分けができない。

| # | 作業 | 完了条件 | 状態 |
|---|---|---|---|
| **D-3a** | `irfft_1024` を radix-2 FFT に | naive DFT との SNR ≥ 120 dB / golden test の Pearson ≥ 0.98 / ストリーミングの G1・G3・G4 維持 | ▶ 次 |
| **D-3b** | レイテンシ測定 | 手元で「音声 1 秒あたり何秒」を段別に測り、**ESP32 への外挿の根拠を明示する** | D-3a の後 |
| **D-3c** | int8 カーネル | fp32 版と波形 SNR ≥ 25 dB（M-39 の PTQ 実測と同水準）。blob 624,692 B | D-3b の後 |
| **D-3d** | 実機 | RAM ピークとレイテンシの実測 | ⚠️ **ハードウェア待ち** |

⚠️ **D-3a で bit 一致は要求できない。** FFT と naive DFT は積和の順序が違うので
必ずずれる。**ここだけは SNR で判定する**（G2 を bit 一致にしたのと理由が逆）。

⚠️ **レイテンシの外挿には根拠が要る。** M-18 の「RTX 4090 ×10 と仮定」のような
係数を推測で置かない。ESP32-S3 の実クロック・SIMD 幅・メモリ帯域から積み、
**外挿は外挿と明記する**（M-16 と同じ方法）。

##### 実装の割り当て（競合しないように別ファイルにする）

| 成果物 | 内容 |
|---|---|
| `csrc/fft.{h,c}` | radix-2 実 FFT。**既存ファイルを触らない**（統合は後） |
| `csrc/bench.c` | レイテンシ測定ハーネス。段別の内訳を出す |
| `csrc/saanotts_int8.{h,c}` | int8 カーネル。fp32 版と並存させて切り替えられるようにする |
| `reports/d3_esp32_model.json` | 外挿の根拠（クロック / SIMD / 帯域） |

⚠️ **naive DFT は消さない。** FFT の検証基準として残す
（`SAAN_USE_NAIVE_DFT` で切り替え）。

### 主要リスク

| リスク | 影響 | 状態 |
|---|---|---|
| ~~集約指標が金属的アーティファクトを検出できるか~~ | ~~未測定（DNSMOS を測っていない）~~ | ✅ **測った。上流の主張は再現しなかった**（M-50 / D-034）。⚠️ **陽性対照 G6 は FAIL** — DNSMOS が下がらないことを「劣化が無い」と読まない |
| ~~decoder をゼロから学習していること~~ | ~~未検証。上流の主張と噛み合わない~~ | ✅ **決着**（M-49 / D-033 / M-52）。`Gγ` は**容量律速ではない**（params +43% で Δ +0.0006）。効くのは学習量で、Stage 3 を 80k にして教師比 **0.6444**（M-61） |
| **ESP32 で実時間に間に合うか** | **残る唯一の成否要因** | ⚠️ 手元 0.023× RT だが、**fp32 のまま移植すると 2.47× RT**（実測 η_host=0.364 を転移）。**int8 + PIE が必須**（D-3c'） |
| **PIE の効果を手元で検証できない** | 書いても正しさしか確かめられない | ⚠️ **正しさは QEMU で確認できた**（bit 完全一致。M-56 / M-57）。**速度は依然として測れない** — QEMU はサイクル精度でない。**実機待ち**（D-3d） |
| **実機で測れていない** | 解析と実機は違う | ⚠️ ハードウェア待ち（D-3d）。RAM は解析で 196.9 KB / SRAM 512 KB |
| ~~int8 カーネルが未実装~~ | ~~blob は 625 KB だが C は fp32 で読んでいる~~ | ✅ **実装済み**（M-45）。flash 2,249,792 → **643,936 B**（−71.4%）。⚠️ **実行時 RAM は減らない**（W8A32 なので flash だけ） |
| **UTMOS と SCOREQ が生徒の劣化で食い違う** | 指標の読み方を誤ると判断を誤る | ⚠️ v1 で UTMOS 比 0.700 / SCOREQ 比 0.387 と乖離した。**主指標は SCOREQ**（D-020） |
| ~~`β` が聴取で決まっていない~~ | ~~式7 を入れるか外すか~~ | ✅ **β=0 で確定。式7 は不要**（M-60 / D-038）。C コアに `β` は最初から無いので**実装への影響はゼロ**。⚠️ 聴取者 1 名 |
| ~~アクセント型の再現性が未測定~~ | ~~日本語 TTS の核~~ | ✅ ミニマルペア 15 群で教師との符号一致 **37/37**（M-44 / M-59）。⚠️ **chance は 0.5 ではない**（経験的ヌル 0.614）。⚠️ 起伏の大きさ（`magnitude_ratio` 1.193）は **3 試行しか聴いておらず**「同じくらい」（M-60）。**検出率の 95% 上限 56.2% = 弱い** |
| ~~ESP32 の RAM に載らない~~ | ~~プロジェクトの成否に直結~~ | ✅ **196.9 KB / SRAM 512 KB の 38%**（stack 込み）。一括版と bit 一致 |
| ~~567 K で日本語の品質が届くか~~ | ~~成果物の価値に直結~~ | ✅ v2 で SCOREQ 教師比 **0.611**、論文の英語比 0.5427 を上回った（M-37）。⚠️ n=24 |
| ~~C99 コアの実装量~~ | ~~参照実装が無い~~ | ✅ golden test 通過（M-41）。ストリーミング版込みで約 900 行 |
| ~~`λ` 群のチューニング~~ | ~~論文に値が無い~~ | ✅ 勾配整合で初期値を決めた（D-021）。`λ_n`/`λ₂` は実行時算出で探索不要 |
| ~~SCOREQ が未導入~~ | ~~論文の主指標が測れない~~ | ✅ PyPI にあった（C-016） |

---

## 1. 現在地と確定事項

### 1.1 一言で

> **⚠️ この計画書は B-0 より前に書かれた部分を多く含む。** その後
> スコープ（D-007）・入力仕様（D-010 / D-011）・環境（D-012）が確定し、
> 辞書枝刈りは実装対象から外れた。**現在地の要約は
> [`../README.md`](../README.md)、確定事項は [`../decisions.md`](../decisions.md) が正。**
> 本書は B-1 以降の検証タスクと Phase 2〜6 の見取り図として使う。

**教師は完全に手元にあり、決定的推論も再現できている。入力仕様も確定した。
残るブロッカーは「テキスト → 音素 + 韻律」の canonical 経路にある 2 つの無警告な欠陥。**

- 教師 ckpt は DL 済み・ロード済み・bit 決定的推論を実測済み（Phase 0 完了）
- 入力は**ひらがな + アクセント記号 + 無声化マーク**に確定。端末側 G2P は 877 B（C-042）
  （D-010 / D-011、`scripts/kana_g2p.py`）
- ESP32 のメモリは I2S 逐次出力で約 96 KB。**中止材料は無い**（M-16）
- 一方で `MultilingualPhonemizer` → `PiperEncoder` には
  **日本語文が無警告で中国語音素になる欠陥**（B-1）と
  **韻律特徴が無警告でズレる欠陥**（B-2）が実在する。
  Phase 1 のラベル一括生成に入る前に、この 2 つを塞ぐのが最優先

> **B-1 は入力仕様の変更で消えている可能性がある。** 中間表現の生成は
> ホスト側でオフラインに行うので、そこがどの経路を通るかを先に確認すること。

### 1.2 実測で確定した事項（採用してよい）

| 項目 | 確定値 | 根拠 |
|---|---|---|
| piper-plus の git ref | **HEAD (`0f3b1a62`, dev) をそのまま使う。checkout も worktree も不要** | 学習時コード `95e74cb2` と HEAD の `vits/` 差分は推論数値に無影響（`mb_istft.py` は完全同一） |
| `v1.13.0` への checkout | **してはいけない** | ckpt は post-FiLM（`dec.cond.weight (512,512,1)` + `cond_layers`）。v1.13.0 では size mismatch |
| `import piper_train` の解決先 | **`uv run` を使う**（D-012 で解決）。uv の独立 venv には stale なコピーが存在しない | 旧: `PYTHONPATH` を先頭に置く必要があった。`.venv` の `site-packages/piper_train/` は v1.13.0 相当の stale コピー |
| 教師 ckpt | `~/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/c3f236e068b95356b871842b4ae7cec2a86c50ea/epoch=499-step=22000.ckpt` (927,048,022 B) | DL 完了済み |
| `hyper_parameters` | `num_symbols=173` / `num_speakers=1` / `num_languages=6` / `inter_channels=192` / `gin_channels=512` / `prosody_dim=16` / `sample_rate=22050` / `hop_length=256` / `use_sdp=True` / **`freeze_dp=True`** / **`max_phoneme_ids=400`** / **`max_spec_length=700`** | `torch.load` 実測 |
| 話者埋め込み | **`speaker_embeddings=None` で呼ぶ。`spk_tsukuyomi.npy` は渡しても bit 完全に無視される** | `spk_proj` / `emb_g` が state_dict に 0 件。None / npy / ランダム 192次元 の 3 通りで `audio`/`z`/`durations` が bit 一致 |
| 言語条件 | **`lid=0` (ja) 固定は必須。lid は焼き込まれていない** | `g = lang_emb` が enc_p / dp / flow / dec 全部に渡る。lid=1 で総フレーム 115→106、z も別物 |
| ロード | `VitsModel.load_from_checkpoint(ckpt, dataset=None, strict=False)` → missing 0 / unexpected 0 / `cond_migrated` 0 | 実測 |
| **EMA** | `ema_generator_state` (decay 0.9995, num_updates 11000, shadow 53 params) は **`load_from_checkpoint` では適用されない**。`apply_ema_shadow_params(model_g.dec, ...)` を **`remove_weight_norm()` の前に**明示的に呼ぶ | 適用有無で `yT` の SNR は **14.5 dB** しかない（12.53 dB は n=1 の退化入力、C-017）。`zT` / `dT` は bit 一致。**適用件数を assert すること**（D-023） |
| 決定性 | `noise_scale=0` で `z_p == m_p` bit 一致、2 回実行で `audio`/`z` bit 一致 | 実測 |
| フレーム整合 | **`ceil(dT).sum() == zT.shape[1] == len(yT)/256`** がラベルパック 8 件すべてで厳密一致。`attn.argmax(-1)` も ceil 累積和と完全一致 | 実測。`src/python_run/piper_plus/timing.py` は ceil しないので**使わない** |
| 音素表 | ckpt 同梱 `config.json` の `phoneme_id_map` (185 entry) を使う。**有効 id は 0..172**。日本語在庫 65 エントリは全部 id ≤ 64 で安全。id 173..184 の 12 個は非日本語 (ɧ ɵ ʏ + 韓国語 PUA) | 実測 |
| 前処理タイプ | `config.json` の **`phoneme_type = "multilingual"`** / `language.code = "ja-en-zh-es-fr-pt"` / `language_id_map = {ja:0, en:1, zh:2, es:3, fr:4, pt:5}` | 実測（本計画作成時に再確認） |
| PUA の canonical ソース | **`piper_plus_g2p.encode.pua` の `CHAR2TOKEN` / `TOKEN2CHAR` (99 entry)**。`PiperEncoder` が内部で呼ぶ | `src/python/jp_phoneme_map.py` は id 表が壊れている（後述） |
| コーパス素材 | JSUT 7,696 + ROHAN4600 4,600 + ITA 424 + Common Voice ja 11,060 = 生 **23,780** 行 → NFKC 重複排除後 **23,271** ユニーク | 実測。論文の 14,343 行の 1.62 倍 |
| 発話長 | 教師実測で **平均 3.96 s/文**、実効 7.73 mora/s、frames/s = 86.13 | 無作為 250 行の決定的推論 |
| 生徒パラメータ逆算 | `Eρ = 192→64→40` の pointwise 2 層 = **14,952（代数的に一意）**。`Aβ` 非埋め込み部 = **192,000 ちょうど**、埋め込み次元 **48**（「30 エントリで +1,440」から直接） | 実測・全探索 |

### 1.3 CLAUDE.md / feasibility.md の要訂正箇所（Phase 0 の成果物）

> **✅ この表の訂正はすべて反映済み**（2026-08-26）。
> `docs/decisions.md` の C-001〜C-009 として恒久化してある。以下は経緯の記録。

| 場所 | 現状の記述 | 訂正内容 |
|---|---|---|
| `CLAUDE.md:59` / `:175-176`, feasibility `§2.4` | `speaker_embeddings=spk_emb` / 「話者を固定するのにそのまま使える」 | **誤り。`None` を渡す。npy は SECS 評価専用** |
| `CLAUDE.md:71-73`, feasibility `§2.4` | 「`v1.13.0` に checkout する」 | **不要。HEAD でそのまま載る**（さらに `#616/#621` で pre-FiLM 自動移行も入った） |
| `CLAUDE.md:210-213`, feasibility `§3.5` 付近 | 「必ず `src/python/jp_phoneme_map.py` の `PHONEME_TO_PUA` を経由すること」 | **誤り。canonical は `piper_plus_g2p.encode.pua`。`jp_phoneme_map.get_phoneme_id_map()` は 58 entry / max id 57 しか返さず、実パックの id（最大 87）と全く対応しない — 誤用すると音素ラベルが黙って総取り違えになる** |
| `CLAUDE.md` 「UTMOS」節 / feasibility `§3.7` | 「UTMOS は日本語データで訓練されている」 | **誤り。VoiceMOS2022 main=BVCC(英語) / OOD=BC2019(中国語)。日本語は含まれない。UTMOS も教師比で報告する** |
| feasibility `§2.4` | 「公開 ONNX の入力は `speaker_embedding[B,256]` で v2.0 の 192 チェックに抵触」 | **別モデルの話。ローカル `models/tsukuyomi.onnx` は 173 音素・pre-FiLM の旧系統で、その 256→512 射影は xavier 初期化のまま学習されていない死んだ入力** |
| `CLAUDE.md` 「S_ja」 | `S_ja = {..., A, I, U, E, O}` | **`A` / `E` / `O` は 23,271 行で 1 度も出現しない。`I` / `U` のみ** |
| feasibility `§2.4` の「config.json 一致」 | ckpt 選定根拠として `config.json` 一致を挙げる | **ローカル ONNX と ckpt は `phoneme_id_map` まで完全一致するので、config 一致は同一性の証明にならない**（実際、同一文で durations が 98 vs 115 と別物） |

---

## 1.5 【スコープ変更 2026-08-26】ESP32 が必須。ブラウザは対象外

**ブラウザ tier は piper-plus の WebAssembly で既に解決済みのため、本プロジェクトの成果物ではない**
（ユーザー判断）。**ESP32 で日本語が喋れなければプロジェクトに意味が無い。**

この変更が計画に与える影響:

| 項目 | 変更前 | 変更後 |
|---|---|---|
| 成果物 | 1.4 M quality (browser) を優先 | **567 K embedded (ESP32) が唯一の成果物** |
| 1.4 M の位置づけ | 本命 | **検証用の足場**（蒸留レシピが日本語で効くかを速く測り、567 K との差分を出すため） |
| オンデバイス G2P | 「音素ID入力に割り切る」で回避 | **回避不可。成立しなければプロジェクトが無意味** |
| D0（tier 決定） | 未解決ブロッカー | **決着済み** |

### B-0 オンデバイス日本語 G2P のフットプリント ✅ 測定完了 (2026-08-26)

**結論: 辞書枝刈り路線は不成立。入力仕様を変更して解決した。**

| 測定 | 結果 |
|---|---|
| 16 MB ボードに収まる最大辞書 | 60k表層 11.57 MB → 文単位一致 **73.0%** |
| 文単位 95% に必要な辞書 | **40.05 MiB** — 32 MB ボードにも入らない |
| C 実装のみ（Python 後処理層なし） | フル 103 MB 辞書でも音素列 95.4% / アクセント 84.0% |

→ **入力を「ひらがな + アクセント記号 + 無声化マーク」に変更**（D-010 / D-011）。
端末側 G2P は **テーブル 877 B（mora 195 + 記号 10 + `ん` 異音 21）**になり、
held-out で表現可能 96.40% / 往復一致 **100%** / 教師出力と **bit 完全一致**。

詳細: [`../research/b0-g2p-footprint.md`](../research/b0-g2p-footprint.md) /
[`../decisions.md`](../decisions.md) D-009〜D-011 /
実装: `scripts/kana_g2p.py`

**以下は測定時の記録**（辞書枝刈りは実装対象から外したので、再実行の予定は無い）。

実測済みの内訳（`build/share/open_jtalk/dic/`）:

```
sys.dic  103,082,017 B   lexsize = 788,923 エントリ (平均 131 B)
  ├ feature 文字列 67,242,425 B  ← 65.2%。品詞・活用など TTS に不要な情報が大半
  ├ darts trie     23,216,752 B
  └ token          12,622,768 B
matrix.bin 3,792,262 B   char.bin 262,496 B   unk.dic 5,690 B
```

TTS に必要なのは**読み・アクセント型・アクセント結合規則・最小限の品詞**のみ。
エントリ枝刈りと feature 削減は独立に効くので、線形概算では:

| 語数 | 素の概算 | feature を TTS 用に削減 |
|---:|---:|---:|
| 10,000 | 1.2 MB | 〜0.6 MB |
| 30,000 | 3.7 MB | 〜1.8 MB |
| 60,000 | 7.5 MB | 〜3.7 MB |

ESP32-S3 (16 MB flash) はアプリ + IDF を引いて 12 MB 前後残るので、**物理的には数 MB の
TTS 特化辞書が載る**。⚠️ ただし darts trie は語数に線形には縮まないので、**上表は概算**。

**検証タスク**:
1. NAIST-JDIC を語彙頻度で枝刈りし、feature を「読み + アクセント型 + アクセント結合型 + 品詞大分類」
   に削った TTS 専用辞書をビルドする。語数は 10k / 30k / 60k の 3 水準
2. 各水準で **実バイナリサイズ**を測る（線形概算の検証）
3. `data/splits/diverse_ja.tsv` と実運用想定文に対し、**フル辞書との音素列一致率**を測る
   — 辞書サイズ単体では判断できない。**「実文の何 % が正しく読めるか」が唯一の判定基準**
4. 未知語フォールバック（`unk.dic` の文字種ベース推定）が漢字複合語で何を返すか確認

**完了条件**: `reports/b0_dict_footprint.json` に語数 × {バイナリサイズ, 音素列一致率,
アクセント型一致率, 未知語率} の表が出て、**「N MB で実文の M % が正しく読める」と言い切れる状態**。

**判断分岐**:
- 一致率が実用水準（目安 95%+）で数 MB に収まる → **プロジェクト続行**
- 収まらない → **入力境界の再定義が必要**。かな入力限定 / 定型文プリコンパイル /
  ホスト側 G2P のいずれかに落とすか、プロジェクト自体を見直す

**→ 実際に「収まらない」となり、かな入力限定（D-010 / D-011）に着地した。**

---

## 2. 検証タスク B-0 〜 B-12

**「検証を先に置く」を計画の原則にする。** 論文が narrow test set で 1.35 の過大評価を
した教訓、および集約スコアが sibilant 欠陥を隠した教訓から。

**B-0 〜 B-12 はすべて決着した（2026-08-28）。**

| 状態 | タスク |
|---|---|
| ✅ 解決済み | **B-0 〜 B-12 の全部**。B-1 / B-2 は Phase A の設計判断で構造的に消え、残りは実測で決着した |

| タスク | 決着 | 記録 |
|---|---|---|
| B-4 長さフィルタ | `max_spec_length=700` で 4.31% 除外。ID 長は 0.11% で実質効かない | M-23 / D-017 |
| B-6 平坦度プローブ | `n_fft=1024 / guard=0 / power=1`。教師ベースラインを `flatness.py` に凍結 | M-27 |
| B-7 `s_v` | **1.2187**。上限 80 は飽和せず、下限 1 が 18.49% のトークンに効く | M-24 / D-019 |
| B-8 `_` PAD | **フレームの 53.76%**。PAD の duration は実音素より長い → 特別扱いしない | M-25 / D-018 |
| B-9 死んでいる音素 | デプロイ語彙は閉包で **57**。疑問 EOS 3 種はコーパスに文を足して解消 | M-26 / D-016 |
| B-10 コーパス重複 | held-out に FT テキスト 10 行。102 uid を除外 | M-28 / D-024 |
| **B-12 事前学習との重複** | **看板の 24 文と MOE-Speech の重複はゼロ**。陽性対照 200/200 で検出力も確認。⚠️ 照合は表層テキストのみ | M-47 |
| **D-4 アクセント型** | ミニマルペア 15 群 32 語で**符号一致 35/36**。記号 `[ ] #` だけで足り A1/A2/A3 は不要。⚠️ **聴取していない** | M-44 / D-030 |

B-1 / B-2 は Phase A の設計判断で**構造的に消えた**（潰したのではなく、
そこを通らない経路にした）。

### B-1 G2P の言語誤ルーティング ✅ 解消済み（A-1 の決定で構造的に消えた）

**当時の問題**: 教師の canonical 経路 `MultilingualPhonemizer` は「かな」を文全体で
判定するため、**かなを 1 文字も含まない行が丸ごと中国語音素になる**。
コーパスの 5.36%（1,247 行）が該当し、符号化 id が `num_symbols=173` 未満に収まるため
**例外も警告も出ない**。

**解消のしかた**: A-1 でラベル生成の入力を**かな中間表現**に統一した。
中間表現の生成は `JapanesePhonemizer` しか通らないので、
`MultilingualPhonemizer` の言語判定が経路から消えた。

**証拠**（M-19、自己実測）:

```
埼玉県秩父市
  旧経路 (MultilingualPhonemizer) : i ɕ f u ʂ                    ← 中国語音素
  新経路 (JapanesePhonemizer)     : s a i t a m a k e N_n ch I ch i b u sh i
```

現行経路との音素ID一致が 86% に留まるのは、**残り 14% が旧経路側の誤ルーティング**
だから（M-20）。新経路を正解とみなしてよい。

⚠️ **入力サニタイズは別途必要。** 未知語は誤読ではなく**無音で脱落する**（D-009）。
外字・幽霊漢字はホスト側 G2P でも同じ失敗をする。

### B-2 `prosody_features` の無警告ズレ ✅ 解消済み（A-2 の決定で消えた）

**当時の問題**: `PiperEncoder._convert_prosody` が zip の前に長さを強制的に揃えるため
`strict=True` が原理的に発火せず、ラテン混じり文で **prosody が末尾側にずれたまま通る**。

**解消のしかた**: A-2 で**ラベル生成の prosody を一律ゼロにした**（B-c で実測して決着）。
prosody を渡さないので、ずれようがない。

根拠（held-out 無作為 24 文、自己実測）:

| 条件 | UTMOS mean |
|---|---:|
| canonical + 実 prosody | 1.730 |
| **中間表現 + zeros** | **1.740** |

対応のある t 検定で **p = 0.72、有意差なし**。加えて:

- **デバイスは prosody を供給できない**ので、教師と生徒の条件が揃うほうが良い
- 実 prosody は ID 列の 92% で位置合わせできない（長さ一致が 24 文中 2 文）
- prosody は duration にしか効かず、ピッチはアクセント記号が担う（M-13）

⚠️ **ゼロは「prosody 無し」ではない。** `prosody_proj(0) = bias` が concat される
第 3 の条件で、**それで一貫させる**という決定（M-20）。

### B-3 `fy` など音素表 OOV ✅ 決着（除外する）

**教師の `phoneme_id_map` に `fy` が無い**ことを確認した（`hy`=55 / `f`=53 はあるが
`fy` は無い）。中間表現の問題ではなく教師側の制約。

コーパス 23,457 行のうち **55 行 (0.23%)** が該当。**ラベル生成から除外する**
（`gen_teacher_labels.py` が KeyError で棄却し `index.jsonl` に理由を残す）。

### B-4 長さフィルタの基準 ✅ 決着（M-23 / D-017）

**採用しない**: 「mora 5〜100 で切る」— 教師側の根拠が無い恣意的な値。
**採用する**: 教師の学習時制約。**実測すると効き方が非対称だった**。

| 制約 | 超過 | 内訳 |
|---|---:|---|
| `max_phoneme_ids = 400` | 25 件 (0.11%) | train 24 / heldout 1 |
| **`max_spec_length = 700` (8.13 秒)** | **1,003 件 (4.31%)** | train 911 / heldout 92 |

**「306 vs 116」の食い違いは 3 つの単位の取り違えだった**（C-019）。
正しい関係式は `len(ids) == 2*n_phonemes + 3 + (PAD 音素の数)` で、
**全 23,297 発話で成立する**（`b4_length_hist.py` が assert する）。

ゲート G12 (4–12 mora/s) を外れるのは 57 件 (0.245%)、G13 の平均範囲にも収まる。
⚠️ **ゲートは厳密なモーラ数ではなく近似（記号を除く音素数 / 2）で測る。**
2 つの尺度は 1.167 倍違うので混ぜて読まないこと。

再現: `uv run python scripts/b_durations_all.py` → `scripts/b4_length_hist.py`

### B-5 教師音声の品質ベースライン ✅ 測定完了 (2026-08-26)

**結論: 「壊滅的」という当初の解釈は誤りだった。** 詳細は M-10 / D-013 / C-012。

当初は「教師 wav が SCOREQ 2.06 / UTMOS 1.62 で論文の 4.68 / 4.42 から乖離している」
ことを問題視していたが、**日本語の既知良品リファレンスを測っていなかった**のが誤り。

24 文 / 平均 5.16 秒 / 前後 0.3 秒パディング / canonical な音素化経路で測り直した:

| 対象 | n | UTMOS mean |
|---|---:|---:|
| 教師（合成） | 24 | **1.748** |
| **実人間音声**（つくよみちゃんコーパス = 教師の元データ） | 24 | **2.305** |

**実人間の日本語ですら 2.305 しか出ない。** UTMOS は日本語でスケールが圧縮されている
（同一エンジンで ES 3.51 / EN 2.97 / FR 2.23 / ZH 1.80）。

```
教師 / 実人間 = 0.758     ← これが正しい読み方
```

発話長との相関 r = −0.372 なので**短尺アーティファクトではない**。

**この spike で確定した評価方針**（D-013）: 品質は**教師比と人間音声比の両方**で報告する。
人間側の分母は教師の元コーパスを使う（話者・録音条件が揃う）。

再現: `uv run --extra eval python scripts/b5_teacher_baseline.py` →
`scripts/b5_measure_mos.py`。一次データは `reports/b5_teacher_baseline.json` /
`reports/b5_human_control.json`。

**✅ SCOREQ は導入済み**（`scoreq==1.0.1`。「pip パッケージが見つからない」は誤りだった、C-016）。
同じ 24 対を測り直した結果（M-29 / D-020）:

| 指標 | 教師 | 実人間 | 教師/人間 |
|---|---:|---:|---:|
| UTMOS | 1.7479 | 2.3047 | 0.758 |
| **SCOREQ synthetic/nr**（主指標） | **2.0488** | **2.4983** | **0.820** [0.773, 0.870] |

2 指標の pooled 相関は r=+0.850 (n=48)。**`data_domain="natural"` は使わない**
（合成音声を実人間より高く採点し、UTMOS と無相関）。

### B-6 スペクトル平坦度プローブの窓設計 ✅ 決着（M-27）

**採用: `n_fft=1024 / hop=256 / guard=0 / power=1`。**
教師ベースラインは `src/saanotts_jp/flatness.py` に凍結してある
（定数がレポートとズレたら `scripts/b6_flatness_grid.py` が exit 1）。

以前の反証（fricative と affricate のプール、guard=1、power=2、n=3 の無声化母音）は
すべて**サンプル不足**が原因だった。クラスごとに 420 音素以上を確保した
評価セット `data/splits/corpus_sibdense.tsv`（220 発話）を先に作って測り直した。

| 判断 | 根拠 |
|---|---|
| guard=0 | guard=1 は span<3 frame を 78% 落とす母集団選択。同じ部分集合で比べると 6/6 で guard=0 が上 |
| n_fft=1024 | 512 との ΔAUC +0.0103 [+0.0074, +0.0131] |
| power=1 | power=2 と**区別できない**（ΔAUC 4.7e-5）。同点として扱った |

**`devoiced (I,U) vs vowel` は AUC 0.8466 / d=+1.303 (n=495)** —
式7 の `S_ja` に `I` `U` を入れる判断をこれが支える。

⚠️ 絶対値の読み方には 3 つの制限がある（`flatness.py` の docstring / M-27）。
特に **`geminate` の基準値は int16 の量子化床**で、教師の性質ではない。

### B-7 `s_v`（日本語 length scale）✅ 決着（M-24 / D-019）

**`s_v = 1.2187`。** ただしこれは**言語固有の話速ではなく、
教師の `ceil` と生徒の `round + clip` の規約差を吸収する係数**。

| 条件 | 発話ごとの比 mean | sd |
|---|---:|---:|
| `s_v = 1.0` | 0.8135 | 0.0239 |
| **`s_v = 1.2187`** | **0.9984** | **0.0206** |

⚠️ **反証された旧説「1.2 前後」と数値が近いが、根拠は別物。**
旧説は壊れた 1 文の SDP サンプル比から出ていて、量としても違う。偶然近いだけ。

⚠️ `r_i` に生徒の予測ではなく `dT` を代入している。**学習後に解き直す。**

**`clip_[1,80]`**: 上限 80 は飽和しない（max ceil 30）。効くのは**下限 1** で、
`dT<1` が 18.49%、うち実音素が 290,692 個。99.96% の発話が該当トークンを含む。

### B-8 `_` PAD がフレームの過半を占める ✅ 決着（M-25 / D-018）

| 項目 | 値 |
|---|---:|
| PAD のトークン比 | 50.02% |
| **PAD のフレーム比** | **53.76%** |
| PAD の duration mean | **2.059**（実音素は 1.697） |

**PAD を特別扱いしない**（D2 の決着）。音声時間の過半を占め、1 個あたりの長さは
実音素より長い。さらに **破擦音・破裂音の摩擦バーストが後続 PAD に入っている**
（破擦音では PAD の帯域 RMS が音素区間の 3.6 倍）。
この教師のアライメントでは `_` は「無」ではなく実音響を担う単位。

### B-9 デプロイ語彙の凍結 ✅ 決着（M-26 / D-016）

**語彙は 57**（論文の英語版は 157）。`kana_g2p` から原理的に出せる音素の閉包で、
コーパス出現 54 の上位集合。合計 **567,008 → 559,008 params**。
⚠️ **MMAC は 1 も減らない**（埋め込みは表引き）。浮くのは flash 8 KB だけ。

疑問 EOS `?!` `?.` `?~` は**到達可能なのにコーパスに 1 行も無かった**ので、
実文 47 行を追加した（`scripts/b9_add_question_eos.py`）。
併せて `〜`(U+301C) が黙って落ちる問題を `kana_g2p.normalize_input()` で塞いだ。

残る未出現は `A` `E` `O`（無声化母音、日本語には出ない）。`a` `e` `o` と同値初期化する。

⚠️ **生徒は教師の音素ID をそのまま使えない。** `src/saanotts_jp/vocab.py` の
`TEACHER_TO_STUDENT` を通す。**重みと一緒に凍結する。**

### B-10 教師の学習テキストとの重複 ✅ 決着（M-28 / D-024）

**held-out に `jsut/repeat500` が 10 行 (0.43%) 残っていた。**
`repeat500` set1 は VOICEACTRESS100 と本文が 98/100 共通で、
その VOICEACTRESS100 が教師の FT テキストそのもの。
**両サブセットを丸ごと除外**（102 uid、`data/splits/exclusions_teacher_ft.txt`）。

train↔heldout のリークは軽微（完全一致 0 / 最大 Jaccard 0.6875）。embedded はクリーン。

⚠️ **実害は未実証。** 教師側 UTMOS で汚染 10 文 vs 非汚染 24 文の差は
+0.102 / p=0.334 で検出できなかった。**予防措置**として除外する。

⚠️ 検査中に `gen_teacher_labels.py` の**ヘッダ行バグ**が見つかった（C-018）。
`csv.reader` がヘッダを飛ばしておらず、パックの `seq=0` が
`uid="id" / text="text"` という架空の発話になっていた。**13 個のゲートを全部通る。**


### B-11 `.venv` の stale install ✅ 解決済み (D-012 の uv 導入で消滅)

**採用しない**: `cd piper-plus && .venv/bin/pip install -e src/python`。
**反証**: editable install は既に `2026-08-24 16:27` 付で入っているのに効いていない。setuptools の finder が `sys.meta_path` に **append**（insert でなく）されるため標準 `PathFinder` が先に `site-packages/piper_train/` を拾い、しかもその実体は**別ディストリビューション `piper_plus_workspace 1.12.0` の所有物**（`grep -l "piper_train/vits/models.py" */RECORD`）なので `piper-train 2.0.0` を入れ直しても消えない。

**採用する**: **`uv` の独立 venv**（D-012）。piper-plus を `[tool.uv.sources]` の
path 依存 (editable) で参照するので、stale なコピーが最初から存在しない。
旧案の `PYTHONPATH` / `sys.path.insert` も効くが、`uv run` を使えば不要
（既存スクリプトの `sys.path.insert` は冗長だが害はない）。

**完了条件**（全スクリプトの冒頭でこれを assert する）:
```bash
export PP=~/Documents/piper-plus
uv run python - <<'EOF'
import piper_train.vits.models as M, inspect
import piper_train.vits.mb_istft as m, piper_train.vits.commons as c
assert M.__file__.startswith("~/Documents/piper-plus/src/python/"), M.__file__
assert "cond_layers" in inspect.getsource(m)
assert hasattr(c, "normalize_checkpoint_state_dict")
print("OK: src/python が解決先")
EOF
```

---

## 3. Phase 0 — 教師の動作確定

**目標**: 「このコマンドがこの出力を出す」レベルで教師の挙動を凍結し、Phase 1 のラベル生成器が依拠する前提をすべて実測値に置き換える。

### P0-1 環境の分離と固定

piper-plus は**読み取り専用**（`checkout` / `commit` / ファイル編集の禁止）。ラベル生成は piper-plus の `.venv` を read-only で起動、評価は sanoTTS-jp 側の独立 venv。

```bash
# ラベル生成側（piper-plus venv を read-only 起動）
export PP=~/Documents/piper-plus
alias ppy="uv run python"   # D-012: Python は uv 経由

# 評価側（完全に別 venv）
python3.12 -m venv ./.venv-eval
```

**完了条件**:
- §B-11 の assert スクリプトが `OK: src/python が解決先` を出す
- `cd $PP && git status --porcelain` が **0 行**、`git rev-parse HEAD` が `0f3b1a62…`、`.git/worktrees` が**存在しない**
- 全作業スクリプトの CI 前提として、この 3 点を確認する `scripts/guard_piper_plus_readonly.sh` が exit 0

> **学習時コードの保険**: `95e74cb2` は dangling commit（`git branch -a --contains` が 0 行、`git cat-file -t` は `commit`）。piper-plus 側で `git gc --prune` が走ると消える。必要なら今のうちに `git archive`（`.git` に一切書き込まない。`worktree` は `.git/worktrees/` を作るので不可）で `vendor/piper-plus-95e74cb/` に抽出しておく。ただし HEAD と推論数値差分がゼロであることは実測済みなので、**現時点では不要**。

### P0-2 ckpt ロードと EMA 適用の確定

`scripts/phase0_verify_teacher.py` を**書き直す**。現行版は `speaker_embeddings=speaker` を渡し、EMA を適用せず、`prosody = torch.zeros(1, len(ids), 3)  # TODO(Phase 1)` のままなので、**5 チェックすべて PASS するのに「話者埋め込みが効いている」という誤った確信を与える**。

```python
def load_teacher(ckpt_path):
    m = VitsModel.load_from_checkpoint(ckpt_path, dataset=None, strict=False)
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False, mmap=True)
    ema = ck.get("ema_generator_state")
    assert ema and "shadow_params" in ema
    n = apply_ema_shadow_params(m.model_g.dec, ema["shadow_params"])   # ★ remove_weight_norm の前
    del ck
    m.eval(); g = m.model_g
    with torch.no_grad():
        g.dec.remove_weight_norm()
    assert not hasattr(g, "spk_proj") and not hasattr(g, "emb_g")
    return g
```

**完了条件** — `ppy scripts/phase0_verify_teacher.py` が以下を全部出す:
```
[OK] resolved piper_train from src/python
[OK] state_dict: missing 0 / unexpected 0 / cond_migrated 0
[OK] no spk_proj, no emb_g            (single-speaker)
[OK] EMA applied 53/53, skipped 0     (before remove_weight_norm)
[OK] speaker invariance: audio/z/durations bit-identical for (None | spk_tsukuyomi.npy | random-192)
[OK] determinism: 2 runs bit-identical; z_p == m_p at noise_scale=0
[OK] frame identity: ceil(dT).sum() == zT.shape[1] == len(yT)//256
[OK] lid sensitivity: lid=1 changes frames (=> lid=0 must be pinned)
```

> `load_from_checkpoint` は実行時に `Optimizer state is in the pre-FiLM layout ... discarded.` を出す。**推論のみなら無害**。この ckpt から学習を再開する場合だけ問題になる。

### P0-3 prosody の実効性と freeze_dp の含意の記録

**測定タスク**: 同一文で `prosody=実A1/A2/A3` / `zeros(1,T,3)` / `None` の 3 通りを比較。

**既知の実測**: `'今日はいい天気ですね。'` で総フレーム **115 / 106 / 119**。音素あたり最大 +35%、`prosody=None` は「ゼロ」ではない。

**記録すべき含意（`docs/notes/teacher-properties.md`）**:
- **`freeze_dp=True`**: 教師 ckpt は Tsukuyomi FT の間 duration predictor を凍結しており、dp の重みは **571 話者・6 言語の v7 multi-speaker base (`epoch=32-step=216326.singlespk.ckpt`)** のまま。つまり蒸留の `dT` は「つくよみちゃんに適応していない多言語 DP」の出力。論文の `Dα` (36,164) が何を模倣することになるのか、CLAUDE.md が挙げるアクセント型ミニマルペア評価がそもそも教師側で成立するのかに直結する。
- **`emb_lang` unify**: `export_onnx.py:495` の `should_unify_emb_lang(None, 1, 6)` は `True` を返し、単一話者×多言語の ONNX 化では `emb_lang[0]` が全言語にコピーされる。`lid=0` = ja なので日本語ラベルに限れば no-op だが、**`lid` を 0 以外にした瞬間 ckpt 教師と配布 ONNX 教師が食い違う**。

**完了条件**: `reports/p0_prosody_sensitivity.json` に 3 条件 × 20 文の総フレーム数が出て、`docs/notes/teacher-properties.md` が上記 2 点を記載。

### P0-4 アクセント型ミニマルペアが教師側で成立するかの確認

**確定していること**: アクセント記号は音素**トークン列の中に inline で**入り、実際に区別される。
```
橋が   h a [ sh i ] g a      箸が   h a ] [ sh i g a      端が   h a [ sh i g a
雨が   a ] [ m e g a          飴が   a [ m e g a
神が   k a ] [ m i g a        紙が   k a [ m i ] g a
今から i ] [ m a k a r a      居間から i [ m a ] k a r a
雲が = 蜘蛛が k u ] [ m o g a  （実際に同アクセント）
```
担ぎ文を揃えた『箸で食べる』vs『橋で食べる』では `durations` も総フレーム（84 vs 83）も変わる = **モデル出力レベルでも区別される**。

**⚠️ ただし平板型（0 型）には `]` が挿入されない**（飴、端）。「`]` の位置比較だけでピッチ評価の正解ラベルになる」は成立せず、**「核なし」を明示的な 1 クラスとして扱う設計が要る**。

**完了条件**: `eval/sets/minimal_pairs_ja.tsv` の 20 ペアそれぞれについて、教師の `(tokens, ceil(dT), 核位置 or NONE)` を出力した `reports/p0_minimal_pairs.json` が存在し、**平板型メンバーが `NONE` として正しくラベルされている**。

### P0-5 ラベル生成の最小疎通（8 文 → 50 文）

**入出力の確定値**（実測済み、これが再現すれば疎通 OK）:
```
x=[1,T] int64 / x_lengths=[1] int64 / lid=[1] int64 / prosody=[1,T,3] int64
→ audio=[1,1,N] f32, attn=[1,1,T_frames,T_text] (0/1), y_mask, z=z_p=m_p=logs_p=[1,192,T_frames], durations=[1,T_text] f32 (ceil 前)
'今日はいい天気ですね。' → T_text=49 → z=(1,192,115) / audio=(1,1,29440) / attn=(1,1,115,49)
29440 == 115*256、attn.sum(frames)[:5] == ceil(durations)[:5] == [4,2,2,2,2]
durations[:5] = [3.7493, 1.4884, 1.1778, 1.2604, 1.4837]
```

**完了条件**:
```bash
ppy scripts/gen_teacher_labels.py --texts eval/sets/smoke50_ja.txt --out data/packs/smoke/ --save-audio
# 期待出力:
#   teacher: n_vocab=173 n_languages=6 prosody_dim=16 inter_channels=192
#   gate_ja_only: 50/50 pass (non-JA tokens: 0)
#   prosody_align: 50/50 pass
#   done: 50 utts, N frames, RTF ~0.066
python scripts/verify_packs.py data/packs/smoke/
#   [OK] 50/50: ceil(dT).sum() == zT.shape[1] == len(yT)//256
#   [OK] 50/50: frame_tok == cumsum(ceil(dT)) の割り当てと完全一致
#   [OK] phoneme_ids.max() < 173
```

**`gen_teacher_labels.py` の既知の欠陥（修正必須）**:
| 欠陥 | 修正 |
|---|---|
| `assert len(ids) == len(pf)` が `_convert_prosody` のせいで**常に真＝無意味** | B-2 の token 段階 strip + 回帰テストに置換 |
| `m_p` を計算・返却しているが npz に保存していない | 保存する（`z_p == m_p` を下流で使えるように） |
| skip 行があると出力ファイル名に穴が空き進捗もズレる | 連番カウンタを入力行番号から分離 |
| `prosody` を int16 で保存（モデル入力は int64） | 読み戻し時のキャストを関数に隠蔽 |
| `PiperEncoder(strict=True)` を使っている | B-2 に従い `strict=False` + 明示 strip |
| EMA 未適用 / `speaker_embeddings` の扱い | P0-2 の `load_teacher()` に置換 |

**`phoneme_tokens_full` の追加は不要**（反証済み）。`phoneme_ids`（49 要素、`frame_tok` の値域と一対一）が既にあり、`config.json['phoneme_id_map']` を反転すれば復元できる。逆引きは **`piper_plus_g2p.encode.pua.CHAR2TOKEN` (99 entry)** を使う（`jp_phoneme_map.PHONEME_TO_PUA` は 22 entry しかなく 173 symbol 中 55 個を解決できない）。
なお `phoneme_tokens`（23 要素の de-pad 版）から intersperse で復元する素朴な方法は**失敗する**（`000006.npz` は読点で `_` が連続し 1+2*64+2=131 ≠ 130）。

### P0-6 教師ラベルの健全性ゲート（Phase 1 に進む条件）

論文の教訓（narrow set で 1.35 の過大評価 / 集約スコアが sibilant 欠陥を隠した）を、**ラベル生成の前**に反映する。

**完了条件** — 以下を全部満たしたら Phase 1 へ:
1. B-1〜B-4 が GREEN
2. `reports/b5_teacher_baseline.json` が `|ckpt_teacher - public_onnx| < 0.3`（SCOREQ / UTMOS 両方）
3. **読み検証 oracle が通る**: JSUT `jsut-label/text_kana/basic5000.yaml`（4.2 MB、DL 済み）+ ITA / ROHAN の第 2 フィールド（カタカナ読み）= **合計 5,000 + 4,600 + 424 行の読み正解データが手元にある**。50 文サンプルで教師の音素列を oracle 読みと突き合わせ、一致率を出す。これは B-1 の中国語化も難読語も一括で検出できる（現在の「読みが付かない行の検出」は pron が `、` のケースしか拾わず、**自信を持って間違って読む**ケースを素通りする）。
4. 教師音声 20 本以上（3 秒以上）を聴取し、つくよみちゃんの音色として妥当と判断

---

## 4. Phase B（旧 Phase 1）— ラベル生成パイプライン ✅ 実装完了

> **実装は済んでいる。** 以下は設計の記録と、本番実行（B-e）の手順。
>
> | 成果物 | 状態 |
> |---|---|
> | `scripts/gen_teacher_labels.py` | ✅ heldout 200 文で採用 200 / 棄却 0 / 124 ms/文 |
> | `src/saanotts_jp/labelpack.py` | ✅ 往復テスト + ゲート 13 項目（`scripts/test_labelpack.py`） |
> | `scripts/b4_device_parity.py` | ✅ CPU/MPS 照合済み。**CUDA は vast.ai 上で要実行** |
> | **B-e 本番実行（20,946 文）** | ⏸ vast.ai 待ち。パック約 5.8 GiB |
>
> 本番の手順:
> ```bash
> uv run python scripts/b4_device_parity.py --device cuda   # 先にこれ
> uv run python scripts/gen_teacher_labels.py --split train --out data/pack
> ```

### P1-1 コーパス調達

**確定した調達計画**（すべて実測でダウンロード可能を確認済み）:

| Tier | ソース | 行数 | ライセンス | 担う軸 | 入手 |
|---|---|---:|---|---|---|
| T1-a | **ROHAN4600** | 4,600 | **CC0 / PD** | モーラバランス、助数詞 1–10・月 1–12、疑問文 5% | curl 1.1 MB |
| T1-b | **ITA** (emotion100 + recitation324) | 424 | **PD** | 音素バランス、稀モーラ (ツァ/テュ/フュ/ヴィ) | curl 71 KB |
| T1-c | Common Voice ja | 11,060 | CC0 (要検証) | 口語・Web | curl 866 KB |
| T1-d | JSUT ver1.1 transcript (9 subset) | 7,696 | subset 別 CC-BY-SA / precedent130 は PD | 常用漢字、旅行、判例、オノマトペ、外来語 | **HTTP Range 抽出 808 KB**（2.7 GB zip を落とさない） |
| | **小計（NFKC 重複排除後）** | **23,271** | | | 約 2.7 MB |
| T2 | 合成ギャップ埋め | 1,500–2,000 | 自作 | `?!` `?.` `?~`、金額、日付時刻、英数字混在、ひらがなのみ、アクセント MP | 自作 |
| **除外** | shunk031/livedoor-news-corpus | — | **CC-BY-ND-4.0（改変禁止）** | — | — |

**判断: T1 だけで論文の 1.62 倍。T3（青空文庫 / jawiki）は不要。**

**⚠️ Common Voice sentence-collector の実態**（Tier 表の「口語・Web・文学」という説明は実態と合わない）:
- pool の 40.7% を占める 9,469 行のうち **6,307 行 (66.6%) が文末句読点を持たない断片**
- **1,110 行がかなを 1 文字も含まない**（= B-1 で全部中国語）
- **958 行 (pool 全体の 4.1%) が「◯◯県◯◯市」だけの裸の地名**（福岡県 57, 愛知県 52, 東京都 48 …）
- 平均 20.6 文字

CLAUDE.md が「テンプレート文は使わない」と明記しているのに、実質テンプレート生成された地名リストが最大ソースの 1 割を占める。**D3: CV をどこまで使うかの判断**（`docs/decisions/D3-corpus-mix.md`）。

**⚠️ CV のライセンス根拠が最弱なのに最大シェア**: repo は MPL-2.0、`server/data/ja/` に個別 LICENSE 無し（3 URL とも 404 実測）。`docs/SENTENCES.md` にも文そのものを CC0 とする明示は無い。**PD/CC0 だけの「配布可能サブセット」（ROHAN 4,600 + ITA 424 + JSUT precedent130 + 合成分 ≈ 7,000 行）を最初から別 tag で管理する。**

**完了条件**:
```bash
bash scripts/fetch_corpus.sh          # 4 ソース DL + JSUT の Range 抽出
python scripts/build_pool.py --out data/interim/pool.tsv
# 期待: RAW 23,780 / UNIQUE(NFKC) 23,271 / dropped 509
#   by source: jsut 7,189 / rohan4600 4,600 / ita 422 / cv 11,060
```

> `scripts/remote_zip_extract.py` は `requests` 依存で **zip64 を明示的に非対応**にしている。JSUT が 4 GB 超や ZIP64 で再パッケージされたら落ちるので、失敗時に全体 DL へフォールバックするパスを書いておく。

### P1-2 前処理パイプライン（実測で決まった順序）

```
[1] ID / 読みフィールド分離   "ID:本文" または "ID:本文,カタカナ読み"
[2] ROHAN のルビ除去          re.sub(r"\(([ぁ-んァ-ヶー]+)\)", "", text)
                              → 4,600 行で括弧の残骸 0 件を実測
[3] 制御文字除去              phonemizer の _sanitize_input と同じ（>= " " のみ残す）
[4] ★NFKC は「重複排除キー」だけに使う。教師に渡す本文は正規化しない★
      理由: 「！？」→「!?」で ?! 判定が壊れ、「？。」→「?。」で疑問判定が $ に落ちる
      （「？！」→「?!」と「？～」→「?~」は NFKC 後も残る。壊れるのは記号が先に来る順序）
      合成文は最初から ASCII の ?! / ?. / ?~ で書けばこの問題を回避できる
[5] 重複排除                  key = NFKC(text).strip()  → 23,780 → 23,271 (-509)
[6] ★汚染除去★               VOICEACTRESS100 = voiceactress100 + repeat500 の
                              「両サブセット丸ごと」（和集合 102 文。100 ではない）
[7] ★長さフィルタ★           encoded_id_len <= 400 (max_phoneme_ids) かつ
                              est_frames <= 700 (max_spec_length ≈ 8.13 s)
                              ※ B-4 の測定結果で確定する
[8] ★OpenJTalk 読み落ち検出★ NJD ノードで pos=="記号" and pron in ("、","") and mora_size==0
                              実測 31/23,271 行 (0.13%)。大半は稀漢字（腔・閃・勃・禕・煖・胞・
                              療・哉・孺・憑・墜・孕）と ＝ ／ ＼ ――
[9] ★言語ルーティング検査★   B-1。出力トークンが日本語 65 エントリに閉じているか
[10] ★音素表 OOV 検査★       B-3。fy を含む 55 行を明示的に検出して除外
[11] ★prosody 整合検査★      B-2。token 段階 strip 後の (tokens, prosody) がペアで一致
[12] ★読み oracle 照合★      jsut-label / ITA / ROHAN のカタカナ読みと突き合わせ
[13] ラベル生成失敗検出       infer() の例外 / NaN / 全ゼロ音声 / dT 総和がモーラ数から乖離
                              → manifest に理由付きで記録して除外（論文の "audited" 方針）
```

**完了条件**: `python scripts/preprocess_pool.py --pool data/interim/pool.tsv --out data/interim/clean.tsv --manifest data/interim/reject.jsonl` が、**各段階の除外行数を内訳つきで出す**。`reject.jsonl` は 1 行 1 理由。

### P1-3 合成でギャップを埋める（T2, 1,500–2,000 行）

pool の実測カバレッジが薄い／ゼロの軸だけを埋める。**学習側の 10% を上限**（論文の 512 行テンプレート失敗を再現しないため）。

| 軸 | 現状 | 目標 | 備考 |
|---|---:|---:|---|
| **`?!` `?.` `?~`** | **0 件** | 各 +300 | **最優先。必ず ASCII で書く**（全角は NFKC で壊れる） |
| `?`（プレーン） | 391 (1.68%) | — | pool から |
| 金額 | 100 (0.43%) | +300 | `1,980円` `税込3,300円` `12万5000円` |
| 日付・時刻 | 429 (1.84%) | +300 | 各月・各曜日を網羅 |
| 数詞と助数詞 | 973 (4.18%, 37 種) | +300 | **読みが変わる境界を必ず入れる**（3本=さんぼん、6本=ろっぽん、8本=はっぽん、1杯=いっぱい、3杯=さんばい） |
| 英数字混在 | 176 (0.76%) | +200 | ⚠️ 読みは D1 に依存（下記） |
| ひらがなのみ | 136 (0.58%) | +200 | 子ども向け文体 |
| アクセント MP | 0 件 | +100 | 学習用。評価用は別途 40 文 |

> **⚠️ 英数字混在の読みは D1 次第で逆転する（未確定）。** `JapanesePhonemizer` では ASCII 語は 1 文字ずつアルファベット名（`christmas` → シー・エイチ・アール…）だが、`MultilingualPhonemizer` では英語読み（`k ɹ ˈɪ s m ə s`）。`IDはA1B2C3です` は ML で `ˈɪ d w # a ə n ɛ t s w ɪ b ɛ k` と破綻する。**D1 が決まるまでこの軸の文は書かない。**

**その他の pool 実測カバレッジ**: 漢字混じり 98.63% / カタカナ 42.81% / 約物 3.50% / アラビア数字 1.84%。

### P1-4 学習 / 評価分割

**評価セット `diverse-ja`（目標 300 文）** — 論文の diverse24 の日本語版。24 文は日本語には足りない。

**絶対除外リスト**:
- **VOICEACTRESS100 全 102 文**（`voiceactress100` + `repeat500` 丸ごと） — 教師 ckpt の FT テキストそのもの
- 学習に使った行そのもの（NFKC キーで照合）
- ⚠️ B-10 が終わるまで、MOE-Speech 事前学習テキストとの重複は**未確認**のまま残る

| 軸 | 文数 | 供給元 |
|---|---:|---|
| 漢字混じり一般 | 30 | ITA recitation324 held-out |
| ひらがなのみ | 20 | 自作 |
| カタカナ語 | 25 | JSUT loanword128 held-out |
| 英数字混在 | 20 | 自作（D1 確定後） |
| 数詞＋助数詞 | 30 | **⚠️ JSUT countersuffix26 は使わない**（26 行中 25 行が中国語ルーティング。`，`→`、` 置換でも直らない — 原因は行内にかなが 1 文字も無いこと）。ROHAN 助数詞文 + 自作 |
| 日付・時刻・金額 | 25 | 自作 + pool |
| 約物 | 20 | pool |
| オノマトペ | 20 | JSUT onomatopee300 held-out |
| 疑問文 `?` | 15 | pool |
| 疑問文 `?!` `?.` `?~` | 各 10 = 30 | **自作必須**（pool に 0 件。⚠️ `evaluation_texts_ja.txt` の 31 文も 4 つとも素の `？` で、この穴埋めにはならない） |
| **アクセント型 MP** | 40 (20 ペア) | **自作必須**。橋/箸/端、雨/飴、神/紙、牡蠣/柿、今/居間、花/鼻、酒/鮭。必ず助詞「が」等を後続させる。**平板型メンバーを「核なし」クラスとして明示** |
| 摩擦音・無声化母音密集 | 25 | pool から「です/ます/した/しつれい/すこし」高頻度文 |
| 長文（p95 以上） | 20 | pool |

`scripts/evaluation/evaluation_texts_ja.txt` の 31 文（アクセント / 疑問 / 数字 / カタカナ / 擬音語の軸で書かれている）を**種**にして拡張するのが最短。

**学習セット**:
- T1 pool 23,271 − diverse-ja 300 − 除外分 ≈ **22,900 行**
- 論文と同条件の比較のため **14,343 行のサブセットも別途切る**（ソース比率を保った層化サンプリング）
- 「23K vs 14.3K vs 512」の 3 点でスケーリングを見られるようにしておく = 論文 §1.5 の再現

**完了条件**:
```bash
python scripts/build_splits.py --clean data/interim/clean.tsv --out data/splits/
# data/splits/{train_23k.tsv, train_14343.tsv, train_512.tsv, diverse_ja.tsv,
#              minimal_pairs_ja.tsv, sibilant_dense_ja.tsv} が生成
python scripts/check_leakage.py data/splits/
#   [OK] diverse_ja ∩ train_* = 0 rows (NFKC key)
#   [OK] VOICEACTRESS100 (102 rows) not in any split
#   [OK] diverse_ja covers 13/13 axes, min 15 rows/axis
```

### P1-5 一括ラベル生成

**性能とサイズの見積り（実測ベース。以前の見積りは 18% 過大だった）**:

| 構成 | 発話数 | 音声時間 | yT int16 | zT fp16 | cT 40ch fp16 |
|---|---:|---:|---:|---:|---:|
| 論文同等 | 14,343 | 15.8 h | 2.50 GB | 1.88 GB | 0.39 GB |
| T1 pool 全体 | 23,271 | **25.6 h** | **4.06 GB** | **3.05 GB** | 0.63 GB |

- 実測 RTF **0.066**（Apple Silicon CPU シングルスレッド）→ 23,271 行で実時間 **約 2 時間**
- **`yT` は int16 の wav 別置き + npz にはパスだけ**（論文の `L_G` はマルチ解像度 STFT を学習中に毎回取る前提なので lazy load でよい）
- `zT` を fp32 で持つと倍。**fp16 で保存する**
- ⚠️ `--save-audio` で float32 を npz に入れると実測 **142.8 kB / audio-second** = 25.6 h で 13 GB。使わない

**保存フォーマット**（`data/packs/{split}/{seq:06d}.npz`）:
```
text / phoneme_ids (int32) / prosody (int16) / dT (float32) / m_p 相当の zT (float16 [192,T])
frame_tok (int32) / yT_path (str, 別置き int16 wav) / src_id / lang_id
+ channel_stats.npz: mu_T[192], sigma_T[192], n_frames
+ index.jsonl: 1 行 1 発話のメタ（source, encoded_len, frames, seconds, reject_reason=null）
```

**`μ_T` / `σ_T` は必ず 1 パスで集計して同梱**。実測で `σ_T` は **0.041〜9.305（230 倍差）**なので、`L_c` の `N_T` 正規化を省くと高分散チャネルが loss を支配する。c-line なら `Eρ` を通した後の 40ch 版も別に保存する。

**完了条件**:
```bash
ppy scripts/gen_teacher_labels.py --texts data/splits/train_23k.tsv --out data/packs/train_23k/ --workers 4
python scripts/verify_packs.py data/packs/train_23k/ --strict
#   [OK] 23,xxx/23,xxx: ceil(dT).sum() == zT.shape[1] == wav_frames
#   [OK] 0 rows with non-JA phoneme tokens
#   [OK] 0 rows with phoneme_ids >= 173
#   [OK] 0 NaN / all-zero audio
#   [OK] channel_stats: sigma_T min/max = 0.0xx / 9.xx, n_frames = ...
#   [WARN] rejected: N rows (see index.jsonl)
```

### P1-6 `s_v` の較正と duration 統計

ラベルが全部揃ってから実施（B-7 参照）。

**完了条件**: `reports/p1_duration_stats.json` に以下:
- `dT` のヒストグラム（生 float / `ceil`）、`frac(dT < 1)`（8 文サンプルでは 14.65%）
- `ceil(dT)` の最大値 → `clip_[1,80]` の上限飽和が実際に起きないことの確認
- `_` PAD とそれ以外の duration 分布を**分けて**（B-8）
- `Σ ceil(dT)` と `Σ clip_[1,80](round(s_v · r_i))` の総フレーム比を 1 にする `s_v` の最小二乗解

---

## 5. Phase C（旧 Phase 2〜4）— 生徒の実装 ✅ 実装完了

> **4 段すべて実装しスモークテストを通した。** 本学習は vast.ai。
> ⚠️ **これは古い**。本学習は手元の M4 Max で完結した（D-027）。
>
> | 成果物 | 状態 |
> |---|---|
> | `src/saanotts_jp/_param_reference.py` | ✅ 4 モジュールが論文の Table I と delta 0 |
> | `src/saanotts_jp/losses.py` | ✅ 式2/3/5/6/7。性質テスト **26 項目**（`scripts/test_losses.py`） |
> | `scripts/train_student.py` | ✅ 4 段のループ。実データ 200 発話で全 stage の損失が下降 |
> | **本学習** | ⏸ vast.ai 待ち |
>
> スモークテスト（heldout 200 発話 / 各 12 step / MPS、M-22）:
>
> | Stage | 損失 | 変化 | ms/step |
> |---|---|---:|---:|
> | 1 Duration `Dα` | 式2 | −54.8% | 58 |
> | 2 Acoustic `Eρ`+`Aβ` | 式3 | −53.2% | 81 |
> | 3 Decoder `Gγ` | 式5 | −20.6% | 58 |
> | 4 Joint | 式6 | −5.2% | **39** |
>
> （M-34。判別器を凍結版に差し替え、iSTFT の `length` と λ を直したあとの値。
> Stage 4 が 12 → 39 ms になったのはマルチスケール判別器で forward が 3 回になったため）
>
> ⚠️ **「回ることの確認」であって品質の確認ではない。** 12 step では何も学習していない。
>
> **本学習で決めること**:
> 1. `λ_Δ / λ_s / λ_T` — 勾配整合で初期値は決めた（D-021）。`λ_n` / `λ₂` は
>    閉形式が出たので**探索不要**（実行時に計算する）
> 2. 学習ステップ数 / lr スケジュール / バッチサイズ — **論文に記載が無い**
> 3. 学習曲線 512 / 5,000 / 20,946 の 3 水準（論文は 2 点しか持っていない）
> 4. `β`（式7）— **聴取で決める**。集約スコアはむしろ下がる（4.09 → 3.92）
>
> ✅ 判別器の構造は D-025 で決着（`scales=3, base_channels=16` = 94,755 params）。
> ⚠️ ただし**正規化 3 種に測れた差は無い**ので、`L_adv` が発散したら
> `norm="weight"` に切り替えて測り直す。

### D0: ターゲット tier ✅ 決着済み (2026-08-26)

**成果物は 567 K embedded c-line のみ**（D-007）。ブラウザは piper-plus の
WebAssembly で解決済みなので対象外。

| tier | Params | 論文 SCOREQ | 本プロジェクトでの位置づけ |
|---|---:|---:|---|
| **embedded c-line** | **0.567 M** | 2.54 | **唯一の成果物**（ESP32） |
| quality z-line | 1.396 M | 4.09 (β=0) / 3.92 (β=6) | **検証用の足場**。日本語でレシピが効くかを速く測り、567 K との差分を出すためだけに作る |

順序としては **1.4 M を先に**作る。検証サイクルが速く、c-line 特有の `Eρ` / 40ch
ボトルネックを後回しにできるため（z-line は hinge adversary（式4）が追加になる）。
ただし**成果物は 567 K** であることを見失わないこと。

**判断を先送りできる分割**: `zT` / `dT` は EMA 非依存で bit 一致するので、
c-line / z-line ターゲットを先に固め、`yT`（EMA の有無で SNR 14.5 dB 差）の
方針を後回しにする進め方が取れる。

### D0-b: メモリ ✅ 見積もり完了 (2026-08-26)

I2S 逐次出力なら arena **約 96 KB**（SRAM 512 KB のうち 416 KB が残る）。
フラッシュは重み 664 KB + G2P 877 B。**メモリを理由に中止する材料は無い**（M-16）。
実機測定は C99 コアと量子化済み生徒が出来てから。

### Phase 2: Duration `Dα`

- 損失: `L_d = Huber_0.25(ℓ̂, log dT) + λ_T [log Σ r_i − log Σ dT_i]²`
- **完了条件**: 教師 `dT` に対する held-out の総フレーム相対誤差 < 3%、音素あたり Huber < (教師 `dT` の分散で正規化して) baseline 比 X 以下
- **必須のチェック**: `minimal_pairs_ja` で、教師が区別している 20 ペアのうち生徒が何ペアを区別できるか（`ceil(d̂)` の系列が異なるか）。**平板型は「核なし」として正しく扱われているか**
- **設計判断 D2**（B-8）: `_` PAD の duration をどう扱うか
- **設計判断 D4**: アクセント記号トークンだけで足りるか、A1/A2/A3 の 3 スカラーを足すか。**コストは `Conv1d(3, 32, k=1)+bias = 128 params`（36,164 の 0.35%）** なので、評価で必要になったら即入れてよい

### Phase 3: Acoustic `Aβ` + c-line

- 損失 (式3): `L_c = ‖ĉ−cT‖₁ + λ₂‖·‖₂² + λ_n‖N_T(ĉ)−N_T(cT)‖₁ + λ_Δ‖Δĉ−ΔcT‖₁ + λ_s L_stat`
- **⚠️ `λ₂, λ_n, λ_Δ, λ_s` は論文に値が無い。チューニング対象**
- **完了条件**: oracle-decoder 経路（教師 `zT`/`cT` → 生徒 `Gγ`）と full-student 経路で平坦度を比較し、劣化の帰属先が特定できる

### Phase 4: Decoder `Gγ` + joint

- 損失 (式5/6)。公開重み `(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)`
- `R` = FFT {512, 1024, 2048} × hop {128, 256, 512}
- **⚠️ 未確定**: iSTFT のフレーミング規約（§6 参照）
- **完了条件**: 3 経路（teacher / oracle-decoder / student）の平坦度が `teacher ≈ oracle > student` か `teacher > oracle` かで欠陥の帰属が言える

### ✅ Phase 5: 摩擦音ノイズ注入 `β` の決定（式7）— **決着**（M-60 / D-038。以下は当初計画）

```
z̃_{t,k} = ẑ_{t,k} + 1[x_t ∈ S_ja] · β · σT_k · ε_{t,k},  ε ~ N(0,1)
S_ja = {s, sh, ts, ch, z, j, h, hy, f, I, U}       ← A/E/O は 0 出現なので除外
```
（実装は `src/saanotts_jp/losses.py` の `S_JA`。⚠️ 上の旧記述にあった `v` は
実装に入っていない — コーパス出現 526 と少なく、外来語専用のため）
⚠️ **破擦音 `{ts, ch}` を含めるかは未決**。閉鎖＋摩擦の複合なので閉鎖部への注入は劣化しうる。区間後半だけの時間マスクが要るかもしれない（論文はこの区別をしていない）。

**スイープ済み（M-40）**: β ∈ {0,2,4,6,8} を n=16 で測った。

| β | J(β) | fricative SFM 比 | SCOREQ 比 | ガード |
|---:|---:|---:|---:|---|
| **0** | **0.0165** | 1.0006 | **0.6186** | ✅ |
| **2** | 0.0229 | 0.9965 | 0.5988 | ✅ |
| 4–8 | 0.023–0.041 | 0.98–0.95 | 0.578–0.515 | ❌ |

**β を上げると SCOREQ が単調に下がる**（論文の 4.09 → 3.92 と同じ傾向）。

⚠️ **この生徒には式7 が要らない可能性が高い。** 論文の生徒は sibilant の SFM 比が
0.857 と低くそこを補うのが目的だったが、本実装は **β=0 で既に 1.0006** と一致している。

**聴取セットは作ってある**（`reports/listening_beta/`、40 試行 / 順序・左右ランダム /
2 反復）。`scripts/score_listening.py` が二項 CI と内的一貫性を出す。

**3 段構えで決める**:
1. **主目的（最大化ではなく一致）**: `J(β) = Σ_c w_c |SFM_ratio_c(β) − 1|` を最小化。`SFM_ratio_c = SFM_c(student)/SFM_c(teacher)`。**「平坦度が高いほど良い」にしない**（際限なくノイズを足す解に落ちる）
2. **ガードレール**: `SCOREQ_ratio(β) ≥ 0.95 × SCOREQ_ratio(0)` / `UTMOS_ratio(β) ≥ 0.975 × baseline` / `ΔCER(β) − ΔCER(0) ≤ +0.01`
   ⚠️ **未検証**: この 2.5% / 5% の差を 95% CI で分離するのに何発話必要かの power 計算が無い。**UTMOS は無音パディングだけで平均 +0.11（ベース 1.6–2.0 に対し約 6%）動く**ので、閾値がノイズに埋もれる可能性がある。**β スイープの前に power 計算を実施する**
3. **タイブレーク = A/B (CMOS) 聴取**。ガードを通った上位 2–3 候補、摩擦音・無声化母音高密度 20 文、順序＋左右ランダム、評価者 10 名以上、同一刺激反復 2 回で内的一貫性。選好比の二項 95% CI が 0.5 を跨いだら **β を小さいほうに倒す**

**完了条件**: `eval/out/beta_selection_<date>.json` に採用 β・全指標・聴取の生 CSV が凍結される

### Phase 6: 量子化 ✅ シミュレーション完了（M-39）

- symmetric int8 / per-output-channel、activations は per-frame
- **Embeddings, normalization affines, iSTFT support code は fp32 のまま**（論文 `sanoTTS.txt:261-264`）

| blob | 本実装（語彙 57） | 論文（語彙 157） | 差 |
|---|---:|---:|---:|
| blob1 (duration + acoustic) | 263,828 B | 280,288 B | −16,460 |
| blob2 (decoder) | 360,864 B | 399,544 B | **−38,680 (−9.7%)** |
| 合計 | **624,692 B** | 679,832 B | −55,140 |

**blob1 の差は語彙 157→57 でほぼ説明がつく。**
⚠️ **blob2 の −9.7% は語彙と無関係**なので、**decoder の構成が論文と違う可能性を示す**
（§6「未使用の最強制約」で予告していた検算がここで効いた）。

量子化誤差: log_d 37.9 dB / c 38.2 dB / **波形 25.8 dB**。
⚠️ **`d` の完全一致は 8 本中 3 本**。`clip[1,80](round(·))` の丸め境界で 1 フレームずれる。

⚠️ **PTQ のシミュレーションであって int8 カーネルではない。** C コアはまだ fp32 で読む
（Phase D-3）。実テキストでの品質劣化も未測定（プローブはランダム音素列）。

---

## 6. 生徒モデルの層構成（逆算結果）

**参照実装**: [`src/saanotts_jp/_param_reference.py`](../../src/saanotts_jp/_param_reference.py)
（4 モジュールすべて delta 0、end-to-end forward 通過、M-17）。学習コードは
[`scripts/train_student.py`](../../scripts/train_student.py)。

⚠️ **delta 0 は「論文と同じパラメータ数」であって「論文と同じ層構成」ではない。**
下記の反証と未確定項目はそのまま残っている。本学習で品質が出ないときに
真っ先に疑うのはここ。

| モジュール | 目標 | 構成後 | 判定の強さ |
|---|---:|---:|---|
| `Eρ`（学習専用） | 14,952 | 14,952 | **実質的に一意**（代数 + 広い族の全探索で hit 1 件） |
| `Dα` | 36,164 | 36,164 | **⚠️ 前提が反証済み**（下記） |
| `Aβ` | 199,536 | 199,536 | **非埋め込み部 192,000 は確実。内訳は非一意** |
| `Gγ` | 331,308 | 331,308 | **⚠️ 狭い探索箱の中で一意にすぎない**（下記） |
| 合計 | 567,008 | 567,008 | 一致 |

### `Eρ`（唯一の実質一意解）

| 層 | 形状 | params |
|---|---|---:|
| `Conv1d(192, 64, k=1) + bias` | 192×64 + 64 | 12,352 |
| `Conv1d(64, 40, k=1) + bias` | 64×40 + 40 | 2,600 |
| **合計** | | **14,952** ✅ |

`233h + 40 = 14952 → h = 64` が唯一の整数解。norm も活性化パラメータも入らない。デプロイ時は実行されないので 567,008 に含めない。

### `Aβ`（非埋め込み部が確実）

**最重要の導出**: `199,536 − 157×48(=7,536) = 192,000 ちょうど`。
埋め込み次元 48 は論文の「30 エントリで +1,440 params」から直接 `1440/30 = 48`。`567,008 − 1,440 = 565,568` が論文の earlier count と一致。

採用案（forward 通過）:

| 層 | 形状 | params |
|---|---|---:|
| `Embedding(157, 48)` | 157×48 | 7,536 |
| token block ×3（音素レート） | `Conv1d(48,48,k=5)+b ×2 + LN(48)` | 69,696 |
| length regulator | `repeat_interleave(h, d̂)` | 0 |
| ⚠️ 音素内位置 `Embedding(88, 48)` | 88×48 | 4,224 |
| frame block ×5（86.13 fps） | 同構造 | 116,160 |
| `Conv1d(48, 40, k=1)` bias なし | 48×40 | 1,920 |
| **合計** | | **199,536** ✅ |

**⚠️ この内訳は非一意**（`leftover=0` の解が複数、`leftover<=400` で 1,003 件）。確実なのは「非埋め込み 192,000」「1 ブロック ≈ 23,200–23,800 = 幅 48 で kernel-5 のフル conv 2 本相当」だけ。
**⚠️ 位置埋め込み 88 エントリは論文に根拠がない選択。** しかも int8 blob の検算はこれを**外す**方向を支持する（下記）。

### `Dα` ⚠️ 前提が反証済み

| 層 | 形状 | params |
|---|---|---:|
| ⚠️ `Embedding(157, 32)` | 157×32 | 5,024 |
| block ×3 | `Conv1d(32,32,k=5)+b ×2 + LN(32) + LayerScale γ` | 31,107 |
| `Conv1d(32, 1, k=1) + bias` | 32×1 + 1 | 33 |
| **合計** | | **36,164** ✅ |

**⚠️ 反証 1: `Dα` に `Embedding(157,32)` を置く構成は論文の勘定と矛盾する。**
論文は「deployed acoustic vocabulary は 157 エントリで、研究構成より 30 多く、**1,440** embedding params を足す。よって shipped graph は 565,568 ではなく **567,008**」と書く。`567,008 − 565,568 = 1,440 = 30×48` **ちょうど**で、増分は全部 acoustic 埋め込みに帰属している。もし `Dα` が同じ語彙の 32 次元埋め込みを持ち V が 127→157 に増えたなら、duration も +960 増えて総増分 2,400、研究構成の合計は 564,608 になり論文と合わない。
**→ `Dα` のパラメータ数はこの語彙変更に不感である。** 日本語適用の指針「`Dα` は `36,164 + (V−157)×32`」は未検証の仮定の上に乗っている。

**⚠️ 反証 2: V=157 は導かれていない、選ばれている。**
「`mod 32` で `2×conv5+nLN` 族のうち埋め込み予算が整数になるのは nLN=1 の V=157 だけ」は、スクリプトが自分で「必ず必要」と結論した 3 スカラーを引き忘れた誤読。正しく引くと `nLN=0 → V=163` / `nLN=1 → V=157` / `nLN=2 → V=151` の **3 つとも整数**。

**⚠️ 反証 3: 「3 個の追加スカラーが必ず必要」も言い過ぎ。**
`mod 32` が強制するのは `head + スカラー ≡ 4 (mod 32)` という合同式だけ。`Conv1d(32,4,1)+b = 132 ≡ 4` なら**スカラー 0 個**で成立する。

**→ Phase 2 の着手前に、研究構成 565,568 の内訳を `Dα` 側でも仮定して総当たりし直す。** 日本語で真っ先に触るパラメータがまさにここ。

### `Gγ` ⚠️ 探索箱が狭い

採用案 C（PyTorch 検証済み、delta 0）:

| 層 | 形状 | params |
|---|---|---:|
| in `Conv1d(40, 76, k=3) + bias` | 40×76×3 + 76 | 9,196 |
| block ×5: `dwConv1d(76,76,k=7,groups=76)` bias なし | 76×7 | 532 |
| block ×5: pw `Conv1d(76, 304, k=1) + bias` | 76×304 + 304 | 23,408 |
| block ×5: pw `Conv1d(304, 76, k=1) + bias` | 304×76 + 76 | 23,180 |
| block ×5: rank-12 down `Conv1d(40, 12, k=1) + bias` | 40×12 + 12 | 492 |
| block ×5: rank-12 up `Conv1d(12, 76, k=1) + bias` | 12×76 + 76 | 988 |
| block ×5: LayerScale γ | 1 | 1 |
| ⚠️ head `Conv1d(76, 48, k=1) + bias` | 76×48 + 48 | 3,696 |
| head `Conv1d(48, 1539, k=1) + bias` | 48×1539 + 1539 | 75,411 |
| **合計** | | **331,308** ✅ |

**確かなこと**: 位相 `1,026 = 513 × 2` は (cos, sin) 2 座標で、`n_fft=1024` の片側ビン 513 と厳密に整合。出力 `513 + 1026 = 1539` ch。`hop 256 @ 22,050 Hz = 86.133 fps`。

**⚠️ 反証 1: 「論文の字義解釈は算術的に不可能」は dense 仮定込み。**
`350,664 > 331,308` は「pointwise が dense」という追加仮定の下での不可能性。**grouped pointwise なら収まる**（同じスクリプトが `g=2` で `delta = −91,585` と出力している）。
**⚠️ 反証 2: 「MMAC 28 が独立に裏づける」は成立しない。** 論文の 28 MMAC/s は「畳み込みを評価し、**かつ 1024 点 iSTFT を使って**、コストを 28 MMAC/s に下げる」という文で **iSTFT 込みの数字**。1024 点実 iFFT はざっと 0.9 MMAC/s なので、**採用案 C（327,300 weights = 28.19 MMAC/s）も iFFT を足せば 28 を超える**。同じ物差しで字義解釈を棄却するなら C 案も棄却される。判別力がない。
**⚠️ 反証 3: 「rank-48 ヘッドが exact 唯一解」は探索箱が狭い。** grouped / 低ランクの pointwise 拡張を一切列挙していない。per-layer で bias を変える、ブロックあたり 2 個以上のスカラー、GroupNorm も未探索。
**代替案 B**（拡張幅 `E=255` に落として dense head 維持、delta −62）も同等に妥当。

**⚠️ 反証 4: 論文には「Table I と本文 4 文」以外にも層仕様がある。**
`sanoTTS.txt:261-264`「Embeddings, **normalization affines**, and the inverse-STFT support code remain in floating point」は**正規化層の affine が存在すること**を明言する。ところが採用案 C は `nLN=0`（5 ブロックすべて正規化層なし）で、この記述と噛み合わない。しかも `Dα` / `Aβ` の採用解はブロックごとに LayerNorm を置いており、**同一論文の 3 モジュールで設計が不整合**。

### 未使用の最強制約: int8 blob のバイト数

論文は `Its two blobs occupy 280,288 and 399,544 bytes` と量子化スキーム（int8 per-output-channel、埋め込みと正規化 affine は fp32）を明示している。**これは候補構成を篩える実効的な制約なのに、逆算で一度も使われていない。**

粗い試算:
- 採用案の duration + acoustic ≈ **295.8 KB**（実測 280,288 B に対し **+5.5%**）
- decoder ≈ **360.9 KB**（399,544 B に対し **−9.7%**）
- **提案されている 88 エントリ位置埋め込み（fp32 で 16,896 B）を外すと blob1 が ≈278.9 KB になり、280,288 B と 0.5% 以内で一致する**

**→ この制約は「位置埋め込みを持たない `Aβ` 分解」を支持する方向に働く。Phase 3 着手前にこの検算を通す。**

### その他の未確定

| 項目 | 状態 |
|---|---|
| **iSTFT のフレーミング規約** | ⚠️ 参照実装は `center=True` で `256·(T−1)` サンプルを出す（`c[1,40,14] → pcm[1,3328] = 256×13`）。論文の golden utterance は **100,096 = 391×256** と **34,304 = 134×256** で **256 の厳密な倍数**。`sanoTTS.txt:257` の「periodic overlap-add envelope」と合わせると、**デプロイ版は非 center 方式で `256·T` を出す可能性が高い**。教師ラベル（piper-plus の hop 256 出力）とのアラインメントに直結するので、蒸留のフレーム対応表を作る前に決める |
| **総計 45 MMAC/s との整合** | ⚠️ 採用構成は 39.7 MMAC/s で 12% 不足。しかも 45 は「**token block も含め 8 ブロック全部をフレームレートで動かす**」読み（44.66）のほうがよく合う。これは「token block = 音素レート、length regulator を token/frame の間に置く」という採用解釈を否定する材料になる |
| **`μ_T` / `σ_T` の扱い** | ⚠️ 式(3) の `N_T` と式(7) の `σT_k` はチャネルごと 40 個（z-line なら 192 個）の定数。デプロイグラフに含まれるのか（= 567,008 の内訳のどこか）、外部定数なのかが未決。パラメータ勘定に効く |
| **rank-12 conditioning の共有/per-block** | down 射影(40→12) をブロック共有にすると 1,968 params 浮き、別ノブで埋める必要がある |
| **位相の正規化** | `cos²+sin²=1` に正規化するか、L2 罰則か。論文に記述なし |
| **quality z-line の幅** | ⚠️ 「decoder 76 → 137〜146」は根拠薄（`137` を出したスクリプト出力が存在せず、モデル式が c-line と別ファミリー）。**R8 z-line (600,097) は参考にしない** — 論文が「waveform-domain transposed-convolution decoder, 522 MMAC/s」と明記する別系統 |
| **日本語での V** | 日本語単言語なら音素在庫を 100–120 に絞れる（縮む方向）。ただし `Dα` 側の係数は上記の反証により未確定 |

---

## 7. 評価パイプライン設計

### 7.1 全体方針

- **すべて教師比で報告する。** SCOREQ / UTMOS / 平坦度は `ratio = metric(student)/metric(teacher)`、CER は差分 `ΔCER`。
  UTMOS も含む（**UTMOS22 は日本語で訓練されていない** — VoiceMOS2022 main=BVCC 英語 / OOD=BC2019 中国語）。
- **3 経路を必ず同時に測る**（論文が sibilant 欠陥を発見できた唯一の理由）:
  1. `teacher` — 教師 `yT`
  2. `oracle-decoder` — 教師潜在 → 生徒 `Gγ`
  3. `student` — フル生徒（β 変種込み）
  平坦度が `teacher ≈ oracle > student` なら欠陥は acoustic、`teacher > oracle` なら decoder。
  **⚠️ 未決**: oracle-decoder の入力は `zT`(192ch) か `cT`(40ch) か。c-line の生徒 decoder の入力は本来 40ch なので、`Eρ` を通すのか `zT` を直接使うのかで測る対象が変わる。**Phase 3 着手前に定義する。**
- **環境は piper-plus と完全分離**（`.venv-eval`）。piper-plus の `.venv` には何もインストールしない。

### 7.2 依存（実測で必要と分かったピン）

```toml
requires-python = ">=3.12,<3.14"
dependencies = [
  "scoreq==1.0.1", "torch>=2.4,<2.14", "torchaudio>=2.4,<2.14",
  "onnxruntime>=1.18", "soundfile", "librosa", "numpy>=2.0", "scipy",
  "pyworld==0.3.5", "setuptools<81",     # pyworld 0.3.5 は pkg_resources を import する
  "jiwer>=4.0", "transformers>=4.44", "tqdm",
]
```

**採用しない**: `torchcodec` を依存に入れる（soundfile 差し替えで解決済みなのに torch バージョンへの強い結合を持ち込む）。
**必ず入れる**: `torch`（`scoreq` は基本依存に入れていないのに `scoreq.py:1-9` が無条件 import する）。
**⚠️ `torchaudio.load` は torchaudio 2.11 で torchcodec 必須になった**（`ImportError: TorchCodec is required for load_with_torchcodec`）。`scoreq` / `f0_extraction` / piper-plus の UTMOS 実装が全部この経路。**soundfile に差し替える**:

```python
import torchaudio, soundfile as sf, torch
def _load(p, *a, **k):
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    return torch.from_numpy(x.T.copy()), sr
torchaudio.load = _load          # ★ import scoreq より前に
import scoreq
```

> **⚠️ `import scoreq` した時点で 378 MB が落ちる。** `scoreq/__init__.py` は全 2 行で `from .scoreq import Scoreq` / `scoreq = Scoreq()` — デフォルト `data_domain='natural'` の Scoreq が eager に構築され `adapt_nr_telephone.onnx` を DL する。`synthetic` を作るとさらに 378 MB。**初回コストは 378 MB ではなく合計 756 MB。** CI キャッシュのサイズ見積りに注意。

### 7.3 各指標

| 指標 | 実装 | 注意 |
|---|---|---|
| **SCOREQ** | `Scoreq(data_domain='synthetic', mode='nr')`。高いほど良い | TTS は `synthetic` 固定。`ref` モードは ONNX 経路で `np.linalg.norm(test−ref)` で PyTorch の `torch.cdist` とスケールが違う可能性。⚠️ 長さ依存性・向きが未検証なので**主指標は NR** |
| **UTMOS** | `torch.hub.load("tarepan/SpeechMOS:v1.2.0","utmos22_strong")`。22.05 kHz を**直渡し**（内部で 16k に落とす） | 外部リサンプルしない。`fairseq` 不要。102,772,865 params |
| **無音パディング** | **全システムで 300 ms に固定** | 実測で UTMOS が平均 +0.11 動く（β の効果量 0.09 と同オーダー）。ゲインも統一 |
| **CER** | `jiwer.cer()`。**`jiwer.wer()` は日本語で常に 1.0 になるので使わない** | ⚠️ ASR / CER 経路は**一度も実行されていない**（`kotoba-tech/kotoba-whisper-v2.0` と `openai/whisper-large-v3-turbo` は「HF に存在確認済み」だけ） |
| **CER の参照** | ⚠️ 「ASR 出力を音素列に変換して比較」案は **B-1 の中国語誤ルーティングを ASR 出力側にも持ち込む**（ASR が数字を算用数字で出せば同じ経路で壊れる）。**先に B-1 を解決してから設計する** | 正規化は NFKC + 約物除去。⚠️ その実効果は「0.30→0.12」ではなく **0.154→0.12**（前者は別々の文ペアの値を並べたもの） |
| **平坦度プローブ** | §B-6 のグリッドで `(n_fft, guard, power)` を分離指標で選ぶ | ⚠️ 推奨されていた `512/guard=1/power=2` は実教師データで反証済み |
| **F0 / アクセント** | `pyworld.harvest` + `stonemask`、`frame_period = 1000*256/22050 = 11.60998 ms` | **`dio` ではなく `harvest`**（実測 voiced 110/116 vs 65/116）。`harvest` は `n_frames+1` を返すので切る |

**音素クラス表** (`ja_classes.py`):
```python
FRICATIVE = {"s","sh","z","j","zy","h","hy","f","v"}
AFFRICATE = {"ts","ch"}
DEVOICED  = {"I","U"}                      # ⚠️ A/E/O は 23,271 行で 0 出現
STOP      = {"k","t","p","b","d","g","ky","gy","ty","dy","py","by","cl","q"}
NASAL     = {"n","m","ny","my","N_m","N_n","N_ng","N_uvular"}
VOWEL     = {"a","i","u","e","o","a:","i:","u:","e:","o:"}
```

**⚠️ 評価セットの必須要件**: 教師 8 発話では無声化母音のフレームが **n=3** しか取れず、全クラス合計でも約 142 フレームしかない。`sibilant_dense_ja.tsv` を別立てし、**クラスごとに最低 300 フレーム**を確保する。それまで教師ベースラインを凍結しない。

### 7.4 集約と報告

- 発話ごとに指標を出し、**発話単位の bootstrap（10,000 回）で比の 95% CI**
- `diverse_ja` は最低 200 文、**テンプレート文禁止**（論文の 512 行 narrow set 問題の再現防止）
- ⚠️ **必要 n の power 計算を先にやる**（§Phase 5 のガードレール参照）

### 7.5 piper-plus 資産の流用判定

| 資産 | 判定 | 理由 |
|---|---|---|
| `scripts/audio_quality_metrics.py` の指標計算 | **流用不可** | `cmd_compute` / `cmd_synthesize` が `return 2` のスタブ（:284-299）。CI も stub しか回していない |
| 同 `compute_diff` / `render_markdown` / `to_bencher_json` | **流用可** | 差分ハーネスの枠。`tests/scripts/test_audio_quality_metrics.py` に閾値回帰 / NaN / bencher の単体テストが 10 件超ある |
| `tools/benchmark/compute_metrics.py` の UTMOS | **要修正で流用可** | 実装は本物 (:129-182)。`torchaudio.load` を soundfile に置換必須。**失敗を -1.0 に潰す設計は捨てる** |
| 同 RMS/peak/silence/sample-rate チェック | **流用可** | 合成物の sanity gate |
| `tools/benchmark/generate_mos_survey.py` | **ほぼそのまま流用可** | 絶対 MOS 用。blind / randomize / seed / CSV・JSON 出力あり、外部 CDN 非依存。**A/B (CMOS) は無いので fork して追加**。base64 埋め込みで約 58.8 KB/音声秒 |
| `tools/benchmark/generate_samples.py` | **部分流用** | ONNX 前提なので合成本体は不可。**ディレクトリ規約 `{samples_dir}/{system}/{lang}/{text_id}.wav` と `generation_results.json` は必ず踏襲**（下流 2 ツールが無改造で動く） |
| `src/python_run/piper_plus/timing.py` | **流用不可** | ceil していないため累積ドリフト（36 音素で 193 ms）。※ これは「バグ」ではなく `docs/spec/phoneme-timing-contract.toml` で契約化されたユーザー向けタイムスタンプ API。教師フレーム整合には使えないだけ |
| `src/python/piper_train/f0_extraction.py` | **設計のみ流用** | hop 256 整合は正しいが pyworld 未導入 / dio 使用 / `torchaudio.load` 依存 |
| `src/python/jp_phoneme_map.py` | **使ってはいけない** | id 表が壊れている（`get_phoneme_id_map()` は 58 entry / max id 57、実パックの id は最大 87）。`PHONEME_TO_PUA` も 22 entry で 173 symbol 中 55 個を解決できない。canonical は `piper_plus_g2p.encode.pua.CHAR2TOKEN` (99 entry, 双方向, `check_pua_compat` 付き) |
| `scripts/evaluation/evaluation_texts_ja.txt` | **シードとして流用可** | 31 文。橋/箸/端・雨/飴を含む。⚠️ 疑問文 4 つはすべて素の `？` で `?!` `?.` `?~` は 0 件 |
| `scripts/audio_parity.py` | **Phase 6 で流用検討** | 階層判定の枠は C99 ゴールデンテストに使えるが、**Pearson 相関は未実装**（tier3 は SNR のみ、`mel_spec_max_mse` は閾値キーとしてしか存在しない）。論文の Pearson ≥ 0.98 は自作 |

---

## 8. リポジトリのディレクトリ構成案

```
./
├── CLAUDE.md                          # §1.3 の訂正を反映
├── pyproject.toml
├── docs/
│   ├── research/sanotts-jp-feasibility.md    # canonical。§1.3 の訂正を反映
│   ├── plan/phase0-1-implementation-plan.md   # 本文書
│   ├── decisions/                     # ADR。各判断の根拠と実測値を凍結
│   │   ├── D0-target-tier.md          # 567K vs 1.4M
│   │   ├── D1-frontend.md             # ★ multilingual vs 言語ピン留め（最優先）
│   │   ├── D2-pad-duration.md         # `_` PAD の duration の扱い
│   │   ├── D3-corpus-mix.md           # CV をどこまで使うか
│   │   ├── D4-accent-features.md      # アクセント記号のみ vs +A1/A2/A3
│   │   ├── D5-istft-framing.md        # center=True vs 非 center
│   │   └── D6-ema.md                  # yT に EMA を当てるか
│   └── notes/teacher-properties.md    # freeze_dp / emb_lang unify / 長さ上限
├── scripts/
│   ├── guard_piper_plus_readonly.sh   # git status / HEAD / worktrees の確認
│   ├── b1_probe_g2p_routing.py        # ★ B-1
│   ├── b4_length_hist.py              # ★ B-4
│   ├── gate_ja_only.py                # ★ 全ラベル入力の言語ゲート
│   ├── fetch_corpus.sh
│   ├── remote_zip_extract.py          # JSUT の HTTP Range 抽出（zip64 フォールバック要）
│   ├── build_pool.py / preprocess_pool.py / build_splits.py / check_leakage.py
│   ├── phase0_verify_teacher.py       # ★ 書き直し（speaker=None / EMA / 実 prosody）
│   ├── gen_teacher_labels.py          # ★ 6 つの既知欠陥を修正
│   ├── verify_packs.py
│   ├── synth_teacher.py / synth_student.py
│   ├── make_survey_mos.py / make_survey_ab.py
│   └── run_eval.sh
├── src/saanotts_jp/
│   ├── frontend/                      # text → (phoneme_ids, prosody)。D1 の実装
│   │   ├── phonemize.py / strip.py / pua.py / gate.py
│   ├── teacher/                       # ckpt ロード・EMA・決定的推論
│   ├── data/                          # pack IO / manifest / channel_stats
│   ├── models/                        # Dalpha / Abeta / Ggamma / Erho（config ノブ化）
│   ├── losses/                        # L_d / L_c / L_G / L_joint
│   └── eval/
│       ├── audio_io.py                # soundfile ベース。torchaudio.load 差し替え
│       ├── align.py                   # ★ ceil(dT) 累積和。timing.py は使わない
│       ├── ja_classes.py / corpus.py
│       ├── m_scoreq.py / m_utmos.py / m_cer.py
│       ├── p_flatness.py / p_accent.py
│       ├── ratio.py / beta_sweep.py / report.py / cli.py
├── data/
│   ├── raw/ interim/ splits/ packs/{smoke,train_23k,train_14343,eval}/
├── eval/
│   ├── sets/{diverse_ja.tsv, minimal_pairs_ja.tsv, sibilant_dense_ja.tsv, smoke50_ja.txt}
│   └── out/{samples/, metrics.json, report.md, beta_selection_<date>.json}
├── reports/                           # ★ §2 の検証タスクの出力先
├── tests/
│   ├── test_prosody_align.py          # ★ B-2 の回帰テスト
│   ├── test_frame_identity.py         # ceil(dT) 恒等式
│   └── test_ja_gate.py
├── data-sources.yml                   # ライセンス台帳
└── vendor/                            # 必要になったときだけ git archive の抽出先
```

### ライセンス台帳 (`data-sources.yml`)

```yaml
- id: rohan4600
  license: {spdx: "CC0-1.0", verified: true, url: "https://github.com/mmorise/rohan4600"}
- id: ita-corpus
  license: {spdx: "CC0-1.0", verified: true, url: "https://github.com/mmorise/ita-corpus"}
- id: common-voice-ja-sentences
  license: {spdx: "CC0-1.0", verified: false,
            note: "repo は MPL-2.0。server/data/ja/ に個別 LICENSE 無し（3 URL とも 404 実測）。
                   docs/SENTENCES.md にも文そのものを CC0 とする明示なし。要一次ソース確認"}
- id: jsut-ver1.1-text
  license:
    per_subset:
      basic5000: ["CC-BY-SA-3.0 (Wikipedia)", "CC-BY-2.0 (Tanaka)", "CC-BY-SA-4.0 (original)"]
      utparaphrase512: "CC-BY-SA-4.0 (SNOW E4)"
      onomatopee300/countersuffix26/loanword128: "CC-BY-SA-4.0"
      voiceactress100/repeat500: "CC-BY-SA-4.0 (Voice Actress Corpus)"   # 評価から全除外
      travel1000: "CC-BY-SA-3.0 (NICT)"
      precedent130: "copyright-free"
    verified: true
    source: "jsut_ver1.1/LICENCE.txt"
  note: "音声は研究用途限定だが本プロジェクトはテキストのみ使用。
         LICENCE.txt に 'The text data were modified to read it easy.' とあり、
         VOICEACTRESS100 の JSUT 版と原典（声優統計コーパス）の字面同一性は未確認"
- id: distributable-subset          # PD/CC0 のみ ≈ 7,000 行。別 tag で管理
  members: [rohan4600, ita-corpus, "jsut:precedent130", synthetic-t2]
```

---

## 9. 残るリスクと判断待ち事項

### 9.1 判断待ち（Decision Records として凍結する）

> **✅ 決着済み**: D0（tier → 567 K, D-007）/ A-1 / A-2 / D1 / D3 /
> **C-1**（λ → D-021）/ **C-2**（判別器 → D-025）/ **D2**（PAD → D-018）/
> **D5**（iSTFT → D-022）/ **D6**（EMA → D-023）。

| ID | 判断 | 期限 | 状態 |
|---|---|---|---|
| ~~**D7**~~ | ~~`β`（式7 のノイズ注入強度）~~ → **β=0 で確定。式7 は不要**（D-038） | ✅ | 候補 β=0 と 2 を聴いて**差を聴き分けられなかった**（M-60）。C コアに `β` は無いので**実装への影響はゼロ**。⚠️ **聴取者 1 名**。⚠️ **上流（英語）は β=6.0 を採用**している（`../upstream-sanotts.md`）。うちの日本語指標では 6 は候補に残らなかった。**言語差か指標の違いかは切り分けていない** |
| ~~**D12**~~ | ~~PIE カーネルを実機なしで書くか~~ → **書いた**（M-57 / M-58） | ✅ | ⚠️ **かつて「toolchain が無いので待ち」としていたのは誤り**（C-033）。⚠️ **「本体は W8A8 への書き換え」も誤り**（C-034。W8A8 は既に実装済みだった）。本体は **intrinsic / アセンブリ**で、GCC は `-O2` で PIE へ自動ベクトル化しない（M-53）。QEMU で **bit 完全一致**、MAC の **99.40%** を被覆。⚠️ **速度は未測定** |
| ~~**D4**~~ | ~~アクセント記号トークンのみか、A1/A2/A3 を足すか~~ → **記号だけで足りる**（D-030） | ✅ | ミニマルペア 15 群 32 語で符号一致 **35/36**（M-44）。⚠️ **聴取はしていない** |
| ~~**D13**~~ | ~~DNSMOS を評価指標に加えるか~~ → **合否ゲートにせず、併記プローブとして採用**（D-034） | ✅ | 実人間の天井を先に取り、6 レーンを同じ発話集合で測定。**教師/人間 0.989**（SCOREQ 0.820 / UTMOS 0.758 と大きく違う）。⚠️ 上流の言う「SCOREQ で高・DNSMOS で低」のパターンは**出ていない**。⚠️ **陽性対照 G6 は FAIL** — 下がらないことを「劣化が無い」と読まない。⚠️ `speechmos` は UTMOS の torch.hub checkout と同名で衝突するので**別プロセス**で測る |
| **D14** | **decoder を教師で初期化するか** | 追試 E-2 → **D-033** | **追わない。** トポロジ差で初期化が定義できない（形状は 27/28 切り出せるが意味が対応せず `hout` は候補ゼロ）。代わりにギャップを分解した（M-49）。上流の「sub-400k は死んだクラス」は **357k R8（波形 transposed-conv）**の話で、331k iSTFT decoder のセルは論文に無い |
| ~~**D10**~~ | ~~int8 の activation スケール~~ → **W8A32（重みだけ int8）を既定**（M-43） | ✅ | W8A8 との差は 2.59 dB で**蓄積しない**（照合で実測）。切り替える利得がホストでは 0.86 倍しかないので保留 |
| ~~**D11**~~ | ~~FFT の実装~~ → **radix-2 自前 / float 版**（M-43） | ✅ | SNR 138.7 dB。double 版は 1.63 倍遅く ESP32 は単精度 FPU のみ |
| ~~**D9**~~ | ~~ストリーミングのチャンク長~~ → **CHUNK=8 / 各段が `[C][2*pad+8]` を持つ**（D-029） | ✅ | 受容野は実測で decoder ±16 / **acoustic ±20**（旧記述の ±10 は誤り）。⚠️ CHUNK=8 は最適化していない |
| ~~**C-1**~~ | ~~`λ₂ / λ_n / λ_Δ / λ_s` と `λ_T`~~ → **勾配整合で初期化。`λ_n`/`λ₂` は実行時算出**（D-021） | ✅ | 残る探索 `λ_Δ`/`λ_s`/`λ_T` は**目標に届いたので優先度低** |
| ~~**C-2**~~ | ~~判別器の構造~~ → **94,755 params**（D-025） | ✅ | ⚠️ 正規化 3 種に測れた差は無い |
| ~~**D2**~~ | ~~`_` PAD の扱い~~ → **特別扱いしない**（D-018） | ✅ | PAD はフレームの 53.76% |
| ~~**D5**~~ | ~~iSTFT のフレーミング~~ → **`center=True` + `length=T*256`**（D-022） | ✅ | 末尾ゼロ埋めも外した |
| ~~**D6**~~ | ~~`yT` に EMA を当てるか~~ → **当てる**（D-023） | ✅ | — |
| ~~**D8**~~ | ~~実行環境（vast.ai か手元か）~~ → **手元の M4 Max**（D-027） | ✅ | CUDA parity ゲートを回避できた |

### 9.2 未検証のまま残るリスク

**⚠️ 「未検証」を減らすことより、未検証だと分かっていることのほうが大事。**
決着したものは消さず、決着の仕方を残す。

| リスク | 状態 | 潰し方 |
|---|---|---|
| **ESP32 で実時間に間に合うか** | ⚠️ **fp32 のままだと 2.47× RT で間に合わない**（実測 η を転移、M-43） | **int8 + PIE カーネル**（D-3c'）。⚠️ **手元では速度を検証できない** |
| ~~レイテンシが一度も測れていない~~ | ✅ 手元 **0.023× RT**（M-43） | FFT 化で 9.8 倍。段別も直接計装で確認（Σ/全体 = 0.9998） |
| ~~ESP32 の RAM ピーク~~ | ✅ **196.9 KB / SRAM 512 KB の 38%** | 一括版と bit 一致。⚠️ fp32 かつ解析値（実機未測定） |
| ~~教師の事前学習テキストと pool の重複~~ → **決着（M-47）** | ✅ **看板の 24 文は重複ゼロ**。陽性対照 200/200 で検出力も確認。heldout の近傍 1 行は 24 文に含まれない | ⚠️ 照合は**表層テキストのみ**。音素列で一致するペアは未測定 |
| ~~アクセント型の再現性~~ → **決着（M-44 / D-030、v3 は M-59）** | ✅ 符号一致 **35/36**（v2）→ **37/37**（v3） | ⚠️ 起伏の大きさは **3 試行のみ**聴いて「同じくらい」（M-60。上限 56.2% = 弱い）。2 型の下降核は n=13〜16 でしか測れていない |
| **`Gγ` の構成が論文と違う** | ⚠️ 疑いが強まった | int8 blob2 が論文比 **−9.7%**（語彙と無関係、M-39）。§6 の逆算をやり直す材料 |
| **総計 45 MMAC/s との 12% 乖離** | ⚠️ 未解決 | 自前計算で 43.6（M-26）。token block を音素レートに置く解釈自体を再検討する材料 |
| **β ガードレールの検出力** | ⚠️ power 計算なし | UTMOS はパディングだけで 6% 動く。必要 n を先に見積もる。スイープは n=16 で回した（M-40） |
| **UTMOS / SCOREQ の日本語での単調性** | ⚠️ 未検証 | B-5 で**スケールの圧縮**は確認した（実人間 = UTMOS 2.305）が、**順位相関は未確認**。⚠️ v1/v2 で **2 指標が食い違った**（UTMOS 比 0.70 / SCOREQ 比 0.39）ので実際に問題になっている |
| **集約指標が sibilant 欠陥を検出できるか** | ⚠️ 構造的に不利 | SCOREQ / UTMOS とも内部で 16 kHz にリサンプルするので 8 kHz 超は捨てられる。**「集約指標が下がらないから β は安全」という読みを禁止**し、プローブを必ず併記する |
| **無声化母音の区間境界** | ⚠️ 未測定 | 先行子音との境界が音響的に曖昧。`ceil(dT)` 境界とのずれ、guard の要否を実データで決める |
| **A/B 聴取の被験者確保** | ⚠️ 未定 | 日本語母語話者 10 名以上が理想。⚠️ **現状のセットは 1 名分**（`reports/listening_beta/`） |
| **`d_hat` の丸め規約** | ⚠️ 部分的 | C の `roundf`（half-away-from-zero）と `torch.round`（half-to-even）が .5 で割れる。golden test の 1 文では発生しなかっただけ |
| ~~SCOREQ が未導入~~ | ✅ PyPI にあった（C-016） | — |
| ~~ASR / CER 経路~~ | ✅ 実施した（M-38） | ⚠️ **表記 CER では符号が逆転した**（C-023）。かな CER が主指標 |
| ~~int8 blob 検算~~ | ✅ 実施した（M-39） | 上の「`Gγ` の構成」に化けた |
| ~~`Dα` が音素埋め込みを持つか~~ | ✅ 実装して golden test を通した | 参照実装のとおりで動いている |
| ~~`95e74cb2` が dangling~~ | 低 | piper-plus で `git gc --prune` が走ると消える。HEAD と推論差分ゼロ |

### 9.3 環境上の落とし穴（記録）

- **zsh**: `git show $t:src/python/...` は `$t:s...` がパラメータ修飾子として解釈されパスが壊れる。**`git show "${t}:src/python/..."` と括る**。
- **piper-plus は読み取り専用**。`checkout` / `commit` / ファイル編集の禁止。作業後は必ず `git status --porcelain` が 0 行、`.git/worktrees` 不在を確認する。
  **これは `.claude/hooks/guard_bash.py` が PreToolUse で機械的に強制する**（`permissions.deny` は Edit/Write しか止められないので、シェル経由をこれでカバーする）。
- **Python は `uv` 経由**（D-012）。依存追加は `uv add`。hook が `pip install` を deny し、uv を経由しない python を ask にする。
- **教師 ckpt の再ダウンロード禁止**。既にキャッシュにある。

---

## 10. 残りのタスク（2026-08-30 現在）

**品質もメモリも目標に届き、追試 E-1 / E-2 / E-2b も決着した。残るのは速度だけ。**

**2026-08-30、端末でかなを自由入力できるようにした**（M-63 / D-040）。
それまでのファームはビルド時に焼き込んだ 1 文しか喋れなかった。
起動時に錨の 1 文を喋ったあと `かな> ` プロンプトで 1 行受ける。
**QEMU の UART に実際に打ち込み、デモ文と同じ中間表現から起動時と同じ checksum
（`0x04de91103a0e49f9`）が出ることを確認した。**
⚠️ **実機の UART では未検証。速度は相変わらず一切測れていない。**

**⚠️ 2026-08-28、P-1 の前提が 2 つとも間違っていたことが判明した:**
**(1) 「toolchain 待ち」ではなかった**（導入していなかっただけ。M-54 / C-033）。
**(2) 「intrinsic を足すだけ」ではなかった** — 現行 int8 カーネルは **W8A32 で積和が fp32**
であり、PIE では速くならない。⚠️ **ただし W8A8 版（`saan_conv1d_i8a`）は既に実装済みで、
「書き換えが本体」は誤りだった**（C-034）。**本体は PIE の intrinsic / アセンブリ**（M-53）。

**E-2b の答え（M-52）: `Gγ` は容量律速ではない。** params を 43% 増やしても
gap は **+0.0006**（CI [−0.013, +0.015]）しか動かない。効くのは学習量で、
steps を 20k → 40k にすると **−0.1090**（ノイズ床 0.0812 超え）。
**打つ手は decoder の作り直しではなく Stage 3 の延長。**

| # | タスク | いま着手できるか | 状態 |
|---|---|---|---|
| **P-1** | **PIE カーネル** | ✅ **実装完了**（M-57 / M-58）＋ **QEMU で全経路 bit 一致**（M-62） | ⚠️ **速度は未測定**。QEMU では測れない。**実機待ち**（手順は `esp32/TESTING.md`） |
| ~~P-2~~ | ~~`β` の聴取決定~~ | ✅ **決着**（M-60 / D-038） | **β=0 で確定。式7 は不要**。⚠️ 聴取者 1 名 |
| ~~E-2b~~ | ~~`Gγ` の幅スイープ~~ | ✅ **決着**（M-52） | **容量律速ではない。学習律速。** |
| ~~E-2c~~ | ~~W=56 が無料か~~ | ⏹ **中止**（D-036） | 結果がどちらでも打つ手が変わらないため |
| ~~E-1~~ | ~~DNSMOS~~ | ✅ **決着**（M-50 / D-034） | 上流の主張は**再現しなかった**。⚠️ **陽性対照 G6 は FAIL** |
| ~~E-2~~ | ~~decoder の教師初期化~~ | ✅ **決着**（M-49 / D-033） | **教師初期化は定義できない**。代わりにギャップを分解した |
| ~~P-3~~ | ~~端末でかなを自由入力する経路~~ | ✅ **実装完了**（M-63 / D-040）＋ **QEMU の UART で実測** | ⚠️ **実機の UART では未検証**。手順は `esp32/TESTING.md` |
| ~~P-4~~ | ~~外の人が実際に動かせる状態にする~~ | ✅ **完了**（M-64 〜 M-67 / D-041） | piper-plus も教師も無しで合成可。**焼くだけの firmware** を v0.1.1 で配布。⚠️ 手順書の欠陥 2 件を修正（M-66） |

### P-1: PIE (SIMD) カーネル — **着手した。ただし想定より大きい**

**唯一の未達項目。** fp32 のまま移植すると **2.47× RT** で実時間に間に合わない（M-43）。

⚠️ **2026-08-28、この節の前提が 2 つとも間違っていたことが判明した。**

#### 誤り 1: 「toolchain 待ち」ではなかった（M-54 / C-033）— **QEMU まで動いた**

⚠️ **さらに: QEMU が ESP32-S3 の PIE を実装しており、実機なしで正しさを検証できる**
（M-56）。「書いても構文が通ることすら未確認」という前提も誤りだった。
`esp32/pie_probe/` のプローブが `PIE PROBE: PASS` を出す。
**ESP-IDF 雛形も初めてビルドが通った**（`saanotts_jp.bin` = 267,968 B）。


「この環境に xtensa toolchain が無い」と書き続けていたが、**待っていたのは外部要因ではなく、
単に導入していなかっただけ**だった。ディスク空きは 194 GB あり、ESP-IDF は 2〜3 GB。

導入済み（2026-08-28）:

```bash
brew install ninja ccache dfu-util
mkdir -p ~/esp && cd ~/esp
git clone -b v5.5 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git
cd esp-idf && ./install.sh esp32s3
```

⚠️ **それでも実機のサイクル数は測れない**（ESP32-S3 ボードが無い）。
できるのは **コンパイル** と **逆アセンブルでの PIE 命令の確認**、命令数の計数まで。

#### 誤り 2 とその訂正: **GCC は PIE へ自動ベクトル化しない**（M-53 / C-032 / C-034）

⚠️ **ここで私は一度間違え、訂正した。経緯を残す。**

まず `saan_conv1d_i8`（88-91 行）が **W8A32**（重みだけ int8、積和は fp32）なのを見て、
「**W8A8 への書き換えが本体**」と書いた（C-032）。**これは誤りだった**（C-034）。

`csrc/saanotts_int8.c` には **2 系統**あり、**W8A8 は既に実装・テスト済み**である:

| 関数 | 方式 | 積和 |
|---|---|---|
| `saan_conv1d_i8` / `saan_dwconv1d_i8` | W8A32 | `yo[t] += wv * xi[t+sh]`（fp32） |
| **`saan_conv1d_i8a` / `saan_dwconv1d_i8a`** | **W8A8** | `a32 += (int32_t)wk[i]*(int32_t)qu[i]`（int32） |

**有効なのは逆アセンブルの実測の方**（`-O2` / ESP32-S3）:

| 関数 | PIE (`ee.*`) 命令数 |
|---|---:|
| `saan_conv1d_i8`（W8A32） | **0** |
| **`saan_conv1d_i8a`（W8A8）** | **0** |
| `saan_dwconv1d_i8` / `_i8a` | 0 / 0 |

**W8A8 の int32 積和ループでも PIE 命令は 1 つも出ない。**
**GCC は `-O2` で PIE ユニットへ自動ベクトル化しない**ので、
**intrinsic かアセンブリを書くしかない**ことが確定した
（「書けば自動で出るかも」という可能性がこれで消えた）。

#### 正しい段階分け

| 段階 | 状態 |
|---|---|
| int8 **重み**の量子化 | ✅ 完了（M-45。flash 2,249,792 → 643,936 B、−71.4%） |
| toolchain の導入 | ✅ 完了（M-54） |
| **W8A8 カーネル**（活性化 int8 / int32 積和） | ✅ **実装・テスト済み**（`*_i8a`） |
| **W8A8 を本番経路に接続** | ✅ **済んでいた**。`saan_conv1d_w` が `SAAN_INT8_ACT` で切り替える（既定 0） |
| **W8A8 の知覚評価** | ✅ **知覚的に無料**（M-55。SCOREQ 差 +0.0049、CI が 0 を含む） |
| **QEMU での PIE 検証環境** | ✅ **できた**（M-56。`ee.vmulas.s8.accx` が正しく動く） |
| **PIE カーネルの実装** | ✅ **完了**（M-57）。**QEMU で bit 完全一致**を確認 |
| **全層への拡張**（活性化ストライドのパディング） | ✅ **完了**（M-58）。**69.0% → 99.40%**。出力は 24/24 で bit 一致、arena 増加 0 B |
| **出荷ファームでの有効化** | ❌ **未。** `esp32/components/saanotts_core/CMakeLists.txt` が `SAAN_PIE` / `SAAN_INT8_ACT` を定義していない。**W8A8 を採る決定が要る** |
| 実機でのサイクル実測 | ❌ **ボードが無い**（本物の待ち。QEMU では測れない） |

⚠️ **M-57 に書いた「77.1%」は誤りだった**（C-035。ベンチ 5 形状を分母にして
decoder ×5 / acoustic ×10 を落としていた）。**正しくは 69.0%**、パディング後 **99.40%**。
残る 0.60% は depthwise で、**チャネル方向のギャザーなので原理的に PIE に載らない**。

**P-1 の実装は終わった。⚠️ ただし「速くなった」とは一言も言えない** —
QEMU はサイクル精度ではないので**速度を一度も測っていない**。
M-43 の 0.088× RT は未検証の外挿のまま。**実機が唯一の残り。**

### ⚠️ 出荷ファームで有効化するかの判断（未決）

| 影響 | 内容 |
|---|---|
| SNR | 25.88 → **23.24 dB** ⚠️ ただし **SCOREQ では差が無い**（M-55。陽性対照つき） |
| メモリ | G1（自主基準 200 KB）を **1.1 KB 超過**（201.1 KB）。SRAM 512 KB の 39%、実機の静的 arena 208 KB には収まる |
| 増分の内訳 | **+4.2 KB は W8A8 そのもの。パディングの寄与は 0 B** |

⚠️ **`SAAN_INT8_ACT` の既定は 0（W8A32）のまま。** M-55 で知覚差が無いと分かったので
PIE を入れる段で 1 に切り替える。**SNR ゲートは落ちる**（平均 23.24 dB）が、
そのゲートは W8A32 の実測に合わせた回帰検出用で、知覚の基準ではない。

**ホストでの W8A8 実測**（`reports/d3c_int8.json`）: 全 conv 層で fp32 比 **0.850**
（W8A32 は 1.014 で速くならない）。`dec pw2` 単層では **0.481**。

⚠️ **W8A8 は精度を約 2.3 dB 落とす**（`decoder.inp` 実活性化で 47.14 → 44.82 dB）。
end-to-end は未測定。現行 W8A32 の end-to-end が fp32 比 平均 25.88 dB /
最小 23.27 dB（M-45）なので、**接続するなら測り直しが要る**。
**golden test も bit 一致では書けない**（許容誤差での比較に変わる）。

上流は同じ ESP32-S3 で **int8 + PIE の 0.22× RT を実測**と申告しており
（`../upstream-sanotts.md`）、**int8 + PIE が必須**という結論は変わらない。

### ✅ P-2: `β`（式7）の聴取決定 — **決着した**（M-60 / D-038）

**結論: `β = 0` で確定。式7（摩擦音へのノイズ注入）は採用しない。**

候補は **β=0 と 2** だった（M-40。sweep は 0/2/4/6/8 を回した）。
`reports/listening_beta/` の A/B を聴いて、**0 と 2 の差を聴き分けられなかった**。

根拠が 3 つ揃った:

1. **指標では決められない**と分かっていた（論文も同様。集約 SCOREQ はむしろ下がる）
2. **聴いても差が出なかった**
3. この生徒は **β=0 で既に教師と一致**していた（M-40）

**実装への影響はゼロ。** C コア（出荷経路）には `β` が最初から無い:

```bash
grep -c "beta" csrc/saanotts.c csrc/saanotts_stream.c csrc/saanotts_int8.c   # → 0 / 0 / 0
```

⚠️ **`σ_T` はラベルパックから外せない** — `L_c` のチャネル正規化項でも使うため。

⚠️ **聴取者は 1 名。** 日本語母語話者 10 名以上が理想で、**追試の余地は残る**。
ただし「β=0 で既に教師と一致」という指標側の裏づけがあるので、
**追試で覆る公算は低い**と判断した。

⚠️ **上流（英語）は β=6.0 を採用している。** うちの日本語指標では 6 は候補に残らず、
聴取でも 0 と 2 の差が出なかった。**言語差なのか指標の違いなのかは切り分けていない。**

### ✅ E-1: DNSMOS — **決着した**（M-50 / D-034）

**結論: 上流の主張は再現しなかった。DNSMOS は合否ゲートにせず、併記プローブとして採用する。**

| レーン | OVRL |
|---|---:|
| 実人間 | 2.7881 |
| 教師 (eval_v2) | 2.7299 |
| **生徒** | **2.1088** |

生徒/教師 = **0.7725** [0.7483, 0.7973]（n=24）。**SCOREQ 比 0.611 より高い** —
**指標によって生徒の見え方が変わる**。

⚠️ **上流の「金属的アーティファクトは SCOREQ で高得点・DNSMOS で低得点」は出なかった。**
金属様対照（Griffin-Lim 位相破壊）に対し DNSMOS は同 SNR の白色雑音より **1.33 甘い**。
**ただし SNR は位相破壊と加法雑音を等化する尺度ではない**（対数スペクトル距離 1.99 dB 対
58.10 dB。照合で指摘）。**「DNSMOS は金属様に鈍い」と言い切れるほど強い証拠ではない。**

⚠️ **陽性対照 G6 は FAIL。** ハードクリップ (SNR 10.5 dB) で 4 スコアとも下がらない。
**DNSMOS が下がらないことを「劣化が無い」と読んではいけない。**

**調査で判明した日本語固有の危険**（先行研究 arXiv:2606.19951 / JVS / 母語話者 15 名）:

- **アクセント誤りに完全に無反応**（人間 4.00→2.16 で DNSMOS 3.82→3.83）。
  **D-4 のアクセント評価の代わりにならない**
- **高ピッチを罰する**（平均 log F0 と r=−0.788。人間は −0.059）。
  **つくよみちゃんは高ピッチ女声**なので不利側に偏る
- 学習データは DNS Challenge（**TTS 音声なし・英語のみ**。一次ソースで確認）

```bash
uv run --extra eval python scripts/e1_dnsmos.py
```

⚠️ `speechmos` は UTMOS の torch.hub checkout と**同名パッケージで衝突する**ので、
同一プロセスで両方を import できない。

### ✅ E-2: decoder の教師初期化 — **決着した**（M-49 / D-033）

**結論: 「教師初期化するか」は問いとして成立しない。定義できない。**

形状としては **27/28** の生徒テンソルが切り出せるが、合致先は**定数 FiLM**
（`cond` / `cond_layers`）か**別フレームレートの ResBlock** で意味が対応せず、
出力ヘッド `hout (1539,48,1)` は**教師側に候補ゼロ**。

```bash
uv run python reports/r3_slice_matrix.py
```

**代わりにレーンラダーでギャップを分解した**（n=200、鎖分解、対応のある bootstrap）:

| 段 | ギャップ（SCOREQ） |
|---|---:|
| **decoder** | **0.395** [0.374, 0.416] |
| acoustic | 0.283 [0.263, 0.303] |
| duration | 0.052 |
| 40 次元 c-line 自体 | **0.024 のみ** |

3 項の和が全体ギャップ 0.729 と一致する。
**了解度（かな CER）の劣化は decoder の段だけ**で起きる（+0.039、acoustic/duration はゼロ）。

⚠️ **論文流の置換定義にすると acoustic 0.508 > decoder 0.395 で順序が逆転する。**
**「主因は decoder」は分解の取り方に依存する主張であって、事実ではない。**

⚠️ **C-026 / C-027 を読むこと。** このタスクでは私（Claude）の推論が 2 回外れている:
「金属的な尾が出れば仮説 (a) の証拠になる」も
「blob2 の制約を満たす構成が 0 件なら逆算が間違い」も**前提から誤っていた**。

### ✅ E-2b: `Gγ` の幅スイープ — **決着した**（M-52）

**結論: `Gγ` は容量律速ではない。学習律速である。**

n=200 / `L1_c_s3` 段 / 教師 SCOREQ 1.9972。**ノイズ床（同幅・同 steps・seed 違い 3 本）= 0.0812**。

| W | steps | params | gap | Δ vs W=76@40k |
|---:|---:|---:|---:|---:|
| 56 | 40k | 219,828 | 0.4661 | +0.0632（床以下） |
| **76** | **40k** | 331,308 | **0.4030** | — |
| 96 | 40k | 474,788 | 0.4035 | **+0.0006** [−0.013, +0.015] |
| 76 | 20k | 331,308 | 0.5120 | +0.1090 |

- **幅は効かない**: params +43% で Δ +0.0006、CI が 0 を含む
- **学習量は効く**: 20k → 40k で −0.1090（床 0.0812 超え）。**40k でもまだ下がる余地**
- ⚠️ **W=56 に落とせるかは保留**（下記 E-2c）

```bash
uv run --extra eval python scripts/e2b_width_sweep.py \
  --run runs/w56_40k --run runs/w76_40k --run runs/w96_40k \
  --n 200 --seed 7 --out reports/e2b_width --baseline w76_40k
```

### ⏹ E-2c: W=56 が無料かの確認 — **中止した**（D-036）

W=56 は params **−34%** で 40k の Δgap が **+0.0632**。40k のノイズ床を測るため
`w76b_40k` / `w76c_40k`（seed 違い 2 本）を回していたが、**12,000 step で中止した**。

**理由: 結果がどちらでも打つ手が変わらない。**

M-43 の外挿では **int8 + PIE が動けば 0.088× RT**（η_host=0.364 を転移した値）で、
必要な 1.0× に対し **11 倍の余裕**がある。W=56 の削減効果は全体の 2 割程度なので、

| int8+PIE の着地 | W=56 の価値 |
|---|---|
| 外挿どおり 0.088〜0.22× RT | **無意味**（余裕十分。W=76 の品質を取るべき） |
| 1.0〜1.25× RT の狭い帯 | 有用 |
| 1.25× RT より遅い | 焼け石に水 |

**外挿はこの帯から 5〜10 倍離れた場所を指している。**
先に P-1（W8A8 + PIE）を進めて着地点が分かってから、必要なら再開する。

中断した ckpt は `runs/w76b_40k` / `runs/w76c_40k` に残っている
（`stage3.pt` は未保存。再開する場合は 0 から回し直し）。

⚠️ **E-2b の主結論は E-2c の結果に依存しない。** 「`Gγ` は容量律速ではない」は
W=96 の Δ **+0.0006** / CI [−0.013, +0.015] で決まっており、床の大きさと無関係。
E-2c が決めるのは「**下げられるか**」だけだった。

### 優先度を下げたまま据え置くもの

`λ_Δ/λ_s/λ_T` の探索（初期値のまま目標に届いた）/ 1.4 M z-line（上限を測る必要が薄れた）/
学習の延長（Stage 2 はまだ下がる余地あり）/ コーパスの CC0 化（現状 CC0 のみだと約 5,200 行）。
