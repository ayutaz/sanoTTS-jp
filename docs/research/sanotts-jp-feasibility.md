# sanoTTS 日本語モデル 実現可能性調査

- 調査日: 2026-08-26
- 論文: Ashish Thapa (Ampixa Labs), *"sanoTTS: The Smallest Real-Time Neural TTS on a General-Purpose Microcontroller"*, [arXiv:2608.21378v1 \[cs.SD\]](https://arxiv.org/abs/2608.21378) (2026-07-14 submitted)
- 教師モデル供給元: [`~/Documents/piper-plus`](file://~/Documents/piper-plus) (piper-plus v2.0.0)
- ⚠️ **この行は誤りだった（C-024）。** 当時「`Ampixa/saanotts` は 404」と記録したが、**綴り間違い**で、正しくは [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS)。**公式実装は実在する**（GPL-3.0）。ただし本リポジトリは MIT なので**ソースコードは参照せず** clean-room で進める。上流から得た事実は [`../upstream-sanotts.md`](../upstream-sanotts.md) を見ること

> **⚠️ 本書は初期調査（2026-08-26、着手判断のための資料）。** その後の実測でいくつかの結論が
> 更新されている（特に §3.1 の G2P と §4 の決定事項）。
> **現在地は [`../README.md`](../README.md)、確定事項は [`../decisions.md`](../decisions.md)、
> 数値は [`../measurements.md`](../measurements.md) が正。**
> 本書は「論文の全数値」と「piper-plus の資産棚卸し」の参照元として使う。
>
> **2026-09-03 時点の現在地**（本書の予想と突き合わせるとき用。数値の出典はすべて `../measurements.md`）:
>
> | 本書が「これから」と書いたこと | 今 |
> |---|---|
> | Phase 0〜6 | **Phase 0 / A / B / C / D と検証タスクは全部決着**。成果物は `runs/v3/stage4.pt` |
> | 端末で喋るか | **M5Stack CoreS3 の実機で喋っている**（[M-90](../measurements.md#m-90)） |
> | RTF | **満チャンク 1 pull で 0.446**（W8A8+PIE）。要件 ≤ 0.5 を満たす。⚠️ 発話全体では 0.541〜0.712 で未達（分母は未決 = D-049） |
> | 品質 | SCOREQ 教師比 **0.6444**（目標 0.55）/ DNSMOS OVRL 教師比 0.7969 / アクセント符号一致 37/37（[M-61](../measurements.md#m-61) / M-59） |
> | G2P（§3.1 の最大の障壁） | **かな中間表現なら端末側 877 B**。さらに **漢字も端末で扱える**（辞書 13.7 MB を mmap。`b0-g2p-footprint.md` の結論は [`k1-kanji-katakana-ondevice.md`](k1-kanji-katakana-ondevice.md) で覆った） |
> | 残っていること | **対照つきの聴取（G32）だけ。** ⚠️ ざっとした聴取は済んでいる（[M-91](../measurements.md#m-91) / [M-93](../measurements.md#m-93) = 実機 / [M-96](../measurements.md#m-96) = ブラウザ。どれも**1 名・対照なし・盲検なし**）。残るのは `reports/k8_listen/` の 12 組と `reports/d4_accent/` |

---

## 0. 結論（先に要点）

| 論点 | 判定 | 根拠 |
|---|---|---|
| 論文のレシピを日本語に適用できるか | **できる**。論文自身がベトナム語・インドネシア語で多言語再訓練を実証済み（Table II） | 「言語ごとに教師・重み・フロントエンドを分け、**学習レシピだけを共有する**」設計 |
| piper-plus を教師にできるか | **できる。ただし ONNX ではなく `.ckpt` + PyTorch が必須** | `SynthesizerTrn.infer()` が蒸留に必要な `(audio, attn, y_mask, (z, z_p, m_p, logs_p), durations)` をそのまま返す。ONNX export は `output` と `durations` しか出さず **潜在変数 z が取れない** |
| 潜在インターフェースの互換性 | **完全に一致する** | 教師 `inter_channels = 192`（論文の「192チャネル教師潜在」と同一）、hop 256 / 22.05 kHz → **86 frames/s** も論文の decoder と同一 |
| 学習用の音声データ | **不要**。テキストのみで足りる | 蒸留ラベル（`dT`, `zT`, `yT`）はすべて教師の決定的推論から生成される |
| ~~最大の障壁~~ **解消済み** | 日本語 G2P（OpenJTalk 辞書 102 MB）が MCU に載らない問題は、**入力仕様の変更で解決した** | 辞書枝刈りは 40 MiB 必要で不成立（B-0）。「ひらがな + アクセント記号 + 無声化マーク」に変更し端末側 **877 B** に（D-010 / D-011 / C-042）。§3.1 は当時の分析として残す |
| 教師 `.ckpt` | **確保済み**: `ayousanz/piper-plus-zero-shot-tsukuyomi` (private) の `epoch=499-step=22000.ckpt` | 単一話者日本語、公開 canonical と `config.json` 一致。§2.4 参照 |
| 教師コーパスのライセンス | **本プロジェクトでは非ブロッカー**（検証目的・非配布、2026-08-26 ユーザー判断） | 公開時には再確認が必要。§6 参照 |

---

## 1. 論文の仕様（再現に必要な数値）

### 1.1 3つの生徒ネットワークと「c-line」

論文の中核は **40次元の明示的な潜在インターフェース（c-line）** で3つの決定的な生徒を繋ぐこと。

```
音素ID x ──▶ Duration Dα: d̂ ──▶ Acoustic Aβ: ĉ ∈ R^{40×T} ──▶ iSTFT Decoder Gγ: ŷ
```

| モジュール | パラメータ数 | 構成 |
|---|---:|---|
| Duration `Dα` | 36,164 | width 32、kernel-5 residual block × 3 |
| Acoustic `Aβ` | 199,536 | width 48、token block × 3 + frame block × 5、出力 40ch |
| Decoder `Gγ` | 331,308 | width 76、kernel-7 block × 5、pointwise 拡張 304ch、rank-12 conditioning、513 magnitude + 1,026 phase bin → **1024点 iSTFT / hop 256** |
| **合計（デプロイ時）** | **567,008** | int8 blob 2個で 679,832 バイト（280,288 + 399,544） |

- 学習専用の `z → c` エンコーダ `Eρ`（14,952 params）は **デプロイ時に実行されない**（acoustic が直接 c を出すため）。
- デプロイ版の acoustic 語彙は 157 エントリ（研究構成より 30 多い、埋め込み +1,440 params）。
- 計算量: 音声1秒あたり **約 45 MMAC**（うち decoder が 28 MMAC/s）。

### 1.2 品質階層（Table I / III）

| システム | Params | SCOREQ | UTMOS | DNS-SIG |
|---|---:|---:|---:|---:|
| embedded fixture (Kristin 蒸留, int8) | 0.567 M | 2.54 | 2.80 | 3.26 |
| R8 z-line ablation | 0.600 M | 2.94 | — | — |
| Kristin diagnostic (β=0) | 1.396 M | 4.09 | 3.98 | 3.58 |
| Kristin diagnostic (β=6, sibilant 修復) | 1.396 M | 3.92 | 3.89 | 3.57 |
| Amy Pareto（現行英語デフォルト） | 1.454 M | 4.13 | 4.10 | 3.61 |
| Amy champion | 1.834 M | 4.16 | 4.06 | 3.61 |
| Kristin 教師 | ~15.7 M | 4.68 | 4.42 | 3.59 |

**重要な含意**: 567 K の embedded tier は SCOREQ 2.54 で「実用品質」ではない。デモ／実証向け。**実用的な品質が欲しいなら 1.4 M 級（quality tier）を狙うべき**で、それは MCU ではなくブラウザ／デスクトップ向け。日本語プロジェクトでも最初にこの二択を決める必要がある。

### 1.3 損失関数（そのまま実装できる形）

**Duration** (式2):

```
r_i = max(1, exp(ℓ̂_i)),   d̂_i = clip_[1,80](round(s_v · r_i))
L_d = Huber_0.25(ℓ̂, log dT) + λ_T [ log Σ_i r_i − log Σ_i dT_i ]²
```

`s_v` は音声ごとの較正済み length scale（英語 quality voices = 1.08、ベトナム語/インドネシア語 = 1.16）。**日本語は較正が必要**。

**潜在インターフェース** (式3):

```
L_c = ‖ĉ − cT‖₁ + λ₂‖ĉ − cT‖₂² + λ_n‖N_T(ĉ) − N_T(cT)‖₁
      + λ_Δ‖Δĉ − ΔcT‖₁ + λ_s L_stat
L_stat = ‖(μ(ĉ) − μ(cT))/σ_T‖₁ + ‖(σ(ĉ) − σ(cT))/σ_T‖₁
```

`N_T(u) = (u − μ_T)/σ_T`（教師パック統計によるチャネル正規化）。高分散チャネルが目的関数を支配するのを防ぐ。
※ 論文本文に `λ₂, λ_n, λ_Δ, λ_s` の**具体値は書かれていない**（"the weighted sum implemented by the trainer"）。**要チューニング**。

**Decoder / waveform** (式5):

```
L_G(ŷ, yT) = λ_w‖ŷ − yT‖₁ + (λ_S/|R|) Σ_{(n,h)∈R} ℓ_{n,h} + λ_A L_adv + λ_F L_FM
ℓ_{n,h} = ‖log(1+|S_{n,h}(ŷ)|) − log(1+|S_{n,h}(yT)|)‖₁
```

`R` = FFT size {512, 1024, 2048} × hop {128, 256, 512}。`L_adv` は LSGAN、`L_FM` は判別器特徴マッチング（一次差分 `Δŷ` に対する判別器）。

**Joint** (式6):

```
ŷ = Gγ(Aβ(x, d̂))
L_joint = L_G(ŷ, yT) + λ_c ‖Aβ(x, d̂) − cT‖₁
```

**公開されている重み**: `(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)`

**z-line（1.396 M / 1.454 M 級）の追加**: c の代わりに 192ch の z をそのまま使い、hinge adversary を足す（式4）:

```
L_zD = E[(1 − D_z(zT))₊] + E[(1 + D_z(ẑ))₊]
L_z,adv = −E[D_z(ẑ)]
```

**推論時の摩擦音修復** (式7):

```
z̃_{t,k} = ẑ_{t,k} + 1[x_t ∈ S] · β · σT_k · ε_{t,k},  ε ~ N(0,1)
S = {/s, sh, z, zh/},  β = 6（聴取で選択）
```

### 1.4 教師ラベル生成の条件

> "For every promoted pack, label generation sets the noise and duration-noise scales to zero and the length scale to one."

つまり `noise_scale = 0`, `noise_scale_w = 0`, `length_scale = 1` で決定的推論。教師は凍結し、`dT`（durations）、`zT`（固定 prior サンプル = m_p）、`yT`（波形）を取得。**posterior `q_φ` は使わず、text-conditioned prior 側からラベルを取る。**

### 1.5 学習データ規模（重要な失敗事例）

| 学習行数 | diverse24 SCOREQ | 備考 |
|---:|---:|---|
| 512 | 1.72 | narrow set では 3.07 に見えた → **1.35 の過大評価** |
| 14,343 | 2.54 | 現行 embedded artifact |

さらに 512 行での対照実験:
- 明示的インターフェース無しの merged text→waveform 生徒 → **1.06 SCOREQ / WER 0.28**（訓練行を丸暗記、未知テキストで崩壊）
- 40次元インターフェースを持つ factored モデル → **2.95 SCOREQ / WER 0**

**→ c-line の明示的インターフェースは必須。データ量も 1万行以上必要。**

### 1.6 量子化とデプロイ

- 重み: **対称 int8、出力チャネルごと**の量子化
- 活性: **フレームごと**の量子化
- 浮動小数点のまま残すもの: 埋め込み、正規化アフィン、iSTFT サポートコード
- ランタイム: caller-owned arena を使う **移植可能な C99 コア** + 小さな port インターフェース（int8/int16 dot・matvec カーネル、メモリ常駐性判断、並列実行、スクラッチ選択、計時）
- ゴールデンテスト: 各 port は fp 参照音声と **Pearson 相関 0.98 以上**で一致すること

| ターゲット | audio (s) | compute (s) | RTF |
|---|---:|---:|---:|
| ESP32-S3 初回 e2e | 4.54 | 6.685 | 1.47 |
| ESP32-S3 最終（PIE + iFFT + dual core） | 4.54 | 1.021 | **0.22** |
| ESP32-C3 初回 | 1.56 | 18.680 | 12.01 |
| ESP32-C3 整数パイプライン | 1.56 | 8.900 | **5.72** |
| Host C (M3 Pro) | 1.556 | 0.0065 | 0.0042 |
| WASM / Node | 4.539 | 0.079 | 0.017 |

S3 のピーク arena 使用量は **約 289 KB**。S3 では SIMD オペランドを内部 SRAM にステージングする必要がある（flash マッピング経由で読むと相関 0.011 まで崩壊した）。

---

## 2. piper-plus 側の資産棚卸し

### 2.1 蒸留ラベル生成に直接使える API（最重要の発見）

`src/python/piper_train/vits/models.py:1002` の `SynthesizerTrn.infer()` は **論文が必要とする教師ラベルをすべて返す**:

```python
return InferOutput(o, attn, y_mask, (z, z_p, m_p, logs_p), durations)
#                  ↑yT  ↑A         ↑zT                     ↑dT
```

docstring に `.. versionchanged:: 2026.03 ... (added durations)` とあり、`durations` は比較的最近追加されている。

決定的ラベル生成の呼び出し（論文 §II の条件）:

```python
out = model.infer(
    x, x_lengths,
    lid=torch.tensor([0]),          # ja = 0
    noise_scale=0.0,                # → z_p = m_p（決定的）
    noise_scale_w=0.0,              # → SDP が決定的
    length_scale=1.0,
    prosody_features=prosody,       # 実 A1/A2/A3（ゼロ埋めは別物になる、§3.2）
    speaker_embeddings=None,        # ← None。この ckpt では完全に無視される
)
yT, zT, dT = out.audio, out.latents[0], out.durations
```

`infer()` 内部で `z_p = m_p + randn_like(m_p) * exp(logs_p) * noise_scale` なので `noise_scale=0` で決定的になる（`onnx_export_mode` を立てる必要はない）。

### 2.2 ONNX では蒸留できない

ローカルの `models/tsukuyomi.onnx` を検査した結果:

```
teacher params: 19,335,311
inputs : input, input_lengths, scales[3], lid, prosody_features[B,T,3],
         speaker_embedding[B,256], speaker_embedding_mask[B,1]
outputs: output, durations
```

**潜在 `z` が出力されない**ため、`.ckpt` を PyTorch で読んで `infer()` を呼ぶ経路しかない。ラベル生成器は piper-plus のリポジトリ内（またはそれを import する形）で動かす必要がある。

### 2.3 アーキテクチャの一致・不一致

| 項目 | 論文の教師 (upstream Piper) | piper-plus v2.0 | 影響 |
|---|---|---|---|
| 潜在次元 `inter_channels` | 192 | **192** | ✅ そのまま。40ch 圧縮も同条件 |
| サンプルレート | 22,050 Hz | **22,050 Hz** | ✅ |
| hop / フレームレート | 256 / 86 fps | **256 / 86.13 fps**（`upsample_rates=(4,4)` × iSTFT(4) × PQMF(4) = 256x） | ✅ decoder の入力レートが一致 |
| Decoder | HiFi-GAN | **MB-iSTFT-VITS2 + PQMF**（`MBiSTFTGenerator`, `src/python/piper_train/vits/mb_istft.py`） | ⚠️ 教師側の違いだが、生徒 decoder は独自実装なので影響は `yT` の音色のみ |
| 話者条件 | 単一話者 | multi-speaker（CAM++ 192-dim / 旧 export は 256-dim）+ `gin_channels=512` | ⚠️ ラベル生成時に固定すれば良い |
| 言語条件 | 無し | `lid` テンソル（ja=0, en=1, zh=2, es=3, fr=4, pt=5） | ⚠️ `lid=0` 固定 |
| 韻律条件 | 無し | **`prosody_dim=16`（A1/A2/A3 を duration predictor に注入）** | ⚠️ **日本語固有・§3.2** |
| フロントエンド | eSpeak NG | **OpenJTalk / NAIST-JDIC**（ホスト側のみ） | ✅ 入力仕様の変更で解決。端末側はテーブル 877 B（D-010 / D-011 / C-042） |
| 音素数 | — | 173（公開 ckpt, `symbol_set_version` 1.0）/ 185（現行コード 1.1） | ⚠️ FT 時は ckpt 側の `phoneme_id_map` を使うこと |

### 2.4 教師モデル候補

| モデル | 話者 | 言語 | 形式 | 入手先 | 備考 |
|---|---|---|---|---|---|
| Tsukuyomi-chan 6lang (canonical) | 1 | JA/EN/ZH/ES/FR/PT | ONNX FP16 39.65 MB (実測) | [`ayousanz/piper-plus-tsukuyomi-chan`](https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan) | 500 epoch FT。**ONNX しか無い → ckpt が必要** |
| CSS10 Japanese 6lang | 1 | 同上 | ONNX FP16 | [`ayousanz/piper-plus-css10-ja-6lang`](https://huggingface.co/ayousanz/piper-plus-css10-ja-6lang) | 50 epoch、6,841 発話 |
| 6-Language Base (MB-iSTFT-VITS2) | 571 | 同上 | **`model.ckpt` 302 MB** | [`ayousanz/piper-plus-base`](https://huggingface.co/ayousanz/piper-plus-base) | ⚠️ v2.0 では読めない（`MBiSTFTGenerator.cond` の FiLM 化で 256 vs 512 の size mismatch、Issue #616）。**`git checkout v1.13.0` が必要** |
| ローカル `models/tsukuyomi.onnx` | 1 | 6lang | ONNX 39 MB, 19.3 M params | piper-plus 同梱 | 入出力は §2.2 の通り |

**HF API で全 repo を調査した結果 (2026-08-26、`ayousanz` として認証済み)**:

公開 repo には蒸留に使える `.ckpt` がほぼ無い:

```
ayousanz/piper-plus-tsukuyomi-chan  → ONNX のみ (tsukuyomi-chan-6lang-fp16.onnx)
ayousanz/piper-plus-css10-ja-6lang  → ONNX のみ
ayousanz/piper-plus-base            → model.ckpt (571話者 multilingual)
```

**private repo に単一話者日本語の `.ckpt` が存在する**:

| repo (private) | ckpt | サイズ | 系統 |
|---|---|---:|---|
| **`piper-plus-zero-shot-tsukuyomi`** | **`epoch=499-step=22000.ckpt`** | **927 MB** | **zero-shot v7 → Tsukuyomi 単一話者 FT** |
| `piper-jp-en-model` | `tsukuyomi-v4-emb-lang-fix/epoch=499-...ckpt` | 935 MB | ja-en bilingual v4 (`num_symbols=97`) |
| `piper-plus-tsukuyomi-chan-all` | `lightning_logs/version_3/last.ckpt` | 1,962 MB | WavLM 300epoch |
| `piper-plus-base-all` | `model.ckpt` ほか多数 | 639 MB〜 | multi-speaker base |
| `piper-plus-tiny` | `checkpoints/last.ckpt` | 601 MB | 571話者、ONNX 7.8 MB の小型版 |

### 採用: `piper-plus-zero-shot-tsukuyomi/epoch=499-step=22000.ckpt`

`config.json` がローカルの canonical モデル (`models/config.json`) と一致する
（ただし後述の通り、**config 一致はモデル同一性の証明にはならない**）:

```
num_speakers: 1          num_languages: 6        phoneme_id_map: 185 entries
quality: "dataset-tsukuyomi-finetune-6lang"
inference: {noise_scale: 0.667, length_scale: 1, noise_w: 0.8}
```

なお **ローカル `models/tsukuyomi.onnx` は公開版 `tsukuyomi-chan-6lang-fp16.onnx` と
SHA-256 が完全一致**することを確認した
(`5289e9b6eaf21080803b7fe1c4dc85b5491d4c216121207a41df18dd5f68e5d7`, 39,652,717 B)。

> **注意**: ローカル ONNX と本 ckpt は `phoneme_id_map` まで完全一致するため、
> **`config.json` の一致は「同じモデル」の証明にならない**。実際、同一文で durations が
> 98 vs 115 と別物になる。ローカル ONNX は 173 音素・pre-FiLM の旧系統であり、
> その `speaker_embedding[B,256]` 入力は xavier 初期化のまま学習されていない死んだ入力。
> 本 ckpt を選ぶ根拠は「単一話者日本語で、v2.0 にロードでき、決定的推論が取れる」ことであって、
> 公開 ONNX と同一であることではない。

同 repo には `eval/spk_tsukuyomi.npy` と `eval/compute_secs.py` が入っている。

> **訂正 (2026-08-26 実測)**: 当初この npy を「話者固定に使える」と書いたが**誤り**。
> この ckpt は `num_speakers=1` で `spk_proj` / `emb_g` を state_dict に 1 件も持たず、
> `speaker_embeddings` に **None / この npy / ランダム 192次元** のどれを渡しても
> `audio` が bit 完全に一致する。話者は重みに焼き込まれている。
> **ラベル生成では `speaker_embeddings=None` を渡すこと。** npy は SECS 評価専用。

### 互換性: 実測で解決済み (2026-08-26)

ckpt をダウンロードして実測した結果、**piper-plus v2.0 (HEAD) にそのままロードできる。
`v1.13.0` への checkout は不要**だった。

`hyper_parameters` 実測値:

```
num_symbols=173  num_speakers=1  num_languages=6
inter_channels=192  hidden_channels=192  gin_channels=512
spk_embed_dim=192  use_zero_shot=True  prosody_dim=16
resblock=2  upsample_rates=(4,4)  upsample_initial_channel=256  use_sdp=True
```

| 懸念 | 実測結果 |
|---|---|
| `speaker_embedding` が 256 dim では? | **192 dim だった** (`spk_embed_dim=192`)。公開 ONNX の 256 dim は別系統のエクスポートで、この ckpt とは無関係 |
| FiLM 化による `dec.cond` の size mismatch (Issue #616) | **起きない**。`dec.cond.weight` は `(512, 512, 1)` で `cond_layers` も存在 → Multi-scale FiLM 適用済み。`normalize_checkpoint_state_dict` の `cond_migrated` は 0 |
| `load_state_dict` | **missing 0 / unexpected 0** |
| 話者埋め込み参照 | `eval/spk_tsukuyomi.npy` は shape `(192,)` / L2 ノルム 1.0 → `speaker_embeddings` にそのまま渡せる |

決定的推論も疎通済み（`scripts/phase0_verify_teacher.py`、**6 チェック**すべて PASS。⚠️ かつて「5 チェック」と書いていたが、スクリプトの `checks` は 6 項目）:

```
zT latent   : (1, 192, 37)   ← 192ch、論文の教師潜在と一致
yT audio    : (1, 1, 9472)   ← 37 frames × hop 256 == 9472 sample（hop 256 を実測で確認）
dT durations: (1, 9)         ← 音素数と一致
audio / z ともに二回実行で bit 完全一致（決定的）
```

### ⚠️ 音素表の落とし穴（実測で判明）

1. **`num_symbols=173` だが `config.json` の `phoneme_id_map` は 185 entry ある。**
   ID 173 以上を渡すと埋め込みの範囲外になる。先頭 173 だけが有効
   （`docs/guides/development/pretrained-models.md` の「音素表の世代差」の注意書きと整合）。
2. **拗音・破擦音 (`ch` `sh` `ts` `ky` など) は `phoneme_id_map` に生の文字列で入っていない。**
   PUA (U+E000〜U+E015) にエンコードされている。`phoneme_id_map["ch"]` は KeyError になることを
   実測で確認した。ラベル生成器は必ず `src/python/jp_phoneme_map.py` の `PHONEME_TO_PUA`
   を経由すること（§3.5 の PUA 整合性の注意と同じ話）。

### 2.5 流用できる評価インフラ

| 資産 | パス | 用途 |
|---|---|---|
| PESQ-WB / STOI / **UTMOS22** / Whisper WER | `scripts/audio_quality_metrics.py` | 論文の UTMOS・WER 指標にほぼ対応（**SCOREQ は未実装 → 追加が必要**） |
| MOS リスニング調査 HTML 生成 | `tools/benchmark/` (`docs/benchmark-mos.md`) | 論文が「学習済み予測器は MOS の代替にならない」と明言している部分の補完 |
| 日本語評価文 | `scripts/evaluation/evaluation_texts_ja.txt` | diverse24 相当の日本語版のシード |
| 日本語 TTS スモークテスト | `scripts/test_japanese_tts.py`, `examples/japanese_tts.sh` | 回帰確認 |

---

## 3. 日本語固有の課題（本調査の中心）

### 3.1 G2P フロントエンドが MCU に載らない ✅ 解決済み

> **この節は初期調査時の分析。結論は変わったが、分析自体は正しかった。**
>
> - **確定した対応**: 入力を「ひらがな + アクセント記号 + 無声化マーク」に変更し、
>   端末側 G2P を **テーブル 877 B（mora 195 + 記号 10 + `ん` 異音 21）**にした（D-010 / D-011 / C-042）
> - **実測**: held-out で表現可能 96.40% / 往復一致 **100%** / 教師出力と **bit 完全一致**
> - **辞書枝刈り路線は不成立**と実測で確定（B-0）。文単位 95% には 40 MiB 必要で
>   32 MB ボードにも入らない。詳細は [`b0-g2p-footprint.md`](b0-g2p-footprint.md)
> - 以下の案 A〜D の比較表は当時のもの。**実際には「案 B: かな入力限定」に着地した**


論文は G2P を**システム境界の外**に置いている:

> "Under the boundary used here—**external G2P excluded**, all inference-time neural tensors included, and physical MCU timing required—..."

そのうえで、参考値として on-chip eSpeak も測っている:

> "The standalone on-chip eSpeak frontend introduces another practical gap: it raises end-to-end WER from **15.8% with desktop eSpeak to 18.5%**."

**日本語にはこの「on-chip eSpeak」に相当するものが無い。** piper-plus の日本語 G2P は OpenJTalk + NAIST-JDIC で、実測サイズは:

```
build/share/open_jtalk/dic  →  102 MB
  matrix.bin  3.8 MB   char.bin  262 KB   sys.dic / unk.dic ...
```

ESP32-S3 の一般的な flash は 8〜16 MB、PSRAM は 2〜8 MB。**102 MB は桁が足りない。** piper-plus 自身も Android では「AAR に辞書を同梱しない」方針を取っており（`docs/guides/platform/android-g2p-dictionary.md`: 「他の 7 言語は `libpiper_plus.so` 内のデータと規則ベース実装で完結する。日本語のみが OpenJTalk の辞書を必要とする」）、この問題は piper-plus エコシステム全体の既知の弱点。

**取り得る対策**:

| 案 | 実装コスト | フットプリント | 品質 | 適用フェーズ |
|---|---|---|---|---|
| **A. ホスト側 G2P**（PC/スマホで音素IDに変換し、MCU には音素ID列を送る） | 低（既存資産そのまま） | 0 | 教師と同等 | **フェーズ1推奨。論文の境界とも一致** |
| **B. かな入力限定の規則ベース G2P**（かな→音素は決定的、数十 KB） | 中 | ~50 KB | 漢字が読めない。アクセントは別途辞書が必要 | フェーズ2 |
| **C. 辞書の枝刈り + 圧縮**（頻出語彙のみ、LOUDS/ダブル配列） | 高 | 数 MB を狙う | 未知語で崩れる | フェーズ3（研究） |
| **D. jpreprocess / lindera 系**（Rust 実装、NAIST-JDIC 埋込 ~20 MB） | 中 | ~20 MB | OpenJTalk 互換 | MCU には依然大きいが、**WASM / モバイル tier では有力** |

piper-plus は既に D の経路を持っている（`docs/guides/platform/swift-g2p-integration.md`: 「jpreprocess (Rust port of OpenJTalk) / NAIST-JDIC 埋込 / ~20 MB」）。

**推奨**: プロジェクトの scope を最初に切ること。
- **MCU tier** → 入力は音素ID（案 A）。「テキストから喋る MCU」ではなく「音素列から喋る MCU」として仕様を定義する。論文の主張範囲と一致するので、正直な比較になる。
- **WASM / ブラウザ tier** → 案 D で完結。論文の quality tier（1.4 M）はブラウザ向けなので、こちらが実用性の主戦場。

### 3.2 ピッチアクセント（A1/A2/A3 韻律特徴）の扱い

piper-plus の日本語は **duration predictor に OpenJTalk full-context label 由来の A1/A2/A3 を注入している**（`models.py:870` `_prepare_prosody_input`、`prosody_dim=16`、`prosody_language_ids={0}` = JA のみ）。

一方、論文の duration student は **音素ID だけ**を入力に取る（`Dα: x → d̂`, width 32）。

**問題**: 教師ラベル `dT` は A1/A2/A3 込みで生成されるのに、生徒がそれを見られないと、生徒は「観測できない条件に依存した目標」を学習することになる。日本語のアクセント型は同音異義語で持続長・ピッチが変わるため、これは実際に効く。

**選択肢**:

1. **音素列のアクセント記号に頼る**（コスト0）
   `src/python_run/piper_plus/phonemize/jp_id_map.py` の `SPECIAL_TOKENS` には既に
   `#`（アクセント句境界）、`[`（上昇）、`]`（下降核）が入っている。
   これらは音素IDとして生徒にも見える。**まずはこれで足りるか実験するのが安い。**

2. **生徒 duration net に A1/A2/A3 の3スカラーを追加入力する**（コスト極小）
   width 32 のネットに 3ch 足すだけ。パラメータ増は数百程度で、567 K の予算に対して無視できる。
   ただし**推論時にも A1/A2/A3 が必要**になり、§3.1 の案 B（かな限定 G2P）と両立しない
   （アクセント核の推定には辞書が要る）。

3. **prosody を切って教師ラベルを生成する**（`prosody_features=None` → ゼロ埋めの後方互換パス）
   生徒と教師の入力条件は揃うが、教師自体のアクセント品質が落ちる。非推奨。

**推奨**: 案1で開始し、アクセント誤りが評価で顕在化したら案2へ。この判断は**評価セットにアクセント型のミニマルペアを入れておかないと検出できない**（例: 「橋/箸/端」「雨/飴」）。

### 3.3 摩擦音・無声化母音（論文が踏んだ地雷の日本語版）

論文は SCOREQ 4.09 / UTMOS 3.98 という良好な集約スコアの裏で **sibilant が "whistly"** になる欠陥を見逃していた。音素クラス別の 2–8 kHz スペクトル平坦度で初めて検出:

| 経路 | sibilant 平坦度 |
|---|---:|
| 教師 | 0.689 |
| oracle decoder（教師潜在 → 生徒 decoder） | 0.693 |
| フル生徒 | **0.590** ← acoustic student が確率的な摩擦を平滑化していた |
| 式7 の修復（β=6） | 0.655 |

**日本語での拡大要因**:
- 摩擦音・破擦音の種類が多い: `s, sh, z, j, ts, ch, h, hy, f, ky` など
- **無声化母音**（`A, I, U, E, O` の大文字トークン。piper-plus の JA 音素表に定義あり）は音響的にほぼ摩擦雑音。「です」「ます」「した」など高頻度語に出る
- `cl`（促音）は無音→バースト、`N` 系（`N_m/N_n/N_ng/N_uvular`）は鼻音

**推奨**:
- 式7 のノイズ注入集合 `S` を日本語向けに拡張:
  **`S_ja = {s, sh, ts, ch, z, j, h, hy, f, I, U}`**
  （当初 `A` `E` `O` も入れていたが、**コーパス 23,271 行の実測で 1 度も出現しない**ため除外。
  日本語の母音無声化は狭母音 `i` `u` にほぼ限られる）
- `β` は聴取で決める（論文も "selected by listening"）。無声化母音と摩擦音で別の `β` を持たせる余地あり
- **音素クラス別のスペクトル平坦度プローブを最初から評価パイプラインに入れる**。論文の教訓の中で最も再利用価値が高い

### 3.4 モーラタイミングと duration の clip 範囲

論文の duration は `clip_[1,80]` にクリップされる。日本語では:
- 長音（`a:`, `i:` … は独立トークン）と促音 `cl` は 1 モーラ分の長さを持つ
- モーラ等時性により、英語より duration の分散が小さい → **duration student には有利に働くはず**
- 式2 の「発話長保存項」`λ_T [log Σ r_i − log Σ dT_i]²` はモーラ言語で特に効くと期待できる

**要検証**: `clip_[1,80]` の上限 80 フレーム = 80/86.13 ≒ 0.93 秒。日本語の長い母音・語末の引き延ばしで飽和しないか、`dT` のヒストグラムで確認すること。

**要較正**: 音声ごとの length scale `s_v`（英語 1.08 / ベトナム語・インドネシア語 1.16）。日本語は実測して決める。

### 3.5 音素インベントリ

| 項目 | 値 |
|---|---|
| 論文の embedded acoustic 語彙 | 157 エントリ（埋め込み 1,440 params が 30 エントリ分） |
| piper-plus JA 音素表 | `jp_id_map.py`: 特殊トークン 10 + 音素 ~99（長音・`N` 4変種・拗音・`cl`/`q` を含む） |
| piper-plus 統合表 | 173 symbol（公開 ckpt, v1.0）/ 185 symbol（現行コード v1.1、KO/SV 追加） |

日本語単言語に絞れば **100〜120 エントリ程度**に収まり、論文の 157 より小さい。埋め込み削減効果自体は小さい（1エントリ ≒ 48 params）が、`^ $ ? ?! ?. ?~ # [ ]` の韻律トークンは必ず残すこと。

**⚠️ 訂正 (2026-08-26 実測)**: 当初「`jp_phoneme_map.py` の表を直接 import すること」と書いたが**誤り**。

- **canonical な変換表は `piper_plus_g2p.encode.pua` の `TOKEN2CHAR` / `CHAR2TOKEN` (99 entry)。**
  `PiperEncoder` が内部で使っているのもこちら。
- `src/python/jp_phoneme_map.py` の `get_phoneme_id_map()` は **58 entry / max id 57** しか返さず、
  **実測で 54 音素の id が ckpt と食い違う**（`a` は jp_phoneme_map で 7、ckpt では 10）。
  これを使うと音素ラベルが例外も出さずに総取り違えになる。
- 拗音・破擦音は `phoneme_id_map` に生の文字列で入っていない
  （`phoneme_id_map["ch"]` は KeyError。`TOKEN2CHAR["ch"] = U+E00E` → ckpt id 46）。

### 3.6 蒸留用テキストコーパス

**音声データは不要**。必要なのは**多様なテキスト 1万行以上**。

論文の教訓（§1.5）を日本語に読み替えると:
- 512 行のテンプレート文では **SCOREQ を 1.35 過大評価**した
- 14,343 行で diverse24 が 1.72 → 2.54 に改善

**日本語テキスト源の候補**（ライセンスを確認のうえ）:
- JSUT / ITA コーパスの読み上げ文（テキストのみ）
- Common Voice ja の文
- 日本語 Wikipedia の抜粋文
- 青空文庫（旧字・旧かなに注意）
- 自前の対象ドメイン文（組込み用途なら通知文・数値読み上げなど）

**日本語固有の多様性軸**（英語には無い、必ずカバーすること）:
- 漢字混じり / ひらがなのみ / カタカナ語 / 英数字混在
- 数詞と助数詞（「1つ」「1個」「1人」で読みが変わる）
- 日付・時刻・金額
- 記号・約物（「」『』〜・）
- 疑問文（`?`, `?!`, `?.`, `?~` の4種の EOS トークンがある）
- アクセント型のミニマルペア（§3.2）

### 3.7 評価指標の日本語適用

論文は多言語について**明確な制約を書いている**:

> "For Vietnamese and Indonesian, we report **only the ratio to the corresponding teacher** because absolute SCOREQ values are not calibrated for comparisons across languages."

**したがって日本語でも「教師比」で報告すべき**（ベトナム語・インドネシア語は 0.82× teacher）。SCOREQ / UTMOS の絶対値を英語モデルと比較してはいけない。

- **UTMOS** は `scripts/audio_quality_metrics.py` に UTMOS22 が実装済み。
  ただし **⚠️ 訂正 (2026-08-26)**: 当初「日本語データで訓練されている」と書いたが**誤り**。
  著者は東大猿渡研だが学習データは VoiceMOS Challenge 2022 の
  main track = BVCC（英語）/ OOD track = BC2019（中国語）で、**日本語は含まれない**。
  UTMOS も SCOREQ 同様、日本語では較正されていないので**教師比で報告する**
- **SCOREQ** は piper-plus 未実装 → 追加が必要
- **WER** は日本語では分かち書きの問題があるため **CER（文字誤り率）を主指標に**すべき。Whisper の日本語出力は表記ゆれ（漢字/かな）が大きいので、比較は「教師 vs 生徒」で取り、絶対値は参考にとどめる
- **人手 MOS** は `tools/benchmark/` の HTML 調査で実施可能。論文も「学習済み予測器は制御された MOS 研究の代替にならない」と明言

---

## 4. アーキテクチャ設計上の決定事項 ✅ すべて決着済み

> 本節は初期調査時の「これから決めること」リスト。**すべて決着したので結果を併記する。**
> 決定の理由と経緯は [`../decisions.md`](../decisions.md) が正。

| # | 決定事項 | 初期の推奨 | **確定した結論** |
|---|---|---|---|
| D1 | ターゲット tier | ③ 両方だが ② を先に | **567 K のみが成果物**。ブラウザは piper-plus の WASM で解決済みなので対象外。1.4 M は検証用の足場（D-007） |
| D2 | 教師モデル | 単一話者日本語 ckpt を確保 | **`piper-plus-zero-shot-tsukuyomi/epoch=499-step=22000.ckpt`**（D-002） |
| D3 | 入力境界 | 音素ID | **ひらがな + アクセント記号 + 無声化マーク**。端末側 G2P は 877 B（D-010 / D-011 / C-042） |
| D4 | 韻律条件 | アクセント記号のみで開始 | **アクセント記号のみで確定**。記号は音素IDそのもので、位置を変えると F0 が 42.8 Hz 変わる。`prosody_features` は duration にしか効かないので、ピッチ目的で生徒に足す必要は無い（M-13） |
| D5 | latent 幅 | 両方作る | **変更なし**。40ch が成果物、192ch は足場 |
| D6 | piper-plus の版 | v1.13.0 | **v2.0 HEAD をそのまま使う**。checkout 不要（D-003） |
| D7 | 実行環境 | （初期調査時は未検討） | **Python は `uv`**（D-012）。⚠️ 学習は手元の M4 Max に変更（D-027） |

---

## 5. 実装ロードマップ

### Phase 0: 前提の確定（コード無し・ブロッカー解消）

> ライセンスは **本プロジェクトでは非ブロッカー**（検証目的・非配布、2026-08-26 ユーザー判断）。
> 公開する段になったら §6 に戻ること。

1. ~~教師 `.ckpt` を確定~~ → **完了**: `piper-plus-zero-shot-tsukuyomi/epoch=499-step=22000.ckpt` を取得済み
2. ~~`infer()` を通る piper-plus 版を特定~~ → **完了**: v2.0 HEAD でそのまま動く（§2.4）
3. ~~決定的推論の再現確認~~ → **完了**: `scripts/phase0_verify_teacher.py` が 6 チェック PASS
4. ~~ターゲット tier を決定~~ → **完了: 567 K embedded tier が成果物**。1.4 M z-line は**作らなかった**（567 K が先に目標へ届いたため）
5. ~~日本語 G2P を繋いでラベル生成を疎通させる~~ → **完了**（D-014 の経路。⚠️ **A1/A2/A3 韻律は使わない** — `prosody_features` は一律ゼロ = D-014、アクセントは音素列の `[` `]` `#` だけで足りた = D-030）

### Phase 1: 教師ラベル生成パイプライン
- 日本語テキスト → OpenJTalk 音素ID + prosody(A1/A2/A3) + アクセント記号
- `SynthesizerTrn.infer(noise_scale=0, noise_scale_w=0, length_scale=1, lid=0)` で `(dT, zT, yT)` を一括生成
- **ラベルパック統計 `μ_T`, `σ_T` をチャネルごとに保存**（式3 の `N_T` と式7 の `σT_k` に必要）
- 出力形式・チェックサム・シード・フラグを manifest に記録（論文の "audited" 方針を踏襲）
- 目標: **14,000 行以上**

### Phase 2: Duration student
- width 32、kernel-5 residual × 3、36 K params
- 式2 の損失。`s_v`（日本語 length scale）を較正
- 単体で `dT` との相関・発話長誤差を評価

### Phase 3: z→c encoder + Acoustic student
- 学習専用エンコーダ `Eρ`: 192ch → 40ch（14,952 params、デプロイ対象外）
- Acoustic `Aβ`: width 48、token block × 3 + frame block × 5、40ch 出力、199 K params
- 式3 の損失（`λ₂, λ_n, λ_Δ, λ_s` は要チューニング）
- z-line 版では `Eρ` を省き 192ch を直接ターゲットにし、hinge adversary（式4）を追加

### Phase 4: Decoder + Joint 蒸留
- `Gγ`: width 76、kernel-7 × 5、pointwise 304ch、rank-12 conditioning → 513 mag + 1026 phase → 1024点 iSTFT / hop 256、331 K params
- 式5（multi-resolution STFT + LSGAN + FM、一次差分判別器）で teacher contract と student contract の両方を入力（predicted-code mixing）
- 式6 の joint で acoustic と decoder を同時更新、`(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)`
- **式7 の日本語版ノイズ注入**（`S_ja`, `β`）を実装

### Phase 5: 量子化 + C99 ランタイム
- 対称 int8（出力チャネルごと）+ フレーム毎活性量子化
- 埋め込み・正規化アフィン・iSTFT は fp のまま
- ゴールデンベクタと **Pearson 相関 ≥ 0.98** のテスト
- ESP32-S3 port（PIE カーネル、half-size real iFFT、dual core column split、arena ≤ 約 289 KB）
  → **PIE カーネルは書けて実機で動いた**（M-57 / M-58 / M-90）。**arena は 180,224 B（used 157,360 B）**で見積りより小さい（M-89）。
  ⚠️ **dual core column split は入れていない**（1 コアで要件を満たしたため。S-1 §5 の S8）

### Phase 6: 評価
- 日本語 diverse セット（テンプレート禁止、§3.6 の多様性軸をカバー、24 文では足りない）
- 教師比での SCOREQ / UTMOS / CER
- **音素クラス別 2–8 kHz スペクトル平坦度プローブ**（摩擦音・無声化母音・破擦音）
- アクセント型ミニマルペアの聴取確認
- 人手 MOS（`tools/benchmark/`）

---

## 6. リスクと未解決事項

| リスク | 深刻度 | 内容 / 対応 |
|---|---|---|
| **教師コーパスのライセンス** | **本プロジェクトでは非ブロッカー** | 検証目的・生成物を配布しないため着手を止めない（2026-08-26 ユーザー判断 = [D-006](../decisions.md#d-006)）。⚠️ **この前提は既に古い。** 「公開する段になったら要再確認」は **[D-035](../decisions.md#d-035) で実際に行われ**、方針は「配布する」に変わった（[D-039](../decisions.md#d-039) で重みを `LicenseRef-sanoTTS-jp-Model-1.0` に）。当時の懸案（つくよみちゃん `CC-BY-4.0 / verified: false`、MOE-Speech `CC-BY-SA-4.0 / verified: false`、**CC-BY-SA の継承**）は一次ソースで調べ直して解消している（C-029〜031） |
| ~~**日本語 G2P が MCU に載らない**~~ | **解消済み** | 辞書枝刈りは 40 MiB 必要で不成立だったが、入力を「ひらがな + アクセント記号 + 無声化マーク」に変更して**端末側 877 B** で解決した（D-009 〜 D-011、`scripts/kana_g2p.py`） |
| ~~ESP32 のメモリ~~ | **解消済み** | 実機で測った: arena は**静的確保 180,224 B / used 157,360 B**、起動直後の内部 DRAM の空き **132,039 B**（[M-89](../measurements.md#m-89) / [M-90](../measurements.md#m-90)）。⚠️ 当時の見積り「約 96 KB」（M-16）より大きいのは、ストリーミングの窓と int8 の作業領域を含むため |
| 参照実装をコードとして使えない | 中 | ⚠️ **当初「404」と書いたのは綴り間違い（C-024）。公式実装は実在するが GPL-3.0 で、MIT の本リポジトリには取り込めない。**論文の数値からの clean-room 再実装になる。`λ₂, λ_n, λ_Δ, λ_s` など**論文に書かれていないハイパーパラメータがある** |
| ~~**G2P の言語誤ルーティング**~~ | **構造的に消えた** | ラベル生成の経路を「漢字文 →[ホスト]→ 中間表現 →[kana_g2p]→ 音素ID」に変えた（[D-014](../decisions.md)）ので、`MultilingualPhonemizer` を通らない。旧 B-1（かな無し行 5.36% が中国語音素になる）は該当が無くなった |
| ~~**`prosody_features` の無警告ズレ**~~ | **構造的に消えた** | `prosody_features` を**一律ゼロに固定**した（[D-014](../decisions.md)。デバイスが A1/A2/A3 を供給できないので教師と条件を揃える。held-out 24 文の UTMOS に有意差なし p=0.72）。⚠️ **ゼロは「prosody 無し」ではない**（`prosody_proj(0) = bias` が concat される）。旧 B-2 は消えた |
| ~~**教師音声の品質ベースラインが低い**~~ | **測って決着** | 天井を測った（[M-10](../measurements.md#m-10) / [M-29](../measurements.md#m-29) / [M-50](../measurements.md#m-50)）: **実人間の日本語ですら SCOREQ 2.4983 / UTMOS 2.3047**。日本語では指標が較正されていないだけで、教師/人間比は SCOREQ 0.820 / UTMOS 0.758。**絶対値を英語モデルと比べない**という運用に変えた（[D-013](../decisions.md) / [D-020](../decisions.md)）。生徒の教師比 SCOREQ は **0.6444**（目標 0.55） |
| stale な `piper_train` の解決 | 中 | `.venv/site-packages/piper_train/` に v1.13.0 相当の古いコピーがあり、`sys.path.insert` を忘れると黙ってそちらが読まれる |
| ~~教師 ckpt と piper-plus の版の非互換~~ | **解消済み** | 実測の結果 v2.0 HEAD で missing 0 / unexpected 0 でロードでき、決定的推論も bit 一致した。§2.4 参照 |
| 音素表の世代差 (173 vs 185) と PUA エンコード | 中 | §2.4。ID 173 以上を渡すと範囲外。`ch`/`sh`/`ts` 等は PUA 経由でないと引けない |
| piper-plus v2.0 と公開 **base** ckpt の非互換 | 低 | Issue #616。`ayousanz/piper-plus-base` を使うなら `v1.13.0` へ checkout が必要。⚠️ **本プロジェクトの教師 ckpt は該当しない** — v2.0 HEAD に missing 0 / unexpected 0 でロードできる（上の行 / §2.4） |
| 集約スコアが欠陥を隠す | 中 | 論文が実際に踏んだ。音素クラス別プローブと聴取を最初から入れる（§3.3） |
| 評価セットの過大評価 | 中 | 論文で 1.35 の過大評価。テンプレート文を使わない（§3.6） |
| 567 K tier の品質が実用に足りない | 中 | SCOREQ 2.54 は教師 4.68 から大きく劣る。期待値の設定が必要（D1） |
| ESP32-S3 のメモリ制約 | 低〜中 | arena 約 289 KB + SIMD オペランドの内部 SRAM ステージングが必須 |

---

## 7. 参考リンク

- 論文: <https://arxiv.org/abs/2608.21378> / HTML 版 <https://arxiv.org/html/2608.21378v1>
- piper-plus: <https://github.com/ayutaz/piper-plus>
- piper-plus 事前学習済みモデル: <https://huggingface.co/ayousanz>
- Piper (upstream): <https://github.com/rhasspy/piper>
- VITS: Kim, Kong, Son, ICML 2021
- SCOREQ: Ragano, Skoglund, Hines, NeurIPS 2024
- UTMOS: Saeki et al., Interspeech 2022
- Parallel WaveGAN（multi-resolution STFT loss の出典）: Yamamoto, Song, Kim, ICASSP 2020
