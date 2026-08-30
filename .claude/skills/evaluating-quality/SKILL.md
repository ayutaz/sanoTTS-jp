---
name: evaluating-quality
description: Use when measuring or reporting sanoTTS-jp audio quality — SCOREQ, UTMOS, CER, spectral flatness, or any comparison of student against teacher. Covers why the absolute numbers are not comparable to the paper and which metric configurations are actively wrong.
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

## ⚠️ 現在の指標が構造的に見ていないもの

**主指標 SCOREQ synthetic/nr と UTMOS は、どちらも「自然さ」の予測器**（D-020）。
**信号の質は測っていない。**

上流（英語）は公開ドキュメントで
*"a metallic artifact scores **high** on [SCOREQ/UTMOS] and low on DNSMOS"*
と書いている。つまり**金属的アーティファクトは、現在の構成では
出ていても検出できないどころか、加点される可能性がある**。

これは B-6 で摩擦音の欠陥を見つけたときと**同じ構図**
（集約スコアが盲目で、分解能を上げたプローブが見つけた）。

## DNSMOS — **測った（M-49 / M-50）。併記プローブであって合否ゲートではない**

```python
from saanotts_jp.dnsmos_metric import score_path   # ← 必ずこのラッパを通す
score_path("a.wav")   # {sig_mos, bak_mos, ovrl_mos, p808_mos, n_windows, ...}
```

| 指標 | 教師 | 実人間 | 生徒 | 教師/人間 |
|---|---:|---:|---:|---:|
| DNSMOS OVRL | 2.7299 | **2.7881** | 2.1088 | **0.979** |
| SCOREQ synthetic/nr | 2.0488 | 2.4983 | 1.2063 | 0.820 |
| UTMOS | 1.7479 | 2.3047 | 1.3585 | 0.758 |

⚠️ **生徒の列は v2 の値**（M-49 / M-50 の時点）。**成果物は v3**（D-037）で、
v3 は SCOREQ **1.2716** / DNSMOS OVRL **2.1730**（M-61）。
⚠️ **v3 の値は前処理を揃えて測り直したもの**で、旧記録（SCOREQ 比 0.6613 /
DNSMOS 比 0.8385）は**生徒だけ前後 0.3 秒のパディングが無い**比較だった（C-038）。
**数値を報告する前に `scripts/release_metrics.py` の G1 を通すこと。**

**上流の言う「metallic は SCOREQ で高・DNSMOS で低」のパターンは出ていない。**
3 指標とも同じ順序で下がる（M-49）。**だから DNSMOS で新しい欠陥は見つからなかった。**

**測る・読むときに必ず守ること**:

- **5 点満点ではない。** 較正多項式の像は SIG [1.1421, 4.0101] /
  BAK [1.0814, 4.3580] / OVRL [1.0938, 3.9318]。**下限が約 1.1 なので比は圧縮される。**
  主たる読みは**対応のある差 Δ** にする
- **実人間の日本語でも OVRL は 2.7881。** しかも**教師のほうが人間より高く出る
  組み合わせがある**（`T_b5`/人間 = 1.027）。絶対値を英語の公表値と並べない（C-012）
- ⚠️ **`p808_mos` は別 ONNX の独立予測。4 スコアを平均しない。**
  リサンプラ違いで OVRL が 0.0015 動くところ **P.808 は 0.0663 動く**（44 倍）。
  自己連結 vs ゼロ埋めでも **0.22 動く**（生徒差 0.27 と同オーダー）
- ⚠️ **DNSMOS が反応しない劣化が実在する**（M-50 の G6）。
  **SNR 10.5 dB のハードクリップで 4 スコアとも下がらず、むしろ上がる。**
  白色雑音では SIG が上がり BAK が下がる（P.835 の設計どおり）
- ⚠️ **位相破壊（Griffin-Lim, SNR −2.7 dB）に DNSMOS はほぼ反応しない** —
  同 SNR の白色雑音より OVRL で 1.33〜1.53 ポイント甘い。
  **フレームレート 86.13 Hz の AM にだけは SIG が選択的**（雑音より厳しい）
- **入力は 16 kHz（Nyquist 8 kHz）。8–11 kHz の欠陥は原理的に見えない。**
  **2–8 kHz の平坦度プローブの代わりにはならない。両方が要る**
- ⚠️ **食い違ったときどちらを採るかを先に決めない。** 食い違い自体が情報

### ⚠️ `speechmos` は UTMOS と**パッケージ名が衝突する**

`torch.hub` の `tarepan/SpeechMOS:v1.2.0` が**同名の `speechmos` パッケージ**を同梱する。
素の `import speechmos.dnsmos` は**両方向で落ちる**（先に読んだ方が勝つ）。
`saanotts_jp.dnsmos_metric` は実ファイルを別名で読むので**同一プロセスで両立する**。
**`import speechmos` を自分で書かないこと。**

### ⚠️ この環境では `import pyworld` が落ちる

setuptools 84.0.0 が `pkg_resources` を同梱しないため
`ModuleNotFoundError: pkg_resources`。`scripts/e1_dnsmos.py` の
`_ensure_pkg_resources()` がプロセス内 shim の例。

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
⚠️ **v3 は 0.1671（教師との差 +0.0320、p=0.31 で有意差なし）**（M-61）。
⚠️ **v2 → v3 の改善は検出できない**（−0.0105 [−0.037, +0.018]）。

⚠️ **Whisper 自体の誤りが両方に乗る**（教師でも 0.135）。教師との差で読む。
⚠️ paired-t p=0.126 / Wilcoxon p=0.040 と食い違う。**n=24 では確定できない**。
