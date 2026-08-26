# 実測値の記録

**このファイルが数値の一次ソース。** 他のドキュメントと食い違ったらここが正。
全項目に再現コマンドを付ける。推測は書かない — 測っていないものは「未測定」と明記する。

共通の前提:

```bash
export PP=/Users/s19447/Documents/piper-plus
export PY=$PP/.venv/bin/python
export PYTHONPATH=$PP/src/python:$PP/src/python/g2p
export SNAP=~/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/c3f236e068b95356b871842b4ae7cec2a86c50ea
```

---

## M-1. 環境

| 項目 | 実測値 |
|---|---|
| piper-plus バージョン | 2.0.0 (`VERSION`) |
| Python | 3.13.9 |
| torch | 2.11.0 |
| HF 認証 | `ayousanz` として `whoami()` が通る |

### ⚠️ M-1.1 stale な `piper_train`（重要）

`.venv/lib/python3.13/site-packages/piper_train/` に **v1.13.0 相当の古いコピー**が実在する。

```bash
$PY -c "import piper_train.vits.models as M; print(M.__file__)"
# → .venv/lib/python3.13/site-packages/piper_train/vits/models.py   ← 古い方

$PY -c "import sys; sys.path.insert(0,'$PP/src/python'); \
        import piper_train.vits.models as M; print(M.__file__)"
# → /Users/s19447/Documents/piper-plus/src/python/piper_train/vits/models.py   ← 正しい
```

**原因**: 古いコピーは別ディストリビューション `piper_plus_workspace-1.12.0` の所有物なので
`pip install -e src/python` をやり直しても消えない。さらに setuptools の editable finder が
`sys.meta_path` に **append**（insert ではない）されるため、標準の `PathFinder` が先に走る。

**対策**: `sys.path.insert(0, ...)` または `PYTHONPATH`。
**教師を触るスクリプトは `__file__` を assert して掴んだ実体を検証すること。**

---

## M-2. 教師 checkpoint

### M-2.1 同定

| 項目 | 値 |
|---|---|
| repo | `ayousanz/piper-plus-zero-shot-tsukuyomi` (HF **private**) |
| ファイル | `epoch=499-step=22000.ckpt` |
| サイズ | 927,048,022 B |
| SHA-256 | `f375c749caa2a707b3fc9ee672142bdc1441bcbcdd3b523dd9efdb18b017683e` |
| snapshot | `c3f236e068b95356b871842b4ae7cec2a86c50ea` |
| 付属 | `config.json`, `eval/spk_tsukuyomi.npy`, `eval/compute_secs.py`, `tsukuyomi-ft-epoch499-zs.onnx` (38.2 MB) |

### M-2.2 `hyper_parameters`

```bash
$PY -c "
import torch; ck=torch.load('$SNAP/epoch=499-step=22000.ckpt',map_location='cpu',weights_only=False)
print(ck['hyper_parameters'])"
```

```
num_symbols=173            num_speakers=1           num_languages=6
inter_channels=192         hidden_channels=192      gin_channels=512
spk_embed_dim=192          use_zero_shot=True       prosody_dim=16
resblock=2                 use_sdp=True             segment_size=8192
upsample_rates=(4,4)       upsample_kernel_sizes=(16,16)
upsample_initial_channel=256
```

### M-2.3 `state_dict` の構造

| prefix | テンソル数 |
|---|---:|
| `model_g` | 686 |
| `model_d` | 111 |
| `pqmf` | 3 |
| `sub_stft_loss` | 3 |
| **計** | **803** |

主要テンソルの形状:

```
model_g.enc_p.emb.weight      (173, 192)      ← num_symbols=173 と一致
model_g.enc_p.proj.weight     (384, 192, 1)
model_g.prosody_proj.weight   (16, 3)         ← A1/A2/A3 → prosody_dim=16
model_g.dec.cond.weight       (512, 512, 1)   ← post-FiLM
model_g.dec.cond_layers.0     (256, 512, 1)
model_g.dec.cond_layers.1     (128, 512, 1)
```

`spk_proj` / `emb_g` を含むキー: **0 件**（M-3.1 参照）

### M-2.4 piper-plus v2.0 へのロード

```bash
$PY scripts/phase0_verify_teacher.py
```

```
state_dict: missing=0 unexpected=0  normalize stats={'stripped': 0, 'cond_migrated': 0}
```

**`v1.13.0` への checkout は不要。HEAD (v2.0.0) でそのまま載る。**
`cond_migrated=0` は pre-FiLM からの移行が発生していない = 元から post-FiLM である証拠。

構築される `SynthesizerTrn` のパラメータ数: **30,836,216**

### M-2.5 EMA

```
ema_generator_state = {decay: 0.9995, num_updates: 11000, shadow_params: 53 個}
ema_spk_proj_state  = None（num_speakers=1 で spk_proj が無いため死んでいる）
```

**`load_state_dict` では適用されない。** 明示的に適用する必要がある:

```python
from piper_train.export_onnx import apply_ema_shadow_params
apply_ema_shadow_params(model.dec, ckpt["ema_generator_state"]["shadow_params"])
model.dec.remove_weight_norm()   # ← 必ず EMA 適用の「後」
```

`remove_weight_norm()` が `weight_g`/`weight_v` を融合してしまうため**順序が逆だと効かない**。
適用有無で `yT` の SNR は 12.53 dB（`zT` / `dT` は bit 一致）。

---

## M-3. 教師への入力

### M-3.1 `speaker_embeddings` は完全に無視される

```bash
$PY - <<'PY'
# None / spk_tsukuyomi.npy / ランダム192次元 の3通りで infer
print("None vs npy   :", torch.equal(run(None).audio, run(npy).audio))
print("None vs random:", torch.equal(run(None).audio, run(torch.randn(1,192)).audio))
PY
```

```
None vs npy   : audio bit一致 = True
None vs random: audio bit一致 = True
```

この ckpt は `num_speakers=1` で `spk_proj` / `emb_g` を持たないため、**何を渡しても
bit 完全に無視される**。話者は重みに焼き込まれている。

