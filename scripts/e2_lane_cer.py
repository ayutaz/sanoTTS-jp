#!/usr/bin/env python3
"""E-2 ラダーの**かな CER**（レーン別）。

`scripts/measure_cer.py` の正規化・かな化・CER をそのまま使う（規則を二度書かない）。
参照テキストはパックの `index.jsonl` から引く。

⚠️ **主指標はかな CER**。表記 CER は Whisper が同じ音を漢字でもひらがなでも
書き起こすので跳ねる（C-023）。⚠️ Whisper 自体の誤りが全レーンに乗るので
**教師レーン (L0) との差**で読む。

実行:
    uv run --extra eval python scripts/e2_lane_cer.py --dir reports/e2_ladder \
        --lane L0_teacher --lane L1_c_s4 --lane L2_oracle_d --lane L3_student
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

from measure_cer import cer, normalize, to_kana  # noqa: E402


def paired_diff_ci(a, b, n_boot=20000, seed=0):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    return {"mean_diff": round(float(d.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)], "n": int(len(d))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="reports/e2_ladder")
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--lane", action="append", default=None)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    root = pathlib.Path(a.dir)
    ladder = json.loads((root / "ladder.json").read_text())
    lanes = a.lane or ladder["lanes"]
    if len(set(lanes)) != len(lanes):
        raise SystemExit(f"レーンが重複している {lanes}")
    uids = [r["uid"] for r in ladder["utterances"]]
    if a.limit:
        uids = uids[: a.limit]
    text_of = {m["uid"]: m["text"]
               for m in (json.loads(l) for l in open(pathlib.Path(a.pack) / "index.jsonl"))}

    from faster_whisper import WhisperModel
    print(f"Whisper {a.model} を読み込み中…")
    model = WhisperModel(a.model, device="cpu", compute_type="int8")

    def transcribe(p: str) -> str:
        segs, _ = model.transcribe(p, language="ja", beam_size=5,
                                   condition_on_previous_text=False)
        return "".join(s.text for s in segs).strip()

    rows, fails = [], 0
    t0 = time.time()
    for uid in uids:
        ref_k = to_kana(text_of[uid])
        rec = {"uid": uid, "text": text_of[uid], "ref_kana": ref_k}
        for lane in lanes:
            hyp = transcribe(str(root / lane / f"{uid}.wav"))
            hyp_k = to_kana(hyp)
            rec[f"{lane}_hyp"] = hyp
            rec[f"{lane}_cer_surface"] = cer(normalize(text_of[uid]), normalize(hyp))
            if ref_k is None or hyp_k is None:
                fails += 1
                rec[f"{lane}_cer"] = None
            else:
                rec[f"{lane}_cer"] = cer(ref_k, hyp_k)
        rows.append(rec)
        print(f"  {uid:<26} " + "  ".join(
            f"{l.split('_')[0]}={('--' if rec[l+'_cer'] is None else f'{rec[l+chr(95)+chr(99)+chr(101)+chr(114)]:.3f}')}"
            for l in lanes))

    ok = [r for r in rows if all(r[f"{l}_cer"] is not None for l in lanes)]
    vals = {l: np.array([r[f"{l}_cer"] for r in ok]) for l in lanes}
    summ = {l: {"n": int(v.size), "mean": round(float(v.mean()), 4),
                "median": round(float(np.median(v)), 4),
                "sd": round(float(v.std(ddof=1)), 4)} for l, v in vals.items()}
    diffs = {}
    if "L0_teacher" in lanes:
        for l in lanes:
            if l != "L0_teacher":
                diffs[f"{l}__minus__L0_teacher"] = paired_diff_ci(vals[l], vals["L0_teacher"])
    adj = {}
    order = [l for l in ("L0_teacher", "L1_c_s4", "L2_oracle_d", "L3_student") if l in lanes]
    for i in range(len(order) - 1):
        adj[f"{order[i+1]}__minus__{order[i]}"] = paired_diff_ci(vals[order[i + 1]],
                                                                 vals[order[i]])
    rep = {"task": "E-2 レーン別 かな CER", "dir": str(root), "model": a.model,
           "lanes": lanes, "n": len(rows), "n_used": len(ok), "n_kana_fail": fails,
           "elapsed_sec": round(time.time() - t0, 1),
           "per_lane": summ, "vs_teacher_paired": diffs, "adjacent_paired": adj,
           "warnings": [
               "Whisper 自体の誤りが全レーンに乗る。L0 との差で読む",
               "n が小さい。少数の失敗発話が平均を動かす（median も併記）",
               "表記 CER も出しているが主指標はかな CER（C-023）"],
           "rows": rows,
           "repro": f"uv run --extra eval python scripts/e2_lane_cer.py --dir {root} "
                    + " ".join(f"--lane {l}" for l in lanes)}
    (root / "cer.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print("\n=== かな CER（レーン別）===")
    for l in lanes:
        print(f"  {l:<14} mean {summ[l]['mean']:.4f}  median {summ[l]['median']:.4f}  n={summ[l]['n']}")
    print("\n=== 隣接レーン差（対応のある bootstrap）===")
    for k, v in adj.items():
        print(f"  {k:<40} {v['mean_diff']:+.4f}  CI {v['ci95']}")
    print(f"\n→ {root}/cer.json  ({rep['elapsed_sec']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
