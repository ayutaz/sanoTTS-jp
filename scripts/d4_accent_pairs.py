#!/usr/bin/env python3
"""D-4: アクセント型ミニマルペアで生徒のピッチアクセント再現性を測る。

CLAUDE.md の未解決 #5。SCOREQ / UTMOS / CER はどれも**集約指標**なので、
「橋 と 箸 が同じ音になっている」を検出できない。生徒の duration net は音素IDしか
見ないため、ピッチアクセントは音素列に入った記号 `[` `]` `#` だけで運ばれる。
**この記号だけで足りるか**をここで数字にする。

設計と読み方の制限は `src/saanotts_jp/accent.py` の docstring に凍結してある。
要点だけ:

* 群内のメンバーは**記号を除いた音素列が完全に同一**。したがって出力の差は
  すべてアクセント記号に起因する。問うのは**向き**であって差の有無ではない
* 教師自身が弁別していないペアは先に落とす（`accent.TEACHER_GATE_ST`）
* **chance は 0.5 ではない**。キャリアを共有するので順列ヌルを併記する

実行:
    uv run python scripts/d4_accent_pairs.py --build-only          # 評価セットの健全性だけ
    uv run python scripts/d4_accent_pairs.py --ckpt runs/v2/stage4.pt
"""

from __future__ import annotations

import argparse
import csv
import itertools
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

from saanotts_jp import accent as A  # noqa: E402
from saanotts_jp.vocab import map_ids  # noqa: E402

PAIRS_TSV = "data/splits/accent_pairs.tsv"

#: ミニマルペア群。**アクセント型は書かない** — `pyopenjtalk` から取って assert する。
#: 候補から落としたもの（記憶で書くと必ず間違える。実測で落とした）:
#:   雲/蜘蛛・髪/紙・記者/貴社・咲く/柵 = 同じ型で不成立 /
#:   骨/炎 = モーラ数が違う / 汽車/記者・服/吹く = 無声化の有無で音素列が変わる
GROUPS: dict[str, list[str]] = {
    "ame":   ["雨", "飴"],       "hashi": ["箸", "橋", "端"],
    "kami":  ["神", "紙"],       "sake":  ["酒", "鮭"],
    "ima":   ["今", "居間"],     "kaki":  ["牡蠣", "柿", "垣"],
    "hana":  ["花", "鼻"],       "aki":   ["秋", "空き"],
    "ishi":  ["石", "意思"],     "shiro": ["白", "城"],
    "momo":  ["桃", "腿"],       "asa":   ["朝", "麻"],
    "kata":  ["肩", "型"],       "kashi": ["菓子", "貸し"],
    "niji":  ["虹", "二時"],
}

#: ⚠️ **語を文中に置くこと。** 語頭配置（「{}が好きです。」）では文頭モーラの F0 が
#: 取れず、`]` 境界の下降 AUC が**教師自身で 0.4624** まで落ちた（文中なら 0.7049）。
#: どちらも (a) 群内で記号以外の音素列が一致 (b) 無声化 0 (c) `#` 句境界 0 を満たす。
CARRIERS = ["そこに{}がならんでいる。", "ここに{}があるよ。"]

#: ⚠️ この 64 文は**評価専用**。テンプレート文なので蒸留コーパスには入れない
#: （CLAUDE.md「テンプレート文は使わない」）。
#: `data/splits/corpus_{train,heldout}.tsv` との完全一致 0 件をゲートで守る。
CORPORA = ["data/splits/corpus_train.tsv", "data/splits/corpus_heldout.tsv"]


# --------------------------------------------------------------------------
# 評価セットの構築とゲート
# --------------------------------------------------------------------------

def openjtalk_accent(word: str) -> tuple[int, int, str]:
    """`(アクセント型, モーラ数, 読み)`。**手打ちしない。必ずここを通す。**"""
    import pyopenjtalk  # noqa: PLC0415
    fe = pyopenjtalk.run_frontend(word)
    if len(fe) != 1:
        raise SystemExit(f"{word!r} が 1 形態素にならない（{len(fe)} ノード）")
    return int(fe[0]["acc"]), int(fe[0]["mora_size"]), fe[0]["pron"]