- **ラベル生成では `speaker_embeddings=None` を渡す**
- `eval/spk_tsukuyomi.npy` は shape `(192,)` / float32 / L2 ノルム 1.0 だが、
  **SECS 評価専用でモデル入力ではない**

### M-3.2 `lid` は焼き込まれていない → `lid=0` 必須

`_get_global_conditioning` が `g = lang_emb` を enc_p / dp / flow / dec 全部に渡す。
`lid=1` にすると総フレーム数が変わり `z` も別物になる。**`lid=torch.tensor([0])` (ja) 固定。**

### M-3.3 `prosody_features` のゼロテンソルは「prosody 無し」ではない

`models.py:891-921` は `None` のとき `torch.zeros` を concat するが、
**ゼロテンソルを渡すと `prosody_proj(0) = bias`（非ゼロ）が concat される**。
実測で総フレーム数が 3 通りとも異なる（実 prosody 115 / ゼロ 106 / None 119）。

**ラベル生成では必ず実 A1/A2/A3 を渡す。**

---

## M-4. 音素化（canonical 経路）

### M-4.1 使うべき関数

```python
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody
ids, prosody = text_to_phoneme_ids_and_prosody(
    text, phoneme_id_map, language="ja", language_id_map=lim)   # lim を必ず渡す
```

**`language_id_map` を渡すと multilingual に auto-promote され、トークン間に `_` の
intersperse padding が入る**（`len(ids) ≒ 2*tokens + 3`）。これを飛ばすと発話が約 2.4 倍速になる
（M-5 参照）。

### M-4.2 音素表の3つの落とし穴

| # | 内容 | 実測 |
|---|---|---|
| 1 | `config.json` の `phoneme_id_map` は **185 entry / max id 184** だが `num_symbols=173`。**有効 id は 0..172** | 日本語在庫は全部 id ≤ 64 なので安全 |
| 2 | 拗音・破擦音は生の文字列で入っていない（**PUA 経由**） | `phoneme_id_map["ch"]` は KeyError |
| 3 | **`jp_phoneme_map.get_phoneme_id_map()` を使ってはいけない** | 58 entry / max id 57。**54 音素の id が ckpt と食い違う** |

canonical な PUA 変換表は **`piper_plus_g2p.encode.pua` の `TOKEN2CHAR` / `CHAR2TOKEN` (99 entry)**:

```
'ch' → U+E00E → ckpt id 46      'ky' → U+E006 → ckpt id 33
'sh' → U+E010 → ckpt id 49      'a:' → U+E000 → ckpt id 20
'ts' → U+E00F → ckpt id 47      'cl' → U+E005 → ckpt id 30
```

id 食い違いの例（`jp_phoneme_map` → ckpt）: `#` 4→7, `[` 5→8, `]` 6→9, `a` 7→10, `i` 8→11, `u` 9→12
**誤用すると音素ラベルが例外も出さずに総取り違えになる。**

---

## M-5. 発話速度

日本語の自然な発話は **6〜8 mora/s**。

### M-5.1 canonical 経路（正しい）

| テキスト | ids | frames | 秒 | mora/s |
|---|---:|---:|---:|---:|
| 今日は良い天気ですね。散歩に行きましょう。 | 90 | 225 | 2.61 | **8.4** |
| 本日の会議は午後三時から、第二会議室で行います。 | 138 | 325 | 3.77 | **7.7** |
| 電源を入れてから、しばらくお待ちください。 | 90 | 238 | 2.76 | **7.6** |

### M-5.2 intersperse padding を飛ばした場合（誤り）

同じ1文目を自前で音素→ID 変換（44 tokens + BOS/EOS = 46 ids）:

| noise_scale_w | frames | 秒 | mora/s |
|---:|---:|---:|---:|
| 0.0 | 107 | 1.24 | **17.7** ← 約 2.4 倍速 |
| 0.5 | 115 | 1.34 | 16.5 |
| 0.8 | 129 | 1.50 | 14.7 |

`length_scale` で補正した場合（`noise_scale_w=0`）:

| length_scale | 秒 | mora/s |
|---:|---:|---:|
| 1.0 | 1.24 | 17.7 |
| 2.0 | 2.28 | 9.7 |
| 2.5 | 2.79 | 7.9 |

**結論**: 速度異常の原因は `noise_scale_w` でも `length_scale` でもなく
**intersperse padding の欠落**。canonical 経路を使えば `length_scale=1.0` で正常。

---

## M-6. 決定的推論とラベル

```bash
cd /Users/s19447/Desktop/saanoTTS-jp && $PY scripts/phase0_verify_teacher.py
```

論文 §II の条件 `noise_scale=0 / noise_scale_w=0 / length_scale=1` で:

```
テキスト: 今日は良い天気ですね。散歩に行きましょう。
音素ID 90 個 (max=64), prosody 90 個

yT audio    : (1, 1, 57600)  (2.612 s)
zT latent   : (1, 192, 225)
dT durations: (1, 90)
発話速度    : 8.4 mora/s

[PASS] zT が 192ch (論文の教師潜在と一致)
[PASS] hop 256 (frames × 256 == audio samples)
[PASS] audio が bit 完全一致 (決定的)
[PASS] z が bit 完全一致 (決定的)
[PASS] dT の長さが音素数と一致
[PASS] 発話速度が自然な範囲 (6-10 mora/s)
```

| 検証項目 | 結果 |
|---|---|
| `zT` のチャネル数 | **192** — 論文の教師潜在と一致 |
| フレーム整合 | `zT.shape[-1] * 256 == len(yT)` が厳密成立（hop 256 の確認） |
| フレームレート | 22050 / 256 = **86.13 fps** — 論文の decoder と一致 |
| 決定性 | 二回実行で `audio` / `z` が bit 完全一致 |
| `dT` の長さ | 音素ID数と一致 |

---

## M-7. 公開 ONNX との関係

```bash
shasum -a 256 $PP/models/tsukuyomi.onnx
```

