# saanoTTS-jp ドキュメント

arXiv:2608.21378 "saanoTTS" の蒸留レシピを日本語に適用し、**ESP32 上で動く
日本語 TTS** を作るプロジェクトの調査・設計・実測記録。

## 読む順序

| # | ドキュメント | 内容 | 更新頻度 |
|---|---|---|---|
| 0 | [`../CLAUDE.md`](../CLAUDE.md) | 実装時の要点だけを抜き出した運用ルール。**コードを書く前に必ず読む** | 実測のたび |
| 0.5 | [`requirements.md`](requirements.md) | **要件定義書**。入力仕様・機能/非機能要件・受け入れ条件 | 仕様変更時 |
| 1 | [`decisions.md`](decisions.md) | 意思決定の記録（何を・なぜ・いつ決めたか）と**訂正履歴** | 決定のたび |
| 2 | [`measurements.md`](measurements.md) | **実測値の一次ソース**。全数値に再現コマンド付き | 実測のたび |
| 3 | [`plan/phase0-1-implementation-plan.md`](plan/phase0-1-implementation-plan.md) | 作業計画。B-0〜B-11 の検証タスクと Phase 0〜6 | フェーズ移行時 |
| 4 | [`research/saanotts-jp-feasibility.md`](research/saanotts-jp-feasibility.md) | 初期調査レポート。論文の全数値と piper-plus の資産棚卸し | ほぼ固定 |

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
[次]   ESP32 実機 spike      RAM ピークとレイテンシ ← 唯一残る go/no-go 項目
[未]   B-1..B-11            ラベル生成前に潰す不確定事項
[未]   Phase 1 以降          ラベル一括生成 → Duration → Acoustic → Decoder → 量子化
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
1. **ESP32 の RAM ピークとレイテンシ** — フラッシュに入っても OOM すれば落ちる。未測定
2. **567 K で日本語が実用に足るか** — 英語実績 SCOREQ 2.54 / WER 14.8%。日本語 CER は未知
3. **教師音声の品質ベースライン** — 暫定 SCOREQ 2.06 が計測アーティファクトか実態か未切り分け

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
│   └── research/saanotts-jp-feasibility.md
└── scripts/
    ├── phase0_verify_teacher.py           教師の決定的推論を検証（6 チェック）
    └── kana_g2p.py                        中間表現 ⇄ 音素列の変換器
```

## 外部依存

| 対象 | 場所 | 備考 |
|---|---|---|
| 教師モデル | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF private) | `epoch=499-step=22000.ckpt` 927 MB |
| piper-plus | `/Users/s19447/Documents/piper-plus` | v2.0.0 HEAD。**読み取り専用で使う** |
| Python 環境 | `/Users/s19447/Documents/piper-plus/.venv` | Python 3.13.9 / torch 2.11.0 |
