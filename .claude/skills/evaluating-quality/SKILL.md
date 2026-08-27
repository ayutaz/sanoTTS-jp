---
name: evaluating-quality
description: Use when measuring or reporting saanoTTS-jp audio quality — SCOREQ, UTMOS, CER, spectral flatness, or any comparison of student against teacher. Covers why the absolute numbers are not comparable to the paper and which metric configurations are actively wrong.
---

# 品質を測る・報告する

## 原則

**日本語では指標が較正されていない。絶対値を論文の英語スコアと比べない。**

論文自身がベトナム語・インドネシア語について
"we report only the ratio to the corresponding teacher because absolute SCOREQ
values are not calibrated for comparisons across languages" と書いている。

**教師比と人間音声比の両方で報告する**（D-013）。人間側の分母は教師の元コーパス
（つくよみちゃんコーパス）を使う — 話者と録音条件が揃う。

| 指標 | 教師 | 実人間 | 教師/人間 |
|---|---:|---:|---:|
| **SCOREQ synthetic/nr**（主指標） | 2.0488 | 2.4983 | **0.820** |
| UTMOS（併記） | 1.7479 | 2.3047 | **0.758** |

**実人間の日本語スタジオ録音でも SCOREQ は 2.4983 しか出ない。**
論文の英語（教師 4.68 / embedded 2.54）と並べて書かない。

**生徒の目標**（論文の英語 embedded の教師比 2.54/4.68 = **0.5427** を当てはめる）:
SCOREQ **1.112** / UTMOS 1.107。⚠️ **UTMOS 比 0.6335 と混同しない**
（一度これで目標を 16.7% 過大にした）。

## SCOREQ の呼び方

```python
from saanotts_jp.scoreq_metric import score_files
score_files(paths, domain="synthetic", mode="nr")
```

| やってはいけないこと | 何が起きるか |
|---|---|
| `scoreq.Scoreq` を直接呼ぶ | torchaudio 2.13 が torchcodec を要求して `ImportError` |
| `data_domain="natural"` を使う | **伝送劣化モデル**（NISQA TRAIN SIM）。合成音を実人間より高く採点し (1.039)、UTMOS と無相関 (r=+0.141) |
| `mode="ref"` を MOS のように読む | **向きが逆**（距離。低いほど良い）。NMR 1 本で比が 1.114〜1.318 に振れる |

`uv run --extra eval python ...` で実行する。

## 音素クラス別スペクトル平坦度（集約スコアで見えない欠陥）

論文は SCOREQ 4.09 の裏で sibilant が whistly になる欠陥を見逃した。
**集約スコアでは出ない。** 設定は `src/saanotts_jp/flatness.py` に凍結:
`n_fft=1024 / hop=256 / guard=0 / power=1`。

```python
from saanotts_jp.flatness import class_flatness, TEACHER_BASELINE, BAND_RMS_BASELINE
```

⚠️ **SFM は必ず帯域内 RMS と併記する。** SFM は尺度不変なので、
**無音は「完全に平坦」に見える**。`geminate` の基準値 0.7987 は閉鎖区間の
int16 量子化床を測っていて、教師の性質ではない（波形 RMS は中央値 1.4 LSB）。

⚠️ **生徒の音声も教師と同じ int16 (scale 32767) 往復に通してから測る。**
通さないと低エネルギー区間で偽の差が出る。

⚠️ **測っているのは音声の 36.6%**（音素ラベル区間のみ）。PAD `_` が 54.7% を占め、
破擦音・破裂音のバーストはそこに入っている。`affricate` の値も「主に閉鎖区間」。

⚠️ **絶対値ではなく同一規則下の教師比で使う。** span 規則を変えると平均が CI の
4〜8 倍動く。

## 評価セットの作り方

**narrow set で測らない。** 論文は narrow test set で SCOREQ 3.07 を出したが、
実際は **1.35 の過大評価**だった。

- **教師の学習テキストを除外する**（`data/splits/exclusions_teacher_ft.txt`、102 uid）。
  `jsut/voiceactress100` と `jsut/repeat500` は本文が 98/100 共通で、
  1 文字違いなので NFKC 重複排除では併合されない
- **テンプレート文を使わない**
- **クラスごとの n を先に確保する。** 「無声化母音のほうが平坦」を n=3 で書いて
  反証された（C-004）。`scripts/b6_build_evalset.py` はクラスごとに 420 音素を確保する
- **アクセント型ミニマルペア**（橋/箸/端、雨/飴）を必ず入れる。
  これが無いとアクセントの誤りは検出できない

## n が小さいときの書き方

n<30 では「有意差なし」を「差が無い」と書かない。**「n=24 では検出できない」**と書く。
検出可能最小差を併記する。

```
✗ EMA あり/なしで SCOREQ に差は無い
✓ SCOREQ は有意差なし（差 -0.0015 / p=0.876 / n=24 の検出限界は 0.028）
```

対応のあるデータ（同じ文の対）には**対応のある検定**を使う。
一度 Welch で検定して p=0.055 と書いたが、対応ありなら p=0.0050 だった。

## WER ではなく CER。しかも「かな CER」

日本語は分かち書きが問題になるので **CER を主指標**にする。
**さらに、参照と仮説の両方を読み（かな）に落としてから比較する。**

```python
# scripts/measure_cer.py の to_kana()
K.text_to_intermediate(text, table)  # → アクセント記号を落として比較
```

⚠️ **表記のまま測ると符号が逆転する**（C-023）。Whisper は同じ音を漢字でも
ひらがなでも書き起こすので、**正しく読めていても CER が跳ね上がる**:

```
和歌山県太地町 → 教師の書き起こし「わけわけんてじまち」
    表記 CER 1.286 ← 跳ねる / かな CER 0.375 ← 実態
```

実測（v2 / n=24）: **かな CER 教師 0.135 / 生徒 0.178（差 +0.043）**。
表記のままだと 0.278 / 0.225 で**生徒のほうが良く見えていた**。

⚠️ **Whisper 自体の誤りが両方に乗る**（教師でも 0.135）。教師との差で読む。
⚠️ paired-t p=0.126 / Wilcoxon p=0.040 と食い違う。**n=24 では確定できない**。