| 項目 | 値 |
|---|---|
| ローカル `models/tsukuyomi.onnx` | SHA-256 `5289e9b6eaf21080803b7fe1c4dc85b5491d4c216121207a41df18dd5f68e5d7` |
| 公開 `tsukuyomi-chan-6lang-fp16.onnx` | **同一 SHA-256** / 39,652,717 B |
| パラメータ数 | 19,335,311 |
| 入力 | `input`, `input_lengths`, `scales[3]`, `lid`, `prosody_features[B,T,3]`, `speaker_embedding[B,256]`, `speaker_embedding_mask[B,1]` |
| 出力 | `output`, `durations` |

**この ONNX からは蒸留できない** — 潜在 `z` が出力されない。

**注意**: この ONNX と教師 ckpt は `phoneme_id_map` まで完全一致するので、
**`config.json` の一致はモデル同一性の証明にならない**。実際、同一文で durations が
98 vs 115 と別物になる。ONNX 側は 173 音素・pre-FiLM の旧系統で、その
`speaker_embedding[B,256]` 入力は xavier 初期化のまま学習されていない死んだ入力。

---

## M-8. OpenJTalk 辞書のフットプリント

**B-0（プロジェクトの成否を決めるタスク）の入力データ。**

```bash
ls -laS $PP/build/share/open_jtalk/dic/
$PY -c "
import struct
h=open('$PP/build/share/open_jtalk/dic/sys.dic','rb').read(72)
print(struct.unpack('<10I', h[:40]))"
```

| ファイル | サイズ |
|---|---:|
| `sys.dic` | 103,082,017 B |
| `matrix.bin` | 3,792,262 B |
| `char.bin` | 262,496 B |
| `left-id.def` / `right-id.def` | 各 77,672 B |
| `unk.dic` | 5,690 B |
| **合計** | **102 MB** |

`sys.dic` のヘッダから読んだ内訳:

```
version = 102        lexsize (エントリ数) = 788,923      平均 131 B/エントリ

darts trie      23,216,752 B  (22.5%)
token           12,622,768 B  (12.2%)
feature 文字列  67,242,425 B  (65.2%)   ← 品詞・活用など TTS に不要な情報が大半
```

`gzip sys.dic` → 20.7 MB（それでも ESP32 の flash には大きい）

### ⚠️ 訂正: 線形概算は 1.50〜2.74 倍の過小評価だった（B-0 で実測）

当初ここに「131 B/entry からの線形概算」を載せていたが、**B-0 で実際にビルドして
測ったら大幅に外れていた**。原因は 2 つ:

1. **`matrix.bin` (3.79 MB) / `char.bin` (262 KB) / `unk.dic` を合計に入れていなかった**
2. **darts trie は語数に線形に縮まない**（実測 34.18〜42.37 B/表層形と非単調）

| 表層形数 | 当初の概算 | **実ビルド (measured)** | 倍率 |
|---:|---:|---:|---:|
| 10,000 | 1.2 MB | **3.29 MB** | 2.74× |
| 30,000 | 3.7 MB | **7.13 MB** | 1.93× |
| 60,000 | 7.5 MB | **12.13 MB** | 1.62× |
| 100,000 | 12.5 MB | **18.77 MB** | 1.50× |

**予算境界での線形外挿は禁止。** 実ビルドして測ること。
詳細は [`research/b0-g2p-footprint.md`](research/b0-g2p-footprint.md)。

参考: ESP32-S3 16 MB ボードで OTA 2 枠を確保した場合の辞書予算は **11,730,944 B**
（`reports/b0_flash_budget.json`。app 枠 2,048 KB/slot は estimated）。

---

## M-9. コーパス（ワークフロー調査による。未再検証）

> **B-0 で確定 (2026-08-26)**: 23,271 行 = `pool.tsv` 12,211 + Common Voice 11,060。
> 分割は train 20,946 / heldout 2,325（層化 90/10、5-gram Jaccard ≥ 0.7 の近傍重複を train に戻した）
> + 手書き embedded 183。実ファイルは `data/splits/` にある。

| ソース | 行数 |
|---|---:|
| JSUT | 7,696 |
| ROHAN4600 | 4,600 |
| ITA | 424 |
| Common Voice ja | 11,060 |
| **生計** | **23,780** |
| NFKC 重複排除後 | **23,271** |

論文の学習行数 14,343 の 1.62 倍。

### 検出された問題

| 内容 | 規模 |
|---|---|
| `MultilingualPhonemizer` で JA と出力が食い違う行 | **1,247 行 (5.36%)** |
| 日本語音素表 65 エントリ外のトークンを出す行 | 1,200 行 (5.16%) |
| JSUT `countersuffix26`（助数詞の読み分け）が該当 | **25 / 26 行 (96.15%)** |
| 無声化母音 `A` / `E` / `O` の出現 | **0 回**（`I` / `U` のみ実在） |

---

## M-10. 教師音声の品質ベースライン（B-5・確定）

再現:
```bash
uv run --extra eval python scripts/b5_teacher_baseline.py   # 24 文を合成
uv run --extra eval python scripts/b5_measure_mos.py        # UTMOS を測る
```

### 前回の暫定値を棄却した理由

暫定計測は **8 文・1.3〜3.6 秒**で SCOREQ 2.06 / UTMOS 1.62 だった。
今回は **24 文・平均 5.16 秒（最短 3.38 / 最長 9.04）・前後 0.3 秒パディング**、
canonical な音素化経路（発話速度 6.87 mora/s で正常）で測り直した。

### 結果

| 対象 | n | UTMOS mean | median | sd |
|---|---:|---:|---:|---:|
| **教師（合成）** | 24 | **1.748** | 1.722 | 0.254 |
| **実人間音声**（つくよみちゃんコーパス、教師の元データ） | 24 | **2.305** | 2.336 | 0.220 |

**発話長との相関 r = −0.372。短尺アーティファクトではない**（むしろ長いほうが低い）。

### ⚠️ 単独の絶対値は解釈できない。UTMOS は日本語でスケールが圧縮されている

**実人間の日本語音声ですら UTMOS 2.305 しか出ない。** 論文の教師の 4.42 と
比較してはいけない。同一エンジン・同一設定で言語だけ変えた piper-plus の
デモ音声でも系統差が出る:

