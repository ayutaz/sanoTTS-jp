# 意思決定と訂正の記録

決定の**理由**を残すためのファイル。「なぜそうなっているか」を後から辿れるようにする。
数値の根拠は [`measurements.md`](measurements.md) を参照。

---

## D-001: 教師は piper-plus の `.ckpt`。ONNX は使わない

- **日付**: 2026-08-26
- **決定**: `SynthesizerTrn.infer()` を PyTorch で呼ぶ

**理由**: ONNX export は `output` と `durations` しか出さず、蒸留に必要な**潜在 `z` が
取れない**（M-7）。`infer()` は `InferOutput(audio, attn, y_mask, (z, z_p, m_p, logs_p), durations)`
を返し、これが論文の `yT` / `zT` / `dT` にそのまま対応する。

---

## D-002: 教師 ckpt は `piper-plus-zero-shot-tsukuyomi/epoch=499-step=22000.ckpt`

- **日付**: 2026-08-26
- **決定**: HF private repo のこの 1 ファイルを教師とする

**理由**: HF の `ayousanz` 全 61 repo を調査した結果、

- 公開 repo の `piper-plus-tsukuyomi-chan` / `piper-plus-css10-ja-6lang` は **ONNX しか無い**
  → D-001 により使えない
- 公開 `.ckpt` は `piper-plus-base/model.ckpt` のみだが 571 話者 multilingual で単一話者ではない
- private に単一話者日本語の ckpt が複数あり、その中で `num_speakers=1` / `num_languages=6` /
  `quality: dataset-tsukuyomi-finetune-6lang` を満たすのがこれ

**不採用にしたもの**:

| 候補 | 不採用の理由 |
|---|---|
| `piper-jp-en-model/tsukuyomi-v4-*` | ja-en bilingual、`num_symbols=97` で別系統 |
| `piper-plus-tsukuyomi-chan-all/version_3/*` | WavLM 300epoch (1.96 GB)。系統が異なる |
| `piper-plus-base/model.ckpt` | 571 話者 multilingual |
| `piper-plus-tiny` | 571 話者 |

> **後の訂正**: 当初この選定根拠に「`config.json` が公開 canonical と一致する」を挙げたが、
> **これは同一性の証明にならない**（C-005）。選定自体は妥当だが根拠の書き方が誤りだった。

---

## D-003: piper-plus は HEAD (v2.0.0) をそのまま使う

- **日付**: 2026-08-26
- **決定**: `v1.13.0` への checkout も worktree も**しない**

