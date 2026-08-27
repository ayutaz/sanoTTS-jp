#!/usr/bin/env python3
"""β の A/B 聴取を採点する（Phase 5 のタイブレーク）。

判定規則（計画書 §Phase 5）:

* 各 β の選好数を数え、**二項 95%CI が 0.5 を跨いだら β を小さいほうに倒す**
* 同一刺激の反復で答えが割れた割合を**内的一貫性**として出す
* 一貫性が低ければ「差が聞き取れていない」ので、やはり小さいほうに倒す

実行:
    uv run python scripts/score_listening.py --dir reports/listening_beta
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib

import numpy as np


def binom_ci(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval。**正規近似は n が小さいと外す**ので使わない。"""
    if n == 0:
        return (0.0, 1.0)
    from scipy.stats import norm

    z = float(norm.ppf(1 - (1 - conf) / 2))
    p = k / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    root = pathlib.Path(args.dir)

    key = json.load(open(root / "key.json"))
    trials = {t["trial"]: t for t in key["trials"]}
    betas = key["betas"]

    answers = {}
    for r in csv.DictReader(open(root / "answer_sheet.csv")):
        v = (r.get("好ましいほう(A/B/同じ)") or "").strip().upper()
        if v in ("A", "B", "同じ", "SAME", "="):
            answers[r["trial"]] = "同じ" if v in ("同じ", "SAME", "=") else v
    if not answers:
        raise SystemExit(
            f"{root}/answer_sheet.csv が空です。README.md の手順で埋めてください")

    votes = {b: 0 for b in betas}
    ties = 0
    per_uid: dict[str, list] = {}
    for tid, a in answers.items():
        t = trials[tid]
        if a == "同じ":
            ties += 1
            chosen = None
        else:
            chosen = t[f"{a}_beta"]
            votes[chosen] += 1
        per_uid.setdefault(t["uid"], []).append(chosen)

    # 内的一貫性: 同じ文で答えが割れた割合
    rep = [v for v in per_uid.values() if len(v) >= 2]
    inconsistent = sum(1 for v in rep if len(set(v)) > 1)
    consistency = 1 - inconsistent / len(rep) if rep else float("nan")

    decided = sum(votes.values())
    hi, lo = max(betas, key=lambda b: votes[b]), min(betas, key=lambda b: votes[b])
    k = votes[hi]
    ci = binom_ci(k, decided) if decided else (0.0, 1.0)
    crosses = ci[0] <= 0.5 <= ci[1]
    winner = min(betas) if crosses else hi

    out = {
        "dir": str(root), "n_answered": len(answers), "n_decided": decided,
        "n_ties": ties, "votes": {str(b): votes[b] for b in betas},
        "preference_for_beta_%g" % hi: {
            "k": k, "n": decided, "p": k / decided if decided else None,
            "ci95_wilson": list(ci), "crosses_0.5": crosses},
        "internal_consistency": {
            "n_sentences_with_repeats": len(rep),
            "inconsistent": inconsistent, "rate": consistency},
        "decision": f"β = {winner:g}",
        "rule": "二項 95%CI が 0.5 を跨いだら β を小さいほうに倒す（計画書 §Phase 5）",
        "repro": f"uv run python scripts/score_listening.py --dir {root}",
    }
    (root / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"回答 {len(answers)} / 判定 {decided}（同じ {ties}）")
    for b in betas:
        print(f"  β={b:g}: {votes[b]} 票")
    print(f"\nβ={hi:g} の選好率 {k}/{decided} = {k/decided:.3f}  "
          f"95%CI [{ci[0]:.3f}, {ci[1]:.3f}]  0.5 を跨ぐ: {crosses}")
    print(f"内的一貫性 {consistency:.3f}（{len(rep)} 文中 {inconsistent} 文で答えが割れた）")
    if consistency < 0.7:
        print("  ⚠️ 一貫性が低い。**差が聞き取れていない**可能性が高い")
    print(f"\n判定: **β = {winner:g}**")
    print(f"→ {root}/result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
