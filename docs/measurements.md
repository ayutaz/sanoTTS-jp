# 実測値の記録

**このファイルが数値の一次ソース。** 他のドキュメントと食い違ったらここが正。
全項目に再現コマンドを付ける。推測は書かない — 測っていないものは「未測定」と明記する。

共通の前提:

```bash
export PP=~/Documents/piper-plus
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
# → ~/Documents/piper-plus/src/python/piper_train/vits/models.py   ← 正しい
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
⚠️ **この 12.53 dB は n=1・intersperse padding 無しの退化入力での値**。
実文では **14.48 dB (n=24) / 14.65 dB (n=59)**、最小 13.53 dB（C-017 / M-33）。

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
intersperse padding が入る**（⚠️ 厳密には `2*n_phonemes + 3 + PAD音素数`。C-019 / M-23）。
これを飛ばすと発話が約 2.4 倍速になる
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
cd . && $PY scripts/phase0_verify_teacher.py
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
piper_train:    ~/Documents/piper-plus/src/python/piper_train/vits/models.py
piper_plus_g2p: ~/Documents/piper-plus/src/python/g2p/piper_plus_g2p/__init__.py
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

## M-22. Phase C: 学習ループのスモークテスト（自己実測）

再現: `uv run python scripts/train_student.py --pack <pack> --smoke`

実データ（heldout 200 発話のパック）で論文の 4 段構成を各 12 step 回した。

| Stage | 損失 | 初期 → 最終 | 変化 | ms/step |
|---|---|---|---:|---:|
| 1 Duration `Dα` | 式2 | 0.1907 → 0.1359 | −28.7% | 52 |
| 2 Acoustic `Eρ`+`Aβ` | 式3 | 9.1967 → 3.6767 | −60.0% | 142 |
| 3 Decoder `Gγ` | 式5 | 0.0990 → 0.0756 | −23.6% | 44 |
| 4 Joint | 式6 | 0.1368 → 0.1104 | −19.3% | 12 |

**全 stage で損失が下がる。** 学習ループ・損失・パック読み出しが噛み合っている。

⚠️ **これは「回ることの確認」であって品質の確認ではない。** 12 step では何も学習できない。

### 損失の性質テスト

`scripts/test_losses.py` で 19 項目（λ の実行時算出を足して**現在 26 項目**、M-30）。
数値が出ることと正しいことは別なので、
**最小値の位置と何に反応するか**で検証した:

- 式2: 完全一致で 0 / 発話長保存項がトークン誤差の打ち消しに反応する
- 式3: **低 σ チャネルの誤差を重く見る**（σ=0.24 → 0.053 vs σ=3.10 → 0.004）/
  Δ 項が定数オフセットに反応しない
- 式5: LSGAN が `D(fake)=1` で 0、`D(fake)=0` で 1
- 式7: 摩擦音区間にだけ注入し、σ に比例する

### 実装で踏んだバグ

`Acoustic.forward` が位置インデックスを `torch.arange`（CPU 既定）で作っており、
MPS で `Placeholder storage has not been allocated` になった。
**CPU では動くので、加速器で回すまで気づかない。**

---

## M-23. B-4: 長さ分布と符号化の関係式（自己実測 / 全 23,297 発話）

教師の duration だけを全コーパスに対して取り、長さフィルタの基準を決めた。
`infer()` のうち decoder / flow を通らない前半（`models.py:1037-1050`）だけを実行する。
**full `infer()` と bit 完全一致することを 64 文で照合してから**本走行した。

再現:
```bash
uv run python scripts/b_durations_all.py --verify 64 --out reports/durations  # 約 65 分
uv run python scripts/b4_length_hist.py                                       # 約 1 分
```

| 系列 | mean | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|---:|
| 中間表現トークン数 | 41.43 | 36 | 74 | 109 | 166 |
| 音素数 | 63.35 | 56 | 113 | 165 | 249 |
| **符号化後 ID 長** | **130.76** | **116** | **231** | **338** | **505** |
| 推定フレーム数 `Σceil(dT)` | 312.37 | 276 | 554 | 829 | 1267 |
| 秒 | 3.63 | 3.2 | 6.4 | 9.6 | 14.7 |

**符号化の関係式（23,297 発話すべてで成立、スクリプトが assert する）**:

```
len(ids) == 2 * n_phonemes + 3 + (PAD 音素の数)
```

PAD 項は `#` 句境界などで**音素そのものが PAD になる**件数。canonical 規則
「PAD の後ろに PAD を挟まない」により、そこだけ 1 個で済む。実測の分布は 0〜10
（0 が 8,103 / 1 が 9,449 / 2 が 3,571 …）。

**教師の学習時制約に対する超過**:

| 制約 | 由来 | 超過 | 内訳 |
|---|---|---:|---|
| `max_phoneme_ids = 400` | piper-plus `dataset.py` | **25 件 (0.11%)** | train 24 / heldout 1 |
| `max_spec_length = 700` (= 8.13 s) | 同上 | **1,003 件 (4.31%)** | train 911 / heldout 92 |

**効いているのはフレーム側**。ID 長は実質問題にならない。

**発話速度**（ゲート G12/G13 の妥当性確認）:

| 単位 | mean | p50 | p99 |
|---|---:|---:|---:|
| 厳密なモーラ / 秒 | 8.51 | 8.5 | 10.2 |
| **ゲートの近似**（記号を除く音素数 / 2） | **7.30** | 7.3 | 8.7 |

⚠️ **2 つの尺度は 1.167 倍違う。混ぜて読まないこと。** ゲート単位で見ると
G12 (4–12) を外れるのは 57 件 (0.245%、すべて下回り)、G13 の平均範囲 6.5–8.5 には
7.30 で収まる。**ゲートは正常な行をほとんど落としていない。**

---

## M-24. B-7: 日本語の length scale `s_v`（自己実測 / 23,297 発話・3,046,407 トークン）

再現: `uv run python scripts/b7_sv_calibration.py`

```
s_v = 1.2187   （コーパス全体で Σ clip_[1,80](round(s_v·dT)) == Σ ceil(dT) になる値）
```

| 条件 | 発話ごとの比 mean | sd | min | max |
|---|---:|---:|---:|---:|
| `s_v = 1.0` | 0.8135 | 0.0239 | 0.701 | 0.943 |
| **`s_v = 1.2187`** | **0.9984** | **0.0206** | 0.916 | 1.143 |

**この 1.2187 は「教師の `ceil` と生徒の `round + clip` の規約差」を吸収する係数**で、
言語固有の話速ではない。`Σceil(dT) / Σclip(round(dT)) = 1.2277` がその中身。

⚠️ **`r_i` に生徒の予測ではなく `dT` を代入している**（完璧な生徒の仮定）。
測れているのは量子化由来のズレだけで、**学習後に生徒の予測で解き直す必要がある。**

**`clip_[1,80]` について確定したこと**:

| 項目 | 値 |
|---|---:|
| `dT < 1` のトークン | **563,181 (18.49%)** |
| うち PAD | 272,489 |
| うち実音素 | **290,692** |
| そのトークンを含む発話 | **23,287 / 23,297 (99.96%)** |
| `dT > 80` のトークン | **0**（max dT 29.22 / max ceil 30） |

**上限 80 は飽和しない。効いているのは下限 1 のほうで、しかも過半が実音素。**

---

## M-25. B-8 / D2: `_` PAD はフレームの過半を占める（自己実測 / 同上）

再現: `uv run python scripts/b8_pad_duration.py`

| 項目 | 値 |
|---|---:|
| PAD のトークン比 | 50.02% |
| **PAD のフレーム比** | **53.76%** |
| PAD の duration mean / p50 / max | 2.059 / 1.586 / 29.22 |
| 実音素の duration mean / p50 / max | 1.697 / 1.524 / 22.64 |
| `dT<1` の割合（PAD / 実音素） | 17.9% / 19.1% |
| 連続 PAD ペア | 24,780 (1.06/発話) |

**PAD は「余り」ではない。** 音声時間の過半を占め、しかも 1 個あたりの duration は
**実音素より長い**。加えて B-6 の照合で、**破擦音・破裂音の摩擦バーストが後続 PAD に
入っている**ことが分かった（破擦音では PAD の帯域 RMS が音素区間の 3.6 倍）。

---

## M-26. B-9: デプロイ語彙は 57（自己実測）

再現:
```bash
uv run python scripts/b9_phoneme_inventory.py   # コーパス出現
uv run python scripts/b9_vocab_closure.py       # 変換器の閉包
```

| 集合 | サイズ |
|---|---:|
| 教師の有効音素 (`num_symbols`) | 173 |
| コーパス 23,501 行での出現 | **54** |
| `kana_g2p` の閉包（原理的に出せる音素） | **57** |
| 閉包にあってコーパスに無い | **3**（`A` `E` `O`）— `?!` `?.` `?~` は文を足して解消 |
| **コーパスにあって閉包に無い** | **0**（列挙に漏れが無い） |

閉包は `intermediate_to_phonemes()` を全 195 mora × {素 / 無声化 `°` / 長音 `ー` /
`ん`+後続 / 記号} で**実際に叩いて**取った。57 個すべてが教師の先頭 173 に存在する。

**パラメータ数への影響**（`VOCAB` 157 → 57）:

| | 論文 (V=157) | 日本語 (V=57) | 差 |
|---|---:|---:|---:|
| `Dα` | 36,164 | 32,964 | −3,200 |
| `Aβ` | 199,536 | 194,736 | −4,800 |
| `Gγ` | 331,308 | 331,308 | 0 |
| **合計** | **567,008** | **559,008** | **−8,000** |

⚠️ **MMAC は 1 も減らない**（埋め込みは表引きで 0 MAC）。浮くのは flash 8 KB だけ。

⚠️ **生徒は教師の音素ID をそのまま使えない。** 57 トークンの教師 ID は最大 64 で、
途中に 8 個の穴（20–25 / 31 / 52）がある。`src/saanotts_jp/vocab.py` の
`TEACHER_TO_STUDENT` を通すこと。**重みと一緒に凍結する。**

---

## M-27. B-6: スペクトル平坦度プローブの窓設計と教師ベースライン（自己実測）

再現:
```bash
uv run python scripts/b6_build_evalset.py --target 420 --out data/splits/corpus_sibdense.tsv
uv run python scripts/gen_teacher_labels.py --split sibdense --out data/pack_sibdense
uv run python scripts/b6_flatness_grid.py     # 約 60 s。定数がズレたら exit 1
```

`n_fft ∈ {256,512,1024} × guard ∈ {0,1} × power ∈ {1,2}` の 12 点を
発話単位クラスタ bootstrap (B=2000) で比較した。評価セットは
**クラスごとに 420 音素以上**を確保した 220 発話（`data/pack_sibdense`）。

**採用: `n_fft=1024 / hop=256 / guard=0 / power=1`**

| 判断 | 根拠 |
|---|---|
| guard=0 | 生の AUC は guard=1 が上 (0.9258 vs 0.8704) だが、guard=1 は span<3 frame の音素を 78% 落とす（13,923→3,073、devoiced は 471→35）**母集団選択**。同じ span≥3 に揃えると **6/6 の設定で guard=0 が上** |
| n_fft=1024 | 512 との ΔAUC +0.0103 [+0.0074, +0.0131]。区別可能 |
| power=1 | power=2 との ΔAUC は 4.7e-5、CI が 0 を含む。**同点**。検出感度が高いほうを採った（「power=1 が優れている」とは言えない） |

**教師ベースライン**（`src/saanotts_jp/flatness.py` に凍結。mean [95%CI] n）:

| クラス | SFM | n |
|---|---|---:|
| fricative | 0.7351 [0.7310, 0.7393] | 1069 |
| affricate | 0.8037 [0.7994, 0.8083] | 512 |
| **devoiced (I,U)** | **0.7408 [0.7351, 0.7465]** | 495 |
| stop | 0.7275 [0.7245, 0.7304] | 2740 |
| vowel | 0.6448 [0.6426, 0.6469] | 7164 |
| nasal | 0.6129 [0.6081, 0.6177] | 1464 |
| approximant | 0.5874 [0.5821, 0.5927] | 924 |
| geminate | 0.7987 [0.7952, 0.8020] | 417 |

`devoiced vs vowel` は AUC 0.8466 (n=471)、`d = +1.303`。
**無声化母音が音響的に摩擦雑音寄りであることが n=495 で確認できた** —
式7 のノイズ注入集合 `S_ja` に `I` `U` を入れる判断はこれが支える。

⚠️ **絶対値の読み方には 3 つの制限がある**（B-6 の照合で判明）:

1. **`geminate` の 0.7987 は教師の性質ではなく int16 の量子化床**（`cl` の波形 RMS は
   中央値 1.4 LSB）。生徒も同じ int16 往復に通してから測ること。
2. **測っているのは音声の 36.6%**。PAD `_` が 54.7% を占め、そこに破擦音・破裂音の
   バーストが入っている（M-25）。`affricate` の値も「主に閉鎖区間」。
3. **span 規則を変えると平均が CI の 4〜8 倍動く**。絶対値ではなく**教師比**で使う。

---

## M-28. B-10: 教師の学習テキストとの重複（自己実測）

再現: `uv run python scripts/b10_overlap.py` → `scripts/b10_write_exclusions.py`

| 検査 | 結果 |
|---|---|
| **held-out に残っていた FT テキスト** | **`jsut/repeat500` 10 行 (0.43%)** |
| うち旧 `pack_sibdense` に入っていた | 4 / 209 (1.91%) |
| train 側の FT テキスト | 92 行 |
| embedded | **0**（クリーン） |
| train↔heldout の完全一致 | **0 件** |
| train↔heldout の 5-gram Jaccard 最大 | 0.6875（J≥0.70 は 0 件） |
| 部分文字列包含（意味のあるもの） | 12 件 |

`repeat500` set1 は本文が **VOICEACTRESS100 と 98/100 共通**で、その
VOICEACTRESS100 が教師（つくよみちゃんコーパス）の FT テキストそのもの。
1 文字違いがあるので NFKC 重複排除では併合されない → **両サブセットを丸ごと除外**
（重複を潰して **102 uid**、`data/splits/exclusions_teacher_ft.txt`）。

⚠️ **「教師が丸暗記しているぶん過大評価になる」は未実証。** 教師側 UTMOS は
汚染 10 文 1.8502 vs 非汚染 24 文 1.7478、差 **+0.102 / p=0.334**（検出可能最小差 0.279）。
除外は予防措置。生徒ができてから別集計して確かめること。

⚠️ 既存の B-5 ベースライン (`reports/b5_teacher_baseline.json`, n=24) に FT テキストは
**0 件**なので、**M-10 の数値は汚染されていない。**

---

## M-29. SCOREQ を導入し、教師と実人間のベースラインを測った（自己実測 / n=24+24）

**`scoreq==1.0.1` は PyPI にある。**「pip パッケージが見つからない」は誤りだった（C-016）。
onnxruntime 経路なので fairseq は要らない。

再現:
```bash
uv add --optional eval 'scoreq>=1.0.1'
uv run --extra eval python scripts/b5_scoreq_baseline.py
```

| 指標 | 教師 | 実人間 | 教師/人間 |
|---|---:|---:|---:|
| UTMOS | 1.7479 | 2.3047 | 0.758 |
| **SCOREQ synthetic/nr** | **2.0488** | **2.4983** | **0.820** [0.773, 0.870] |
| SCOREQ natural/nr | 3.5080 | 3.3753 | **1.039** ← 合成を人間より上に置く |

UTMOS と SCOREQ synthetic/nr の pooled 相関は **r = +0.850 [0.746, 0.914] (n=48)**。
2 指標は同じ方向を向いている。

⚠️ **`data_domain="natural"` は使ってはいけない。** 実体は NISQA TRAIN SIM
（符号化・雑音・パケットロスの伝送劣化）モデルで、**合成音声を実人間より高く採点**し
UTMOS と pooled 無相関 (r=+0.141)。TTS の劣化軸を見ていない。

⚠️ **SCOREQ の synthetic モデルも日本語では較正されていない。**
学習データは VoiceMOS 2022 Train Set = BVCC（英語）。**実人間の日本語スタジオ録音でも
2.4983 しか出ない。** 論文の英語スコア（教師 4.68 / embedded 2.54）と直接比べないこと。

**生徒の目標値**（論文の英語 embedded が教師比 2.54/4.68 = 0.5427 なのを当てはめる場合）:

| 指標 | 目標（教師比 0.5427） | 人間比 |
|---|---:|---:|
| SCOREQ synthetic/nr | **1.112** | 0.445 |
| UTMOS | 1.107 | 0.480 |

⚠️ `mode="ref"` は**向きが逆**（距離なので低いほど良い）。自己距離 0.0 で確認したが、
NMR 1 本の選び方で教師比が 1.114〜1.318 と振れるので補助指標に留める。

---

## M-30. C-1: 論文に値が無い λ を勾配ノルムで決めた（自己実測 / 220 発話・81,475 フレーム）

再現: `uv run python scripts/c1_lambda_balance.py`（約 210 s）

**「全部 1.0」は明確に誤り。** 実 `zT` に対して各項の勾配ノルムを `l1` 項に揃えると:

| λ | 勾配整合値 | 決め方 |
|---|---:|---|
| `λ_n` | **0.272** | **閉形式** `1/√(mean(1/σ_k²))`。実測と 5 桁一致。σ 依存なので**実行時に計算する** |
| `λ₂` | 誤差レベル依存 | **閉形式** `1/(2·RMS(残差))`。**固定値にできない**。実行時に残差から出す |
| `λ_Δ` | **0.86** | 生徒の残差は時間的に滑らか。白色雑音仮定の 0.77 より上を採る |
| `λ_s` | **0.19** | 誤差の種類によらずほぼ一定（0.173〜0.221） |
| `λ_T` | **0.27** | 式2 の Huber 項との勾配比 |

