#!/usr/bin/env python3
"""Phase 5: 式7 の摩擦音ノイズ注入 `β` の候補を絞る。

```
z̃_{t,k} = ẑ_{t,k} + 1[x_t ∈ S_ja] · β · σT_k · ε_{t,k}
```

⚠️ **最終決定は聴取でやる。** 論文も聴取で β=6 を選んでおり、
**集約スコア（SCOREQ）はむしろ下がる**（4.09 → 3.92）。
このスクリプトがやるのは**候補を 2〜3 個に絞ること**だけ。

主目的は「平坦度の最大化」ではなく **教師との一致**:

```
J(β) = Σ_c w_c · |SFM_c(生徒, β) / SFM_c(教師) − 1|      c ∈ S_ja が属するクラス
```

**「平坦度が高いほど良い」にしない** — 際限なくノイズを足す解に落ちる。

ガードレール（論文 §Phase 5 の設計）:
- `SCOREQ_ratio(β) ≥ 0.95 × SCOREQ_ratio(0)`
- `UTMOS_ratio(β)  ≥ 0.975 × UTMOS_ratio(0)`

実行:
    uv run --extra eval python scripts/b_beta_sweep.py --ckpt runs/v2/stage4.pt \
        --betas 0,2,4,6,8 --n 16 --out reports/beta_sweep
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, "src")

#: J(β) で見るクラス。**式7 が触るのは S_ja の音素だけ**なので、
#: そのクラス（摩擦音・破擦音・無声化母音）に重みを置く
TARGET_CLASSES = {"fricative": 1.0, "affricate": 1.0, "devoiced": 1.0}
#: 触らないクラス。**ここが動いたら副作用**なので監視する
GUARD_CLASSES = ("vowel", "nasal", "stop", "approximant")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--betas", default="0,2,4,6,8")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--out", default="reports/beta_sweep")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    betas = [float(b) for b in args.betas.split(",")]
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # ⚠️ **1 プロセスで回す。** β ごとに subprocess を起こすと、教師 ckpt (927 MB) +
    # UTMOS + SCOREQ の読み込みが積み上がって **SIGABRT で落ちる**（実測: n=16 の
    # 3 条件目で exit 134）。教師 wav は β に依存しないので使い回すのが正しくもある。
    sys.path.insert(0, "scripts")
    import eval_student as ES  # noqa: PLC0415
    import synthesize_student as SS  # noqa: PLC0415
    import torch  # noqa: PLC0415

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    rows = ES.pick_rows("data/splits/corpus_heldout.tsv", args.n, args.seed)
    print(f"{len(rows)} 文（汚染除外後 / seed {args.seed}）")
    teacher_bundle = ES.load_teacher()
    student_bundle = SS.load_student(args.ckpt, device)

    runs = {}
    for b in betas:
        sub = outdir / f"beta{b:g}"
        print(f"  β = {b:g} …", end="", flush=True)
        runs[b] = ES.run_eval(args.ckpt, sub, rows=rows,
                              teacher_bundle=teacher_bundle,
                              student_bundle=student_bundle,
                              beta=b, device=device, seed=args.seed, quiet=True)
        q = runs[b]["quality"]["scoreq_synthetic_nr"]["ratio_student_over_teacher"]
        print(f" SCOREQ 比 {q['ratio']:.4f}")

    base = runs[betas[0]]
    rows = []
    for b in betas:
        e = runs[b]
        fr = e["flatness_student_over_teacher"]
        j = sum(w * abs(fr[c]["sfm"] - 1.0)
                for c, w in TARGET_CLASSES.items() if c in fr)
        j /= sum(w for c, w in TARGET_CLASSES.items() if c in fr)
        side = {c: fr[c]["sfm"] for c in GUARD_CLASSES if c in fr}
        rms = {c: fr[c]["band_rms"] for c in TARGET_CLASSES if c in fr}
        q = e["quality"]
        rows.append({
            "beta": b, "J": j,
            "sfm_ratio_target": {c: fr[c]["sfm"] for c in TARGET_CLASSES if c in fr},
            "band_rms_ratio_target": rms,
            "sfm_ratio_guard": side,
            "scoreq_ratio": q["scoreq_synthetic_nr"]["ratio_student_over_teacher"]["ratio"],
            "utmos_ratio": q["utmos"]["ratio_student_over_teacher"]["ratio"],
        })

    s0 = rows[0]["scoreq_ratio"]
    u0 = rows[0]["utmos_ratio"]
    for r in rows:
        r["passes_guard"] = bool(r["scoreq_ratio"] >= 0.95 * s0
                                 and r["utmos_ratio"] >= 0.975 * u0)

    passed = [r for r in rows if r["passes_guard"]]
    ranked = sorted(passed, key=lambda r: r["J"])[:3]

    rep = {
        "ckpt": args.ckpt, "n_utterances": args.n, "betas": betas,
        "objective": "J(β) = mean_c |SFM_c(生徒)/SFM_c(教師) − 1| over "
                     + ", ".join(TARGET_CLASSES),
        "guard": {"scoreq": f">= 0.95 x {s0:.4f}", "utmos": f">= 0.975 x {u0:.4f}"},
        "rows": rows,
        "candidates_for_listening": [r["beta"] for r in ranked],
        "decision": "⚠️ **未決。最終決定は聴取（CMOS）でやる。**",
        "warnings": [
            "**J(β) は代理指標**。論文は聴取で β=6 を選び、SCOREQ はむしろ下がった",
            "**power 計算をしていない。** ガードの 5% / 2.5% の差を n="
            f"{args.n} で分離できるかは未検証（計画書 §Phase 5 の指摘）",
            "破擦音 {ts, ch} は閉鎖＋摩擦の複合。閉鎖部への注入は劣化しうるが、"
            "論文はこの区別をしていない。時間マスクは未実装",
        ],
        "repro": f"uv run --extra eval python scripts/b_beta_sweep.py --ckpt {args.ckpt} "
                 f"--betas {args.betas} --n {args.n} --out {args.out}",
    }
    (outdir / "sweep.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))

    print(f"\n{'β':>4} {'J(β)':>8} {'fric':>7} {'affr':>7} {'devo':>7} "
          f"{'RMS fric':>9} {'SCOREQ比':>9} {'UTMOS比':>8}  guard")
    for r in rows:
        t = r["sfm_ratio_target"]
        print(f"{r['beta']:>4.0f} {r['J']:>8.4f} "
              f"{t.get('fricative', float('nan')):>7.4f} "
              f"{t.get('affricate', float('nan')):>7.4f} "
              f"{t.get('devoiced', float('nan')):>7.4f} "
              f"{r['band_rms_ratio_target'].get('fricative', float('nan')):>9.4f} "
              f"{r['scoreq_ratio']:>9.4f} {r['utmos_ratio']:>8.4f}  "
              f"{'OK' if r['passes_guard'] else 'NG'}")
    print(f"\n聴取候補（J の小さい順・ガード通過のみ）: "
          f"{rep['candidates_for_listening']}")
    print("⚠️ **これは候補の提示であって決定ではない。** 最終決定は聴取で行う。")
    print(f"   wav: {outdir}/beta*/student/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
