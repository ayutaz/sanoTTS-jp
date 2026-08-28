# sanoTTS-jp ドキュメント

arXiv:2608.21378 "sanoTTS" の蒸留レシピを日本語に適用し、**ESP32 上で動く
日本語 TTS** を作るプロジェクトの調査・設計・実測記録。

## 読む順序

| # | ドキュメント | 内容 | 更新頻度 |
|---|---|---|---|
| 0 | [`../CLAUDE.md`](../CLAUDE.md) | 実装時の要点だけを抜き出した運用ルール。**コードを書く前に必ず読む** | 実測のたび |
| 0.5 | [`requirements.md`](requirements.md) | **要件定義書**。入力仕様・機能/非機能要件・受け入れ条件 | 仕様変更時 |
| 1 | [`decisions.md`](decisions.md) | 意思決定の記録 D-001〜D-034 と**訂正履歴 C-001〜C-027** | 決定のたび |
| 2 | [`measurements.md`](measurements.md) | **実測値の一次ソース** M-1〜M-51。全数値に再現コマンド付き | 実測のたび |
| 3 | [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) | **作業計画**。B-0〜B-12 の検証タスクと Phase 0〜D の状態、**§10 に残りのタスク P-1/P-2/E-1/E-2** | フェーズ移行時 |
| 3.5 | [`plan/phase-a-decisions.md`](plan/phase-a-decisions.md) | Phase A の決定（入力経路 / prosody / パック形式）と根拠 | 固定 |
| 2.5 | [`upstream-sanotts.md`](upstream-sanotts.md) | **公式実装 `Ampixa/sanoTTS` から得た事実**（GPL-3.0）。⚠️ すべて**上流の申告値で未再現**。ソースコードは読まない | 上流を見たとき |
| 3.7 | [`vastai-runbook.md`](vastai-runbook.md) | **vast.ai 実行手順**。ラベル一括生成 → 本学習。教師の同一性照合とゲート | 実行時 |
| 4 | [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md) | B-0 の結論レポート。辞書枝刈りが不成立と判定した根拠 | 固定 |
| 5 | [`research/sanotts-jp-feasibility.md`](research/sanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し。⚠️ 結論の一部は更新済み | ほぼ固定 |

**数値が食い違ったら [`measurements.md`](measurements.md) が正**。
他のドキュメントはそこからの引用または解釈として扱う。

⚠️ 例外は [`upstream-sanotts.md`](upstream-sanotts.md)。**あれは上流の申告値であって、うちの実測ではない。** M-番号と混ぜないこと。

## 現在地（2026-08-28 時点）

```
[完了] 論文の仕様抽出        論文 PDF から全数値を抽出
[完了] 教師 ckpt の特定       HF private 61 repo を調査 → 1 件に確定
[完了] 教師の動作確認         v2.0 に missing 0 / unexpected 0 でロード、決定的推論が bit 再現
[完了] 音素化経路の確定       canonical 経路を特定、発話速度 8.4 mora/s を確認
[完了] スコープ確定           ESP32 のみ。ブラウザは対象外
[完了] B-0 G2P フットプリント  辞書路線は不成立と判明（40 MiB 必要）
[完了] 入力仕様の確定         ひらがな + アクセント記号 + 無声化マーク → 端末 951 B
[完了] 変換器の実装           scripts/kana_g2p.py。往復 100%、教師出力と bit 一致
[完了] 要件定義              requirements.md
[完了] ESP32 メモリ収支      I2S 逐次出力で 96 KB / SRAM 残 416 KB。中止材料なし
[完了] B-5 教師品質ベースライン UTMOS 1.748 / 実人間 2.305 → 比 0.758。指標の較正ずれを補正
[完了] Phase A              ラベル生成の設計確定（入力経路 / prosody / パック形式）
[完了] Phase B              パイプライン実装 + 本番実行（手元の CPU で 47 分）
[完了] Phase C              生徒 4 段の学習ループ。スモークテスト全通過
[完了] B-4/6/7/8/9/10/12    検証タスク完走。D-016〜D-031 として設計値を凍結
[完了] SCOREQ 導入           論文の主指標。教師 2.0488 / 実人間 2.4983 → 比 0.820
[完了] 本番ラベルパック      train 20,790 / heldout 2,314 発話。SHA-256 固定（M-35）
[完了] 本学習 v1/v2         **SCOREQ 教師比 0.611（論文の英語比 0.5427 を超過）**（M-36 / M-37）
[完了] CER                  かな CER 教師 0.135 / 生徒 0.178（M-38）
[完了] int8 量子化           blob 624,692 B（論文比 −8.1%）（M-39）
[完了] β スイープ            β=0 と 2 が候補。⚠️ **聴取待ち**（M-40）
[完了] Phase D-1            C99 コア。Pearson 1.000000 / SNR 117.5 dB（M-41）
[完了] Phase D-2            **ストリーミング化。1,258→196.9 KB で SRAM に載った**（M-42）
[完了] Phase D-3a/b/c       FFT 1,435 倍 / 手元 **0.023× RT** / int8 カーネル（M-43）
[完了] Phase D-3c'-1/2      **int8 end-to-end**。fp32 比 平均 25.88 dB / ブロブ −71.4%（M-45）
[完了] Phase D-3c'-4        ESP-IDF 雛形。⚠️ **一度もビルドしていない**（M-46。toolchain は M-54 で導入済み）
[完了] D-4                  アクセント型ミニマルペア。符号一致 35/36 で**再現している**（M-44）
[完了] B-12                 教師の事前学習との重複検査。**看板の 24 文は汚染ゼロ**（M-47）
[完了] 敵対的検証            空虚に通るゲート 2 件 + silent failure 1 件を修正（M-48）
[完了] E-1 DNSMOS           生徒/教師 **0.7725**（SCOREQ 比 0.611 より高い）。上流の主張は**再現せず**
                            ⚠️ **陽性対照 G6 は FAIL**。DNSMOS が下がらない＝劣化が無い、ではない（M-50 / D-034）
[完了] E-2 decoder 教師初期化 **定義できない**（27/28 は形状一致だが意味が対応せず、hout は候補ゼロ）
                            代わりにギャップを分解: decoder 0.395 / acoustic 0.283 / duration 0.052（M-49 / D-033）
[完了] E-2b 幅スイープ        **`Gγ` は容量律速ではない**。params +43% で Δ +0.0006（CI が 0 を含む）
                            効くのは学習量: 20k→40k で −0.1090（ノイズ床 0.0812 超え）（M-52）
[完了] ライセンス再調査        一次ソースで**記録 3 件を訂正**。CV は CC0 確定、つくよみちゃんは
                            **モデル配布を明示的に許可**。**継承の障害は最初から無かった**（D-035 / C-029〜031）
[完了] ESP-IDF 導入           **「toolchain 待ち」は誤りだった**。v5.5 を導入し、C99 コア 5 ファイルが
                            厳密 `-std=c99` で ESP32-S3 向けに通過。⚠️ `M_PI` の移植性バグを 1 件発見（M-54 / C-033）
[中止] E-2c W=56 が無料か     結果がどちらでも打つ手が変わらないため中止（D-036）
[今]   P-1 PIE カーネル       **W8A8 は既に実装済み**（`saan_conv1d_i8a`。ホストで fp32 比 0.850）。
                            ⚠️ 逆アセンブルすると **W8A8 でも PIE 命令 0 件** = GCC は自動ベクトル化しない。
                            残りは **(a) 本番経路への接続** と **(b) intrinsic/アセンブリ**（M-53 / C-034）
[待]   P-2 β の聴取決定      候補 β=0 と 2。⚠️ 上流（英語）は 6.0。**人が要る**
[未]   Phase D-3d           実機測定（**ESP32-S3 ボード待ち**。これは本物の待ち）
```

**残りは速度だけ**。詳細は
[`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) §10。

⚠️ **「金属的な尾が出たら E-2 の仮説 (a) の証拠になる」は撤回**（C-026）。
尾が出ても言えるのは「うちの decoder が不十分」までで、逆算の当否は別問題。

⚠️ **「待ち」に分類するときは、待っている相手を名指しできるか確かめること**（C-033）。
名指しできないなら待ちではなく**未着手**。実機ボードは名指しできるが、toolchain は違った。

### 凍結した設計値（2026-08-28）

| 項目 | 値 | 記録 |
|---|---|---|
| デプロイ語彙 | **57**（英語版は 157）→ 合計 **559,008 params** / int8 blob 624,692 B | D-016 / M-26 / M-39 |
| 長さフィルタ | `max_spec_length=700` で 4.31% 除外 | D-017 / M-23 |
| `_` PAD | **フレームの 53.76%**。特別扱いしない | D-018 / M-25 |
| `s_v` | **1.2187**（丸め規約の差の吸収） | D-019 / M-24 |
| 評価の主指標 | SCOREQ synthetic/nr + UTMOS 併記。**DNSMOS は合否にせず併記プローブ** | D-020 / **D-034** |
| ギャップの帰属 | decoder 0.395 / acoustic 0.283 / duration 0.052。⚠️ 定義依存で順序が逆転する | **D-033** / M-49 |
| λ | `λ_n`/`λ₂` は実行時算出、`λ_Δ=0.86` / `λ_s=0.19` / `λ_T=0.27` | D-021 / M-30 |
| iSTFT | `center=True` + `length=T*256` | D-022 / M-32 |
| `yT` | EMA 適用版 | D-023 / M-33 |
| 判別器 | 94,755 params（学習専用） | D-025 / M-31 |
| 平坦度プローブ | `n_fft=1024 / guard=0 / power=1` | M-27 |
| ストリーミング | ステート保持 / CHUNK=8。**196.9 KB で一括版と bit 一致** | D-029 / M-42 |
| アクセント | 記号 `[ ] #` のみ。A1/A2/A3 は**足さない**（符号一致 35/36） | **D-030** / M-44 |
| ESP32 の配置 | 重みは flash の `model` パーティション、arena は **208 KB を静的確保** | **D-031** / M-46 |
| 逆 FFT | radix-2 自前 / float。naive の 1,435 倍 / SNR 138.7 dB | M-43 |
| int8 | **W8A32**（重みだけ int8）。blob 624,692 B | M-43 |
| 実行環境 | 手元の M4 Max（ラベル生成 CPU / 学習 MPS） | D-027 |
| `Eρ` | Stage 2 で凍結、Stage 3 で decoder と学習 | D-028 |
| CER | **かな CER**（表記 CER は符号が逆転する） | C-023 |

### 何が確実で、何が未知か

**確実（実測済み）**
- 教師は手元にあり、決定的にラベル `(dT, zT, yT)` を吐く
- 潜在インターフェースが論文と一致（192ch / hop 256 / 86.13 fps）
- 蒸留に音声データは不要。テキスト 23,271 行を調達可能
- 45 MMAC/s は言語非依存なので ESP32 の 0.22× RT はそのまま日本語に移る見込み
- **日本語のデプロイ語彙は 57**（変換器の閉包）。論文の英語 157 より小さい

- **オンデバイス G2P は 951 B で成立する**（往復 100%、教師出力と bit 一致）

**未知（プロジェクトの成否を左右する順）**

1. **ESP32 で実時間に間に合うか** — 手元は 0.022× RT だが、**fp32 のまま移植すると
   2.47× RT で間に合わない**（実測 η を転移、M-43）。**PIE の int8 カーネルが必須**。
   論文の 0.22× RT も fp32 では達成不可能と検算できた
2. ~~ESP32 に載るか~~ — ✅ **197 KB / SRAM 512 KB の 38%**（M-42）。
   ⚠️ ただし fp32。int8 カーネルにすればさらに減る
3. **`β`（式7）** — **聴取で決める。** 候補は β=0 と 2（M-40）。
   ⚠️ この生徒は β=0 で既に教師と一致しており、**式7 が要らない可能性が高い**
4. ~~アクセント型の再現性~~ — ✅ **符号一致 35/36 = 0.972**（M-44 / D-030、
   ミニマルペア 15 群 32 語 64 文）。**記号 `[` `]` `#` だけで足りており、
   duration net への A1/A2/A3 追加は不要**。
   ⚠️ 聴取は未実施 / 2 型の下降核は n=13〜16 でしか測れていない

**品質は目標に届いた**（SCOREQ 教師比 0.611 > 論文の英語比 0.5427、M-37）。
⚠️ ただし **n=24** で、絶対値は日本語で較正されていない。聴取も未実施。
**残るのはメモリとレイテンシ。**

## リポジトリ構成

```
sanoTTS-jp/
├── CLAUDE.md                              運用ルール（実装前に読む）
├── docs/
│   ├── README.md                          このファイル
│   ├── requirements.md                    要件定義書
│   ├── decisions.md                       決定記録 D-001〜D-034 + 訂正履歴 C-001〜C-027
│   ├── measurements.md                    実測値の一次ソース M-1〜M-51
│   ├── upstream-sanotts.md                公式実装から得た事実（⚠️ 上流申告値・未再現）
│   ├── vastai-runbook.md                  vast.ai の実行手順（次のフェーズ）
│   ├── plan/phase0-1-implementation-plan.md
│   ├── plan/phase-a-decisions.md          Phase A の決定
│   └── research/
│       ├── b0-g2p-footprint.md            B-0 の結論
│       └── sanotts-jp-feasibility.md     初期調査
├── src/saanotts_jp/                       ライブラリ（scripts から import する）
│   ├── _param_reference.py                論文 Table I を再現する層構成
│   ├── losses.py                          式2 / 3 / 5 / 6 / 7
│   ├── discriminator.py                   一次差分判別器（学習専用）
│   ├── vocab.py                           ⚠️ デプロイ語彙 57 と教師IDの写像（重みと一緒に凍結）
│   ├── labelpack.py                       ラベルパックの読み書き + 13 ゲート
│   ├── durations.py                       全行 duration の読み込み
│   ├── flatness.py                        音素クラス別 SFM（設定と教師ベースラインを凍結）
│   ├── accent.py                          アクセント型ミニマルペア評価（設定を凍結・D-030）
│   ├── scoreq_metric.py                   SCOREQ ラッパ（torchcodec 回避）
│   └── teacher_identity.py                piper-plus のコミット / ソース SHA-256 のピン留め
├── csrc/                                  **C99 推論コア（Phase D）**
│   ├── saanotts.h / saanotts.c            一括版。依存は libm のみ。malloc を呼ばず arena を使う
│   ├── saanotts_stream.h / .c             **ストリーミング版**（196.9 KB / SRAM に載る）
│   ├── saanotts_internal.h                両版で共有するカーネル（**2 回書かない**）
│   ├── fft.h / fft.c                      radix-2 逆実 FFT（naive の 1,435 倍）
│   ├── saanotts_int8.h / .c               int8 カーネル（⚠️ **PIE 未使用**）
│   ├── golden_test.c                      参照実装との一致（Pearson >= 0.98）
│   ├── stream_test.c                      受け入れ条件 G1〜G4（**stack 込みで判定**）
│   ├── fft_test.c / int8_test.c           各カーネルの単体検証
│   ├── bench.c                            レイテンシ測定（段別の内訳）
│   └── Makefile                           `make all-test` / `make run-bench`
├── deploy/                                vast.ai 用（⚠️ 現在は使っていない、D-027）
│   ├── vastai_bootstrap.sh                setup → parity → labels → train
│   └── retarget_sources.py                path 依存をインスタンスのパスに向け直す
├── pyproject.toml / uv.lock               uv 環境定義
├── .claude/
│   ├── settings.json                      permissions.deny + PreToolUse hook
│   ├── hooks/guard_bash.py                piper-plus 保護 / uv 強制 / 本番パック保護（83 ケース + commit ガードのテスト付き）
│   └── skills/                            recording-measurements / teacher-inference /
│                                           student-training / evaluating-quality /
│                                           verifying-reports / writing-gates
├── reports/                               一次データ (JSON)。⚠️ 全行ダンプは追跡しない
└── scripts/
    ├── phase0_verify_teacher.py           教師の決定的推論を検証（6 チェック）
    ├── kana_g2p.py                        中間表現 ⇄ 音素列の変換器 + 入力正規化
    ├── gen_teacher_labels.py              中間表現 → 教師 → ラベルパック
    ├── b_durations_all.py                 全行の duration だけを取る（B-4/7/8 の土台）
    ├── train_student.py                   生徒 4 段の蒸留学習
    ├── test_losses.py / test_labelpack.py / test_discriminator.py
    ├── b4_device_parity.py                CPU/GPU のラベル一致検証（★ 本番前のゲート）
    ├── b4_length_hist.py                  長さ分布と符号化の関係式
    ├── b5_teacher_baseline.py / b5_measure_mos.py / b5_scoreq_baseline.py
    ├── b6_build_evalset.py                クラス別 n を確保した評価セットを作る
    ├── b6_flatness_grid.py                SFM の窓設計グリッド（定数がズレたら exit 1）
    ├── b7_sv_calibration.py               length scale s_v の較正
    ├── b8_pad_duration.py                 `_` PAD のフレーム占有率
    ├── b9_vocab_closure.py                デプロイ語彙の閉包
    ├── b9_add_question_eos.py             疑問 EOS 4 種の文を追加
    ├── b10_overlap.py / b10_write_exclusions.py
    ├── c1_lambda_balance.py               λ の勾配整合
    ├── d5_istft_framing.py / d6_ema_ablation.py
    ├── eval_metrics.py                    UTMOS + SCOREQ 4 設定を並べて出す
    ├── esp32_memory_budget.py             ESP32-S3 のメモリ収支見積もり
    ├── train_student.py                   生徒 4 段の蒸留学習（段間で重みを引き継ぐ）
    ├── synthesize_student.py              **生徒だけで音声を作る**（教師を呼ばない）
    ├── eval_student.py                    教師比 + 音素クラス別 SFM/RMS
    ├── measure_cer.py                     かな CER（表記 CER も参考で併記）
    ├── quantize_student.py                int8 PTQ シミュレーションと blob サイズ
    ├── b_beta_sweep.py                    β の候補絞り込み（決定は聴取）
    ├── build_listening_set.py             A/B 聴取セット（順序・左右ランダム / 2 反復）
    ├── score_listening.py                 二項 CI と内的一貫性で判定
    ├── export_c_weights.py                重みと golden を SAAN 形式で書き出す
    └── b0/                                B-0 の測定スクリプト（記録用）

（`data/pack*` `runs/` `reports/eval_*` `csrc/*.bin` は .gitignore。再生成できる）
```

## 実行方法

```bash
uv sync --extra eval                             # 環境構築（初回・依存変更時）

# 健全性チェック（ここが通らないなら先に進まない）
uv run python scripts/phase0_verify_teacher.py   # 教師の疎通（6 チェック）
uv run python scripts/kana_g2p.py                # 中間表現変換器（10 ケース）
uv run python scripts/test_losses.py             # 損失の性質（26 項目）
uv run python scripts/test_labelpack.py          # パック往復 + ゲート発火
uv run python scripts/test_discriminator.py      # 判別器（23 チェック）
uv run python .claude/hooks/test_guard_bash.py   # hook の回帰（83 ケース + commit ガード）
uv run python src/saanotts_jp/_param_reference.py  # 論文 Table I の再現 + V=57

# C99 推論コア（Phase D）
uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt
make -C csrc all-test                            # golden test + ストリーミング G1〜G4
```

一から作り直す場合:

```bash
uv run python scripts/gen_teacher_labels.py --split train   --out data/pack     # 47 分
uv run python scripts/gen_teacher_labels.py --split heldout --out data/pack_heldout
uv run python scripts/train_student.py --run runs/v2 --stage 1 --steps 20000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 2 --steps 60000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 3 --steps 40000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 4 --steps 60000 --accum 8
uv run --extra eval python scripts/eval_student.py --ckpt runs/v2/stage4.pt --n 24 \
    --out reports/eval_v2
```

⚠️ **ラベルは一度だけ生成する**（D-015）。hook が `data/pack` の破棄と再生成を deny する。

**Python は必ず `uv` 経由**（`pip install` を使わない）。**学習は vast.ai**（[D-012](decisions.md)）。
手元で `--split train` のラベルを丸ごと生成しようとすると hook が止める。

## 外部依存

| 対象 | 場所 | 備考 |
|---|---|---|
| 教師モデル | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF private) | `epoch=499-step=22000.ckpt` 927 MB |
| piper-plus | `~/Documents/piper-plus` | v2.0.0 HEAD。**読み取り専用で使う** |
| Python 環境 | 本リポジトリの `uv`（`pyproject.toml`） | Python 3.14.0 / torch 2.13.0。教師ラベルは piper-plus venv と bit 一致 |
| 学習環境 | **vast.ai** | ラベル生成も向こうで実行（D-012）。手順は [`vastai-runbook.md`](vastai-runbook.md) |
| 評価指標 | `scoreq==1.0.1`（PyPI） | `uv sync --extra eval`。ラッパは `src/saanotts_jp/scoreq_metric.py` |

⚠️ **piper-plus のコミットは `src/saanotts_jp/teacher_identity.py` にピン留めしてある。**
別マシンで教師を動かす前に `verify()` を通すこと（コードが違えばラベルは静かに別物になる）。
