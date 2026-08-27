"""音素クラス別 2–8 kHz スペクトル平坦度 (SFM) プローブ。

論文は SCOREQ 4.09 の裏で sibilant が whistly になる欠陥を見逃した。集約スコアでは
出ず、**音素クラス別の 2–8 kHz スペクトル平坦度**で初めて出た（教師 0.689 →
生徒 0.590）。日本語は摩擦音が多く、無声化母音 `I` `U` も音響的にほぼ摩擦雑音なので
このプローブは評価パイプラインの必須項目にする。

**採用設定は B-6 のグリッド探索で決めた**（`scripts/b6_flatness_grid.py` /
`reports/b6_flatness_grid.json`）。生徒の評価でも必ず同じ設定を使うため、
ここに定数として凍結する。値を変えると教師ベースラインと比較できなくなる。

窓の規則（報告に明記した通り）:

* `hop = n_fft // 4`
* Hann 窓（periodic）/ `center=True` / reflect padding。STFT フレーム `t` の中心は
  サンプル `t*hop`
* 音素 i のフレーム区間は教師の `w_ceil = ceil(w)` から
  `[cumsum(ceil(dT))[i-1], cumsum(ceil(dT))[i])`（hop 256 の教師フレーム単位）。
  `guard` はこの**教師フレーム単位**で両端から削る
* サンプル区間は ×256。その区間に**中心が入る** STFT フレームだけを使う
* SFM は 2–8 kHz のビンについて `exp(mean(log X)) / mean(X)`、
  `X = |S|**power`。音素 1 インスタンスの値はそのフレーム平均
* **帯域が丸ごとゼロのフレーム（デジタル無音）は NaN にして落とす。** 実測で全
  81,684 フレーム中 3,571 (4.37%) がこれに当たる。落とした後の使用ビンの最小振幅は
  2.873e-10 で、log の下駄 `_FLOOR = 1e-30` に触れるビンは 0 個（B-6 で確認）

⚠️ **この値の読み方には 3 つの制限がある**（B-6 の照合で判明）:

1. **生徒の音声も必ず教師と同じ int16 (scale 32767) 往復に通してから測ること。**
   通さないと低エネルギー区間で偽の差が出る。実際 `geminate` の基準値 0.7978 は
   教師の性質ではなく**量子化床**を測っている（`cl` の波形 RMS は中央値 1.4 LSB、
   ±0.5 LSB の再量子化で SFM が自身の CI 半幅の 1.6 倍動く）。
2. **測っているのは音声の 36.6% にあたる音素ラベル区間だけ。** 残りは
   intersperse PAD `_` が 54.7%、アクセント記号が 5.9%。しかも **PAD は無音ではなく
   実音声を持ち、破擦音・破裂音の摩擦バーストがそこに入っている**
   （破擦音では PAD の帯域 RMS が音素区間の 3.6 倍）。したがって `affricate` の
   基準値 0.8031 も `cl` と同じく「主に閉鎖区間を測った値」として読むこと。
3. **凍結した CI は span 規則を変えると 4〜8 倍動く**（span を「音素+後続 PAD」に
   変えると stop 0.7272→0.7016 / affricate 0.8031→0.7844 / fricative 0.7346→0.7192）。
   絶対値としてではなく、**同一規則下の教師比としてのみ**使うこと。
"""

from __future__ import annotations

import numpy as np

SR = 22050
HOP = 256                      # 教師の hop。フレーム区間の単位
BAND_HZ = (2000.0, 8000.0)     # 論文の帯域
_FLOOR = 1e-30                 # log の下駄

# --- 採用設定（B-6 / reports/b6_flatness_grid.json）-------------------------
# 基準は「fricative+affricate vs vowel+nasal」の AUC 最大。ただし:
#
# * **guard は生の AUC で決めてはいけない。** guard=1 は span < 3 frame の音素を
#   全部落とす（実測 13,923 -> 3,073 = 78% 減、devoiced は 471 -> 35）。生の AUC は
#   guard=1 のほうが高く出る (0.9258 vs 0.8704) が、これは「長い音素だけ見た」効果。
#   guard=0 を同じ span>=3 の部分集合に制限して比べると **6/6 の設定で guard=0 が上**
#   （delta +0.0056〜+0.0099、対応のある発話クラスタ bootstrap 95%CI がすべて 0 を含まない）
# * power=1 と power=2 は AUC が**区別できない**（|delta| 4.7e-5、CI [-0.0024, +0.0024]。
#   guard=0 内では僅かに power=2 のほうが高い）。**同点として扱い**、検出感度
#   （gap / クラス平均 CI 半幅）が高い power=1 を採った（33.9 vs 31.1）。
#   「power=1 が優れている」とは言えない
N_FFT = 1024
GUARD = 0
POWER = 1
HOP_RULE = "n_fft // 4"
HOP_STFT = N_FFT // 4        # = 256。偶然だが教師の hop と一致する