| 言語 | UTMOS |
|---|---:|
| ES | 3.506 |
| PT | 3.248 |
| EN | 2.970 |
| FR | 2.229 |
| **JA（実人間）** | **2.305** |
| **JA（教師）** | **1.748** |
| ZH | 1.800 |

UTMOS の学習データは VoiceMOS Challenge 2022 の main = BVCC（英語）/
OOD = BC2019（中国語）で、**日本語を含まない**（C-003）。

### 正しい読み方: 人間音声を天井とした比

```
教師 / 実人間 = 1.748 / 2.305 = 0.758
```

**教師は実人間音声の 76% の位置にいる。** 「SCOREQ 2.06 だから壊滅的」という
当初の解釈は誤りだった。ただし論文の教師は自身の言語の人間音声とほぼ同水準
（英語 BVCC の自然音声は UTMOS スケールの上限付近）なので、**本プロジェクトの教師は
論文の教師より相対的に低い位置にいる**ことは事実。

### 生徒の目標値への含意

生徒は**教師比**で報告する（D-008）。さらに本測定から、**人間音声比**も併記すべき:

```
論文の英語 embedded:  生徒 2.80 / 教師 4.42 = 0.63×teacher
本プロジェクトの目標: 0.63 × 1.748 ≒ UTMOS 1.10（教師比 0.63 を維持する場合）
                     人間比では 0.48×
```

⚠️ **SCOREQ は未測定**（pip パッケージ未導入）。UTMOS だけで判断しないこと。
⚠️ 人間音声の対照は**教師の元コーパスそのもの**なので話者・録音条件が揃っている点は良いが、
n=24 でありネイティブ聴取は行っていない。

---

## M-11. C 実装のみの G2P と教師経路の乖離（B-0 の核心・自己実測）

**piper-plus の日本語 G2P は `pyopenjtalk.extract_fullcontext()` を呼ぶ**
(`src/python/g2p/piper_plus_g2p/japanese.py:139`)。中身は **pyopenjtalk-plus 0.4.1-post8** で、
`use_sudachi_kanji_yomi=True` / `predict_nani=True` が**デフォルト有効**。

この Python 後処理層は ESP32 に載らない:

| 依存 | サイズ |
|---|---:|
| `sudachidict_core` | **207.1 MB** |
| `sudachipy` | 7.9 MB |
| `nani_model.onnx` | 0.13 MB |

`use_vanilla=True`（= C 実装のみ、ESP32 で再現可能な範囲）との差を held-out 800 文で実測:

```bash
$PY - <<'EOF'   # 全文は git log / docs/research/b0-g2p-footprint.md 参照
labs_default = pyopenjtalk.extract_fullcontext(t)
labs_vanilla = pyopenjtalk.extract_fullcontext(t, use_vanilla=True)
EOF
```

```
held-out 800 文  default(Python後処理あり) vs use_vanilla(C実装のみ)
  音素列         一致 763/800 = 95.38%
  A1(アクセント)  一致 672/800 = 84.00%

音素列が違った 37 文の内訳:
  長さが違う(読み自体が別)   17 文 (2.12%)
  無声化のみ (i↔I, u↔U)     15 文 (1.88%)
  読みが違う                 5 文 (0.62%)

→ 無声化のみの差を許容すれば音素列一致は 97.25%
```

**含意**: フル 103 MB 辞書を載せられたとしても、C 実装だけでは音素列が 4.6% /
アクセントが 16% ずれる。**これは辞書サイズと無関係の下限**。

⚠️ ただし **「一致しない」は「間違い」ではない**。この測定は「Python 後処理層ありの出力と
同じか」であって、どちらが正しいかは測っていない。無声化の差 (1.88%) は
知覚的にほぼ無害と考えられる。

⚠️ **さらに重要**: 蒸留ラベルの生成にどちらの経路を使うかは**こちらで選べる**。
ラベル生成時も `use_vanilla=True` を使えば、学習と ESP32 デプロイで G2P が一致し、
この乖離は原理的に消える（未検証。§未測定 参照）。

---

## M-12. ひらがな入力に限定した場合（自己実測）

**問い**: 入力仕様をひらがなのみに変更すれば、辞書問題は消えるか。

### 読み: **解決する**

かな→音素は決定的な規則で書ける。`reports/b0_alternatives.json` の実測:

| 項目 | 値 |
|---|---:|
| mora テーブル (162 mora) | **1,644 B** |
| OpenJTalk の全規則テーブル（無声化・長音・数詞など込み、文字列のみ） | 20,511 B |
| 音素 PER (held-out) | 1.178% |
| **無声化を正規化した PER** | **0.272%** |
| 無声化を正規化した文単位一致 | 90.41% |

**40 MB の辞書が 1.6 KB の表に置き換わる。**

### アクセント: **解決しない。しかも辞書を積んでも直らない**

漢字かな交じり文 → ひらがな化 → **フル 103 MB 辞書**で再解析し、
元の漢字版と比較（held-out 500 文、自己実測）:

```
音素列    一致 420/500 = 84.00%
アクセント 一致  75/500 = 15.00%   ← 核心
```

実例:

```
今日は良い天気ですね。   → ky o ] [ o w a y o ] [ i t e ] [ N_ng k i d e s U n e
きょうはよいてんきですね。 → ky o ] [ o w a y o ] [ i t e   [ N_ng k i d e ] s U n e
                                                    ↑ 「てんき」のアクセント核が動く
```

**原因は辞書サイズではなく分割の曖昧性。** かなにすると語境界と語義が失われるので、
Viterbi でも復元できない。したがって**かなキーのアクセント辞書を積んでも上限は同じ 15%**。

参考: 発音をキーにしたアクセント辞書自体は小さい
（340,271 ユニークキー / 素 6.23 MB、コーパス語彙に枝刈りすれば ~0.16 MB で token 98%）。
だが上記の理由で**サイズの問題ではない**ので、この案は採らない。

