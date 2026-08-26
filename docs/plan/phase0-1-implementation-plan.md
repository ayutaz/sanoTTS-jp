# saanoTTS-jp 実装計画

- 作成 2026-08-26 / 最終更新 2026-08-26（B-5 完了時点）
- 対象: `arXiv:2608.21378` の蒸留レシピの日本語適用（PoC・非配布）
- 上位ドキュメント: [`../README.md`](../README.md)（現在地）/ [`../decisions.md`](../decisions.md)（確定事項）/ [`../../CLAUDE.md`](../../CLAUDE.md)
- 本文書の位置づけ: **§0 が現行のロードマップ。§1 以降は検証タスク B-* と各 Phase の詳細。**
  未確定のものは `⚠️ 未検証`、済んだものは `✅` を付ける。反証された主張は採用しない。

---

## 0. 現行ロードマップ

```
✅ Phase 0   教師の動作確定             決定的推論が bit 再現、EMA 適用、6 チェック PASS
✅ B-0       G2P フットプリント          辞書路線は不成立 → 入力仕様を変更して解決
✅ D0-b      ESP32 メモリ収支            I2S 逐次出力で 96 KB / SRAM 残 416 KB。中止材料なし
✅ B-5       教師品質ベースライン         教師/実人間 = 0.758。「壊滅的」は誤りだった
✅ Phase A   ラベル生成の設計確定         A-1/A-3 確定、A-2 は実測で決着（prosody=zeros）
✅ Phase B   実装完了                    B-a/B-b/B-c/B-d 済み。B-e (本番実行) のみ vast.ai 待ち
✅ Phase C   実装完了                    4 段の学習ループがスモークテスト通過
▶  次        本学習 (vast.ai)            ← ラベル一括生成 → 1.4 M → 567 K
   Phase D   C99 コア + ESP32 実機
```

### Phase A: ラベル生成の設計を固める

**最優先の設計判断**: いま経路が 2 つあり噛み合っていない。

```
デバイス:   中間表現 ──[mora テーブル 951 B]──▶ 音素ID
ラベル生成: 漢字文   ──[MultilingualPhonemizer]──▶ 音素ID ──▶ 教師
```

蒸留では**生徒が学ぶ入力と、デバイスが実際に作る入力が一致していなければならない**。
ラベル生成も中間表現から始めるべきで、そうすると **B-1（かな無し行 5.36% が
中国語音素になる問題）が構造的に消える**。

| # | タスク | 完了条件 |
|---|---|---|
| **A-1** | ラベル生成の入力を中間表現に統一 | held-out 全件で「中間表現 → 音素ID」が漢字経路と一致するか、差分の規模を把握。B-1 が消えるかを判定 |
| **A-2** | prosody の扱いを決定 | 中間表現は A1/A2/A3 を持たない。実 prosody あり/なしで `dT` を比較し、生徒が phoneme ID だけから学習可能か判断 |
| ~~A-3~~ | ~~教師品質ベースライン~~ | ✅ 完了（B-5 / M-10 / D-013） |

### Phase B: ラベル一括生成（vast.ai）

テキスト 1.2 MB を持ち込み、向こうで 23,271 文（学習 20,946）のラベルを生成。
CPU 換算 1.1 時間、パックは fp16+int16 で 4.42 GB。SHA-256 と生成環境を manifest に固定。
残る **B-2 / B-3 / B-4 / B-6 〜 B-10** はこのフェーズで潰す。

### Phase C: 生徒の実装

**1.4 M（z-line）を先に**作る。日本語で蒸留レシピが機能するかを速く確かめるため。
Duration → Acoustic → Decoder → joint の順。その後 **567 K（c-line）**に落とし、
差分で実用性を判断する。詳細は §5 / §6。

**学習曲線を 512 / 5,000 / 20,946 行の 3 水準で取る。**
論文はデータ量について **2 点しか持っていない**（512 → 1.72 / 14,343 → 2.54、§IV-A）。
飽和点の記述が無いので「14,343 で足りる」とは言えない。日本語は音素インベントリが
大きくアクセント記号も入るので必要量が変わる可能性がある（逆にモーラ等時性は有利に働きうる）。
**追加コストは 1 水準あたり $1 未満**（M-18）なので、論文が埋めていない部分を実測する。

#### 学習コストの実測（M-18）

| epoch | RTX 4090 (×10 仮定) | spot $0.13/h |
|---:|---:|---:|
| 100 | 0.3 h | $0.04 |
| 300 | 1.0 h | $0.13 |
| 1000 | 3.4 h | $0.44 |

生徒が 567 K と極小なので**計算費用は実質ゼロ**。支配的なのはディスク（ラベルパック
4.42 GB）と λ 群の探索試行回数。月 $10〜50 の桁を見ておけば足りる。

論文に値の無い `λ₂, λ_n, λ_Δ, λ_s` はここでチューニングする。
**摩擦音ノイズ注入（式7）と音素クラス別スペクトル平坦度プローブは最初から評価に入れる。**

### Phase D: C99 コア + ESP32 実機

参照実装が 404 なので**推論コアは自前で書く**。ゴールデンテスト（fp 参照と
Pearson 相関 0.98 以上）が受け入れ条件。**RAM ピークとレイテンシの実測はここで初めて可能**。

### 主要リスク

| リスク | 影響 | 判明する時期 |
|---|---|---|
| 567 K で日本語 CER が実用外 | 成果物の価値に直結。英語実績 WER 14.8%、日本語は未知 | Phase C（1.4 M との差分） |
| C99 コアの実装量 | 参照実装が無い。Phase D の主コスト | Phase D |
| `λ` 群のチューニング | 論文に値が無い | Phase C |
| SCOREQ が未導入 | 論文の主指標が測れない | Phase C までに解決する |

---

## 1. 現在地と確定事項

### 1.1 一言で

> **⚠️ この計画書は B-0 より前に書かれた部分を多く含む。** その後
> スコープ（D-007）・入力仕様（D-010 / D-011）・環境（D-012）が確定し、
> 辞書枝刈りは実装対象から外れた。**現在地の要約は
> [`../README.md`](../README.md)、確定事項は [`../decisions.md`](../decisions.md) が正。**
> 本書は B-1 以降の検証タスクと Phase 2〜6 の見取り図として使う。

**教師は完全に手元にあり、決定的推論も再現できている。入力仕様も確定した。
残るブロッカーは「テキスト → 音素 + 韻律」の canonical 経路にある 2 つの無警告な欠陥。**

- 教師 ckpt は DL 済み・ロード済み・bit 決定的推論を実測済み（Phase 0 完了）
- 入力は**ひらがな + アクセント記号 + 無声化マーク**に確定。端末側 G2P は 951 B
  （D-010 / D-011、`scripts/kana_g2p.py`）
- ESP32 のメモリは I2S 逐次出力で約 96 KB。**中止材料は無い**（M-16）
- 一方で `MultilingualPhonemizer` → `PiperEncoder` には
  **日本語文が無警告で中国語音素になる欠陥**（B-1）と
  **韻律特徴が無警告でズレる欠陥**（B-2）が実在する。
  Phase 1 のラベル一括生成に入る前に、この 2 つを塞ぐのが最優先

> **B-1 は入力仕様の変更で消えている可能性がある。** 中間表現の生成は
> ホスト側でオフラインに行うので、そこがどの経路を通るかを先に確認すること。

### 1.2 実測で確定した事項（採用してよい）

| 項目 | 確定値 | 根拠 |
|---|---|---|
| piper-plus の git ref | **HEAD (`0f3b1a62`, dev) をそのまま使う。checkout も worktree も不要** | 学習時コード `95e74cb2` と HEAD の `vits/` 差分は推論数値に無影響（`mb_istft.py` は完全同一） |
| `v1.13.0` への checkout | **してはいけない** | ckpt は post-FiLM（`dec.cond.weight (512,512,1)` + `cond_layers`）。v1.13.0 では size mismatch |
| `import piper_train` の解決先 | **`uv run` を使う**（D-012 で解決）。uv の独立 venv には stale なコピーが存在しない | 旧: `PYTHONPATH` を先頭に置く必要があった。`.venv` の `site-packages/piper_train/` は v1.13.0 相当の stale コピー |
| 教師 ckpt | `~/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/c3f236e068b95356b871842b4ae7cec2a86c50ea/epoch=499-step=22000.ckpt` (927,048,022 B) | DL 完了済み |
| `hyper_parameters` | `num_symbols=173` / `num_speakers=1` / `num_languages=6` / `inter_channels=192` / `gin_channels=512` / `prosody_dim=16` / `sample_rate=22050` / `hop_length=256` / `use_sdp=True` / **`freeze_dp=True`** / **`max_phoneme_ids=400`** / **`max_spec_length=700`** | `torch.load` 実測 |
| 話者埋め込み | **`speaker_embeddings=None` で呼ぶ。`spk_tsukuyomi.npy` は渡しても bit 完全に無視される** | `spk_proj` / `emb_g` が state_dict に 0 件。None / npy / ランダム 192次元 の 3 通りで `audio`/`z`/`durations` が bit 一致 |
| 言語条件 | **`lid=0` (ja) 固定は必須。lid は焼き込まれていない** | `g = lang_emb` が enc_p / dp / flow / dec 全部に渡る。lid=1 で総フレーム 115→106、z も別物 |
| ロード | `VitsModel.load_from_checkpoint(ckpt, dataset=None, strict=False)` → missing 0 / unexpected 0 / `cond_migrated` 0 | 実測 |
| **EMA** | `ema_generator_state` (decay 0.9995, num_updates 11000, shadow 53 params) は **`load_from_checkpoint` では適用されない**。`apply_ema_shadow_params(model_g.dec, ...)` を **`remove_weight_norm()` の前に**明示的に呼ぶ | 適用有無で `yT` の SNR は 12.53 dB しかない。`zT` / `dT` は bit 一致 |
| 決定性 | `noise_scale=0` で `z_p == m_p` bit 一致、2 回実行で `audio`/`z` bit 一致 | 実測 |
| フレーム整合 | **`ceil(dT).sum() == zT.shape[1] == len(yT)/256`** がラベルパック 8 件すべてで厳密一致。`attn.argmax(-1)` も ceil 累積和と完全一致 | 実測。`src/python_run/piper_plus/timing.py` は ceil しないので**使わない** |
| 音素表 | ckpt 同梱 `config.json` の `phoneme_id_map` (185 entry) を使う。**有効 id は 0..172**。日本語在庫 65 エントリは全部 id ≤ 64 で安全。id 173..184 の 12 個は非日本語 (ɧ ɵ ʏ + 韓国語 PUA) | 実測 |
| 前処理タイプ | `config.json` の **`phoneme_type = "multilingual"`** / `language.code = "ja-en-zh-es-fr-pt"` / `language_id_map = {ja:0, en:1, zh:2, es:3, fr:4, pt:5}` | 実測（本計画作成時に再確認） |
| PUA の canonical ソース | **`piper_plus_g2p.encode.pua` の `CHAR2TOKEN` / `TOKEN2CHAR` (99 entry)**。`PiperEncoder` が内部で呼ぶ | `src/python/jp_phoneme_map.py` は id 表が壊れている（後述） |
| コーパス素材 | JSUT 7,696 + ROHAN4600 4,600 + ITA 424 + Common Voice ja 11,060 = 生 **23,780** 行 → NFKC 重複排除後 **23,271** ユニーク | 実測。論文の 14,343 行の 1.62 倍 |
| 発話長 | 教師実測で **平均 3.96 s/文**、実効 7.73 mora/s、frames/s = 86.13 | 無作為 250 行の決定的推論 |
| 生徒パラメータ逆算 | `Eρ = 192→64→40` の pointwise 2 層 = **14,952（代数的に一意）**。`Aβ` 非埋め込み部 = **192,000 ちょうど**、埋め込み次元 **48**（「30 エントリで +1,440」から直接） | 実測・全探索 |

### 1.3 CLAUDE.md / feasibility.md の要訂正箇所（Phase 0 の成果物）

> **✅ この表の訂正はすべて反映済み**（2026-08-26）。
> `docs/decisions.md` の C-001〜C-009 として恒久化してある。以下は経緯の記録。