# 教師ベースライン（`data/pack_sibdense` 209 発話 / 採用設定）。
# **生徒の評価はこの値との差で報告する。**（mean, ci_low, ci_high, n）
TEACHER_BASELINE = {
    "vowel":         (0.6448, 0.6426, 0.6469, 7164),
    "devoiced":      (0.7408, 0.7351, 0.7465, 495),
    "nasal":         (0.6129, 0.6081, 0.6177, 1464),
    "stop":          (0.7275, 0.7245, 0.7304, 2740),
    "fricative":     (0.7351, 0.7310, 0.7393, 1069),
    "affricate":     (0.8037, 0.7994, 0.8083, 512),
    "approximant":   (0.5874, 0.5821, 0.5927, 924),
    "geminate":      (0.7987, 0.7952, 0.8020, 417),
    "fricative_noz": (0.7320, 0.7280, 0.7363, 1003),
    "OBSTRUENT":     (0.7573, 0.7540, 0.7606, 1581),
    "SONORANT":      (0.6394, 0.6373, 0.6415, 8628),
}

# `scripts/b6_build_evalset.py` の CLASSES と**同一でなければならない**。
# b6_flatness_grid.py が起動時に突き合わせる。
# ⚠️ `z` は語頭で破擦音 [dz]、母音間で摩擦音 [z] になるが OpenJTalk は区別しない。
# ここでは fricative に置いてある（B-6 で `z` 除外時の差も測った）。
CLASSES: dict[str, tuple[str, ...]] = {
    "vowel":       ("a", "i", "u", "e", "o"),
    "devoiced":    ("I", "U"),
    "nasal":       ("m", "n", "ny", "my", "N_m", "N_n", "N_ng", "N_uvular"),
    "stop":        ("p", "t", "k", "b", "d", "g", "py", "ty", "ky",
                    "by", "dy", "gy", "kw", "gw"),
    "fricative":   ("s", "sh", "h", "hy", "f", "z", "v"),
    "affricate":   ("ts", "ch", "j"),
    "approximant": ("r", "ry", "w", "y"),
    "geminate":    ("cl",),
}
TOK2CLASS = {t: c for c, ts in CLASSES.items() for t in ts}

# 欠陥検出の主対象。論文の sibilant whistle はここに出る
# ⚠️ 破裂音 (stop) は**含まない**。選択基準にしたのは「摩擦性を持つ 2 クラス」なので
# `OBSTRUENT`（阻害音＝破裂音も含む）という名前は誤り。`SIBILANT_GROUP` を使うこと。
SIBILANT_GROUP = ("fricative", "affricate")
OBSTRUENT = SIBILANT_GROUP   # 後方互換。新しいコードでは使わない
SONORANT = ("vowel", "nasal")

# 帯域内 RMS の教師ベースライン `(median, 母音比)`。
# ⚠️ **SFM は尺度不変なので、エネルギーが無い区間も「平坦」と出る。**
# geminate `cl` は SFM 0.7978 で全クラス最高だが、帯域内 RMS は母音の **0.005 倍**
# ＝ 閉鎖（ほぼ無音）である。摩擦雑音が豊かなのではない。
# **生徒の評価では SFM と RMS を必ず一緒に見ること。** 特に cl / stop の SFM 単独を
# 音質の指標として読んではいけない。
BAND_RMS_BASELINE = {
    "vowel":         (1.800e-01, 1.000),
    "devoiced":      (1.036e-01, 0.576),
    "nasal":         (5.408e-02, 0.300),
    "stop":          (1.288e-01, 0.715),
    "fricative":     (2.260e-01, 1.256),
    "affricate":     (6.415e-02, 0.356),
    "approximant":   (1.951e-01, 1.084),
    "geminate":      (8.083e-04, 0.004),
}