⚠️ 15.7% のかなキーは本質的に多義（`ハシ` は 0/2 と 1/2 の両方が存在）。

### 結論: アクセントは入力に持たせる

アクセント記号 `[`（上昇）/ `]`（下降核）/ `#`（句境界）は
**すでに音素列のトークン**なので、入力がこれを含めば端末側の推論は不要になる。

| 案 | 端末フットプリント | アクセント精度 |
|---|---:|---|
| **ひらがな + アクセント記号** | **~2 KB** | **教師と同一** |
| ひらがなのみ + かなアクセント辞書 | 0.16〜15 MB | **15%（辞書を積んでも上限）** |
| ひらがなのみ・平板 | ~2 KB | 平板（棒読み） |

---

## M-13. アクセントはアーキテクチャで対応済み — 音素IDとして入る（自己実測）

**問い**: 論文はアクセントをどう扱ったのか。VITS のアーキテクチャで対応できないのか。

### 答え: 論文は特別なことを何もしていない。強勢/アクセントは**音素IDの一種**

教師の `phoneme_id_map` を引くと、両方が普通の音素として登録されている:

```
日本語 (OpenJTalk):  '[' → id 8   ']' → id 9   '#' → id 7
英語   (eSpeak NG):  'ˈ' → id 87  'ˌ' → id 88  'ː' → id 89
```

**論文の英語モデルは eSpeak の強勢マーク `ˈ`/`ˌ` を音素IDとして受け取っている。
日本語のアクセント記号 `[`/`]`/`#` はまったく同じ機構に乗る。**

### VITS にはピッチ予測器が無い。ピッチは潜在 z の中にある

FastSpeech2 と違い VITS は明示的な F0 予測器を持たない。経路は:

```
音素ID (アクセント記号込み) → enc_p → m_p, logs_p → z (192ch, ピッチを含む) → decoder → 波形
```

生徒側も同じで、`Aβ(x, d̂) → ĉ (40ch)` の `x` にアクセント記号が入っていれば、
**生徒はピッチアクセントを学習できる。アーキテクチャ上の欠落は無い。**

### 実測: 分節音を完全に固定し、アクセント記号だけ動かす

```
頭高(箸) h a ] [ sh i o m o ] [ ts u    ← 13 トークン
尾高(橋) h a [ sh i ] o m o ] [ ts u    ← 13 トークン（] の位置だけ違う）
```

```
有声フレームでの F0 平均絶対差: 42.8 Hz (最大 61.3 Hz)
z の平均絶対差: 0.4082
```

**アクセント記号の位置を 1 つ動かすだけで F0 が 40 Hz 以上変わる。**

### `prosody_features` (A1/A2/A3) はピッチに効かない。duration にだけ効く

`models.py:940-970` を読むと:

```python
x, m_p, logs_p, x_mask = self.enc_p(x, x_lengths, g=g)      # ← prosody を通さない
x_dp = self._prepare_prosody_input(x, x_mask, prosody_features, lid=lid)
logw = self.dp(x_dp, x_mask, g=g, ...)                       # ← prosody はここだけ
```

`m_p` / `logs_p`（= z の分布 = ピッチ）は **prosody を通らない enc_p の出力**から作られる。
prosody は duration predictor にしか入らない。

**含意**: 論文の生徒設計（acoustic は音素IDと duration のみを見る）は日本語でも正しい。
A1/A2/A3 を生徒に足す必要があるとすれば duration net の側だけで、ピッチのためではない。

### プロジェクトへの含意

**アクセントは蒸留の問題ではなく G2P の問題**。生徒は入力にアクセント記号があれば学習できる。
記号を正しく入れるのが辞書の仕事で、そこが M-12 / D-010 の論点につながる。

---

## M-14. かな中間表現の変換器（実装・実測）

実装: `scripts/kana_g2p.py`。再現: `$PY scripts/kana_g2p.py`

### 端末側に載るデータ

```
mora テーブル  116 エントリ / 951 B
ん の異音規則  18 件
```

### カバー率と往復精度

| 評価セット | 表現可能 | 往復の完全一致 | 中間表現のサイズ |
|---|---:|---:|---:|
| held-out 1,500 文 | **96.40%** | **100.00%** | 平均 108 B/文 |
| embedded 184 文 | **98.91%** | **100.00%** | 平均 65 B/文 |

### end-to-end 検証

中間表現 → 規則だけで音素ID → 教師 → 音声 が、**漢字経路と bit 完全一致**（5/5 文）。

```
今日は良い天気ですね。  → きょ][おわよ][いて][んきです°ね   音声bit一致=True
橋を渡る。            → は[し]おわ[たる                音声bit一致=True
箸を持つ。            → は][しおも][つ                音声bit一致=True
```

カタカナ語も含めたセルフテストは 10/10 一致（`コンピューター` `シャットダウン` `バッテリー` 等）。
**表記ゆれは起きない** — 中間表現はホスト側が決定的に生成するため。

### ⚠️ mora テーブル導出で踏んだ 3 つの罠

`build_mora_table()` は各かなをキャリアに入れて音素を実測するが、キャリア選びで 3 回失敗した:

| キャリア | 症状 | 原因 |
|---|---|---|
| `あ_あ` | `へ → ['e']` | **助詞と解釈された** |
| `か_か` | `し → ['sh','I']` | 無声子音に挟まれて**無声化** |
| `ま_ま` | `す → ['s','U']` | `ま+す+ま` が**「ます」という実在語**を作り無声化 |

最終形は `ま_ま` キャリア + (a) 導出後に無声化を基底形へ正規化 + (b) 助詞になる
`は` `へ` `を` を明示的に上書き。**テーブルは常に有声の基底形を持ち、無声化は `°` が担う。**

### 表現できない 3.6% の内訳

アクセント句境界 `#` が**モーラ内部**（子音と母音の間）に現れるケースが主
（`['t', '#', ...]` のような並び）。中間表現がモーラ単位のため表現できない。要調査。

---

## M-15. 実行環境と学習コストの見積もり

### uv 環境は piper-plus venv と bit 一致する