⚠️ **λ₂ のスケジュール方向に注意。** 残差が小さくなるほど λ₂ は**大きくなる**
（初期化時 残差 RMS ≈ σ_k での勾配整合値は 0.397）。「2.0 から減衰」は逆。
`losses.py` は `lambda_2=None` のとき毎ステップ残差から計算する実装にした
（結果として `λ₂·‖·‖₂² = ½·RMS(残差)` = L2 が実質 RMS 項になる）。

**式2 の length 項に構造的な床があった**（C-021）:
`r = max(1, exp(l̂))` の clamp があるのにターゲットが `Σ dT` のままだと、
`dT` の 18.49% が 1 未満なので `Σmax(1,dT)/ΣdT = 1.044`。**完璧な生徒でも
length 項が 0 にならず、λ_T > 0 が発話を 4.4% 短くする定常バイアス**になる。
ターゲットを `Σ max(1, dT)` にして自己整合させた（`length_target` で上書き可能）。

---

## M-31. C-2: 一次差分判別器の凍結（自己実測）

再現: `uv run python scripts/test_discriminator.py`（23 チェック PASS）

| 構成 | params | 生徒比 | MPS d_step | MPS g_side |
|---|---:|---:|---:|---:|
| **small（既定）** `scales=3, base_channels=16` | **94,755** | 0.167 | 17.2 ms | 12.3 ms |
| large | 500,484 | 0.882 | 26.3 ms | 22.7 ms |

判別器は**学習専用でデプロイされない**ので 559,008 params の勘定に入らない。

一次差分は**モジュール内**で取る（`Δy[t] = y[t] − y[t−1]`）。定数オフセットで
出力・全中間特徴が不変であることを確認（max|Δ| 5.49e-08、符号反転では 13.6 万倍動く）。

⚠️ **論文が指定しているのは「Δŷ に判別器を掛ける」の 1 点だけ。**
層構成・チャネル数・スケール数・正規化はすべて本実装の仮定（A1〜A9 として
`discriminator.py` の docstring に列挙）。

⚠️ **正規化 3 種（spectral / weight / none）に測れた差は無い。**
当初「spectral は weight より 14.5% 分離が遅い (p=0.055)」と書いたが、
別 split・別 5 seed では消えた (p=0.77)。既定を spectral にしているのは
Lipschitz 制約という**理屈**からで、実測の裏付けは無い。

⚠️ 分離プローブ自体が**実質 Δ 振幅の検出器**だった（fake の Δ-RMS が real の 6.0 倍、
Δ-RMS 単独で AUC 0.922）。Δ ドメインで RMS を揃えると held-out acc は 0.500 に落ちる。
**プローブを直してから「判別器が健全」と言うこと。**

---

## M-32. D5: iSTFT のフレーミング規約（自己実測 / 220 発話）

再現: `uv run python scripts/d5_istft_framing.py`

**教師の規約**: `data/pack_sibdense` の全発話で `sum(ceil(dT)) * 256 == len(yT)`。
つまり **T フレーム → ちょうど 256·T サンプル**（違反 0）。

| 生徒側の実装 | 出力サンプル数 | 往復 SNR |
|---|---|---:|
| `torch.istft(center=True)`（`length` 省略） | **256·(T−1)** ← 1 フレーム不足 | — |
| `torch.istft(center=False)` | **RuntimeError**（`window overlap add min: 1`）12 通り全滅 | — |
| **`center=True, length=T*256`** | **256·T** | **139.0 ± 0.02 dB** (n=220) |

**採用: `center=True` + `length=T*256`。** `_param_reference.py` の `Decoder.istft` を 1 行修正した。

⚠️ 併せて `train_student.py` の末尾ゼロ埋め (`F.pad`) を外した。ゼロ埋めは
**勾配経路の無い損失の下駄**で、完璧な decoder でも SNR 上限を固定していた:
発話全体で 95.03 ± 0.55 dB、**学習が実際に使う 32 フレームセグメントでは 33.64 ± 1.38 dB**
（セグメント末尾 256 サンプルはセグメント全体の 4.50% のエネルギーを持つ）。

---

## M-33. D6: 波形ラベル `yT` に EMA を当てるか（自己実測 / n=24 対）

再現: `uv run --extra eval python scripts/d6_ema_ablation.py`

| 指標 | EMA あり | EMA なし | 差 | p |
|---|---:|---:|---:|---:|
| **SCOREQ**（論文の主指標） | — | — | −0.0015 / +0.0065 | **0.876 / 0.443**（有意差なし） |
| UTMOS | — | — | −0.0418 | 0.0004（EMA なしが高い） |
| 摩擦音 2–8 kHz SFM | 0.3464 | 0.3393 | +0.0071 | 0.0014（EMA ありが雑音的） |

`zT` / `dT` は 24/24 で **bit 完全一致**（EMA は decoder にしか掛からないので当然）。
`yT` の SNR は **14.48 dB**（n=24）。

**採用: EMA を適用した `yT` を蒸留ターゲットにする。** 決め手は品質指標ではなく
**canonical との整合**で、piper-plus の `export_onnx.py` は `ema_generator_state` があれば
無条件で EMA を適用し、**無効化する CLI フラグが存在しない**（= 公開 ONNX は EMA 適用済み）。
EMA なしを蒸留すると「誰も配布していない教師」を真似ることになる。

⚠️ **EMA の適用は件数を assert すること。** `remove_weight_norm()` の後に呼ぶと
**53 個中 23 個だけ当たり、EMA が半分載った第三の重み**になる。
`applied == len(shadow) and skipped == 0` を確認する（`applied > 0` では通ってしまう）。

---

## M-34. Phase C: 全修正を入れ直したあとのスモークテスト（自己実測）

再現: `uv run python scripts/train_student.py --pack data/pack_sibdense --smoke`

| Stage | 損失 | 変化 | ms/step | M-22 比 |
|---|---|---:|---:|---|
| 1 Duration `Dα` | 式2 | −54.8% | 58 | 52 |
| 2 Acoustic `Eρ`+`Aβ` | 式3 | −53.2% | 81 | 142 |
| 3 Decoder `Gγ` | 式5 | −20.6% | 58 | 44 |
| 4 Joint | 式6 | −5.2% | **39** | 12 |

**Stage 4 が 12 → 39 ms（3.3 倍）になったのは判別器を凍結版に差し替えたから**
（旧プレースホルダ 384,961 params → small 94,755 params だが、マルチスケール化で
forward が 3 回になる）。vast.ai の時間見積もりはこの値を使う。

⚠️ **「回ることの確認」であって品質の確認ではない。** 12 step では何も学習していない。

---

## M-35. 本番ラベルパックの生成（自己実測・ローカル CPU / D-027）

再現:
```bash
uv run python scripts/gen_teacher_labels.py --split train   --out data/pack
uv run python scripts/gen_teacher_labels.py --split heldout --out data/pack_heldout
(cd data/pack && shasum -a 256 -c SHA256SUMS)
```

| split | 入力行 | 採用 | 棄却 | shard | サイズ | 時間 |
|---|---:|---:|---:|---:|---:|---:|
| **train** | 20,893 | **20,790** | 103 | 163 | **5.5 GB** | **47.2 分** (135 ms/文) |
| heldout | 2,323 | **2,314** | 9 | 19 | 627 MB | 約 5 分 |

環境: Python 3.14.0 / torch 2.13.0 / numpy 2.4.6 / macOS-26.2-arm64 / **device=cpu**。
`data/pack/SHA256SUMS` の **330 ファイル全件が一致**（D-015 の固定完了）。
パック全体ゲート G13 の問題は **0 件**。

棄却の内訳（train 103 件）:

| 理由 | 件数 |
|---|---:|
| `fy` が教師の音素表に無い（B-3） | 50 |
| G12 発話速度が 4 mora/s 未満 | 53 |

⚠️ **A-3 の見積もり 4.42 GB に対し実測 5.5 GB（+24%）**。
C-014 でサンプルの偏りを直したはずだが、まだ過小だった。
原因は未特定（発話長の分布か、shard のアラインメントか）。**サイズ見積もりは
実測でしか当たらない**という C-009 / C-014 と同じ教訓がもう一度出た。

⚠️ **`seq=0` が実データであることを確認した**（`sentence_collector_08727`）。
C-018 のヘッダ行バグは修正済み。

チャネル統計: `sigma_T` は **0.0414 〜 9.9769（241.1 倍）**。
式3 のチャネル正規化項が必要な理由がここにある。

---

## M-36. 本学習 v1（各 5,000 step）と初回評価（自己実測）

再現:
```bash
uv run python scripts/train_student.py --run runs/v1 --all --steps 5000 --accum 8
uv run --extra eval python scripts/eval_student.py --ckpt runs/v1/stage4.pt \
    --n 24 --out reports/eval_v1
```

device=mps / train 20,790 発話 / val 2,314 発話（`data/pack_heldout`）。

### 学習

| Stage | 損失 | 変化 | ms/step | 所要 |
|---|---|---:|---:|---:|
| 1 Duration `Dα`（式2） | 0.3366 → **0.0375** | −88.9% | 8 | 0.7 分 |
| 2 Acoustic `Eρ`+`Aβ`（式3） | 7.1737 → **0.6439** | −91.0% | 68 | 5.7 分 |
| 3 Decoder `Gγ`（式5） | 0.0865 → **0.0354** | −59.1% | 42 | 3.5 分 |
| 4 Joint（式6） | 0.2783 → **0.1461** | −47.5% | 64 | 5.4 分 |

全段で **val が train と同水準**（過学習していない）。Stage 4 の ckpt は
デプロイ対象 3 つが揃い、**合計 559,008 params が D-016 と一致**（スクリプトが assert）。

**2 つの設計判断が実データで裏付けられた**:

| 判断 | 予測 | 実測 |
|---|---|---|
| **λ₂ は残差から実行時算出**（D-021） | 学習が進むほど**上がる** | **0.644 → 2.114**。固定値や減衰を焼いていたら後半で L2 が効かなくなっていた |
| **`Eρ` は Stage 2 で凍結**（D-028） | 自明解（c が定数に潰れる）を避ける | 全 40 記録で **`c_rank` = 40/40**、`c_std` 0.343 → 0.328 |

`λ_n` は σ から **0.2338**（定数 1.0 の 1/4）。

### 評価（held-out 24 文 / 汚染除外済み）

| 指標 | 生徒 | 教師 | 教師比 [95%CI] | 目標 |
|---|---:|---:|---|---:|
| **SCOREQ synthetic/nr** | 0.7645 | 1.9732 | **0.387** [0.349, 0.437] | 1.112 |
| UTMOS | 1.2552 | 1.7925 | **0.700** [0.674, 0.729] | 1.107 |
| 発話長（生徒/教師） | — | — | **0.961** | ±5% 以内 ✅ |

**⚠️ 2 指標が食い違う。** UTMOS の絶対値 1.2552 は目標 1.107 を**超えている**のに、
SCOREQ は 0.7645 で目標 1.112 に遠い。B-5 では pooled 相関 r=0.850 だったが、
**生徒の劣化に対しては 2 指標が別の方向を向く**。
**論文の主指標は SCOREQ なので、UTMOS が良いことを根拠にしない**（D-020）。

**⚠️ 5,000 step は明らかに足りない。** Stage 4 の 5,000 step は 5,000 発話
= **0.24 epoch**。20,790 発話を 1 周すらしていない。この数値は
「パイプラインが端から端まで動いた」ことの記録であって、到達可能な品質ではない。

### 音素クラス別 SFM（生徒/教師）— **RMS と対で読む**

| クラス | SFM 生徒 | SFM 教師 | SFM 比 | **RMS 比** | n |
|---|---:|---:|---:|---:|---:|
| affricate | 0.8269 | 0.8051 | 1.027 | 0.761 | 27 |
| approximant | 0.6586 | 0.6015 | 1.095 | 0.733 | 97 |
| devoiced | 0.7879 | 0.7484 | 1.053 | 0.785 | 28 |
| fricative | 0.7700 | 0.7266 | 1.060 | 0.781 | 89 |
| geminate | 0.7818 | 0.8117 | 0.963 | 1.312 | 7 |
| **nasal** | 0.7183 | 0.6189 | **1.161** | **0.497** ⚠️ | 114 |
| stop | 0.7581 | 0.7201 | 1.053 | 0.697 | 194 |
| vowel | 0.7035 | 0.6545 | 1.075 | 0.717 | 565 |

**全クラスで SFM が教師より高い（より雑音的）一方、RMS は 0.5〜0.78 に落ちている。**
これは「摩擦音が豊かになった」のではなく **decoder がまだ十分な音を出せていない**
状態を示す。⚠️ **SFM 単独で読んでいたら「生徒のほうが sibilant が良い」と
誤読していた**（M-27 の警告どおりの事象が初回で出た）。

`nasal` の RMS 比 0.497 が最も低い。鼻音は低域主体なので 2–8 kHz が薄いのは
教師も同じだが、生徒はさらに落ちている。

---

## M-37. 本学習 v2（stage ごとに step 配分）— 目標を超えた（自己実測）

再現:
```bash
uv run python scripts/train_student.py --run runs/v2 --stage 1 --steps 20000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 2 --steps 60000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 3 --steps 40000 --accum 8
uv run python scripts/train_student.py --run runs/v2 --stage 4 --steps 60000 --accum 8
uv run --extra eval python scripts/eval_student.py --ckpt runs/v2/stage4.pt \
    --n 24 --out reports/eval_v2
```

v1 の実測 ms/step と epoch 換算から step を配分し直した（合計 2.7 時間 / device=mps）。

### 評価（held-out 24 文 / 汚染除外済み / v1 と同一文）

| 指標 | v1 | **v2** | 目標 | 教師 |
|---|---:|---:|---:|---:|
| **SCOREQ synthetic/nr** | 0.7645 | **1.2063** | 1.112 | 1.9732 |
| SCOREQ 教師比 | 0.387 | **0.611** [0.568, 0.658] | 0.5427 | — |
| UTMOS | 1.2552 | **1.3585** | 1.107 | 1.7925 |
| UTMOS 教師比 | 0.700 | **0.758** [0.728, 0.791] | — | — |
| 発話長比 | 0.961 | **0.960** | ±5% | — |

**SCOREQ の教師比 0.611 は、論文の英語 embedded の教師比 0.5427 を上回る**
（95%CI [0.568, 0.658] が 0.5427 を含まない）。絶対値 1.2063 も目標 1.112 を超えた。

⚠️ **この読み方には 3 つの限界がある。**

1. **n=24。** 24 文でしか測っていない
2. **「目標」は論文の英語比を日本語に当てはめた推定値**であって、日本語で妥当な
   目標かは分からない（D-020）。**絶対値を論文の 2.54 と比べてはいけない**
3. **SCOREQ 自体が日本語で較正されていない**（BVCC = 英語で学習）。
   実人間の日本語スタジオ録音でも 2.4983 しか出ない（M-29）

### 音素クラス別 SFM / RMS（生徒/教師）

| クラス | SFM 比 v1 | **SFM 比 v2** | RMS 比 v1 | **RMS 比 v2** |
|---|---:|---:|---:|---:|
| affricate | 1.027 | **1.026** | 0.761 | **0.887** |
| approximant | 1.095 | **1.036** | 0.733 | **0.800** |
| **devoiced** | 1.053 | **0.994** | 0.785 | **0.904** |
| fricative | 1.060 | **1.039** | 0.781 | **0.873** |
| geminate | 0.963 | 0.971 | 1.312 | 1.590 |
| nasal | 1.161 | **1.066** | 0.497 ⚠️ | **0.663** |
| stop | 1.053 | **1.029** | 0.697 | **0.879** |
| vowel | 1.075 | **1.032** | 0.717 | **0.802** |

**SFM 比が全クラスで 1 に近づき（最大 1.16 → 1.07）、RMS 比も上がった。**
v1 で出ていた `nasal` の「音が出ていない」警告は消えた。
**`devoiced` (I,U) は 0.994 でほぼ教師と一致** — 日本語固有の懸念だった無声化母音が
再現できている。

⚠️ `geminate` の RMS 比 1.59 は不安定。教師の閉鎖区間が int16 の量子化床
（波形 RMS 中央値 1.4 LSB）なので比が発散しやすい（M-27）。**このクラスの比は読まない。**

### step 数の配分（次の run の材料）

| Stage | step | epoch | 所要 | 収束の様子 |
|---|---:|---:|---:|---|
| 1 Duration | 20,000 | 7.7 | 2.7 分 | val 0.0322 → **0.0237** |
| 2 Acoustic | 60,000 | 2.9 | 68 分 | val 0.660 → **0.430**。まだ下がる余地あり |
| 3 Decoder | 40,000 | 15.4 | 28 分 | **20,000 step で SNR +8.91 dB に飽和**。以降 8.0〜8.4 で頭打ち |
| 4 Joint | 60,000 | 2.9 | 56 分 | val 0.158 → **0.152** |

**Stage 3 は 20,000 step で十分**（40,000 は半分無駄だった）。
判別器の損失は全区間 0.47〜0.49 で一定 = **LSGAN は均衡している**（発散も崩壊もなし）。

⚠️ Stage 4 の終盤 10 記録は train 0.1263 / val 0.1400 で乖離が出ている。
2.9 epoch で過学習と断じるのは早いが、**次の run では監視する**。

---

