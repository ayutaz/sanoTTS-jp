# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクトの目的

arXiv:2608.21378 "saanoTTS: The Smallest Real-Time Neural TTS on a General-Purpose Microcontroller"
のレシピを **日本語**に適用し、piper-plus (MB-iSTFT-VITS2) を教師とした
蒸留生徒モデルを作る。

論文の公式リポジトリ `github.com/Ampixa/saanotts` は 404 で参照実装が入手できないため、
**論文本文の数値からの再実装**になる。論文に書かれていないハイパーパラメータ
（`L_c` の `λ₂, λ_n, λ_Δ, λ_s`）はチューニング対象。

## ドキュメントの読み方

| ファイル | 役割 |
|---|---|
| **このファイル** | 実装時の要点だけ。**コードを書く前に必ず読む** |
| [`docs/measurements.md`](docs/measurements.md) | **数値の一次ソース**。全項目に再現コマンド付き。食い違ったらここが正 |
| [`docs/decisions.md`](docs/decisions.md) | 決定の理由と**訂正履歴**（同じ間違いを繰り返さないため） |
| [`docs/plan/phase0-1-implementation-plan.md`](docs/plan/phase0-1-implementation-plan.md) | 作業計画。B-0〜B-11 と Phase 0〜6 |
| [`docs/research/saanotts-jp-feasibility.md`](docs/research/saanotts-jp-feasibility.md) | 初期調査。論文の全数値と piper-plus の資産棚卸し |
| [`docs/README.md`](docs/README.md) | 索引と現在地 |

現状: Phase 0（教師の動作確定）まで完了。`scripts/phase0_verify_teacher.py` が 6 チェック PASS。
次は **B-0（オンデバイス G2P のフットプリント実測）**— プロジェクトの成否を決めるタスク。
実装コードはまだ無い（このファイルの「コマンド」節はコードが入り次第更新すること）。

## アーキテクチャ（固定仕様）

3つの決定的な生徒を **40次元の明示的な潜在インターフェース (c-line)** で繋ぐ。
このインターフェースを省いた merged モデルは論文の対照実験で崩壊している
(SCOREQ 1.06、訓練行の丸暗記) ので、**factored 構成は必須**。

```
音素ID x ─▶ Duration Dα (36,164) ─▶ Acoustic Aβ (199,536) ─▶ iSTFT Decoder Gγ (331,308) ─▶ 22.05kHz PCM
                width 32              width 48, 40ch出力         width 76, 1024pt iSTFT/hop 256
```

- 合計 567,008 params / int8 blob 2個 679,832 B / 45 MMAC per audio-second
- 学習専用の `z→c` エンコーダ `Eρ` (14,952) は**デプロイ時に実行されない**
  （acoustic が c を直接出す）。パラメータ数の勘定に入れないこと
- z-line 版（1.4 M 級, quality tier）は `Eρ` を省き 192ch の z を直接ターゲットにし、
  hinge adversary を追加する

## 教師モデルの扱い

教師は `/Users/s19447/Documents/piper-plus` の piper-plus。

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
                  prosody_features=prosody,  # 実 A1/A2/A3。ゼロ埋めは別物になる
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
融合してしまうため）。適用有無で `yT` の SNR は 12.53 dB しかない（`zT` / `dT` は bit 一致）。

**`prosody_features` にゼロテンソルを渡すのは「prosody 無し」ではない。**
`prosody_proj(0) = bias`（非ゼロ）が concat されるため、None / ゼロ / 実 prosody で
総フレーム数が 3 通りとも変わる。ラベル生成では必ず実 A1/A2/A3 を渡す。

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

ESP32-S3 (16 MB flash) はアプリ + IDF を引いても 12 MB 前後残るので、**数 MB の TTS 特化辞書は
物理的に載る**。⚠️ ただし上表は平均バイト数からの線形概算で、darts trie は線形には縮まない。
**実際に枝刈り辞書をビルドしてサイズと未知語率を実測すること**（計画書の最優先タスク）。

