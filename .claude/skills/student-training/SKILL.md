---
name: student-training
description: Use when writing or changing the sanoTTS-jp student model, its losses, or the training loop — Duration/Acoustic/Decoder, iSTFT framing, the c-line, lambda weights, the discriminator, or anything that reads a teacher label pack. Covers the silent misalignments that produce numbers without producing a working model.
---

# 生徒モデルを触る

## 原則

**教師と生徒の contract がずれても、損失は数値を出し続ける。** 落ちないので気づかない。
実際に踏んだ 5 つを先に潰す。

## 1. 音素IDは必ず写す

**教師の ID 空間と生徒の埋め込みインデックスは別物。**

```python
from saanotts_jp.vocab import map_ids
ids = map_ids(teacher_ids)      # ✓
model(torch.tensor(teacher_ids))  # ✗ IndexError か、別の音素の行を引く
```

- 教師: 0〜173 の飛び飛び
- 生徒: **57**（`kana_g2p` が原理的に出せる音素の閉包、D-016）。最大 teacher_id は 64、
  途中に 8 個の穴（20–25 / 31 / 52）

**この写像表は重みと一緒に凍結する。** 語彙が変わったら重みは無効。

⚠️ `A` `E` `O` はコーパス出現 0（日本語の母音無声化は狭母音のみ）。
未学習行を残さないよう `a` `e` `o` と**同値初期化**する。

## 2. iSTFT は `length=T*256` を渡す

教師は **T フレーム → ちょうど 256·T サンプル**（220 発話すべてで成立、D-022）。

```python
torch.istft(S, n_fft=1024, hop_length=256, win_length=1024,
            window=..., center=True, length=T * 256)   # ✓
```

| やり方 | 結果 |
|---|---|
| `length` を省く | **256·(T−1)** ← 1 フレーム不足 |
| `center=False` | hann(1024)/hop=256 では**必ず RuntimeError** |
| **`center=True, length=T*256`** | 256·T / 往復 SNR 139 dB |

⚠️ **長さ合わせに `F.pad` を使わない。** ゼロ埋めは**勾配経路の無い損失の下駄**で、
学習が使う 32 フレームセグメントでは SNR 上限を **33.6 dB** に固定する
（発話全体で測ると 95 dB に見えるので気づかない）。`assert` で長さ一致を要求する。

## 3. λ は「暫定 1.0」にしない

論文が公開しているのは `(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)` だけ。
**残りを 1.0 で置くのは中立ではない** — 実測では正規化項を 3.7 倍・統計項を 5.3 倍
重く見る設定だった（C-021）。

| λ | 出し方 |
|---|---|
| `λ_n` | **実行時**。`ChannelStats.lambda_n` = `1/√(mean(1/σ_k²))`。σ 依存でパックが変わると 0.37〜0.46 倍ずれる |
| `λ₂` | **実行時**。残差 RMS から `1/(2·RMS)`。⚠️ 学習が進むほど**大きくなる**（減衰ではない） |
| `λ_Δ` / `λ_s` / `λ_T` | 定数 0.86 / 0.19 / 0.27（勾配整合の実測値、D-021）。ここが探索対象 |

**新しい λ を導入したら、値を決める前に勾配ノルムを測る**（`scripts/c1_lambda_balance.py`）。

## 4. 式2 のターゲットは `Σ max(1, dT)`

論文の字面は `Σ dT` だが、`r = max(1, exp(l̂))` に clamp があるので**教師側にも同じ
関数を掛けないと自己整合しない**。`dT` の 18.49% が 1 未満なので、
**完璧な生徒でも length 項が 0 にならず、発話を 4.4% 短くする定常バイアス**になる。

デプロイの展開規則を `ceil` にするなら `length_target=Σceil(dT)` を渡す。

## 5. `_` PAD を「余り」扱いしない

| 項目 | 値 |
|---|---:|
| PAD のトークン比 | 50.02% |
| **PAD のフレーム比** | **53.76%** |
| PAD の duration mean | **2.059**（実音素は 1.697） |