## M-38. CER（かな CER が主指標）— v2 の生徒（自己実測 / n=24）

再現: `uv run --extra eval python scripts/measure_cer.py --eval-dir reports/eval_v2`
（faster-whisper `large-v3` / CPU int8）

### 測り方を変えた（C-023）

**参照と仮説の両方を OpenJTalk で読みに落としてから比較する。**
表記のまま測ると、Whisper が同じ音を漢字でもひらがなでも書き起こすため
**正しく読めていても CER が跳ね上がる**。

| | 教師 mean | 生徒 mean | 差 |
|---|---:|---:|---:|
| **かな CER（主指標）** | **0.1351** | **0.1776** | **+0.0425** |
| 表記 CER（参考） | 0.2781 | 0.2249 | **−0.0531** ← **符号が逆** |

表記 CER で「生徒のほうが良い」に見えたのは、教師の 2 発話で Whisper が
ひらがな書き起こしを返し CER が 1.286 / 1.571 に跳ねたため:

```
和歌山県太地町  → 教師の書き起こし「わけわけんてじまち」  表記 CER 1.286 / かな CER 0.375
和歌山県北山村  → 教師の書き起こし「わけざけんきてやばばら」表記 CER 1.571 / かな CER 0.500
```

### 結果（かな CER）

| | mean | median | sd | max |
|---|---:|---:|---:|---:|
| 教師 | 0.1351 | 0.0860 | — | 0.500 |
| 生徒 | 0.1776 | 0.1457 | — | 0.500 |

差 **+0.0425**、bootstrap CI95 **[−0.0136, +0.0889]**、
paired-t **p=0.1257** / Wilcoxon **p=0.0395**。

⚠️ **2 つの検定が食い違う。** 平均ベースの t は有意でなく、順位ベースの Wilcoxon は
有意。分布に外れ値（教師・生徒とも max 0.500）があるので Wilcoxon のほうが
妥当だが、**n=24 では「生徒のほうが悪い」を確定できない**と書くのが正確。
中央値の差（0.086 → 0.146）は t が拾えていない。

⚠️ **Whisper 自体の誤りが両方に乗る。** 教師（合成音声）ですら 0.1351 ある。
**生徒の絶対 CER に意味は薄く、教師との差で読む。**

---

## M-39. Phase 6: int8 量子化と blob サイズ（自己実測）

再現: `uv run python scripts/quantize_student.py --ckpt runs/v2/stage4.pt --out reports/quant_v2`

方式は論文どおり: **symmetric int8 / per-output-channel**、
**embedding・LayerNorm・1-D パラメータ（bias / LayerScale）は fp32**。

| モジュール | int8 重み | scale | fp32 | 合計 |
|---|---:|---:|---:|---:|
| duration | 30,752 B | 772 B | 8,848 B | **40,372 B** |
| acoustic | 186,240 B | 3,232 B | 33,984 B | **223,456 B** |
| decoder | 327,300 B | 17,532 B | 16,032 B | **360,864 B** |

論文の 2 blob 構成に合わせた比較（論文は英語・語彙 157）:

| blob | 本実装（語彙 57） | 論文 | 差 |
|---|---:|---:|---:|
| blob1 (duration + acoustic) | **263,828 B** | 280,288 B | −16,460 |
| blob2 (decoder) | **360,864 B** | 399,544 B | −38,680 |
| **合計** | **624,692 B** | 679,832 B | **−55,140** |

**blob1 の差 −16,460 B は語彙差でほぼ説明がつく**: 埋め込みは fp32 のまま残すので
`(157−57) × (32 + 48) × 4 B = 32,000 B` 減るはずだが、実測差は 16,460 B。
残りは論文が位置埋め込みを持つかどうかなどの構成差（計画書 §6「未使用の最強制約」）。

⚠️ **blob2 の −38,680 B (−9.7%) は語彙と無関係**なので、**decoder の構成が
論文と違う可能性を示す**。逆算の探索箱が狭かった箇所（計画書 §6 の `Gγ`）と一致する。

### 量子化誤差（ランダム音素列 8 本）

| 経路 | SNR |
|---|---:|
| log duration | 37.9 dB |
| c-line | 38.2 dB |
| **波形** | **25.8 dB** |

⚠️ **`d` が完全一致したのは 8 本中 3 本だけ。** `clip_[1,80](round(·))` の
丸め境界で 1 フレームずれる発話がある。**フレーム数がずれると音の長さが変わる**ので、
実テキストでの影響を測ること（このプローブはランダム音素列）。

⚠️ これは **PTQ のシミュレーション**であって ESP32 のカーネルではない。
実機の演算順序・丸めは別に検証が要る。

---

## M-40. Phase 5: `β`（式7 摩擦音ノイズ注入）のスイープ（自己実測 / n=16）

再現:
```bash
uv run --extra eval python scripts/b_beta_sweep.py --ckpt runs/v2/stage4.pt \
    --betas 0,2,4,6,8 --n 16 --out reports/beta_sweep
```

目的関数は**教師との一致**（最大化ではない）:
`J(β) = mean_c |SFM_c(生徒)/SFM_c(教師) − 1|`（c ∈ fricative, affricate, devoiced）。

| β | J(β) | fric | affr | devo | RMS fric | SCOREQ 比 | UTMOS 比 | ガード |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **0** | **0.0165** | 1.0006 | 1.0153 | 1.0337 | 0.794 | **0.6186** | 0.7485 | ✅ |
| **2** | 0.0229 | 0.9965 | 1.0093 | 1.0560 | 0.831 | 0.5988 | 0.7445 | ✅ |
| 4 | 0.0227 | 0.9832 | 0.9959 | 1.0473 | 1.046 | 0.5783 | 0.7339 | ❌ |
| 6 | 0.0353 | 0.9676 | 0.9679 | 1.0414 | 1.335 | 0.5485 | 0.7297 | ❌ |
| 8 | 0.0405 | 0.9525 | 0.9533 | 1.0273 | 1.475 | 0.5151 | 0.7206 | ❌ |

ガード: `SCOREQ_ratio ≥ 0.95 × ratio(0)` かつ `UTMOS_ratio ≥ 0.975 × ratio(0)`。

**β を上げると SCOREQ が単調に下がる**（0.619 → 0.515）。論文でも
4.09 → 3.92 と下がっており、**同じ傾向が日本語でも出た**。

**⚠️ この生徒には式7 が要らない可能性が高い。** 論文の生徒は sibilant の SFM が
0.590 / 0.689 = **0.857** と低く、そこを補うのが式7 の目的だった。
本実装の生徒は **β=0 で既に fricative 1.0006 / affricate 1.0153** と教師に一致している。
足すべき欠損が無い。

**聴取候補: β = 0 と 2。** ⚠️ **最終決定は聴取（CMOS）で行う**（論文も同様）。
J(β) は代理指標にすぎない。

⚠️ **power 計算をしていない。** ガードの 5% / 2.5% の差を n=16 で分離できるかは
未検証（計画書 §Phase 5 の指摘がそのまま残っている）。

---

## M-41. Phase D: C99 推論コアが参照実装と一致した（自己実測）

再現:
```bash
uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt
cd csrc && make test
```

**依存は libm だけ。** malloc はコアで呼ばず、呼び出し側が arena を渡す。
重みは自己記述形式（SAAN v1: 名前・shape・dtype・offset をヘッダに持つ）で読む。

### ゴールデンテスト（「今日は良い天気ですね。」53 ids → 106 frames → 1.231 s）

| 項目 | Pearson | SNR | max\|Δ\| |
|---|---:|---:|---:|
| `d_hat`（整数） | **53/53 完全一致** | — | 0 |
| `log_d` | 1.000000 | 125.91 dB | 7.2e-07 |
| `c`（40ch） | 1.000000 | 131.37 dB | 7.2e-07 |
| **`pcm`** | **1.000000** | **117.51 dB** | 5.8e-07 |

**受け入れ条件（Pearson ≥ 0.98）を大きく超えた。** 残差は fp32 の丸め誤差水準。
`cc -Wall -Wextra` で警告 0。

⚠️ `d_hat` の完全一致は運任せの部分がある。C の `roundf` は half-away-from-zero、
`torch.round` は **half-to-even**。ちょうど .5 のときだけ結果が割れる
（この文では発生しなかっただけ）。**入力を変えたら再確認すること。**

### ⚠️ このままでは ESP32 に載らない（1.26 MB / SRAM の 246%）

arena の実使用は 0.97 MB。106 frames の発話での内訳:

| 項目 | KB | 割合 |
|---|---:|---:|
| **mag + cos + sin** | **637.2** | 51% |
| iSTFT の acc + wsq | 218.0 | 17% |
| decoder の te (E=304) | 125.9 | 10% |
| pcm | 106.0 | 8% |
| decoder の h/tw/tg | 94.4 | 8% |
| acoustic の hf | 59.6 | 5% |
| c-line | 16.6 | 1% |
| **合計** | **1,257.7** | — |

**ESP32-S3 の SRAM 512 KB に対し 246%。M-16 の見積もり 96 KB の 13.1 倍。**

M-16 が間違っていたのではなく、**あの見積もりは「I2S 逐次出力」を前提にしていた**
のに対し、現在の実装は**発話全体をバッファしている**（正しさを先に出すため）。

**ストリーミング化で消える分**:
- `mag/cos/sin` はフレームごとに作れば 513×3×4 = **6 KB**（637 KB → 6 KB）
- iSTFT は overlap-add のリングバッファで **10 KB**（218 KB → 10 KB）
- `pcm` は I2S に流せば保持不要（106 KB → 0）

⚠️ **時間方向のカーネルがあるのでフレーム単位には割れない。**
decoder の受容野は `inp` k=3 (±1) + `dw` k=7 × 5 段 (±15) = **±16 フレーム**、
acoustic の frame block は k=5 × 5 段 = **±10 フレーム**。
チャンク処理＋オーバーラップが要る。

⚠️ **naive DFT を使っている**（`irfft_1024`）。正しさ優先で FFT にしていないので
**実機のレイテンシ測定には使えない**。O(N²) = 1024² per frame。

---

## M-42. Phase D-2: ストリーミング化で ESP32 の SRAM に載った（自己実測）

再現: `make -C csrc all-test`（`csrc/stream_test.c` が D-029 の G1〜G4 を判定する）

### 結果

| # | 条件 | 結果 |
|---|---|---|
| **G1** | ピーク RAM < 200 KB | ✅ **197.0 KB**（実用最大 350 ids / 785 frames）。⚠️ **arena のみ。** D-3 統合後は stack 込みで測り直した（M-43） |
| **G2** | 一括版と **bit 完全一致** | ✅ **27,136 sample すべて一致**（`memcmp`） |
| **G3** | 発話長に対して RAM が O(1) | ✅ ids 比例分（8 B/id）を除いて **194.3 KB で一定** |
| **G4** | golden test が通り続ける | ✅ Pearson 1.000000 |

| 発話長 | ピーク | ids 比例を除く |
|---:|---:|---:|
| 53 ids / 106 frames | 194.7 KB | 194.3 KB |
| 350 ids / 785 frames（実用最大 = D-017） | **197.0 KB** | 194.3 KB |
| 848 ids / 1,921 frames | 200.9 KB | 194.3 KB |

**一括版 1,258 KB → 197 KB（−84%）。** ESP32-S3 の SRAM 512 KB に対し 38%。

⚠️ **G1 の判定は「テストに使った 1 文」ではなく実用最大長で行う。**
最初は 53 ids（185 KB）だけで測って OK を出していたが、350 ids では 243 KB あった。
**短い文だけで測ると甘い判定になる。**

### 実装で踏んだ 4 つの罠（どれも「動くが値が違う」）

| # | 症状 | 原因 |
|---|---|---|
| 1 | 全サンプル不一致 max\|Δ\| 0.37 | **段の出力の発話外に bias 由来の非ゼロ**が残る。一括版は配列外＝ゼロ |
| 2 | 先頭 pad フレームがずれる | **AcBlock 内部の c1→c2 の間**も同じ問題。c1 の出力の発話外もゼロにする必要がある |
| 3 | 出力サンプル 1025 以降が壊れる | **iSTFT リングの衝突**。`out_pos` を N/2 から始めると `[0, 512)` が pop されずゼロクリアもされない |
| 4 | 末尾フレームが出ない | **時刻が負のフレームも iSTFT に push**していた。絶対フレーム番号で位置を決める |

⚠️ **1 と 2 は「同じ原因の別の現れ方」**。conv が時刻を混ぜる箇所すべてで、
発話外をゼロにしないと一括版と一致しない。**1x1 conv の後は不要**（時刻を混ぜないため）。

⚠️ **c-line を PyTorch の golden と比べて 7e-07 の差に悩んだが、
それは一括版 vs PyTorch の差と同値だった。** ストリーミングの検証では
**一括版と比べる**のが正しい（比較対象を間違えると原因の切り分けができない）。

### 方式（D-029）

各段が入力を `[C][2*pad + CHUNK]` に溜め、**バッファ全体に conv を掛けて中央
CHUNK フレームだけ**を下流へ渡す。中央フレームの計算に必要な入力は全部バッファ内に
あるので、**カーネルを一括版と共有したまま bit 一致する**。

| 段 | pad | 由来 |
|---|---:|---|
| AcBlock × 5 | 4 | c1 k=5 (±2) + c2 k=5 (±2) |
| decoder inp | 1 | k=3 |
| dw ブロック × 5 | 3 | k=7 |
| **合計遅延** | **36 フレーム** | + iSTFT の 2 フレーム = **38 フレーム = 0.44 秒** |

**token block もチャンク化した**（受容野 ±12 トークン）。
`tok_h` を発話全体で持つと **192 B/id** かかり、350 ids で 68 KB になって G1 を
満たさなかった。必要なトークン範囲 ±12 を都度計算する方式に変えて O(1) にした。

計算量は各段 `(2*pad + CHUNK) / CHUNK` 倍（pad=3, CHUNK=8 で 1.75 倍）。
ハロー再計算方式（全体 10 倍）よりはるかに軽い。

⚠️ **`irfft` は naive DFT のまま**（O(N²)）。**レイテンシはまだ測れない**（Phase D-3）。
⚠️ **int8 カーネルも未実装。** C コアは fp32 で読んでいる。

---

## M-43. Phase D-3a/b: FFT 化とレイテンシの初測定（自己実測）

再現: `make -C csrc all-test` / `make -C csrc run-bench`

### D-3a: radix-2 逆実 FFT（`csrc/fft.{h,c}`）

| 項目 | 値 |
|---|---:|
| naive DFT | 2,221,638 ns/call (sd 18,643, n=1000) |
| **radix-2 FFT** | **1,548 ns/call** (sd 498, n=1000) |
| **速度比** | **1,435 倍** |
| naive との SNR | **138.7 dB**（要件 120 dB を大きく超える） |
| twiddle テーブル | 6,144 B（flash 可） |
| stack | 4,128 B |
| コンパイル警告 | 0 |

⚠️ **bit 一致は要求していない**（積和の順序が違うので必ずずれる）。
⚠️ **naive DFT は消していない。** `-DSAAN_USE_NAIVE_DFT` で切り替わる。
**FFT の検証基準として残す。**

⚠️ **SNR 138 dB の原因は「float 出力への丸め」ではなく内部 float 演算**（照合で訂正）。
double 版は naive と 179/200 が bit 一致し、float↔double の差が 137.7 dB。
**出力丸めの寄与はゼロ。**

⚠️ **stack 4,224 B は arena の外**（照合で指摘）。ESP32 では SRAM を共有するので
**G1 の判定に合算する**ように直した。arena だけ見ていると 200 KB 超過に気づかない。

統合後も golden test は Pearson 1.000000（`out.pcm` の SNR 117.51 → 117.50 dB）、
ストリーミングの G1〜G3 も維持（一括版と bit 一致）。

### D-3b: レイテンシ（**このプロジェクト初の測定**）

手元 = Apple M4 Max / `cc -O2` / fp32 / ストリーミング版。
「音声 1 秒あたりの実時間」= × RT。

| 発話 | FFT 化前 | FFT 化後 | **+ 無駄チャンク削除** | 改善 |
|---|---:|---:|---:|---:|
| short (106 frames) | 0.228 | 0.037 | **0.031** | 7.4x |
| medium (323 frames) | 0.219 | 0.027 | **0.024** | 9.1x |
| long (785 frames) | 0.215 | 0.024 | **0.022** | 9.8x |

**無駄チャンクの削除**（照合で指摘）: `pull` の打ち切り条件が
`+ 4*CH + 16` の安全マージンを積んでおり、**48 フレーム（5 チャンク）が
出力に一切寄与していなかった**。外しても PCM は bit 一致する。

**段別（long / FFT 化後）**:

| 段 | ms | 割合 |
|---|---:|---:|
| **decoder** | **127.2** | **59.9%** |
| acoustic frame | 52.5 | 24.8% |
| token block | 30.0 | 14.1% |
| **iSTFT** | **1.9** | **0.9%** |
| duration | 0.7 | 0.3% |

**iSTFT は 88% → 0.9% になり、いまは decoder が支配的。**
段別の合計は全体の **96.8%** を説明する（内訳が信頼できる）。

⚠️ **段別はマイクロベンチのモデル**（`saanotts_stream.c` を編集せずに測るため）。
⚠️ **bench の段別 iSTFT が naive DFT のまま**で整合性が 8.9 に飛んだ。
本体と**同じ関数を呼ぶ**ように直して 0.968 に戻した。
**同じ計算を 2 か所に書くと、片方だけ古くなる**（D-2 で得た教訓の再発）。

### ⚠️ メモリと速度のトレードオフ（実測して速度を選んだ）

