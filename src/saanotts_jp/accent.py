"""アクセント型ミニマルペアのピッチ評価。**設定をここに凍結する。**

CLAUDE.md の未解決 #5「アクセント型の再現性」を数字にするためのモジュール。
生徒 duration net は音素IDしか見ないので、ピッチアクセントは**音素列に既に入っている
記号 `[` 上昇 / `]` 下降核 / `#` 句境界だけ**で運ばれる。これで足りるかは
集約指標（SCOREQ / UTMOS / CER）では検出できない。

`flatness.py` と同じ作法で、値を変えると教師ベースラインと比較できなくなる定数を
ここに固定する。

## 評価の骨格

ミニマルペア群（例 雨(1型)/飴(0型)）を**固定キャリア文**に入れて教師と生徒で合成し、
モーラ単位の log-F0 を取る。群内のメンバーは**記号を除いた音素列が完全に同一**なので、
出力の差は**すべてアクセント記号に起因する**。したがって問うべきは
「生徒が記号を使っているか」ではなく **「教師と同じ向きに使っているか」**。

主指標は**ペア内コントラストの向き** `cos(Δ_T, Δ_S)`:

    Δ_T = 中心化した教師の mora log-F0 (A) − 同 (B)
    Δ_S = 中心化した生徒の mora log-F0 (A) − 同 (B)

`cos > 0` は「二乗距離によるテンプレート照合の 2AFC が正解」と**数学的に同値**
（`|S_A−T_A|²+|S_B−T_B|²` と入れ替え版の差が `−2 Δ_S·Δ_T` に潰れる）ので、
2AFC を別に実装しない。

## ⚠️ 読み方の制限（すべてパイロットで実測した性質）

1. **ペアの音が違うこと自体は証拠にならない。** `[` `]` `#` は教師で実フレームを持つ
   （`reports/durations/durations.npz` 全 23,399 発話で `[` 平均 1.485 フレーム /
   `]` 1.234 / `#` 2.174、合計フレームの 6.35%）。アクセントを完全に無視するモデルでも
   A と B は違う音になる。**必ず向き（cos の符号）で判定する。**
2. **chance は 0.5 ではない。** 全ペアが同じキャリアを共有するので Δ どうしに相関が残る。
   経験的ヌルを順列で作って比較すること（`empirical_null`）。
3. **教師ゲートを先に通す。** 教師自身が弁別していないペアで生徒を減点しない
   （`TEACHER_GATE_ST`）。
4. **語を文頭に置くと指標が壊れる。** 文頭モーラの F0 が取れず、`]` 境界の下降 AUC が
   **教師自身で 0.4624** まで落ちた（文中配置なら 0.7049）。キャリアを変えたら
   必ず教師ベースラインを取り直す。
5. **ピーク位置（argmax モーラ）は使わない。** 高原状の輪郭で argmax が跳ぶ。
6. **`fmin` を下げるとオクターブ誤りが混ざる。** つくよみちゃんは実測 p50 ≈ 347 Hz。
   `fmin=80` だと全体 p1 が 118 Hz（半分の誤り）まで落ちた。
7. **piper-plus は 1 型（頭高）で `]` と `[` を同じ境界に出す**
   （`g2p/piper_plus_g2p/japanese.py` の挿入判定が elif ではなく独立した `if`）。
   教師も生徒も同じ入力を見るのでパリティは保たれるが、
   **`]` 単独と `][` は分けて集計する**。
"""

from __future__ import annotations

import itertools

import numpy as np

# --- 音声側（教師のフレーム規約に合わせる）---------------------------------
SR = 22050
HOP = 256                  # 教師の hop。pyin の hop と揃えるとフレーム番号がそのまま使える
FRAME_LENGTH = 1024        # pyin の窓
FMIN = 150.0               # ⚠️ 下げるとオクターブ誤り。制限 6 を参照
FMAX = 600.0
REF_HZ = 100.0             # semitone の基準。差しか使わないので値自体に意味はない

