#!/usr/bin/env python3
"""S-7: 論文 §IV-B の**もう一方の軸**（教師 decoder に別の潜在を通す）を日本語で埋める。

`e2_lane_ladder.py` の L1 は `Eρ`（40 次元 c-line ボトルネック）と `Gγ` の**合成**で、
両者を分離できない。ここでは **教師 decoder** に

  T0 = `dec(zT)`                     ← 陽性対照。パックの `yT` と bit 一致するはず
  T1 = `dec(lift(Eρ(zT)))`           ← c-line ボトルネックだけ（decoder は教師）
  T2 = `dec(lift(Aβ(ids, ceil(dT))))` ← + acoustic（論文の 4.68 → 3.70 に対応する軸）

を通す。`lift` は c(40) → z(192) の**最小二乗の線形写像**で、
**評価に使わない別の発話**で当てる。

⚠️ **非対称な結論しか出せない。** lift は線形なので、
T1 が**高ければ**「40 次元 c-line で足りる」の証拠になるが、
**低くても**「c-line が悪い」の証拠にはならない（非線形の逆写像ならもっと戻せる）。

⚠️ 教師の呼び方は CLAUDE.md「教師モデルの扱い」節に従う（CPU / EMA は
`remove_weight_norm` の前 / `speaker_embeddings=None` / `lid=0`）。**piper-plus は読み取り専用。**

実行:
    uv run python scripts/e2_teacher_decoder_lanes.py --out reports/e2_teacher_dec
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import numpy as np
import soundfile as sf
import torch

PIPER_PLUS = os.path.expanduser("~/Documents/piper-plus")
sys.path.insert(0, PIPER_PLUS + "/src/python")
sys.path.insert(0, PIPER_PLUS + "/src/python/g2p")
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import gen_teacher_labels as G  # noqa: E402
from e2_lane_ladder import load_modules, snr_db  # noqa: E402
from eval_student import int16_roundtrip, pad  # noqa: E402
from saanotts_jp.labelpack import HOP, SR, YT_SCALE, PackReader  # noqa: E402
from saanotts_jp.vocab import map_ids  # noqa: E402

LANES = ("T0_dec_zT", "T1_dec_lift_cT", "T2_dec_lift_chat")


def fit_lift(pack, seq_list, erho, max_frames=200000):
    """c(40) → z(192) の最小二乗線形写像（bias 付き）を当てる。"""
    Cs, Zs, n = [], [], 0
    with torch.no_grad():
        for s in seq_list:
            d = pack[s]
            z = torch.from_numpy(d["zT"])[None]
            c = erho(z)[0].numpy().T                    # [T, 40]
            Cs.append(c)
            Zs.append(d["zT"].T)                        # [T, 192]
            n += c.shape[0]
            if n >= max_frames:
                break
    C = np.concatenate(Cs).astype(np.float64)
    Z = np.concatenate(Zs).astype(np.float64)
    A = np.hstack([C, np.ones((len(C), 1))])            # [N, 41]
    W, *_ = np.linalg.lstsq(A, Z, rcond=None)           # [41, 192]
    pred = A @ W
    ss_res = ((Z - pred) ** 2).sum(0)
    ss_tot = ((Z - Z.mean(0)) ** 2).sum(0)
    r2 = 1.0 - ss_res / np.maximum(ss_tot, 1e-12)
    return W, {"n_frames_fit": int(len(C)), "n_utts_fit": len(Cs),
               "r2_mean": float(r2.mean()), "r2_median": float(np.median(r2)),
               "r2_min": float(r2.min()), "r2_max": float(r2.max())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--out", default="reports/e2_teacher_dec")
    ap.add_argument("--uids-from", default="reports/eval_v2/eval.json")
    ap.add_argument("--fit-utts", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--verify-fp32", type=int, default=3,
                    help="陽性対照: この件数だけ教師を回し直して fp32 の z で decode する")
    a = ap.parse_args()

    pack = PackReader(a.pack)
    meta = [json.loads(l) for l in open(pathlib.Path(a.pack) / "index.jsonl")]
    seq_of = {m["uid"]: m["seq"] for m in meta}
    uids = [u["uid"] for u in json.load(open(a.uids_from))["utterances"]]
    eval_seq = {seq_of[u] for u in uids}
    # **評価発話を fit に使わない**（使うと lift が評価文に当てはまるだけになる）
    rng = np.random.default_rng(a.seed)
    pool = [m["seq"] for m in meta if m["seq"] not in eval_seq]
    fit_seq = [int(x) for x in rng.choice(pool, min(a.fit_utts, len(pool)), replace=False)]
    assert not (set(fit_seq) & eval_seq)

    mods, minfo = load_modules("cpu")
    W, fitinfo = fit_lift(pack, fit_seq, mods["erho"])
    print(f"lift 適合: {fitinfo}")

    snap = G.snapshot()
    ckpt = torch.load(snap + G.CKPT, map_location="cpu", weights_only=False)
    teacher = G.build_teacher(ckpt)
    with torch.no_grad():
        g = teacher._get_global_conditioning(None, torch.tensor([0]),
                                             speaker_embeddings=None)

    @torch.no_grad()
    def dec(z: torch.Tensor) -> np.ndarray:
        """`SynthesizerTrn.infer` と同じ decode（y_mask は全長 1 なので省ける）。"""
        out = teacher.dec(z, g=g)
        o = out[0] if isinstance(out, tuple) else out
        return o.squeeze().numpy()

    outdir = pathlib.Path(a.out)
    for lane in LANES:
        (outdir / lane).mkdir(parents=True, exist_ok=True)

    Wt = torch.from_numpy(W.astype(np.float32))          # [41, 192]

    def lift(c: torch.Tensor) -> torch.Tensor:
        """c [1,40,T] → ẑ [1,192,T]。"""
        x = torch.cat([c[0].t(), torch.ones(c.shape[-1], 1)], dim=1)   # [T, 41]
        return (x @ Wt).t()[None]

    # ---- 陽性対照は **fp32 の z** で取る ----
    # ⚠️ パックの `zT` は fp16 で保存されている（labelpack の設計）。それを decoder に
    # 戻すと yT と bit 一致しない（実測 SNR 73.6 dB / max 5 LSB）。**decode 経路の
    # 誤りではなく保存精度**であることを、その場で infer し直して切り分ける。
    fp32_check = []
    for uid in uids[: a.verify_fp32]:
        item = pack[seq_of[uid]]
        ids = item["ids"].tolist()
        with torch.no_grad():
            o = teacher.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                              lid=torch.tensor([0]), noise_scale=0.0, noise_scale_w=0.0,
                              length_scale=1.0,
                              prosody_features=torch.zeros(1, len(ids), 3),
                              speaker_embeddings=None)
            z32 = o.latents[0] * o.y_mask
            y32 = dec(z32)
        yp = np.round(item["yT"] * YT_SCALE).astype(np.int16)
        y32i = np.clip(np.round(y32 * YT_SCALE), -YT_SCALE, YT_SCALE).astype(np.int16)
        y16 = dec(torch.from_numpy(item["zT"])[None])
        fp32_check.append({
            "uid": uid,
            "dec_of_fp32_z_bitexact_vs_pack_yT": bool(len(y32i) == len(yp)
                                                      and np.array_equal(y32i, yp)),
            "dec_of_fp16_z_snr_db_vs_pack_yT": round(snr_db(item["yT"], y16), 2),
        })

    rows, pos_ok, neg_ok = [], 0, 0
    for i, uid in enumerate(uids):
        item = pack[seq_of[uid]]
        zt = torch.from_numpy(item["zT"])[None]
        ids_s = map_ids(item["ids"])
        x = torch.from_numpy(ids_s).long()[None]
        d_ceil = torch.from_numpy(np.ceil(item["dT"]).astype(np.int64))[None]
        with torch.no_grad():
            c_t = mods["erho"](zt)
            c_h = mods["acoustic"](x, d_ceil)
        y = {"T0_dec_zT": dec(zt),
             "T1_dec_lift_cT": dec(lift(c_t)),
             "T2_dec_lift_chat": dec(lift(c_h))}
        # 陽性対照: T0 がパックの yT と int16 で bit 一致すること
        yi = np.clip(np.round(y["T0_dec_zT"] * YT_SCALE), -YT_SCALE, YT_SCALE).astype(np.int16)
        yp = np.round(item["yT"] * YT_SCALE).astype(np.int16)
        exact = bool(len(yi) == len(yp) and np.array_equal(yi, yp))
        pos_ok += exact
        rec = {"uid": uid, "frames": item["zT"].shape[1],
               "T0_bitexact_vs_pack": exact,
               "T0_max_abs_lsb": (int(np.abs(yi.astype(np.int64) - yp.astype(np.int64)).max())
                                  if len(yi) == len(yp) else None)}
        for lane in LANES:
            q = int16_roundtrip(y[lane])
            sf.write(outdir / lane / f"{uid}.wav", pad(q), SR)
            rec[f"{lane}_samples"] = int(len(q))
            rec[f"{lane}_snr_db"] = snr_db(item["yT"], y[lane])
        rows.append(rec)
    # 陰性対照: uid をずらして比べたら bit 一致しない
    for i, uid in enumerate(uids):
        alt = uids[(i + 1) % len(uids)]
        if alt == uid:
            continue
        ya, _ = sf.read(outdir / "T0_dec_zT" / f"{alt}.wav", dtype="float32")
        yb, _ = sf.read(outdir / "T0_dec_zT" / f"{uid}.wav", dtype="float32")
        neg_ok += int(len(ya) == len(yb) and np.array_equal(ya, yb))

    rep = {"task": "S-7: 教師 decoder に別の潜在を通す（c-line と decoder の分離）",
           "pack": a.pack, "n": len(rows), "lanes": list(LANES),
           "lift": {"kind": "linear least squares c(40)+bias -> z(192)",
                    "fit_utts": len(fit_seq), "disjoint_from_eval": True, **fitinfo},
           "modules": minfo,
           "gates": {
               "T0_positive_control_fp32_z": {
                   "n": len(fp32_check),
                   "ok": all(r["dec_of_fp32_z_bitexact_vs_pack_yT"] for r in fp32_check),
                   "detail": fp32_check,
                   "note": "パックの zT は fp16。fp32 の z を入れれば yT と bit 一致する"
                           "はずで、しなければ decode 経路（g / y_mask / EMA）が違う"},
               "T0_fp16_storage_floor": {
                   "n": len(rows),
                   "bitexact_vs_pack_yT": pos_ok,
                   "snr_db_mean": round(float(np.mean([r["T0_dec_zT_snr_db"]
                                                       for r in rows])), 2),
                   "note": "⚠️ これが T レーン全体のノイズ床。T1 / T2 はこれより下でしか"
                           "議論できない"},
               "T0_negative_control_bitexact_must_be_0": neg_ok},
           "utterances": rows,
           "caveats": [
               "lift は線形。T1 が高ければ『40 次元 c-line で足りる』の証拠になるが、"
               "低くても『c-line が悪い』の証拠にはならない（非対称）",
               "T1 / T2 は教師 decoder を通しているので、うちの Gγ の能力は測っていない",
           ],
           "repro": f"uv run python scripts/e2_teacher_decoder_lanes.py --out {a.out}"}
    (outdir / "ladder.json").write_text(json.dumps(
        {**rep, "utterances": rows, "lanes": list(LANES)}, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != "utterances"},
                     ensure_ascii=False, indent=1))
    print(f"\n→ {outdir}/ladder.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