**ピッチアクセント。** piper-plus の日本語 duration predictor は OpenJTalk の
A1/A2/A3 を `prosody_dim=16` で注入しているが、論文の duration student は音素IDしか見ない。
まずは音素列に既に入っているアクセント記号 (`#` 句境界 / `[` 上昇 / `]` 下降核) で足りるか試す。
不足なら生徒 duration net に A1/A2/A3 の3スカラーを足す（width 32 なので増分は数百 params）。
**この良し悪しはアクセント型のミニマルペア（橋/箸/端、雨/飴）を評価セットに入れないと検出できない。**

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

**評価は「教師比」で報告する。** 論文はベトナム語・インドネシア語について
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

## piper-plus の参照点

| 用途 | パス（`/Users/s19447/Documents/piper-plus` 相対） |
|---|---|
| 教師の `infer()` | `src/python/piper_train/vits/models.py:1002` |
| 韻律 A1/A2/A3 の注入 | `src/python/piper_train/vits/models.py:870` (`_prepare_prosody_input`) |
| MB-iSTFT decoder | `src/python/piper_train/vits/mb_istft.py` |
| 日本語音素表 | `src/python_run/piper_plus/phonemize/jp_id_map.py` |
| 音素→PUA マップ（C++ と一致必須） | `src/python/jp_phoneme_map.py` |
| UTMOS22 / Whisper WER / PESQ / STOI | `scripts/audio_quality_metrics.py` （**SCOREQ は未実装、追加が必要**） |
| MOS リスニング調査 | `tools/benchmark/` (`docs/benchmark-mos.md`) |
| 日本語評価文のシード | `scripts/evaluation/evaluation_texts_ja.txt` |
| データセットのライセンス台帳 | `data-sources.yml` |

piper-plus の Python 環境は `uv` workspace（`.venv/`, Python 3.13, torch 2.11）。
教師を動かすスクリプトは piper-plus 側の venv で実行するのが早い:

```bash
/Users/s19447/Documents/piper-plus/.venv/bin/python <script>
```

## スコープ

**ターゲットは ESP32。ブラウザは対象外**（piper-plus の WebAssembly で既に解決済みのため、
そこを再実装しても価値が無い — 2026-08-26 ユーザー判断）。

したがって:
- **成果物は 567 K の embedded tier**（論文で SCOREQ 2.54 / ESP32-S3 で 0.22× RT）
- 1.4 M quality tier は**成果物ではなく検証用の足場**。蒸留レシピが日本語で機能するかを
  速く確かめ、567 K との品質差を測るためだけに作る
- **オンデバイス日本語 G2P が成立しなければプロジェクトの意味が無い**（上記「G2P」節）

**本プロジェクトは検証 (PoC) であり、生成物を配布しない。**
そのため教師コーパスのライセンス（つくよみちゃんコーパス `CC-BY-4.0 / verified: false`、
MOE-Speech `CC-BY-SA-4.0 / verified: false`）は着手のブロッカーにしない
(2026-08-26 ユーザー判断)。ただし **成果物を公開する段になったら再確認が必要**
— CC-BY-SA は蒸留物への継承の議論があるため。

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
import sys; sys.path.insert(0, "/Users/s19447/Documents/piper-plus/src/python")
# または PYTHONPATH=/Users/s19447/Documents/piper-plus/src/python
```

**教師を触るスクリプトは必ず `__file__` を assert して掴んだ実体を検証すること**
（`scripts/phase0_verify_teacher.py` がその実装例）。

## 音素化は canonical 経路を使う（自前で組まない）

```python
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody
ids, prosody = text_to_phoneme_ids_and_prosody(
    text, phoneme_id_map, language="ja", language_id_map=lim)   # lim を必ず渡す