def units_of(tokens: list[str], table: dict) -> list[tuple[str, int, bool]]:
    """中間表現トークン → `accent.mora_spans` が食う `(label, 音素数, 記号か)`。"""
    import kana_g2p as K  # noqa: PLC0415
    units = []
    for t in tokens:
        if t in K.MARKS:
            units.append((t, 1, True))
        else:
            # `ん` `ー` は文脈依存なので単独変換では 0〜1 になる。合計を下で assert する
            units.append((t, max(1, len(K.intermediate_to_phonemes([t], table))), False))
    total = sum(n for _, n, _ in units)
    n_all = len(K.intermediate_to_phonemes(tokens, table))
    if total != n_all:
        raise SystemExit(f"トークンの音素数が合わない: {total} != {n_all} / {tokens}")
    return units


def build_rows(table, pim) -> tuple[list[dict], list[dict]]:
    """5 つのゲートを通った群だけを行にする。落ちた群は理由付きで返す。"""
    import kana_g2p as K  # noqa: PLC0415

    corpus = set()
    for path in CORPORA:
        for r in csv.reader(open(path), delimiter="\t"):
            if r and r[0] != "source" and r[-1]:
                corpus.add(r[-1].strip())

    rows, dropped = [], []
    for gid, words in GROUPS.items():
        acc = {w: openjtalk_accent(w) for w in words}
        types = [acc[w][0] for w in words]
        if len(set(types)) != len(types):
            dropped.append({"group": gid, "why": f"アクセント型が重複 {types}"})
            continue
        if len({acc[w][1] for w in words}) != 1:
            dropped.append({"group": gid, "why": "モーラ数が違う"})
            continue
        for ci, carrier in enumerate(CARRIERS):
            cand, why = [], None
            phseqs, mids = [], []
            for w in words:
                text = carrier.format(w)
                toks = K.text_to_intermediate(text, table)
                mid = "".join(toks)
                ph = K.intermediate_to_phonemes(toks, table)
                phseqs.append(tuple(p for p in ph if p not in K.MARKS))
                mids.append(mid)
                cand.append({"group": gid, "word": w, "accent_type": acc[w][0],
                             "mora": acc[w][1], "pron": acc[w][2],
                             "carrier_id": ci, "text": text, "intermediate": mid})
            # (a) 記号を除いた音素列が群内で完全一致
            if len(set(phseqs)) != 1:
                why = "群内で音素列が一致しない（無声化やモーラ数の違い）"
            # (b) 中間表現が全メンバーで相異なる
            elif len(set(mids)) != len(mids):
                why = "中間表現が同一（記号でも弁別できない）"
            # (c) 無声化マークが無い（F0 が原理的に取れないモーラを作らない）
            elif any("°" in m for m in mids):
                why = "無声化モーラを含む"
            # (d) 句境界 `#` が無い（ポーズがコントラストを壊す）
            elif any("#" in m for m in mids):
                why = "句境界 `#` を含む"
            # (e) 蒸留コーパスに完全一致で存在しない
            elif any(c["text"] in corpus for c in cand):
                why = "蒸留コーパスと完全一致"
            # 未知語が無音で消えていないか（B-0）。語の読みが音素列に出ているか
            elif any(len(p) < 2 for p in phseqs):
                why = "音素列が短すぎる（未知語が無音で脱落した可能性）"
            if why:
                dropped.append({"group": gid, "carrier_id": ci, "why": why})
                continue
            rows.extend(cand)
    return rows, dropped


def write_tsv(rows, path: str = PAIRS_TSV) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["group", "word", "accent_type", "mora", "pron",
                    "carrier_id", "text", "intermediate"])
        for r in rows:
            w.writerow([r["group"], r["word"], r["accent_type"], r["mora"], r["pron"],
                        r["carrier_id"], r["text"], r["intermediate"]])


