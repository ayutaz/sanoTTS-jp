#!/usr/bin/env python3
"""E-2 ラダーの品質指標（SCOREQ / UTMOS / DNSMOS）と**対応のある** CI。

⚠️ **`scripts/eval_metrics.py:ratio_ci` は docstring どおり「独立 2 標本、非対応」。**
全レーンが同じ発話から描かれているのでそのまま使うと検出力を捨てる。ここでは
**発話単位でペアを保った bootstrap** を使う。

⚠️ SCOREQ / UTMOS / DNSMOS はいずれも日本語で較正されていない（D-013 / D-020）。
絶対値を論文の英語スコアと比べない。DNSMOS は `--human` で天井を測ってから読む。

実行:
    uv run --extra eval python scripts/e2_lane_metrics.py --dir reports/e2_ladder
    uv run --extra eval python scripts/e2_lane_metrics.py --dir reports/e2_ladder --human
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

# ⚠️ **DNSMOS はここでは測れない。** UTMOS を配る `tarepan/SpeechMOS` の
# torch.hub checkout に**同名の `speechmos` パッケージ**があり、
# 同一プロセスでは必ずどちらかが `ImportError` になる（両方向を実測）。
# DNSMOS は `scripts/e2_dnsmos.py` で単独に測り、`--dnsmos-json` で合流させる。

LANE_ORDER = ("L0_teacher", "L_repr", "L1_c_s3", "L1_c_s4", "L2_oracle_d", "L3_student",
              "T0_dec_zT", "T1_dec_lift_cT", "T2_dec_lift_chat")
#: 隣接差を取る「はしご」。L1 は共適応後 (s4) を本線にする（s3 は G7 の対照）
CHAINS = (("L0_teacher", "L1_c_s4", "L2_oracle_d", "L3_student"),
          ("T0_dec_zT", "T1_dec_lift_cT", "T2_dec_lift_chat"))
LADDER = CHAINS[0]


def paired_diff_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    """**対応のある** 平均差の bootstrap CI。`a` と `b` は同じ発話の並び。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert a.shape == b.shape
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    lo, hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    return {"mean_diff": round(float(d.mean()), 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "half_width": round((hi - lo) / 2, 4), "n": int(len(d)),
            "paired": True, "n_boot": n_boot, "seed": seed}


def paired_ratio_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    """**対応のある** 平均比の bootstrap CI（発話単位で同じ添字を再標本）。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n_boot, len(a)))
    boots = a[idx].mean(axis=1) / b[idx].mean(axis=1)
    return {"ratio": round(float(a.mean() / b.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)],
            "n": int(len(a)), "paired": True}


def summarize(v) -> dict:
    import eval_metrics as EM
    return EM.summarize(v)


def measure_lane(paths) -> dict:
    import eval_metrics as EM
    from saanotts_jp.scoreq_metric import score_files
    out, t = {}, {}
    t0 = time.time()
    scored = score_files([str(p) for p in paths], domain="synthetic", mode="nr")
    out["scoreq_synthetic_nr"] = [scored[str(p)] for p in paths]
    t["scoreq_sec"] = round(time.time() - t0, 2)
    t0 = time.time()
    out["utmos"] = EM.measure_utmos(paths)
    t["utmos_sec"] = round(time.time() - t0, 2)
    out["_timing"] = t
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="reports/e2_ladder")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dnsmos-json", default=None,
                    help="scripts/e2_dnsmos.py の出力を合流させる（既定は <dir>/dnsmos.json）")
    ap.add_argument("--human", action="store_true",
                    help="実人間 24 本の天井も測る（DNSMOS を絶対値で読む前に必須）")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    root = pathlib.Path(a.dir)
    ladder = json.loads((root / "ladder.json").read_text())
    uids = [r["uid"] for r in ladder["utterances"]]
    lanes = [l for l in LANE_ORDER if l in ladder["lanes"]]
    if not lanes:
        raise SystemExit(f"未知のレーン構成 {ladder['lanes']}")
    global LADDER
    LADDER = next((c for c in CHAINS if all(x in lanes for x in c)), CHAINS[0])
    ref = LADDER[0]
    per_lane = {}
    for lane in lanes:
        paths = [root / lane / f"{u}.wav" for u in uids]
        missing = [p for p in paths if not p.exists()]
        if missing:
            raise SystemExit(f"{lane}: wav が無い {missing[:3]}")
        print(f"  {lane} を測定中… (n={len(paths)})")
        per_lane[lane] = measure_lane(paths)

    # --- DNSMOS を合流させる（別プロセスの出力）---
    dj = pathlib.Path(a.dnsmos_json or (root / "dnsmos.json"))
    dns_human = None
    if dj.exists():
        d = json.loads(dj.read_text())
        if d.get("uids") != uids:
            raise SystemExit(f"{dj} の uid 並びがラダーと違う（合流できない）")
        for ln in lanes:
            for k, v in d["lanes"][ln].items():
                if not k.startswith("_"):
                    per_lane[ln][k] = v
        dns_human = d.get("human")
    else:
        print(f"  ⚠️ {dj} が無いので DNSMOS は合流しない")

    metrics = [k for k in per_lane[lanes[0]] if not k.startswith("_")]
    summary, ratio_to_teacher, adjacent = {}, {}, {}
    for m in metrics:
        summary[m] = {ln: summarize(per_lane[ln][m]) for ln in lanes}
        if ref in lanes:
            ratio_to_teacher[m] = {
                ln: paired_ratio_ci(per_lane[ln][m], per_lane[ref][m], seed=a.seed)
                for ln in lanes if ln != ref}
        steps = [s for s in LADDER if s in lanes]
        adjacent[m] = {
            f"{steps[i]}__minus__{steps[i+1]}":
                paired_diff_ci(per_lane[steps[i]][m], per_lane[steps[i + 1]][m],
                               seed=a.seed)
            for i in range(len(steps) - 1)}
        if "L1_c_s3" in lanes and "L1_c_s4" in lanes:      # G7 の対照
            adjacent[m]["G7_L1_c_s4__minus__L1_c_s3"] = paired_diff_ci(
                per_lane["L1_c_s4"][m], per_lane["L1_c_s3"][m], seed=a.seed)

    # --- 帰属: gap の大小関係そのものを対応のある bootstrap で検定する ---
    # 「decoder gap > acoustic gap」は CI の重なりでは言えない。差を直接測る
    gapcmp = {}
    for m in metrics:
        steps = [x for x in LADDER if x in lanes]
        if len(steps) < 3:
            continue
        v = {ln: np.asarray(per_lane[ln][m], float) for ln in steps}
        names_all = ["decoder", "acoustic", "duration"] if len(steps) == 4 else \
                    ["cline", "acoustic"]
        g = {names_all[i]: v[steps[i]] - v[steps[i + 1]] for i in range(len(steps) - 1)}
        names = list(g)
        gapcmp[m] = {f"{names[i]}__minus__{names[j]}":
                     paired_diff_ci(g[names[i]], g[names[j]], seed=a.seed)
                     for i in range(len(names)) for j in range(i + 1, len(names))}
        gapcmp[m]["_sum_of_gaps"] = round(float(sum(x.mean() for x in g.values())), 4)
        gapcmp[m][f"_total_gap_{steps[0]}_minus_{steps[-1]}"] = round(
            float((v[steps[0]] - v[steps[-1]]).mean()), 4)

    # --- G6: 検出力。隣接差の CI 半幅が 0.10 SCOREQ 以下か ---
    key = "scoreq_synthetic_nr"
    halves = {k: v["half_width"] for k, v in adjacent[key].items()
              if not k.startswith("G7_")}
    g6 = {"metric": key, "half_widths": halves,
          "max_half_width": max(halves.values()) if halves else None,
          "threshold": 0.10, "ok": bool(halves) and max(halves.values()) <= 0.10,
          "threshold_note": "⚠️ 0.10 は全体ギャップ 0.767（教師 1.9732 − 生徒 1.2063, M-37）の"
                            "13% として**測る前に**決めた値"}

    human = None
    if a.human:
        b5 = json.loads(pathlib.Path("reports/b5_scoreq.json").read_text())
        hp = [str(pathlib.Path(p).expanduser()) for p in b5["sets"]["human"]["wavs"]]
        hp = [p for p in hp if pathlib.Path(p).exists()]
        print(f"  実人間 {len(hp)} 本の天井を測定中…")
        human = measure_lane(hp)
        if dns_human:
            for k, v in dns_human.items():
                if not k.startswith("_"):
                    human[k] = v
        human = {"n": len(hp), "wavs": hp[:3],
                 "note": "⚠️ b5_scoreq.json と同じ 24 本。**パディング無しの生の"
                         "コーパス wav**で、レーン側（前後 0.3 s パディング + int16 往復）"
                         "とは前処理が違う",
                 **{m: summarize(human[m]) for m in human if not m.startswith("_")}}

    rep = {"task": "E-2 レーンラダーの品質指標（対応のある CI）",
           "dir": str(root), "n": len(uids), "lanes": lanes,
           "gates": {"G6_power": g6},
           "per_lane_summary": summary,
           "ratio_to_teacher_paired": ratio_to_teacher,
           "adjacent_paired_diff": adjacent,
           "gap_comparisons_paired": gapcmp,
           "human_ceiling": human,
           "timing": {ln: per_lane[ln]["_timing"] for ln in lanes},
           "per_utterance": {ln: {m: per_lane[ln][m] for m in metrics} for ln in lanes},
           "uids": uids,
           "warnings": [
               "SCOREQ / UTMOS / DNSMOS はいずれも日本語で較正されていない（D-013 / D-020）",
               "オラクルレーン（L1 / L2）は教師の時間軸を持つので、韻律が教師どおりである"
               "こと自体が MOS 予測器に加点されうる。レーン間比較にこの交絡が入る",
               "ラダーは単調とは限らない。差の和が全体ギャップに一致することを期待しない",
           ],
           "repro": f"uv run --extra eval python scripts/e2_lane_metrics.py --dir {root}"
                    + (" --human" if a.human else "")}
    out = pathlib.Path(a.out or (root / "metrics.json"))
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1))

    print(f"\n=== レーン別（n={len(uids)}）===")
    hdr = ["scoreq_synthetic_nr", "utmos"] + [m for m in metrics if m.startswith("dnsmos")]
    print(f"  {'lane':<14}" + "".join(f"{h.replace('scoreq_synthetic_nr','SCOREQ').replace('dnsmos_','DNS.'):>12}" for h in hdr))
    for ln in lanes:
        print(f"  {ln:<14}" + "".join(f"{summary[h][ln]['mean']:12.4f}" for h in hdr))
    if human:
        print(f"  {'human(参考)':<14}" + "".join(f"{human[h]['mean']:12.4f}" for h in hdr))
    print(f"\n=== 隣接レーン差（対応のある bootstrap 95% CI）— {key} ===")
    for k, v in adjacent[key].items():
        print(f"  {k:<40} {v['mean_diff']:+7.4f}  CI {v['ci95']}  半幅 {v['half_width']:.4f}")
    if key in gapcmp:
        print(f"\n=== gap の大小関係（対応のある bootstrap）— {key} ===")
        for k, v in gapcmp[key].items():
            if k.startswith("_"):
                print(f"  {k:<40} {v}")
            else:
                sig = "★" if (v["ci95"][0] > 0 or v["ci95"][1] < 0) else " "
                print(f"  {k:<40} {v['mean_diff']:+7.4f}  CI {v['ci95']} {sig}")
    print(f"\nG6 検出力: {'OK' if g6['ok'] else 'NG!'} 最大半幅 {g6['max_half_width']} (しきい値 0.10)")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