**PAD は音声時間の過半を占め、1 個あたり実音素より長い。**
破擦音・破裂音の摩擦バーストは後続 PAD に入っている（帯域 RMS が音素区間の 3.6 倍）。
この教師のアライメントでは `_` は実音響を担う単位（D-018）。

## パックを読むとき

```python
from saanotts_jp.labelpack import PackReader
```

- `yT` は **int16 / 固定 scale 32767**。**二重にスケールしない**
- `zT` は fp16 192ch。c-line (40ch) は `Eρ` を通した後
- `dT` は**生の float**。フレーム数は `ceil(dT)`（`round` ではない）
- フレーム i の区間は `[cumsum(ceil(dT))[i-1], cumsum(ceil(dT))[i])`、
  サンプルは ×256。**`sum(ceil(dT))*256 == len(yT)` を assert する**

## 判別器

`FirstDifferenceDiscriminator`（既定 94,755 params、学習専用でデプロイされない）。

- **一次差分はモジュール内で取る。** 呼び出し側は生波形を渡す
  （real / fake で差分の取り方がずれる事故を構造的に防ぐ）
- 論文が指定しているのは「Δŷ に判別器を掛ける」の 1 点だけ。
  層構成・正規化はすべて仮定（`discriminator.py` の A1〜A9）
- ⚠️ **正規化 3 種に測れた差は無い。** spectral を既定にしているのは理屈からで、
  実測の裏付けは無い。`L_adv` が発散したら `norm="weight"` を試す
- 立ち上がりに 100〜200 step かかるのは正常。60 step で gap が出なくても壊れていない

## 6. 「損失が下がった」は成果物が出来た証明ではない

旧実装は**スモークテスト全通過なのに本学習に使えなかった**（C-022）:
重みを保存せず、段の間で引き継がず、Stage 3/4 が `Aβ` を使わず
（predicted-code mixing の代理がガウスノイズ）、式3 の統計がダミーだった。
**どの欠陥も損失を下げる。** むしろ引き継がないほうが初期損失が高く、下げ幅が大きく見える。

判定に入れること:

- Stage 4 の ckpt で**デプロイ対象 3 つが揃い、合計 559,008 と一致する**ことを assert
- 前段の ckpt が無ければ**止める**（黙って新規初期化しない）
- **held-out での検証損失**を並記する
- `c_rank` / `c_std` を毎回ログに出す（`Eρ` の自明解の監視、D-028）

## `Eρ` の扱い（論文に無い / D-028）

`c` の意味を決めるのは decoder であって `Eρ` 単独ではない。Stage 2 で両方を
自由に動かすと**両方が定数を出す自明解**がある。

| Stage | `Eρ` |
|---|---|
| 2 | **凍結**。`Aβ` だけが追う |
| 3 | **学習**。decoder と一緒に動かして `c` の意味を決める |
| 4 | 凍結。`Aβ` が追い直す（式6 の第 2 項がアンカー） |

`c_rank` が下がり続けるなら潰れている。別案（再構成損失を足す / 順序を変える）は未検証。

## 7. C99 コアと参照実装は 1:1 で対応させる

`csrc/saanotts.c` は `src/saanotts_jp/_param_reference.py` の写しである。
**片方だけ直すと golden test が落ちる。それが検出手段。**

対応で間違えやすいところ:

| 箇所 | 落とし穴 |
|---|---|
| LayerNorm | PyTorch は**チャネル方向**に正規化する（参照実装は `transpose(1,2)` してから）。C は `[C,T]` レイアウトなので時刻ごとに C 本を見る。**軸を間違えても数値は出る** |
| Duration の残差 | LayerScale 付き（`x + γ·f(x)`）。**Acoustic は素の残差**（γ 無し） |
| decoder の条件付け | `g = cup(cdown(c))` の `c` は**毎段とも元の入力**。`h` ではない |
| GELU | PyTorch の既定は erf 版。tanh 近似ではない |
| `round` | C の `roundf` は half-away-from-zero、`torch.round` は **half-to-even**。ちょうど .5 で割れる |

```bash
uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt
make -C csrc all-test    # golden / stream G1〜G4 / FFT / int8
make -C csrc run-bench   # レイテンシ（段別の内訳）
```

