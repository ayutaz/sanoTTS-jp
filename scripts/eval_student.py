#!/usr/bin/env python3
"""生徒を教師と同じ文で比較評価する（Phase 6 の本体）。

**同じ held-out 文で教師と生徒の両方を合成し、教師比で報告する**（D-013 / D-020）。
絶対値は日本語で較正されていないので、論文の英語スコアと直接比べない。

出す指標:

* **SCOREQ synthetic/nr**（論文の主指標）と **UTMOS**（併記）
* 音素クラス別 **2–8 kHz スペクトル平坦度**（集約スコアで見えない sibilant 欠陥）。
  ⚠️ **帯域内 RMS と必ず併記する**（M-27）
* 発話長の比（`s_v` が合っているか）

実行:
    uv run --extra eval python scripts/eval_student.py \
        --ckpt runs/v1/stage4.pt --n 24 --out reports/eval_v1
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import warnings

import numpy as np
import soundfile as sf
import torch

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from saanotts_jp import flatness as FL  # noqa: E402
from saanotts_jp.vocab import TOKENS, map_ids  # noqa: E402

SR = 22050
HOP = 256
PAD_SEC = 0.3        # B-5 と同じ。指標が端で不安定になるのを避ける


def int16_roundtrip(pcm: np.ndarray) -> np.ndarray:
    """パックと同じ int16 (scale 32767) 往復を通す。

    ⚠️ **教師と生徒を同じ量子化に揃えるため必須**（M-27）。
    通さないと低エネルギー区間で偽の差が出る（`geminate` の基準値は量子化床）。
    """
    return (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16).astype(np.float32) / 32767


def pad(pcm: np.ndarray) -> np.ndarray:
    n = int(PAD_SEC * SR)
    return np.concatenate([np.zeros(n, np.float32), pcm, np.zeros(n, np.float32)])


def class_spans(ids_student: np.ndarray, frames: np.ndarray):
    """音素クラスごとのフレーム区間。`ceil(dT)` の cumsum で作る（M-27 と同じ規則）。"""
    spans, pos = [], 0
    for i, f in zip(ids_student, frames, strict=True):
        cls = FL.TOK2CLASS.get(TOKENS[int(i)])
        if cls and f > 0:
            spans.append((cls, pos, pos + int(f)))
        pos += int(f)
    return spans


def measure(paths: list[str]) -> dict:
    """UTMOS + SCOREQ synthetic/nr。"""
    import eval_metrics as EM
    out: dict = {}
    try:
        out["utmos"] = EM.measure_utmos(paths)
    except Exception as exc:                       # noqa: BLE001
        out["utmos_error"] = str(exc)
    from saanotts_jp.scoreq_metric import score_files
    scored = score_files(paths, domain="synthetic", mode="nr")
    out["scoreq_synthetic_nr"] = [scored[str(p)] for p in paths]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--texts", default="data/splits/corpus_heldout.tsv")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--out", default="reports/eval_student")
    ap.add_argument("--beta", type=float, default=0.0)
    ap.add_argument("--s-v", type=float, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import gen_teacher_labels as G
    import kana_g2p as K
    import synthesize_student as SS

    device = args.device or (
        "mps" if torch.backends.mps.is_available() else "cpu")
    outdir = pathlib.Path(args.out)
    (outdir / "student").mkdir(parents=True, exist_ok=True)
    (outdir / "teacher").mkdir(parents=True, exist_ok=True)

    # --- 文を選ぶ（B-10 の汚染を除外） ---
    excluded = G.load_exclusions()
    rows = []
    for r in csv.reader(open(args.texts), delimiter="\t"):
        if not r or not r[-1] or r[0] == "source" or (len(r) >= 3 and r[1] in excluded):
            continue
        rows.append((r[1], r[-1]))
    rng = np.random.default_rng(args.seed)
    rows = [rows[i] for i in rng.choice(len(rows), min(args.n, len(rows)), replace=False)]
    print(f"{len(rows)} 文（汚染除外後 / seed {args.seed}）")

    # --- 教師（EMA 適用、prosody=zeros。ラベル生成と同一条件） ---
    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    snap = G.snapshot()
    ckpt = torch.load(snap + G.CKPT, map_location="cpu", weights_only=False)
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]
    teacher = G.build_teacher(ckpt)

    # --- 生徒 ---
    *models, ck = SS.load_student(args.ckpt, device)
    sigma_c = ck.get("c_stats", {}).get("sigma") if isinstance(ck.get("c_stats"), dict) else None
    s_v = args.s_v if args.s_v is not None else SS.S_V
    gen = torch.Generator(device=device).manual_seed(args.seed)

    utts, t_paths, s_paths = [], [], []
    for uid, text in rows:
        ids_t = G.encode_intermediate(K.text_to_intermediate(text, table), pim)
        with torch.no_grad():
            o = teacher.infer(torch.tensor([ids_t]), torch.tensor([len(ids_t)]),
                              lid=torch.tensor([0]), noise_scale=0.0,
                              noise_scale_w=0.0, length_scale=1.0,
                              prosody_features=torch.zeros(1, len(ids_t), 3),
                              speaker_embeddings=None)
        y_t = int16_roundtrip(o.audio.squeeze().numpy())
        d_t = np.ceil(o.durations[0].numpy())

        ids_s = map_ids(ids_t)
        y_s, meta = SS.synthesize(models, ids_s, device, s_v=s_v, beta=args.beta,
                                  sigma_c=sigma_c, generator=gen)
        y_s = int16_roundtrip(y_s)
        d_s = None
        with torch.no_grad():
            r = torch.exp(models[0](torch.from_numpy(ids_s).long()[None].to(device)))
            d_s = torch.clamp(torch.round(s_v * r), 1, 80)[0].cpu().numpy()

        tp, sp = outdir / "teacher" / f"{uid}.wav", outdir / "student" / f"{uid}.wav"
        sf.write(tp, pad(y_t), SR); sf.write(sp, pad(y_s), SR)
        t_paths.append(str(tp)); s_paths.append(str(sp))
        utts.append({"uid": uid, "text": text, "n_ids": len(ids_t),
                     "teacher_frames": int(d_t.sum()), "student_frames": int(d_s.sum()),
                     "teacher_sec": float(len(y_t) / SR),
                     "student_sec": float(len(y_s) / SR),
                     "ids_student": ids_s.tolist(),
                     "d_teacher": d_t.tolist(), "d_student": d_s.tolist()})

    # --- 品質指標 ---
    print("SCOREQ / UTMOS を測定中…")
    m_t, m_s = measure(t_paths), measure(s_paths)

    import eval_metrics as EM
    summary = {}
    for key in ("scoreq_synthetic_nr", "utmos"):
        if key not in m_t or key not in m_s:
            continue
        a, b = np.array(m_s[key]), np.array(m_t[key])
        summary[key] = {
            "student": EM.summarize(a, key), "teacher": EM.summarize(b, key),
            "ratio_student_over_teacher": EM.ratio_ci(a, b),
        }

    # --- 音素クラス別 SFM（教師比）。**帯域内 RMS と必ず併記する**（M-27） ---
    fl = {"student": {}, "teacher": {}}
    for who, wavs, dkey in (("teacher", t_paths, "d_teacher"),
                            ("student", s_paths, "d_student")):
        acc: dict[str, list] = {}
        acc_rms: dict[str, list] = {}
        for u, w in zip(utts, wavs, strict=True):
            pcm, _ = sf.read(w, dtype="float32")
            pcm = pcm[int(PAD_SEC * SR): len(pcm) - int(PAD_SEC * SR)]
            spans = class_spans(np.array(u["ids_student"]), np.array(u[dkey]))
            for tgt, got in ((acc, FL.class_flatness(pcm, SR, spans)),
                             (acc_rms, FL.class_band_rms(pcm, SR, spans))):
                for k, v in got.items():
                    tgt.setdefault(k, []).extend(v)
        for k, v in acc.items():
            arr = np.asarray([x for x in v if np.isfinite(x)])
            r = np.asarray([x for x in acc_rms.get(k, []) if np.isfinite(x)])
            if arr.size:
                fl[who][k] = {
                    "n": int(arr.size), "sfm_mean": float(arr.mean()),
                    "sfm_sd": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
                    "band_rms_median": float(np.median(r)) if r.size else float("nan")}

    flat_ratio = {}
    for k in fl["student"]:
        if k not in fl["teacher"] or not fl["teacher"][k]["sfm_mean"]:
            continue
        t, st = fl["teacher"][k], fl["student"][k]
        flat_ratio[k] = {
            "sfm": round(st["sfm_mean"] / t["sfm_mean"], 4),
            "band_rms": (round(st["band_rms_median"] / t["band_rms_median"], 4)
                         if t["band_rms_median"] else None)}

    len_ratio = float(np.mean([u["student_frames"] / max(u["teacher_frames"], 1)
                               for u in utts]))
    rep = {
        "ckpt": args.ckpt, "stage": ck.get("stage"), "n": len(utts),
        "s_v": s_v, "beta": args.beta, "device": device, "seed": args.seed,
        "quality": summary,
        "flatness_by_class": fl, "flatness_student_over_teacher": flat_ratio,
        "length_ratio_student_over_teacher": round(len_ratio, 4),
        "targets": {"scoreq_synthetic_nr": 1.112, "utmos": 1.107,
                    "note": "論文の英語 embedded の教師比 0.5427 を当てはめた値（D-020）"},
        "warnings": [
            "SCOREQ / UTMOS はいずれも日本語で較正されていない。**絶対値を論文の英語スコアと比べない**",
            "SFM は帯域内 RMS と併記して読む。geminate の基準値は int16 の量子化床（M-27）",
            "教師も生徒も同じ int16 (scale 32767) 往復に通してから測っている",
        ],
        "repro": f"uv run --extra eval python scripts/eval_student.py --ckpt {args.ckpt} --out {args.out}",
        "utterances": utts,
    }
    (outdir / "eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))

    print(f"\n=== 品質（n={len(utts)}）===")
    for k, v in summary.items():
        r = v["ratio_student_over_teacher"]
        tgt = rep["targets"].get(k)
        print(f"  {k:<22} 生徒 {v['student']['mean']:.4f} / 教師 {v['teacher']['mean']:.4f}"
              f"  比 {r['ratio']:.4f} {r['ci95']}"
              + (f"  目標 {tgt}" if tgt else ""))
    print(f"\n=== 発話長 ===\n  生徒/教師 = {len_ratio:.4f}"
          + ("  ⚠️ s_v の再較正が要る" if abs(len_ratio - 1) > 0.05 else "  （±5% 以内）"))
    print("\n=== 音素クラス別 SFM（生徒/教師）⚠️ RMS と対で読む ===")
    print(f"  {'class':<12} {'SFM 生徒':>9} {'SFM 教師':>9} {'比':>7} "
          f"{'RMS 比':>8}  n")
    for k in sorted(flat_ratio):
        t, st, r = fl["teacher"][k], fl["student"][k], flat_ratio[k]
        rr = f"{r['band_rms']:.4f}" if r["band_rms"] is not None else "  n/a"
        flag = ""
        if r["band_rms"] is not None and r["band_rms"] < 0.5:
            flag = "  ⚠️ 音が出ていない（SFM が上がっても改善ではない）"
        print(f"  {k:<12} {st['sfm_mean']:9.4f} {t['sfm_mean']:9.4f} "
              f"{r['sfm']:7.4f} {rr:>8}  {st['n']}{flag}")
    print(f"\n→ {outdir}/eval.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
