---
name: student-training
description: Use when writing or changing the saanoTTS-jp student model, its losses, or the training loop — Duration/Acoustic/Decoder, iSTFT framing, the c-line, lambda weights, the discriminator, or anything that reads a teacher label pack. Covers the silent misalignments that produce numbers without producing a working model.
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

## 学習を回す前に

```bash
uv run python scripts/train_student.py --pack data/pack_sibdense --smoke
```

⚠️ **スモークは「回ること」の確認であって品質の確認ではない。**
12 step では何も学習していない。損失が下がったことを品質の根拠に書かない。

**ローカルで本学習しない**（D-012）。vast.ai の手順は `docs/vastai-runbook.md`。
