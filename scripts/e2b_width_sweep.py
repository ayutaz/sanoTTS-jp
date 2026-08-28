#!/usr/bin/env python3
"""E-2b: `Gγ` の幅スイープ — decoder のギャップが**容量律速か学習律速か**を切り分ける。

M-49 で全体ギャップ 0.729 を分解したところ **decoder が最大の 0.395**（`L1_c_s4`）で、
Stage 3 だけの段でも **0.365**（`L0_teacher` 1.9972 → `L1_c_s3` 1.6318）だった。
**なぜ悪いかは分かっていない**:

| 仮説 | 予測 | 打つ手 |
|---|---|---|
| (i) 容量律速 | 幅 `W` を増やすと gap が縮む | decoder を大きくする（**仕様変更**） |
| (ii) 学習律速 | 幅を変えても gap が動かない | step 数 / 損失 / 判別器を触る（サイズ据え置き） |

**(ii) なら幅を減らしても品質が落ちない可能性**があり、decoder は推論時間の
**59.9%**（M-43）なので P-1（速度）に直接効く。**だから狭める側も振る。**

⚠️ **これは調査であって採用ではない。** `W` を変えると 559,008 params と
int8 blob 629 KB が変わる。

## 空虚にしないための設計

**幅を変えた 4 本を比べるだけでは足りない。** Stage 3 は GAN を含むので run 間の
ばらつきがあり、**同じ幅で seed だけ変えた対**（`w76` / `w76b`）を**ノイズ床**として
先に測らないと、「W=96 で 0.02 縮んだ」がノイズか効果か永久に言えない
（`.claude/skills/writing-gates/`）。

実行:
    uv run --extra eval python scripts/e2b_width_sweep.py \
        --run runs/w56 --run runs/w76 --run runs/w76b --run runs/w96 \
        --n 200 --seed 7 --out reports/e2b_width
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from eval_student import int16_roundtrip, pad  # noqa: E402
from saanotts_jp._param_reference import Decoder, Erho  # noqa: E402
from saanotts_jp.labelpack import HOP, SR, PackReader  # noqa: E402

#: M-49 の `L1_c_s3` 段の参照値（`reports/e2_ladder_n200/metrics.json`、n=200 seed 7）
M49_REF = {"L0_teacher": 1.9972, "L1_c_s3": 1.6318, "gap": 0.3654, "W": 76}
EXPANSION = 4


def sd_hash(sd: dict) -> str:
    m = hashlib.sha256()
    for k in sorted(sd):
        m.update(k.encode())
        m.update(sd[k].detach().cpu().numpy().tobytes())
    return m.hexdigest()


def paired_diff_ci(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    """**対応のある**平均差 (a - b) の bootstrap CI。同じ発話の並びであること。"""
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


def load_run(run: pathlib.Path, device):
    """`stage3.pt` から `Eρ` と `Gγ` を復元する。

    ⚠️ **幅は ckpt の自己申告 `decoder_width` ではなく state_dict の実体から読む。**
    自己申告を信じると、記録だけ 96 で中身が 76 のとき黙って通る。
    ⚠️ `load_state_dict` の結果をハッシュで検算する。載せ忘れると乱数初期化のまま
    「幅を増やしたら悪化した」という**逆向きの誤結論**が出る。
    """
    p = run / "stage3.pt"
    if not p.exists():
        raise SystemExit(f"{p} がありません。先に Stage 3 を回してください。")
    ck = torch.load(p, map_location="cpu", weights_only=False)
    sd_dec, sd_erho = ck["state"]["decoder"], ck["state"]["erho"]
    width = int(sd_dec["inp.weight"].shape[0])
    claimed = ck.get("decoder_width")
    if claimed is not None and int(claimed) != width:
        raise SystemExit(f"G0 違反: {run} の自己申告 {claimed} と実体 {width} が違う")

    dec = Decoder(W=width, E=EXPANSION * width).to(device).eval()
    dec.load_state_dict(sd_dec)
    erho = Erho().to(device).eval()
    erho.load_state_dict(sd_erho)
    h_dec, h_erho = sd_hash(sd_dec), sd_hash(sd_erho)
    if sd_hash(dec.state_dict()) != h_dec or sd_hash(erho.state_dict()) != h_erho:
        raise SystemExit(f"G3 違反: {run} のロード後の重みが ckpt と違う")
    return {"name": run.name, "run": str(run), "width": width,
            "params": int(sum(x.numel() for x in dec.parameters())),
            "decoder_sha256": h_dec, "erho_sha256": h_erho,
            "seed": (ck.get("args") or {}).get("seed"),
            "steps": (ck.get("args") or {}).get("steps"),
            "elapsed_sec": ck.get("elapsed_sec"),
            "dec": dec, "erho": erho}


def render(mods, item, device) -> np.ndarray:
    """`L1_c_s3` = `Gγ(Eρ(zT))`。M-49 の `render_lanes` と同じ経路。"""
    zt = torch.from_numpy(item["zT"])[None].to(device)
    with torch.no_grad():
        c = mods["erho"](zt)
        return Decoder.istft(*mods["dec"](c))[0].cpu().numpy()


def noise_groups(runs: list[dict]) -> dict:
    """ノイズ床を測ってよい束 = **幅も steps も同じ**（seed だけ違う）run。

    ⚠️ **幅だけで束ねてはいけない。** steps が違う run を混ぜると、学習量の差が
    「run 間ばらつき」に化けて床が水増しされ、本物の効果が「床以下」と判定される。
    """
    out: dict = {}
    for r in runs:
        out.setdefault((r["width"], r["steps"]), []).append(r)
    return out


def gates(runs: list[dict], n_uids: int, wrote: dict) -> list[dict]:
    """空虚に通らないための検査。**陽性対照と陰性対照を両方張る。**"""
    g = []
    widths = [r["width"] for r in runs]
    shas = [r["decoder_sha256"] for r in runs]

    # G1 陽性対照: 幅が本当に違う run が含まれる（全部同じならスイープになっていない）
    g.append({"id": "G1_widths_differ", "ok": len(set(widths)) > 1,
              "detail": f"幅 {sorted(set(widths))}",
              "why": "全部同じ幅なら「幅を変えても変わらない」は自明で無意味"})

    # G2 陰性対照: **幅も steps も同じで seed だけ違う**対がある（ノイズ床が測れる）
    # ⚠️ 幅だけで束ねると、steps が違う run が床に混ざって**床が水増しされる**。
    # 実際に w76(20k) と w76_40k(40k) を同じ束に入れて床が 0.0417 → 0.1508 に化け、
    # 「40k の改善はノイズ床以下」という**逆の判定**が出た。
    pairs = [(k, [x["name"] for x in v]) for k, v in noise_groups(runs).items()
             if len(v) > 1]
    ok2 = bool(pairs)
    g.append({"id": "G2_noise_floor_pair", "ok": ok2,
              "detail": (f"同幅・同 steps の対 {pairs}" if pairs
                         else "同幅・同 steps の対が無い"),
              "why": "幅と steps を揃え seed だけ変えた対が無いと、run 間ばらつきを分離できない"})

    # G3: どの run も重みが違う（同じ ckpt を 2 回渡していない）
    g.append({"id": "G3_all_distinct", "ok": len(set(shas)) == len(shas),
              "detail": f"{len(set(shas))} 種 / {len(shas)} 本",
              "why": "同一 ckpt を 2 本渡すと差 0 が「ノイズ床 0」に化ける"})

    # G4: 全レーンで同じ発話・同じ本数
    counts = {k: v for k, v in wrote.items()}
    g.append({"id": "G4_same_utterances", "ok": all(c == n_uids for c in counts.values()),
              "detail": f"{counts}（期待 {n_uids}）",
              "why": "本数が違うと対応のある比較が成立しない"})

    # G5: 幅と params が単調に対応している（幅だけ変えたつもりで別物になっていないか）
    pw = sorted({(r["width"], r["params"]) for r in runs})
    ok5 = all(pw[i][1] < pw[i + 1][1] for i in range(len(pw) - 1))
    g.append({"id": "G5_params_monotonic", "ok": ok5, "detail": str(pw),
              "why": "幅だけを動かしたなら params は幅に対して単調に増える"})
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True,
                    help="Stage 3 を回した run ディレクトリ（複数指定）")
    ap.add_argument("--pack", default="data/pack_heldout")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7,
                    help="uid 抽選の seed。**M-49 と同じ 7 が既定**")
    ap.add_argument("--out", default="reports/e2b_width")
    ap.add_argument("--device", default=None)
    ap.add_argument("--baseline", default="w76",
                    help="Δgap の基準にする run 名（既定 = 仕様幅 76）")
    ap.add_argument("--skip-render", action="store_true",
                    help="wav が既にあるなら再合成しない")
    a = ap.parse_args()

    device = a.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    outdir = pathlib.Path(a.out)
    pack = PackReader(a.pack)
    meta = [json.loads(l) for l in open(pathlib.Path(a.pack) / "index.jsonl")]
    seq_of = {m["uid"]: m["seq"] for m in meta}

    # M-49 と同じ抽選規則（n>0・同 seed なら同じ 200 文になる）
    rng = np.random.default_rng(a.seed)
    pool = [m for m in meta if m["frames"] * HOP > 0]
    sel = rng.choice(len(pool), min(a.n, len(pool)), replace=False)
    uids = [pool[int(i)]["uid"] for i in sel]

    runs = [load_run(pathlib.Path(r), device) for r in a.run]
    names = [r["name"] for r in runs]
    if len(set(names)) != len(names):
        raise SystemExit(f"run 名が重複している {names}")
    print(f"n={len(uids)} 発話 / device={device}")
    for r in runs:
        print(f"  {r['name']:<6} W={r['width']:<4} params={r['params']:>8,}  "
              f"seed={r['seed']}  steps={r['steps']}  dec={r['decoder_sha256'][:12]}")

    lanes = ["L0_teacher"] + [r["name"] for r in runs]
    for lane in lanes:
        (outdir / lane).mkdir(parents=True, exist_ok=True)

    wrote = {lane: 0 for lane in lanes}
    t0 = time.time()
    for i, uid in enumerate(uids):
        item = pack[seq_of[uid]]
        tgt = outdir / "L0_teacher" / f"{uid}.wav"
        if not (a.skip_render and tgt.exists()):
            sf.write(tgt, pad(int16_roundtrip(item["yT"].astype(np.float32))), SR)
        wrote["L0_teacher"] += 1
        for r in runs:
            tgt = outdir / r["name"] / f"{uid}.wav"
            if not (a.skip_render and tgt.exists()):
                y = int16_roundtrip(render(r, item, device))
                # 教師の時間軸をそのまま使うので長さは一致するはず
                if len(y) != item["zT"].shape[1] * HOP:
                    raise SystemExit(f"G6 違反: {r['name']}/{uid} の長さが合わない")
                sf.write(tgt, pad(y), SR)
            wrote[r["name"]] += 1
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(uids)} 合成 ({time.time()-t0:.0f}s)")

    # --- SCOREQ ---------------------------------------------------------------
    from saanotts_jp.scoreq_metric import score_files
    paths, index = [], {}
    for lane in lanes:
        index[lane] = [outdir / lane / f"{uid}.wav" for uid in uids]
        paths += [str(p) for p in index[lane]]
    print(f"SCOREQ: {len(paths)} ファイル")
    t0 = time.time()
    scored = score_files(paths, domain="synthetic", mode="nr")
    print(f"  {time.time()-t0:.0f}s")
    vals = {lane: np.array([scored[str(p)] for p in index[lane]], float) for lane in lanes}

    # --- gap と Δgap ----------------------------------------------------------
    ref = vals["L0_teacher"]
    base = a.baseline
    if base not in vals:
        raise SystemExit(f"--baseline {base} が run 名にない（{names}）")

    per_run = {}
    for r in runs:
        v = vals[r["name"]]
        per_run[r["name"]] = {
            "width": r["width"], "params": r["params"], "seed": r["seed"],
            "steps": r["steps"],
            "decoder_sha256": r["decoder_sha256"],
            "scoreq_mean": round(float(v.mean()), 4),
            "scoreq_sd": round(float(v.std(ddof=1)), 4),
            "gap_vs_teacher": paired_diff_ci(ref, v),
            "delta_gap_vs_baseline": paired_diff_ci(vals[base], v),
        }

    # ノイズ床: **幅も steps も同じで seed だけ違う**対の |Δgap|（複数あれば最大）
    floor, floor_pairs = None, []
    for (w, st), grp in noise_groups(runs).items():
        nm = [x["name"] for x in grp]
        for i in range(len(nm)):
            for j in range(i + 1, len(nm)):
                d = paired_diff_ci(vals[nm[i]], vals[nm[j]])
                floor_pairs.append({"pair": [nm[i], nm[j]], "width": w,
                                    "steps": st, **d})
                floor = abs(d["diff"]) if floor is None else max(floor, abs(d["diff"]))

    g = gates(runs, len(uids), wrote)
    # G7: 仕様幅 76 の gap が M-49 の 0.365 から大きく外れていないか（経路の健全性）
    if base in per_run:
        gb = per_run[base]["gap_vs_teacher"]["diff"]
        g.append({"id": "G7_baseline_matches_M49",
                  "ok": abs(gb - M49_REF["gap"]) < 0.08,
                  "detail": f"W=76 の gap {gb:.4f} / M-49 の L1_c_s3 {M49_REF['gap']:.4f}",
                  "why": ("大きく外れたら学習経路か測定経路が M-49 と違う。"
                          "⚠️ しきい 0.08 は run 間ばらつきを見込んだ緩い健全性検査で、"
                          "**合格基準ではない**")})

    verdict = {}
    for name, d in per_run.items():
        if name == base:
            continue
        dg = d["delta_gap_vs_baseline"]
        # Δgap の CI が 0 を跨がず、かつ大きさがノイズ床を超えて初めて「効果」
        sig = (dg["ci95"][0] > 0) or (dg["ci95"][1] < 0)
        beats = floor is None or abs(dg["diff"]) > floor
        verdict[name] = {
            "delta": dg["diff"], "ci95": dg["ci95"],
            "significant_vs_zero": bool(sig),
            "exceeds_noise_floor": bool(beats),
            "call": ("効果あり" if sig and beats else
                     "ノイズ床以下" if sig else "有意差なし"),
        }

    res = {
        "task": "E-2b Gγ 幅スイープ",
        "n": len(uids), "uid_seed": a.seed, "pack": a.pack, "device": device,
        "baseline": base,
        "m49_reference": M49_REF,
        "teacher_scoreq_mean": round(float(ref.mean()), 4),
        "gates": g,
        "noise_floor_abs": None if floor is None else round(floor, 4),
        "noise_floor_pairs": floor_pairs,
        "per_run": per_run,
        "verdict": verdict,
        "caveats": [
            "SCOREQ は日本語で較正されていない。絶対値を論文の英語スコアと比べない（D-013 / D-020）",
            "この gap は `L1_c_s3` 段（Stage 3 のみ）。M-49 の見出し 0.395 は `L1_c_s4`（共適応後）",
            "`Eρ` も Stage 3 で一緒に学習されるので、レーンは Eρ+Gγ の合成。両者を分離しない",
            "幅を変えると 559,008 params と int8 blob 629 KB が変わる。**調査であって採用ではない**",
            "人が聴いていない。SCOREQ の差が可聴かは別問題",
        ],
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "sweep.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))

    # --- 表示 -----------------------------------------------------------------
    print()
    bad = [x for x in g if not x["ok"]]
    for x in g:
        print(f"  {'OK ' if x['ok'] else 'NG!'} {x['id']:<26} {x['detail']}")
    print()
    print(f"  教師 (L0) SCOREQ {ref.mean():.4f}   n={len(uids)}")
    print(f"  {'run':<9}{'W':>5}{'steps':>8}{'params':>10}{'SCOREQ':>9}{'gap':>9}"
          f"{'gap CI95':>20}{'\u0394gap vs '+base:>16}")
    for r in runs:
        d = per_run[r["name"]]
        gg, ci = d["gap_vs_teacher"]["diff"], d["gap_vs_teacher"]["ci95"]
        dg = d["delta_gap_vs_baseline"]["diff"]
        print(f"  {r['name']:<9}{r['width']:>5}{r['steps']:>8,}{r['params']:>10,}"
              f"{d['scoreq_mean']:>9.4f}{gg:>9.4f}"
              f"{f'[{ci[0]:+.4f},{ci[1]:+.4f}]':>20}{dg:>+16.4f}")
    print()
    if floor is not None:
        print(f"  ノイズ床（同幅・別 seed の |Δ|）= {floor:.4f}")
        for p in floor_pairs:
            print(f"    {p['pair'][0]} vs {p['pair'][1]} (W={p['width']}): "
                  f"{p['diff']:+.4f} [{p['ci95'][0]:+.4f}, {p['ci95'][1]:+.4f}]")
    else:
        print("  ⚠️ ノイズ床が測れていない（同幅・別 seed の対が無い）。"
              "**Δgap の解釈はできない**")
    print()
    for name, v in verdict.items():
        print(f"  {name:<7} Δ={v['delta']:+.4f} [{v['ci95'][0]:+.4f}, {v['ci95'][1]:+.4f}]"
              f"  → {v['call']}")
    print(f"\n  → {outdir/'sweep.json'}")
    if bad:
        print(f"\n{len(bad)} 件のゲートが FAIL。**結論を書かないこと**")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