| 場所 | 現状の記述 | 訂正内容 |
|---|---|---|
| `CLAUDE.md:59` / `:175-176`, feasibility `§2.4` | `speaker_embeddings=spk_emb` / 「話者を固定するのにそのまま使える」 | **誤り。`None` を渡す。npy は SECS 評価専用** |
| `CLAUDE.md:71-73`, feasibility `§2.4` | 「`v1.13.0` に checkout する」 | **不要。HEAD でそのまま載る**（さらに `#616/#621` で pre-FiLM 自動移行も入った） |
| `CLAUDE.md:210-213`, feasibility `§3.5` 付近 | 「必ず `src/python/jp_phoneme_map.py` の `PHONEME_TO_PUA` を経由すること」 | **誤り。canonical は `piper_plus_g2p.encode.pua`。`jp_phoneme_map.get_phoneme_id_map()` は 58 entry / max id 57 しか返さず、実パックの id（最大 87）と全く対応しない — 誤用すると音素ラベルが黙って総取り違えになる** |
| `CLAUDE.md` 「UTMOS」節 / feasibility `§3.7` | 「UTMOS は日本語データで訓練されている」 | **誤り。VoiceMOS2022 main=BVCC(英語) / OOD=BC2019(中国語)。日本語は含まれない。UTMOS も教師比で報告する** |
| feasibility `§2.4` | 「公開 ONNX の入力は `speaker_embedding[B,256]` で v2.0 の 192 チェックに抵触」 | **別モデルの話。ローカル `models/tsukuyomi.onnx` は 173 音素・pre-FiLM の旧系統で、その 256→512 射影は xavier 初期化のまま学習されていない死んだ入力** |
| `CLAUDE.md` 「S_ja」 | `S_ja = {..., A, I, U, E, O}` | **`A` / `E` / `O` は 23,271 行で 1 度も出現しない。`I` / `U` のみ** |
| feasibility `§2.4` の「config.json 一致」 | ckpt 選定根拠として `config.json` 一致を挙げる | **ローカル ONNX と ckpt は `phoneme_id_map` まで完全一致するので、config 一致は同一性の証明にならない**（実際、同一文で durations が 98 vs 115 と別物） |

---

## 1.5 【スコープ変更 2026-08-26】ESP32 が必須。ブラウザは対象外

**ブラウザ tier は piper-plus の WebAssembly で既に解決済みのため、本プロジェクトの成果物ではない**
（ユーザー判断）。**ESP32 で日本語が喋れなければプロジェクトに意味が無い。**

この変更が計画に与える影響:

| 項目 | 変更前 | 変更後 |
|---|---|---|
| 成果物 | 1.4 M quality (browser) を優先 | **567 K embedded (ESP32) が唯一の成果物** |
| 1.4 M の位置づけ | 本命 | **検証用の足場**（蒸留レシピが日本語で効くかを速く測り、567 K との差分を出すため） |
| オンデバイス G2P | 「音素ID入力に割り切る」で回避 | **回避不可。成立しなければプロジェクトが無意味** |
| D0（tier 決定） | 未解決ブロッカー | **決着済み** |

### B-0 オンデバイス日本語 G2P のフットプリント ✅ 測定完了 (2026-08-26)

**結論: 辞書枝刈り路線は不成立。入力仕様を変更して解決した。**

| 測定 | 結果 |
|---|---|
| 16 MB ボードに収まる最大辞書 | 60k表層 11.57 MB → 文単位一致 **73.0%** |
| 文単位 95% に必要な辞書 | **40.05 MiB** — 32 MB ボードにも入らない |
| C 実装のみ（Python 後処理層なし） | フル 103 MB 辞書でも音素列 95.4% / アクセント 84.0% |

→ **入力を「ひらがな + アクセント記号 + 無声化マーク」に変更**（D-010 / D-011）。
端末側 G2P は **mora テーブル 951 B + `ん` 異音規則 18 件**になり、
held-out で表現可能 96.40% / 往復一致 **100%** / 教師出力と **bit 完全一致**。

詳細: [`../research/b0-g2p-footprint.md`](../research/b0-g2p-footprint.md) /
[`../decisions.md`](../decisions.md) D-009〜D-011 /
実装: `scripts/kana_g2p.py`

**以下は測定時の記録**（辞書枝刈りは実装対象から外したので、再実行の予定は無い）。

実測済みの内訳（`build/share/open_jtalk/dic/`）:

```
sys.dic  103,082,017 B   lexsize = 788,923 エントリ (平均 131 B)
  ├ feature 文字列 67,242,425 B  ← 65.2%。品詞・活用など TTS に不要な情報が大半
  ├ darts trie     23,216,752 B
  └ token          12,622,768 B
matrix.bin 3,792,262 B   char.bin 262,496 B   unk.dic 5,690 B
```

TTS に必要なのは**読み・アクセント型・アクセント結合規則・最小限の品詞**のみ。
エントリ枝刈りと feature 削減は独立に効くので、線形概算では:

| 語数 | 素の概算 | feature を TTS 用に削減 |
|---:|---:|---:|
| 10,000 | 1.2 MB | 〜0.6 MB |
| 30,000 | 3.7 MB | 〜1.8 MB |
| 60,000 | 7.5 MB | 〜3.7 MB |

ESP32-S3 (16 MB flash) はアプリ + IDF を引いて 12 MB 前後残るので、**物理的には数 MB の
TTS 特化辞書が載る**。⚠️ ただし darts trie は語数に線形には縮まないので、**上表は概算**。

**検証タスク**:
1. NAIST-JDIC を語彙頻度で枝刈りし、feature を「読み + アクセント型 + アクセント結合型 + 品詞大分類」
   に削った TTS 専用辞書をビルドする。語数は 10k / 30k / 60k の 3 水準
2. 各水準で **実バイナリサイズ**を測る（線形概算の検証）
3. `data/splits/diverse_ja.tsv` と実運用想定文に対し、**フル辞書との音素列一致率**を測る
   — 辞書サイズ単体では判断できない。**「実文の何 % が正しく読めるか」が唯一の判定基準**
4. 未知語フォールバック（`unk.dic` の文字種ベース推定）が漢字複合語で何を返すか確認

**完了条件**: `reports/b0_dict_footprint.json` に語数 × {バイナリサイズ, 音素列一致率,
アクセント型一致率, 未知語率} の表が出て、**「N MB で実文の M % が正しく読める」と言い切れる状態**。

**判断分岐**:
- 一致率が実用水準（目安 95%+）で数 MB に収まる → **プロジェクト続行**
- 収まらない → **入力境界の再定義が必要**。かな入力限定 / 定型文プリコンパイル /
  ホスト側 G2P のいずれかに落とすか、プロジェクト自体を見直す

**→ 実際に「収まらない」となり、かな入力限定（D-010 / D-011）に着地した。**

---

## 2. ⚠️ 着手前に実測で潰す不確定事項

**このセクションが全部 GREEN になるまで Phase 1 のラベル一括生成に進んではいけない。**
論文が narrow test set で 1.35 の過大評価をした教訓、および集約スコアが sibilant 欠陥を隠した教訓から、**「検証を先に置く」を計画の原則にする**。

### B-1 【最優先】G2P の言語誤ルーティング ⚠️ 未検証（対策が）

**現象（実測で再現済み）**: 教師の `phoneme_type` は `multilingual` なので canonical な経路は `MultilingualPhonemizer(['ja','en','zh','es','fr','pt'])`。ところが `multilingual.py:420` が `context_has_kana` を**文全体で**判定し、`:163/:173/:187` が CJK を `"ja" if context_has_kana else "zh"` に振り分けるため、**かなを 1 文字も含まない行が丸ごと中国語になる**。

```
'慶應義塾大学文学部、仏文学科卒業。'
  ML: ['tɕʰ','iŋ','tone4','iŋ','tone1','i','tone4','ʂ','u','tone2', ...]   ← 北京語
  JA: ['k','e','[','e','o','o','g','i','j','u','k','u','d','a', ...]
'一個，二個，三個。'   ML: ['i','tone2','k','ɤ','tone4','ɚ','tone4', ...]   ← 北京語
'2025年7月17日木曜日。' ML: 数字が全部 drop され中国語化
```

**規模**: pool 23,271 行のうち **1,247 行 (5.36%) で JA と出力が食い違い、1,200 行 (5.16%) が日本語音素表 65 エントリ外のトークンを出す**（tone4 2622, tone1 2449, tone2 1498, ɕ 1090, ʂ 972 …）。
**危険なのは、符号化 id が最大 139 で `num_symbols=173` 未満のため例外も skip も起きず、そのまま中国語音声のラベルが生成されること。**

**さらに悪いこと**: 評価セットの供給元候補だった JSUT `countersuffix26` は **26 行中 25 行 (96.15%)** がこれに該当する。「1つ/1個/1本の読み分け」という日本語 TTS で最も繊細な軸に、日本語として読まれない文を丸ごと当てる設計になっていた。

**piper-plus 内部でも 2 経路が矛盾している**:
- 6 言語事前学習: `prepare_multilingual_dataset.py:340,346` が**発話の言語タグごとに** `get_phonemizer(language)`（ja なら `JapanesePhonemizer`）
- つくよみちゃん FT: `preprocess.py:950-958 phonemize_batch_multilingual` が **文全体を `MultilingualPhonemizer` に渡す**

FT の 100 文はすべてかな入りなので両者一致するが、蒸留用 23,271 行では 5.36% で分岐する。

**検証タスク**:
```bash
export PP=/Users/s19447/Documents/piper-plus
uv run python scripts/b1_probe_g2p_routing.py --pool data/interim/pool.tsv --out reports/b1_routing.json
```
**完了条件**:
1. `reports/b1_routing.json` に `{"ml_ja_divergent": N, "non_ja_token_rows": M}` が出る
2. **決定 D1 を下す**（下の 3 択から 1 つ、根拠つきで `docs/decisions/D1-frontend.md` に記録）
   - (a) `JapanesePhonemizer` に言語ピン留め（教師の FT 前処理とは食い違うが、FT の 100 文では両者一致するので実害は限定的、という仮説）
   - (b) `MultilingualPhonemizer` のまま、非日本語音素を出した行を除外
   - (c) `MultilingualPhonemizer` に渡す前にかなを 1 文字も含まない行を検出して (a) にフォールバック
3. **どの選択でも、`scripts/gate_ja_only.py` が全ラベル生成入力に対して「出力トークンが日本語 65 エントリ ∪ {`^`,`$`,`?`,`?!`,`?.`,`?~`} に閉じている」ことを assert する**。1 行でも漏れたらラベル生成を止める。

> ⚠️ 並行作業で書かれた `scratchpad/gen_teacher_labels.py` は「`MultilingualPhonemizer` が canonical」とコメントで断定している。D1 が決まるまでこのスクリプトを本番のラベル生成に使ってはいけない。

### B-2 【最優先】prosody_features の無警告ズレ ⚠️ 未検証（対策が）

**反証済みの誤り**: 「`zip(..., strict=True)` で長さ一致は構造的に保証される」は**成立しない**。
`PiperEncoder._convert_prosody` (`encoder.py:133-141`) が zip の**前に** `while len(result) < expected_len: result.append(None)` / `return result[:expected_len]` で長さを強制的に揃えるため、`:185-187` の `strict=True` は**原理的に発火しない**。

**本計画作成時の再現実測**:
```
'The quick brown fox、雨が降る。'
  tokens 34 / prosody 34 → raw_ids 31 (空白 3 個が drop) → ids 65 / prosody 65 (例外なし)
```
つまり **prosody が末尾側に 3 ずれたまま無警告で通る**。`preprocess.py:911-919` の長さチェックも長さが等しいので発火しない。

**prosody は実効性のある入力**（総フレームを 8〜9% 動かす。実 prosody 115 / ゼロテンソル 106 / None 119）ので、これは実害のあるデータ破壊。

**採用しない対策**: `PiperEncoder(strict=True)`。教師の学習時は `preprocess.py:821` が strict 引数を渡さない（= `strict=False`）ので前処理が食い違い、かつ**ラテン単語間の空白が `KeyError` になって複数語ラテン文が全滅する**（28 文中 5 文）。CLAUDE.md が要求する「英数字混在」軸を消す。

**採用する対策**: **token 段階で、`phoneme_id_map` に無いトークンを prosody ごとペアで除去する。**

```python
def strip_unmapped(tokens, prosody, id_map):
    keep = [(t, p) for t, p in zip(tokens, prosody, strict=True)
            if pua.map_token(t) in id_map or t in id_map]
    return [t for t, _ in keep], [p for _, p in keep]
```

**検証タスク**: 回帰テスト `tests/test_prosody_align.py`
**完了条件**: 以下が全部 PASS
```
pytest tests/test_prosody_align.py -q
# 1) ラテン混じり文で strip 後の (tokens, prosody) が ground truth と要素単位で一致
# 2) 純日本語 200 文で strip 前後の tokens が不変（= 過剰除去していない）
# 3) len(phoneme_ids) == 2*len(tokens_after_strip) + 3 が全文で成立
```