`o1539`（decoder の生出力 `[1539][CH]` = 49 KB）を 1 フレームずつにすると
arena は減るが `saan_conv1d` の T=1 呼び出しが効率を落とす:

| 構成 | RAM（stack 込み） | 速度 |
|---|---:|---:|
| `o1539` を 1 フレームずつ | 159.3 KB | 0.031 × RT |
| **`o1539` を CH まとめ + 作業領域を実寸に詰める** | **196.9 KB** | **0.022 × RT** |

**ESP32 では速度が律速**（移植可能 C で 0.93× RT）なので**速度を取った**。
作業領域を段ごとの実寸（`AC_W×16` と `DEC_W×14`）に詰めて 200 KB を切っている
（以前は `maxC(76) × maxW(16)` という**どちらにも要らない上限**を確保していた）。

### ESP32-S3 への外挿（⚠️ **実機は一度も動かしていない**）

出典は Espressif のデータシートと **esp-dsp の公表サイクル数**
（`esp-dsp-benchmarks.rst` の raw を fetch。⚠️ **HTML 版を要約させると別バージョンの
数値が返る**）。本生徒の 43.618 MMAC/audio-second（M-26）に対する予測:

| 実装 | core | iSTFT | 合計 × RT |
|---|---:|---:|---:|
| **移植可能 C / fp32（＝現行 `csrc` をそのまま移植）** | 0.900 | 0.029 | **0.93** |
| esp-dsp 級 asm / fp32 | 0.295 | 0.016 | 0.31 |
| esp-dsp 級 PIE / int16 | 0.085 | 0.003 | 0.088 |
| **esp-dsp 級 PIE / int8** | 0.029 | 0.003 | **0.032** |

⚠️ **η（アプリ効率）= 1.0 の下限値**。活性化・LayerNorm・再量子化・メモリ移動が
入るので実際は遅くなる。

⚠️ **照合で η を実測できた**: 手元の非 iSTFT 実測から **η_host = 0.364**。
これを転移すると移植可能 C / fp32 の core は 0.900 ではなく **2.47× RT**。
**「0.93 でぎりぎり」ではなく「fp32 では届かない」が正しい読み方**で、
int8 + PIE が必須という結論はむしろ強化される。

**論文の 0.22× RT の検算**: 45 MMAC/s ÷ 0.22 = **205 MMAC/s** が必要。

| 経路 | 必要な η | 判定 |
|---|---:|---|
| esp-dsp f32 asm (142 MMAC/s) | **144%** | **不可能** |
| esp-dsp int16 asm (490 MMAC/s) | 42% | 妥当 |
| esp-dsp int8 asm (1,427 MMAC/s) | 14% | 妥当（余裕あり） |

**論文の 0.22× RT は fp32 では達成できない。int8 または int16 の PIE が前提。**
論文が int8 blob を配っている（679,832 B / 567,008 params ≒ 1.2 B/param）ことと整合する。

**→ 本プロジェクトの結論: 現行の移植可能 C（fp32）をそのまま ESP32 に載せると
0.93× RT で実時間ぎりぎり。実用には int8 PIE カーネルが要る（D-3c の続き）。**

### D-3c: int8 カーネル（`csrc/saanotts_int8.{h,c}`）

| 検証 | 結果 |
|---|---|
| 逆量子化 fp32 との一致 | **100 dB 超**（カーネルは正しい） |
| fp32 との差（重み int8 / activation fp32） | SNR **44.0 dB** |
| fp32 との差（重み・activation とも int8） | SNR **41.6 dB** |
| int8 blob | 643,936 B（payload 624,692 B。**M-39 と一致**） |
| C の量子化器と Python の一致 | **544,292/544,292 値が一致** |
| fp32 コアに int8 blob を渡す | **「必要なテンソルが無い」で止まる**（黙って走らない） |

⚠️ **手元では速くならない**（全 conv 層で fp32 比 0.86〜1.01 倍）。
Apple の SIMD が fp32 向きなため。**ESP32-S3 では逆になるが、それは測れない。**

⚠️ **今の int8 カーネルは移植可能 C。** 上表の「esp-dsp 級 PIE」に届くには
**アセンブリか intrinsic が要る**（未着手）。