## 9. 速度を測るとき（M-43）

| 罠 | 症状 |
|---|---|
| **結果を使わないと最適化で消える** | ベンチが **0.000 ms** を出す。入力を毎回変え、出力を合計して印字する |
| **同じ計算を 2 か所に書く** | 本体を FFT 化してもベンチの段別が naive DFT のままで、整合性が **8.9** に飛んだ |
| **メモリを arena だけで測る** | FFT の **stack 4,224 B が計測外**。合算すると 200 KB を超えていた |
| **η=1 の下限だけ見る** | fp32 は「0.93× RT であと少し」に見えるが、実測 η を転移すると **2.47× RT** |

**メモリと速度はトレードオフ**。`o1539` を 1 フレームずつにすると 159 KB まで
落ちるが **35% 遅くなる**。ESP32 では速度が律速なので速度を取った。

⚠️ **手元では int8 の速度を検証できない。** Apple の SIMD は fp32 向きで、
int8 にしても 0.86〜1.01 倍にしかならない。**PIE の効果は実機でしか測れない。**

```bash
make -C csrc all-test    # 一括版の golden test + ストリーミングの G1〜G4
```

## 8. ストリーミング版を触るなら（M-42）

一括版と**bit 一致**が要件。`make -C csrc stream` が判定する。踏んだ罠:

| 症状 | 原因 |
|---|---|
| 全サンプル不一致 | **段の出力の発話外に bias 由来の非ゼロ**が残る。一括版は配列外＝ゼロ |
| 先頭 pad フレームだけずれる | **ブロック内部の c1→c2 の間**も同じ。c1 の出力の発話外もゼロにする |
| 途中から壊れる | **iSTFT リングの衝突**。`out_pos` を N/2 から始めると `[0,512)` が pop されない |
| 末尾が出ない | **時刻が負のフレームも iSTFT に push**していた。絶対フレーム番号で位置を決める |

**原則: conv が時刻を混ぜる箇所すべてで、発話外をゼロにする。**
1x1 conv の後は不要（時刻を混ぜないため）。

⚠️ **切り分けは「一括版と」比べる。** PyTorch の golden と比べると
fp32 の丸め差 7e-07 が乗って、実装バグと区別できない。

⚠️ **メモリは実用最大長で測る。** 53 ids では 185 KB でも、
350 ids（D-017 の上限）では 243 KB あった。短い文だけの測定は甘い。

## 学習を回す

```bash
uv run python scripts/train_student.py --run runs/v3 --stage 1 --steps 20000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 2 --steps 60000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 3 --steps 80000 --accum 8
uv run python scripts/train_student.py --run runs/v3 --stage 4 --steps 60000 --accum 8
uv run --extra eval python scripts/eval_student.py --ckpt runs/v3/stage4.pt --n 24 \
    --out reports/eval_v3
```

⚠️ **Stage 3 は 80,000 step。40,000 ではない**（D-037）。
40k → 80k にしただけで SCOREQ +0.0653 [+0.022, +0.108] / DNSMOS +0.0660 /
アクセント 35/36 → **37/37** と 5 指標すべて改善した（M-59）。
⚠️ **160k は無駄**（最終品質は 80k と有意差なし）。**80k が最適点。**
⚠️ **効くのは容量ではなく学習量**（M-52。params を 43% 増やしても gap は
+0.0006 しか動かない）。**decoder を作り直す前に Stage 3 を延ばすこと。**

**step 配分の実測（M-37）**: Stage 3 は **20,000 step で SNR +8.91 dB に飽和**する
（40,000 は半分無駄）。Stage 2 の val はまだ下がる余地がある。

**手元の M4 Max で完結する**（D-027。ラベル生成は CPU で 40 分、学習は MPS で約 1.3 時間）。
ラベル生成に MPS を使わないのは CPU と bit 一致しないため（M-21）。

⚠️ **スモークは「回ること」の確認であって品質の確認ではない。**
損失が下がったことを品質の根拠に書かない。

⚠️ **合成には教師を呼ばない。** `synthesize_student.py` は生徒だけで動く。
ここで教師を混ぜると評価が意味を失う。
