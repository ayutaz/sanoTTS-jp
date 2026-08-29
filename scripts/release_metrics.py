#!/usr/bin/env python3
"""リリースに載せる数値を、**前処理を揃えて**測り直す（C-038）。

## なぜ要るのか — 実際に踏んだ

M-59 に記録した v3 の SCOREQ 教師比 **0.6613** は誤りだった。

* 生徒側 = `reports/student_wav_v3`（`synthesize_student.py` の出力。**パディング無し**）
* 教師側 = `reports/eval_v2/teacher`（`eval_student.py` の出力。**前後 0.3 s パディング**）

を突き合わせていた。SCOREQ は前後の無音に反応するので、**生徒だけが下駄を履いた**。
両方を揃えると **0.6444** になる（生徒 1.3049 → 1.2716）。

⚠️ **例外も警告も出ない。** どちらのディレクトリにも同じ uid の wav が同じ数だけあり、
音として再生でき、教師側の値は過去の記録と完全一致していた。
**「教師の値が一致した」は、生徒側の前処理が揃っていることを何も保証しない。**

## ゲート

| | 内容 | 落ち方 |
|---|---|---|
| **G1** | 比較する全セットが**同じ長さの先頭無音**を持つ | パディングが揃っていないと FAIL |
| **G2** | 教師 wav が 2 つの eval 実行で **bit 一致** | 測定経路が変わっていたら FAIL |
| **G3** | 全セットが**同じ uid 集合**を持つ | 対応のある検定が成立しない |
| **陽性対照** | `student_wav_v3`（無パディング）を混ぜると **G1 が落ちる** | ⚠️ 落ちなければ G1 は空虚 |

実行:
    uv run --extra eval python scripts/release_metrics.py --out reports/release_v0.1.0
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib

import numpy as np
import soundfile as sf

import sys
sys.path.insert(0, "src")

SR = 22050
PAD_SEC = 0.3
#: 先頭無音の長さを測るときの「無音」の定義。int16 の量子化床（1/32767）より上に取る
SILENCE_EPS = 2.0 / 32767


def lead_silence_samples(path: str) -> int:
    """先頭の連続無音サンプル数。**パディングの有無を機械的に見分ける唯一の手段。**"""
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != SR:
        raise SystemExit(f"{path}: sr={sr}（{SR} を期待）")
    nz = np.flatnonzero(np.abs(x[:, 0]) > SILENCE_EPS)
    return int(nz[0]) if len(nz) else len(x)


def paired_diff_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    rng = np.random.default_rng(seed)
    boots = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    return {"diff": round(float(d.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)],
            "n": int(len(d))}


def ratio_ci(num, den, n_boot: int = 20000, seed: int = 0) -> dict:
    num, den = np.asarray(num, float), np.asarray(den, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(num), size=(n_boot, len(num)))
    boots = num[idx].mean(axis=1) / den[idx].mean(axis=1)
    return {"ratio": round(float(num.mean() / den.mean()), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/release_v0.1.0")
    ap.add_argument("--n-boot", type=int, default=20000)
    a = ap.parse_args()

    SETS = {
        "teacher":    "reports/eval_v3_full/teacher",
        "student_v2": "reports/eval_v2/student",
        "student_v3": "reports/eval_v3_full/student",
    }
    #: ⚠️ **陽性対照。** G1 が空虚でないことを示すためだけに測る。比率には使わない
    CONTROL = {"student_v3_unpadded": "reports/student_wav_v3"}

    uids = sorted(pathlib.Path(p).stem for p in glob.glob(SETS["teacher"] + "/*.wav"))
    report: dict = {"n": len(uids), "sets": {k: v for k, v in SETS.items()},
                    "control": CONTROL, "gates": {}}

    # --- G3: uid 集合が一致するか -------------------------------------------------
    for name, d in {**SETS, **CONTROL}.items():
        got = sorted(pathlib.Path(p).stem for p in glob.glob(d + "/*.wav"))
        if got != uids:
            raise SystemExit(f"G3 違反: {name} の uid が違う（{len(got)} 本）")
    report["gates"]["G3_same_uids"] = {"ok": True, "n": len(uids)}

    # --- G1: 先頭無音の長さが揃っているか -----------------------------------------
    expect = int(PAD_SEC * SR)
    lead = {name: [lead_silence_samples(f"{d}/{u}.wav") for u in uids]
            for name, d in {**SETS, **CONTROL}.items()}
    g1 = {}
    for name, v in lead.items():
        mn = int(min(v))
        g1[name] = {"lead_silence_min": mn, "lead_silence_median": int(np.median(v)),
                    "padded": mn >= expect}
    report["gates"]["G1_same_padding"] = {
        "expect_samples": expect, "per_set": g1,
        "ok": all(g1[n]["padded"] for n in SETS),
        "positive_control_fails": not g1["student_v3_unpadded"]["padded"],
        "note": "⚠️ 陽性対照が落ちなければ G1 は空虚。**両方を確認すること**",
    }
    if not report["gates"]["G1_same_padding"]["ok"]:
        raise SystemExit(f"G1 違反: パディングが揃っていない\n{json.dumps(g1, indent=1)}")
    if not report["gates"]["G1_same_padding"]["positive_control_fails"]:
        raise SystemExit("G1 が空虚: 無パディングの対照が落ちなかった")

    # --- G2: 教師 wav が 2 つの eval 実行で bit 一致するか -------------------------
    same = sum(pathlib.Path(f"reports/eval_v3_full/teacher/{u}.wav").read_bytes()
               == pathlib.Path(f"reports/eval_v2/teacher/{u}.wav").read_bytes()
               for u in uids)
    report["gates"]["G2_teacher_bit_identical"] = {"same": same, "n": len(uids),
                                                   "ok": same == len(uids)}
    if same != len(uids):
        raise SystemExit(f"G2 違反: 教師 wav が {len(uids) - same} 本ずれている")

    # --- SCOREQ ---------------------------------------------------------------
    from saanotts_jp.scoreq_metric import score_files
    allsets = {**SETS, **CONTROL}
    paths = {n: [f"{d}/{u}.wav" for u in uids] for n, d in allsets.items()}
    sc = score_files([p for v in paths.values() for p in v],
                     domain="synthetic", mode="nr")
    val = {n: np.array([sc[p] for p in paths[n]], float) for n in allsets}

    t = val["teacher"]
    report["scoreq_synthetic_nr"] = {
        "means": {n: round(float(val[n].mean()), 4) for n in allsets},
        "teacher_ratio": {n: ratio_ci(val[n], t, a.n_boot) for n in
                          ("student_v2", "student_v3", "student_v3_unpadded")},
        "paired_v3_minus_v2": paired_diff_ci(val["student_v3"], val["student_v2"],
                                             a.n_boot),
        "padding_effect_on_v3": paired_diff_ci(val["student_v3_unpadded"],
                                               val["student_v3"], a.n_boot),
    }

    report["caveats"] = [
        "SCOREQ は日本語で較正されていない（D-013 / D-020）。絶対値を英語モデルと比べない",
        "n=24。人による評価は 1 名の少数試行しかしていない（M-60 / C-037）",
        "`student_v3_unpadded` は**陽性対照**であって成果物の数値ではない",
    ]
    report["repro"] = ("uv run --extra eval python scripts/release_metrics.py"
                       f" --out {a.out}")

    outdir = pathlib.Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "scoreq.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))

    print(f"n={len(uids)}  ゲート G1/G2/G3 すべて PASS"
          f"（陽性対照は期待どおり FAIL: 先頭無音 "
          f"{g1['student_v3_unpadded']['lead_silence_min']} < {expect}）\n")
    print(f"  {'set':<22}{'SCOREQ':>9}{'教師比':>10}{'CI95':>20}")
    for n in ("teacher", "student_v2", "student_v3", "student_v3_unpadded"):
        r = report["scoreq_synthetic_nr"]["teacher_ratio"].get(n)
        rs = f"{r['ratio']:.4f}" if r else "—"
        cs = "[%+.4f,%+.4f]" % tuple(r["ci95"]) if r else "—"
        mark = "  ⚠️ 陽性対照" if n.endswith("unpadded") else ""
        print(f"  {n:<22}{val[n].mean():>9.4f}{rs:>10}{cs:>20}{mark}")
    d = report["scoreq_synthetic_nr"]["paired_v3_minus_v2"]
    p = report["scoreq_synthetic_nr"]["padding_effect_on_v3"]
    print(f"\n  v3 − v2（対応あり）      {d['diff']:+.4f} [{d['ci95'][0]:+.4f}, {d['ci95'][1]:+.4f}]")
    print(f"  パディングだけの効果      {p['diff']:+.4f} [{p['ci95'][0]:+.4f}, {p['ci95'][1]:+.4f}]"
          f"  ← これが 0.6613 の正体")
    print(f"\n  → {outdir / 'scoreq.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