> **`prosody_features=None` は「prosody ゼロ」ではない。** `models.py:891-921` は None のとき `torch.zeros(...)` を**そのまま concat** するが、ゼロテンソルを渡すと `prosody_proj(0) = bias`（非ゼロ）が concat される。実測で総フレームが 115 / 106 / 119 と 3 通り全部違う。ラベル生成では必ず実 prosody を明示的に渡すこと。

### B-3 `fy` など音素表 OOV の無警告分解 ⚠️ 未検証（規模が）

**反証済みの誤り**: 「`fy` は約 30 行、`strict=True` で弾ける」。
実測は **55 行 / 55 出現**（rohan4600 53 / jsut loanword128 1 / cv sentence-collector 1）。そして `strict=True` でも**エラーにならない** — `encoder.py:108-125 _tokens_to_raw_ids` が `map_token('fy')` の返す未変換 2 文字列を 1 文字ずつ引くため、**`fy` が黙って `f`(53) + `y`(64) に分解される**。

**完了条件**: B-1 の `gate_ja_only.py` が `fy` を含む 55 行を検出してレポートに出す。方針は **(a) 該当行を除外**（PoC なので音素表拡張はしない。拡張は `id_maps.py` / `pua.json` / C++ / Rust / `docs/spec/pua-contract.toml` の同時更新と CI ゲート `check_pua_consistency.py` を要し、しかも ckpt 側の埋め込み表 173 エントリが埋まっているので新規 id を足すと ckpt と非互換になる）。

### B-4 長さフィルタの基準 ⚠️ 未検証

**採用しない**: 「mora 5〜100 で切る」。教師側の根拠が無い恣意的な値。
**採用する**: 教師の学習時制約 `max_phoneme_ids=400` / `max_spec_length=700`（`dataset.py:243-244` が超過発話を捨てる）。

実測: pool を `PiperEncoder` で符号化すると **p50=116 / p90=231 / p99=337 / max=503、400 超が 24 行**（jsut/basic5000 23, ita/emotion100 1）、300 超が 742 行。
フレーム換算では `max_spec_length=700` = 700×256/22050 ≈ **8.13 秒**。

**注意**: 調査が挙げた「rohan 先頭 300 行で phoneme_ids mean 306 / 7.0% が 400 超」と、検証者の「pool 全体で p50=116 / 400 超 24 行」は**符号化前トークン長 vs 符号化後 id 長（2N+3）の取り違えを含む可能性がある**。どちらが正しいか要再測。

**完了条件**:
```bash
python scripts/b4_length_hist.py --pool data/interim/pool.tsv --out reports/b4_len.json
# reports/b4_len.json に token_len / encoded_id_len / est_frames の 3 系列のヒストグラムが出る
# encoded_id_len > 400 の行数と、est_frames > 700 の行数が確定する
```
これが出るまで「1 万行を流す」判断はしない。

### B-5 教師音声の品質ベースライン ✅ 測定完了 (2026-08-26)

**結論: 「壊滅的」という当初の解釈は誤りだった。** 詳細は M-10 / D-013 / C-012。

当初は「教師 wav が SCOREQ 2.06 / UTMOS 1.62 で論文の 4.68 / 4.42 から乖離している」
ことを問題視していたが、**日本語の既知良品リファレンスを測っていなかった**のが誤り。

24 文 / 平均 5.16 秒 / 前後 0.3 秒パディング / canonical な音素化経路で測り直した:

| 対象 | n | UTMOS mean |
|---|---:|---:|
| 教師（合成） | 24 | **1.748** |
| **実人間音声**（つくよみちゃんコーパス = 教師の元データ） | 24 | **2.305** |

**実人間の日本語ですら 2.305 しか出ない。** UTMOS は日本語でスケールが圧縮されている
（同一エンジンで ES 3.51 / EN 2.97 / FR 2.23 / ZH 1.80）。

```
教師 / 実人間 = 0.758     ← これが正しい読み方
```

発話長との相関 r = −0.372 なので**短尺アーティファクトではない**。

**この spike で確定した評価方針**（D-013）: 品質は**教師比と人間音声比の両方**で報告する。
人間側の分母は教師の元コーパスを使う（話者・録音条件が揃う）。

再現: `uv run --extra eval python scripts/b5_teacher_baseline.py` →
`scripts/b5_measure_mos.py`。一次データは `reports/b5_teacher_baseline.json` /
`reports/b5_human_control.json`。

⚠️ **SCOREQ は未導入**（pip パッケージが見つからない）。UTMOS だけで判断しないこと。
論文の主指標は SCOREQ なので、Phase C までに導入経路を確保する。

### B-6 スペクトル平坦度プローブの窓設計 ⚠️ 未検証

**採用しない**（いずれも実教師データで反証された）:
- 「摩擦音の教師基準値 0.381」→ これは **fricative + affricate をプールした値**。`FRICATIVE` / `AFFRICATE` を別クラスにする推奨コードとは非互換。**fricative 単独は 0.3355 (n=16)、affricate は 0.4846 (n=7)**。
- 「n_fft=1024 は摩擦音より長く逆転する」→ 実教師 8 パックでは逆転しない。むしろ `n_fft=1024/guard=0` が推奨設定 `512/guard=1` より分離が良い（AUC 0.856 vs 0.835）。
- 「両端 1 フレーム除外 (guard=1)」→ 同じ `n_fft=512` の `guard=0` に全指標で劣る（ratio 1.633 < 1.787, Cohen's d 1.506 < 1.851, AUC 0.835 < 0.885）。日本語摩擦音の実測区間長は **1〜4 フレーム / mean 2.24**（14 フレームの摩擦音は 1 つも存在しない）なので、guard=1 は「隣接母音の漏れ込み防止」として機能せずサンプルを 3 割削っているだけ。
- 「power=2 は 4 倍鋭い」→ SFM は [0,1] に有界なので power=2 は平均を 0 側に圧縮するだけ。AUC は 0.839 → 0.835 とむしろ悪化。
- 「無声化母音 0.305 > 破裂音 0.297」→ **n=3、bootstrap 95%CI [0.0855, 0.4524]、P(devoiced>stop)=0.560**。コイン投げ。

**確かなのは 1 対比だけ**: fricative/affricate が vowel/nasal より高い（AUC 0.835–0.885）。

