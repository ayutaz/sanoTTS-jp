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

## M-10. 教師音声の品質（暫定・要再測定）

> ワークフロー調査エージェントによる**暫定値**。8 文・1.3〜3.6 秒の短尺クリップでの計測。

| 指標 | 教師の実測 | 論文の教師 (Kristin) |
|---|---:|---:|
| SCOREQ | 2.06 | 4.68 |
| UTMOS | 1.62 | 4.42 |

**大きく外れている。** 原因が (a) 短尺クリップ (b) パディング (c) 教師自体の品質 の
どれかは未切り分け。**生徒は教師を超えない**ので、これはプロジェクト全体の期待値に直結する。

⚠️ この計測が canonical な音素化経路（M-4.1）を使っていたかは未確認。
M-5.2 の通り、経路を間違えると 2.4 倍速の音声になり、それだけで品質スコアは崩れる。
**再測定時はまず経路を確認すること。**

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