**理由**: 当初「公開 base ckpt は FiLM 化で size mismatch する (Issue #616) ので
`v1.13.0` が必要」と考えたが、**実測したら不要だった**（M-2.4）。

- ckpt の `spk_embed_dim=192` で v2.0 の 192 次元チェックを通る
- `dec.cond.weight` が `(512,512,1)` + `cond_layers` あり = **元から post-FiLM**
- `load_state_dict` が **missing 0 / unexpected 0**、`cond_migrated=0`

むしろ `v1.13.0` に落とすと pre-FiLM 想定になって size mismatch する。

**運用**: piper-plus のリポジトリは**読み取り専用**で扱う（checkout / commit / 編集をしない）。
`sys.path.insert` で `src/python` を先頭に置く（M-1.1 の stale install 対策）。

---

## D-004: `speaker_embeddings=None` を渡す

- **日付**: 2026-08-26
- **決定**: ラベル生成では `None`。`eval/spk_tsukuyomi.npy` は渡さない

**理由**: この ckpt は `num_speakers=1` で `spk_proj` / `emb_g` を state_dict に 1 件も持たず、
**None / npy / ランダム 192 次元 の 3 通りで audio が bit 完全一致する**（M-3.1）。
何を渡しても無視されるので、意図を明示するために `None` を渡す。
話者はすでに重みに焼き込まれている。

---

## D-005: 音素化は `text_to_phoneme_ids_and_prosody` に `language_id_map` を渡して呼ぶ

- **日付**: 2026-08-26
- **決定**: 自前で音素→ID 変換を組まない

**理由**: `language_id_map` を渡すと multilingual に auto-promote され、
**トークン間に `_` の intersperse padding が入る**（`len(ids) ≒ 2*tokens+3`）。
これを飛ばすと**発話が約 2.4 倍速になる**（17.7 mora/s vs 正常な 7.6〜8.4、M-5）。

加えて自前実装は PUA 変換（M-4.2）を間違えやすく、間違えても例外が出ない。

---

## D-006: ライセンスは着手のブロッカーにしない

- **日付**: 2026-08-26（ユーザー判断）
- **決定**: 教師コーパスのライセンス確認を待たずに着手する

**理由**: 本プロジェクトは**検証 (PoC) で生成物を配布しない**ため。

**ただし公開する段になったら再確認が必要**:
- つくよみちゃんコーパス: `CC-BY-4.0 / verified: false`（規約は tyc.rei-yumesaki.net/about/terms/）
- MOE-Speech (20 speakers): `CC-BY-SA-4.0 / verified: false`
- **CC-BY-SA は蒸留物への継承の議論がある**

---

## D-007: ターゲットは ESP32 のみ。ブラウザは対象外

- **日付**: 2026-08-26（ユーザー判断）
- **決定**: 567 K embedded tier が唯一の成果物。1.4 M は検証用の足場

**理由**: **ブラウザは piper-plus の WebAssembly で既に解決済み**なので、そこを
再実装しても価値がない。「ESP32 に対応できなければ意味がない」。

**この決定が計画に与えた影響**:

| 項目 | 変更前 | 変更後 |
|---|---|---|
| 成果物 | 1.4 M quality (browser) を優先 | **567 K embedded (ESP32)** |
| 1.4 M の位置づけ | 本命 | **足場**（レシピが日本語で効くかの確認と、567 K との差分測定） |
| オンデバイス G2P | 「音素ID入力に割り切る」で回避 | **回避不可。成立しなければプロジェクトが無意味** |
| 最優先タスク | ラベル生成の疎通 | **B-0（G2P フットプリント spike）** |

**根拠となる観測**: 辞書 102 MB のうち **65.2% が feature 文字列**で、TTS には不要な
品詞・活用情報が大半（M-8）。枝刈り + feature 削減で数 MB に収まる可能性があり、
「102 MB だから不可能」という初期判断は雑すぎた。ただし線形概算なので B-0 で実測が必要。

**判断分岐**: B-0 で「数 MB で実文の 95%+ が正しく読める」が示せなければ、
入力境界の再定義（かな入力限定 / 定型文プリコンパイル / ホスト側 G2P）か
プロジェクト自体の見直しになる。

---

## D-008: 生徒の評価は「教師比」で報告する

- **日付**: 2026-08-26
- **決定**: SCOREQ / UTMOS の絶対値を英語モデルと比較しない。WER ではなく **CER** を主指標にする

**理由**: 論文自身がベトナム語・インドネシア語について
"we report only the ratio to the corresponding teacher because absolute SCOREQ values
are not calibrated for comparisons across languages" と明言している。
UTMOS も日本語では較正されていない（C-003）。
WER は日本語では分かち書きが問題になる。

---

## D-009: B-0 の結論 — G2P は端末に載せず、入力境界を音素ID列に置く

- **日付**: 2026-08-26
- **状態**: **測定完了。ユーザー判断待ちの論点が 3 つ残る**
- **一次データ**: [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md),
  `reports/b0_{size,coverage,alternatives,flash_budget,feature_fields}.json`

### 測定結果（すべて実ビルド + held-out 2,325 文）

| 問い | 答え |
|---|---|
| 16 MB ボード + OTA 2 枠 (11.73 MB) に収まる最大辞書 | **60k表層 +guard+noread = 11.57 MB** |
| その一致率 | **文単位 73.0%** / トークン単位 PER 2.36% (= **97.6%**) |
| 文単位 95% に必要な辞書 | **400k entries = 40.05 MiB** → **32 MB ボードにも入らない** |
| アクセント込み 95% | **400k / 40 MiB でも 89.29%。届かない** |

### さらに根本的な制約（辞書サイズと無関係）

教師の G2P (`pyopenjtalk.extract_fullcontext`) は **pyopenjtalk-plus の Python 後処理層**
（SudachiDict 207 MB + nani ONNX）を**デフォルトで通る**。これは ESP32 に載らない。
C 実装のみ (`use_vanilla=True`) に落とすと、**フル 103 MB 辞書でも**:

- 音素列 **95.4%**（自己実測 800 文。うち無声化のみの差が 1.88% なので、それを許容すれば 97.25%）
- アクセント **76.2%**（レポート、アクセント句単位）/ **84.0%**（自己実測、A1 単位）

**これは辞書を大きくしても縮まない下限。** ただし M-11 の通り、
**ラベル生成側も `use_vanilla=True` に揃えれば原理的に消える**（要検証）。

### 決定

**蒸留の入力境界を音素ID列に固定する**（論文 §II の "external G2P excluded" と同じ境界）。
CLAUDE.md の既存の判断が本 spike の実測で裏付けられた形。

- ホスト側 G2P: 端末フットプリント **0 B**、精度は教師と同一、回線 **68 B/文**
  （音声を Opus で流すより 2 桁小さいので「端末で合成する」利点は失われない）
- 完全スタンドアロン用途は**定型文プリコンパイル**: 1 B/音素、通知系 **40.4 B/文**、
  1,000 文で 40 KB。同じ文を PCM で焼くより 3,228 倍小さい
- **辞書枝刈りは実装対象から外す**

### 実務的に最も価値のある発見: guard set

頻度枝刈りをすると**全角数字が落ちて数値が無音で消える**。
`午後3時15分です。` → `ゴゴ、ジ、フンデス`（`９` は 60k 辞書にも入らない）。

未知語は**誤読ではなく無音で脱落する**（`unk.dic` の 40 エントリは読み・アクセントを
一切持たないため `、` に置換される）。例外も警告も出ない。

対策は「名詞,数」279 + 「*,接尾」2,254 + 「記号,*」202 = **2,705 表層形の強制追加**。
30k 水準で +531 KB の費用に対し、embedded セットの音素列一致が
**28.96% → 80.87%（+51.9pt）**、無音化 token が 194 → 27 instance。

**枝刈り路線を採らなくても、この「未知語は無音で消える」性質は
入力サニタイズの設計に効く**（外字・幽霊漢字はホスト側 G2P でも同じ失敗をする）。

### ⚠️ ユーザー判断が必要な 3 点

1. **「目安 95%」は文単位かトークン単位か。** 文単位なら「入力境界の再定義」、
   トークン単位なら 30k / 7.13 MB でも合格し「枝刈り続行」もあり得る。**判定が反転する**
2. **OTA を要件に含めるか。** 含めなければ 16 MB ボードの辞書枠は 11.19 → 13.19 MiB
3. **任意テキストを喋る必要があるか、定型文＋数値スロットで足りるか。**
   後者なら辞書問題は消える

### ⚠️ go/no-go を出す前に必要な残測定

- **RAM ピークとレイテンシが完全に未測定。** flash に入っても OOM すれば落ちる。
  ESP-IDF がこのマシンに無く、xtensa ビルドもしていない
- **B-1 との経路不一致**: B-0 は `JapanesePhonemizer`/`pyopenjtalk` 基準だが、
  教師の canonical 経路は `MultilingualPhonemizer`。かな無し行 5.72% が中国語に誤ルーティング
  される問題の上に全数値が乗っている

---

# 訂正の記録

**同じ間違いを繰り返さないための記録。** 私（Claude）が誤った記述をし、
実測または反証検証で訂正したもの。

## C-001: 「`spk_tsukuyomi.npy` を話者固定に使える」→ 誤り

- **誤った記述**: ラベル生成で `speaker_embeddings=spk_emb` を渡す
- **実際**: 何を渡しても bit 完全に無視される（M-3.1）
- **なぜ間違えたか**: repo に `eval/spk_tsukuyomi.npy` があったので、用途を確認せず
  「話者埋め込み参照ファイル」と解釈した。state_dict に `spk_proj` があるか確認していなかった
- **対応**: D-004

## C-002: 「`jp_phoneme_map.PHONEME_TO_PUA` を経由すること」→ 誤り

- **誤った記述**: ラベル生成器は `src/python/jp_phoneme_map.py` の表を使う
- **実際**: canonical は `piper_plus_g2p.encode.pua` の `TOKEN2CHAR` / `CHAR2TOKEN` (99 entry)。
  `jp_phoneme_map.get_phoneme_id_map()` は 58 entry / max id 57 で、
  **54 音素の id が ckpt と食い違う**（`a` が 7 vs 10）
- **危険度**: **高**。誤用すると音素ラベルが例外も出さずに総取り違えになる
- **なぜ間違えたか**: ファイル名から役割を推測した。実際に import して ckpt と突き合わせていなかった
- **対応**: M-4.2, D-005

## C-003: 「UTMOS は日本語データで訓練されている」→ 誤り

- **誤った記述**: UTMOS は日本語音声への適用が英語モデルより素性が良い
- **実際**: VoiceMOS Challenge 2022 の main track = BVCC（英語）/ OOD track = BC2019（中国語）。
  **日本語は含まれない**
- **なぜ間違えたか**: 著者が東大猿渡研なので学習データも日本語だろうと推測した
- **対応**: D-008。UTMOS も教師比で報告する

## C-004: 「`S_ja` に無声化母音 `A` `E` `O` を含める」→ 誤り

- **誤った記述**: `S_ja = {s, sh, ts, ch, z, j, h, hy, f, A, I, U, E, O}`
- **実際**: `A` / `E` / `O` はコーパス 23,271 行で**一度も出現しない**。
  日本語の母音無声化は狭母音 `i` `u` にほぼ限られる
- **訂正後**: `S_ja = {s, sh, ts, ch, z, j, h, hy, f, I, U}`

## C-005: 「`config.json` の一致が ckpt 選定の根拠」→ 不十分

- **誤った記述**: `config.json` が公開 canonical と一致するので同じモデル
- **実際**: ローカル ONNX と ckpt は `phoneme_id_map` まで完全一致するが、
  **同一文で durations が 98 vs 115 と別物**。config 一致は同一性の証明にならない
- **対応**: D-002 に注記。選定根拠は「単一話者日本語で、v2.0 にロードでき、
  決定的推論が取れる」ことに置き換えた

## C-006: EMA の適用漏れ

- **誤り**: Phase 0 スクリプトで `load_state_dict` のみ実行し、EMA を適用していなかった
- **実際**: `ema_generator_state` は `load_state_dict` では適用されない。
  適用有無で `yT` の SNR は 12.53 dB
- **対応**: `apply_ema_shadow_params()` を `remove_weight_norm()` の**前に**呼ぶ（M-2.5）

## C-007: 「教師が 2.4 倍速で喋る」→ 私の音素化ミス

- **誤った観測**: 教師が 17.7 mora/s（自然な日本語の約 2.4 倍）で喋っていると報告した
- **実際**: canonical 経路を使えば **8.4 mora/s** で正常。原因は私が
  **intersperse padding を飛ばして自前で音素→ID 変換した**こと（M-5）
- **なぜ間違えたか**: `JapanesePhonemizer.phonemize()` の出力をそのまま ID 化した。
  `text_to_phoneme_ids_and_prosody` の auto-promote 挙動を知らなかった
- **対応**: D-005。Phase 0 スクリプトに発話速度チェック（6〜10 mora/s）を追加

## C-009: M-8 の枝刈りサイズ線形概算 → 1.50〜2.74 倍の過小評価

- **誤った記述**: 131 B/entry から線形に「10k=1.2 MB / 30k=3.7 MB / 60k=7.5 MB」
- **実際**（B-0 の実ビルド）: **10k=3.29 / 30k=7.13 / 60k=12.13 / 100k=18.77 MB**
- **なぜ間違えたか**: (1) `matrix.bin` 3.79 MB / `char.bin` 262 KB を合計に入れ忘れた
  (2) darts trie が語数に線形に縮まないことを「⚠️ 未検証」と注記しただけで済ませ、
  数字自体は線形のまま出してしまった
- **教訓**: **予算境界の判断に概算を使わない。** 実ビルドして測る
- **対応**: M-8 を実測値で差し替え

## C-008: 「G2P 102 MB だから MCU には載らない」→ 結論として雑

- **誤った記述**: OpenJTalk 辞書は 102 MB なので ESP32 では不可能。音素ID入力に割り切る
- **実際**: サイズの **65.2% は品詞・活用などの feature 文字列**で TTS には不要。
  枝刈り + feature 削減で数 MB に収まる可能性がある（M-8）
- **なぜ間違えたか**: ディレクトリ全体のサイズだけ見て、内訳を調べなかった
- **対応**: D-007。B-0 として実測タスク化した

---

# 未決の判断事項

| # | 内容 | 必要な入力 | 期限 |
|---|---|---|---|
| 1 | **B-0 の結果を受けた続行判断** | 枝刈り辞書のサイズと実文カバー率 | 蒸留の実装より前 |
| 2 | B-1 の D1: G2P 経路の選択（言語ピン留め / 除外 / フォールバック） | `reports/b1_routing.json` | ラベル一括生成より前 |
| 3 | 567 K の日本語品質が実用に足るかの判定基準 | 1.4 M との差分、日本語 CER | Phase 6 |
| 4 | 教師品質が低かった場合の期待値再設定 | B-5 の再測定 | Phase 1 と並行 |