**検証タスク**: `sibilant_dense_ja` 評価セット（クラスごと最低 300 フレーム）を先に作り、その上で `(n_fft, guard, power)` のグリッドを**分離指標 (Cohen's d / AUC) で**選ぶ。平均値の比では選ばない。
**完了条件**: `reports/b6_flatness_grid.json` に `n_fft ∈ {256,512,1024} × guard ∈ {0,1} × power ∈ {1,2}` の 12 点それぞれの `(mean, CI, Cohen's d, AUC)` がクラス対ごとに出る。**採用する設定は AUC 最大のもの**、教師ベースラインはそこで凍結する。

### B-7 `s_v`（日本語 length scale）⚠️ 未検証

**採用しない**: 「`s_v` の初期値は 1.2 前後」。
**反証**: 根拠にされた raw duration 合計 42.37 は `'2025年7月17日木曜日。'` という **B-1 の中国語誤ルーティングで数字が全部落ちた壊れた文**の出力。正常 40 文で測り直すと `noise_scale_w=0.8 / 0` の比は **mean 1.179 / sd 0.124 / min 0.877 / max 1.705**。点推定に使えるばらつきではない。
**さらに根本的に**、この比は「SDP の確率的サンプルが決定的推論より何 % 長いか」であって、`d̂_i = clip_[1,80](round(s_v · r_i))` の生徒推論時較正係数 `s_v` とは**別物**。生徒は `nsw=0` のラベルで学習するので、この比を初期値にする論理は成立しない。

**正しい較正方法**: Phase 1 でラベルを全部生成したあと、`Σ ceil(dT)` と `Σ clip_[1,80](round(s_v · r_i))` の総フレーム比が 1 になる `s_v` をコーパス全体で最小二乗で解く。それまで `s_v = 1.0` を仮置きする。

**`clip_[1,80]` について確定していること**:
- **上限 80 は飽和しない**。実音素の `ceil(dT)` 最大は ONNX 40 文で 16 フレーム、実 ckpt パック 8 件で 8 フレーム（`_` PAD は 18）。
- **効いているのは下限 1 のほう**。250 発話**すべて**に `ceil(dT) <= 1` のトークンが存在する。教師は `ceil(w)` でフレーム割当するのに対し生徒は `round(·)` の床が 1 なので、**総フレーム長が系統的にずれる**。しかも `dur<1` の 57 個のうち `_` PAD は 25 個 (43.9%) だけで、残り 32 個 (56.1%) は実音素（`[`×5, `d`×4, `o`×3, `t`×3 …）。「パッドだけの話」ではなく実音素で直接効く。

### B-8 `_` PAD がフレームの約半分を占める ⚠️ 未検証（設計影響が）

`PiperEncoder._post_process` (`encoder.py:190-192`) が音素間に必ず PAD を挿入するため、**pack 全体 389 トークン中 191 個 (49%) が id 0 (`_`)**。しかも `_` は intersperse が一様でなく、読点で連続する（`000006.npz` は index 73,74 が両方 0）。

**設計判断が要る**（`docs/decisions/D2-pad-duration.md`）:
- 論文の `Dα` が `clip_[1,80](round(s_v·r_i))` を全トークンに適用するなら、実効トークン予算の半分が PAD に消える
- PAD の duration を別ヘッドで扱うか、PAD を除いた列で学習して推論時に再挿入するか
- プローブ側でも、音素の音響実現は `[pad, phone, pad]` にまたがるのに `phone` の 1〜4 フレームしか見ていない

### B-9 死んでいる音素が 11 個ある ⚠️ 未検証（含意が）

pool 23,271 行を通しても日本語 65 エントリのうち **`A E O a: i: u: e: o: N q zy` の 11 個が一度も出ない**。
- `N` は `_apply_n_phoneme_rules` が必ず `N_m` / `N_n` / `N_ng` / `N_uvular` に置換する
- 長音記号系と `q` / `zy` はこの OpenJTalk 辞書が出さない

**日本語テキストをいくら足しても埋まらない。** 生徒の埋め込み表サイズと「音素カバレッジ 100%」の主張の前提になるので、コーパス確定と同時に凍結する。

### B-10 教師の事前学習テキストとの重複 ⚠️ 未検証

汚染源として確定しているのは FT テキスト **VOICEACTRESS100** だが、教師は 6 言語 **497,519 発話**（日本語部分は MOE-Speech 20speakers）で事前学習されている。その書き起こしと pool の重複は**未検査**。丸暗記による過大評価は事前学習側からも来る。

**訂正**: VOICEACTRESS100 の除外対象は **100 文ではなく 102 文**。`voiceactress100` と `repeat500` のユニーク本文は各 100・共通 98・差分が各 2（`…遠隔管理している。` vs `…遠隔監視している。` / `…特殊な装飾キーである。` vs `…特殊な修飾キーである。`）。1 文字違いなので NFKC 重複排除では併合されない。**両サブセットを丸ごと除外するのが正しい運用**。

### B-11 `.venv` の stale install ✅ 解決済み (D-012 の uv 導入で消滅)

**採用しない**: `cd piper-plus && .venv/bin/pip install -e src/python`。
**反証**: editable install は既に `2026-08-24 16:27` 付で入っているのに効いていない。setuptools の finder が `sys.meta_path` に **append**（insert でなく）されるため標準 `PathFinder` が先に `site-packages/piper_train/` を拾い、しかもその実体は**別ディストリビューション `piper_plus_workspace 1.12.0` の所有物**（`grep -l "piper_train/vits/models.py" */RECORD`）なので `piper-train 2.0.0` を入れ直しても消えない。

**採用する**: **`uv` の独立 venv**（D-012）。piper-plus を `[tool.uv.sources]` の
path 依存 (editable) で参照するので、stale なコピーが最初から存在しない。
旧案の `PYTHONPATH` / `sys.path.insert` も効くが、`uv run` を使えば不要
（既存スクリプトの `sys.path.insert` は冗長だが害はない）。

**完了条件**（全スクリプトの冒頭でこれを assert する）:
```bash
export PP=/Users/s19447/Documents/piper-plus
uv run python - <<'EOF'
import piper_train.vits.models as M, inspect
import piper_train.vits.mb_istft as m, piper_train.vits.commons as c
assert M.__file__.startswith("/Users/s19447/Documents/piper-plus/src/python/"), M.__file__
assert "cond_layers" in inspect.getsource(m)
assert hasattr(c, "normalize_checkpoint_state_dict")
print("OK: src/python が解決先")
EOF
```

---

## 3. Phase 0 — 教師の動作確定

**目標**: 「このコマンドがこの出力を出す」レベルで教師の挙動を凍結し、Phase 1 のラベル生成器が依拠する前提をすべて実測値に置き換える。

### P0-1 環境の分離と固定

piper-plus は**読み取り専用**（`checkout` / `commit` / ファイル編集の禁止）。ラベル生成は piper-plus の `.venv` を read-only で起動、評価は saanoTTS-jp 側の独立 venv。

```bash
# ラベル生成側（piper-plus venv を read-only 起動）
export PP=/Users/s19447/Documents/piper-plus
alias ppy="uv run python"   # D-012: Python は uv 経由

# 評価側（完全に別 venv）
python3.12 -m venv /Users/s19447/Desktop/saanoTTS-jp/.venv-eval
```

**完了条件**:
- §B-11 の assert スクリプトが `OK: src/python が解決先` を出す
- `cd $PP && git status --porcelain` が **0 行**、`git rev-parse HEAD` が `0f3b1a62…`、`.git/worktrees` が**存在しない**
- 全作業スクリプトの CI 前提として、この 3 点を確認する `scripts/guard_piper_plus_readonly.sh` が exit 0

> **学習時コードの保険**: `95e74cb2` は dangling commit（`git branch -a --contains` が 0 行、`git cat-file -t` は `commit`）。piper-plus 側で `git gc --prune` が走ると消える。必要なら今のうちに `git archive`（`.git` に一切書き込まない。`worktree` は `.git/worktrees/` を作るので不可）で `vendor/piper-plus-95e74cb/` に抽出しておく。ただし HEAD と推論数値差分がゼロであることは実測済みなので、**現時点では不要**。

### P0-2 ckpt ロードと EMA 適用の確定

`scripts/phase0_verify_teacher.py` を**書き直す**。現行版は `speaker_embeddings=speaker` を渡し、EMA を適用せず、`prosody = torch.zeros(1, len(ids), 3)  # TODO(Phase 1)` のままなので、**5 チェックすべて PASS するのに「話者埋め込みが効いている」という誤った確信を与える**。

```python
def load_teacher(ckpt_path):
    m = VitsModel.load_from_checkpoint(ckpt_path, dataset=None, strict=False)
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False, mmap=True)
    ema = ck.get("ema_generator_state")
    assert ema and "shadow_params" in ema
    n = apply_ema_shadow_params(m.model_g.dec, ema["shadow_params"])   # ★ remove_weight_norm の前
    del ck
    m.eval(); g = m.model_g
    with torch.no_grad():
        g.dec.remove_weight_norm()
    assert not hasattr(g, "spk_proj") and not hasattr(g, "emb_g")
    return g
```

**完了条件** — `ppy scripts/phase0_verify_teacher.py` が以下を全部出す:
```
[OK] resolved piper_train from src/python
[OK] state_dict: missing 0 / unexpected 0 / cond_migrated 0
[OK] no spk_proj, no emb_g            (single-speaker)
[OK] EMA applied 53/53, skipped 0     (before remove_weight_norm)
[OK] speaker invariance: audio/z/durations bit-identical for (None | spk_tsukuyomi.npy | random-192)
[OK] determinism: 2 runs bit-identical; z_p == m_p at noise_scale=0
[OK] frame identity: ceil(dT).sum() == zT.shape[1] == len(yT)//256
[OK] lid sensitivity: lid=1 changes frames (=> lid=0 must be pinned)
```

> `load_from_checkpoint` は実行時に `Optimizer state is in the pre-FiLM layout ... discarded.` を出す。**推論のみなら無害**。この ckpt から学習を再開する場合だけ問題になる。

### P0-3 prosody の実効性と freeze_dp の含意の記録

**測定タスク**: 同一文で `prosody=実A1/A2/A3` / `zeros(1,T,3)` / `None` の 3 通りを比較。

**既知の実測**: `'今日はいい天気ですね。'` で総フレーム **115 / 106 / 119**。音素あたり最大 +35%、`prosody=None` は「ゼロ」ではない。

**記録すべき含意（`docs/notes/teacher-properties.md`）**:
- **`freeze_dp=True`**: 教師 ckpt は Tsukuyomi FT の間 duration predictor を凍結しており、dp の重みは **571 話者・6 言語の v7 multi-speaker base (`epoch=32-step=216326.singlespk.ckpt`)** のまま。つまり蒸留の `dT` は「つくよみちゃんに適応していない多言語 DP」の出力。論文の `Dα` (36,164) が何を模倣することになるのか、CLAUDE.md が挙げるアクセント型ミニマルペア評価がそもそも教師側で成立するのかに直結する。
- **`emb_lang` unify**: `export_onnx.py:495` の `should_unify_emb_lang(None, 1, 6)` は `True` を返し、単一話者×多言語の ONNX 化では `emb_lang[0]` が全言語にコピーされる。`lid=0` = ja なので日本語ラベルに限れば no-op だが、**`lid` を 0 以外にした瞬間 ckpt 教師と配布 ONNX 教師が食い違う**。

**完了条件**: `reports/p0_prosody_sensitivity.json` に 3 条件 × 20 文の総フレーム数が出て、`docs/notes/teacher-properties.md` が上記 2 点を記載。

### P0-4 アクセント型ミニマルペアが教師側で成立するかの確認

**確定していること**: アクセント記号は音素**トークン列の中に inline で**入り、実際に区別される。
```
橋が   h a [ sh i ] g a      箸が   h a ] [ sh i g a      端が   h a [ sh i g a
雨が   a ] [ m e g a          飴が   a [ m e g a
神が   k a ] [ m i g a        紙が   k a [ m i ] g a
今から i ] [ m a k a r a      居間から i [ m a ] k a r a
雲が = 蜘蛛が k u ] [ m o g a  （実際に同アクセント）
```
担ぎ文を揃えた『箸で食べる』vs『橋で食べる』では `durations` も総フレーム（84 vs 83）も変わる = **モデル出力レベルでも区別される**。

**⚠️ ただし平板型（0 型）には `]` が挿入されない**（飴、端）。「`]` の位置比較だけでピッチ評価の正解ラベルになる」は成立せず、**「核なし」を明示的な 1 クラスとして扱う設計が要る**。

**完了条件**: `eval/sets/minimal_pairs_ja.tsv` の 20 ペアそれぞれについて、教師の `(tokens, ceil(dT), 核位置 or NONE)` を出力した `reports/p0_minimal_pairs.json` が存在し、**平板型メンバーが `NONE` として正しくラベルされている**。

### P0-5 ラベル生成の最小疎通（8 文 → 50 文）

**入出力の確定値**（実測済み、これが再現すれば疎通 OK）:
```
x=[1,T] int64 / x_lengths=[1] int64 / lid=[1] int64 / prosody=[1,T,3] int64
→ audio=[1,1,N] f32, attn=[1,1,T_frames,T_text] (0/1), y_mask, z=z_p=m_p=logs_p=[1,192,T_frames], durations=[1,T_text] f32 (ceil 前)
'今日はいい天気ですね。' → T_text=49 → z=(1,192,115) / audio=(1,1,29440) / attn=(1,1,115,49)
29440 == 115*256、attn.sum(frames)[:5] == ceil(durations)[:5] == [4,2,2,2,2]
durations[:5] = [3.7493, 1.4884, 1.1778, 1.2604, 1.4837]
```

**完了条件**:
```bash
ppy scripts/gen_teacher_labels.py --texts eval/sets/smoke50_ja.txt --out data/packs/smoke/ --save-audio
# 期待出力:
#   teacher: n_vocab=173 n_languages=6 prosody_dim=16 inter_channels=192
#   gate_ja_only: 50/50 pass (non-JA tokens: 0)
#   prosody_align: 50/50 pass
#   done: 50 utts, N frames, RTF ~0.066
python scripts/verify_packs.py data/packs/smoke/
#   [OK] 50/50: ceil(dT).sum() == zT.shape[1] == len(yT)//256
#   [OK] 50/50: frame_tok == cumsum(ceil(dT)) の割り当てと完全一致
#   [OK] phoneme_ids.max() < 173
```

**`gen_teacher_labels.py` の既知の欠陥（修正必須）**:
| 欠陥 | 修正 |
|---|---|
| `assert len(ids) == len(pf)` が `_convert_prosody` のせいで**常に真＝無意味** | B-2 の token 段階 strip + 回帰テストに置換 |
| `m_p` を計算・返却しているが npz に保存していない | 保存する（`z_p == m_p` を下流で使えるように） |
| skip 行があると出力ファイル名に穴が空き進捗もズレる | 連番カウンタを入力行番号から分離 |
| `prosody` を int16 で保存（モデル入力は int64） | 読み戻し時のキャストを関数に隠蔽 |
| `PiperEncoder(strict=True)` を使っている | B-2 に従い `strict=False` + 明示 strip |
| EMA 未適用 / `speaker_embeddings` の扱い | P0-2 の `load_teacher()` に置換 |

**`phoneme_tokens_full` の追加は不要**（反証済み）。`phoneme_ids`（49 要素、`frame_tok` の値域と一対一）が既にあり、`config.json['phoneme_id_map']` を反転すれば復元できる。逆引きは **`piper_plus_g2p.encode.pua.CHAR2TOKEN` (99 entry)** を使う（`jp_phoneme_map.PHONEME_TO_PUA` は 22 entry しかなく 173 symbol 中 55 個を解決できない）。
なお `phoneme_tokens`（23 要素の de-pad 版）から intersperse で復元する素朴な方法は**失敗する**（`000006.npz` は読点で `_` が連続し 1+2*64+2=131 ≠ 130）。

### P0-6 教師ラベルの健全性ゲート（Phase 1 に進む条件）

論文の教訓（narrow set で 1.35 の過大評価 / 集約スコアが sibilant 欠陥を隠した）を、**ラベル生成の前**に反映する。

**完了条件** — 以下を全部満たしたら Phase 1 へ:
1. B-1〜B-4 が GREEN
2. `reports/b5_teacher_baseline.json` が `|ckpt_teacher - public_onnx| < 0.3`（SCOREQ / UTMOS 両方）
3. **読み検証 oracle が通る**: JSUT `jsut-label/text_kana/basic5000.yaml`（4.2 MB、DL 済み）+ ITA / ROHAN の第 2 フィールド（カタカナ読み）= **合計 5,000 + 4,600 + 424 行の読み正解データが手元にある**。50 文サンプルで教師の音素列を oracle 読みと突き合わせ、一致率を出す。これは B-1 の中国語化も難読語も一括で検出できる（現在の「読みが付かない行の検出」は pron が `、` のケースしか拾わず、**自信を持って間違って読む**ケースを素通りする）。
4. 教師音声 20 本以上（3 秒以上）を聴取し、つくよみちゃんの音色として妥当と判断

---

## 4. Phase 1 — ラベル生成パイプライン

### P1-1 コーパス調達

**確定した調達計画**（すべて実測でダウンロード可能を確認済み）:

| Tier | ソース | 行数 | ライセンス | 担う軸 | 入手 |
|---|---|---:|---|---|---|
| T1-a | **ROHAN4600** | 4,600 | **CC0 / PD** | モーラバランス、助数詞 1–10・月 1–12、疑問文 5% | curl 1.1 MB |
| T1-b | **ITA** (emotion100 + recitation324) | 424 | **PD** | 音素バランス、稀モーラ (ツァ/テュ/フュ/ヴィ) | curl 71 KB |
| T1-c | Common Voice ja | 11,060 | CC0 (要検証) | 口語・Web | curl 866 KB |
| T1-d | JSUT ver1.1 transcript (9 subset) | 7,696 | subset 別 CC-BY-SA / precedent130 は PD | 常用漢字、旅行、判例、オノマトペ、外来語 | **HTTP Range 抽出 808 KB**（2.7 GB zip を落とさない） |
| | **小計（NFKC 重複排除後）** | **23,271** | | | 約 2.7 MB |
| T2 | 合成ギャップ埋め | 1,500–2,000 | 自作 | `?!` `?.` `?~`、金額、日付時刻、英数字混在、ひらがなのみ、アクセント MP | 自作 |
| **除外** | shunk031/livedoor-news-corpus | — | **CC-BY-ND-4.0（改変禁止）** | — | — |

**判断: T1 だけで論文の 1.62 倍。T3（青空文庫 / jawiki）は不要。**

**⚠️ Common Voice sentence-collector の実態**（Tier 表の「口語・Web・文学」という説明は実態と合わない）:
- pool の 40.7% を占める 9,469 行のうち **6,307 行 (66.6%) が文末句読点を持たない断片**
- **1,110 行がかなを 1 文字も含まない**（= B-1 で全部中国語）
- **958 行 (pool 全体の 4.1%) が「◯◯県◯◯市」だけの裸の地名**（福岡県 57, 愛知県 52, 東京都 48 …）
- 平均 20.6 文字

CLAUDE.md が「テンプレート文は使わない」と明記しているのに、実質テンプレート生成された地名リストが最大ソースの 1 割を占める。**D3: CV をどこまで使うかの判断**（`docs/decisions/D3-corpus-mix.md`）。

**⚠️ CV のライセンス根拠が最弱なのに最大シェア**: repo は MPL-2.0、`server/data/ja/` に個別 LICENSE 無し（3 URL とも 404 実測）。`docs/SENTENCES.md` にも文そのものを CC0 とする明示は無い。**PD/CC0 だけの「配布可能サブセット」（ROHAN 4,600 + ITA 424 + JSUT precedent130 + 合成分 ≈ 7,000 行）を最初から別 tag で管理する。**

**完了条件**:
```bash
bash scripts/fetch_corpus.sh          # 4 ソース DL + JSUT の Range 抽出
python scripts/build_pool.py --out data/interim/pool.tsv
# 期待: RAW 23,780 / UNIQUE(NFKC) 23,271 / dropped 509
#   by source: jsut 7,189 / rohan4600 4,600 / ita 422 / cv 11,060
```

> `scripts/remote_zip_extract.py` は `requests` 依存で **zip64 を明示的に非対応**にしている。JSUT が 4 GB 超や ZIP64 で再パッケージされたら落ちるので、失敗時に全体 DL へフォールバックするパスを書いておく。

### P1-2 前処理パイプライン（実測で決まった順序）

```
[1] ID / 読みフィールド分離   "ID:本文" または "ID:本文,カタカナ読み"
[2] ROHAN のルビ除去          re.sub(r"\(([ぁ-んァ-ヶー]+)\)", "", text)
                              → 4,600 行で括弧の残骸 0 件を実測
[3] 制御文字除去              phonemizer の _sanitize_input と同じ（>= " " のみ残す）
[4] ★NFKC は「重複排除キー」だけに使う。教師に渡す本文は正規化しない★
      理由: 「！？」→「!?」で ?! 判定が壊れ、「？。」→「?。」で疑問判定が $ に落ちる
      （「？！」→「?!」と「？～」→「?~」は NFKC 後も残る。壊れるのは記号が先に来る順序）
      合成文は最初から ASCII の ?! / ?. / ?~ で書けばこの問題を回避できる
[5] 重複排除                  key = NFKC(text).strip()  → 23,780 → 23,271 (-509)
[6] ★汚染除去★               VOICEACTRESS100 = voiceactress100 + repeat500 の
                              「両サブセット丸ごと」（和集合 102 文。100 ではない）
[7] ★長さフィルタ★           encoded_id_len <= 400 (max_phoneme_ids) かつ
                              est_frames <= 700 (max_spec_length ≈ 8.13 s)
                              ※ B-4 の測定結果で確定する
[8] ★OpenJTalk 読み落ち検出★ NJD ノードで pos=="記号" and pron in ("、","") and mora_size==0
                              実測 31/23,271 行 (0.13%)。大半は稀漢字（腔・閃・勃・禕・煖・胞・
                              療・哉・孺・憑・墜・孕）と ＝ ／ ＼ ――
[9] ★言語ルーティング検査★   B-1。出力トークンが日本語 65 エントリに閉じているか
[10] ★音素表 OOV 検査★       B-3。fy を含む 55 行を明示的に検出して除外
[11] ★prosody 整合検査★      B-2。token 段階 strip 後の (tokens, prosody) がペアで一致
[12] ★読み oracle 照合★      jsut-label / ITA / ROHAN のカタカナ読みと突き合わせ
[13] ラベル生成失敗検出       infer() の例外 / NaN / 全ゼロ音声 / dT 総和がモーラ数から乖離
                              → manifest に理由付きで記録して除外（論文の "audited" 方針）
```

**完了条件**: `python scripts/preprocess_pool.py --pool data/interim/pool.tsv --out data/interim/clean.tsv --manifest data/interim/reject.jsonl` が、**各段階の除外行数を内訳つきで出す**。`reject.jsonl` は 1 行 1 理由。

### P1-3 合成でギャップを埋める（T2, 1,500–2,000 行）

pool の実測カバレッジが薄い／ゼロの軸だけを埋める。**学習側の 10% を上限**（論文の 512 行テンプレート失敗を再現しないため）。

| 軸 | 現状 | 目標 | 備考 |
|---|---:|---:|---|
| **`?!` `?.` `?~`** | **0 件** | 各 +300 | **最優先。必ず ASCII で書く**（全角は NFKC で壊れる） |
| `?`（プレーン） | 391 (1.68%) | — | pool から |
| 金額 | 100 (0.43%) | +300 | `1,980円` `税込3,300円` `12万5000円` |
| 日付・時刻 | 429 (1.84%) | +300 | 各月・各曜日を網羅 |
| 数詞と助数詞 | 973 (4.18%, 37 種) | +300 | **読みが変わる境界を必ず入れる**（3本=さんぼん、6本=ろっぽん、8本=はっぽん、1杯=いっぱい、3杯=さんばい） |
| 英数字混在 | 176 (0.76%) | +200 | ⚠️ 読みは D1 に依存（下記） |
| ひらがなのみ | 136 (0.58%) | +200 | 子ども向け文体 |
| アクセント MP | 0 件 | +100 | 学習用。評価用は別途 40 文 |

> **⚠️ 英数字混在の読みは D1 次第で逆転する（未確定）。** `JapanesePhonemizer` では ASCII 語は 1 文字ずつアルファベット名（`christmas` → シー・エイチ・アール…）だが、`MultilingualPhonemizer` では英語読み（`k ɹ ˈɪ s m ə s`）。`IDはA1B2C3です` は ML で `ˈɪ d w # a ə n ɛ t s w ɪ b ɛ k` と破綻する。**D1 が決まるまでこの軸の文は書かない。**

**その他の pool 実測カバレッジ**: 漢字混じり 98.63% / カタカナ 42.81% / 約物 3.50% / アラビア数字 1.84%。

### P1-4 学習 / 評価分割

**評価セット `diverse-ja`（目標 300 文）** — 論文の diverse24 の日本語版。24 文は日本語には足りない。

**絶対除外リスト**:
- **VOICEACTRESS100 全 102 文**（`voiceactress100` + `repeat500` 丸ごと） — 教師 ckpt の FT テキストそのもの
- 学習に使った行そのもの（NFKC キーで照合）
- ⚠️ B-10 が終わるまで、MOE-Speech 事前学習テキストとの重複は**未確認**のまま残る

| 軸 | 文数 | 供給元 |
|---|---:|---|
| 漢字混じり一般 | 30 | ITA recitation324 held-out |
| ひらがなのみ | 20 | 自作 |
| カタカナ語 | 25 | JSUT loanword128 held-out |
| 英数字混在 | 20 | 自作（D1 確定後） |
| 数詞＋助数詞 | 30 | **⚠️ JSUT countersuffix26 は使わない**（26 行中 25 行が中国語ルーティング。`，`→`、` 置換でも直らない — 原因は行内にかなが 1 文字も無いこと）。ROHAN 助数詞文 + 自作 |
| 日付・時刻・金額 | 25 | 自作 + pool |
| 約物 | 20 | pool |
| オノマトペ | 20 | JSUT onomatopee300 held-out |
| 疑問文 `?` | 15 | pool |
| 疑問文 `?!` `?.` `?~` | 各 10 = 30 | **自作必須**（pool に 0 件。⚠️ `evaluation_texts_ja.txt` の 31 文も 4 つとも素の `？` で、この穴埋めにはならない） |
| **アクセント型 MP** | 40 (20 ペア) | **自作必須**。橋/箸/端、雨/飴、神/紙、牡蠣/柿、今/居間、花/鼻、酒/鮭。必ず助詞「が」等を後続させる。**平板型メンバーを「核なし」クラスとして明示** |
| 摩擦音・無声化母音密集 | 25 | pool から「です/ます/した/しつれい/すこし」高頻度文 |
| 長文（p95 以上） | 20 | pool |

`scripts/evaluation/evaluation_texts_ja.txt` の 31 文（アクセント / 疑問 / 数字 / カタカナ / 擬音語の軸で書かれている）を**種**にして拡張するのが最短。

**学習セット**:
- T1 pool 23,271 − diverse-ja 300 − 除外分 ≈ **22,900 行**
- 論文と同条件の比較のため **14,343 行のサブセットも別途切る**（ソース比率を保った層化サンプリング）
- 「23K vs 14.3K vs 512」の 3 点でスケーリングを見られるようにしておく = 論文 §1.5 の再現

**完了条件**:
```bash
python scripts/build_splits.py --clean data/interim/clean.tsv --out data/splits/
# data/splits/{train_23k.tsv, train_14343.tsv, train_512.tsv, diverse_ja.tsv,
#              minimal_pairs_ja.tsv, sibilant_dense_ja.tsv} が生成
python scripts/check_leakage.py data/splits/
#   [OK] diverse_ja ∩ train_* = 0 rows (NFKC key)
#   [OK] VOICEACTRESS100 (102 rows) not in any split
#   [OK] diverse_ja covers 13/13 axes, min 15 rows/axis
```

### P1-5 一括ラベル生成

**性能とサイズの見積り（実測ベース。以前の見積りは 18% 過大だった）**:

| 構成 | 発話数 | 音声時間 | yT int16 | zT fp16 | cT 40ch fp16 |
|---|---:|---:|---:|---:|---:|
| 論文同等 | 14,343 | 15.8 h | 2.50 GB | 1.88 GB | 0.39 GB |
| T1 pool 全体 | 23,271 | **25.6 h** | **4.06 GB** | **3.05 GB** | 0.63 GB |

- 実測 RTF **0.066**（Apple Silicon CPU シングルスレッド）→ 23,271 行で実時間 **約 2 時間**
- **`yT` は int16 の wav 別置き + npz にはパスだけ**（論文の `L_G` はマルチ解像度 STFT を学習中に毎回取る前提なので lazy load でよい）
- `zT` を fp32 で持つと倍。**fp16 で保存する**
- ⚠️ `--save-audio` で float32 を npz に入れると実測 **142.8 kB / audio-second** = 25.6 h で 13 GB。使わない

**保存フォーマット**（`data/packs/{split}/{seq:06d}.npz`）:
```
text / phoneme_ids (int32) / prosody (int16) / dT (float32) / m_p 相当の zT (float16 [192,T])
frame_tok (int32) / yT_path (str, 別置き int16 wav) / src_id / lang_id
+ channel_stats.npz: mu_T[192], sigma_T[192], n_frames
+ index.jsonl: 1 行 1 発話のメタ（source, encoded_len, frames, seconds, reject_reason=null）
```

**`μ_T` / `σ_T` は必ず 1 パスで集計して同梱**。実測で `σ_T` は **0.041〜9.305（230 倍差）**なので、`L_c` の `N_T` 正規化を省くと高分散チャネルが loss を支配する。c-line なら `Eρ` を通した後の 40ch 版も別に保存する。

**完了条件**:
```bash
ppy scripts/gen_teacher_labels.py --texts data/splits/train_23k.tsv --out data/packs/train_23k/ --workers 4
python scripts/verify_packs.py data/packs/train_23k/ --strict
#   [OK] 23,xxx/23,xxx: ceil(dT).sum() == zT.shape[1] == wav_frames
#   [OK] 0 rows with non-JA phoneme tokens
#   [OK] 0 rows with phoneme_ids >= 173
#   [OK] 0 NaN / all-zero audio
#   [OK] channel_stats: sigma_T min/max = 0.0xx / 9.xx, n_frames = ...
#   [WARN] rejected: N rows (see index.jsonl)
```

### P1-6 `s_v` の較正と duration 統計

ラベルが全部揃ってから実施（B-7 参照）。

**完了条件**: `reports/p1_duration_stats.json` に以下:
- `dT` のヒストグラム（生 float / `ceil`）、`frac(dT < 1)`（8 文サンプルでは 14.65%）
- `ceil(dT)` の最大値 → `clip_[1,80]` の上限飽和が実際に起きないことの確認
- `_` PAD とそれ以外の duration 分布を**分けて**（B-8）
- `Σ ceil(dT)` と `Σ clip_[1,80](round(s_v · r_i))` の総フレーム比を 1 にする `s_v` の最小二乗解

---

## 5. Phase 2 以降の見取り図

### D0: ターゲット tier ✅ 決着済み (2026-08-26)

**成果物は 567 K embedded c-line のみ**（D-007）。ブラウザは piper-plus の
WebAssembly で解決済みなので対象外。

| tier | Params | 論文 SCOREQ | 本プロジェクトでの位置づけ |
|---|---:|---:|---|
| **embedded c-line** | **0.567 M** | 2.54 | **唯一の成果物**（ESP32） |
| quality z-line | 1.396 M | 4.09 (β=0) / 3.92 (β=6) | **検証用の足場**。日本語でレシピが効くかを速く測り、567 K との差分を出すためだけに作る |

順序としては **1.4 M を先に**作る。検証サイクルが速く、c-line 特有の `Eρ` / 40ch
ボトルネックを後回しにできるため（z-line は hinge adversary（式4）が追加になる）。
ただし**成果物は 567 K** であることを見失わないこと。

**判断を先送りできる分割**: `zT` / `dT` は EMA 非依存で bit 一致するので、
c-line / z-line ターゲットを先に固め、`yT`（EMA の有無で SNR 12.53 dB 差）の
方針を後回しにする進め方が取れる。

### D0-b: メモリ ✅ 見積もり完了 (2026-08-26)

I2S 逐次出力なら arena **約 96 KB**（SRAM 512 KB のうち 416 KB が残る）。
フラッシュは重み 664 KB + G2P 951 B。**メモリを理由に中止する材料は無い**（M-16）。
実機測定は C99 コアと量子化済み生徒が出来てから。

### Phase 2: Duration `Dα`

- 損失: `L_d = Huber_0.25(ℓ̂, log dT) + λ_T [log Σ r_i − log Σ dT_i]²`
- **完了条件**: 教師 `dT` に対する held-out の総フレーム相対誤差 < 3%、音素あたり Huber < (教師 `dT` の分散で正規化して) baseline 比 X 以下
- **必須のチェック**: `minimal_pairs_ja` で、教師が区別している 20 ペアのうち生徒が何ペアを区別できるか（`ceil(d̂)` の系列が異なるか）。**平板型は「核なし」として正しく扱われているか**
- **設計判断 D2**（B-8）: `_` PAD の duration をどう扱うか
- **設計判断 D4**: アクセント記号トークンだけで足りるか、A1/A2/A3 の 3 スカラーを足すか。**コストは `Conv1d(3, 32, k=1)+bias = 128 params`（36,164 の 0.35%）** なので、評価で必要になったら即入れてよい

### Phase 3: Acoustic `Aβ` + c-line

- 損失 (式3): `L_c = ‖ĉ−cT‖₁ + λ₂‖·‖₂² + λ_n‖N_T(ĉ)−N_T(cT)‖₁ + λ_Δ‖Δĉ−ΔcT‖₁ + λ_s L_stat`
- **⚠️ `λ₂, λ_n, λ_Δ, λ_s` は論文に値が無い。チューニング対象**
- **完了条件**: oracle-decoder 経路（教師 `zT`/`cT` → 生徒 `Gγ`）と full-student 経路で平坦度を比較し、劣化の帰属先が特定できる

### Phase 4: Decoder `Gγ` + joint

- 損失 (式5/6)。公開重み `(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)`
- `R` = FFT {512, 1024, 2048} × hop {128, 256, 512}
- **⚠️ 未確定**: iSTFT のフレーミング規約（§6 参照）
- **完了条件**: 3 経路（teacher / oracle-decoder / student）の平坦度が `teacher ≈ oracle > student` か `teacher > oracle` かで欠陥の帰属が言える

### Phase 5: 摩擦音ノイズ注入 `β` の決定（式7）

```
z̃_{t,k} = ẑ_{t,k} + 1[x_t ∈ S_ja] · β · σT_k · ε_{t,k},  ε ~ N(0,1)
S_ja = {s, sh, ts, ch, z, j, h, hy, f, v, I, U}     ← A/E/O は 0 出現なので除外
```
⚠️ **破擦音 `{ts, ch}` を含めるかは未決**。閉鎖＋摩擦の複合なので閉鎖部への注入は劣化しうる。区間後半だけの時間マスクが要るかもしれない（論文はこの区別をしていない）。

**3 段構えで決める**:
1. **主目的（最大化ではなく一致）**: `J(β) = Σ_c w_c |SFM_ratio_c(β) − 1|` を最小化。`SFM_ratio_c = SFM_c(student)/SFM_c(teacher)`。**「平坦度が高いほど良い」にしない**（際限なくノイズを足す解に落ちる）
2. **ガードレール**: `SCOREQ_ratio(β) ≥ 0.95 × SCOREQ_ratio(0)` / `UTMOS_ratio(β) ≥ 0.975 × baseline` / `ΔCER(β) − ΔCER(0) ≤ +0.01`
   ⚠️ **未検証**: この 2.5% / 5% の差を 95% CI で分離するのに何発話必要かの power 計算が無い。**UTMOS は無音パディングだけで平均 +0.11（ベース 1.6–2.0 に対し約 6%）動く**ので、閾値がノイズに埋もれる可能性がある。**β スイープの前に power 計算を実施する**
3. **タイブレーク = A/B (CMOS) 聴取**。ガードを通った上位 2–3 候補、摩擦音・無声化母音高密度 20 文、順序＋左右ランダム、評価者 10 名以上、同一刺激反復 2 回で内的一貫性。選好比の二項 95% CI が 0.5 を跨いだら **β を小さいほうに倒す**

**完了条件**: `eval/out/beta_selection_<date>.json` に採用 β・全指標・聴取の生 CSV が凍結される

### Phase 6: 量子化

- symmetric int8 / per-output-channel、activations は per-frame
- **Embeddings, normalization affines, iSTFT support code は fp32 のまま**（論文 `saanoTTS.txt:261-264`）
- **完了条件**: fp32 と int8 の 3 経路平坦度・SCOREQ 比が閾値内。**int8 blob のバイト数が層構成の検算になる**（§6 参照）

---

## 6. 生徒モデルの層構成（逆算結果）

**参照実装**: `scratchpad/saanotts_param_reference.py`（4 モジュールすべて delta 0、end-to-end forward 通過）。
**⚠️ `src/` に写す前に、下記の「未確定」項目を config ノブとして外に出すこと。** 間違った定数がハードコードされたまま実装が進むのを防ぐ。

| モジュール | 目標 | 構成後 | 判定の強さ |
|---|---:|---:|---|
| `Eρ`（学習専用） | 14,952 | 14,952 | **実質的に一意**（代数 + 広い族の全探索で hit 1 件） |
| `Dα` | 36,164 | 36,164 | **⚠️ 前提が反証済み**（下記） |
| `Aβ` | 199,536 | 199,536 | **非埋め込み部 192,000 は確実。内訳は非一意** |
| `Gγ` | 331,308 | 331,308 | **⚠️ 狭い探索箱の中で一意にすぎない**（下記） |
| 合計 | 567,008 | 567,008 | 一致 |

### `Eρ`（唯一の実質一意解）

| 層 | 形状 | params |
|---|---|---:|
| `Conv1d(192, 64, k=1) + bias` | 192×64 + 64 | 12,352 |
| `Conv1d(64, 40, k=1) + bias` | 64×40 + 40 | 2,600 |
| **合計** | | **14,952** ✅ |

`233h + 40 = 14952 → h = 64` が唯一の整数解。norm も活性化パラメータも入らない。デプロイ時は実行されないので 567,008 に含めない。

### `Aβ`（非埋め込み部が確実）

**最重要の導出**: `199,536 − 157×48(=7,536) = 192,000 ちょうど`。
埋め込み次元 48 は論文の「30 エントリで +1,440 params」から直接 `1440/30 = 48`。`567,008 − 1,440 = 565,568` が論文の earlier count と一致。

採用案（forward 通過）:

| 層 | 形状 | params |
|---|---|---:|
| `Embedding(157, 48)` | 157×48 | 7,536 |
| token block ×3（音素レート） | `Conv1d(48,48,k=5)+b ×2 + LN(48)` | 69,696 |
| length regulator | `repeat_interleave(h, d̂)` | 0 |
| ⚠️ 音素内位置 `Embedding(88, 48)` | 88×48 | 4,224 |
| frame block ×5（86.13 fps） | 同構造 | 116,160 |
| `Conv1d(48, 40, k=1)` bias なし | 48×40 | 1,920 |
| **合計** | | **199,536** ✅ |

**⚠️ この内訳は非一意**（`leftover=0` の解が複数、`leftover<=400` で 1,003 件）。確実なのは「非埋め込み 192,000」「1 ブロック ≈ 23,200–23,800 = 幅 48 で kernel-5 のフル conv 2 本相当」だけ。
**⚠️ 位置埋め込み 88 エントリは論文に根拠がない選択。** しかも int8 blob の検算はこれを**外す**方向を支持する（下記）。

### `Dα` ⚠️ 前提が反証済み

| 層 | 形状 | params |
|---|---|---:|
| ⚠️ `Embedding(157, 32)` | 157×32 | 5,024 |
| block ×3 | `Conv1d(32,32,k=5)+b ×2 + LN(32) + LayerScale γ` | 31,107 |
| `Conv1d(32, 1, k=1) + bias` | 32×1 + 1 | 33 |
| **合計** | | **36,164** ✅ |

**⚠️ 反証 1: `Dα` に `Embedding(157,32)` を置く構成は論文の勘定と矛盾する。**
論文は「deployed acoustic vocabulary は 157 エントリで、研究構成より 30 多く、**1,440** embedding params を足す。よって shipped graph は 565,568 ではなく **567,008**」と書く。`567,008 − 565,568 = 1,440 = 30×48` **ちょうど**で、増分は全部 acoustic 埋め込みに帰属している。もし `Dα` が同じ語彙の 32 次元埋め込みを持ち V が 127→157 に増えたなら、duration も +960 増えて総増分 2,400、研究構成の合計は 564,608 になり論文と合わない。
**→ `Dα` のパラメータ数はこの語彙変更に不感である。** 日本語適用の指針「`Dα` は `36,164 + (V−157)×32`」は未検証の仮定の上に乗っている。

**⚠️ 反証 2: V=157 は導かれていない、選ばれている。**
「`mod 32` で `2×conv5+nLN` 族のうち埋め込み予算が整数になるのは nLN=1 の V=157 だけ」は、スクリプトが自分で「必ず必要」と結論した 3 スカラーを引き忘れた誤読。正しく引くと `nLN=0 → V=163` / `nLN=1 → V=157` / `nLN=2 → V=151` の **3 つとも整数**。

**⚠️ 反証 3: 「3 個の追加スカラーが必ず必要」も言い過ぎ。**
`mod 32` が強制するのは `head + スカラー ≡ 4 (mod 32)` という合同式だけ。`Conv1d(32,4,1)+b = 132 ≡ 4` なら**スカラー 0 個**で成立する。

**→ Phase 2 の着手前に、研究構成 565,568 の内訳を `Dα` 側でも仮定して総当たりし直す。** 日本語で真っ先に触るパラメータがまさにここ。

### `Gγ` ⚠️ 探索箱が狭い

採用案 C（PyTorch 検証済み、delta 0）:

| 層 | 形状 | params |
|---|---|---:|
| in `Conv1d(40, 76, k=3) + bias` | 40×76×3 + 76 | 9,196 |
| block ×5: `dwConv1d(76,76,k=7,groups=76)` bias なし | 76×7 | 532 |
| block ×5: pw `Conv1d(76, 304, k=1) + bias` | 76×304 + 304 | 23,408 |
| block ×5: pw `Conv1d(304, 76, k=1) + bias` | 304×76 + 76 | 23,180 |
| block ×5: rank-12 down `Conv1d(40, 12, k=1) + bias` | 40×12 + 12 | 492 |
| block ×5: rank-12 up `Conv1d(12, 76, k=1) + bias` | 12×76 + 76 | 988 |
| block ×5: LayerScale γ | 1 | 1 |
| ⚠️ head `Conv1d(76, 48, k=1) + bias` | 76×48 + 48 | 3,696 |
| head `Conv1d(48, 1539, k=1) + bias` | 48×1539 + 1539 | 75,411 |
| **合計** | | **331,308** ✅ |

**確かなこと**: 位相 `1,026 = 513 × 2` は (cos, sin) 2 座標で、`n_fft=1024` の片側ビン 513 と厳密に整合。出力 `513 + 1026 = 1539` ch。`hop 256 @ 22,050 Hz = 86.133 fps`。

**⚠️ 反証 1: 「論文の字義解釈は算術的に不可能」は dense 仮定込み。**
`350,664 > 331,308` は「pointwise が dense」という追加仮定の下での不可能性。**grouped pointwise なら収まる**（同じスクリプトが `g=2` で `delta = −91,585` と出力している）。
**⚠️ 反証 2: 「MMAC 28 が独立に裏づける」は成立しない。** 論文の 28 MMAC/s は「畳み込みを評価し、**かつ 1024 点 iSTFT を使って**、コストを 28 MMAC/s に下げる」という文で **iSTFT 込みの数字**。1024 点実 iFFT はざっと 0.9 MMAC/s なので、**採用案 C（327,300 weights = 28.19 MMAC/s）も iFFT を足せば 28 を超える**。同じ物差しで字義解釈を棄却するなら C 案も棄却される。判別力がない。
**⚠️ 反証 3: 「rank-48 ヘッドが exact 唯一解」は探索箱が狭い。** grouped / 低ランクの pointwise 拡張を一切列挙していない。per-layer で bias を変える、ブロックあたり 2 個以上のスカラー、GroupNorm も未探索。
**代替案 B**（拡張幅 `E=255` に落として dense head 維持、delta −62）も同等に妥当。

**⚠️ 反証 4: 論文には「Table I と本文 4 文」以外にも層仕様がある。**
`saanoTTS.txt:261-264`「Embeddings, **normalization affines**, and the inverse-STFT support code remain in floating point」は**正規化層の affine が存在すること**を明言する。ところが採用案 C は `nLN=0`（5 ブロックすべて正規化層なし）で、この記述と噛み合わない。しかも `Dα` / `Aβ` の採用解はブロックごとに LayerNorm を置いており、**同一論文の 3 モジュールで設計が不整合**。

### 未使用の最強制約: int8 blob のバイト数

論文は `Its two blobs occupy 280,288 and 399,544 bytes` と量子化スキーム（int8 per-output-channel、埋め込みと正規化 affine は fp32）を明示している。**これは候補構成を篩える実効的な制約なのに、逆算で一度も使われていない。**

粗い試算:
- 採用案の duration + acoustic ≈ **295.8 KB**（実測 280,288 B に対し **+5.5%**）
- decoder ≈ **360.9 KB**（399,544 B に対し **−9.7%**）
- **提案されている 88 エントリ位置埋め込み（fp32 で 16,896 B）を外すと blob1 が ≈278.9 KB になり、280,288 B と 0.5% 以内で一致する**

**→ この制約は「位置埋め込みを持たない `Aβ` 分解」を支持する方向に働く。Phase 3 着手前にこの検算を通す。**

### その他の未確定

| 項目 | 状態 |
|---|---|
| **iSTFT のフレーミング規約** | ⚠️ 参照実装は `center=True` で `256·(T−1)` サンプルを出す（`c[1,40,14] → pcm[1,3328] = 256×13`）。論文の golden utterance は **100,096 = 391×256** と **34,304 = 134×256** で **256 の厳密な倍数**。`saanoTTS.txt:257` の「periodic overlap-add envelope」と合わせると、**デプロイ版は非 center 方式で `256·T` を出す可能性が高い**。教師ラベル（piper-plus の hop 256 出力）とのアラインメントに直結するので、蒸留のフレーム対応表を作る前に決める |
| **総計 45 MMAC/s との整合** | ⚠️ 採用構成は 39.7 MMAC/s で 12% 不足。しかも 45 は「**token block も含め 8 ブロック全部をフレームレートで動かす**」読み（44.66）のほうがよく合う。これは「token block = 音素レート、length regulator を token/frame の間に置く」という採用解釈を否定する材料になる |
| **`μ_T` / `σ_T` の扱い** | ⚠️ 式(3) の `N_T` と式(7) の `σT_k` はチャネルごと 40 個（z-line なら 192 個）の定数。デプロイグラフに含まれるのか（= 567,008 の内訳のどこか）、外部定数なのかが未決。パラメータ勘定に効く |
| **rank-12 conditioning の共有/per-block** | down 射影(40→12) をブロック共有にすると 1,968 params 浮き、別ノブで埋める必要がある |
| **位相の正規化** | `cos²+sin²=1` に正規化するか、L2 罰則か。論文に記述なし |
| **quality z-line の幅** | ⚠️ 「decoder 76 → 137〜146」は根拠薄（`137` を出したスクリプト出力が存在せず、モデル式が c-line と別ファミリー）。**R8 z-line (600,097) は参考にしない** — 論文が「waveform-domain transposed-convolution decoder, 522 MMAC/s」と明記する別系統 |
| **日本語での V** | 日本語単言語なら音素在庫を 100–120 に絞れる（縮む方向）。ただし `Dα` 側の係数は上記の反証により未確定 |

---

## 7. 評価パイプライン設計

### 7.1 全体方針

- **すべて教師比で報告する。** SCOREQ / UTMOS / 平坦度は `ratio = metric(student)/metric(teacher)`、CER は差分 `ΔCER`。
  UTMOS も含む（**UTMOS22 は日本語で訓練されていない** — VoiceMOS2022 main=BVCC 英語 / OOD=BC2019 中国語）。
- **3 経路を必ず同時に測る**（論文が sibilant 欠陥を発見できた唯一の理由）:
  1. `teacher` — 教師 `yT`
  2. `oracle-decoder` — 教師潜在 → 生徒 `Gγ`
  3. `student` — フル生徒（β 変種込み）
  平坦度が `teacher ≈ oracle > student` なら欠陥は acoustic、`teacher > oracle` なら decoder。
  **⚠️ 未決**: oracle-decoder の入力は `zT`(192ch) か `cT`(40ch) か。c-line の生徒 decoder の入力は本来 40ch なので、`Eρ` を通すのか `zT` を直接使うのかで測る対象が変わる。**Phase 3 着手前に定義する。**
- **環境は piper-plus と完全分離**（`.venv-eval`）。piper-plus の `.venv` には何もインストールしない。

### 7.2 依存（実測で必要と分かったピン）

```toml
requires-python = ">=3.12,<3.14"
dependencies = [
  "scoreq==1.0.1", "torch>=2.4,<2.14", "torchaudio>=2.4,<2.14",
  "onnxruntime>=1.18", "soundfile", "librosa", "numpy>=2.0", "scipy",
  "pyworld==0.3.5", "setuptools<81",     # pyworld 0.3.5 は pkg_resources を import する
  "jiwer>=4.0", "transformers>=4.44", "tqdm",
]
```

**採用しない**: `torchcodec` を依存に入れる（soundfile 差し替えで解決済みなのに torch バージョンへの強い結合を持ち込む）。
**必ず入れる**: `torch`（`scoreq` は基本依存に入れていないのに `scoreq.py:1-9` が無条件 import する）。
**⚠️ `torchaudio.load` は torchaudio 2.11 で torchcodec 必須になった**（`ImportError: TorchCodec is required for load_with_torchcodec`）。`scoreq` / `f0_extraction` / piper-plus の UTMOS 実装が全部この経路。**soundfile に差し替える**:

```python
import torchaudio, soundfile as sf, torch
def _load(p, *a, **k):
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    return torch.from_numpy(x.T.copy()), sr
torchaudio.load = _load          # ★ import scoreq より前に
import scoreq
```

> **⚠️ `import scoreq` した時点で 378 MB が落ちる。** `scoreq/__init__.py` は全 2 行で `from .scoreq import Scoreq` / `scoreq = Scoreq()` — デフォルト `data_domain='natural'` の Scoreq が eager に構築され `adapt_nr_telephone.onnx` を DL する。`synthetic` を作るとさらに 378 MB。**初回コストは 378 MB ではなく合計 756 MB。** CI キャッシュのサイズ見積りに注意。

### 7.3 各指標

| 指標 | 実装 | 注意 |
|---|---|---|
| **SCOREQ** | `Scoreq(data_domain='synthetic', mode='nr')`。高いほど良い | TTS は `synthetic` 固定。`ref` モードは ONNX 経路で `np.linalg.norm(test−ref)` で PyTorch の `torch.cdist` とスケールが違う可能性。⚠️ 長さ依存性・向きが未検証なので**主指標は NR** |
| **UTMOS** | `torch.hub.load("tarepan/SpeechMOS:v1.2.0","utmos22_strong")`。22.05 kHz を**直渡し**（内部で 16k に落とす） | 外部リサンプルしない。`fairseq` 不要。102,772,865 params |
| **無音パディング** | **全システムで 300 ms に固定** | 実測で UTMOS が平均 +0.11 動く（β の効果量 0.09 と同オーダー）。ゲインも統一 |
| **CER** | `jiwer.cer()`。**`jiwer.wer()` は日本語で常に 1.0 になるので使わない** | ⚠️ ASR / CER 経路は**一度も実行されていない**（`kotoba-tech/kotoba-whisper-v2.0` と `openai/whisper-large-v3-turbo` は「HF に存在確認済み」だけ） |
| **CER の参照** | ⚠️ 「ASR 出力を音素列に変換して比較」案は **B-1 の中国語誤ルーティングを ASR 出力側にも持ち込む**（ASR が数字を算用数字で出せば同じ経路で壊れる）。**先に B-1 を解決してから設計する** | 正規化は NFKC + 約物除去。⚠️ その実効果は「0.30→0.12」ではなく **0.154→0.12**（前者は別々の文ペアの値を並べたもの） |
| **平坦度プローブ** | §B-6 のグリッドで `(n_fft, guard, power)` を分離指標で選ぶ | ⚠️ 推奨されていた `512/guard=1/power=2` は実教師データで反証済み |
| **F0 / アクセント** | `pyworld.harvest` + `stonemask`、`frame_period = 1000*256/22050 = 11.60998 ms` | **`dio` ではなく `harvest`**（実測 voiced 110/116 vs 65/116）。`harvest` は `n_frames+1` を返すので切る |

**音素クラス表** (`ja_classes.py`):
```python
FRICATIVE = {"s","sh","z","j","zy","h","hy","f","v"}
AFFRICATE = {"ts","ch"}
DEVOICED  = {"I","U"}                      # ⚠️ A/E/O は 23,271 行で 0 出現
STOP      = {"k","t","p","b","d","g","ky","gy","ty","dy","py","by","cl","q"}
NASAL     = {"n","m","ny","my","N_m","N_n","N_ng","N_uvular"}
VOWEL     = {"a","i","u","e","o","a:","i:","u:","e:","o:"}
```

**⚠️ 評価セットの必須要件**: 教師 8 発話では無声化母音のフレームが **n=3** しか取れず、全クラス合計でも約 142 フレームしかない。`sibilant_dense_ja.tsv` を別立てし、**クラスごとに最低 300 フレーム**を確保する。それまで教師ベースラインを凍結しない。

### 7.4 集約と報告

- 発話ごとに指標を出し、**発話単位の bootstrap（10,000 回）で比の 95% CI**
- `diverse_ja` は最低 200 文、**テンプレート文禁止**（論文の 512 行 narrow set 問題の再現防止）
- ⚠️ **必要 n の power 計算を先にやる**（§Phase 5 のガードレール参照）

### 7.5 piper-plus 資産の流用判定

| 資産 | 判定 | 理由 |
|---|---|---|
| `scripts/audio_quality_metrics.py` の指標計算 | **流用不可** | `cmd_compute` / `cmd_synthesize` が `return 2` のスタブ（:284-299）。CI も stub しか回していない |
| 同 `compute_diff` / `render_markdown` / `to_bencher_json` | **流用可** | 差分ハーネスの枠。`tests/scripts/test_audio_quality_metrics.py` に閾値回帰 / NaN / bencher の単体テストが 10 件超ある |
| `tools/benchmark/compute_metrics.py` の UTMOS | **要修正で流用可** | 実装は本物 (:129-182)。`torchaudio.load` を soundfile に置換必須。**失敗を -1.0 に潰す設計は捨てる** |
| 同 RMS/peak/silence/sample-rate チェック | **流用可** | 合成物の sanity gate |
| `tools/benchmark/generate_mos_survey.py` | **ほぼそのまま流用可** | 絶対 MOS 用。blind / randomize / seed / CSV・JSON 出力あり、外部 CDN 非依存。**A/B (CMOS) は無いので fork して追加**。base64 埋め込みで約 58.8 KB/音声秒 |
| `tools/benchmark/generate_samples.py` | **部分流用** | ONNX 前提なので合成本体は不可。**ディレクトリ規約 `{samples_dir}/{system}/{lang}/{text_id}.wav` と `generation_results.json` は必ず踏襲**（下流 2 ツールが無改造で動く） |
| `src/python_run/piper_plus/timing.py` | **流用不可** | ceil していないため累積ドリフト（36 音素で 193 ms）。※ これは「バグ」ではなく `docs/spec/phoneme-timing-contract.toml` で契約化されたユーザー向けタイムスタンプ API。教師フレーム整合には使えないだけ |
| `src/python/piper_train/f0_extraction.py` | **設計のみ流用** | hop 256 整合は正しいが pyworld 未導入 / dio 使用 / `torchaudio.load` 依存 |
| `src/python/jp_phoneme_map.py` | **使ってはいけない** | id 表が壊れている（`get_phoneme_id_map()` は 58 entry / max id 57、実パックの id は最大 87）。`PHONEME_TO_PUA` も 22 entry で 173 symbol 中 55 個を解決できない。canonical は `piper_plus_g2p.encode.pua.CHAR2TOKEN` (99 entry, 双方向, `check_pua_compat` 付き) |
| `scripts/evaluation/evaluation_texts_ja.txt` | **シードとして流用可** | 31 文。橋/箸/端・雨/飴を含む。⚠️ 疑問文 4 つはすべて素の `？` で `?!` `?.` `?~` は 0 件 |
| `scripts/audio_parity.py` | **Phase 6 で流用検討** | 階層判定の枠は C99 ゴールデンテストに使えるが、**Pearson 相関は未実装**（tier3 は SNR のみ、`mel_spec_max_mse` は閾値キーとしてしか存在しない）。論文の Pearson ≥ 0.98 は自作 |

---

## 8. リポジトリのディレクトリ構成案

```
/Users/s19447/Desktop/saanoTTS-jp/
├── CLAUDE.md                          # §1.3 の訂正を反映
├── pyproject.toml
├── docs/
│   ├── research/saanotts-jp-feasibility.md    # canonical。§1.3 の訂正を反映
│   ├── plan/phase0-1-implementation-plan.md   # 本文書
│   ├── decisions/                     # ADR。各判断の根拠と実測値を凍結
│   │   ├── D0-target-tier.md          # 567K vs 1.4M
│   │   ├── D1-frontend.md             # ★ multilingual vs 言語ピン留め（最優先）
│   │   ├── D2-pad-duration.md         # `_` PAD の duration の扱い
│   │   ├── D3-corpus-mix.md           # CV をどこまで使うか
│   │   ├── D4-accent-features.md      # アクセント記号のみ vs +A1/A2/A3
│   │   ├── D5-istft-framing.md        # center=True vs 非 center
│   │   └── D6-ema.md                  # yT に EMA を当てるか
│   └── notes/teacher-properties.md    # freeze_dp / emb_lang unify / 長さ上限
├── scripts/
│   ├── guard_piper_plus_readonly.sh   # git status / HEAD / worktrees の確認
│   ├── b1_probe_g2p_routing.py        # ★ B-1
│   ├── b4_length_hist.py              # ★ B-4
│   ├── gate_ja_only.py                # ★ 全ラベル入力の言語ゲート
│   ├── fetch_corpus.sh
│   ├── remote_zip_extract.py          # JSUT の HTTP Range 抽出（zip64 フォールバック要）
│   ├── build_pool.py / preprocess_pool.py / build_splits.py / check_leakage.py
│   ├── phase0_verify_teacher.py       # ★ 書き直し（speaker=None / EMA / 実 prosody）
│   ├── gen_teacher_labels.py          # ★ 6 つの既知欠陥を修正
│   ├── verify_packs.py
│   ├── synth_teacher.py / synth_student.py
│   ├── make_survey_mos.py / make_survey_ab.py
│   └── run_eval.sh
├── src/saanotts_jp/
│   ├── frontend/                      # text → (phoneme_ids, prosody)。D1 の実装
│   │   ├── phonemize.py / strip.py / pua.py / gate.py
│   ├── teacher/                       # ckpt ロード・EMA・決定的推論
│   ├── data/                          # pack IO / manifest / channel_stats
│   ├── models/                        # Dalpha / Abeta / Ggamma / Erho（config ノブ化）
│   ├── losses/                        # L_d / L_c / L_G / L_joint
│   └── eval/
│       ├── audio_io.py                # soundfile ベース。torchaudio.load 差し替え
│       ├── align.py                   # ★ ceil(dT) 累積和。timing.py は使わない
│       ├── ja_classes.py / corpus.py
│       ├── m_scoreq.py / m_utmos.py / m_cer.py
│       ├── p_flatness.py / p_accent.py
│       ├── ratio.py / beta_sweep.py / report.py / cli.py
├── data/
│   ├── raw/ interim/ splits/ packs/{smoke,train_23k,train_14343,eval}/
├── eval/
│   ├── sets/{diverse_ja.tsv, minimal_pairs_ja.tsv, sibilant_dense_ja.tsv, smoke50_ja.txt}
│   └── out/{samples/, metrics.json, report.md, beta_selection_<date>.json}
├── reports/                           # ★ §2 の検証タスクの出力先
├── tests/
│   ├── test_prosody_align.py          # ★ B-2 の回帰テスト
│   ├── test_frame_identity.py         # ceil(dT) 恒等式
│   └── test_ja_gate.py
├── data-sources.yml                   # ライセンス台帳
└── vendor/                            # 必要になったときだけ git archive の抽出先
```

### ライセンス台帳 (`data-sources.yml`)

```yaml
- id: rohan4600
  license: {spdx: "CC0-1.0", verified: true, url: "https://github.com/mmorise/rohan4600"}
- id: ita-corpus
  license: {spdx: "CC0-1.0", verified: true, url: "https://github.com/mmorise/ita-corpus"}
- id: common-voice-ja-sentences
  license: {spdx: "CC0-1.0", verified: false,
            note: "repo は MPL-2.0。server/data/ja/ に個別 LICENSE 無し（3 URL とも 404 実測）。
                   docs/SENTENCES.md にも文そのものを CC0 とする明示なし。要一次ソース確認"}
- id: jsut-ver1.1-text
  license:
    per_subset:
      basic5000: ["CC-BY-SA-3.0 (Wikipedia)", "CC-BY-2.0 (Tanaka)", "CC-BY-SA-4.0 (original)"]
      utparaphrase512: "CC-BY-SA-4.0 (SNOW E4)"
      onomatopee300/countersuffix26/loanword128: "CC-BY-SA-4.0"
      voiceactress100/repeat500: "CC-BY-SA-4.0 (Voice Actress Corpus)"   # 評価から全除外
      travel1000: "CC-BY-SA-3.0 (NICT)"
      precedent130: "copyright-free"
    verified: true
    source: "jsut_ver1.1/LICENCE.txt"
  note: "音声は研究用途限定だが本プロジェクトはテキストのみ使用。
         LICENCE.txt に 'The text data were modified to read it easy.' とあり、
         VOICEACTRESS100 の JSUT 版と原典（声優統計コーパス）の字面同一性は未確認"
- id: distributable-subset          # PD/CC0 のみ ≈ 7,000 行。別 tag で管理
  members: [rohan4600, ita-corpus, "jsut:precedent130", synthetic-t2]
```

---

## 9. 残るリスクと判断待ち事項

### 9.1 判断待ち（Decision Records として凍結する）

> **✅ 決着済み**: D0（tier → 567 K が成果物、D-007）/ D3 の一部（コーパスは 23,271 行で確定）。
> **消えた可能性**: D1 は入力仕様の変更で不要になるかもしれない。Phase A-1 で判定する。

| ID | 判断 | 期限 | 影響範囲 |
|---|---|---|---|
| **A-1** | **ラベル生成の入力を中間表現に統一するか**（統一すれば D1 が消える） | **Phase B 着手前（最優先）** | ラベル全体。生徒が学ぶ入力とデバイスの出力の一致 |
| **A-2** | prosody をどう供給するか（中間表現は A1/A2/A3 を持たない） | Phase B 着手前 | `dT` の質。生徒 duration net の設計 |
| ~~**D1**~~ | ~~フロントエンド: `MultilingualPhonemizer` か言語ピン留めか~~ | A-1 の結果次第で消える | pool の 5.36% のラベル |
| ~~**D0**~~ | ~~ターゲット tier~~ → **567 K が成果物、1.4 M は足場**（D-007） | ✅ | — |
| **D2** | `_` PAD (トークンの 49%) の duration の扱い | Phase 2 着手前 | `Dα` の実効トークン予算、`clip` の下限問題 |
| **D3** | Common Voice をどこまで使うか（断片 66.6% / 裸の地名 4.1% / ライセンス verified:false） | Phase 1 の split 作成前 | pool の 40.7%。配布可能サブセットの定義 |
| **D4** | アクセント記号トークンのみか、A1/A2/A3 3 スカラー (+128 params) を足すか | Phase 2 の評価後 | `Dα` のアクセント再現性 |
| **D5** | iSTFT のフレーミング: `center=True` (256·(T−1)) か非 center (256·T) か | Phase 4 着手前 | 教師ラベルとのアラインメント全体 |
| **D6** | `yT` に EMA を当てるか（適用有無で SNR 12.53 dB。`zT`/`dT` は bit 一致） | Phase 4 着手前 | 波形ラベルのみ。**c-line/z-line ターゲットを先に固めて後回しにできる** |

### 9.2 未検証のまま残るリスク

| リスク | 状態 | 潰し方 |
|---|---|---|
| **教師の事前学習テキスト (497,519 発話 / MOE-Speech) と pool の重複** | ⚠️ 未検査 | `ayousanz/moe-speech-20speakers-ljspeech` の metadata を引いて照合 |
| **`Dα` が音素埋め込みを持つか** | ⚠️ 反証済み前提の上に立っている | 研究構成 565,568 の内訳を `Dα` 側でも仮定して総当たり |
| **`Gγ` の正規化層の有無** | ⚠️ 論文の "normalization affines" 記述と採用解 (nLN=0) が衝突 | int8 blob バイト数を制約に入れて再探索 |
| **int8 blob 検算** | ⚠️ 未実施 | duration+acoustic 280,288 B / decoder 399,544 B を候補構成ごとに予測。位置埋め込みを外すと 0.5% 以内で合う |
| **総計 45 MMAC/s との 12% 乖離** | ⚠️ 未解決 | token block を音素レートに置く解釈自体を再検討する材料 |
| **β ガードレールの検出力** | ⚠️ power 計算なし | UTMOS はパディングだけで 6% 動く。必要 n を先に見積もる |
| **ASR / CER 経路** | ⚠️ 一度も実行していない | kotoba-whisper と whisper-large-v3-turbo を両方回して乖離を見る。日本語特化モデルは低品質音声に「それらしい日本語」を出して CER を過小評価するリスク |
| **SCOREQ が未導入** | ⚠️ **論文の主指標が測れていない** | pip パッケージが見つからない。Phase C までに導入経路を確保する。現状 UTMOS のみ |
| **UTMOS / SCOREQ の日本語での単調性** | ⚠️ 未検証 | B-5 で**スケールの圧縮**は確認した（実人間の日本語 = UTMOS 2.305）が、**順位相関は未確認**。日本語 MOS 付きコーパス（JVS-MuSiC 等）で一度確認する。天井が分かっても、その下で単調に並ぶ保証は別 |
| **集約指標が sibilant 欠陥を検出できるか** | ⚠️ 構造的に不利 | SCOREQ / UTMOS とも内部で 16 kHz にリサンプルするので 8 kHz 超は捨てられる。**「集約指標が下がらないから β は安全」という読みを禁止**し、プローブを必ず併記する |
| **無声化母音の区間境界** | ⚠️ 未測定 | 先行子音との境界が音響的に曖昧。`ceil(dT)` 境界とのずれ、guard の要否を実データで決める |
| **A/B 聴取の被験者確保** | ⚠️ 未定 | 日本語母語話者 10 名以上。HTML は自己完結なので配布は容易 |
| **`95e74cb2` が dangling** | 低 | piper-plus で `git gc --prune` が走ると消える。HEAD と推論差分ゼロなので現時点では不要だが、必要なら `git archive` で抽出 |

### 9.3 環境上の落とし穴（記録）

- **zsh**: `git show $t:src/python/...` は `$t:s...` がパラメータ修飾子として解釈されパスが壊れる。**`git show "${t}:src/python/..."` と括る**。
- **piper-plus は読み取り専用**。`checkout` / `commit` / ファイル編集の禁止。作業後は必ず `git status --porcelain` が 0 行、`.git/worktrees` 不在を確認する。
  **これは `.claude/hooks/guard_bash.py` が PreToolUse で機械的に強制する**（`permissions.deny` は Edit/Write しか止められないので、シェル経由をこれでカバーする）。
- **Python は `uv` 経由**（D-012）。依存追加は `uv add`。hook が `pip install` を deny し、uv を経由しない python を ask にする。
- **教師 ckpt の再ダウンロード禁止**。既にキャッシュにある。