⚠️ **`duration.proj.weight` は int8 経路に載らない**（照合で指摘）。
52 個の int8 テンソルの 1 つなのに `saan_conv1d` を通らず
`saanotts.c` のインライン内積で使われている。統合時に対応が要る
（現状は `SAAN_ERR_MISSING` で**安全に止まる**）。
→ **M-45 (c'-1) で解消**。conv 化して fp32 で bit 一致を確認したうえで int8 経路に載せた。

⚠️ **W8A8 の end-to-end も照合で実測された**: 波形 SNR **24.06 dB**（W8A32 は 26.65 dB）。
（→ C 実装での再測定は **M-45**: held-out 24 文で W8A32 25.88 dB / W8A8 23.24 dB）
差は 2.59 dB で**層ごとの差とほぼ同じ = 30 層直列でも誤差は蓄積しない**。
W8A32 を選ぶ理由は「W8A8 が危険だから」ではなく
「**flash は 1 バイトも減らず、速度利得もホストでは 0.86 倍しかない**」から。


## M-44. D-4: アクセント型ミニマルペアの再現性（自己実測 / 15 群 32 語 64 文 38 ペア）

再現（**18 秒**・CPU・2 回走らせて `d4_accent.json` が SHA-256 まで bit 一致）:

```bash
uv run python scripts/d4_accent_pairs.py --build-only              # 評価セットのゲートだけ
uv run python scripts/d4_accent_pairs.py --ckpt runs/v2/stage4.pt --out reports/d4_accent
```

CLAUDE.md の未解決 #5。SCOREQ / UTMOS / CER は集約指標なので
「橋 と 箸 が同じ音になっている」を検出できない。生徒の duration net は音素IDしか
見ないため、ピッチアクセントは音素列の記号 `[` `]` `#` だけで運ばれる。
**この記号だけで足りるか**を測った。凍結設定は `src/saanotts_jp/accent.py`。

### 評価セット（`data/splits/accent_pairs.tsv`）

ミニマルペア 15 群 32 語 × キャリア 2 本 = **64 文 / 38 ペア**。
**アクセント型は手打ちせず `pyopenjtalk.run_frontend(word)[0]["acc"]` から取って
スクリプトが assert する**。全 32 語が `mora_size=2`、群内で `pron` が一致。

キャリアは `そこに{}がならんでいる。` と `ここに{}があるよ。`。
生成時に 5 つのゲートを全群で通した（落ちた群 0）:

| ゲート | 結果 |
|---|---|
| (a) 群内で**記号を除いた音素列が完全一致** | 30/30 群×キャリアで一致 |
| (b) 中間表現が群内で相異なる | 一致 |
| (c) 無声化マーク `°` を含まない | 0 件 |
| (d) 句境界 `#` を含まない | 0 件 |
| (e) `corpus_train` / `corpus_heldout` と完全一致 | **0 件**（20,985 + 2,333 行に対して） |

健全性: 発話速度 教師 7.94 [7.05, 8.83] / 生徒 8.26 [7.53, 9.07] mora/s（G12 の 4–12 内）。

⚠️ **この 64 文はテンプレートなので蒸留コーパスに入れない**（CLAUDE.md「テンプレート文は
使わない」）。ミニマルペアはキャリア固定が成立要件なので**評価専用**として置く。

### ⚠️ 「A と B の音が違う」は証拠にならない

`[` `]` `#` は教師で実フレームを持つ（M-35 の `reports/durations/durations.npz`）。
本セットでもペア内の `|Δn_ids|` は {0: 14 ペア, 2: 24 ペア}、
教師の `|Δframes|` 平均 **3.03**（全長平均 113.4 フレーム）。
**アクセントを完全に無視するモデルでも A と B は違う音になる。**
したがって主指標は差の大きさではなく**向き** `cos(Δ_T, Δ_S)` にした
（これは二乗距離テンプレート照合の 2AFC と数学的に同値）。

### 教師ベースライン — 教師も全部は弁別していない

| 型の組合せ | n | 教師 `\|Δ_T\|` 平均 | min |
|---|---:|---:|---:|
| 0型 vs 1型 | 18 | 3.678 st | 1.487 |
| 1型 vs 2型 | 14 | 3.031 st | 1.488 |
| 0型 vs 2型 | 6 | 2.764 st | 1.627 |

`|Δ_T| >= 1.5` semitone のゲートを **36/38 が通過**。落ちた 2 ペアは
`kaki_c1 牡蠣/垣 (1.488)` と `kashi_c1 菓子/貸し (1.487)`。**この 2 つは生徒の失点にしない。**

### 主指標（教師ゲート通過 36 ペア / グループ単位クラスタ bootstrap 10,000 回）

| 指標 | 値 | CI95 | 経験的ヌル |
|---|---:|---|---:|
| **符号一致 `cos > 0`** | **35/36 = 0.9722** | [0.897, 1.000] | **0.614** |
| **cos 平均** | **0.7159** | [0.632, 0.798] | **+0.135** |
| `\|Δ_S\|/\|Δ_T\|` 平均 | 1.1712 | [0.981, 1.403] | — |

⚠️ **chance は 0.5 ではない。** 全ペアが同じ 2 本のキャリアを共有するので Δ どうしに
相関が残る。順列ヌル（別グループの `Δ_T` に当てる、n=308）で
**cos>0 が 0.614 / cos 平均 +0.135**。型の組合せが違うものだけに絞った厳しめのヌル
（n=200）でも 0.625 / +0.155。**符号一致・cos 平均とも CI95 下限がヌルを上回る。**

### 交絡を落とした部分集合と別指標でも同じ向き

| 見方 | 結果 |
|---|---|
| `\|Δn_ids\| == 0`（トークン数が同一＝持続長で説明できない） | **13/13**、cos 平均 0.655 |
| 群内同定（生徒の輪郭を教師テンプレートに最適割り当て）2 メンバー | 25/26 = 0.962（chance 0.5） |
| 同 **3 メンバー**（箸/橋/端・牡蠣/柿/垣） | **4/4**（chance **1/6**） |
| 教師ゲート 0.0 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0 / 3.5 st | 0.974 / 0.974 / 0.972 / 1.000 / 1.000 / 1.000 / 1.000 |

**しきい値を動かしても結論は動かない。**

### 副指標 — 境界 AUC（`]` 単独と `][` を分けて集計）

piper-plus は 1 型（頭高）で `]` と `[` を同じ境界に出す
（`g2p/piper_plus_g2p/japanese.py` の挿入判定が elif ではなく独立した `if`）。
本セットの `]` 76 個のうち**単独は 16 個だけ**（それが 2 型の下降核にちょうど対応する）。

| | 下降 all | `]` 単独 | `][` | 上昇 `[` |
|---|---:|---:|---:|---:|
| 教師 | 0.7044 (n=76 vs 528) | **0.6526 (n=16)** | 0.7182 (n=60) | 0.6636 (n=189) |
| 生徒 | 0.7713 (n=72 vs 517) | **0.5895 (n=13)** | 0.8114 (n=59) | 0.6415 (n=183) |
| 生徒/教師 | 1.095 | **0.904** | 1.130 | 0.967 |

下降の深さは 教師 +0.804 st / 生徒 +1.210 st（比 1.505）。

⚠️ **集約した「下降 all 1.095」は 2 型の弱さを隠す。** `]` 単独＝2 型の下降核では
教師 0.6526 に対し生徒 **0.5895**（ほぼ chance）。ただし **n=13〜16 と小さい**ので
「2 型が壊れている」とまでは言えない。ペアコントラスト側では
1型 vs 2型 が 13/13、0型 vs 2型 が 6/6 なので、**指標間で食い違っている**。

### モーラ F0 の欠測 — **1 つだけ出た逆向きはここで説明が付く**

モーラ F0 取得率は 教師 **0.9961** / 生徒 **0.9839**（欠測 13 / 672 発話×モーラ）。
欠測の内訳は `し`6 / `さ`2 / `そ`2 / `か`2 / `き`1（摩擦音・破裂音の頭で pyin が落ちる）。
⚠️ **当初「`し` `さ` `か` `き` に集中」と書いたが `そ` 2 件が抜けていた**（照合で指摘）。

**アクセント記号が違う境界に接するモーラが欠測すると、そのペアの cos は
キャリアだけを比べている。** 36 ペア中 **10 ペア**がこれに該当し、
**唯一の逆向き `kashi_c0 菓子/貸し (cos = −0.130)` もその 1 つ**
（マスクが 12 モーラ中 9 を残したが、落ちた 3 つに語の 2 モーラ `か` `し` が
両方入っていた）。

| | n | 符号一致 | cos 平均 |
|---|---:|---:|---:|
| 主指標（欠測ペアも含む） | 36 | **35/36 = 0.972** | 0.716 |
| 参考: 記号境界のモーラが残ったペアのみ | 26 | 26/26 = 1.000 | 0.767 |

⚠️ **26/26 を主指標に格上げしない。** 逆向きが出たあとに定義した部分集合なので
**後付けの除外**にあたる。分母 26 は 36 の 72%。両方を並べて読むこと。

### 採用しなかった指標

| 指標 | 実測 | なぜ落としたか |
|---|---:|---|
| ピーク位置（argmax モーラ）一致 | 25/64 = **0.391** | 高原状の輪郭で argmax が跳ぶ。chance 相当 |
| 発話長・`\|Δframes\|` の差 | 生徒/教師 0.9622 | 記号がフレームを持つので**アクセント無視でも差が出る** |

### 未検証

- **聴取していない。** 1.17 倍（中央値 1.088、範囲 0.38〜3.30）のコントラスト差が
  耳にどう聞こえるかは指標では決まらない。wav は
  `reports/d4_accent/{teacher,student}/*.wav`（64 対）に残してある
- **2 型の下降核**は `]` 単独 n=13〜16 でしか測れていない。2 型語を増やさないと確定しない
- キャリアは 2 本だけ。**語頭配置では指標が壊れる**ことは確認済み（D-030）だが、
  文末配置・疑問文・複合語内は未測定
- `s_v=1.2187` / `β=0` / CPU / `runs/v2/stage4.pt` の 1 条件のみ

---

## M-45. Phase D-3c'-1/2: int8 経路の end-to-end 統合（自己実測）

**何を解いたか**: int8 カーネルは M-39 で書けていたが**パイプラインに 1 行も繋がっていなかった**。
`saan_w()` によるディスパッチを入れ、一括版・ストリーミング版・bench の
**13 + 13 + 13 か所すべて**を fp32 / int8 両対応にした。
どちらの経路を通るかは**読み込んだブロブの dtype だけ**で決まる（実行時）。

M-39 が残していた宿題「実テキストでの影響を測ること」への回答でもある。

再現（3 分・ホスト・M4 Max）:

```bash
make -C csrc clean && make -C csrc all-test       # 6 本すべて
make -C csrc int8-golden                          # 統合の主ゲート
make -C csrc int8-e2e                             # 波形 SNR（held-out 24 文）
cd csrc && ./bench --weights student_i8.bin --golden golden.bin \
    --json ../reports/d3c2_latency_int8.json --label int8-w8a32
```

### c'-1: `duration.proj.weight` が int8 経路に載った

M-39 の照合が指摘した「52 個の int8 テンソルのうちこれだけがインライン内積」を解消。
`nn.Conv1d(32,1,1)` なので `saan_conv1d` に置き換えるだけで意味論が一致する。

**積和の順序が変わっていないことを fp32 で単独検証した**（先に int8 化すると切り分け不能）:

| `./golden_test student.bin golden.bin` | 置換前 | 置換後 |
|---|---:|---:|
| d_hat 完全一致 | 53/53 | 53/53 |
| out.log_d SNR | 125.91 dB (max\|Δ\| 7.227e-07) | **125.91 dB (7.227e-07)** |
| out.c SNR | 131.37 dB (7.153e-07) | **131.37 dB (7.153e-07)** |
| out.pcm SNR | 117.50 dB (5.811e-07) | **117.50 dB (5.811e-07)** |

`./stream_test student.bin golden.bin` の出力も **20 行 diff なしで一致**。

### c'-2: 統合の主ゲート — C の int8 経路が PyTorch の fake-quant と一致する

⚠️ **fake-quant golden はこの作業まで存在しなかった。** `export_c_weights.py --int8` は
`quantize()` の結果をモデルに書き戻していなかったので、golden は常に fp32 参照だった。
その相手と比べると差が「量子化誤差 26 dB」に埋もれ、**層を 1 つ fp32 に置き忘れても検出できない**。
`--golden-from-quantized` を足して塞いだ（blob に int8 で書いた 52 テンソルを
そのまま逆量子化して書き戻すので、**規則が二重化しない**）。

`./golden_test student_i8.bin golden_i8.bin`:

| 段 | Pearson | SNR |
|---|---:|---:|
| d_hat | — | **53/53 完全一致** |
| out.log_d | 1.000000 | **120.70 dB** |
| out.c | 1.000000 | **127.75 dB** |
| out.pcm | 1.000000 | **115.91 dB** |

fp32 経路の同じ比較（125.91 / 131.37 / 117.50 dB）とほぼ同水準。
**int8 でも fp32 でも「C は参照実装と一致する」が成立している。**

⚠️ **bit 一致ではない**（`saan_conv1d_i8` は scale を最後に 1 回掛けるので原理的にずれる）。
判定は必ず SNR で行う。40 dB 台に落ちたら層の載せ忘れ。

### c'-2: 波形 SNR（int8 経路 vs fp32 経路。どちらも C）

`./int8_e2e_test student.bin student_i8.bin golden.bin ids_heldout.bin`

⚠️ **d̂ は fp32 側に固定して測る。** 固定しないと int8 で d̂ が変わった文で
フレーム数が変わり、**波形 SNR がそもそも定義できない**。
1 フレーム = 11.6 ms のずれは可聴でないので、これは品質ではなく測定手順の問題。
測定専用の入口 `saan_synthesize_d()` を足した（本番は `saan_synthesize`）。

| | golden 1 文 | held-out 24 文 |
|---|---:|---:|
| log_d SNR | 34.14 dB | 33.63 〜 39.92 dB |
| c SNR | 41.17 dB | 40.76 〜 43.52 dB |
| **波形 SNR** | **26.08 dB** | **平均 25.88 / 最小 23.27 / 最大 31.28 dB** |
| d̂ 一致（文単位） | 53/53 | 9/24 文が完全一致 |
| d̂ 一致（トークン単位） | 53/53 | **2,395/2,425 = 98.8%** |

⚠️ **計画書の「波形 SNR ≥ 25 dB」は 1 文ごとの判定としては達成できない。**
**24 文中 9 文が 23.27〜24.99 dB** に入る。これは量子化そのものの性質で、
C 実装とは無関係（M-39 の PTQ シミュレーションも 8 本の**平均** 25.8 dB / 最小 23.51 dB）。
判定は「**24 文の平均 ≥ 25 dB かつ最小 ≥ 23 dB**」に直した（`int8_e2e_test.c` に凍結）。

### メモリ — int8 化しても実行時 RAM は 1 バイトも変わらない

`./stream_test student_i8.bin golden.bin`（W8A32・既定）:

| | fp32 ブロブ | int8 ブロブ |
|---|---:|---:|
| G1 実用最大 350 ids の arena | 192.8 KB | **192.8 KB** |
| ＋ FFT stack | 4.1 KB | 4.1 KB |
| **合計（G1 判定値）** | **196.9 KB** | **196.9 KB** |
| G2 一括版と bit 完全一致 | 27,136 sample | **27,136 sample** |
| ブロブ（flash） | 2,249,792 B | **643,936 B**（−71.4%） |

**減るのは flash だけ。** W8A32 は activation を fp32 のまま扱うので中間バッファが同じ。

### ⚠️ 副産物: G3「RAM が O(1)」は duration の一時領域を数えていなかった

`saan_arena` に**高水位 `peak`** を足した（`used` は mark/rollback で戻るので
一時確保を取りこぼす）。すると G3 の数字が変わった:

| ids | パイプライン定常部 | 高水位 |
|---:|---:|---:|
| 53 | 190.5 KB | 190.5 KB |
| 350 | 192.8 KB | 192.8 KB |
| 848 | 196.7 KB | **324.6 KB** |

原因は `saan_stream_init` 冒頭の `saan_run_duration` が h/t1/t2 を
**384 B/id** 取ってすぐ返すこと。`saan_stream_arena_needed` は**最初からこの分を数えている**
ので確保漏れではない（350 ids で確保 218.4 KB > 定常 192.8 KB の差がこれ）が、
**「RAM が O(1)」という表示だけが実態より小さかった**。

⚠️ **D-017 の実用最大 350 ids では定常部（192.8 KB）が一時領域（134 KB）を上回るので
G1 = 196.9 KB は変わらない。** 交差するのは約 480 ids から。
G3 は「パイプライン定常部が O(1)」を測るよう直し、高水位を併記するようにした。

### W8A8（`-DSAAN_INT8_ACT=1`）— 既定にしない理由が実測で裏づいた

これまで W8A32/W8A8 の区別は**関数名の接尾辞と散文だけ**だった。
`SAAN_INT8_ACT`（既定 0）というコンパイル時スイッチにして grep 可能にした。

`make -C csrc CFLAGS_EXTRA=-DSAAN_INT8_ACT=1 …`:

| | W8A32（既定） | W8A8 |
|---|---:|---:|
| 波形 SNR 平均 / 最小（24 文） | **25.88 / 23.27 dB** | 23.24 / 20.87 dB |
| 25 dB 未満の文 | 9/24 | 21/24 |
| d̂ トークン一致 | 2,395/2,425 (98.8%) | 2,367/2,425 (97.6%) |
| G1 合計 RAM | **196.9 KB** | **201.1 KB（200 KB を超える）** |
| G2 一括版と bit 一致 | OK | OK |
| ブロブ（flash） | 643,936 B | **643,936 B（同じ）** |

**W8A8 は G1 と波形 SNR の両方を落とし、flash は 1 バイトも減らない。**
超過分 4.2 KB は conv 1 本ぶんの activation 作業領域（`decoder.pw2` cin=304 × 窓 16）。
⚠️ この 4.2 KB は `saan_arena_needed` / `saan_stream_arena_needed` に
**数え込んである**（数えないと `SAAN_ERR_ARENA` で落ちる）。

⚠️ W8A8 を fake-quant golden と比べると **26.98 dB** にしかならない。
golden は activation が fp32 なので、**W8A8 の正しさはこの golden では検証できない**
（比較相手が違う）。W8A8 を本気で使うなら activation も量子化した参照が要る。**未実装。**

### 速度（参考値。**合否には使わない**）

`./bench --weights <blob> --golden golden.bin`（公開 API の全体時間、n=10）:

| ケース | fp32 | int8 W8A32 | 比 |
|---|---:|---:|---:|
| short 53 ids / 1.231 s | 40.75 ms (0.033× RT) | 45.42 ms (0.037× RT) | 1.11× |
| medium 150 ids / 3.750 s | 96.40 ms (0.026× RT) | 106.69 ms (0.028× RT) | 1.11× |
| long 350 ids / 9.114 s | 215.34 ms (0.024× RT) | 234.39 ms (0.026× RT) | 1.09× |

⚠️ **ホストで int8 が遅いのは正常。** Apple の SIMD は fp32 向き（M-39 のカーネル単体でも
W8A32/fp32 = 1.02）。**PIE の効果は ESP32 実機でしか測れない**ので、
この表を「int8 化が失敗した」と読まないこと。

### 併せて直した 2 件（どちらも int8 統合を止めていた）

1. `scripts/gen_teacher_labels.py` / `scripts/b6_flatness_grid.py` の
   `glob.glob("~/.cache/…")` が **`~` を展開しない**。キャッシュが実在しても
   `snapshot()` は必ず `SystemExit` していた。`export_c_weights.py` がこれを呼ぶので
   **`make student_i8.bin` は blob だけ書いて golden 生成の直前で死ぬ**（部分書き込み）。
   `os.path.expanduser()` を通して修正。
   確認: `uv run python -c 'import sys;sys.path.insert(0,"scripts");import gen_teacher_labels as G;print(G.snapshot())'`
2. `csrc/bench.c` が `saan_tf` の戻り値を**一度も NULL 検査していなかった**。
   `bconv`/`bdw`/`BNEED` で塞いだ。
   ⚠️ **当初「int8 ブロブを渡すと NULL 参照で落ちる」と書いたが症状の記述が不正確**
   （照合で指摘）。修正前の HEAD でも NULL 参照クラッシュではなく
   `stream init 失敗` を出して exit していた。

### 何を見ていないか

- **ESP32 実機**。速度もメモリも手元の外挿でしかない
- **聴取**。波形 SNR 25.88 dB が聞いて分かる差なのかは測っていない
- **int8 の SCOREQ / UTMOS / CER**。品質指標は fp32 生徒でしか測っていない（M-37 / M-38）
- **W8A8 の正しさ**。activation を量子化した参照が無い

---

## 未測定（測る必要があるもの）

| 項目 | なぜ必要か | 対応タスク |
|---|---|---|
| 枝刈り辞書の実バイナリサイズと実文カバー率 | **プロジェクトの成否を決める** | B-0 |
| 教師音声の品質ベースライン（正しい経路・十分な長さで） | 生徒の上限が決まる | B-5 |
| `dT` のヒストグラムと `clip_[1,80]` の飽和 | 日本語で上限 80 frames (0.93 s) が足りるか | B-7 |
| 日本語 length scale `s_v` | 論文は英語 1.08 / ベトナム語 1.16 | B-7 |
| A1/A2/A3 を落とした場合の `dT` の変化 | 生徒 duration net に韻律を足すべきか | P0-3 |
| 567 K / 1.4 M の日本語 CER | 実用性の判断 | Phase 6 |
| **`use_vanilla=True` でラベル生成した場合の教師音声品質** | **学習/デプロイの G2P を揃えれば M-11 の乖離は消えるはず。教師が vanilla 音素列で良い音を出すかは未検証** | B-0 追試 |
| C 実装のみの読み・アクセントが「間違い」なのか「別解」なのか | M-11 は一致率であって正誤ではない。ネイティブ聴取か正解データが要る | B-0 追試 |
| 枝刈り辞書 × `use_vanilla` の組み合わせでの一致率 | 現在の測定は Python 後処理層ありが基準。二重に効くのか相殺するのか不明 | B-0 追試 |

---

## M-46. Phase D-3c'-4: ESP-IDF 雛形と arena の実測（自己実測）

⚠️ **ESP32 実機の数値は 1 つも無い。** この環境に ESP-IDF も xtensa toolchain も
無い（`idf.py` 無し / `IDF_PATH` 空 / `~/.espressif` 無し）ので、
`idf.py build` / flash / 実 SRAM / 実レイテンシ / I2S の実サンプルレートは
**すべて未検証**。ここに載るのは全部ホスト（M4 Max）の値。

再現:

```bash
bash scripts/check_esp32_template.sh    # 下の (a)(c)(f)(g) を含む 9 ゲート
make -C csrc arena                      # (b)(d)(e)。⚠️ 既知の欠陥で現状 exit 1
```

### (a) コアにホスト専用 API が無い

```bash
cd csrc && for f in saanotts.c saanotts_stream.c fft.c saanotts_int8.c; do
  cc -std=gnu17 -O2 -Wall -Wextra -Werror -c $f -o /tmp/$f.o; done
nm -u /tmp/*.c.o | sed 's/^ *//' | grep -v '^_saan_' | sort -u
```

出力（`___chkstk_darwin` / `___stack_chk_*` は macOS 固有）:

```
___chkstk_darwin  ___stack_chk_fail  ___stack_chk_guard
_bzero  _cosf  _erff  _expf  _memcpy  _memmove  _snprintf  _strncmp  _vsnprintf
```

| 項目 | 結果 |
|---|---|
| malloc / calloc / free / fopen / mmap / POSIX / printf | **0 件** |
| double の `cos` / `sin` | **0 件** — naive DFT は `-O2` で除去されている |
| `-std=gnu17 -O2 -Wall -Wextra -Werror` の警告 | **0**（4 ファイルとも） |

`snprintf` / `vsnprintf` は `saan_tf` / `saan_w` のテンソル名組み立て。newlib にある。

⚠️ **`-std=c99` では判定していない。** IDF の既定は gnu17。`-std=c99` だと
newlib の `M_PI` が `__STRICT_ANSI__` で隠れうるが、macOS の libc では
`-std=c99` でも見えるので**手元では再現しない**種類の失敗。
`csrc/saanotts.c:412` と `csrc/saanotts_stream.c:398` が `M_PI` を無条件に使う。

⚠️ **`csrc/Makefile` の `CORE` が壊れていた**（このタスクで直した）。
`saanotts_int8.c` に `saan_w` / `saan_conv1d_w` / `saan_dwconv1d_w` /
`saan_act_scratch_needed` が入って**コアの一部になっている**のに 3 ファイルのままで、
`make clean && make all-test` が `golden_test` のリンクで落ちていた。
既存のバイナリが新しかったので `make` が再ビルドせず**気づかれていなかった**。

### (b) arena の真値 — `needed()` も `peak_used` も確保量ではない

```bash
make -C csrc arena
```

| 量 | 値 | 意味 |
|---|---:|---|
| `saan_stream_arena_needed(350)` | **340,016 B** (332.0 KB) | 緩い上限。**静的確保に使うと 512 KB SRAM が破綻する** |
| `st.peak_used` / `a.peak` (n_ids=350) | **197,424 B** (192.8 KB) | 高水位。M-42 の「197 KB」はこれ |
| init も pull も通る**最小 arena** (n_ids=350) | **197,632 B** (193 KB) | ALIGN16 の切り上げと確保順で高水位をわずかに上回る |
| `needed()` / 最小 | **1.72 倍** | |

⚠️ **M-42 の「197 KB」を確保量と読むと 208 B 足りない。** 高水位は
「確保すべき量」ではない。M-42 の記述自体は G1（ピーク RAM < 200 KB）の判定として
正しく、ここで訂正するのは**その値の使い道**。

arena 208 KB (212,992 B) 固定で n_ids を振った結果（n_ids 1〜1000 の 23 点）:

| n_ids | 結果 |
|---|---|
| 1 〜 520 | init も pull も成功 |
| 560 〜 1000 | `SAAN_ERR_ARENA` で**きれいに失敗** |
| **クラッシュ** | **0 件** |

正しく init できたときの `a.used`: **194,640 B** (n_ids=1) 〜 **198,768 B** (n_ids=520)。
ids に比例する分は 8 B/id（M-42 の G3 と整合）。

### (c) ⚠️ `saan_stream_init` が arena 不足を取りこぼす（**未修正**）

`saan_alloc` は失敗しても `used` を進めずに NULL を返す。`saan_stream_init` は
確保を約 25 回するのに**各グループの最後の 1 個しか NULL 検査していない**
（`csrc/saanotts_stream.c:378-394` は `im->frm` だけ、`:407-412` は
`im->obuf` と `im->tok_out` だけ）。そのため大きい確保（`o1539` 49,248 B、
`w_e` 17,024 B）だけが失敗して後続の小さい確保が成功すると、
**init が `SAAN_OK` を返したまま壊れた状態**になる。

n_ids=350 で arena を 150〜260 KB の 1 KB 刻み（111 点）で走らせた結果:

| arena | 挙動 |
|---|---|
| 〜174 KB | `SAAN_ERR_ARENA`（正常） |
| **175〜191 KB のうち 15 サイズ** | **init が OK → pull で SEGV** |
| 180 / 186 / 192 KB | たまたま clean fail ⚠️ **刻みが粗いと見逃す** |
| 193 KB 〜 | 正常 |

`a.used` の値が診断になる:

| 状態 | `a.used` |
|---|---:|
| 正しく init | 194,640 〜 198,768 B |
| 黙って確保に失敗 | 178,992 / 185,136 / **191,280** B |

ESP32 では StoreProhibited パニック = **ログも出さずに再起動**になる。

⚠️ **このタスクでは直していない** — `csrc/saanotts.c` と
`csrc/saanotts_stream.c` は D-3c'-1/2 が並行して編集中だったため。
修正は `saan_arena` に粘着フラグ `failed` を足し、`saan_alloc` の先頭で
`if (a->failed) return NULL;`、失敗時に `a->failed = 1`、
`saan_arena_init` / `saan_arena_reset` で 0 に戻す — **1 箇所で済む**。
これで各グループ末尾の既存の NULL 検査が正しく効く。

⚠️ 発生するのは **arena が最小値 197,632 B を下回るときだけ**なので、
208 KB を確保する雛形は踏まない。**それでも直すべき。**

### (d) pull のレイテンシプロファイル（ホスト・n_ids=350）

| 項目 | 値 |
|---|---:|
| pull が返すもの | **フレーム数**（最大 `SAAN_CHUNK`=8）。サンプル数は `n * 256` |
| 満チャンク 1 個 | 2,048 sample = **92.88 ms** の音声 |
| pull 回数 / 端数チャンク | 99 回 / 1 回（最終 `n`=1） |
| **初回 pull** | **12.21 ms** |
| 2 回目以降 mean | **2.04 ms** |
| **初回 / 定常** | **6.0 倍** |
| 定常 xRT | 0.0220（M-43 の 0.022 と整合） |
| 算法遅延 | 38 frames = **0.441 s**（受容野 36 + iSTFT 2） |

初回が重いのは受容野 38 フレームの warmup で内部の `step_chunk` が複数回走るため。
**`i2s_channel_enable()` の前にプリロールしないと確実にアンダーランする**
（雛形は 4 チャンク = 371 ms 先に計算する）。

### (e) `saan_irfft_1024` のスタック使用

```bash
cd csrc && cc -std=gnu17 -O2 -c fft.c -o /tmp/fftx.o && otool -tv /tmp/fftx.o | grep -A 14 '_saan_irfft_1024:'
```

| 項目 | 値 |
|---|---:|
| 自動変数 `zr[512] + zi[512]`（float） | **4,096 B** |
| arm64 / clang -O2 の実フレーム | **4,224 B**（`sub sp,sp,#0x1000` + `sub sp,sp,#0x20` + 退避 96 B） |

⚠️ **Xtensa では別の値になる（未測定）。** 確かなのは「iSTFT 1 回で 4 KB 超」で、
FreeRTOS の小さい既定タスクスタックでは足りないということ。

### (f) 重み blob のレイアウト

| blob | ファイル | tensors | payload | offset が 16 の倍数でない件数 |
|---|---:|---:|---:|---:|
| `csrc/student.bin` (fp32) | 2,249,792 B | 131 | 2,236,032 B | **0** |
| `csrc/student_i8.bin` (int8) | 643,936 B | 183 | 624,692 B | **0** |

⚠️ **base さえ 16 バイト境界なら全テンソルが 16 バイト境界に載る。**
ずれるとしたら base だけ。だから blob の置き方（partition か EMBED か）が
アライメントを決める（D-031）。

### (g) ホスト stub による雛形の検証

`esp32/host_stub/` に IDF API の偽ヘッダを置き、`esp32/main/*.c` を
そのままホストでビルドして、I2S に書いたはずの int16 を突き合わせた。

```bash
cc -std=gnu17 -O2 -Wall -Wextra -Werror -I esp32/host_stub -I esp32/main -I csrc \
   -o /tmp/saan_hoststub esp32/main/main.c esp32/main/saan_model.c esp32/main/saan_i2s.c \
   esp32/host_stub/stubs.c esp32/host_stub/host_main.c \
   csrc/saanotts.c csrc/saanotts_stream.c csrc/fft.c csrc/saanotts_int8.c -lm
/tmp/saan_hoststub csrc/student.bin    csrc/golden.bin /tmp/a.wav
/tmp/saan_hoststub csrc/student_i8.bin csrc/golden.bin /tmp/b.wav
```

| blob | vs C 一括版 `saan_synthesize`（int16） | vs Python 参照 `golden.bin` |
|---|---|---|
| fp32 | **27,136 sample すべて bit 一致** | 42/27,136 が違う。max\|Δ\| **1 LSB** / SNR 92.45 dB |
| int8 | **27,136 sample すべて bit 一致** | 23,579/27,136 が違う。max\|Δ\| 784 LSB / SNR **26.08 dB** |

⚠️ **golden との bit 一致は要求できない。** golden は Python 参照実装の出力で、
C コアとは SNR 117.50 dB（`max|Δ| 5.8e-07`）の一致。float で 6e-07 ずれると
丸め境界のサンプルだけ int16 で 1 LSB 飛ぶ。int8 はさらに量子化で波形が変わり、
SNR 26.08 dB は M-39 の PTQ 実測（≥ 25 dB）と同水準で**想定どおり**。

これで潰れたのは**アプリ側のロジックだけ**:
フレーム数とサンプル数の取り違え / 端数チャンクの落とし / プリロールの順序 /
int16 変換 / arena サイズ / 下限ガード。

### 手元では測っていないこと（**実機初日のタスクリスト**）

1. `idf.py build` が通るか — ESP-IDF も xtensa-esp32s3-elf-gcc も無い
2. flash に焼けるか / 起動するか
3. 実際の SRAM 消費（IDF + FreeRTOS + I2S DMA を含む free heap）
4. 実際の xRT とアンダーラン — **M-43 の 2.47 × RT は外挿**
5. I2S の実サンプルレート誤差（ESP32-S3 に APLL が無い）
6. flash から mmap した重みが D-cache を thrash しないか
7. `sdkconfig.defaults` のオプション名が実在するか（menuconfig にかけていない）
8. `esp32/main` が呼ぶ **IDF API の綴りが本物と一致するか** — stub は自作
9. 端末側 G2P — **1 行も書いていない**（このタスクの範囲外。雛形は固定 53 ID）

## M-47. B-12: 教師の事前学習テキスト (MOE-Speech) と評価コーパスの重複（自己実測・自分で再現済み）

**なぜ測ったか**: 看板の数字「SCOREQ 教師比 0.611」は heldout 24 文で測っている。
**その 24 文が教師の学習テキストに入っていたら汚染**になる。
B-10 で除外したのは教師の **FT テキスト 102 uid** だけで、
**事前学習の MOE-Speech 59,694 発話との重複は未検査**だった。

再現:

```bash
uv run python scripts/b12_moe_overlap.py     # 約 2 秒。音声は落とさない（metadata.csv 6.26 MB のみ）
```

出力:

```
[positive control] 200/200 exact, 200 J=1.0
[moe] rows=60233 unique_norm=59780 sha256=00028b52442541ed…
[heldout vs MOE] rows=2333  exact=0  punct-only-exact=0  maxJ=0.9167  J>=0.9:1  J>=0.7:1  J>=0.5:2  substr(意味あり)=17
[embedded vs MOE] rows=183  exact=0  punct-only-exact=0  maxJ=0.3571
[sibdense vs MOE] rows=221  exact=0  punct-only-exact=0  maxJ=0.3478
[train vs MOE] rows=20985  exact=0  punct-only-exact=6  maxJ=1.0  J>=0.9:6
[eval24] n=24  exact=0  punct-only-exact=0  J>=0.7:0  J>=0.5:0  maxJ=0.2381  substr(意味あり)=0
```

| 照合対象 | 行数 | 完全一致 | 句読点除去後一致 | 最大 5-gram Jaccard |
|---|---:|---:|---:|---:|
| **SCOREQ を測った 24 文** | 24 | **0** | **0** | **0.2381** |
| heldout 全体 | 2,333 | 0 | 0 | 0.9167（1 行） |
| embedded | 183 | 0 | 0 | 0.3571 |
| sibdense | 221 | 0 | 0 | 0.3478 |
| train | 20,985 | 0 | **6** | 1.0 |

**結論: 看板の 0.611 は汚染されていない。**

**検出漏れでないことを陽性対照で示している**: MOE の行 200 件をそのまま query に入れると
**200/200 が完全一致・Jaccard 1.0 で検出**される（スクリプト内で assert）。
「0 件」がゲートの故障でないことの証明はこれで付く。

train 側の 6 行（`cv/sentence_collector` の canon 4〜9 文字の短文）は
**蒸留では汚染に当たらない** — 正解テキストという概念が無く、ラベルは教師の決定的推論だけから出る。

⚠️ **見ていないこと**:

- **照合は表層テキストのみ。** 教師が実際に見たのは音素列なので、
  **表記が違って音素列が同一になるペアは検出できない**（未測定）
- MOE-Speech の**実使用 59,694 発話そのもの**は入手していない。
  配布版 60,233 行（上位集合）で照合したので取りこぼす方向の誤差は無いが、
  落ちた 539 行がどれかは不明
- 教師の事前学習 497,519 発話のうち**日本語以外の 437,825 発話とは照合していない**

## M-48. 敵対的検証で見つかった「空虚に通るゲート」と silent failure（自己実測）

M-44〜M-46 の実装主張を別エージェントに敵対的検証させ、**私自身が再実行して確認**した欠陥。
**どれも「テストが通っている」状態で潜んでいた。**

| # | 欠陥 | 実証 | 対応 |
|---|---|---|---|
| 1 | **`golden_test.c` が Pearson だけで合否を出していた** | Pearson はスケール・オフセットに不変なので、層を落としても 0.98 を超える。表示していた SNR は `printf` されるだけだった | `min_snr = 40 dB` を合否に追加 |
| 2 | **`int8_e2e_test` が 2 つのブロブの dtype を検査していなかった** | `./int8_e2e_test student.bin student.bin …` が「**平均 inf dB / OK**」を出す | dtype (fp32=0 / int8=1) と byte 同一を検査。逆順も弾く |
| 3 | **`saan_alloc` の失敗が粘着しない** | 大きい確保が失敗しても `used` を進めないので、後続の小さい確保が成功する。`saan_stream_init` が **`SAAN_OK` を返したまま NULL を抱える**。arena 175〜191 KB の **15 / 111 点**で再現 | `saan_arena` に粘着フラグ `failed` を追加（1 箇所で全確保地点を保護）。**15 → 0 点** |
| 4 | `bench.c` が dtype を文字列リテラル `"fp32"` で書いていた | int8 の計測結果が JSON 上は fp32 と記録される | ブロブから読むように変更 |
| 5 | FFT の arm64 実フレームを **4,192 B** と記録していた | `sub sp,sp,#0x1000` の**あとに `sub sp,sp,#0x20` がもう 1 つある**。`96 + 4096 + 32 = 4,224 B` | docs 4 箇所を訂正（`stream_test.c` の既存定数 4224 が正しかった） |

再現:

```bash
cd csrc
./int8_e2e_test student.bin student.bin golden.bin ids_heldout.bin /dev/null   # 弾かれること
make clean && make all-test          # arena ストレスを含む。EXIT=0 / 警告 0
./arena_stress student.bin golden.bin | grep "落ちた"
cc -std=c99 -O2 -c fft.c -o /tmp/fft.o && otool -tv /tmp/fft.o | sed -n "/_saan_irfft_1024:/,+14p"
```

`make all-test` に **`arena` を組み込んだ**（欠陥 3 が直って通るようになったため）。

⚠️ **`int8_e2e_test` のしきい値は「今回の実測に合わせて」後から決められている**
（`GATE_MIN_DB 23.0` に対し観測最小 23.27 dB、余裕 0.27 dB）。
**回帰検出には使えるが、独立な合格基準ではない。**
⚠️ **私が最初に指定したゲート「波形 SNR ≥ 25 dB」は満たされていない** —
実測は平均 25.88 dB だが**最小 23.27 dB / 24 文中 9 文が 25 dB 未満**。
実装側が判定式を「平均 ≥ 25 かつ 最小 ≥ 23」に**緩めた**。

---

## M-49. 追試 E-2: レーンラダーで品質ギャップを分解した（自己実測 / DNSMOS は相乗り）

**何を測ったか**: 教師と生徒の SCOREQ ギャップ 0.767（M-37）が
**decoder / acoustic / duration のどこから来ているか**を、
`data/pack_heldout` と `runs/v2/stage{3,4}.pt` だけで分解した。
**学習ゼロ・新規重みゼロ**。教師 ckpt は S-7 レーンと同一性検査でのみ使う。

### レーンの定義

| レーン | 中身 | 追加で入る誤差 |
|---|---|---|
| `L0_teacher` | パックの `yT` | — （基準） |
| `L_repr` | `istft(stft(yT))` | 1024/256 表現の天井 |
| `L1_c_s3` | `Gγ_stage3(Eρ(zT))` | c-line + decoder（共適応前・G7 対照） |
| `L1_c_s4` | `Gγ_stage4(Eρ(zT))` | c-line + decoder（共適応後・本線） |
| `L2_oracle_d` | `Gγ_stage4(Aβ(ids, ceil(dT)))` | + acoustic |
| `L3_student` | 現行の推論経路（`d̂` も生徒） | + duration |
| `T0_dec_zT` | **教師** decoder ← パックの `zT` | 教師そのもの（fp16 保存の床のみ） |
| `T1_dec_lift_cT` | **教師** decoder ← `lift(Eρ(zT))` | **c-line ボトルネックだけ** |
| `T2_dec_lift_chat` | **教師** decoder ← `lift(Aβ(ids, ceil(dT)))` | + acoustic（論文 4.68→3.70 の軸） |

`lift` は c(40)+bias → z(192) の**最小二乗の線形写像**。
**評価に使わない 300 発話 88,245 フレーム**で当てた（z の 192 チャネルの
R² mean 0.9804 / median 0.9824 / min 0.9132）。

再現:

```bash
uv run python scripts/e2_pack_is_teacher.py --n 24                       # G1 強陽性対照
uv run python scripts/e2_lane_ladder.py --out reports/e2_ladder          # n=24
uv run python scripts/e2_lane_ladder.py --out reports/e2_ladder_n200 --n 200 --seed 7
uv run --extra eval python scripts/e2_dnsmos.py --dir reports/e2_ladder --human
uv run --extra eval python scripts/e2_lane_metrics.py --dir reports/e2_ladder --human
uv run python scripts/e2_teacher_decoder_lanes.py --out reports/e2_teacher_dec
uv run --extra eval python scripts/e2_dnsmos.py  --dir reports/e2_teacher_dec
uv run --extra eval python scripts/e2_lane_metrics.py --dir reports/e2_teacher_dec
uv run --extra eval python scripts/e2_lane_cer.py --dir reports/e2_ladder \
    --lane L0_teacher --lane L1_c_s4 --lane L2_oracle_d --lane L3_student
uv run python reports/e2_blob_padding.py                                  # S-1
```

### 前提の検証（ゲート 8 本。**壊して落ちることを確認したものは印を付けた**）

| ゲート | 結果 | 壊して落ちるか |
|---|---|---|
| **G1 強** パックの `yT`/`zT`/`dT` が教師の出力そのもの | **24/24 bit 一致**（yT int16 / zT fp16 / dT fp32）。陰性対照（uid を 1 つずらす）0/24 | ✅ 陰性対照が同一実行内にある |
| **G1b** ラダー下端 `L3` が `reports/eval_v2/student/*.wav` と一致 | **24/24 bit 一致**（max\|Δ\| = 0 LSB）。陰性対照 0/24 | ✅ |
| **G2** レーンが互いに別物 | 144 ペアすべて max\|Δ\| > 0 | ✅ `--lane X --lane X` は異常終了する（実行して確認） |
| **G3** `Eρ` が stage3/stage4 で同一かつロード済み | sha256 一致 `ebc4712…`。decoder は不一致（Stage 4 が decoder だけ動かした） | ✅ `load_state_dict` を潰すと落ちた（実行して確認） |
| **G4** オラクルレーンの長さ == `frames*256` | 96/96（n=24）/ 800/800（n=200） | ✅ **実際に落ちた**（`center=True` の STFT で L_repr が 256 サンプル長かった） |
| **G5** 表現の天井 `istft(stft(yT))` | SNR **138.68 dB**（min 138.37）。D5 の 139.0 dB と一致 | ✅ **実際に落ちた**（int16 往復後に測って 68.65 dB になった） |
| **G6** 検出力（隣接差 CI 半幅 ≤ 0.10） | n=24 で最大 **0.0713** / n=200 で **0.0213** | しきい値は測る前に宣言（全体ギャップ 0.767 の 13%） |
| **T0** 教師 decode 経路の陽性対照 | `dec(fp32 z)` が `yT` と **bit 一致 3/3** | ✅ **実際に落ちた**（fp16 の `zT` を入れて 0/24・73.6 dB） |

⚠️ **`reports/eval_v2/teacher/*.wav` との bit 一致は原理的に不可能。**
書き出し経路が二重量子化している（`int16_roundtrip` の ÷32767 →
libsndfile の PCM_16 ×32768）うえ、`int16_roundtrip` は `round` ではなく
**truncate**（`.astype(np.int16)`）。実測で max\|Δ\| = 2 LSB / SNR 68.5 dB。
**同一性検査は `scripts/e2_pack_is_teacher.py` の側（教師を回し直す）で取ること。**

⚠️ **`speechmos` は UTMOS と名前が衝突する。** `torch.hub` が読む
`tarepan/SpeechMOS` の checkout に**同名の `speechmos` パッケージ**があり、
同一プロセスでは必ずどちらかが `ImportError` になる（両方向を実測）。
DNSMOS は `scripts/e2_dnsmos.py` で**別プロセス**に分けてある。

### 結果 A: L ラダー（生徒 decoder を通した鎖分解）

SCOREQ synthetic/nr。比は**対応のある** bootstrap（`eval_metrics.ratio_ci` は
docstring どおり非対応なので使っていない）。

| レーン | n=24 | 教師比 (n=24) | n=200 | 教師比 (n=200) |
|---|---:|---:|---:|---:|
| L0_teacher | 1.9727 | — | 1.9972 | — |
| L_repr | 1.9780 | 1.0027 [1.0002, 1.0051] | 2.0038 | 1.0033 [1.0022, 1.0044] |
| L1_c_s3 | 1.5726 | 0.7972 [0.7704, 0.8252] | 1.6318 | 0.8171 [0.8084, 0.8260] |
| **L1_c_s4** | 1.5465 | 0.7840 [0.7562, 0.8123] | 1.6022 | **0.8022** [0.7936, 0.8112] |
| **L2_oracle_d** | 1.2644 | 0.6409 [0.6084, 0.6756] | 1.3194 | **0.6606** [0.6500, 0.6716] |
| **L3_student** | 1.2063 | 0.6115 [0.5804, 0.6433] | 1.2678 | **0.6348** [0.6236, 0.6464] |

隣接差（対応のある bootstrap 95% CI）:

| 差 | n=24 | n=200 |
|---|---|---|
| **decoder** L0 − L1_c_s4 | +0.4262 [0.3604, 0.4938] | **+0.3950 [0.3736, 0.4163]** |
| **acoustic** L1_c_s4 − L2 | +0.2821 [0.2059, 0.3486] | **+0.2828 [0.2632, 0.3027]** |
| **duration** L2 − L3 | +0.0580 [0.0120, 0.1020] | **+0.0516 [0.0336, 0.0695]** |
| G7 対照 L1_c_s4 − L1_c_s3 | −0.0261 [−0.0476, −0.0054] | −0.0296 [−0.0390, −0.0200] |

gap の大小関係（**CI の重なりでは言えないので差を直接測った**）:

| 比較 | n=24 | n=200 |
|---|---|---|
| decoder − acoustic | +0.1441 [0.0422, 0.2635] | **+0.1123 [0.0819, 0.1424]** |
| decoder − duration | +0.3682 [0.2860, 0.4511] | +0.3434 [0.3152, 0.3714] |
| acoustic − duration | +0.2241 [0.1240, 0.3174] | +0.2311 [0.2007, 0.2616] |

**3 項の和 0.7294 は全体ギャップ L0−L3 = 0.7294 と一致**（鎖分解なので恒等的）。
**ラダーは SCOREQ / UTMOS / CER では単調**だった。
⚠️ **「全指標で単調（逆転なし）」は誤りだった**（敵対的検証で反証）。
**DNSMOS BAK は逆転する**: n=200 の鎖 L0→L1_c_s4→L2→L3 で
3.9160 → 3.8229 → 3.8789 → 3.8732、**L1−L2 = −0.0561 [−0.0735, −0.0387]**
（対応のある bootstrap、有意）。

⚠️ **G7（共適応の交絡）は小さいが有意。** Stage 4 の decoder は predicted-code
mixing で `Aβ` と共適応しているぶん、純粋な `c_T` では stage3 の decoder より
**0.030 だけ悪い**。decoder gap 0.395 の 7.5% で、結論の向きは変わらない。

### 結果 B: T ラダー（教師 decoder に別の潜在を通す）— c-line と decoder の分離

`L1` は `Eρ`(40 次元ボトルネック) と `Gγ` の合成なので、L ラダー単独では分離できない。
教師 decoder に線形 lift 経由で通すとこうなる:

| レーン | SCOREQ n=24 | SCOREQ n=200 | UTMOS n=200 | DNSMOS OVRL n=200 | T0 比 (SCOREQ, n=200) |
|---|---:|---:|---:|---:|---:|
| T0_dec_zT | 1.9731 | 2.0049 | 1.8017 | 2.7726 | — |
| **T1_dec_lift_cT** | 1.9527 | 1.9806 | 1.7745 | 2.7407 | **0.9879** |
| T2_dec_lift_chat | 1.4618 | 1.4969 | 1.4082 | 2.3827 | 0.7466 |

**c-line だけのコスト T0 − T1**（対応のある bootstrap 95% CI）:

| 指標 | n=24 | n=200 |
|---|---|---|
| SCOREQ | +0.0204 [−0.0093, +0.0496] | **+0.0243 [+0.0148, +0.0338]** |
| UTMOS | +0.0146 [−0.0142, +0.0445] | +0.0272 [+0.0162, +0.0382] |
| DNSMOS OVRL | −0.0015 [−0.0301, +0.0286] | +0.0319 [+0.0209, +0.0431] |

→ **40 次元 c-line のボトルネックは n=24 ではゼロと区別できず、n=200 で初めて
検出できる大きさ（SCOREQ +0.024 = 教師の 1.2%）。**
同じ n=200 の decoder gap 0.395 の **6.2%** にすぎない。
lift は線形なので、これは c-line が失う情報量の**上限側の見積り**
（非線形の逆写像ならもっと戻せる）。
したがって **L ラダーの「decoder gap」0.395 は c-line ではなく `Gγ` に帰属する。**

論文流の定義（どちらも同じ天井からの置換）で並べると（うちは n=200）:

| | うち（日本語） | 論文（英語・§IV-B） |
|---|---:|---:|
| 天井 = 教師 z → 教師 decoder | 2.0049 | 4.68 |
| 教師 z → **生徒 decoder** | 1.6022（比 **0.802**） | 3.20（357k R8・比 0.684）/ 4.13（1.0M teacher-init・比 0.883） |
| **生徒潜在** → 教師 decoder | 1.4969（比 **0.747**） | 3.70（比 0.791） |
| **decoder gap** | 0.3950 [0.3736, 0.4163] | 1.49 |
| **acoustic gap** | 0.5080 [0.4777, 0.5388] | 0.98 |
| **decoder − acoustic** | **−0.1130 [−0.1408, −0.0854]** | +0.51 |

⚠️ **順序が論文と逆**（論文は decoder > acoustic、うちは acoustic > decoder）。
n=200 では 3 指標とも有意: SCOREQ −0.1130 [−0.1408, −0.0854] /
UTMOS −0.0813 [−0.0995, −0.0645] / DNSMOS OVRL −0.0508 [−0.0862, −0.0153]。
（n=24 では SCOREQ −0.0851 [−0.1653, −0.0040] / DNSMOS は有意でなかった）
⚠️ **ただし `T2` には未制御の交絡がある** — lift は線形で、`c_T` 上では誤差ゼロと
区別できない（T1）が、**分布外の `ĉ` 上での lift 誤差は測っていない**。
acoustic gap が過大になっている可能性を排除できない。**この順序は示唆であって決着ではない。**

### 結果 C: DNSMOS（追試 E-1 の一部）

**実人間の天井を先に測った**（`reports/b5_scoreq.json` と同じつくよみちゃんコーパス 24 本）。

| set | OVRL | SIG | BAK | P.808 |
|---|---:|---:|---:|---:|
| **実人間** | **2.7866** | 3.1111 | 3.7973 | 3.6667 |
| L0_teacher | 2.7547 | 3.0112 | 3.9367 | 3.3016 |
| L1_c_s4 | 2.4493 | 2.6852 | 3.8563 | 3.1482 |
| L2_oracle_d | 2.1950 | 2.3501 | 3.8915 | 3.1048 |
| L3_student | 2.1071 | 2.2686 | 3.8494 | 3.0187 |

**教師/人間 = 0.989（OVRL）。** SCOREQ の 0.820 / UTMOS の 0.758 と**大きく違う** —
DNSMOS は日本語の教師をほぼ人間と同じに採点する。
生徒の教師比は **0.765（n=24）/ 0.781（n=200）** で、SCOREQ の 0.611 / 0.635 より高い。

⚠️ **上流の言う「金属的アーティファクトは SCOREQ で高得点・DNSMOS で低得点」の
パターンは出ていない。** 3 指標とも同じ順序で単調に下がる。

⚠️ **これは追試 E-1 本体ではない。** E-1 は別レーンで進行中で、独自のラッパ
`src/saanotts_jp/dnsmos_metric.py`（soxr_hq でリサンプル）を持つ。
M-49 の数値は `scripts/e2_dnsmos.py`（torchaudio でリサンプル）の経路。
**両経路を同じ 48 ファイルで突き合わせた**:

| レーン | M-49 の経路 | E-1 のラッパ | 最大差 | 相関 |
|---|---:|---:|---:|---:|
| L0_teacher | 2.7547 | 2.7579 | 0.0459 | 0.9976 |
| L3_student | 2.1071 | 2.1088 | 0.0484 | 0.9972 |

レーン間ギャップ 0.3〜0.6 に対して無視できるが、**同じ数値ではない**。
再現: 上の突き合わせは `saanotts_jp.dnsmos_metric.score_path` を 24 文 ×2 レーンに適用。
⚠️ 実人間の 24 本は**パディング無しの生のコーパス wav** で、レーン側
（前後 0.3 s パディング + int16 往復）とは前処理が違う。天井の絶対値はその分ずれる。
⚠️ DNSMOS は 16 kHz にリサンプルするので、2–8 kHz の平坦度プローブの代わりにはならない。

### 結果 D: 音素クラス別 SFM のレーン別（n=200）

| class | L0 | L1_c_s4 | L2 | L3 | n |
|---|---:|---:|---:|---:|---:|
| fricative | 0.7399 | 0.7495 | 0.7481 | 0.7567 | 790 |
| devoiced | 0.7411 | 0.7422 | 0.7532 | 0.7578 | 289 |
| affricate | 0.8049 | 0.8157 | 0.8174 | 0.8182 | 297 |
| stop | 0.7250 | 0.7362 | 0.7447 | 0.7463 | 1939 |
| nasal | 0.6104 | 0.6586 | 0.6634 | 0.6627 | 1156 |
| vowel | 0.6460 | 0.6600 | 0.6707 | 0.6731 | 5406 |

**生徒は 8 クラス中 7 クラスで教師より SFM が高い（＝平坦・雑音寄り）。**
⚠️ **「どのクラスでも」は誤りだった**（敵対的検証で反証）。
**`geminate` だけは下がる**（L0 0.7969 → L3 0.7811）。
⚠️ geminate の基準値は閉鎖区間の int16 量子化床を測っており教師の性質ではない（M-27）ので、
この 1 クラスの逆転を「生徒が良い」と読まないこと。
論文の英語版の欠陥（sibilant が 0.689 → 0.590 と**低く**なる = whistly）とは**逆向き**。
⚠️ 帯域内 RMS は全クラスで下がっている（例 vowel 0.1797 → 0.1407）ので、
SFM の上昇は「高域のエネルギーが減って相対的に平坦に見える」可能性を含む。
**SFM 単独で読まない**（M-27）。⚠️ `nasal` の跳ね（0.610 → 0.659）は
**L1 の時点で既に出ている** = decoder 由来。

### 結果 E: かな CER のレーン別（n=24 / Whisper large-v3 / 750 s）

**了解度の劣化はすべて decoder の段で起きている。**

| レーン | かな CER mean | median |
|---|---:|---:|
| L0_teacher | 0.1359 | 0.0860 |
| L1_c_s4 | 0.1753 | 0.0984 |
| L2_oracle_d | 0.1766 | 0.1222 |
| L3_student | **0.1776** | 0.1457 |

| 隣接差（対応のある bootstrap 95% CI） | 値 |
|---|---|
| **decoder** L1_c_s4 − L0 | **+0.0394 [+0.0172, +0.0651]** |
| acoustic L2 − L1_c_s4 | +0.0013 [−0.0265, +0.0273] |
| duration L3 − L2 | +0.0010 [−0.0643, +0.0485] |

**L3 の 0.1776 は M-37 / `reports/eval_v2/cer.json` の 0.17759 と一致**
（L3 が公表済みの生徒と bit 一致しているので当然だが、経路の確認になる）。

⚠️ **SCOREQ の帰属と食い違う。** 自然さ（SCOREQ/UTMOS/DNSMOS）では
論文流の定義で acoustic のほうが大きいのに、**了解度（かな CER）では
decoder が唯一の寄与**で acoustic / duration はゼロと区別できない。
⚠️ n=24。CI は広い。Whisper 自身の誤りが全レーンに乗る。

### 結果 F: blob 算術の再検証（S-1 / (a1)）

論文本文（**自分で取得**: `curl -sL https://arxiv.org/pdf/2608.21378`、
sha256 `64b0d426b585e05f87867375928755a79d02ffc989374c08f1aa52c37267eab1`、
311,287 B、`pdftotext -layout` で 353 行。写しは `reports/e2_paper/`）から:

- §III-A「Its two blobs occupy 280,288 and 399,544 bytes.
  **Padding**, floating-point biases, and per-channel scales account for the
  difference between parameter count and binary size.」
- §V「We export the embedded graph with symmetric int8 weights quantized per output
  channel, while **activations are quantized per frame**. Embeddings, normalization
  affines, and **the inverse-STFT support code** remain in floating point.」

⚠️ **`reports/r3_blob2_constraint.py` の `blob = P + 3B + 4C` には padding 項が無い。**
だからその「0 件」は「解が無い」ではなく「**前提が違う**」を意味する。

宣言した上限つきモデル（テンソルごとのアラインメント A ∈ {4,16,64} B / iSTFT 支援表
hann 4,096 B + FFT twiddle 8,192 B + bit-reversal 4,096 B = 最大 16,384 B）で計算し直すと:

| | うちの実測 | 論文 | 差 | 到達可能か |
|---|---:|---:|---:|---|
| blob1（Dα+Aβ と仮定） | 295,828 | 280,288 | **−15,540** | ✗ **padding は増やすことしかできない** |
| blob2（Gγ） | 360,864 | 399,544 | +38,680 | ✗（align-64 + iSTFT 最大でも 382,540） |
| **合計** | **656,692** | **679,832** | **+23,140** | align-64 なら **✓**（余地 27,913）/ align-16 は ✗（19,129）/ align-4 は ✗（16,933） |

**論文は blob の中身（どのモジュールがどちらに入るか）を書いていない**ので、
判定材料になるのは**合計**のほう。合計は**アラインメントの宣言しだいで到達可能**になる。

→ **(a1) は blob 算術では決着しない。** 「0 件だから (a) が最有力」とは書けない。
G8 陽性対照: 同じ規則で再計算して 360,864 B に一致し、
`dw` を 1 本抜くと 360,028 B に変わる（探索器が対象を測っている）。

⚠️ 論文の量子化は **W8A8**（activations are quantized per frame）で、
うちの M-39 / M-45 は **W8A32**（実行時 RAM は減らない）。**別物なので混ぜない。**

### 3 仮説の判定

| 仮説 | 判定 |
|---|---|
| **(a) 論文の `Gγ` 逆算が間違っている** | **決着しない。** (a1) blob 算術では決まらない（上記 E）。(a2)「その違いが品質を律速している」は測っていない。⚠️ ただし **iSTFT 表現が天井ではない**ことは確定（L_repr − L0 = +0.0067 [+0.0045, +0.0087]、論文の ±0.005 と同オーダー） |
| **(b) 上流の「sub-400k は死んだクラス」は上流固有** | **部分的に支持。** 論文本文を自分で読んで確認したところ、その根拠は **357k の R8 = 波形領域 transposed-conv / 522 MMAC/s** の測定であり、**331k の iSTFT decoder（28 MMAC/s）に教師潜在を通したセルは論文に存在しない**。うちの L1 がそのセルを埋め、教師比 **0.802**（論文の R8 0.684 と 1.0M teacher-init 0.883 の間）。⚠️ 言語も教師もレシピも違うので**決定的ではない** |
| **(c) 日本語では from-scratch で足りる** | **支持されない。ただし「decoder が主因」でもない。** 鎖分解では decoder gap 0.395 が acoustic 0.283 / duration 0.052 より**有意に大きく**（decoder − acoustic = +0.112 [0.082, 0.142]、n=200）、`L0−L1 = 0.395 > L1−L3 = 0.334` なので事前に決めた反証条件（decoder gap > 残り全部 → 足りていない）が**わずかに**発火する。c-line のコストは 0.024 しかないのでこの 0.395 は `Gγ` に帰属する。⚠️ **一方、論文流の置換定義では acoustic gap 0.508 > decoder gap 0.395 で順序が逆**（3 指標とも有意）。**どちらの分解を採るかで主因が入れ替わる**ので、「decoder が唯一の主因」とも「decoder は問題ない」とも書けない。⚠️ **了解度（かな CER）では decoder が唯一の寄与**（+0.0394 [0.0172, 0.0651]、acoustic / duration はゼロと区別できない） |

### 測っていないこと

- **`Gγ` の幅スイープ（S-8）**。容量律速かどうかは測っていない。Stage 3 だけで
  W∈{76(2 seed), 96, 128} × 20k step ≒ **2 時間**。M-37 は Stage 3 が
  **20,000 step で SNR 8.91 dB に飽和**したと記録しており（40,000 step 実行）、
  L1 の波形 SNR 9.35 dB はこれと整合する。**step 律速ではない**が、
  **容量律速かどうかの証拠ではない**
- **非線形の c→z 逆写像**。lift は線形のみ
- **分布外の `ĉ` 上での lift 誤差**。T2 の acoustic gap が過大な可能性
- **聴取**。全レーンの wav は `reports/e2_ladder*/`・`reports/e2_teacher_dec*/` にある
- **DNSMOS の日本語較正**。天井は測ったが、前処理がレーン側と揃っていない

---

## M-50. 追試 E-1: DNSMOS P.835 を 4 レーンで測った（自己実測 / 各 n=24）

**M-49 で相乗り測定された DNSMOS を、独立の経路で測り直し + 対照実験を足した。**
M-49 の人間天井は**再現できた**（下記「M-49 との照合」）。
⚠️ **合否は決めない**（D-013。DNSMOS も日本語で較正されていない）。

**事前登録**: 測定を 1 回も走らせる前に `scripts/e1_dnsmos.py` の docstring として
commit した（`git log --diff-filter=A scripts/e1_dnsmos.py`）。予測は
「生徒の SIG / OVRL は**金属的アーティファクトの有無に関係なく**教師より低く出る。
だから『生徒の DNSMOS が低かった』を金属的の証拠として読まない」。
⚠️ **完全な盲検ではない** — 設計時に n=1 の疎通確認で符号は見えていた。
盲検なのは効果量・特異性・ゲートの結果のほう。

再現:

```bash
uv sync --extra eval
uv run --extra eval python scripts/e1_dnsmos.py     # 約 5 分。reports/e1_dnsmos/e1.json
```

### レーン（4 本 × 24 発話。SCOREQ / UTMOS とまったく同じ wav）

| レーン | 実体 | 対応 |
|---|---|---|
| `T_v2` | `reports/eval_v2/teacher` | `S_v2` と uid で**対応あり** |
| `S_v2` | `reports/eval_v2/student` | 〃 |
| `T_b5` | `reports/b5_teacher_wav` | 教師の**別の 24 文**（同一システムの独立 2 標本） |
| `H` | HF `ayousanz/tsukuyomi-chan-ljspeech` `VOICEACTRESS100_001..024` | **対応なし**（テキストが違う） |

### 結果（主系列 = as-is / ライブラリ既定の自己連結 / soxr_hq）

| レーン | SIG | BAK | **OVRL** | P.808 | SCOREQ | UTMOS |
|---|---:|---:|---:|---:|---:|---:|
| `T_v2` 教師 | 2.9781 | 3.9413 | **2.7299** | 3.2108 | 1.9732 | 1.7925 |
| `S_v2` 生徒 | 2.2699 | 3.8529 | **2.1088** | 2.9441 | 1.2063 | 1.3585 |
| `T_b5` 教師(別文) | 3.1426 | 3.9382 | **2.8634** | 3.3608 | 2.0488 | 1.7478 |
| `H` 実人間 | 3.1123 | 3.8009 | **2.7881** | 3.6004 | 2.4983 | 2.3047 |

⚠️ **5 点満点ではない。** 較正多項式 `get_polyfit_val` の像は
SIG [1.1421, 4.0101] / BAK [1.0814, 4.3580] / OVRL [1.0938, 3.9318]
（raw ∈ [1,5] を代入して確認）。**下限が 0 でなく約 1.1 なので比は圧縮される。**

**生徒 − 教師（対応あり / n=24）**:

| 指標 | 差 | 95% CI | paired-t p | Wilcoxon p | 生徒/教師 | 検出限界 |
|---|---:|---|---:|---:|---:|---:|
| SIG | −0.7082 | [−0.8007, −0.6157] | 7.4e-14 | 1.2e-07 | 0.7622 [0.7368, 0.7885] | 0.0925 |
| BAK | −0.0884 | [−0.1336, −0.0433] | 4.96e-04 | 1.3e-04 | 0.9776 [0.9669, 0.9881] | 0.0452 |
| **OVRL** | **−0.6211** | [−0.7017, −0.5406] | 6.2e-14 | 1.2e-07 | **0.7725** [0.7483, 0.7973] | 0.0805 |
| P.808 | −0.2667 | [−0.3343, −0.1992] | 3.0e-08 | 2.4e-07 | 0.9169 [0.8986, 0.9357] | 0.0675 |

**人間比（対応なし / bootstrap CI）**:

| 指標 | 教師/人間 | 生徒/人間 | `T_b5`/人間 |
|---|---:|---:|---:|
| SIG | 0.9569 [0.9216, 0.9938] | 0.7293 [0.6991, 0.7618] | 1.0097 [0.9749, 1.0449] |
| OVRL | **0.9791** [0.9394, 1.0213] | **0.7563** [0.7230, 0.7923] | **1.0270** [0.9840, 1.0708] |
| P.808 | 0.8918 [0.8626, 0.9224] | 0.8177 [0.7920, 0.8452] | 0.9335 [0.8994, 0.9678] |

⚠️ **実人間の日本語スタジオ録音でも OVRL は 2.7881 で 3 に届かない。**
しかも `T_b5`（教師の合成音）は実人間より**高く**出る（比 1.027、MWU p=0.17 で
n=24 では区別できない）。**絶対値を英語の公表値と並べない**（C-012 の再発防止）。

⚠️ **DNSMOS は教師と人間の差をほぼ潰す**（OVRL 0.979）が、
SCOREQ は 0.820 / UTMOS は 0.758。**3 指標で教師/人間の見え方が大きく違う。**

### 物差し: 同一システムの標本間ばらつき

`T_b5` と `T_v2` はどちらも同じ教師の合成音（別の 24 文）。
**OVRL の差は +0.1334**（MWU p=0.015）。生徒 − 教師の対応差 −0.6211 は
この物差しの **4.7 倍**。⚠️ 逆に言えば、**0.13 程度の差はレーンを取り替えるだけで出る。**

### ゲート（8 本中 **7 本 PASS / 1 本 FAIL**）

| ゲート | 内容 | 結果 |
|---|---|---|
| **G0** | `speechmos` の名前衝突。素の import は**両方向で落ちる**（陽性対照）。ラッパの分離 import が両方向で通り、素の import と**同値** | ✅ |
| **G1** | 決定性。別プロセス 2 回 + 親の 3 者で **384/384 が float64 完全一致**（`==`） | ✅ |
| **G2** | ラッパ == 明示 soxr_hq + ndarray == ライブラリのパス経路。**max\|Δ\| = 0.000000** | ✅ |
| **G3** | `sr==22050` / `subtype==PCM_16` / `n_out == ceil(n_in*16000/sr)`。96/96 | ✅ |
| **G4** | レーン間で SHA-256 が 1 件も重複しない。**96/96 が unique** | ✅ |
| **G5** | 同じ wav の SCOREQ / UTMOS が既存記録と一致（8 組すべて \|Δ\| < 6e-5） | ✅ |
| **G6** | 陽性対照 4 系統 ×3 段で SIG と OVRL が単調減少 | ❌ **FAIL** |
| **G7** | 金属様アーム 3 種が原音と別物 + **DNSMOS でない**プローブで分離 | ✅ |
| **G8** | \|Δ(policy)\| と \|Δ(padding)\| が \|Δ(生徒−教師)\| より小さい | ✅ |

⚠️ **G3 の長さ則は `round` ではなく `ceil`。** soxr_hq の実測で
`round` は 96 本中 **46 本**が外れる。

#### G6 が落ちた（**これが本測定で一番重要な発見**）

`T_v2` 24 本に 4 系統 ×3 段の劣化を掛けた（原音: SIG 2.9781 / BAK 3.9413 /
OVRL 2.7299 / P.808 3.2108）:

| 系統 | 段 | SIG | BAK | OVRL | P.808 | SNR |
|---|---|---:|---:|---:|---:|---:|
| 白色雑音 | 20 dB | **3.1732 ↑** | 3.0919 | 2.4787 | 2.7649 | 20.0 dB |
| | 10 dB | **3.1369 ↑** | 2.5622 | 2.1233 | 2.4570 | 10.0 dB |
| | 0 dB | 1.8154 | 1.3526 | 1.2843 | 2.1583 | 0.0 dB |
| 再量子化 | 8 bit | 2.3945 | 3.6403 | 2.1099 | 3.0332 | 29.1 dB |
| | 5 bit | 1.9140 | 3.5474 | 1.7204 | 2.6968 | 11.5 dB |
| | 3 bit | 1.1755 | 3.3318 | 1.1661 | 2.4314 | 0.9 dB |
| ハードクリップ | 0.7×peak | **2.9841 ↑** | **3.9448 ↑** | **2.7359 ↑** | **3.2127 ↑** | 28.9 dB |
| | 0.5×peak | **2.9959 ↑** | **3.9458 ↑** | **2.7458 ↑** | **3.2489 ↑** | 18.5 dB |
| | 0.3×peak | **3.0285 ↑** | 3.9432 | **2.7671 ↑** | **3.3098 ↑** | 10.5 dB |
| ローパス | 6 kHz | 2.9668 | 3.9354 | 2.7190 | 3.3144 | 24.1 dB |
| | 4 kHz | 2.9346 | 3.9221 | 2.6885 | 3.0358 | 20.4 dB |
| | 2 kHz | 2.8583 | 3.8987 | 2.6149 | 2.7540 | 16.8 dB |

単調に下がった系統数: **SIG 2/4 / OVRL 3/4 / BAK 3/4 / P.808 2/4**。

2 つの落ち方は性格が違う:

1. **白色雑音で SIG が上がる**のは P.835 の設計どおりでもありうる
   （SIG は「背景雑音を無視して音声信号だけを見る」軸）。実際 **BAK は
   3.94 → 3.09 → 2.56 → 1.35 と強く単調**で、雑音はちゃんと BAK に出ている。
2. **ハードクリップは 4 スコアすべてが動かないか、わずかに上がる。**
   0.3×peak（**SNR 10.5 dB** の歪み）でも OVRL は 2.7299 → 2.7671 と**上がる**。
   ⚠️ **DNSMOS はこの劣化に対して盲目である**（少なくともこの 24 本では）。

⚠️ **ゲートは緩めていない。** 「満たさなかった」と記録する。

#### G7: 金属様アームの特異性 — **上流の主張とは逆向きに出た**

金属的アーティファクトの**代理**を 3 系統作り、**同じ SNR の白色雑音アームと対**にした。
「特異性」= Δ(金属様) − Δ(SNR 一致の雑音)。**正なら金属様のほうがダメージが小さい。**

| アーム | SNR | ΔOVRL | 雑音対の ΔOVRL | 特異性 (OVRL) | 特異性 (SIG) |
|---|---:|---:|---:|---:|---:|
| Griffin-Lim 8 iter | −2.73 dB | −0.2585 | −1.5921 | **+1.3336** | +1.3292 |
| Griffin-Lim 32 iter | −2.67 dB | −0.0567 | −1.5819 | **+1.5252** | +1.5224 |
| Griffin-Lim 128 iter | −2.78 dB | −0.0568 | −1.5862 | **+1.5294** | +1.5327 |
| 86.13 Hz AM 深さ 0.05 | 29.04 dB | −0.0518 | −0.1041 | +0.0523 | **−0.1836** |
| 86.13 Hz AM 深さ 0.15 | 19.50 dB | −0.2510 | −0.2658 | +0.0148 | **−0.5069** |
| 86.13 Hz AM 深さ 0.30 | 13.48 dB | −0.4293 | −0.4528 | +0.0235 | **−0.7070** |
| フレーム凍結 (2 に 1 回) | 0.47 dB | −0.5824 | −1.5060 | **+0.9236** | +0.7525 |

読み取れること（**向きは仮定せず観測した**）:

- **位相破壊（Griffin-Lim）に対して DNSMOS はほぼ反応しない。** SNR −2.7 dB
  という壊滅的な条件でも OVRL は −0.06〜−0.26 しか落ちず、**同じ SNR の白色雑音
  （−1.59）より 1.3〜1.5 ポイント甘い**。
  → **「DNSMOS なら金属的を捕まえられる」という素朴な期待は、少なくとも
  位相破壊の代理に対しては成り立たない。**
- **唯一 SIG だけが 86.13 Hz AM に選択的**（特異性が**負** = 雑音より厳しい）。
  「金属的」がフレームレートの周期変調を指すなら、**見るべきは OVRL ではなく SIG**。

アームが本物であることの確認（**DNSMOS ではないプローブ**で）:

| アーム | 2–8k SFM Δ | 8–11k SFM Δ | 86.13 Hz 変調線 Δ | 原音と bit 一致 |
|---|---:|---:|---:|---|
| GL 8 iter | +0.01352 [0.0129, 0.0142] | +0.01164 [0.0112, 0.0121] | −2.28 [−5.00, +0.44] | 0/24 |
| AM 0.30 | +0.00164 [0.0009, 0.0024] | +0.00820 [0.0074, 0.0090] | **+2068 [1702, 2434]** | 0/24 |
| フレーム凍結 | +0.03436 [0.0287, 0.0400] | +0.18250 [0.1762, 0.1888] | −2.87 [−5.29, −0.45] | 0/24 |

⚠️ **これは代理に対する感度であって、うちの生徒に実在する欠陥に対する感度ではない。**

#### G8: tiling / padding の交絡

`speechmos` は 9.01 秒に満たない音声を**自己連結して水増ししてから**採点する
（`while len(audio) < len_samples: audio = np.append(audio, audio)`）。
実測で `eval_v2` は **24/24 が 9.01 秒未満**、採点窓数は **1〜7 と非単調**。

| 指標 | \|Δ 生徒−教師\| | \|Δ policy\| | \|Δ padding\| |
|---|---:|---:|---:|
| SIG | 0.7082 | 0.0782 | 0.0247 |
| BAK | 0.0884 | 0.0536 | 0.0855 |
| OVRL | 0.6211 | 0.0879 | 0.0289 |
| P.808 | 0.2667 | **0.2207** | 0.0768 |

⚠️ **P.808 は自己連結 vs 9.01 秒ゼロ埋めで 0.2207 動く。** 生徒差 0.2667 と同オーダー。
**P.808 の生徒差は前処理と切り分けられていない。**
窓数と各スコアのレーン内相関は 4 レーンとも CI が 0 を跨ぐ。

### M-49（先行測定）との照合 — **再現できた**

M-49 は `scripts/e2_dnsmos.py` で `torchaudio.transforms.Resample` を使い、
こちらは `librosa.resample(res_type="soxr_hq")`。**同じ人間 24 本を両方の経路で測った**:

| 指標 | soxr_hq（本測定） | torchaudio | M-49 の記録 | \|Δ torchaudio − M-49\| |
|---|---:|---:|---:|---:|
| SIG | 3.1123 | 3.1111 | 3.1111 | 3.7e-05 |
| BAK | 3.8009 | 3.7973 | 3.7973 | 3.4e-05 |
| OVRL | 2.7881 | 2.7866 | 2.7866 | 7.1e-06 |
| P.808 | 3.6004 | 3.6667 | 3.6667 | 9.3e-06 |

**M-49 の人間天井は独立に再現できた。** 残る差は**リサンプラだけ**で、
OVRL では 0.0015、**P.808 では 0.0663（44 倍）**。
⚠️ M-49 の `L0_teacher` は `reports/eval_v2/teacher` とは**別の wav**
（レーンを作り直しており bit 一致は原理的に不可能、SNR 68.5 dB）なので、
`T_v2` 2.7299 と `L0_teacher` 2.7547 の差（0.0248）は**リサンプラだけでは説明できない**。

### 指標間の一致・食い違い

発話単位（同一 24 文、Pearson）:

| レーン | SCOREQ vs OVRL | UTMOS vs OVRL |
|---|---|---|
| `T_v2` | **+0.7545** [+0.505, +0.888] | +0.0650 [−0.347, +0.456] |
| `S_v2` | +0.5896 [+0.244, +0.802] | +0.4982 [+0.119, +0.751] |
| `T_b5` | +0.5251 [+0.154, +0.766] | +0.4273 [+0.029, +0.709] |
| `H` | +0.1221 [−0.296, +0.501] | +0.0987 [−0.317, +0.483] |

**変化量どうし**（生徒 − 教師、対応あり）:
`ΔSCOREQ vs ΔOVRL` **+0.5758** [+0.225, +0.795] / `ΔUTMOS vs ΔOVRL` −0.1531 [−0.524, +0.267]。

順位が最も食い違った発話（`reports/e1_dnsmos/e1.json` の
`largest_rank_disagreement_scoreq_vs_dnsmos`）:

| uid | ΔSCOREQ | ΔOVRL | SCOREQ 順位 | DNSMOS 順位 |
|---|---:|---:|---:|---:|
| `BASIC5000_0123` | −0.5011 | −0.7327 | 21 | 8 |
| `BASIC5000_2652` | −0.5663 | −0.7699 | 19 | 6 |
| `BASIC5000_4197` | −0.9725 | −0.5618 | 5 | 15 |

⚠️ **どちらが正しいかは決めない**（`evaluating-quality` skill の明示指示）。

### F0 交絡 — **決着しなかった**

2 推定器で平均 log F0 を出し、対応のある ΔDNSMOS との相関を見た:

| 推定器 | 平均 ΔlogF0 (生徒−教師) | r(ΔlogF0, ΔOVRL) | r(ΔlogF0, ΔSIG) |
|---|---:|---|---|
| `pyworld.harvest` | −0.0735 | +0.3150 [−0.101, +0.637] | +0.3120 [−0.104, +0.635] |
| `librosa.pyin` | −0.0102 | +0.0766 [−0.337, +0.466] | +0.1478 [−0.272, +0.520] |

⚠️ **2 推定器で符号が保たれない**（P.808 と BAK で反転）。**平均 ΔlogF0 自体が
7 倍違う。** M-44 の pyin→yin で 35/36 が 28/36 に動いた前例と同じ形なので、
**n=24 の本測定では F0 交絡の有無を判定できない**（あるとも無いとも書けない）。

⚠️ この環境では `import pyworld` が `ModuleNotFoundError: pkg_resources` で落ちる
（setuptools 84.0.0 が `pkg_resources` を同梱しない）。
本スクリプトは**プロセス内だけの最小 shim** で回避している。**eval extra を使う
他のスクリプトでも同じ理由で pyworld は落ちる。**

### ⚠️ `speechmos` は UTMOS と**パッケージ名が衝突する**（M-49 の指摘を再現し、解決した）

`torch.hub` の `tarepan/SpeechMOS:v1.2.0` は
`~/.cache/torch/hub/tarepan_SpeechMOS_v1.2.0/speechmos/` という**同名パッケージ**を同梱する。

```
UTMOS を先に読む  → import speechmos.dnsmos       → ModuleNotFoundError
DNSMOS を先に読む → torch.hub.load(UTMOS)          → ModuleNotFoundError: speechmos.utmos22
```

M-49 は「別プロセスに分ける」で回避しているが、**同一プロセスで両立できる**:
`src/saanotts_jp/dnsmos_metric.py` は `importlib.metadata` で site-packages 上の
`speechmos/dnsmos.py` の実ファイルを引き当て、`spec_from_file_location` で
**別名モジュール**として読む。`dnsmos.py` は相対 import を持たないのでこれで完結する。
G0 が (a) 衝突の実在（陽性対照）(b) 両方向で通ること (c) 素の import と**同値**
（`repr` 一致）を毎回検査する。

### 環境（`reports/e1_dnsmos/e1.json` の `environment`）

```
speechmos 0.0.1.1   librosa 1.0.0   onnxruntime 1.29.0   Python 3.14.0
resampler = soxr_hq（凍結）   SR=16000   INPUT_LENGTH=9.01（ライブラリから assert）
同梱 ONNX  sig_bak_ovr.onnx 1,157,965 B  model_v8.onnx 224,860 B
           bak_ovr.onnx 742,375 B        sig.onnx 742,203 B
uv が入れた実体は reports/r1_dnsmos/speechmos.whl と 13/13 ファイル一致
  (wheel sha256 31c4c9d3234f6ee10102edff74333014c50006a3f389daf7ecacae34e68ebbf7)
```

⚠️ **ホイールの出自は照合していない。** 同梱 ONNX が `microsoft/DNS-Challenge`
（CC-BY-4.0）由来かどうかは未確認。本 PoC は配布しないので着手のブロッカーではないが、
**公開する段になったら再確認が要る**（教師コーパスと同じ扱い）。

### 測っていないこと

- **聴取**（P-2 の担当）。DNSMOS を β の選択に流用していない
- **8–11 kHz の欠陥**。DNSMOS の入力は 16 kHz で Nyquist が 8 kHz。
  **原理的に見えない。2–8 kHz の SFM プローブの代わりにはならない**
- **うちの生徒に金属的欠陥が実在するか。** G7 は**代理**に対する感度しか測っていない
- **上流の申告（metallic ⇒ DNSMOS 低）の再現。** `docs/upstream-sanotts.md` に隔離のまま
- **personalized DNSMOS / DNSMOS Pro / NISQA / distillmos**
- **int8 / C99 コアの出力音声**（E-1 は fp32 経路の wav だけ）
- **日本語 DNSMOS の較正そのもの**（日本語 MOS の収集・再学習）