# --- 判定のしきい値 ---------------------------------------------------------
TEACHER_GATE_ST = 1.5
"""教師が弁別していると見なす `|Δ_T|`（semitone、モーラベクトルの L2 ノルム）。

パイロットの実測分布（文中キャリア・38 ペア）は 1.49〜7.4 で、
1.5 を割ったのは 2 ペアだけだった。**この値は「教師が区別していない」の線引きであって、
生徒の合格ラインではない。**
"""

MARKS = frozenset({"[", "]", "#"})
"""境界に立ちうるアクセント記号。ポーズ・BOS/EOS はモーラ境界には現れない。"""


def f0_track(y: np.ndarray, sr: int = SR) -> np.ndarray:
    """`librosa.pyin` の凍結設定。フレーム `t` の中心はサンプル `t*HOP`。"""
    import librosa  # noqa: PLC0415  (eval 用の重い依存を import 時に引かない)

    f0, _, _ = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=sr,
                            frame_length=FRAME_LENGTH, hop_length=HOP, center=True)
    return f0


def semitone(f0) -> np.ndarray:
    """Hz → semitone。非有限・非正は NaN に落とす。"""
    a = np.asarray(f0, dtype=float)
    return 12.0 * np.log2(np.where(np.isfinite(a) & (a > 0), a, np.nan) / REF_HZ)


def mora_spans(units, phoneme_ids, pad_id: int, frames) -> list[tuple[str, int, int]]:
    """モーラごとのフレーム区間 `[lo, hi)` を作る。**記号は区間を持たない。**

    Parameters
    ----------
    units
        `(label, n_phonemes, is_mark)` を音素列の消費順に並べたもの。
        `n_phonemes` は 1 モーラが消費する音素数（`きょ` → `ky` `o` で 2）。
    phoneme_ids
        `encode_intermediate` が作った音素ID列（`^`, PAD, …, EOS を含む）。
    frames
        `phoneme_ids` と同じ長さの、ID ごとのフレーム数（教師は `ceil(dT)`）。

    区間の規則は **「そのモーラの音素 + 各音素の直後の PAD」**。
    ⚠️ PAD を外すと足りない — intersperse PAD が全フレームの過半を占める
    （`flatness.py` の制限 2 と同じ事情）。記号 `[` `]` `#` とその PAD は含めない。
    """
    frames = np.asarray(frames, dtype=int)
    if len(frames) != len(phoneme_ids):
        raise ValueError(f"frames {len(frames)} != ids {len(phoneme_ids)}")
    # 音素ごとの ID 位置。encode_intermediate と同じ規則:
    # 先頭 `^` + PAD の 2 個を飛ばし、PAD 以外の音素の後ろにだけ PAD が 1 個入る
    pos, positions = 2, []
    while pos < len(phoneme_ids) - 1:                 # 末尾の EOS は音素ではない
        positions.append(pos)
        # その音素自身が PAD なら後ろに PAD を挟まない（encode_intermediate と同一）
        pos += 1 if phoneme_ids[pos] == pad_id else 2
    if pos != len(phoneme_ids) - 1:
        raise ValueError(f"ID 列を消費しきれていない: {pos} != {len(phoneme_ids) - 1}")
    n_ph = sum(n for _, n, _ in units)
    if n_ph != len(positions):
        raise ValueError(f"units の音素数 {n_ph} != ID 列の音素数 {len(positions)}")

    cum = np.concatenate([[0], np.cumsum(frames)]).astype(int)
    spans, k = [], 0
    for label, n, is_mark in units:
        idxs = positions[k:k + n]
        k += n
        if is_mark:
            continue
        lo = cum[idxs[0]]
        hi = cum[min(idxs[-1] + 2, len(cum) - 1)]     # 最後の音素 + その直後の PAD
        spans.append((label, int(lo), int(hi)))
    return spans


def mora_f0(y: np.ndarray, spans, sr: int = SR) -> np.ndarray:
    """モーラごとの F0（Hz）。区間内の**有限値の中央値**。無ければ NaN。"""
    f0 = f0_track(y, sr)
    out = []
    for _, lo, hi in spans:
        seg = f0[lo:min(hi, len(f0))]
        seg = seg[np.isfinite(seg)]
        out.append(float(np.median(seg)) if seg.size else np.nan)
    return np.asarray(out, dtype=float)


