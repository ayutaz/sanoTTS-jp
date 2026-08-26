#!/usr/bin/env python3
"""B-7: 日本語の length scale `s_v` を較正する。

論文は `s_v` を言語ごとに較正している（英語 1.08 / ベトナム語・インドネシア語 1.16）。
生徒の推論は `d̂_i = clip_[1,80](round(s_v · r_i))`、教師は `w_ceil = ceil(w)`。
**丸めの規約が違う**（ceil vs round）ので、生徒が教師の duration を完璧に当てても
総フレーム長は系統的にずれる。`s_v` はまずこのズレを吸収する係数である。

⚠️ **ここで測れるのは「量子化に由来するズレ」だけ。**
`r_i` は本来「生徒の予測」だが生徒がまだ存在しないので `r_i = dT_i`（完璧な生徒）を
代入する。**学習後に生徒の予測で解き直す必要がある。** 混同しないこと。

**反証済みの主張**: 「`s_v` の初期値は 1.2 前後」— 根拠にされた比 42.37 は
B-1 の誤ルーティングで数字が全部落ちた壊れた文の出力だった。しかもその比は
「SDP の確率的サンプルが決定的推論より何 % 長いか」で、`s_v` とは別物。

実行:
    uv run python scripts/b7_sv_calibration.py --out reports/b7_sv.json
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

sys.path.insert(0, "src")
from saanotts_jp.durations import HOP, SR, load  # noqa: E402

CLIP_LO, CLIP_HI = 1, 80


def student_frames(dT: np.ndarray, sv: float) -> np.ndarray:
    """`d̂_i = clip_[1,80](round(s_v · r_i))`。numpy の round は偶数丸めなので
    torch.round と同じ挙動（half to even）。生徒の実装と揃えること。"""
    return np.clip(np.round(sv * dT), CLIP_LO, CLIP_HI)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--durations", default="reports/durations")
    ap.add_argument("--out", default="reports/b7_sv.json")
    args = ap.parse_args()

    d = load(args.durations)
    n = len(d)
    teacher_frames = np.array([r["frames"] for r in d.index], dtype=np.float64)
    total_teacher = teacher_frames.sum()

    # --- s_v をコーパス全体の総フレーム比が 1 になるように解く（単調なので二分探索） ---
    def total_student(sv: float) -> float:
        return float(sum(student_frames(d.utt(i)[1], sv).sum() for i in range(n)))

    lo, hi = 0.5, 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if total_student(mid) < total_teacher:
            lo = mid
        else:
            hi = mid
    sv_star = (lo + hi) / 2

    # --- 発話ごとの比のばらつき（点推定に使えるかを見る） ---
    def ratios(sv: float) -> np.ndarray:
        return np.array([student_frames(d.utt(i)[1], sv).sum() / teacher_frames[i]
                         for i in range(n)])

    r1 = ratios(1.0)
    rs = ratios(sv_star)

    # --- clip の下限・上限がどれだけ効いているか ---
    all_d = d.dT
    pad_id = 0
    below1 = all_d < 1.0
    above80 = all_d > 80.0
    is_pad = d.ids == pad_id
    utt_with_below1 = sum(1 for i in range(n) if (d.utt(i)[1] < 1.0).any())

    # ceil vs round の寄与を分離する（s_v=1 のとき）
    ceil_sum = float(np.ceil(all_d).sum())
    round_sum = float(np.clip(np.round(all_d), CLIP_LO, CLIP_HI).sum())

    out = {
        "n_utterances": n,
        "n_tokens": int(all_d.size),
        "s_v": {
            "solved": sv_star,
            "criterion": "コーパス全体で sum(clip_[1,80](round(s_v*dT))) == sum(ceil(dT))",
            "total_teacher_frames": total_teacher,
            "total_student_frames_at_solved": total_student(sv_star),
            "total_student_frames_at_1.0": total_student(1.0),
        },
        "ratio_at_sv_1.0": {"mean": float(r1.mean()), "sd": float(r1.std(ddof=1)),
                            "min": float(r1.min()), "max": float(r1.max()),
                            "p5": float(np.percentile(r1, 5)),
                            "p95": float(np.percentile(r1, 95))},
        "ratio_at_solved_sv": {"mean": float(rs.mean()), "sd": float(rs.std(ddof=1)),
                               "min": float(rs.min()), "max": float(rs.max()),
                               "p5": float(np.percentile(rs, 5)),
                               "p95": float(np.percentile(rs, 95))},
        "rounding_convention": {
            "teacher_ceil_total": ceil_sum,
            "student_round_clip_total_at_sv1": round_sum,
            "ratio_ceil_over_round": ceil_sum / round_sum,
            "note": "教師は ceil、生徒は round+clip。s_v はまずこの差を吸収する",
        },
        "clip_bounds": {
            "tokens_below_1": int(below1.sum()),
            "tokens_below_1_share": float(below1.mean()),
            "tokens_below_1_that_are_PAD": int((below1 & is_pad).sum()),
            "tokens_below_1_that_are_real": int((below1 & ~is_pad).sum()),
            "utterances_with_a_token_below_1": utt_with_below1,
            "utterances_with_a_token_below_1_share": utt_with_below1 / n,
            "tokens_above_80": int(above80.sum()),
            "max_dT": float(all_d.max()),
            "max_ceil_dT": int(np.ceil(all_d).max()),
            "verdict_upper_bound": ("上限 80 は飽和しない" if all_d.max() <= 80
                                    else "⚠️ 上限 80 に当たるトークンがある"),
        },
        "seconds_per_frame": HOP / SR,
        "repro": "uv run python scripts/b7_sv_calibration.py --out reports/b7_sv.json",
        "caveat": ("r_i に生徒の予測ではなく dT を代入している（完璧な生徒の仮定）。"
                   "測れているのは量子化由来のズレだけで、学習後に解き直しが要る。"),
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=2)

    print(f"s_v = {sv_star:.4f}  （総フレーム {total_teacher:,.0f} に一致させる値）")
    print(f"  s_v=1.0 での発話ごとの比: mean {r1.mean():.4f} sd {r1.std(ddof=1):.4f} "
          f"[{r1.min():.3f}, {r1.max():.3f}]")
    print(f"  s_v={sv_star:.4f} での比 : mean {rs.mean():.4f} sd {rs.std(ddof=1):.4f} "
          f"[{rs.min():.3f}, {rs.max():.3f}]")
    c = out["clip_bounds"]
    print(f"\ndT < 1 のトークン: {c['tokens_below_1']:,} "
          f"({c['tokens_below_1_share']*100:.2f}%) "
          f"— PAD {c['tokens_below_1_that_are_PAD']:,} / 実音素 {c['tokens_below_1_that_are_real']:,}")
    print(f"  そのトークンを含む発話: {c['utterances_with_a_token_below_1']:,}/{n:,} "
          f"({c['utterances_with_a_token_below_1_share']*100:.1f}%)")
    print(f"dT > 80 のトークン: {c['tokens_above_80']} / max dT {c['max_dT']:.2f} "
          f"→ {c['verdict_upper_bound']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
