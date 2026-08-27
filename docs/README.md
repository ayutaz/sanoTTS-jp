# saanoTTS-jp ドキュメント

arXiv:2608.21378 "saanoTTS" の蒸留レシピを日本語に適用し、**ESP32 上で動く
日本語 TTS** を作るプロジェクトの調査・設計・実測記録。

## 読む順序

| # | ドキュメント | 内容 | 更新頻度 |
|---|---|---|---|
| 0 | [`../CLAUDE.md`](../CLAUDE.md) | 実装時の要点だけを抜き出した運用ルール。**コードを書く前に必ず読む** | 実測のたび |
| 0.5 | [`requirements.md`](requirements.md) | **要件定義書**。入力仕様・機能/非機能要件・受け入れ条件 | 仕様変更時 |
| 1 | [`decisions.md`](decisions.md) | 意思決定の記録 D-001〜D-028 と**訂正履歴 C-001〜C-022** | 決定のたび |
| 2 | [`measurements.md`](measurements.md) | **実測値の一次ソース** M-1〜M-35。全数値に再現コマンド付き | 実測のたび |
| 3 | [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) | **作業計画**。B-0〜B-11 の検証タスクと Phase 0〜D の状態 | フェーズ移行時 |
| 3.5 | [`plan/phase-a-decisions.md`](plan/phase-a-decisions.md) | Phase A の決定（入力経路 / prosody / パック形式）と根拠 | 固定 |
| 3.7 | [`vastai-runbook.md`](vastai-runbook.md) | **vast.ai 実行手順**。ラベル一括生成 → 本学習。教師の同一性照合とゲート | 実行時 |
| 4 | [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md) | B-0 の結論レポート。辞書枝刈りが不成立と判定した根拠 | 固定 |
| 5 | [`research/saanotts-jp-feasibility.md`](research/saanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し。⚠️ 結論の一部は更新済み | ほぼ固定 |

**数値が食い違ったら [`measurements.md`](measurements.md) が正**。他のドキュメントは
そこからの引用または解釈として扱う。

## 現在地（2026-08-27 時点）

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
[完了] Phase B              パイプライン実装。B-e (本番実行) のみ vast.ai 待ち
[完了] Phase C              生徒 4 段の学習ループ。スモークテスト全通過
[完了] B-4/6/7/8/9/10       検証タスク完走。D-016〜D-025 として設計値を凍結
[完了] SCOREQ 導入           論文の主指標。教師 2.0488 / 実人間 2.4983 → 比 0.820
[次]   本学習 (vast.ai)      ../docs/vastai-runbook.md の手順で。まず CUDA parity ゲート
[未]   Phase D              C99 コア + ESP32 実機
```

### 凍結した設計値（2026-08-27）

| 項目 | 値 | 記録 |
|---|---|---|
| デプロイ語彙 | **57**（英語版は 157）→ 合計 **559,008 params** | D-016 / M-26 |
| 長さフィルタ | `max_spec_length=700` で 4.31% 除外 | D-017 / M-23 |
| `_` PAD | **フレームの 53.76%**。特別扱いしない | D-018 / M-25 |
| `s_v` | **1.2187**（丸め規約の差の吸収） | D-019 / M-24 |
| 評価の主指標 | SCOREQ synthetic/nr + UTMOS 併記 | D-020 / M-29 |
| λ | `λ_n`/`λ₂` は実行時算出、`λ_Δ=0.86` / `λ_s=0.19` / `λ_T=0.27` | D-021 / M-30 |
| iSTFT | `center=True` + `length=T*256` | D-022 / M-32 |
| `yT` | EMA 適用版 | D-023 / M-33 |
| 判別器 | 94,755 params（学習専用） | D-025 / M-31 |
| 平坦度プローブ | `n_fft=1024 / guard=0 / power=1` | M-27 |

### 何が確実で、何が未知か

**確実（実測済み）**
- 教師は手元にあり、決定的にラベル `(dT, zT, yT)` を吐く
- 潜在インターフェースが論文と一致（192ch / hop 256 / 86.13 fps）
- 蒸留に音声データは不要。テキスト 23,271 行を調達可能
- 45 MMAC/s は言語非依存なので ESP32 の 0.22× RT はそのまま日本語に移る見込み
- **日本語のデプロイ語彙は 57**（変換器の閉包）。論文の英語 157 より小さい

- **オンデバイス G2P は 951 B で成立する**（往復 100%、教師出力と bit 一致）

**未知（プロジェクトの成否を左右する順）**
1. **567 K で日本語が実用に足るか** — 目標は SCOREQ **1.112** / UTMOS 1.107
   （論文の英語 embedded の教師比 0.5427 を当てはめた値）。日本語 CER は未知
2. **ESP32 実機のレイテンシと実 heap** — 解析では余裕があるが、C99 コアが出来るまで測れない
3. **学習ステップ数 / lr / バッチサイズが論文に無い** — 損失曲線で決める

## リポジトリ構成

```
saanoTTS-jp/
├── CLAUDE.md                              運用ルール（実装前に読む）
├── docs/
│   ├── README.md                          このファイル
│   ├── requirements.md                    要件定義書
│   ├── decisions.md                       決定記録 D-001〜D-025 + 訂正履歴 C-001〜C-021
│   ├── measurements.md                    実測値の一次ソース M-1〜M-34
│   ├── vastai-runbook.md                  vast.ai の実行手順（次のフェーズ）
│   ├── plan/phase0-1-implementation-plan.md
│   ├── plan/phase-a-decisions.md          Phase A の決定
│   └── research/
│       ├── b0-g2p-footprint.md            B-0 の結論
│       └── saanotts-jp-feasibility.md     初期調査
├── src/saanotts_jp/                       ライブラリ（scripts から import する）
│   ├── _param_reference.py                論文 Table I を再現する層構成
│   ├── losses.py                          式2 / 3 / 5 / 6 / 7
│   ├── discriminator.py                   一次差分判別器（学習専用）
│   ├── vocab.py                           ⚠️ デプロイ語彙 57 と教師IDの写像（重みと一緒に凍結）
│   ├── labelpack.py                       ラベルパックの読み書き + 13 ゲート
│   ├── durations.py                       全行 duration の読み込み
│   ├── flatness.py                        音素クラス別 SFM（設定と教師ベースラインを凍結）
│   ├── scoreq_metric.py                   SCOREQ ラッパ（torchcodec 回避）
│   └── teacher_identity.py                piper-plus のコミット / ソース SHA-256 のピン留め
├── deploy/
│   ├── vastai_bootstrap.sh                setup → parity → labels → train
│   └── retarget_sources.py                path 依存をインスタンスのパスに向け直す
├── pyproject.toml / uv.lock               uv 環境定義
├── .claude/
│   ├── settings.json                      permissions.deny + PreToolUse hook
│   ├── hooks/guard_bash.py                piper-plus 保護 / uv 強制 / 本番パック保護（58 ケースのテスト付き）
│   └── skills/                            recording-measurements / teacher-inference /
│                                          student-training / evaluating-quality / verifying-reports
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
    └── b0/                                B-0 の測定スクリプト（記録用）
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
uv run python .claude/hooks/test_guard_bash.py   # hook の回帰（58 ケース）
uv run python src/saanotts_jp/_param_reference.py  # 論文 Table I の再現 + V=57

# 次のフェーズ（vast.ai 上で実行する）
bash deploy/vastai_bootstrap.sh setup
bash deploy/vastai_bootstrap.sh parity           # ★ D-015 のゲート。ここで結果を読む
```

**Python は必ず `uv` 経由**（`pip install` を使わない）。**学習は vast.ai**（[D-012](decisions.md)）。
手元で `--split train` のラベルを丸ごと生成しようとすると hook が止める。

## 外部依存

| 対象 | 場所 | 備考 |
|---|---|---|
| 教師モデル | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF private) | `epoch=499-step=22000.ckpt` 927 MB |
| piper-plus | `/Users/s19447/Documents/piper-plus` | v2.0.0 HEAD。**読み取り専用で使う** |
| Python 環境 | 本リポジトリの `uv`（`pyproject.toml`） | Python 3.14.0 / torch 2.13.0。教師ラベルは piper-plus venv と bit 一致 |
| 学習環境 | **vast.ai** | ラベル生成も向こうで実行（D-012）。手順は [`vastai-runbook.md`](vastai-runbook.md) |
| 評価指標 | `scoreq==1.0.1`（PyPI） | `uv sync --extra eval`。ラッパは `src/saanotts_jp/scoreq_metric.py` |

⚠️ **piper-plus のコミットは `src/saanotts_jp/teacher_identity.py` にピン留めしてある。**
別マシンで教師を動かす前に `verify()` を通すこと（コードが違えばラベルは静かに別物になる）。