def read_tsv(path: str = PAIRS_TSV) -> list[dict]:
    rows = []
    for r in csv.DictReader(open(path), delimiter="\t"):
        r["accent_type"] = int(r["accent_type"])
        r["mora"] = int(r["mora"])
        r["carrier_id"] = int(r["carrier_id"])
        rows.append(r)
    return rows


# --------------------------------------------------------------------------
# 合成と測定
# --------------------------------------------------------------------------

def synth_all(rows, ckpt_path, device, s_v, beta, outdir) -> list[dict]:
    """教師と生徒を同じ入力経路で合成し、モーラ F0 まで出す。"""
    import gen_teacher_labels as G  # noqa: PLC0415
    import kana_g2p as K  # noqa: PLC0415
    import synthesize_student as SS  # noqa: PLC0415

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    snap = G.snapshot()
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]
    pad_id = pim["_"][0]

    print("教師 ckpt を読み込み中…")
    ckpt = torch.load(snap + G.CKPT, map_location="cpu", weights_only=False)
    teacher = G.build_teacher(ckpt)              # EMA は build_teacher が assert する
    *models, sck = SS.load_student(ckpt_path, device)
    sigma_c = sck.get("c_stats", {}).get("sigma") if isinstance(sck.get("c_stats"), dict) else None
    gen = torch.Generator(device=device).manual_seed(0)

    for sub in ("teacher", "student"):
        (outdir / sub).mkdir(parents=True, exist_ok=True)

    out = []
    for i, r in enumerate(rows):
        toks = K.text_to_intermediate(r["text"], table)
        assert "".join(toks) == r["intermediate"], (r["text"], "".join(toks))
        ids_t = G.encode_intermediate(toks, pim)
        assert max(ids_t) < 173, max(ids_t)
        units = units_of(toks, table)

        with torch.no_grad():
            o = teacher.infer(torch.tensor([ids_t]), torch.tensor([len(ids_t)]),
                              lid=torch.tensor([0]), noise_scale=0.0,
                              noise_scale_w=0.0, length_scale=1.0,
                              prosody_features=torch.zeros(1, len(ids_t), 3),
                              speaker_embeddings=None)
        y_t = o.audio.squeeze().numpy()
        d_t = np.ceil(o.durations[0].numpy()).astype(int)
        assert o.latents[0].shape[1] == 192 and len(d_t) == len(ids_t)
        assert o.latents[0].shape[-1] * A.HOP == y_t.shape[-1]

        ids_s = map_ids(ids_t)
        y_s, _ = SS.synthesize(models, ids_s, device, s_v=s_v, beta=beta,
                               sigma_c=sigma_c, generator=gen)
        with torch.no_grad():
            rr = torch.exp(models[0](torch.from_numpy(ids_s).long()[None].to(device)))
            d_s = torch.clamp(torch.round(s_v * rr), 1, 80)[0].cpu().numpy().astype(int)

        sp_t = A.mora_spans(units, ids_t, pad_id, d_t)
        sp_s = A.mora_spans(units, ids_t, pad_id, d_s)
        assert [s[0] for s in sp_t] == [s[0] for s in sp_s]

        tag = f"{r['group']}_c{r['carrier_id']}_{r['word']}"
        sf.write(outdir / "teacher" / f"{tag}.wav", y_t, A.SR)
        sf.write(outdir / "student" / f"{tag}.wav", y_s, A.SR)

        rec = dict(r)
        rec.update({
            "tag": tag, "n_ids": len(ids_t),
            "morae": [s[0] for s in sp_t],
            "frames_teacher": int(d_t.sum()), "frames_student": int(d_s.sum()),
            "boundary_marks": ["".join(sorted(s)) for s in A.boundary_marks(units)],
            "f0_teacher": A.mora_f0(y_t, sp_t).tolist(),
            "f0_student": A.mora_f0(y_s, sp_s).tolist(),
        })
        out.append(rec)
        print(f"  [{i + 1:2d}/{len(rows)}] {tag}")
    return out