本プロジェクトは `uv` で独立した環境を持つ（`pyproject.toml` / `uv.lock`）。
piper-plus は **path 依存 (editable)** で参照するだけで、リポジトリは変更しない。

| 環境 | Python | torch |
|---|---|---|
| piper-plus `.venv` | 3.13.9 | 2.11.0 |
| **本プロジェクト `uv`** | **3.14.0** | **2.13.0** |

バージョンが違うが、**教師ラベルは bit 完全一致する**（SHA-256 で照合）:

```
今日は良い天気ですね。  audio e1bebfcc553e67a9  z fe7b3ccaa246bf07  d 1c92df6d5a839fe7
電源を入れてください。  audio a835df26fbd397c6  z 8d0f9f9b841017e5  d 240c6e5a2da76e99
橋を渡る。            audio 42aa518a10372198  z 2ce62ea5a909de61  d ebaa7befd11b42a0
```

**含意**: ラベル生成をローカルでもリモート（vast.ai）でも実行でき、結果が同じになる。

**副次的な利益**: uv の venv には M-1.1 の stale な `piper_train`（v1.13.0 相当）が
存在しないため、`sys.path.insert` なしで正しいモジュールが解決される。

```
piper_train:    /Users/s19447/Documents/piper-plus/src/python/piper_train/vits/models.py
piper_plus_g2p: /Users/s19447/Documents/piper-plus/src/python/g2p/piper_plus_g2p/__init__.py
```

⚠️ **GPU 推論の bit 一致は未検証。** 上記は CPU 同士の比較。GPU は別カーネル・TF32 等で
差が出る可能性がある。ラベル生成は一度だけ実行し、パックに SHA-256 を付けて固定すること。

### 教師ラベル生成のコスト（実測）

M3 Pro CPU で 60 文を計測:

```
186 ms/文   平均 252 frames / 2.93 秒音声 / 102 音素ID
```

学習用 20,946 文への外挿:

| 項目 | 値 |
|---|---:|
| CPU 逐次の所要時間 | **1.1 時間** |
| 生成される音声の総量 | 17.1 時間分 |

### ラベルパックのサイズ（20,946 文）

> **⚠️ 訂正 (2026-08-27)**: 当初 4.42 GiB としていたが、**サンプルの偏りで 26% 過小**だった（C-014）。

サンプルの取り方で 26% 変わる:

| サンプル | n | `n_ids` mean | frames 換算 | pack (fp16+int16) |
|---|---:|---:|---:|---:|
| 先頭 60 行（当初 M-15 の条件） | 60 | 101.7 | 260 | 4.56 GiB / 4.89 GB |
| **無作為 400 行** | 400 | **128.3** | **328** | **5.75 GiB / 6.17 GB** |

**採用: 約 5.8 GiB / 6.2 GB**（fp16 + int16）。fp32 なら約 11.6 GiB / 12.3 GB。

`corpus_train.tsv` は source 順（jsut → rohan → ita → cv）に並んでおり、
**先頭を取ると JSUT basic5000 の短文に偏る**。ランダムサンプルを使うこと。

（frames は A-3 実測の `frames/n_ids = 2.56` で換算。教師推論を全件回さずに済ませた）

**入力テキストのみなら 1.2 MB。**

→ **ラベルは vast.ai 側で生成する。** 6〜12 GB を転送するより、
テキスト 1.2 MB + ckpt 927 MB（HF から直接取得）を渡すほうが桁違いに安い。
**インスタンスのディスクは 40 GB 以上**（要件定義 §8.4）を確保すること。

---

## M-16. ESP32-S3 のメモリ収支（⚠️ 実機測定ではない・差分見積もり）

再現: `uv run python scripts/esp32_memory_budget.py`

### 方法

**bottom-up の積み上げをやめ、論文の実機実測 289 KB を起点にした。**

最初にテンソル形状から積み上げたところ **586 KB** となり、論文の 289 KB を
297 KB 超過した。支配項は自分で置いた「重みステージング 316 KB」という仮定で、
論文が実機で 289 KB を達成している事実と矛盾する。つまり仮定のほうが誤り（C-009 と同型）。

本プロジェクトのグラフは論文と**完全に同一**（567,008 params / 22.05 kHz / hop 256 /
1024 点 iSTFT）。違うのは発話長と端末側 G2P だけなので、実測値をスケールするのが正しい。

### 論文の 289 KB の内訳

| 内容 | サイズ | 性質 |
|---|---:|---|
| PCM 出力 int16 (100,096 sample) | 195.5 KB | 発話長に比例 |
| それ以外の作業領域 | **93.5 KB** | ほぼ一定 |
| 合計（論文の実機実測） | 289.0 KB | |

**arena の 68% は PCM 出力バッファ。グラフ本体の作業領域は 94 KB しかない。**

### 発話長ごとの arena

| 発話 | PCM | 作業領域 | arena |
|---|---:|---:|---:|
| embedded 想定 (1.5 s) | 64.6 KB | 93.5 KB | 158.1 KB |
| train 平均 (2.93 s) | 126.2 KB | 93.5 KB | 219.7 KB |
| 論文 golden (4.54 s) | 195.5 KB | 93.5 KB | 289.0 KB |
| 最長級 (8 s) | 344.5 KB | 93.5 KB | 438.0 KB |

**I2S へ逐次出力すれば PCM は数 KB のリングで済み、発話長によらず約 94 KB に固定できる。**

### 本プロジェクトが論文に足すもの

| 内容 | サイズ |
|---|---:|
| mora テーブル（flash 常駐可） | 951 B（実測） |
| 中間表現の入出力バッファ（最長 413 B から） | 1.6 KB |
| **RAM 増分** | **2.5 KB** = 論文の作業領域の 2.7% |

### 判定

| 構成 | 必要 | 内部 SRAM 512 KB の残り |
|---|---:|---:|
| PCM 全保持 / 4.54 s | 291.5 KB | 220.5 KB |
| PCM 全保持 / 8 s | 440.6 KB | 71.4 KB |
| **I2S 逐次出力 / 発話長不問** | **96.0 KB** | **416.0 KB** |