```

**`language_id_map` を渡すと multilingual に auto-promote され、トークン間に `_` の
intersperse padding が入る**（`len(ids) ≒ 2*tokens + 3`）。これを飛ばして自前で
音素→ID を組むと **発話が約 2.4 倍速になる**（実測 17.7 mora/s、正常は 7.6〜8.4 mora/s）。
`scripts/phase0_verify_teacher.py` が速度チェックを含む実装例。

## ⚠️ ラベル生成の前に塞ぐ 2 つの欠陥

詳細と検証タスクは [`docs/plan/phase0-1-implementation-plan.md`](docs/plan/phase0-1-implementation-plan.md) §2。
**どちらも例外を出さず黙ってデータを壊すので、一括ラベル生成の前に必ず塞ぐこと。**

1. **G2P の言語誤ルーティング** — 教師の `phoneme_type` は `multilingual` だが、
   `MultilingualPhonemizer` は「かな」を**文全体**で判定するため、
   **かなを1文字も含まない行が丸ごと中国語音素になる**（`慶應義塾大学文学部、仏文学科卒業。`
   → 北京語の声調付き音素）。コーパス 23,271 行のうち **1,247 行 (5.36%)** が該当。
   符号化 id が `num_symbols=173` 未満に収まるため例外も警告も出ない。
2. **`prosody_features` の無警告ズレ** — `PiperEncoder._convert_prosody` が
   zip の前に長さを強制的に揃えるため、`strict=True` が原理的に発火しない。
   ラテン文字混じり文で空白がドロップされ、**prosody が末尾側にずれたまま通る**。
   prosody は総フレーム数を 8〜9% 動かす実効的な入力なので、実害のあるデータ破壊。

## 未解決のブロッカー（優先順）

**ターゲット tier は決着済み（567 K / ESP32）。B-0（G2P フットプリント）も測定完了**
— 結論と残論点は [`docs/decisions.md`](docs/decisions.md) D-009。

**B-0 の要点**: 16 MB ボードに収まる最大辞書 (60k / 11.57 MB) で文単位一致 73%、
文単位 95% には 40 MiB 必要で 32 MB ボードにも入らない。さらに教師の G2P が依存する
Python 後処理層 (SudachiDict 207 MB) は ESP32 に載らず、C 実装のみだと辞書サイズと
無関係にアクセントが 16〜24% ずれる。
→ **入力境界を音素ID列に固定し、辞書枝刈りは実装対象から外す。**

残るブロッカー:

1. **【ユーザー判断】「目安 95%」は文単位かトークン単位か / OTA を要件に含めるか /
   任意テキストが要るか定型文で足りるか。** 1 つ目で B-0 の判定が反転する（D-009）
2. **【go/no-go 前に必須】ESP32 の RAM ピークとレイテンシが未測定。**
   flash に入っても OOM すれば落ちる。ESP-IDF 未導入・xtensa ビルド未実施
3. **B-0 と B-1 の G2P 経路の不一致。** B-0 は `JapanesePhonemizer` 基準、
   教師の canonical は `MultilingualPhonemizer`。かな無し行 5.72% の中国語誤ルーティング
   の上に B-0 の全数値が乗っている
4. **567 K で日本語が実用に足るか。** 論文の英語実績は SCOREQ 2.54 / WER 14.8%。
   日本語 CER は未知。モーラ等時性は英語の強勢拍リズムより duration 予測が容易なはずで
   有利に働く可能性がある一方、摩擦音・無声化母音の多さは不利に働く。
   **1.4 M を先に作って上限を測り、567 K との差分で判断する。**
5. **教師音声の品質ベースラインが未確定** — 短尺 8 文の暫定計測で SCOREQ 2.06 / UTMOS 1.62 と、
   論文の教師 (4.68) から大きく外れた（計画書 §2 B-5）。
   **生徒は教師を超えない**ので、ここが低いままなら期待値を設定し直す必要がある。

## ⚠️ 未知語は誤読ではなく「無音で消える」

B-0 の実測。`unk.dic` の 40 エントリは**読み・アクセントを一切持たない**ため、
未知語は `njd_set_pronunciation.c` の規則で `、`（読点）に置換される。
**例外も警告も出ず、語が丸ごと音声から消える。**

```
齟齬 → 無音        蜃気楼 → 蜃気(無音) + 楼(無音)
氷点下 → コーリ+テン+カ（1文字ずつ既知だと誤読になる）
```

**フル辞書でも起きる**（外字・幽霊漢字）ので、ホスト側 G2P に倒しても
入力サニタイズは別途必要。
