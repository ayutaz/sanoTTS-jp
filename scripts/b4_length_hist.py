#!/usr/bin/env python3
"""B-4: 長さフィルタの基準を教師側の制約から決める。

**採用しない**: 「mora 5〜100 で切る」— 教師側の根拠が無い恣意的な値。
**採用する**: 教師の学習時制約 `max_phoneme_ids=400` / `max_spec_length=700`
（piper-plus の `dataset.py` が超過発話を捨てる）。

⚠️ 過去の調査が「rohan 先頭 300 行で mean 306 / 7.0% が 400 超」と言い、
検証者が「pool 全体で p50=116 / 400 超 24 行」と言って食い違った。
**符号化前トークン長と符号化後 ID 長 (2N+3) の取り違えの疑い**があったので、
ここで 3 系列を別々に出して決着させる。

実行:
    uv run python scripts/b4_length_hist.py --out reports/b4_length_hist.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
from saanotts_jp.durations import HOP, SR, load  # noqa: E402

MAX_PHONEME_IDS = 400      # piper-plus dataset.py の学習時制約
MAX_SPEC_LENGTH = 700      # 同上。700 * 256 / 22050 = 8.13 秒


def pct(a: np.ndarray, qs=(1, 5, 25, 50, 75, 90, 95, 99, 100)) -> dict:
    return {f"p{q}": float(np.percentile(a, q)) for q in qs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--durations", default="reports/durations")
    ap.add_argument("--out", default="reports/b4_length_hist.json")
    args = ap.parse_args()

    d = load(args.durations)
    n = len(d)
    tok = np.array([r["n_tokens"] for r in d.index], dtype=np.int32)
    ids = np.array([r["n_ids"] for r in d.index], dtype=np.int32)
    frames = np.array([r["frames"] for r in d.index], dtype=np.int32)
    split = np.array([r["split"] for r in d.index])
    src = [r["uid"].rsplit("_", 1)[0] if "_" in r["uid"] else r["uid"]
           for r in d.index]

    # **符号化の関係式は音素数に対して成り立つ**（中間表現トークン数ではない）。
    # 1 モーラが 1〜2 音素になる（`きょ` → `ky` `o`）ので `2*n_tokens+3` は成立しない。
    # 過去の「306 vs 116」の食い違いはこの取り違えが原因（B-4）。
    pad = 0
    n_ph = np.array([int((d.utt(i)[0] != pad).sum()) - 2 for i in range(n)],
                    dtype=np.int32)   # `^` と `$` を引く
    rel = ids - (2 * n_ph + 3)
    rel_exact = int((rel == 0).sum())

    # ずれの正体: **音素そのものが PAD になる場合**（`#` 句境界など）。
    # canonical 規則「PAD の後ろに PAD を挟まない」により、そこだけ 1 個で済む。
    # したがって `len(ids) == 2*n_phonemes + 3 + (PAD 音素の数)` が厳密な関係で、
    # PAD 音素の数は ids 中の `_ _` 隣接ペア数に等しい。**これを assert する。**
    pad_pairs = np.array([int(((u[:-1] == pad) & (u[1:] == pad)).sum())
                          for u in (d.utt(i)[0] for i in range(n))], dtype=np.int32)
    exact_full = int((rel == pad_pairs).sum())
    assert exact_full == n, (
        f"符号化の関係式が {n - exact_full} 発話で成り立たない。"
        "encode_intermediate の PAD 規則が canonical とずれている疑い")

    # モーラ数（記号を除いた中間表現トークン）と発話速度。G12 の範囲の妥当性を見る
    import kana_g2p as K
    table = K.build_mora_table()
    MARKS = {"[", "]", "#", "°"}
    n_mora = np.array([sum(1 for t in K.text_to_intermediate(r["text"], table)
                           if t not in MARKS) for r in d.index], dtype=np.int32)
    mora_rate = n_mora / np.maximum(frames * HOP / SR, 1e-6)

    # ゲート G12/G13 は **ids からの近似モーラ数**（アクセント記号などを除いた
    # 音素数 / 2）で測っている。**厳密なモーラ数とはスケールが違う**ので、
    # ゲートの妥当性はゲート自身の単位で確認する。
    GATE_SKIP = (0, 1, 2, 3, 7, 8, 9)
    mora_gate = np.array([float(np.sum(~np.isin(d.utt(i)[0], GATE_SKIP))) / 2.0
                          for i in range(n)], dtype=np.float64)
    gate_rate = mora_gate / np.maximum(frames * HOP / SR, 1e-6)

    sec = frames * HOP / SR
    # モーラ数の近似: 中間表現のトークンのうち記号でないもの。ここでは n_tokens を使い、
    # 記号ぶんの過大評価が入ることを注記する（発話速度の絶対値ではなく分布を見る）
    rate = tok / np.maximum(sec, 1e-6)

    over_ids = ids > MAX_PHONEME_IDS
    over_frames = frames > MAX_SPEC_LENGTH

    def by_split(mask):
        return {s: int((mask & (split == s)).sum()) for s in sorted(set(split))}

    worst = np.argsort(-frames)[:20]
    out = {
        "n_utterances": n,
        "n_rejected_at_g2p": d.meta["n_rejected"],
        "series": {
            "token_len":      {"mean": float(tok.mean()), **pct(tok)},
            "encoded_id_len": {"mean": float(ids.mean()), **pct(ids)},
            "est_frames":     {"mean": float(frames.mean()), **pct(frames)},
            "seconds":        {"mean": float(sec.mean()), **pct(sec)},
            "tokens_per_sec": {"mean": float(rate.mean()), **pct(rate)},
            "n_phonemes":     {"mean": float(n_ph.mean()), **pct(n_ph)},
            "n_mora":         {"mean": float(n_mora.mean()), **pct(n_mora)},
            "mora_per_sec":   {"mean": float(mora_rate.mean()), **pct(mora_rate)},
        },
        "gate_units": {
            "exact_mora_per_sec": {"mean": float(mora_rate.mean()), **pct(mora_rate)},
            "gate_proxy_mora_per_sec": {"mean": float(gate_rate.mean()), **pct(gate_rate)},
            "scale_ratio_exact_over_proxy": float(mora_rate.mean() / gate_rate.mean()),
            "note": ("ゲートは近似（アクセント記号を除いた音素数/2）で測る。"
                     "厳密なモーラ数より約 17% 小さく出る。混ぜて読まないこと"),
        },
        "gate_g12": {
            "range": [4.0, 12.0], "measured_in": "gate proxy units",
            "below_4": int((gate_rate < 4.0).sum()),
            "above_12": int((gate_rate > 12.0).sum()),
            "outside_share": float(((gate_rate < 4.0) | (gate_rate > 12.0)).mean()),
            "g13_mean_range": [6.5, 8.5],
            "g13_mean_measured": float(gate_rate.mean()),
            "g13_would_fire": not (6.5 <= float(gate_rate.mean()) <= 8.5),
            "note": ("G12 は「自前で音素化すると 2.4 倍速になる」ことを捕まえる網。"
                     "正常な行を落としていないかをここで確認する"),
        },
        "intersperse_relation": {
            "formula": "len(ids) == 2*n_phonemes + 3 + (PAD 音素の数)",
            "naive_formula": "len(ids) == 2*n_tokens + 3  （**誤り**。1 モーラが 1〜2 音素）",
            "exact_with_pad_term": exact_full, "share_exact": exact_full / n,
            "exact_without_pad_term": rel_exact,
            "deviation_counts": dict(collections.Counter(map(int, rel)).most_common(8)),
            "note": ("ずれは `#` 句境界などで音素そのものが PAD になる件数。"
                     "canonical は「PAD の後ろに PAD を挟まない」ので 1 個で済む"),
        },
        "teacher_limits": {
            "max_phoneme_ids": MAX_PHONEME_IDS,
            "max_spec_length": MAX_SPEC_LENGTH,
            "max_spec_length_sec": MAX_SPEC_LENGTH * HOP / SR,
            "over_ids": int(over_ids.sum()), "over_ids_share": float(over_ids.mean()),
            "over_ids_by_split": by_split(over_ids),
            "over_frames": int(over_frames.sum()),
            "over_frames_share": float(over_frames.mean()),
            "over_frames_by_split": by_split(over_frames),
            "over_either": int((over_ids | over_frames).sum()),
        },
        "longest_20": [
            {"uid": d.index[i]["uid"], "split": d.index[i]["split"],
             "n_tokens": int(tok[i]), "n_ids": int(ids[i]),
             "frames": int(frames[i]), "sec": round(float(sec[i]), 2),
             "text": d.index[i]["text"][:60]} for i in worst
        ],
        "histograms": {
            "encoded_id_len": np.histogram(ids, bins=np.arange(0, 620, 20))[0].tolist(),
            "encoded_id_len_bins": np.arange(0, 620, 20).tolist(),
            "est_frames": np.histogram(frames, bins=np.arange(0, 1300, 50))[0].tolist(),
            "est_frames_bins": np.arange(0, 1300, 50).tolist(),
        },
        "by_source": {
            s: {"n": int(sum(1 for x in src if x == s))}
            for s in sorted(set(src))
        } if len(set(src)) < 60 else "省略（source 種類が多い）",
        "repro": "uv run python scripts/b4_length_hist.py --out reports/b4_length_hist.json",
        "note": ("tokens_per_sec は中間表現トークン数 / 秒。記号（[ ] # °）を含むので "
                 "mora/s より大きく出る。G12 の mora/s とは別物。"),
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=2)

    print(f"{n:,} 発話 / g2p 棄却 {d.meta['n_rejected']}")
    for k, v in out["series"].items():
        print(f"  {k:<16} mean {v['mean']:8.2f}  p50 {v['p50']:8.1f} "
              f" p90 {v['p90']:8.1f}  p99 {v['p99']:8.1f}  max {v['p100']:8.1f}")
    t = out["teacher_limits"]
    print(f"\nids > {MAX_PHONEME_IDS}: {t['over_ids']} 件 ({t['over_ids_share']*100:.2f}%)"
          f"  {t['over_ids_by_split']}")
    print(f"frames > {MAX_SPEC_LENGTH}: {t['over_frames']} 件 "
          f"({t['over_frames_share']*100:.2f}%)  {t['over_frames_by_split']}")
    print(f"どちらか超過: {t['over_either']} 件")
    r = out["intersperse_relation"]
    print(f"\nlen(ids) == 2*n_phonemes+3+PAD音素: {r['exact_with_pad_term']}/{n} "
          f"({r['share_exact']*100:.2f}%)  ← 全件成立を assert 済み")
    u = out["gate_units"]
    print(f"\nモーラ速度  厳密 {u['exact_mora_per_sec']['mean']:.2f} / "
          f"ゲート近似 {u['gate_proxy_mora_per_sec']['mean']:.2f} mora/s "
          f"（比 {u['scale_ratio_exact_over_proxy']:.3f}）")
    g = out["gate_g12"]
    print(f"G12 (4–12): 下回り {g['below_4']} / 上回り {g['above_12']} "
          f"= {g['outside_share']*100:.3f}%")
    print(f"G13 平均 {g['g13_mean_measured']:.2f} が 6.5–8.5 の中か: "
          f"{'❌ 外れる' if g['g13_would_fire'] else '✅ 入る'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
