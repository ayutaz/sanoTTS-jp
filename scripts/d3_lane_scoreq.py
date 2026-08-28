#!/usr/bin/env python3
"""fp32 / W8A32 / W8A8 の 3 レーンを SCOREQ で比べる（M-55）。

**なぜ要るか**: int8 経路はこれまで **SNR でしか検証されていなかった**。
「fp32 比 25.88 dB」が可聴かは一度も測られておらず、PIE（整数 SIMD）が要求する
W8A8 は SNR ゲートを**落とす**（平均 23.24 dB / 最小 20.87 dB）。
**SNR で判断すると PIE の作業が理由なく止まる。**

⚠️ **「差が無い」を報告するゲートには陽性対照が要る**（`.claude/skills/writing-gates/`）。
M-50 では DNSMOS の陽性対照 G6 が FAIL していた前例がある。ここでは
**W8A8 と同じ SNR の白色雑音**を fp32 に足し、SCOREQ が確かに下がることを先に示す。

⚠️ **`d̂` は fp32 側に固定して合成されている**（`csrc/dump_pcm.c --ref`）。
これは**音響経路だけ**の比較で、`d̂` の差（W8A32 98.8% / W8A8 97.6%）は入っていない。

    make -C csrc lanes
    uv run --extra eval python scripts/d3_lane_scoreq.py --dir reports/lanes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, "src")

LANES = ("fp32", "w8a32", "w8a8")
#: 陽性対照。W8A8 の実測 SNR に合わせる（M-55）
CTL_SNR_DB = 23.24
#: 陽性対照の合格条件。これを下回るなら「検出器が動いていない」
CTL_MIN_DROP = 0.05


def paired_diff_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert a.shape == b.shape, (a.shape, b.shape)
    d = a - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    return {"diff": round(float(d.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)],
            "n": int(len(d))}


def make_control(src: list[pathlib.Path], out: pathlib.Path, snr_db: float, seed: int):
    """`src` に SNR `snr_db` の白色雑音を足したものを `out` に書く。"""
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    made = []
    for p in src:
        x, sr = sf.read(p, dtype="float32")
        p_sig = float((x.astype(np.float64) ** 2).mean())
        sigma = np.sqrt(p_sig / (10.0 ** (snr_db / 10.0)))
        y = np.clip(x + rng.normal(0, sigma, x.shape).astype(np.float32), -1.0, 1.0)
        sf.write(out / p.name, y, sr)
        made.append(out / p.name)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="reports/lanes")
    ap.add_argument("--out", default=None, help="既定は <dir>/scoreq.json")
    a = ap.parse_args()
    root = pathlib.Path(a.dir)

    files = {L: sorted((root / L).glob("*.wav")) for L in LANES}
    missing = [L for L, v in files.items() if not v]
    if missing:
        raise SystemExit(f"レーンが空: {missing}。先に `make -C csrc lanes` を回すこと")

    gates = []
    n = {L: len(v) for L, v in files.items()}
    gates.append({"id": "G1_same_count", "ok": len(set(n.values())) == 1, "detail": str(n),
                  "why": "本数が違うと対応のある比較が成立しない"})

    # G2 陽性対照（レーンが別物であること）: 同一ファイルを 2 回測ると差 0 が出る
    sha = {L: hashlib.sha256(b"".join(p.read_bytes() for p in v)).hexdigest()[:16]
           for L, v in files.items()}
    gates.append({"id": "G2_lanes_differ", "ok": len(set(sha.values())) == len(LANES),
                  "detail": str(sha),
                  "why": "レーンが同一ファイルだと「差が無い」が自明に出る"})

    # G3: 長さが揃っている（d̂ 固定が効いているか）
    lens = {L: [sf.info(p).frames for p in v] for L, v in files.items()}
    same_len = all(lens[L] == lens["fp32"] for L in LANES)
    gates.append({"id": "G3_same_length", "ok": same_len,
                  "detail": f"総サンプル {[sum(lens[L]) for L in LANES]}",
                  "why": "d̂ を固定していないとフレーム数が変わり、同じ発話の比較にならない"})

    ctl = make_control(files["fp32"], root / "ctl_white", CTL_SNR_DB, seed=7)

    from saanotts_jp.scoreq_metric import score_files
    order = list(LANES) + ["ctl_white"]
    allf = {**files, "ctl_white": ctl}
    paths = [str(p) for L in order for p in allf[L]]
    scored = score_files(paths, domain="synthetic", mode="nr")
    vals = {L: np.array([scored[str(p)] for p in allf[L]], float) for L in order}

    # G4 陽性対照: 同じ SNR の白色雑音で SCOREQ が確かに下がること
    drop = float(vals["fp32"].mean() - vals["ctl_white"].mean())
    gates.append({"id": "G4_positive_control", "ok": drop >= CTL_MIN_DROP,
                  "detail": f"白色雑音 {CTL_SNR_DB} dB で SCOREQ が {drop:.4f} 低下"
                            f"（下限 {CTL_MIN_DROP}）",
                  "why": "この帯域の劣化を検出できないなら「差が無い」は無意味"})

    res = {
        "task": "D-3 レーン比較（fp32 / W8A32 / W8A8）",
        "n": int(len(files["fp32"])),
        "control_snr_db": CTL_SNR_DB,
        "gates": gates,
        "per_lane": {L: {"scoreq_mean": round(float(vals[L].mean()), 4),
                         "scoreq_sd": round(float(vals[L].std(ddof=1)), 4),
                         "vs_fp32": paired_diff_ci(vals[L], vals["fp32"])}
                     for L in order},
        "w8a8_vs_w8a32": paired_diff_ci(vals["w8a8"], vals["w8a32"]),
        "caveats": [
            "d̂ は fp32 側に固定。**音響経路だけ**の比較で、d̂ の差は入っていない",
            "SCOREQ は日本語で較正されていない（D-013 / D-020）。**人は聴いていない**",
            "⚠️ SCOREQ は強雑音域で単調ではない（M-55 §4）。絶対値を雑音量の尺度に使わない",
            "W8A8 が ESP32-S3 で実際に速いかは未測定（ボードが無い）",
        ],
    }
    outp = pathlib.Path(a.out) if a.out else root / "scoreq.json"
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=2))

    bad = [g for g in gates if not g["ok"]]
    for g in gates:
        print(f"  {'OK ' if g['ok'] else 'NG!'} {g['id']:<22} {g['detail']}")
    print()
    print(f"  {'lane':<11}{'SCOREQ':>9}{'vs fp32':>10}{'CI95':>22}")
    for L in order:
        d = res["per_lane"][L]
        c = d["vs_fp32"]
        print(f"  {L:<11}{d['scoreq_mean']:>9.4f}{c['diff']:>+10.4f}"
              f"   [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}]")
    w = res["w8a8_vs_w8a32"]
    print(f"\n  W8A8 − W8A32 = {w['diff']:+.4f} [{w['ci95'][0]:+.4f}, {w['ci95'][1]:+.4f}]"
          f"  n={w['n']}")
    same = w["ci95"][0] <= 0.0 <= w["ci95"][1]
    print(f"  → {'差が無い（CI が 0 を含む）' if same else '差がある'}")
    print(f"\n  → {outp}")
    if bad:
        print(f"\n{len(bad)} 件のゲートが FAIL。**結論を書かないこと**")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