フラッシュは重み 664 KB + G2P 951 B = 約 665 KB。8 MB ボードでも問題にならない。

**→ メモリを理由に中止する材料は無い。** I2S 逐次出力なら 416 KB の余裕があり、
IDF が内部 SRAM の 80% を食わない限り結論は変わらない。

⚠️ **これで go/no-go は出せない。** 残るのは (1) IDF/FreeRTOS/I2S の実 SRAM 消費、
(2) アプリの実バイナリサイズ、(3) 1 発話の実行時間。いずれも
**C99 コアと量子化済み生徒が出来てから**実機で測る。

---

## M-17. 生徒モデルの層構成（パラメータ数の再現）

実装: `src/saanotts_jp/_param_reference.py`
再現: `uv run python src/saanotts_jp/_param_reference.py`

```
E_rho        14952  target    14952  delta +0
D_alpha      36164  target    36164  delta +0
A_beta      199536  target   199536  delta +0
G_gamma     331308  target   331308  delta +0
deployed total 567008  target 567008  match=True
```

end-to-end forward も通る（音素ID → c (1,40,14) → PCM (1,3328) = 0.151 s @22.05 kHz）。

### ⚠️ パラメータ数の一致は構成の一意性を意味しない

判定の強さはモジュールごとに違う:

| モジュール | 判定 |
|---|---|
| `Eρ` | **実質的に一意**。`233h + 40 = 14952 → h = 64` が唯一の整数解 |
| `Aβ` | **非埋め込み部 192,000 は確実**（`199,536 − 157×48 = 192,000` ちょうど）。内訳は**非一意**（`leftover=0` の解が複数） |
| `Dα` | 前提の一部が反証済み（計画書 §6） |
| `Gγ` | **狭い探索箱の中で一意にすぎない** |

埋め込み次元 48 は論文の「30 エントリで +1,440 params」から `1440/30 = 48` と直接出る。
`567,008 − 1,440 = 565,568` が論文の earlier count と一致するので、この解釈は堅い。

⚠️ **総 MMAC が論文の 45 MMAC/s に対して約 39.7 と 12% 足りない**（計画書 §6）。
構成の解釈に誤りが残っている可能性がある。Phase C で実装するときに再検討すること。

---

## M-18. 学習 1 ステップのコストと vast.ai 費用（⚠️ ローカル実測 → GPU 外挿）

再現: `uv run python scripts/bench_train_step.py`

decoder + 判別器（論文 式5/6 相当: MR-STFT 512/1024/2048 + LSGAN + FM + iSTFT）、
segment 8192 sample = 0.372 s。

| device | batch | s/step | 音声秒/実時間秒 |
|---|---:|---:|---:|
| CPU | 8 | 0.0803 | 37.0 |
| **MPS** | **16** | **0.0117** | **507.5** |

**手元の Mac (MPS) ですら 507× リアルタイム。** 学習コーパス 17.1 時間 = 1 epoch 2 分弱。

### 費用（RTX 4090 を ×10 と仮定）

| epoch | 音声量 | 時間 | on-demand $0.29/h | spot $0.13/h |
|---:|---:|---:|---:|---:|
| 100 | 1,710 h | 0.3 h | $0.10 | $0.04 |
| 300 | 5,130 h | 1.0 h | $0.29 | $0.13 |
| 1000 | 17,100 h | 3.4 h | **$0.98** | **$0.44** |

**1000 epoch でも $1 未満。** 生徒が 567 K と極小なため。

### ⚠️ この見積もりの弱点

1. **GPU の外挿倍率が推測**（×10 / ×30 で幅を出した）。実機で 1 回測れば確定する
2. **必要 epoch 数が不明。** 論文は step 数を書いていない
3. **判別器の構造が推測**（385 K の MSD 相当を置いた）。論文は「一次差分に対する判別器」としか書いていない
4. Duration / Acoustic 単体の学習コストは含めていない（decoder より小さい）
5. **λ 群の探索試行回数を含んでいない**（論文に値が無いので複数回必要）

### 実務的な結論

支配的なのは計算費用ではなく **ディスク**（ラベルパック 4.42 GB）と**試行回数**。
月 $10〜50 の桁。GPU は最安の RTX 4090 spot で十分で、A100 / H100 は不要。
むしろ**ディスク 40 GB 以上**と spot 中断からの再開性のほうが設計上の論点。

---

## M-19. A-1 の不一致は経路 (b) の誤りではない（自己実測）

Phase A の A-1 で、2 経路の音素ID一致率が held-out で 86.8〜93.4% と出た。
**この「不一致」を経路 (b)（中間表現経由）の欠陥と読んではいけない。**

不一致の実例を引くと、上位は**かなを 1 文字も含まない地名**だった:

```
埼玉県秩父市
  経路(a) MultilingualPhonemizer : i ɕ f u ʂ                       ← 中国語音素
  経路(b) JapanesePhonemizer     : s a i t a m a k e N_n ch I ch i b u sh i  ← 正しい

長崎県五島市
  経路(a) : i ɕ u t ʂ                                              ← 中国語音素
  経路(b) : n a g a s a k i k e N_ng g o sh i m a sh i              ← 正しい
```

**これは B-1（かな無し行 5.36% が中国語音素になる問題）そのもの。**
経路を (b) に統一することで B-1 が実際に解消されることの直接的な証拠になる。

⚠️ したがって A-1 の一致率は「(b) の正しさ」の指標ではない。
**(a) を正解とみなす前提が成り立たない。** 不一致は次の 3 つに分けて数えるべき:

1. **(a) が中国語誤ルーティング** → (b) が正しい。B-1 の解消
2. (b) の mora テーブル不足 → 拡張で解消（表現可能率 85.7% → 92.8% → 正規化込み 100%）
3. 真に判断が分かれるもの → 個別に精査

再現:
```bash
uv run python - <<'EOF'
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody
from piper_plus_g2p.japanese import JapanesePhonemizer
# 埼玉県秩父市 を両経路に通して比較
EOF
```

---

## M-20. Phase B: ラベル生成経路の確定（自己実測）

