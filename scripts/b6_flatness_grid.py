#!/usr/bin/env python3
"""B-6: 音素クラス別 2–8 kHz スペクトル平坦度プローブの窓設計を凍結する。

論文は SCOREQ 4.09 の裏で sibilant が whistly になる欠陥を見逃した。集約スコアでは
検出できず、音素クラス別のスペクトル平坦度で初めて出た（教師 0.689 → 生徒 0.590）。
**その窓設定をここで決め、教師ベースラインを凍結する。**

過去の B-6 の主張は n が足りず全部反証された（`docs/decisions.md` C-004）。
今回は `data/pack_sibdense`（209 発話 / 全クラス 400 音素以上）で測る。

測る格子:
    n_fft ∈ {256, 512, 1024} × guard ∈ {0, 1} × power ∈ {1, 2}   = 12 点
    hop = n_fft // 4（全点共通の規則）

窓と区間の規則は `src/saanotts_jp/flatness.py` の docstring に書いた通り。要点:

* 音素 i のフレーム区間は教師の `w_ceil = ceil(w)` から
  `[cumsum(ceil(dT))[i-1], cumsum(ceil(dT))[i])`。`sum(ceil(dT))*256 == len(yT)` を assert
* `guard` は**教師フレーム（hop 256）単位**で両端から削る
* SFM は 1 STFT フレームごとに計算し、音素インスタンスの値はそのフレーム平均。
  **統計の単位は音素インスタンス**（フレームではない）

信頼区間は**発話単位のクラスタ・ブートストラップ**。同一発話内の音素は独立でないので、
音素を直接リサンプルすると CI が不当に狭くなる。参考のため素朴な音素単位 CI も出す。

実行:
    uv run python scripts/b6_flatness_grid.py
    uv run python scripts/b6_flatness_grid.py --boot 200        # 速く回す
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import subprocess
import sys
import time

import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
sys.path.insert(0, "~/Documents/piper-plus/src/python")
sys.path.insert(0, "~/Documents/piper-plus/src/python/g2p")

from piper_plus_g2p.encode import pua  # noqa: E402
from saanotts_jp import flatness as F  # noqa: E402
from saanotts_jp.labelpack import HOP, NUM_SYMBOLS, SR, PackReader  # noqa: E402

import b6_build_evalset as EV  # noqa: E402

# クラス定義が 2 か所にあるので必ず突き合わせる（片方だけ直すと黙ってズレる）
assert {k: tuple(v) for k, v in EV.CLASSES.items()} == F.CLASSES, "CLASSES が食い違う"

N_FFTS = (256, 512, 1024)
GUARDS = (0, 1)
POWERS = (1, 2)

# 出力するクラス対。(A, B) で AUC は「A の SFM が B より高い確率」
PAIRS = [
    ("fricative", "vowel"),
    ("affricate", "vowel"),
    ("fricative", "nasal"),
    ("devoiced", "vowel"),      # 日本語固有の関心
    ("devoiced", "fricative"),  # 同上
    ("fricative", "affricate"),
    ("OBSTRUENT", "SONORANT"),  # ← 採用設定の選択基準
]
SELECT_PAIR = ("OBSTRUENT", "SONORANT")

GROUPS = {"OBSTRUENT": F.OBSTRUENT, "SONORANT": F.SONORANT}
CLASS_NAMES = list(F.CLASSES)
# `z` を抜いた fricative。OpenJTalk は語頭 [dz] / 母音間 [z] を区別しない
CLASS_NAMES_EXT = CLASS_NAMES + ["fricative_noz"]


def build_id2tok() -> dict[int, str]:
    # ⚠️ glob は `~` を展開しない
    hits = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"))
    if not hits:
        raise SystemExit("教師 ckpt スナップショットが HF キャッシュに無い")
    pim = json.load(open(hits[0] + "config.json"))["phoneme_id_map"]
    # ⚠️ config.json の phoneme_id_map は 185 entry あるが num_symbols=173。
    # 173 以上は埋め込みの範囲外なので落とす（実測値、CLAUDE.md）
    return {v[0]: pua.CHAR2TOKEN.get(k, k) for k, v in pim.items() if v[0] < NUM_SYMBOLS}


def load_instances(pack: str, id2tok: dict[int, str]):
    """発話ごとに (wav, spans, tokens) を作る。**アライメントをここで assert する。**"""
    pr = PackReader(pack)
    utts = []
    for i in range(len(pr)):
        u = pr[i]
        ce = np.ceil(u["dT"].astype(np.float64)).astype(np.int64)
        assert int(ce.sum()) * HOP == len(u["yT"]), (
            f"utt {i}: sum(ceil(dT))*{HOP}={int(ce.sum())*HOP} != len(yT)={len(u['yT'])}")
        toks = [id2tok[int(p)] for p in u["ids"]]
        clss = [F.TOK2CLASS.get(t) for t in toks]
        spans = F.spans_from_durations(u["dT"], clss)
        keep = [t for t, c in zip(toks, clss) if c is not None]
        assert len(keep) == len(spans)
        utts.append((u["yT"].astype(np.float64), spans, keep))
    return pr, utts


def measure(utts, n_fft: int, guard: int, power: int):
    """12 点のうち 1 点。返り値は音素インスタンス単位の平坦配列。

    `flatness.class_flatness` と同じ規則をここで展開している（STFT を n_fft ごとに
    1 回で済ませるため）。**両者が一致することを verify_consistency で確認する。**
    """
    hop = n_fft // 4
    bins = F.band_slice(n_fft, SR)
    sfm_v, utt_v, cls_v, tok_v, span_v, rms_v = [], [], [], [], [], []
    n_nan_frames = 0
    n_dropped = 0
    n_floored = 0
    min_used = np.inf
    for ui, (wav, spans, toks) in enumerate(utts):
        mag = F.stft_mag(wav, n_fft, hop)
        sfm = F.frame_sfm(mag, bins, power)
        # 帯域内 RMS。**SFM は尺度不変なので閉鎖区間（無音）も「平坦」に出る。**
        # 生徒との比較でこの罠に落ちないよう、エネルギーも一緒に持っておく
        rms = np.sqrt((mag[bins, :].astype(np.float64) ** 2).mean(axis=0))
        # log の下駄 `_FLOOR` が実際の値に効いていないことを毎回確かめる。
        # ゼロビンは「帯域が丸ごとゼロ = デジタル無音」のフレームにしか無いはずで、
        # そのフレームは frame_sfm が NaN にして落としている
        band = mag[bins, :].astype(np.float64)
        used = band[:, band.mean(axis=0) > 0]
        if used.size:
            n_floored += int((used <= F._FLOOR).sum())
            min_used = min(min_used, float(used.min()))
        n_nan_frames += int(np.isnan(sfm).sum())
        n_stft = sfm.shape[0]
        for (cls, f0, f1), tok in zip(spans, toks):
            a, b = f0 + guard, f1 - guard
            if b <= a:
                n_dropped += 1
                continue
            t0 = -(-(a * HOP) // hop)
            t1 = min(-(-(b * HOP) // hop), n_stft)
            if t1 <= t0:
                n_dropped += 1
                continue
            v = sfm[t0:t1]
            v = v[np.isfinite(v)]
            if not v.size:
                n_dropped += 1
                continue
            sfm_v.append(v.mean())
            rms_v.append(float(rms[t0:t1].mean()))
            span_v.append(f1 - f0)
            utt_v.append(ui)
            cls_v.append(CLASS_NAMES.index(cls))
            tok_v.append(tok)
    return {
        "sfm": np.asarray(sfm_v, dtype=np.float64),
        "utt": np.asarray(utt_v, dtype=np.int32),
        "cls": np.asarray(cls_v, dtype=np.int16),
        "is_z": np.asarray([t == "z" for t in tok_v], dtype=bool),
        "span": np.asarray(span_v, dtype=np.int32),
        "rms": np.asarray(rms_v, dtype=np.float64),
        "n_dropped": n_dropped,
        "n_nan_frames": n_nan_frames,
        "n_stft_hop": hop,
        "n_band_bins": bins.stop - bins.start,
        "n_floored_bins_in_used_frames": n_floored,
        "min_used_band_magnitude": float(min_used),
    }


def masks_for(d, name: str) -> np.ndarray:
    if name in GROUPS:
        return np.isin(d["cls"], [CLASS_NAMES.index(c) for c in GROUPS[name]])
    if name == "fricative_noz":
        return (d["cls"] == CLASS_NAMES.index("fricative")) & ~d["is_z"]
    if name == "z_only":
        return (d["cls"] == CLASS_NAMES.index("fricative")) & d["is_z"]
    if name == "OBSTRUENT_noz":
        return np.isin(d["cls"], [CLASS_NAMES.index(c) for c in F.OBSTRUENT]) & ~d["is_z"]
    return d["cls"] == CLASS_NAMES.index(name)


def auc(a: np.ndarray, b: np.ndarray) -> float:
    """P(A > B) + 0.5 P(A == B)。Mann-Whitney U から。"""
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan")
    r = rankdata(np.concatenate([a, b]))
    return float((r[:na].sum() - na * (na + 1) / 2.0) / (na * nb))


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan")
    sp = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else float("nan")


def cluster_draws(utt: np.ndarray, n_utt: int, boot: int, seed: int):
    """発話単位クラスタ・ブートストラップの gather index を作る generator。"""
    order = np.argsort(utt, kind="stable")
    sorted_utt = utt[order]
    starts = np.searchsorted(sorted_utt, np.arange(n_utt), side="left")
    ends = np.searchsorted(sorted_utt, np.arange(n_utt), side="right")
    per_utt = [order[s:e] for s, e in zip(starts, ends)]
    rng = np.random.default_rng(seed)
    for _ in range(boot):
        pick = rng.integers(0, n_utt, size=n_utt)
        yield np.concatenate([per_utt[p] for p in pick])


def ci(v: list[float]) -> tuple[float, float]:
    a = np.asarray([x for x in v if np.isfinite(x)])
    if a.size < 2:
        return float("nan"), float("nan")
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


def analyse(d, boot: int, seed: int, n_utt: int, naive: bool = False):
    """1 設定ぶんのクラス統計とクラス対統計。CI は発話単位クラスタ bootstrap。"""
    masks = {c: masks_for(d, c) for c in CLASS_NAMES_EXT + list(GROUPS)}
    sfm = d["sfm"]

    boot_class = {c: [] for c in masks}
    boot_pair_diff = {p: [] for p in PAIRS}
    boot_pair_auc = {p: [] for p in PAIRS}

    if naive:  # 参考: 音素を直接リサンプル（独立性を仮定した素朴な CI）
        rng = np.random.default_rng(seed + 1)
        draws = (rng.integers(0, len(sfm), size=len(sfm)) for _ in range(boot))
    else:
        draws = cluster_draws(d["utt"], n_utt, boot, seed)

    for gi in draws:
        s = sfm[gi]
        sub = {c: s[masks[c][gi]] for c in masks}
        for c, v in sub.items():
            boot_class[c].append(v.mean() if v.size else np.nan)
        for p in PAIRS:
            a, b = sub[p[0]], sub[p[1]]
            boot_pair_diff[p].append(a.mean() - b.mean() if a.size and b.size else np.nan)
            boot_pair_auc[p].append(auc(a, b))

    classes = {}
    for c in CLASS_NAMES_EXT + list(GROUPS):
        v = sfm[masks[c]]
        lo, hi = ci(boot_class[c])
        classes[c] = {"n": int(v.size), "mean": float(v.mean()) if v.size else None,
                      "sd": float(v.std(ddof=1)) if v.size > 1 else None,
                      "ci_low": lo, "ci_high": hi}

    pairs = {}
    for p in PAIRS:
        a, b = sfm[masks[p[0]]], sfm[masks[p[1]]]
        lo, hi = ci(boot_pair_diff[p])
        alo, ahi = ci(boot_pair_auc[p])
        pairs[f"{p[0]}_vs_{p[1]}"] = {
            "n_a": int(a.size), "n_b": int(b.size),
            "mean_a": float(a.mean()), "mean_b": float(b.mean()),
            # スキーマ上の (mean, ci_low, ci_high) は**平均差**を指す
            "mean": float(a.mean() - b.mean()), "ci_low": lo, "ci_high": hi,
            "cohens_d": cohens_d(a, b),
            "auc": auc(a, b), "auc_ci_low": alo, "auc_ci_high": ahi,
            "n": int(a.size + b.size),
        }
    return classes, pairs, boot_pair_auc


def matched_guard_comparison(grid, boot: int, seed: int, n_utt: int):
    """**guard の効果を、母集団を固定して測る。**

    guard=1 は span < 3 frame の音素を全部落とすので、生の AUC 比較は
    「窓の効果」と「長い音素だけ残した効果」が混ざる。日本語摩擦音の実測 span は
    mean 2.14 frame なので落ちる量が大きい（実測 78%）。
    そこで **guard=0 を span>=3 の部分集合に制限し、guard=1 と同一インスタンスで**
    比べる。対応のある発話クラスタ bootstrap で ΔAUC の CI を出す。
    """
    out = {}
    for n_fft in N_FFTS:
        for power in POWERS:
            d0, d1 = grid[(n_fft, 0, power)]["d"], grid[(n_fft, 1, power)]["d"]
            keep = d0["span"] >= 3
            assert int(keep.sum()) == len(d1["sfm"]), (
                f"matched 集合が合わない: {int(keep.sum())} != {len(d1['sfm'])}")
            assert np.array_equal(d0["cls"][keep], d1["cls"])
            assert np.array_equal(d0["utt"][keep], d1["utt"])
            assert np.array_equal(d0["span"][keep], d1["span"])
            m0 = {k: (v[keep] if isinstance(v, np.ndarray) and v.shape == keep.shape
                      else v) for k, v in d0.items()}

            a0 = masks_for(m0, SELECT_PAIR[0]), masks_for(m0, SELECT_PAIR[1])
            a1 = masks_for(d1, SELECT_PAIR[0]), masks_for(d1, SELECT_PAIR[1])
            auc0 = auc(m0["sfm"][a0[0]], m0["sfm"][a0[1]])
            auc1 = auc(d1["sfm"][a1[0]], d1["sfm"][a1[1]])

            b0, b1 = [], []
            for gi in cluster_draws(m0["utt"], n_utt, boot, seed):
                b0.append(auc(m0["sfm"][gi][a0[0][gi]], m0["sfm"][gi][a0[1][gi]]))
                b1.append(auc(d1["sfm"][gi][a1[0][gi]], d1["sfm"][gi][a1[1][gi]]))
            lo, hi = ci(list(np.asarray(b0) - np.asarray(b1)))
            out[f"n_fft{n_fft}_power{power}"] = {
                "n_matched_instances": int(keep.sum()),
                "auc_guard0_on_matched": auc0,
                "auc_guard1": auc1,
                "delta_guard0_minus_guard1": auc0 - auc1,
                "paired_ci_low": lo, "paired_ci_high": hi,
                "guard0_better": bool(lo > 0), "guard1_better": bool(hi < 0),
            }
    return out


def coverage(grid) -> dict:
    """設定ごとに、クラス別に何インスタンス残るか。**n が足りないと結論が出せない。**"""
    out = {}
    for k, v in grid.items():
        cl = v["d"]["cls"]
        out[f"n_fft{k[0]}_guard{k[1]}_power{k[2]}"] = {
            c: int((cl == i).sum()) for i, c in enumerate(CLASS_NAMES)}
    return out


def sensitivity(grid) -> dict:
    """**プローブが検出できる最小のズレ**の目安。

    本来の用途は「生徒の摩擦音 SFM が教師より下がったか」を見ること。検出力は
    クラス平均の bootstrap CI 幅で決まる。ここでは尺度に依存しないよう
    `detect_index = (摩擦音側の平均 - 共鳴音側の平均) / CI 半幅` を出す。
    大きいほど、同じ相対ズレを小さい標本で検出できる。
    **選択基準は AUC（指示どおり）で、これは副次的な報告値。**
    """
    out = {}
    for k, v in grid.items():
        c = v["classes"]
        def half(name):
            b = c[name]
            return (b["ci_high"] - b["ci_low"]) / 2.0
        gap_fo = c["fricative"]["mean"] - c["vowel"]["mean"]
        gap_os = c["OBSTRUENT"]["mean"] - c["SONORANT"]["mean"]
        out[f"n_fft{k[0]}_guard{k[1]}_power{k[2]}"] = {
            "fricative_mean_ci_halfwidth": half("fricative"),
            "obstruent_mean_ci_halfwidth": half("OBSTRUENT"),
            "gap_fricative_minus_vowel": gap_fo,
            "gap_obstruent_minus_sonorant": gap_os,
            "detect_index_fricative": gap_fo / half("fricative") if half("fricative") else None,
            "detect_index_obstruent": gap_os / half("OBSTRUENT") if half("OBSTRUENT") else None,
        }
    return out


def z_exclusion(d, boot: int, seed: int, n_utt: int) -> dict:
    """**`z` を fricative から抜いても同じ結論か。**

    OpenJTalk は語頭の破擦音 [dz] と母音間の摩擦音 [z] を区別せず、どちらも `z` になる。
    fricative クラスに [dz] が混ざっていると摩擦音の基準値が破擦音側に引っ張られる。
    過去に「摩擦音の教師基準値 0.381」を fricative+affricate のプール値として
    出してしまった前科がある（C-004）ので、必ず分けて報告する。
    """
    zpairs = [("fricative_noz", "vowel"), ("fricative_noz", "nasal"),
              ("devoiced", "fricative_noz"), ("fricative_noz", "affricate"),
              ("z_only", "fricative_noz"), ("OBSTRUENT_noz", "SONORANT")]
    masks = {n: masks_for(d, n) for n in
             {x for p in zpairs for x in p} | {"fricative", "z_only"}}
    sfm = d["sfm"]
    bdiff = {p: [] for p in zpairs}
    bauc = {p: [] for p in zpairs}
    bmean = {n: [] for n in masks}
    for gi in cluster_draws(d["utt"], n_utt, boot, seed):
        s2 = sfm[gi]
        sub = {n: s2[masks[n][gi]] for n in masks}
        for n, v in sub.items():
            bmean[n].append(v.mean() if v.size else np.nan)
        for p in zpairs:
            a, b = sub[p[0]], sub[p[1]]
            bdiff[p].append(a.mean() - b.mean() if a.size and b.size else np.nan)
            bauc[p].append(auc(a, b))
    out = {"classes": {}, "pairs": {}}
    for n in masks:
        v = sfm[masks[n]]
        lo, hi = ci(bmean[n])
        out["classes"][n] = {"n": int(v.size), "mean": float(v.mean()) if v.size else None,
                             "ci_low": lo, "ci_high": hi}
    for p in zpairs:
        a, b = sfm[masks[p[0]]], sfm[masks[p[1]]]
        lo, hi = ci(bdiff[p])
        alo, ahi = ci(bauc[p])
        out["pairs"][f"{p[0]}_vs_{p[1]}"] = {
            "n_a": int(a.size), "n_b": int(b.size),
            "mean_a": float(a.mean()), "mean_b": float(b.mean()),
            "mean": float(a.mean() - b.mean()), "ci_low": lo, "ci_high": hi,
            "cohens_d": cohens_d(a, b), "auc": auc(a, b),
            "auc_ci_low": alo, "auc_ci_high": ahi, "n": int(a.size + b.size)}
    return out


def energy_diagnostic(d) -> dict:
    """**SFM は尺度不変なので、閉鎖区間（デジタル的にほぼ無音）も「平坦」に出る。**

    実際 geminate `cl` と stop は摩擦音並みの SFM を示すが、これは摩擦雑音ではなく
    2–8 kHz にエネルギーが無いだけの可能性がある。生徒と比べるときにこの罠に
    落ちないよう、クラスごとの帯域内 RMS を一緒に凍結する。
    """
    out = {}
    for i, c in enumerate(CLASS_NAMES):
        m = d["cls"] == i
        r = d["rms"][m]
        out[c] = {"n": int(m.sum()), "band_rms_median": float(np.median(r)),
                  "band_rms_mean": float(r.mean()),
                  "band_rms_p10": float(np.percentile(r, 10)),
                  "band_rms_p90": float(np.percentile(r, 90))}
    med = {c: v["band_rms_median"] for c, v in out.items()}
    ref = med["vowel"]
    for c in out:
        out[c]["band_rms_median_rel_vowel"] = med[c] / ref if ref else None
    return out


def verify_consistency(utts, key, n_check: int = 8) -> dict:
    """`flatness.class_flatness` が本スクリプトの展開版と一致することを確認する。

    実装が 2 つあるので、片方だけ直したときに黙ってズレるのを防ぐ。
    """
    n_fft, guard, power = key
    worst = 0.0
    n_vals = 0
    for wav, spans, _ in utts[:n_check]:
        ref = F.class_flatness(wav, SR, spans, n_fft=n_fft, guard=guard, power=power)
        d = measure([(wav, spans, _)], n_fft, guard, power)
        got: dict[str, list[float]] = {c: [] for c in CLASS_NAMES}
        for c, v in zip(d["cls"], d["sfm"]):
            got[CLASS_NAMES[int(c)]].append(float(v))
        for c in CLASS_NAMES:
            a, b = np.asarray(ref.get(c, [])), np.asarray(got[c])
            assert a.shape == b.shape, f"{c}: {a.shape} != {b.shape}"
            if a.size:
                worst = max(worst, float(np.abs(a - b).max()))
                n_vals += a.size
    return {"n_utt_checked": n_check, "n_values": n_vals, "max_abs_diff": worst}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/pack_sibdense")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260827)
    ap.add_argument("--out", default="reports/b6_flatness_grid.json")
    args = ap.parse_args()

    t0 = time.perf_counter()
    id2tok = build_id2tok()
    pr, utts = load_instances(args.pack, id2tok)
    n_utt = len(utts)
    n_span = sum(len(s) for _, s, _ in utts)
    print(f"{args.pack}: {n_utt} 発話 / クラス付き音素 {n_span:,} / "
          f"{pr.manifest['n_frames']:,} frame  "
          f"(sum(ceil(dT))*{HOP} == len(yT) を全発話で assert 済み)")

    grid = {}
    for n_fft in N_FFTS:
        for guard in GUARDS:
            for power in POWERS:
                key = (n_fft, guard, power)
                t = time.perf_counter()
                d = measure(utts, n_fft, guard, power)
                classes, pairs, bauc = analyse(d, args.boot, args.seed, n_utt)
                grid[key] = {"d": d, "classes": classes, "pairs": pairs, "bauc": bauc}
                sel = pairs[f"{SELECT_PAIR[0]}_vs_{SELECT_PAIR[1]}"]
                print(f"  n_fft={n_fft:>4} hop={d['n_stft_hop']:>3} "
                      f"bins={d['n_band_bins']:>3} guard={guard} power={power}: "
                      f"n={len(d['sfm']):>6} drop={d['n_dropped']:>5} "
                      f"AUC(obst>son)={sel['auc']:.4f} "
                      f"[{sel['auc_ci_low']:.4f},{sel['auc_ci_high']:.4f}] "
                      f"({time.perf_counter()-t:.1f}s)")

    sens = sensitivity(grid)
    sel_name = f"{SELECT_PAIR[0]}_vs_{SELECT_PAIR[1]}"
    literal = max(grid, key=lambda k: grid[k]["pairs"][sel_name]["auc"])
    print(f"\n生の AUC 最大: n_fft={literal[0]} guard={literal[1]} power={literal[2]}  "
          f"AUC={grid[literal]['pairs'][sel_name]['auc']:.4f}"
          f"  ← guard 間は母集団が違うので、これだけでは決められない")

    matched = matched_guard_comparison(grid, args.boot, args.seed, n_utt)
    print("\n母集団を固定した guard 比較"
          "（guard=0 を span>=3 に制限して guard=1 と同一集合にする）:")
    for k, v in matched.items():
        tag = ("guard=0 が有意に上" if v["guard0_better"] else
               "guard=1 が有意に上" if v["guard1_better"] else "区別できない")
        print(f"  {k:<22} n={v['n_matched_instances']:>5}  "
              f"AUC g0={v['auc_guard0_on_matched']:.4f} g1={v['auc_guard1']:.4f}  "
              f"delta={v['delta_guard0_minus_guard1']:+.4f} "
              f"[{v['paired_ci_low']:+.4f},{v['paired_ci_high']:+.4f}]  {tag}")

    # **guard は母集団を固定した比較で決める。**生の AUC で決めると
    # 「長い音素だけ残した効果」を「窓が良くなった効果」と取り違える。
    g0_wins = sum(v["guard0_better"] for v in matched.values())
    g1_wins = sum(v["guard1_better"] for v in matched.values())
    adopted_guard = 0 if g0_wins > g1_wins else 1
    print(f"  -> matched で guard=0 の勝ち {g0_wins}/{len(matched)}, "
          f"guard=1 の勝ち {g1_wins}/{len(matched)} -> guard={adopted_guard} を採る")

    # AUC 最大。**ただし対応のある bootstrap で区別できない設定は同点とみなす。**
    # 0.00005 の差を「勝ち」と呼ぶのは C-004 と同じ間違いになる。
    # 同点のなかからは検出感度（クラス平均 CI 半幅に対する gap）が最大のものを採る
    # ＝プローブの本来の仕事「生徒の SFM 低下を拾う」に直結する量。
    cand = [k for k in grid if k[1] == adopted_guard]
    top = max(cand, key=lambda k: grid[k]["pairs"][sel_name]["auc"])
    tied, tie_detail = [top], {}
    for k in cand:
        if k == top:
            continue
        lo, hi = ci(list(np.asarray(grid[top]["bauc"][SELECT_PAIR])
                         - np.asarray(grid[k]["bauc"][SELECT_PAIR])))
        indist = lo <= 0.0
        tie_detail[f"n_fft{k[0]}_guard{k[1]}_power{k[2]}"] = {
            "delta_auc_vs_top": (grid[top]["pairs"][sel_name]["auc"]
                                 - grid[k]["pairs"][sel_name]["auc"]),
            "paired_ci_low": lo, "paired_ci_high": hi,
            "indistinguishable_from_top": bool(indist)}
        if indist:
            tied.append(k)
    best = max(tied, key=lambda k: (
        sens[f"n_fft{k[0]}_guard{k[1]}_power{k[2]}"]["detect_index_obstruent"], -k[0], -k[2]))
    print(f"  AUC 最大は n_fft={top[0]} power={top[2]}。対応のある CI で区別できない "
          f"同点集合 = {[f'n_fft{k[0]}_power{k[2]}' for k in tied]}")
    print(f"\n採用: n_fft={best[0]} hop={best[0]//4} guard={best[1]} power={best[2]}  "
          f"AUC={grid[best]['pairs'][sel_name]['auc']:.6f}  "
          f"（同点のなかで検出感度最大）")

    # 勝者が他の設定と区別できるか。**同じリサンプルを共有した対応のある差**で見る。
    ba = np.asarray(grid[best]["bauc"][SELECT_PAIR])
    versus = {}
    for k in grid:
        if k == best:
            continue
        dd = ba - np.asarray(grid[k]["bauc"][SELECT_PAIR])
        lo, hi = ci(list(dd))
        versus[f"n_fft{k[0]}_guard{k[1]}_power{k[2]}"] = {
            "delta_auc": float(grid[best]["pairs"][sel_name]["auc"]
                               - grid[k]["pairs"][sel_name]["auc"]),
            "paired_ci_low": lo, "paired_ci_high": hi,
            "distinguishable": bool(lo > 0),
        }
    n_dist = sum(v["distinguishable"] for v in versus.values())
    print(f"  勝者と有意に区別できる設定: {n_dist}/{len(versus)}"
          f"（対応のある発話クラスタ bootstrap 95%CI）")

    cov = coverage(grid)
    print("\nクラス別の残存インスタンス数（guard=1 は span<3 を全部落とす）:")
    hdr = "  " + "setting".ljust(26) + "".join(c[:6].rjust(8) for c in CLASS_NAMES)
    print(hdr)
    for k, v in cov.items():
        print("  " + k.ljust(26) + "".join(str(v[c]).rjust(8) for c in CLASS_NAMES))

    # 参考: 素朴な音素単位 bootstrap は CI がどれだけ狭く出るか
    _, naive_pairs, _ = analyse(grid[best]["d"], args.boot, args.seed, n_utt, naive=True)

    print("\n検出感度（副次指標。gap / クラス平均 CI 半幅。大きいほど小さいズレを拾える）:")
    for k, v in sens.items():
        print(f"  {k:<26} fricative {v['detect_index_fricative']:>7.1f}  "
              f"obstruent {v['detect_index_obstruent']:>7.1f}  "
              f"(CI 半幅 {v['fricative_mean_ci_halfwidth']:.4f} / "
              f"{v['obstruent_mean_ci_halfwidth']:.4f})")

    energy = energy_diagnostic(grid[best]["d"])
    print("\n帯域内 RMS（SFM は尺度不変。閉鎖区間は「無音ゆえに平坦」になりうる）:")
    for c in CLASS_NAMES:
        e = energy[c]
        print(f"  {c:<14} median={e['band_rms_median']:.2e}  "
              f"母音比={e['band_rms_median_rel_vowel']:.3f}")

    zx = z_exclusion(grid[best]["d"], args.boot, args.seed, n_utt)
    print("\nz 除外の確認（採用設定）:")
    for k, v in zx["classes"].items():
        print(f"  {k:<16} n={v['n']:>5}  mean={v['mean']:.4f} "
              f"[{v['ci_low']:.4f}, {v['ci_high']:.4f}]")
    for k, v in zx["pairs"].items():
        print(f"  {k:<34} d={v['cohens_d']:+.3f}  AUC={v['auc']:.4f} "
              f"[{v['auc_ci_low']:.4f}, {v['auc_ci_high']:.4f}]")

    consistency = verify_consistency(utts, best)
    print(f"  flatness.class_flatness との一致: "
          f"max|diff|={consistency['max_abs_diff']:.3e} "
          f"({consistency['n_values']} 値)")

    baseline = grid[best]["classes"]
    print(f"\n教師ベースライン（採用設定 / {args.pack}）:")
    for c in CLASS_NAMES_EXT:
        b = baseline[c]
        print(f"  {c:<14} n={b['n']:>5}  mean={b['mean']:.4f}  "
              f"95%CI [{b['ci_low']:.4f}, {b['ci_high']:.4f}]")

    rep = {
        "task": "B-6 音素クラス別 2-8kHz スペクトル平坦度プローブの窓設計",
        "repro": "uv run python scripts/b6_flatness_grid.py",
        "pack": args.pack,
        "pack_manifest": {k: pr.manifest[k] for k in
                          ("n_utterances", "n_frames", "teacher", "corpus")},
        "n_utterances": n_utt,
        "n_class_phonemes": n_span,
        "bootstrap": {"n_resamples": args.boot, "seed": args.seed,
                      "unit": "utterance (cluster bootstrap)",
                      "interval": "percentile 2.5 / 97.5",
                      "why": "同一発話内の音素は独立でない。音素を直接リサンプルすると "
                             "CI が不当に狭く出る"},
        "window_rules": {
            "hop": "n_fft // 4",
            "window": "periodic Hann",
            "centering": "center=True / reflect padding / フレーム t の中心 = t*hop",
            "band_hz": list(F.BAND_HZ),
            "band_bins_inclusive": True,
            "span": "教師の w_ceil=ceil(w) から [cumsum(ceil(dT))[i-1], cumsum(ceil(dT))[i])"
                    " (hop 256 の教師フレーム単位)。サンプル区間は x256",
            "guard_unit": "教師フレーム (hop 256)。両端から guard フレーム削る",
            "frame_selection": "サンプル区間に中心が入る STFT フレームのみ",
            "sfm": "exp(mean(log X)) / mean(X), X = |S|**power, 2-8 kHz のビン",
            "instance_value": "そのインスタンスの STFT フレームにわたる SFM の平均",
            "stat_unit": "音素インスタンス（フレームではない）",
            "log_floor": F._FLOOR,
            "log_floor_check": "帯域が丸ごとゼロのフレーム（デジタル無音、実測 4.4-5.6%）は "
                               "NaN にして落とす。使用フレーム内で floor に触れるビンは "
                               "全設定で 0 個（grid.*.n_floored_bins_in_used_frames）",
            "nan_frame_note": "n_nan_frames は帯域が全ゼロのフレーム数。先頭末尾の無音が主で、"
                              "クラス付き音素インスタンスは 1 件も全滅していない（n_instances_dropped=0）",
        },
        "alignment_check": {
            "assertion": "sum(ceil(dT))*256 == len(yT)",
            "n_utterances_passed": n_utt, "n_failed": 0,
        },
        "classes": {k: list(v) for k, v in F.CLASSES.items()},
        "z_note": "z は語頭で破擦音 [dz] / 母音間で摩擦音 [z] になるが OpenJTalk は "
                  "区別しない。fricative に入れてある。fricative_noz が z 除外版",
        "grid": {
            f"n_fft{k[0]}_guard{k[1]}_power{k[2]}": {
                "n_fft": k[0], "hop": v["d"]["n_stft_hop"], "guard": k[1],
                "power": k[2], "n_band_bins": v["d"]["n_band_bins"],
                "n_instances": int(len(v["d"]["sfm"])),
                "n_instances_dropped": v["d"]["n_dropped"],
                "n_nan_frames": v["d"]["n_nan_frames"],
                "n_floored_bins_in_used_frames": v["d"]["n_floored_bins_in_used_frames"],
                "min_used_band_magnitude": v["d"]["min_used_band_magnitude"],
                "classes": v["classes"], "pairs": v["pairs"],
            } for k, v in grid.items()
        },
        "sensitivity": {
            "why": "生徒の SFM 低下を検出する力。gap / クラス平均 bootstrap CI 半幅。"
                   "選択基準は AUC（指示どおり）でこれは副次的な報告値",
            "index": sens,
        },
        "selection": {
            "criterion": f"{sel_name} の AUC 最大。"
                         "ただし guard は母集団が変わるので matched 比較で先に決める",
            "literal_auc_max": {
                "n_fft": literal[0], "guard": literal[1], "power": literal[2],
                "auc": grid[literal]["pairs"][sel_name]["auc"],
                "caveat": "guard=1 は span<3 frame を全部落とすので、この AUC は "
                          "長い音素だけを見た値。matched 比較では guard=0 が 6/6 で勝つ",
            },
            "guard_decided_by": {"matched_guard0_wins": g0_wins,
                                 "matched_guard1_wins": g1_wins,
                                 "n_compared": len(matched),
                                 "adopted_guard": adopted_guard},
            "tie_break": {
                "rule": "対応のある発話クラスタ bootstrap で AUC 最大と区別できない設定は"
                        "同点。同点のなかで detect_index_obstruent（gap / クラス平均 CI "
                        "半幅）が最大のものを採る。さらに同点なら n_fft・power の小さい方",
                "auc_max_setting": {"n_fft": top[0], "guard": top[1], "power": top[2],
                                    "auc": grid[top]["pairs"][sel_name]["auc"]},
                "tied_with_top": [f"n_fft{k[0]}_guard{k[1]}_power{k[2]}" for k in tied],
                "detail": tie_detail,
            },
            "vs_others_paired_caveat": "guard の違う設定との比較は母集団が違う。"
                                       "同一 guard 内の比較だけが対応のある比較になる",
            "adopted": {"n_fft": best[0], "hop": best[0] // 4, "guard": best[1],
                        "power": best[2]},
            "auc": grid[best]["pairs"][sel_name]["auc"],
            "auc_ci": [grid[best]["pairs"][sel_name]["auc_ci_low"],
                       grid[best]["pairs"][sel_name]["auc_ci_high"]],
            "vs_others_paired": versus,
            "n_distinguishable": n_dist, "n_compared": len(versus),
        },
        "teacher_baseline": {
            "setting": {"n_fft": best[0], "hop": best[0] // 4, "guard": best[1],
                        "power": best[2], "band_hz": list(F.BAND_HZ), "sr": SR},
            "source": args.pack,
            "classes": baseline,
            "pairs": grid[best]["pairs"],
        },
        "matched_guard_comparison": {
            "why": "guard=1 は span<3 frame を全部落とす（実測 78%）。生の AUC 比較は "
                   "窓の効果と母集団選択の効果が混ざる。guard=0 を同じ span>=3 の "
                   "部分集合に制限して比べる",
            "results": matched,
        },
        "class_coverage": {
            "why": "guard=1 は devoiced を 471 -> 35 まで削る。n=35 では日本語固有の "
                   "無声化母音の問いに答えられない（C-004 の再発）",
            "counts": cov,
        },
        "naive_instance_bootstrap_at_adopted": {
            "why": "参考。音素単位でリサンプルすると CI がどれだけ狭く出るか",
            "pairs": naive_pairs,
        },
        "band_energy_at_adopted": {
            "why": "SFM は尺度不変。閉鎖区間（geminate cl / stop）は摩擦雑音ではなく "
                   "2-8 kHz にエネルギーが無いために平坦に見える可能性がある。"
                   "生徒との比較では SFM と RMS を必ず一緒に見ること",
            "classes": energy,
        },
        "z_exclusion": {
            "why": "OpenJTalk は語頭 [dz] と母音間 [z] を区別しない。fricative から "
                   "z を抜いても結論が変わらないかを確認する",
            "setting": {"n_fft": best[0], "guard": best[1], "power": best[2]},
            **zx,
        },
        "implementation_consistency": consistency,
        "environment": {
            "python": platform.python_version(), "numpy": np.__version__,
            "platform": platform.platform(),
            "git_commit": subprocess.run(["git", "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip(),
        },
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }
    with open(args.out, "w") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=float)
    print(f"\n→ {args.out}  ({rep['elapsed_sec']:.0f}s)")

    # **凍結した定数がレポートとズレていないか毎回確かめる。**
    # 片方だけ直すと、生徒の評価が別の基準と比べられてしまう。
    rc = 0
    if (best[0], best[1], best[2]) != (F.N_FFT, F.GUARD, F.POWER):
        print(f"⚠️ src/saanotts_jp/flatness.py の定数 "
              f"(N_FFT={F.N_FFT}, GUARD={F.GUARD}, POWER={F.POWER}) が "
              f"採用設定 {best} と食い違う。更新すること")
        rc = 1
    for c, (m, lo, hi, n) in F.TEACHER_BASELINE.items():
        b = baseline[c]
        if b["n"] != n or abs(b["mean"] - m) > 5e-5:
            print(f"⚠️ TEACHER_BASELINE[{c}] が食い違う: "
                  f"定数 ({m}, n={n}) vs 実測 ({b['mean']:.4f}, n={b['n']})")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
