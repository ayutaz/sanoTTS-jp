# saanoTTS-jp ドキュメント

arXiv:2608.21378 "saanoTTS" の蒸留レシピを日本語に適用し、**ESP32 上で動く
日本語 TTS** を作るプロジェクトの調査・設計・実測記録。

## 読む順序

| # | ドキュメント | 内容 | 更新頻度 |
|---|---|---|---|
| 0 | [`../CLAUDE.md`](../CLAUDE.md) | 実装時の要点だけを抜き出した運用ルール。**コードを書く前に必ず読む** | 実測のたび |
| 0.5 | [`requirements.md`](requirements.md) | **要件定義書**。入力仕様・機能/非機能要件・受け入れ条件 | 仕様変更時 |
| 1 | [`decisions.md`](decisions.md) | 意思決定の記録 D-001〜D-012 と**訂正履歴 C-001〜C-011** | 決定のたび |
| 2 | [`measurements.md`](measurements.md) | **実測値の一次ソース** M-1〜M-16。全数値に再現コマンド付き | 実測のたび |
| 3 | [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) | 作業計画。B-1〜B-11 の検証タスクと Phase 2〜6 の見取り図。⚠️ B-0 より前に書かれた部分を含む | フェーズ移行時 |
| 4 | [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md) | B-0 の結論レポート。辞書枝刈りが不成立と判定した根拠 | 固定 |
| 5 | [`research/saanotts-jp-feasibility.md`](research/saanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し。⚠️ 結論の一部は更新済み | ほぼ固定 |

**数値が食い違ったら [`measurements.md`](measurements.md) が正**。他のドキュメントは
そこからの引用または解釈として扱う。

## 現在地（2026-08-26 時点）

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
[次]   B-1 / B-2 を塞ぐ      ラベル生成前の 2 つの無警告データ破壊
[未]   B-3..B-11            残りの不確定事項
[未]   Phase 1 以降          ラベル一括生成 → Duration → Acoustic → Decoder → 量子化
[未]   ESP32 実機 spike      C99 コアと量子化済み生徒が出来てから
```

### 何が確実で、何が未知か

**確実（実測済み）**
- 教師は手元にあり、決定的にラベル `(dT, zT, yT)` を吐く
- 潜在インターフェースが論文と一致（192ch / hop 256 / 86.13 fps）
- 蒸留に音声データは不要。テキスト 23,271 行を調達可能
- 45 MMAC/s は言語非依存なので ESP32 の 0.22× RT はそのまま日本語に移る見込み
- 日本語音素は 65〜90 で論文のデプロイ語彙 157 に収まる

- **オンデバイス G2P は 951 B で成立する**（往復 100%、教師出力と bit 一致）

**未知（プロジェクトの成否を左右する順）**
1. **567 K で日本語が実用に足るか** — 英語実績 SCOREQ 2.54 / WER 14.8%。日本語 CER は未知
2. **ESP32 実機のレイテンシと実 heap** — 解析では余裕があるが、C99 コアが出来るまで測れない
3. **SCOREQ が未導入** — UTMOS だけで品質を判断しない

## リポジトリ構成

```
saanoTTS-jp/
├── CLAUDE.md                              運用ルール（実装前に読む）
├── docs/
│   ├── README.md                          このファイル
│   ├── requirements.md                    要件定義書
│   ├── decisions.md                       決定記録 + 訂正履歴
│   ├── measurements.md                    実測値の一次ソース
│   ├── plan/phase0-1-implementation-plan.md
│   └── research/
│       ├── b0-g2p-footprint.md            B-0 の結論
│       └── saanotts-jp-feasibility.md     初期調査
├── pyproject.toml / uv.lock               uv 環境定義
├── .claude/
│   ├── settings.json                      permissions.deny + PreToolUse hook
│   ├── hooks/guard_bash.py                piper-plus 保護 / uv 強制（33 ケースのテスト付き）
│   └── skills/                            recording-measurements / teacher-inference
├── reports/                               B-0 の一次データ (JSON)
└── scripts/
    ├── phase0_verify_teacher.py           教師の決定的推論を検証（6 チェック）
    ├── kana_g2p.py                        中間表現 ⇄ 音素列の変換器
    ├── esp32_memory_budget.py             ESP32-S3 のメモリ収支見積もり
    ├── b5_teacher_baseline.py             教師音声 24 文を生成
    ├── b5_measure_mos.py                  UTMOS を測る
    ├── dump_naist_jdic.py                 sys.dic のエントリ列挙
    └── b0/                                B-0 の測定スクリプト（記録用）
```

## 実行方法

```bash
uv sync                                          # 環境構築（初回・依存変更時）
uv run python scripts/phase0_verify_teacher.py   # 教師の疎通確認（6 チェック）
uv run python scripts/kana_g2p.py                # 中間表現変換器のセルフテスト
```

**Python は必ず `uv` 経由**（`pip install` を使わない）。**学習は vast.ai**（[D-012](decisions.md)）。

## 外部依存

| 対象 | 場所 | 備考 |
|---|---|---|
| 教師モデル | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF private) | `epoch=499-step=22000.ckpt` 927 MB |
| piper-plus | `/Users/s19447/Documents/piper-plus` | v2.0.0 HEAD。**読み取り専用で使う** |
| Python 環境 | 本リポジトリの `uv`（`pyproject.toml`） | Python 3.14.0 / torch 2.13.0。教師ラベルは piper-plus venv と bit 一致 |
| 学習環境 | **vast.ai** | ラベル生成も向こうで実行（D-012） |