def boundary_marks(units) -> list[frozenset]:
    """モーラ境界 `i`（モーラ `i` と `i+1` の間）に立っている記号の集合。

    返り値の長さは **モーラ数 − 1**。先頭モーラより前の記号は境界を持たないので落とす。
    """
    labels: list[frozenset] = []
    pend: set[str] = set()
    seen = False
    for label, _, is_mark in units:
        if is_mark:
            if seen and label in MARKS:
                pend.add(label)
            continue
        if seen:
            labels.append(frozenset(pend))
        pend = set()
        seen = True
    return labels


def auc(pos, neg) -> float:
    """Mann–Whitney の U から出す AUC。`flatness.py` の devoiced AUC と同じ流儀。"""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not pos.size or not neg.size:
        return float("nan")
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1
    return float((ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2)
                 / (pos.size * neg.size))


def contrast(a_t, a_s, b_t, b_s, mask) -> dict:
    """ペア (A, B) のコントラスト。`mask` は群内の全メンバーで F0 が取れたモーラ。

    中心化してから差を取るので、**話者の平均ピッチや全体の高さは落ちる**。
    """
    def c(v):
        v = np.asarray(v, float)[mask]
        return v - v.mean()

    d_t, d_s = c(a_t) - c(b_t), c(a_s) - c(b_s)
    n_t, n_s = float(np.linalg.norm(d_t)), float(np.linalg.norm(d_s))
    return {"n_morae": int(mask.sum()), "norm_teacher_st": n_t, "norm_student_st": n_s,
            "cos": float(d_t @ d_s / (n_t * n_s + 1e-12)),
            "delta_teacher": d_t.tolist(), "delta_student": d_s.tolist()}


def empirical_null(pairs, *, cross_type_only: bool = False) -> dict:
    """**chance は 0.5 ではない。** 別グループの Δ_T と突き合わせた順列ヌル。

    全ペアが同じキャリアと同じモーラ数を共有するので、
    「生徒がアクセント記号を教師と同じ向きに使っている」以外の理由でも cos は正に寄る。

    `cross_type_only=True` にすると**アクセント型の組合せが違う**ものだけを使う
    （0対1 の Δ_S を 1対2 の Δ_T に当てる）。こちらのほうが厳しい。
    """
    vals = []
    for a, b in itertools.permutations(pairs, 2):
        if a["group"] == b["group"]:
            continue
        if len(a["delta_student"]) != len(b["delta_teacher"]):
            continue
        if cross_type_only and a["type_pair"] == b["type_pair"]:
            continue
        x, y = np.asarray(a["delta_student"]), np.asarray(b["delta_teacher"])
        vals.append(float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12)))
    vals = np.asarray(vals)
    return {"n": int(vals.size), "cos_mean": float(vals.mean()) if vals.size else float("nan"),
            "frac_cos_positive": float((vals > 0).mean()) if vals.size else float("nan")}


def cluster_bootstrap(pairs, stat, *, n_boot: int = 10000, seed: int = 0) -> dict:
    """**グループ単位**のクラスタ bootstrap。

    同じ群の 2 キャリア・最大 3 メンバーは独立でないので、発話単位で resample すると
    CI が狭く出る（`measure_cer.py` が paired 検定を使うのと同じ理由）。
    """
    groups = sorted({p["group"] for p in pairs})
    by_group = {g: [p for p in pairs if p["group"] == g] for g in groups}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        picked = [p for g in rng.choice(groups, len(groups), replace=True)
                  for p in by_group[g]]
        v = stat(picked)
        if np.isfinite(v):
            draws.append(v)
    draws = np.asarray(draws)
    return {"point": float(stat(pairs)), "n_boot": int(draws.size),
            "ci95": [float(np.percentile(draws, 2.5)),
                     float(np.percentile(draws, 97.5))] if draws.size else [float("nan")] * 2}