### PAD 規則を canonical に合わせたら 0% → 86%

中間表現から音素IDを組む際、当初は**全音素の後ろに PAD を入れていた**。
canonical (`PiperEncoder._post_process`, encoder.py:172-204) は
**「その音素自身が PAD なら挟まない」**という規則を持つ。

```
held-out 400 文  音素ID完全一致
  修正前   0/400 =  0.0%   ← PAD が倍に増えていた
  修正後 344/400 = 86.0%
```

**例外は出ない。** 発話は生成でき、音として再生できる。ID 列を直接比較して初めて分かる。

残る 14% の差分は `ɕ`（中国語）と高位 PUA（U+E035 / U+E046 / U+E047 / U+E049、
日本語の PUA は U+E000–E015）で、**すべて canonical 側の誤ルーティング**（M-19 と同じ）。

### B-c: prosody は zeros で足りる

中間表現は A1/A2/A3 を持たない。実 prosody を流用しようにも、
**ID 列の長さが一致するのは 24 文中 2 文（8%）**なので位置合わせができない。

held-out 無作為 24 文で教師音声を両条件で生成し UTMOS を比較:

| 条件 | n | UTMOS mean | median | sd |
|---|---:|---:|---:|---:|
| canonical + 実 prosody | 24 | 1.730 | 1.649 | 0.229 |
| **中間表現 + zeros** | 24 | **1.740** | 1.698 | 0.195 |

```
差 = +0.0096 (sd 0.127)   対応のある t 検定 t=+0.363  p=0.72  → 有意差なし
```

→ **ラベル生成では prosody にゼロを渡す。** 理由:

1. 教師音声の品質に有意差が無い（上記）
2. **デバイスは prosody を供給できない**ので、教師と生徒の条件が揃う
3. 実 prosody は ID 列の 92% で位置合わせできない
4. prosody は duration にしか効かず、ピッチはアクセント記号が担う（M-13）

⚠️ **ゼロは「prosody 無し」ではない。** `prosody_proj(0) = bias` が concat される
（M-3.3）。`None` とも実 prosody とも違う第 3 の条件で、**それで一貫させる**という決定。

### フレーム比の再較正

`frames / n_ids` はこの構成で **2.31**（canonical + 実 prosody では 2.56）。
prosody=zeros で発話が約 5% 短くなるため。**ゲート G13 の範囲を 2.4–2.7 から
2.15–2.55 に改める**（`labelpack.py`）。

---

## M-21. B-d: デバイス間のラベル一致（自己実測）

再現: `uv run python scripts/b4_device_parity.py`（vast.ai 上でも同じものを走らせる）

M-15 で「piper-plus venv と uv 環境で bit 完全一致」を確認したが、**あれは CPU 同士**だった。
ラベル生成は GPU で行う予定（D-012）なので、加速器での挙動を確認した。

固定 8 文（`PROBE_TEXTS`、**変えないこと**）で照合:

| 比較 | frames | bit 一致 | SNR | int16 化後に差が残るサンプル |
|---|---|---:|---:|---:|
| cpu vs cpu | 全一致 | **8/8** | 完全一致 | **0** |
| **cpu vs mps** | 全一致 | **0/8** | 97.0–106.5 dB | **2,295** |

### 読み方

- **フレーム数は一致する。** duration predictor の出力は加速器でも同じ整数に落ちる
- **波形と潜在は bit 一致しない。** 縮約順序やカーネルの違いによる
- **SNR 97 dB は、パックの int16 量子化（SNR 76.9 dB, M-20/A-3）より 20 dB 良い**。
  つまり量子化誤差のほうが大きい
- ただし **int16 化しても 2,295 サンプルは 1 LSB ずれる**（全体の約 0.4%）。
  「量子化で吸収される」わけではない

### 決定

**GPU でのラベル生成を許容する。** 0.4% のサンプルが 1 LSB ずれても、
教師ラベルとしての価値は変わらない（int16 の量子化雑音より小さい）。

ただし **デバイス間の再現性は無い**ので:

1. ラベルは**一度だけ**生成する
2. パックに **SHA-256** を付けて固定する
3. manifest に**生成デバイス**を記録する（`environment.device`）
4. 部分再生成するときは**同じデバイス**で行う

⚠️ **CUDA では未検証。** MPS で 97 dB だったからといって CUDA も同等とは限らない
（TF32 が効くと精度はさらに落ちる）。**vast.ai のインスタンス上でこのスクリプトを
走らせてから**本番のラベル生成に入ること。

---

## 未測定（測る必要があるもの）

| 項目 | なぜ必要か | 対応タスク |
|---|---|---|
| 枝刈り辞書の実バイナリサイズと実文カバー率 | **プロジェクトの成否を決める** | B-0 |
| 教師音声の品質ベースライン（正しい経路・十分な長さで） | 生徒の上限が決まる | B-5 |
| `dT` のヒストグラムと `clip_[1,80]` の飽和 | 日本語で上限 80 frames (0.93 s) が足りるか | B-7 |
| 日本語 length scale `s_v` | 論文は英語 1.08 / ベトナム語 1.16 | B-7 |
| A1/A2/A3 を落とした場合の `dT` の変化 | 生徒 duration net に韻律を足すべきか | P0-3 |
| アクセント型ミニマルペアが教師で区別できるか | 生徒の評価軸として成立するか | P0-4 |
| 567 K / 1.4 M の日本語 CER | 実用性の判断 | Phase 6 |
| **`use_vanilla=True` でラベル生成した場合の教師音声品質** | **学習/デプロイの G2P を揃えれば M-11 の乖離は消えるはず。教師が vanilla 音素列で良い音を出すかは未検証** | B-0 追試 |
| C 実装のみの読み・アクセントが「間違い」なのか「別解」なのか | M-11 は一致率であって正誤ではない。ネイティブ聴取か正解データが要る | B-0 追試 |
| 枝刈り辞書 × `use_vanilla` の組み合わせでの一致率 | 現在の測定は Python 後処理層ありが基準。二重に効くのか相殺するのか不明 | B-0 追試 |