def stft_mag(wav: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """`[n_bins, n_frames]` の振幅スペクトル。フレーム t の中心はサンプル t*hop。

    librosa を経由せず numpy だけで組む（依存を減らし、窓と centering を明示するため）。
    """
    x = np.asarray(wav, dtype=np.float64)
    pad = n_fft // 2
    x = np.pad(x, pad, mode="reflect") if x.size > pad else np.pad(x, pad, mode="constant")
    n_frames = 1 + (len(x) - n_fft) // hop
    win = np.hanning(n_fft + 1)[:-1]        # periodic Hann
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    return np.abs(np.fft.rfft(x[idx] * win, axis=1)).T


def band_slice(n_fft: int, sr: int = SR, band: tuple[float, float] = BAND_HZ) -> slice:
    """2–8 kHz（両端含む）に入るビンの slice。"""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    lo = int(np.searchsorted(freqs, band[0], side="left"))
    hi = int(np.searchsorted(freqs, band[1], side="right"))
    return slice(lo, hi)


def frame_sfm(mag: np.ndarray, bins: slice, power: int) -> np.ndarray:
    """フレームごとの SFM。帯域が全ゼロ（デジタル無音）のフレームは NaN。"""
    x = mag[bins, :].astype(np.float64)
    if power != 1:
        x = x ** power
    am = x.mean(axis=0)
    gm = np.exp(np.log(np.maximum(x, _FLOOR)).mean(axis=0))
    out = np.full(am.shape, np.nan)
    ok = am > 0
    out[ok] = gm[ok] / am[ok]
    return out


def spans_from_durations(dT: np.ndarray, token_classes) -> list[tuple[str, int, int]]:
    """`dT` と各トークンのクラス名から `(class, f0, f1)` を作る。

    **教師は `w_ceil = ceil(w)` でフレームを割り当てる**（`models.py` の `infer()`）。
    呼び出し側で `sum(ceil(dT))*HOP == len(wav)` を assert すること。
    """
    ends = np.cumsum(np.ceil(np.asarray(dT, dtype=np.float64))).astype(np.int64)
    starts = np.concatenate([[0], ends[:-1]])
    return [(c, int(s), int(e))
            for c, s, e in zip(token_classes, starts, ends) if c is not None]


def class_band_rms(
    wav: np.ndarray,
    sr: int,
    spans,
    *,
    n_fft: int = N_FFT,
    guard: int = GUARD,
) -> dict[str, list[float]]:
    """`{クラス名: [音素インスタンスごとの 2–8 kHz RMS, ...]}`。

    **`class_flatness` と必ず対で使う。** SFM は尺度不変なので、
    **無音は「完全に平坦」に見える**。`geminate` の基準値 0.7987 は閉鎖区間の
    int16 量子化床を測っていて、教師の性質ではない（M-27）。
    RMS を併記しないと、生徒が音を出さなくなったことを「摩擦音が豊かになった」と
    誤読する。span の切り方は `class_flatness` と同一。
    """
    if sr != SR:
        raise ValueError(f"sr={sr} は想定外（教師は {SR} Hz 固定）")
    hop = n_fft // 4
    mag = stft_mag(wav, n_fft, hop)
    bins = band_slice(n_fft, sr)
    # mag は [n_bins, n_frames]。**帯域方向は axis=0**（軸を取り違えると
    # 「フレームごとの RMS」ではなく「ビンごとの時間平均」になる）
    rms = np.sqrt((mag[bins, :] ** 2).mean(axis=0))
    n_stft = rms.shape[0]

    out: dict[str, list[float]] = {c: [] for c in CLASSES}
    for cls, f0, f1 in spans:
        a, b = f0 + guard, f1 - guard
        if b <= a:
            continue
        t0 = -(-(a * HOP) // hop)
        t1 = min(-(-(b * HOP) // hop), n_stft)
        if t1 <= t0:
            continue
        v = rms[t0:t1]
        v = v[np.isfinite(v)]
        if v.size:
            out.setdefault(cls, []).append(float(v.mean()))
    return out


def class_flatness(
    wav: np.ndarray,
    sr: int,
    spans,
    *,
    n_fft: int = N_FFT,
    guard: int = GUARD,
    power: int = POWER,
) -> dict[str, list[float]]:
    """`{クラス名: [音素インスタンスごとの SFM, ...]}`。

    `spans` は `(class, f0, f1)` の列。**f0/f1 は hop 256 の教師フレーム番号**
    （`spans_from_durations` が作る）。既定の設定は B-6 で凍結した採用設定。
    """
    if sr != SR:
        raise ValueError(f"sr={sr} は想定外（教師は {SR} Hz 固定）")
    hop = n_fft // 4
    mag = stft_mag(wav, n_fft, hop)
    sfm = frame_sfm(mag, band_slice(n_fft, sr), power)
    n_stft = sfm.shape[0]

    out: dict[str, list[float]] = {c: [] for c in CLASSES}
    for cls, f0, f1 in spans:
        a, b = f0 + guard, f1 - guard
        if b <= a:
            continue                       # guard で潰れた区間は捨てる
        # サンプル区間 [a*HOP, b*HOP) に中心が入る STFT フレーム
        t0 = -(-(a * HOP) // hop)          # ceil
        t1 = min(-(-(b * HOP) // hop), n_stft)
        if t1 <= t0:
            continue
        v = sfm[t0:t1]
        v = v[np.isfinite(v)]
        if v.size:
            out.setdefault(cls, []).append(float(v.mean()))
    return out
