#!/usr/bin/env python3
"""B-8 / D2: `_` PAD がフレーム予算をどれだけ食っているかを測り、扱いを決める。

`PiperEncoder._post_process` が音素間に必ず PAD を挿入するので、**トークンの約半分が
id 0 (`_`)** になる。8 パックの手測りでは 389 トークン中 191 個 (49%) だった。
論文の `Dα` は `clip_[1,80](round(s_v·r_i))` を全トークンに適用するので、
実効トークン予算の半分が PAD に消える可能性がある。

**トークン比とフレーム比は別物。** PAD が短ければトークンの 49% でもフレームは数 % かもしれない。
そこを測らずに設計を変えない。

実行:
    uv run python scripts/b8_pad_duration.py --out reports/b8_pad.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

import numpy as np

sys.path.insert(0, "src")
from saanotts_jp.durations import HOP, SR, load  # noqa: E402

PAD_ID = 0


def stats(a: np.ndarray) -> dict:
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "mean": float(a.mean()), "sd": float(a.std(ddof=1)),
            "min": float(a.min()), "p25": float(np.percentile(a, 25)),
            "p50": float(np.percentile(a, 50)), "p75": float(np.percentile(a, 75)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--durations", default="reports/durations")
    ap.add_argument("--out", default="reports/b8_pad.json")
    args = ap.parse_args()

    d = load(args.durations)
    n = len(d)
    id2tok = d.id2tok
    is_pad = d.ids == PAD_ID
    ceil_d = np.ceil(d.dT)

    # 連続 PAD（読点などで起きる）
    consec = 0
    per_utt_pad_tok, per_utt_pad_frame = [], []
    for i in range(n):
        ids, dt = d.utt(i)
        p = ids == PAD_ID
        consec += int(((p[:-1]) & (p[1:])).sum())
        c = np.ceil(dt)
        per_utt_pad_tok.append(p.mean())
        per_utt_pad_frame.append(c[p].sum() / c.sum())

    per_utt_pad_tok = np.array(per_utt_pad_tok)
    per_utt_pad_frame = np.array(per_utt_pad_frame)

    # 音素別の duration（PAD がどれだけ短いかを他と比べる）
    by_tok: dict[str, list[float]] = collections.defaultdict(list)
    for tid, val in zip(d.ids, d.dT, strict=True):
        by_tok[id2tok.get(int(tid), f"?{tid}")].append(float(val))
    per_phoneme = {k: stats(np.array(v)) for k, v in
                   sorted(by_tok.items(), key=lambda kv: -len(kv[1]))}

    out = {
        "n_utterances": n, "n_tokens": int(d.ids.size),
        "pad_token_share": float(is_pad.mean()),
        "pad_frame_share": float(ceil_d[is_pad].sum() / ceil_d.sum()),
        "pad_raw_duration_share": float(d.dT[is_pad].sum() / d.dT.sum()),
        "per_utterance": {
            "pad_token_share": stats(per_utt_pad_tok),
            "pad_frame_share": stats(per_utt_pad_frame),
        },
        "duration_pad": stats(d.dT[is_pad]),
        "duration_real": stats(d.dT[~is_pad]),
        "ceil_frames_pad": stats(ceil_d[is_pad]),
        "ceil_frames_real": stats(ceil_d[~is_pad]),
        "pad_dT_below_1_share": float((d.dT[is_pad] < 1.0).mean()),
        "real_dT_below_1_share": float((d.dT[~is_pad] < 1.0).mean()),
        "consecutive_pad_pairs": consec,
        "consecutive_pad_pairs_per_utt": consec / n,
        "per_phoneme_duration": per_phoneme,
        "budget": {
            "note": ("生徒が全トークンに duration を出す前提での実効予算。"
                     "PAD を別扱いにすると何が浮くかの材料"),
            "frames_total": float(ceil_d.sum()),
            "frames_on_pad": float(ceil_d[is_pad].sum()),
            "frames_on_real": float(ceil_d[~is_pad].sum()),
            "seconds_total": float(ceil_d.sum() * HOP / SR),
        },
        "repro": "uv run python scripts/b8_pad_duration.py --out reports/b8_pad.json",
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=2)

    print(f"{n:,} 発話 / {d.ids.size:,} トークン")
    print(f"PAD のトークン比 : {out['pad_token_share']*100:.2f}%")
    print(f"PAD のフレーム比 : {out['pad_frame_share']*100:.2f}%  ← 設計判断はこちらで")
    print(f"PAD の duration  : mean {out['duration_pad']['mean']:.3f} "
          f"p50 {out['duration_pad']['p50']:.3f} max {out['duration_pad']['max']:.2f}")
    print(f"実音素の duration: mean {out['duration_real']['mean']:.3f} "
          f"p50 {out['duration_real']['p50']:.3f} max {out['duration_real']['max']:.2f}")
    print(f"dT<1 の割合      : PAD {out['pad_dT_below_1_share']*100:.1f}% / "
          f"実音素 {out['real_dT_below_1_share']*100:.1f}%")
    print(f"連続 PAD ペア    : {consec:,} ({consec/n:.2f}/発話)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