def build_pairs(recs) -> list[dict]:
    """群 × キャリアごとに全 2 組を作り、コントラストを測る。"""
    pairs = []
    by_key: dict[tuple, list] = {}
    for r in recs:
        by_key.setdefault((r["group"], r["carrier_id"]), []).append(r)
    for (gid, ci), items in by_key.items():
        st_t = [A.semitone(it["f0_teacher"]) for it in items]
        st_s = [A.semitone(it["f0_student"]) for it in items]
        # 群内の全メンバーで教師・生徒とも F0 が取れたモーラだけを使う
        mask = np.all([np.isfinite(t) & np.isfinite(s) for t, s in zip(st_t, st_s)], axis=0)
        for (ia, a), (ib, b) in itertools.combinations(list(enumerate(items)), 2):
            c = A.contrast(st_t[ia], st_s[ia], st_t[ib], st_s[ib], mask)
            # 2 メンバーで記号が違う境界に接するモーラが mask に残っているか。
            # ⚠️ 落ちていると、測っているのはキャリアだけでアクセントを見ていない。
            diff_b = [i for i, (x, y) in enumerate(zip(a["boundary_marks"],
                                                       b["boundary_marks"])) if x != y]
            need = sorted({i for j in diff_b for i in (j, j + 1)})
            c.update({"group": gid, "carrier_id": ci,
                      "pair": f"{a['word']}/{b['word']}",
                      "type_pair": tuple(sorted((a["accent_type"], b["accent_type"]))),
                      "delta_n_ids": abs(a["n_ids"] - b["n_ids"]),
                      "differing_boundaries": diff_b,
                      "accent_morae_in_mask": bool(all(mask[i] for i in need)),
                      "teacher_gate": c["norm_teacher_st"] >= A.TEACHER_GATE_ST})
            pairs.append(c)
    return pairs


