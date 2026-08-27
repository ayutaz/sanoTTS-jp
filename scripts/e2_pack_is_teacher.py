#!/usr/bin/env python3
"""G1 の強い陽性対照: **パックの `yT` / `zT` が本当に教師の出力か**を実測で確かめる。

`reports/eval_v2/teacher/*.wav` との照合では、書き出し経路の二重量子化
（`int16_roundtrip` の ÷32767 → libsndfile の PCM_16 ×32768）が挟まるので
bit 一致にならない。ここでは**教師をその場で回して int16 量子化前の値と比べる**。

⚠️ 教師の呼び方は CLAUDE.md「教師モデルの扱い」節に従う
（CPU / EMA を remove_weight_norm の前 / prosody ゼロ / speaker_embeddings=None /
 noise_scale = noise_scale_w = 0 / lid=0 / length_scale=1）。

**陰性対照つき**: uid を 1 つずらした照合が同時に落ちることを確かめる。
落ちなければ、このスクリプトは何も比較していない。

実行:
    uv run python scripts/e2_pack_is_teacher.py --n 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import torch

PIPER_PLUS = os.path.expanduser("~/Documents/piper-plus")
sys.path.insert(0, PIPER_PLUS + "/src/python")
sys.path.insert(0, PIPER_PLUS + "/src/python/g2p")
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import gen_teacher_labels as G  # noqa: E402
from saanotts_jp.labelpack import YT_SCALE, PackReader  # noqa: E402
from saanotts_jp.teacher_identity import check as teacher_src_check  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--out", default="reports/e2_ladder/pack_is_teacher.json")
    ap.add_argument("--uids-from", default="reports/eval_v2/eval.json",
                    help="utterances[].uid を持つ JSON（ladder.json でもよい）")
    a = ap.parse_args()

    bad = teacher_src_check()
    if bad:
        print(f"⚠️ piper-plus のソース SHA が固定値と違う: {list(bad)}")

    pack = PackReader(a.pack)
    meta = [json.loads(l) for l in open(os.path.join(a.pack, "index.jsonl"))]
    ev = json.load(open(a.uids_from))
    uids = [u["uid"] for u in ev["utterances"]][: a.n]
    seq = {m["uid"]: m["seq"] for m in meta}

    snap = G.snapshot()
    ckpt = torch.load(snap + G.CKPT, map_location="cpu", weights_only=False)
    teacher = G.build_teacher(ckpt)        # EMA assert つき

    rows = []
    cache = {}
    for uid in uids:
        item = pack[seq[uid]]
        ids = item["ids"].tolist()
        with torch.no_grad():
            o = teacher.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                              lid=torch.tensor([0]), noise_scale=0.0,
                              noise_scale_w=0.0, length_scale=1.0,
                              prosody_features=torch.zeros(1, len(ids), 3),
                              speaker_embeddings=None)
        y = o.audio.squeeze().numpy()
        z = o.latents[0].squeeze(0).numpy()          # (z, z_p, m_p, logs_p) の z
        d = o.durations[0].numpy()
        cache[uid] = (y, z, d)
        yi_teacher = np.clip(np.round(y * YT_SCALE), -YT_SCALE, YT_SCALE).astype(np.int16)
        yi_pack = np.round(item["yT"] * YT_SCALE).astype(np.int16)
        zt_pack = item["zT"]
        z_f16 = z.astype(np.float16).astype(np.float32)
        rows.append({
            "uid": uid,
            "len_match": len(yi_teacher) == len(yi_pack),
            "yT_int16_bitexact": bool(len(yi_teacher) == len(yi_pack)
                                      and np.array_equal(yi_teacher, yi_pack)),
            "yT_max_abs_lsb": (int(np.abs(yi_teacher.astype(np.int64)
                                          - yi_pack.astype(np.int64)).max())
                               if len(yi_teacher) == len(yi_pack) else None),
            "zT_f16_bitexact": bool(zt_pack.shape == z_f16.shape
                                    and np.array_equal(zt_pack, z_f16)),
            "dT_bitexact": bool(np.array_equal(np.float32(d), item["dT"])),
            "frames": int(zt_pack.shape[1]),
        })

    # --- 陰性対照: uid を 1 つずらす。ここが通ったら比較していない ---
    neg = []
    for i, uid in enumerate(uids):
        alt = uids[(i + 1) % len(uids)]
        if alt == uid:
            continue
        y_alt = cache[alt][0]
        yi_alt = np.clip(np.round(y_alt * YT_SCALE), -YT_SCALE, YT_SCALE).astype(np.int16)
        yi_pack = np.round(pack[seq[uid]]["yT"] * YT_SCALE).astype(np.int16)
        neg.append({"pack_uid": uid, "teacher_uid": alt,
                    "bitexact": bool(len(yi_alt) == len(yi_pack)
                                     and np.array_equal(yi_alt, yi_pack))})

    pos_ok = all(r["yT_int16_bitexact"] and r["zT_f16_bitexact"] and r["dT_bitexact"]
                 for r in rows)
    neg_ok = all(not r["bitexact"] for r in neg)
    rep = {"task": "G1 強陽性対照: pack の yT/zT/dT が教師の出力そのものか",
           "pack": a.pack, "n": len(rows), "device": "cpu",
           "piper_plus_source_mismatch": {k: v for k, v in bad.items()},
           "positive": rows, "negative_control": neg,
           "ok": bool(pos_ok and neg_ok and neg),
           "uid_source": a.uids_from,
           "repro": f"uv run python scripts/e2_pack_is_teacher.py --n {a.n}"
                    f" --uids-from {a.uids_from} --out {a.out}"}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
