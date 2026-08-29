#!/usr/bin/env python3
"""Stage 3 の step 数を上げたとき、**decoder が過剰特化していないか**を見る。

## なぜ L1 だけでは足りないか

`L1_c_s3` = `Gγ(Eρ(zT))` は**教師の潜在 `zT` を入力に使う**。これは Stage 3 が
実際に最適化しているレーンそのものなので、step を増やせば当然良くなる。
**しかし成果物の経路は `Aβ` が出す `c` を入力にする。**

Stage 3 を延ばしすぎると **decoder が `Eρ(zT)` の分布に過剰特化し、`Aβ` の出力に
対して脆くなる**恐れがある。L1 だけを見ているとこれは検出できない。

そこで **2 本を同時に測る**:

| レーン | 中身 | 何が分かるか |
|---|---|---|
| `L1_c_s3` | `Gγ(Eρ(zT))` | Stage 3 が最適化している対象そのもの |
| **`L2_oracle_d`** | **`Gγ(Aβ(ids, ceil(dT)))`** | **`Aβ` の出力に対する汎化**（duration は教師で固定） |

⚠️ `Aβ` は **Stage 2 の重み**（Stage 4 の共適応前）。したがって L2 は
「共適応する前の時点で、decoder が acoustic の出力を扱えているか」を見る。
**L1 が伸びているのに L2 が伸びない / 悪化するなら、それが過剰特化の兆候。**

⚠️ duration は教師の `ceil(dT)` で固定してある。**`Dα` の汎化はここに入っていない**
（Stage 3 は `Dα` を触らないので、この判断には要らない）。

実行:
    uv run --extra eval python scripts/d3_stage3_ladder.py \
        --run runs/v3 --run runs/s160000 --n 200 --seed 7 --out reports/s3_ladder
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from eval_student import int16_roundtrip, pad  # noqa: E402
from saanotts_jp._param_reference import Acoustic, Decoder, Erho  # noqa: E402
from saanotts_jp.labelpack import HOP, SR, PackReader  # noqa: E402
from saanotts_jp.vocab import map_ids  # noqa: E402


def sd_hash(sd: dict) -> str:
    m = hashlib.sha256()
    for k in sorted(sd):
        m.update(k.encode())
        m.update(sd[k].detach().cpu().numpy().tobytes())
    return m.hexdigest()


def paired_diff_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    return {"diff": round(float(d.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)],
            "n": int(len(d))}


def load(run: pathlib.Path, device):
    """`stage3.pt` から `Eρ`/`Gγ`、`stage2.pt` から `Aβ` を復元する。"""
    p3, p2 = run / "stage3.pt", run / "stage2.pt"
    for p in (p3, p2):
        if not p.exists():
            raise SystemExit(f"{p} がありません")
    c3 = torch.load(p3, map_location="cpu", weights_only=False)
    c2 = torch.load(p2, map_location="cpu", weights_only=False)
    out = {}
    for name, cls, sd in (("erho", Erho, c3["state"]["erho"]),
                          ("dec", Decoder, c3["state"]["decoder"]),
                          ("acoustic", Acoustic, c2["state"]["acoustic"])):
        m = cls().to(device).eval()
        m.load_state_dict(sd)
        # ⚠️ 載せ忘れると乱数初期化のまま流れて「延ばすと悪化した」という逆の結論が出る
        if sd_hash(m.state_dict()) != sd_hash(sd):
            raise SystemExit(f"G3 違反: {run}/{name} のロード後の重みが ckpt と違う")
        out[name] = m
    out["steps"] = (c3.get("args") or {}).get("steps")
    out["dec_sha"] = sd_hash(c3["state"]["decoder"])
    out["ac_sha"] = sd_hash(c2["state"]["acoustic"])
    out["name"] = run.name
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="reports/s3_ladder")
    ap.add_argument("--device", default=None)
    a = ap.parse_args()

    device = a.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    outdir = pathlib.Path(a.out)
    pack = PackReader(a.pack)
    meta = [json.loads(l) for l in open(pathlib.Path(a.pack) / "index.jsonl")]
    seq_of = {m["uid"]: m["seq"] for m in meta}
    rng = np.random.default_rng(a.seed)
    pool = [m for m in meta if m["frames"] * HOP > 0]
    sel = rng.choice(len(pool), min(a.n, len(pool)), replace=False)
    uids = [pool[int(i)]["uid"] for i in sel]

    runs = [load(pathlib.Path(r), device) for r in a.run]
    print(f"n={len(uids)} / device={device}")
    for r in runs:
        print(f"  {r['name']:<10} steps={r['steps']:>7}  dec={r['dec_sha'][:12]}"
              f"  ac={r['ac_sha'][:12]}")

    # ⚠️ `Aβ` は全 run で同じ（Stage 2 は共有）。違っていたら比較が成立しない
    if len({r["ac_sha"] for r in runs}) != 1:
        raise SystemExit("G1 違反: run ごとに acoustic が違う。Stage 2 は共有のはず")
    if len({r["dec_sha"] for r in runs}) != len(runs):
        raise SystemExit("G2 違反: decoder が重複している（同じ ckpt を 2 回渡した）")

    lanes = ["L0"] + [f"{r['name']}/{ln}" for r in runs for ln in ("L1", "L2")]
    for ln in lanes:
        (outdir / ln.replace("/", "__")).mkdir(parents=True, exist_ok=True)

    for uid in uids:
        item = pack[seq_of[uid]]
        zt = torch.from_numpy(item["zT"])[None].to(device)
        x = torch.from_numpy(map_ids(item["ids"])).long()[None].to(device)
        d_ceil = torch.from_numpy(np.ceil(item["dT"]).astype(np.int64))[None].to(device)
        sf.write(outdir / "L0" / f"{uid}.wav",
                 pad(int16_roundtrip(item["yT"].astype(np.float32))), SR)
        with torch.no_grad():
            for r in runs:
                c_t = r["erho"](zt)
                y1 = Decoder.istft(*r["dec"](c_t))[0].cpu().numpy()
                sf.write(outdir / f"{r['name']}__L1" / f"{uid}.wav",
                         pad(int16_roundtrip(y1)), SR)
                c_h = r["acoustic"](x, d_ceil)
                y2 = Decoder.istft(*r["dec"](c_h))[0].cpu().numpy()
                sf.write(outdir / f"{r['name']}__L2" / f"{uid}.wav",
                         pad(int16_roundtrip(y2)), SR)

    from saanotts_jp.scoreq_metric import score_files
    keys = ["L0"] + [f"{r['name']}__{ln}" for r in runs for ln in ("L1", "L2")]
    idx = {k: [outdir / k / f"{u}.wav" for u in uids] for k in keys}
    scored = score_files([str(p) for k in keys for p in idx[k]],
                         domain="synthetic", mode="nr")
    val = {k: np.array([scored[str(p)] for p in idx[k]], float) for k in keys}

    ref = val["L0"]
    res = {"n": len(uids), "seed": a.seed, "teacher_scoreq": round(float(ref.mean()), 4),
           "per_run": {}}
    print(f"\n  教師 (L0) SCOREQ {ref.mean():.4f}\n")
    print(f"  {'run':<10}{'steps':>8}{'L1 gap':>10}{'L1 CI95':>20}"
          f"{'L2 gap':>10}{'L2 CI95':>20}")
    for r in runs:
        g1 = paired_diff_ci(ref, val[f"{r['name']}__L1"])
        g2 = paired_diff_ci(ref, val[f"{r['name']}__L2"])
        res["per_run"][r["name"]] = {"steps": r["steps"], "L1": g1, "L2": g2,
                                     "decoder_sha256": r["dec_sha"]}
        c1 = "[%+.4f,%+.4f]" % (g1["ci95"][0], g1["ci95"][1])
        c2 = "[%+.4f,%+.4f]" % (g2["ci95"][0], g2["ci95"][1])
        print(f"  {r['name']:<10}{r['steps']:>8,}{g1['diff']:>10.4f}{c1:>20}"
              f"{g2['diff']:>10.4f}{c2:>20}")
    res["caveats"] = [
        "L1 は Stage 3 が最適化している対象そのもの。伸びて当然",
        "L2 は Aβ（Stage 2 の重み）の出力に対する汎化。**過剰特化はここに出る**",
        "duration は教師の ceil(dT) で固定。Dα の汎化は入っていない",
        "SCOREQ は日本語で較正されていない（D-013 / D-020）。人は聴いていない",
    ]
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ladder.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"\n  → {outdir / 'ladder.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
