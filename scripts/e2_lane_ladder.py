#!/usr/bin/env python3
"""E-2 レーンラダー: 欠陥が decoder 由来か acoustic 由来か duration 由来かを切り分ける。

**教師 ckpt を一度もロードしない。** `data/pack_heldout` の `yT` が教師レーンそのもので、
`zT` / `ceil(dT)` / `ids` も全部入っている（読み取りのみ。D-015）。

レーン（上から順に情報を落としていく）:

| レーン | 中身 | 追加で入る誤差 |
|---|---|---|
| `L0_teacher`   | パックの `yT`                          | — （基準） |
| `L_repr`       | `istft(stft(yT))`                      | 1024/256 表現の天井 |
| `L1_c_s3`      | `Gγ_stage3(Eρ(zT))`                    | c-line + decoder（共適応前） |
| `L1_c_s4`      | `Gγ_stage4(Eρ(zT))`                    | c-line + decoder（共適応後） |
| `L2_oracle_d`  | `Gγ_stage4(Aβ(ids, ceil(dT)))`         | + acoustic |
| `L3_student`   | 現行の推論経路（`d̂` も生徒）           | + duration |

⚠️ `L1` は `Eρ`(14,952) と `Gγ` の**合成**なので、両者を分離しない。
⚠️ 論文 §IV-B の 3.20 / 4.13 は z-line(192ch) の測定で、この `L1` と直接比較しない。

実行:
    uv run python scripts/e2_lane_ladder.py --out reports/e2_ladder
    uv run python scripts/e2_lane_ladder.py --out reports/e2_ladder_n200 --n 200 --seed 7
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

from eval_student import PAD_SEC, class_spans, int16_roundtrip, pad  # noqa: E402
from saanotts_jp import flatness as FL  # noqa: E402
from saanotts_jp._param_reference import Acoustic, Decoder, Duration, Erho  # noqa: E402
from saanotts_jp.labelpack import HOP, SR, PackReader  # noqa: E402
from saanotts_jp.vocab import map_ids  # noqa: E402

LANES = ("L0_teacher", "L_repr", "L1_c_s3", "L1_c_s4", "L2_oracle_d", "L3_student")
#: `L1`/`L2`/`L_repr` は教師とサンプル完全整合（長さ = frames*256）
ALIGNED = ("L_repr", "L1_c_s3", "L1_c_s4", "L2_oracle_d")
S_V = 1.2187          # synthesize_student.S_V と同じ（D-019）
CLIP_LO, CLIP_HI = 1, 80


def sd_hash(sd: dict) -> str:
    m = hashlib.sha256()
    for k in sorted(sd):
        m.update(k.encode())
        m.update(sd[k].detach().cpu().numpy().tobytes())
    return m.hexdigest()


def snr_db(ref: np.ndarray, x: np.ndarray) -> float:
    n = min(len(ref), len(x))
    ref, x = ref[:n].astype(np.float64), x[:n].astype(np.float64)
    d = ref - x
    return float(10 * np.log10(max((ref ** 2).sum(), 1e-30) / max((d ** 2).sum(), 1e-30)))


def log_spec_distance(ref: np.ndarray, x: np.ndarray) -> float:
    """対数スペクトル距離 (dB, RMS)。整合レーンでのみ意味がある。"""
    n = min(len(ref), len(x))
    a = FL.stft_mag(ref[:n], FL.N_FFT, FL.HOP_STFT)
    b = FL.stft_mag(x[:n], FL.N_FFT, FL.HOP_STFT)
    m = min(a.shape[1], b.shape[1])
    la = 20 * np.log10(np.maximum(a[:, :m], 1e-8))
    lb = 20 * np.log10(np.maximum(b[:, :m], 1e-8))
    return float(np.sqrt(np.mean((la - lb) ** 2)))


def load_modules(device):
    """stage3 / stage4 から必要なモジュールを復元する。

    ⚠️ **`load_state_dict` の結果を assert する。** 構築しただけで載せ忘れると
    例外なくランダム初期化の重みが流れ、L1 が壊滅して「331k decoder は使い物に
    ならない」という誤結論が出る（G3）。
    """
    c3 = torch.load("runs/v2/stage3.pt", map_location="cpu", weights_only=False)
    c4 = torch.load("runs/v2/stage4.pt", map_location="cpu", weights_only=False)
    h3, h4 = sd_hash(c3["state"]["erho"]), sd_hash(c4["state"]["erho"])
    if h3 != h4:
        raise SystemExit(f"G3 違反: erho が stage3/stage4 で違う ({h3[:16]} vs {h4[:16]})")

    def build(cls, sd):
        m = cls().to(device).eval()
        m.load_state_dict(sd)
        return m

    mods = {
        "erho": build(Erho, c4["state"]["erho"]),
        "dec_s3": build(Decoder, c3["state"]["decoder"]),
        "dec_s4": build(Decoder, c4["state"]["decoder"]),
        "acoustic": build(Acoustic, c4["state"]["acoustic"]),
        "duration": build(Duration, c4["state"]["duration"]),
    }
    # G3: ロード後の重みがチェックポイントと一致することを確認する
    got = sd_hash({k: v for k, v in mods["erho"].state_dict().items()})
    if got != h4:
        raise SystemExit(f"G3 違反: ロード後の erho が ckpt と違う ({got[:16]} vs {h4[:16]})")
    info = {
        "erho_sha256": h4,
        "decoder_s3_sha256": sd_hash(c3["state"]["decoder"]),
        "decoder_s4_sha256": sd_hash(c4["state"]["decoder"]),
        "acoustic_s4_sha256": sd_hash(c4["state"]["acoustic"]),
        "erho_identical_s3_s4": True,
        "decoder_identical_s3_s4":
            sd_hash(c3["state"]["decoder"]) == sd_hash(c4["state"]["decoder"]),
    }
    return mods, info


def istft_roundtrip(y: np.ndarray) -> np.ndarray:
    """`Decoder.istft` と同一の枠組みで STFT → iSTFT する（表現の天井 / G5）。

    ⚠️ `center=True` の STFT はフレームが T+1 本になるので、そのまま
    `Decoder.istft` に渡すと **256 サンプル長い**波形が返る（G4 が捕まえた）。
    教師の `len(yT) == 256*T` に合わせて切り戻す。
    """
    t = torch.from_numpy(y.astype(np.float32))
    win = torch.hann_window(1024)
    spec = torch.stft(t, n_fft=1024, hop_length=256, win_length=1024, window=win,
                      center=True, return_complex=True)
    mag, ang = spec.abs(), spec.angle()
    out = Decoder.istft(mag[None], torch.cos(ang)[None], torch.sin(ang)[None])[0].numpy()
    return out[: len(y)]


@torch.no_grad()
def render_lanes(mods, item, device, lanes) -> dict[str, np.ndarray]:
    """1 発話ぶんの全レーン波形（int16 往復前）。"""
    zt = torch.from_numpy(item["zT"])[None].to(device)          # [1,192,T]
    ids_s = map_ids(item["ids"])
    x = torch.from_numpy(ids_s).long()[None].to(device)
    d_ceil = torch.from_numpy(np.ceil(item["dT"]).astype(np.int64))[None].to(device)
    out: dict[str, np.ndarray] = {}

    if "L0_teacher" in lanes:
        out["L0_teacher"] = item["yT"].astype(np.float32)
    if "L_repr" in lanes:
        out["L_repr"] = istft_roundtrip(item["yT"])

    c_t = mods["erho"](zt) if ({"L1_c_s3", "L1_c_s4"} & set(lanes)) else None
    for lane, key in (("L1_c_s3", "dec_s3"), ("L1_c_s4", "dec_s4")):
        if lane in lanes:
            out[lane] = Decoder.istft(*mods[key](c_t))[0].cpu().numpy()
    if "L2_oracle_d" in lanes:
        c_h = mods["acoustic"](x, d_ceil)
        out["L2_oracle_d"] = Decoder.istft(*mods["dec_s4"](c_h))[0].cpu().numpy()
    if "L3_student" in lanes:
        r = torch.exp(mods["duration"](x))
        d_hat = torch.clamp(torch.round(S_V * r), CLIP_LO, CLIP_HI).long()
        c_s = mods["acoustic"](x, d_hat)
        out["L3_student"] = Decoder.istft(*mods["dec_s4"](c_s))[0].cpu().numpy()
        out["_d_student"] = d_hat[0].cpu().numpy()
    out["_ids_student"] = ids_s
    out["_d_teacher"] = np.ceil(item["dT"]).astype(np.int64)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--out", default="reports/e2_ladder")
    ap.add_argument("--uids-from", default="reports/eval_v2/eval.json",
                    help="この JSON の utterances[].uid を使う（既定 = eval_v2 と同一 24 文）")
    ap.add_argument("--n", type=int, default=0, help=">0 ならパックから無作為に n 文選ぶ")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--lane", action="append", default=None)
    ap.add_argument("--teacher-ref", default="reports/eval_v2/teacher",
                    help="G1 の照合先。無ければ G1 は skip と記録する")
    ap.add_argument("--student-ref", default="reports/eval_v2/student",
                    help="G1b の照合先。L3 が公表済みの生徒と bit 一致するか")
    a = ap.parse_args()

    lanes = list(a.lane) if a.lane else list(LANES)
    # G2: 同じレーンを 2 回渡したら異常終了する（比較が自分自身になるのを防ぐ）
    if len(set(lanes)) != len(lanes):
        raise SystemExit(f"G2 違反: レーンが重複している {lanes}")
    bad = [x for x in lanes if x not in LANES]
    if bad:
        raise SystemExit(f"未知のレーン {bad}（有効: {list(LANES)}）")

    device = a.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    pack = PackReader(a.pack)
    meta = [json.loads(l) for l in open(pathlib.Path(a.pack) / "index.jsonl")]
    seq_of = {m["uid"]: m["seq"] for m in meta}

    if a.n > 0:
        rng = np.random.default_rng(a.seed)
        pool = [m for m in meta if m["frames"] * HOP > 0]
        sel = rng.choice(len(pool), min(a.n, len(pool)), replace=False)
        uids = [pool[int(i)]["uid"] for i in sel]
    else:
        ev = json.load(open(a.uids_from))
        uids = [u["uid"] for u in ev["utterances"]]
    missing = [u for u in uids if u not in seq_of]
    if missing:
        raise SystemExit(f"パックに無い uid: {missing[:5]}")

    mods, minfo = load_modules(device)
    outdir = pathlib.Path(a.out)
    for lane in lanes:
        (outdir / lane).mkdir(parents=True, exist_ok=True)

    rows, gate_len, gate_pairs = [], [], []
    flat_acc = {lane: {} for lane in lanes}
    flat_rms = {lane: {} for lane in lanes}
    for uid in uids:
        item = pack[seq_of[uid]]
        frames = item["zT"].shape[1]
        w = render_lanes(mods, item, device, lanes)
        ref = int16_roundtrip(w["L0_teacher"]) if "L0_teacher" in w else None

        rec = {"uid": uid, "frames": frames, "n_ids": len(item["ids"]),
               "teacher_samples": len(item["yT"])}
        quant = {}
        ref_f = w["L0_teacher"] if "L0_teacher" in w else None
        for lane in lanes:
            y = int16_roundtrip(w[lane])
            quant[lane] = y
            sf.write(outdir / lane / f"{uid}.wav", pad(y), SR)
            rec[f"{lane}_samples"] = int(len(y))
            # G4: 教師の時間軸を使っているレーンは長さがぴったり一致する
            if lane in ALIGNED:
                gate_len.append(len(y) == frames * HOP == len(item["yT"]))
                # ⚠️ SNR は **int16 往復の前**の float で測る。往復後に測ると
                # 量子化床（実測 68.5 dB）に当たって表現の天井が見えなくなる
                rec[f"{lane}_snr_db"] = (snr_db(ref_f, w[lane])
                                         if ref_f is not None else None)
                rec[f"{lane}_snr_db_int16"] = snr_db(ref, y) if ref is not None else None
                # LSD は **int16 往復後**を主にする（両者を同じ床に置く。M-27 と同じ理由）。
                # float のまま測ると生徒側の極小ビンが log の下駄 1e-8 に当たって膨らむ
                rec[f"{lane}_lsd_db"] = (log_spec_distance(ref, y)
                                         if ref is not None else None)
                rec[f"{lane}_lsd_db_float"] = (log_spec_distance(ref_f, w[lane])
                                               if ref_f is not None else None)
            # 音素クラス別 SFM / 帯域内 RMS（span 規則は M-27 と同一）
            d = w["_d_student"] if lane == "L3_student" else w["_d_teacher"]
            spans = class_spans(w["_ids_student"], d)
            for tgt, got in ((flat_acc[lane], FL.class_flatness(y, SR, spans)),
                             (flat_rms[lane], FL.class_band_rms(y, SR, spans))):
                for k, v in got.items():
                    tgt.setdefault(k, []).extend(v)
        if "L3_student" in lanes:
            rec["L3_frames"] = int(w["_d_student"].sum())
            rec["length_ratio_L3_over_L0"] = rec["L3_frames"] / frames
        # G2: レーンが互いに別物であること
        cand = [x for x in ("L1_c_s3", "L1_c_s4", "L2_oracle_d", "L3_student") if x in lanes]
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                p, q = quant[cand[i]], quant[cand[j]]
                n = min(len(p), len(q))
                gate_pairs.append((cand[i], cand[j],
                                   float(np.abs(p[:n] - q[:n]).max())))
        rows.append(rec)

    # ---- G1: 教師レーンの同一性（陽性対照 + 陰性対照）----
    g1 = {"checked": False}
    tref = pathlib.Path(a.teacher_ref)
    if "L0_teacher" in lanes and tref.exists():
        npad = int(PAD_SEC * SR)
        pos, neg, pos_lsb, neg_lsb = 0, 0, [], []
        for i, uid in enumerate(uids):
            f = tref / f"{uid}.wav"
            if not f.exists():
                continue
            ref_w, _ = sf.read(f, dtype="float32")
            ref_w = ref_w[npad:len(ref_w) - npad]
            mine, _ = sf.read(outdir / "L0_teacher" / f"{uid}.wav", dtype="float32")
            mine = mine[npad:len(mine) - npad]
            if len(ref_w) == len(mine):
                d = int(np.abs(np.round(ref_w * 32767) - np.round(mine * 32767)).max())
                pos_lsb.append(d)
                pos += (d <= 2)
            # 陰性対照: uid を 1 つずらす。**重なり区間で比較する** —
            # 長さ違いだけで落ちる陰性対照は「比較していない」ことを検出できない
            alt = uids[(i + 1) % len(uids)]
            g = tref / f"{alt}.wav"
            if g.exists() and alt != uid:
                o, _ = sf.read(g, dtype="float32")
                o = o[npad:len(o) - npad]
                n = min(len(o), len(mine))
                d2 = int(np.abs(np.round(o[:n] * 32767)
                                - np.round(mine[:n] * 32767)).max())
                neg_lsb.append(d2)
                neg += (d2 <= 2)
        g1 = {"checked": True, "n": len(pos_lsb),
              "positive_within_2lsb": pos, "negative_control_pass_must_be_0": neg,
              "max_abs_lsb_positive": max(pos_lsb) if pos_lsb else None,
              "min_abs_lsb_negative_overlap": min(neg_lsb) if neg_lsb else None,
              "ok": (None if not pos_lsb else (pos == len(pos_lsb) and neg == 0)),
              "skipped": not pos_lsb,
              "note": "⚠️ bit 一致は原理的に不可能。書き出し経路が二重量子化"
                      "（int16_roundtrip の ÷32767 → libsndfile PCM_16 の ×32768）"
                      "で、しかも eval_v2 側は round ではなく truncate。"
                      "本当の同一性検査は scripts/e2_pack_is_teacher.py（bit 一致 24/24）"}

    # ---- G1b: ラダーの下端（L3）が公表済みの生徒 wav と bit 一致するか ----
    g1b = {"checked": False}
    sref = pathlib.Path(a.student_ref)
    if "L3_student" in lanes and sref.exists():
        npad = int(PAD_SEC * SR)
        exact, cmp_n, neg_exact = 0, 0, 0
        for i, uid in enumerate(uids):
            f = sref / f"{uid}.wav"
            if not f.exists():
                continue
            r, _ = sf.read(f, dtype="float32")
            m, _ = sf.read(outdir / "L3_student" / f"{uid}.wav", dtype="float32")
            cmp_n += 1
            exact += int(len(r) == len(m) and np.array_equal(np.round(r * 32767),
                                                             np.round(m * 32767)))
            alt = sref / f"{uids[(i + 1) % len(uids)]}.wav"
            if alt.exists() and uids[(i + 1) % len(uids)] != uid:
                o, _ = sf.read(alt, dtype="float32")
                neg_exact += int(len(o) == len(m)
                                 and np.array_equal(np.round(o * 32767),
                                                    np.round(m * 32767)))
        g1b = {"checked": True, "n": cmp_n, "bitexact": exact,
               "negative_control_bitexact_must_be_0": neg_exact,
               "ok": (None if cmp_n == 0 else (exact == cmp_n and neg_exact == 0)),
               "skipped": cmp_n == 0,
               "note": "ラダーの下端が eval_v2 で公表した生徒そのものであることの錨"}

    summary = {
        "task": "E-2 レーンラダー（render + 整合指標）",
        "pack": a.pack, "n": len(uids), "device": device, "lanes": lanes,
        "seed": a.seed, "uid_source": (f"random n={a.n} seed={a.seed}" if a.n
                                       else a.uids_from),
        "modules": minfo,
        "gates": {
            "G1_teacher_identity": g1,
            "G1b_student_anchor": g1b,
            "G2_lanes_distinct": {
                "n_pairs": len(gate_pairs),
                "min_max_abs_diff": (min(p[2] for p in gate_pairs)
                                     if gate_pairs else None),
                "ok": bool(gate_pairs) and all(p[2] > 0 for p in gate_pairs),
            },
            "G3_erho_frozen": {"ok": True, "erho_sha256": minfo["erho_sha256"][:16],
                               "note": "stage3 と stage4 で一致 / ロード後も一致"},
            "G4_oracle_length": {"n": len(gate_len), "ok": all(gate_len)},
        },
        "utterances": rows,
        "repro": f"uv run python scripts/e2_lane_ladder.py --out {a.out}"
                 + (f" --n {a.n} --seed {a.seed}" if a.n else ""),
    }
    # G5: 表現の天井
    if "L_repr" in lanes:
        s = [r["L_repr_snr_db"] for r in rows if r.get("L_repr_snr_db") is not None]
        summary["gates"]["G5_representation_ceiling"] = {
            "n": len(s), "snr_mean_db": round(float(np.mean(s)), 2),
            "snr_min_db": round(float(np.min(s)), 2), "ok": bool(s) and min(s) >= 100.0,
            "threshold_note": "⚠️ 100 dB は D5 の往復実測 139.0 dB から余裕を取った回帰検出用。"
                              "独立な合格基準ではない"}

    # 整合指標の集計
    agg = {}
    for lane in ALIGNED:
        if lane not in lanes:
            continue
        s = np.array([r[f"{lane}_snr_db"] for r in rows], float)
        si = np.array([r[f"{lane}_snr_db_int16"] for r in rows], float)
        l = np.array([r[f"{lane}_lsd_db"] for r in rows], float)
        lf = np.array([r[f"{lane}_lsd_db_float"] for r in rows], float)
        agg[lane] = {"snr_db_mean": round(float(s.mean()), 3),
                     "snr_db_sd": round(float(s.std(ddof=1)), 3) if len(s) > 1 else None,
                     "snr_db_int16_mean": round(float(si.mean()), 3),
                     "lsd_db_mean": round(float(l.mean()), 3),
                     "lsd_db_sd": round(float(l.std(ddof=1)), 3) if len(l) > 1 else None,
                     "lsd_db_float_mean": round(float(lf.mean()), 3)}
    summary["aligned_metrics_vs_L0"] = agg

    # 音素クラス別 SFM（⚠️ 帯域内 RMS と対で読む。M-27）
    fl = {}
    for lane in lanes:
        fl[lane] = {}
        for k, v in flat_acc[lane].items():
            arr = np.asarray([x for x in v if np.isfinite(x)])
            r = np.asarray([x for x in flat_rms[lane].get(k, []) if np.isfinite(x)])
            if arr.size:
                fl[lane][k] = {"n": int(arr.size), "sfm_mean": round(float(arr.mean()), 4),
                               "band_rms_median": (round(float(np.median(r)), 6)
                                                   if r.size else None)}
    summary["flatness_by_class"] = fl
    if "L0_teacher" in lanes:
        ratio = {}
        for lane in lanes:
            if lane == "L0_teacher":
                continue
            ratio[lane] = {}
            for k, v in fl[lane].items():
                t = fl["L0_teacher"].get(k)
                if not t or not t["sfm_mean"]:
                    continue
                ratio[lane][k] = {
                    "sfm": round(v["sfm_mean"] / t["sfm_mean"], 4),
                    "band_rms": (round(v["band_rms_median"] / t["band_rms_median"], 4)
                                 if t["band_rms_median"] else None)}
        summary["flatness_over_L0"] = ratio

    (outdir / "ladder.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    print(f"=== E-2 ラダー: {len(uids)} 文 / {len(lanes)} レーン / device={device} ===")
    for name, g in summary["gates"].items():
        mark = "SKIP" if g.get("skipped") else ("OK " if g.get("ok") else "NG!")
        print(f"  {name:<28} {mark} {json.dumps({k:v for k,v in g.items() if k!='threshold_note'}, ensure_ascii=False)}")
    print("\n=== 整合指標（L0 = パックの yT に対して）===")
    for lane, v in agg.items():
        print(f"  {lane:<14} SNR {v['snr_db_mean']:8.3f} dB   LSD {v['lsd_db_mean']:7.3f} dB")
    print(f"\n→ {outdir}/ladder.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