def identification(recs) -> dict:
    """群内の同定。生徒の輪郭を教師のテンプレートに**最適割り当て**で当てる。

    2 メンバーでは符号一致と同値（`accent.contrast` の docstring 参照）だが、
    3 メンバー（箸/橋/端・牡蠣/柿/垣）では chance が **1/6** になるので情報が増える。
    """
    by_key: dict[tuple, list] = {}
    for r in recs:
        by_key.setdefault((r["group"], r["carrier_id"]), []).append(r)
    out = {"by_size": {}, "detail": []}
    for (gid, ci), items in sorted(by_key.items()):
        k = len(items)
        st_t = [A.semitone(i["f0_teacher"]) for i in items]
        st_s = [A.semitone(i["f0_student"]) for i in items]
        mask = np.all([np.isfinite(t) & np.isfinite(s) for t, s in zip(st_t, st_s)], axis=0)
        ct = [t[mask] - t[mask].mean() for t in st_t]
        cs = [s[mask] - s[mask].mean() for s in st_s]
        best = min(itertools.permutations(range(k)),
                   key=lambda p: sum(float(np.sum((cs[i] - ct[p[i]]) ** 2)) for i in range(k)))
        ok = best == tuple(range(k))
        b = out["by_size"].setdefault(str(k), {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += int(ok)
        out["detail"].append({"group": gid, "carrier_id": ci, "k": k,
                              "words": [i["word"] for i in items], "correct": ok,
                              "assignment": list(best)})
    import math
    for k, b in out["by_size"].items():
        b["chance"] = round(1.0 / math.factorial(int(k)), 4)
        b["accuracy"] = round(b["correct"] / b["n"], 4)
    return out


def boundary_auc(recs) -> dict:
    """`]` 下降核 / `[` 上昇が F0 の下降・上昇として出ているか（モーラ境界の AUC）。

    ⚠️ piper-plus は 1 型で `]` と `[` を同じ境界に出すので、
    **`]` 単独と `][` を分けて集計する**。
    """
    out = {}
    for who, key in (("teacher", "f0_teacher"), ("student", "f0_student")):
        buckets: dict[str, list] = {"fall_bracket_only": [], "fall_both": [],
                                    "fall_none": [], "rise_any": [], "rise_none": []}
        for r in recs:
            st = A.semitone(r[key])
            lab = r["boundary_marks"]
            assert len(lab) == len(st) - 1, (len(lab), len(st))
            d = st[:-1] - st[1:]                       # 正 = 下がった
            for i, L in enumerate(lab):
                if not np.isfinite(d[i]):
                    continue
                if "]" in L and "[" in L:
                    buckets["fall_both"].append(d[i])
                elif "]" in L:
                    buckets["fall_bracket_only"].append(d[i])
                else:
                    buckets["fall_none"].append(d[i])
                (buckets["rise_any"] if "[" in L else buckets["rise_none"]).append(-d[i])
        neg = buckets["fall_none"]
        allfall = buckets["fall_bracket_only"] + buckets["fall_both"]
        out[who] = {
            "fall_auc": A.auc(allfall, neg),
            "fall_auc_bracket_only": A.auc(buckets["fall_bracket_only"], neg),
            "fall_auc_both_marks": A.auc(buckets["fall_both"], neg),
            "rise_auc": A.auc(buckets["rise_any"], buckets["rise_none"]),
            "n": {k: len(v) for k, v in buckets.items()},
            "fall_mean_st": float(np.mean(allfall)) if allfall else float("nan"),
            "nofall_mean_st": float(np.mean(neg)) if neg else float("nan"),
        }
    return out


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="runs/v2/stage4.pt")
    ap.add_argument("--out", default="reports/d4_accent")
    ap.add_argument("--build-only", action="store_true",
                    help="評価セットの構築とゲートだけ。教師も生徒も呼ばない")
    ap.add_argument("--device", default="cpu",
                    help="⚠️ CPU と MPS は bit 一致しない（M-21）。既定は CPU")
    ap.add_argument("--s-v", type=float, default=None)
    ap.add_argument("--beta", type=float, default=0.0)
    args = ap.parse_args()

    import gen_teacher_labels as G
    import kana_g2p as K
    import synthesize_student as SS

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
    s_v = args.s_v if args.s_v is not None else SS.S_V

    rows, dropped = build_rows(table, pim)
    write_tsv(rows)
    n_groups = len({r["group"] for r in rows})
    n_words = len({(r["group"], r["word"]) for r in rows})
    n_pairs = sum(len(list(itertools.combinations(
        [r for r in rows if r["group"] == g and r["carrier_id"] == c], 2)))
        for g in {r["group"] for r in rows} for c in range(len(CARRIERS)))
    print(f"評価セット: {n_groups} 群 / {n_words} 語 / {len(rows)} 文 / {n_pairs} ペア"
          f"  → {PAIRS_TSV}")
    for d in dropped:
        print(f"  落とした: {d}")
    for r in rows[:3]:
        print(f"    例 {r['word']}({r['accent_type']}型) {r['text']} → {r['intermediate']}")
    if args.build_only:
        return 0

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    recs = synth_all(rows, args.ckpt, args.device, s_v, args.beta, outdir)

    cov_t = float(np.mean([np.isfinite(r["f0_teacher"]).mean() for r in recs]))
    cov_s = float(np.mean([np.isfinite(r["f0_student"]).mean() for r in recs]))
    pairs = build_pairs(recs)
    usable = [p for p in pairs if p["teacher_gate"]]
    failed_gate = [{"group": p["group"], "carrier_id": p["carrier_id"], "pair": p["pair"],
                    "norm_teacher_st": round(p["norm_teacher_st"], 3)}
                   for p in pairs if not p["teacher_gate"]]

    def frac_pos(ps):
        return float(np.mean([p["cos"] > 0 for p in ps])) if ps else float("nan")

    def mean_cos(ps):
        return float(np.mean([p["cos"] for p in ps])) if ps else float("nan")

    def mag_ratio(ps):
        return (float(np.mean([p["norm_student_st"] / p["norm_teacher_st"] for p in ps]))
                if ps else float("nan"))

    # モーラ F0 の欠測。⚠️ アクセント記号が違う境界に接するモーラが落ちると
    # 「キャリアだけ比べる」ことになり、そのペアの cos は解釈できない
    missing = [{"tag": r["tag"], "mora_index": i, "mora": m,
                "teacher_ok": bool(np.isfinite(r["f0_teacher"][i])),
                "student_ok": bool(np.isfinite(r["f0_student"][i]))}
               for r in recs for i, m in enumerate(r["morae"])
               if not (np.isfinite(r["f0_teacher"][i]) and np.isfinite(r["f0_student"][i]))]
    unevaluable = [{"group": p["group"], "carrier_id": p["carrier_id"], "pair": p["pair"],
                    "cos": round(p["cos"], 4)}
                   for p in usable if not p["accent_morae_in_mask"]]
    clean = [p for p in usable if p["accent_morae_in_mask"]]

    null = A.empirical_null(usable)
    null_x = A.empirical_null(usable, cross_type_only=True)
    nolen = [p for p in usable if p["delta_n_ids"] == 0]
    auc_out = boundary_auc(recs)

    bytype: dict[str, dict] = {}
    for p in pairs:
        k = f"{p['type_pair'][0]}型 vs {p['type_pair'][1]}型"
        b = bytype.setdefault(k, {"n": 0, "teacher_norm": [], "cos": []})
        b["n"] += 1
        b["teacher_norm"].append(p["norm_teacher_st"])
        if p["teacher_gate"]:
            b["cos"].append(p["cos"])
    for k, b in bytype.items():
        b["teacher_norm_mean"] = round(float(np.mean(b["teacher_norm"])), 3)
        b["teacher_norm_min"] = round(float(min(b["teacher_norm"])), 3)
        b["n_gated"] = len(b["cos"])
        b["cos_mean"] = round(float(np.mean(b["cos"])), 4) if b["cos"] else None
        b["frac_cos_positive"] = (round(float(np.mean(np.asarray(b["cos"]) > 0)), 4)
                                  if b["cos"] else None)
        del b["teacher_norm"], b["cos"]

    rep = {
        "task": "D-4 アクセント型ミニマルペア",
        "ckpt": args.ckpt, "stage": None, "device": args.device,
        "s_v": s_v, "beta": args.beta,
        "config": {"fmin": A.FMIN, "fmax": A.FMAX, "frame_length": A.FRAME_LENGTH,
                   "hop": A.HOP, "teacher_gate_st": A.TEACHER_GATE_ST,
                   "carriers": CARRIERS},
        "evalset": {"n_groups": n_groups, "n_words": n_words, "n_sentences": len(rows),
                    "n_pairs": len(pairs), "dropped": dropped,
                    "corpus_exact_match": 0},
        "mora_f0_coverage": {"teacher": round(cov_t, 4), "student": round(cov_s, 4)},
        "teacher_gate": {"threshold_st": A.TEACHER_GATE_ST,
                         "passed": len(usable), "total": len(pairs),
                         "failed_pairs": failed_gate},
        "primary": {
            "sign_agreement": A.cluster_bootstrap(usable, frac_pos),
            "cos_mean": A.cluster_bootstrap(usable, mean_cos),
            "magnitude_ratio_student_over_teacher": A.cluster_bootstrap(usable, mag_ratio),
            "n_correct": int(sum(p["cos"] > 0 for p in usable)), "n": len(usable),
            "magnitude_ratio_median": round(float(np.median(
                [p["norm_student_st"] / p["norm_teacher_st"] for p in usable])), 4),
            "magnitude_ratio_range": [
                round(min(p["norm_student_st"] / p["norm_teacher_st"] for p in usable), 3),
                round(max(p["norm_student_st"] / p["norm_teacher_st"] for p in usable), 3)],
        },
        "gate_sensitivity": [
            {"gate_st": g, "n": len([p for p in pairs if p["norm_teacher_st"] >= g]),
             "n_correct": int(sum(p["cos"] > 0 for p in pairs
                                  if p["norm_teacher_st"] >= g)),
             "cos_mean": round(mean_cos([p for p in pairs
                                         if p["norm_teacher_st"] >= g]), 4)}
            for g in (0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)],
        "identification": identification(recs),
        "mora_f0_missing": {
            "n_missing": len(missing), "n_total": sum(len(r["morae"]) for r in recs),
            "detail": missing,
            "unevaluable_pairs": unevaluable,
            "note": "アクセント記号が違う境界に接するモーラが落ちたペアは、"
                    "キャリアだけを比べている。**主指標からは外さない**（後付けの除外になるため）。"
                    "参考として下の clean_subset を見ること",
        },
        "clean_subset": {
            "n": len(clean), "n_correct": int(sum(p["cos"] > 0 for p in clean)),
            "sign_agreement": frac_pos(clean), "cos_mean": mean_cos(clean),
            "note": "⚠️ 後付けの部分集合。主指標の代わりに使わない",
        },
        "no_length_confound_subset": {
            "n": len(nolen), "n_correct": int(sum(p["cos"] > 0 for p in nolen)),
            "sign_agreement": frac_pos(nolen), "cos_mean": mean_cos(nolen),
            "note": "|Δn_ids| == 0。トークン数が同一なので持続長では説明できない",
        },
        "empirical_null": {"same_carrier_other_group": null,
                           "cross_accent_type": null_x,
                           "note": "⚠️ chance は 0.5 ではない。キャリアを共有するため"},
        "boundary_auc": auc_out,
        "by_type_pair": bytype,
        "rejected_metric": {
            "peak_position_argmax": "採用しない。高原状の輪郭で argmax が跳ぶ",
        },
        "warnings": [
            "群内メンバーは記号以外の音素列が同一なので、A と B の音が違うこと自体は"
            "アクセント再現の証拠にならない。判定は必ず cos の符号で行う",
            "chance は 0.5 ではない。empirical_null と比べること",
            "教師が弁別していないペア（teacher_gate.failed_pairs）は生徒の失点にしない",
            "CPU で測定。MPS とは bit 一致しない（M-21）",
        ],
        "repro": (f"uv run python scripts/d4_accent_pairs.py --ckpt {args.ckpt} "
                  f"--out {args.out}"),
        "pairs": pairs, "utterances": recs,
    }
    (outdir / "d4_accent.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))

    # --- 表示 ---
    print(f"\n=== モーラ F0 取得率 ===  教師 {cov_t:.3f} / 生徒 {cov_s:.3f}")
    print(f"\n=== 教師ゲート |Δ_T| >= {A.TEACHER_GATE_ST} st ===  "
          f"{len(usable)}/{len(pairs)} 通過")
    for f in failed_gate:
        print(f"  落ちた: {f['group']}_c{f['carrier_id']} {f['pair']} "
              f"|Δ_T|={f['norm_teacher_st']}")
    print(f"\n{'group':<8} {'pair':<10} {'m':>2} {'|Δ_T|':>6} {'|Δ_S|':>6} {'cos':>6}  判定")
    for p in sorted(pairs, key=lambda x: -x["norm_teacher_st"]):
        v = ("教師が弁別せず(除外)" if not p["teacher_gate"]
             else ("OK" if p["cos"] > 0 else "**逆向き**"))
        print(f"{p['group']}_c{p['carrier_id']:<5} {p['pair']:<10} {p['n_morae']:2d} "
              f"{p['norm_teacher_st']:6.2f} {p['norm_student_st']:6.2f} {p['cos']:+6.2f}  {v}")

    pr = rep["primary"]
    print(f"\n=== 主指標（教師ゲート通過 {len(usable)} ペア）===")
    print(f"  符号一致  {pr['n_correct']}/{pr['n']} = {pr['sign_agreement']['point']:.3f}  "
          f"CI95 {[round(x, 3) for x in pr['sign_agreement']['ci95']]}"
          f"   ヌル {null['frac_cos_positive']:.3f}")
    print(f"  cos 平均  {pr['cos_mean']['point']:.3f}  "
          f"CI95 {[round(x, 3) for x in pr['cos_mean']['ci95']]}"
          f"   ヌル {null['cos_mean']:+.3f}")
    mr = pr["magnitude_ratio_student_over_teacher"]
    print(f"  |Δ_S|/|Δ_T| 平均 {mr['point']:.3f}  CI95 {[round(x, 3) for x in mr['ci95']]}"
          f"  中央値 {pr['magnitude_ratio_median']:.3f}  範囲 {pr['magnitude_ratio_range']}"
          + ("   （CI が 1.0 を含む = 誇張とは言えない）"
             if mr["ci95"][0] <= 1.0 <= mr["ci95"][1] else ""))
    nl = rep["no_length_confound_subset"]
    print(f"  長さ交絡なし部分集合  {nl['n_correct']}/{nl['n']} "
          f"cos {nl['cos_mean']:.3f}")
    print(f"  厳しめヌル（型の組合せが違うもの n={null_x['n']}）: "
          f"cos>0 {null_x['frac_cos_positive']:.3f} / cos 平均 {null_x['cos_mean']:+.3f}")

    print("\n=== 境界 AUC（⚠️ `]` 単独と `][` を分ける）===")
    for who in ("teacher", "student"):
        o = auc_out[who]
        print(f"  {who:<8} 下降 all {o['fall_auc']:.4f} / `]`単独 "
              f"{o['fall_auc_bracket_only']:.4f} (n={o['n']['fall_bracket_only']}) / "
              f"`][` {o['fall_auc_both_marks']:.4f} (n={o['n']['fall_both']})  "
              f"上昇 {o['rise_auc']:.4f}")
    print(f"  比（生徒/教師）下降 AUC "
          f"{auc_out['student']['fall_auc'] / auc_out['teacher']['fall_auc']:.4f}  "
          f"下降深さ {auc_out['student']['fall_mean_st'] / auc_out['teacher']['fall_mean_st']:.4f}")

    print("\n=== 群内の同定（生徒の輪郭を教師テンプレートに割り当て）===")
    for k, b in sorted(rep["identification"]["by_size"].items()):
        print(f"  {k} メンバー群: {b['correct']}/{b['n']} = {b['accuracy']:.3f}  "
              f"(chance {b['chance']})")

    print("\n=== 教師ゲートしきい値への感度 ===")
    for g in rep["gate_sensitivity"]:
        print(f"  |Δ_T| >= {g['gate_st']:>4.1f} st : {g['n_correct']:2d}/{g['n']:2d} = "
              f"{g['n_correct'] / g['n']:.3f}  cos 平均 {g['cos_mean']:+.3f}")

    mm = rep["mora_f0_missing"]
    print(f"\n=== モーラ F0 の欠測 {mm['n_missing']}/{mm['n_total']} ===")
    for u in mm["unevaluable_pairs"]:
        print(f"  ⚠️ 記号が違う境界のモーラが落ちた: {u['group']}_c{u['carrier_id']} "
              f"{u['pair']} cos={u['cos']:+.3f} → このペアの cos は解釈できない")
    cs = rep["clean_subset"]
    print(f"  参考（後付け部分集合）: {cs['n_correct']}/{cs['n']} "
          f"cos 平均 {cs['cos_mean']:.3f}")

    print("\n=== アクセント型の組合せ別 ===")
    for k, b in sorted(bytype.items()):
        print(f"  {k}  n={b['n']:2d} 教師|Δ| 平均 {b['teacher_norm_mean']:.2f} "
              f"min {b['teacher_norm_min']:.2f}  ゲート後 n={b['n_gated']} "
              f"cos {b['cos_mean']}")
    print(f"\n→ {outdir}/d4_accent.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
