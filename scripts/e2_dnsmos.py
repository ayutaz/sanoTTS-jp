#!/usr/bin/env python3
"""DNSMOS P.835 (+ P.808) を wav 群に対して測る。**別プロセスで動かす必要がある。**

⚠️ **`speechmos` は名前が衝突する。** UTMOS を配る `tarepan/SpeechMOS` の
torch.hub checkout にも `speechmos` という**同名パッケージ**が入っていて、
- 先に UTMOS を読むと `from speechmos import dnsmos` が `ImportError`
- 先に PyPI の speechmos を読むと UTMOS 側が `No module named 'speechmos.utmos22'`
のどちらかで必ず落ちる（実測 2026-08-28、両方向を観測）。
**だから DNSMOS はこのスクリプトで単独に測る。**

⚠️ DNSMOS も日本語で較正されていない。**`--human` の天井なしに絶対値で読まない**（C-012）。

実行:
    uv run --extra eval python scripts/e2_dnsmos.py --dir reports/e2_ladder --human
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from speechmos import dnsmos  # noqa: E402

assert "site-packages" in dnsmos.__file__, dnsmos.__file__
KEYS = ("ovrl_mos", "sig_mos", "bak_mos", "p808_mos")
SR16 = 16000


def load_16k(path: str) -> np.ndarray:
    """soundfile + torchaudio で 16 kHz mono に落とす（eval_metrics と同一実装）。"""
    import torch
    import torchaudio
    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    t = torch.from_numpy(wav[:, 0]).unsqueeze(0)
    if sr != SR16:
        t = torchaudio.transforms.Resample(sr, SR16)(t)
    return t[0].numpy().astype(np.float32)


def run_set(paths) -> dict:
    acc = {f"dnsmos_{k}": [] for k in KEYS}
    t0 = time.time()
    for p in paths:
        r = dnsmos.run(load_16k(str(p)), SR16)
        for k in KEYS:
            acc[f"dnsmos_{k}"].append(float(r[k]))
    acc["_sec"] = round(time.time() - t0, 2)
    return acc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="reports/e2_ladder")
    ap.add_argument("--human", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = pathlib.Path(a.dir)
    ladder = json.loads((root / "ladder.json").read_text())
    uids = [r["uid"] for r in ladder["utterances"]]
    out = {"dir": str(root), "n": len(uids), "uids": uids, "lanes": {}}
    for lane in ladder["lanes"]:
        paths = [root / lane / f"{u}.wav" for u in uids]
        print(f"  {lane} …")
        out["lanes"][lane] = run_set(paths)
    if a.human:
        b5 = json.loads(pathlib.Path("reports/b5_scoreq.json").read_text())
        hp = [str(pathlib.Path(p).expanduser()) for p in b5["sets"]["human"]["wavs"]]
        hp = [p for p in hp if pathlib.Path(p).exists()]
        print(f"  human (n={len(hp)}) …")
        out["human"] = run_set(hp)
        out["human_n"] = len(hp)
        out["human_note"] = ("⚠️ b5_scoreq.json と同じ 24 本。**生のコーパス wav** で、"
                             "レーン側（前後 0.3 s パディング + int16 往復）とは前処理が違う")
    out["repro"] = (f"uv run --extra eval python scripts/e2_dnsmos.py --dir {root}"
                    + (" --human" if a.human else ""))
    p = pathlib.Path(a.out or (root / "dnsmos.json"))
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n  {'set':<14}" + "".join(f"{k.replace('dnsmos_',''):>10}" for k in KEYS))
    for name, v in list(out["lanes"].items()) + ([("human", out["human"])] if a.human else []):
        print(f"  {name:<14}" + "".join(f"{np.mean(v['dnsmos_'+k]):10.4f}" for k in KEYS))
    print(f"\n→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
