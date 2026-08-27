# -*- coding: utf-8 -*-
"""B-12: 教師の**事前学習**テキスト (MOE-Speech 20speakers) との重複検査。

B-10 が検査したのは教師の **FT** テキスト (つくよみちゃん = JSUT voiceactress100 /
repeat500) だけで、**事前学習テキストとの重複は未検査**のまま残っていた
(docs/plan/phase0-1-implementation-plan.md:1538)。ここを埋める。

教師 `ayousanz/piper-plus-zero-shot-tsukuyomi` の base は v7 multilingual
(571 話者 / 6 言語 / 497,519 発話)。うち**日本語は MOE-Speech 20speakers の
59,694 発話**（`~/Documents/piper-plus/docs/handoff/zero-shot-tts-handoff-2026-06-20.md`
§5.1）。HF で配布されている metadata.csv は 60,233 行なので、
**配布版は学習に使われた集合の上位集合**（539 行多い = filtered で落ちた分）。
上位集合で照合するので、**重複を取りこぼす方向の誤差は無い**（過大に出る側）。

⚠️ 音声 (wavs.zip 21.8 GB) はダウンロードしない。metadata.csv (6.26 MB) のみ。
⚠️ 本文は JSON に書かない (uid と件数だけ)。MOE-Speech はライセンス上再配布不可。

正規化と近傍の定義は **scripts/b10_overlap.py と同一**:
  norm()  = NFKC + 空白全除去
  canon() = norm() から句読点・記号も除去
  近傍     = canon の文字 5-gram Jaccard / 部分文字列包含 (>= 8 文字を「意味あり」)

再現:
  uv run python scripts/b12_moe_overlap.py
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from b10_overlap import MEANINGFUL_SUB_LEN, canon, jacc, norm, sha256, shingles  # noqa: E402

SPLIT_DIR = os.path.join(ROOT, "data", "splits")
OUT = os.path.join(ROOT, "reports", "b12_moe_overlap.json")
REPO = "ayousanz/moe-speech-20speakers-ljspeech"
SPLITS = ["heldout", "embedded", "sibdense", "train"]
EVAL_JSON = os.path.join(ROOT, "reports", "eval_v2", "eval.json")


def load_split(name: str) -> list[dict]:
    path = os.path.join(SPLIT_DIR, f"corpus_{name}.tsv")
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        assert head == ["source", "id", "text"], (name, head)
        for ln, line in enumerate(f, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            p = line.split("\t")
            assert len(p) == 3, (name, ln, p)
            rows.append({"split": name, "source": p[0], "id": p[1],
                         "norm": norm(p[2]), "canon": canon(p[2])})
    return rows


def load_moe(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line:
                continue
            p = line.split("|", 2)
            assert len(p) == 3, (ln, len(p))
            rows.append({"id": p[0], "spk": p[1],
                         "norm": norm(p[2]), "canon": canon(p[2])})
    return rows


class Index:
    """MOE 側の文字 5-gram 転置インデックス（b10_overlap.Index と同じ作り）。"""

    def __init__(self, rows):
        self.rows = rows
        self.sh = [shingles(r["canon"]) for r in rows]
        self.inv = collections.defaultdict(list)
        for i, s in enumerate(self.sh):
            for g in s:
                self.inv[g].append(i)


def compare(query_rows, idx: Index, min_sub_len: int = 5):
    best_pairs, sub_pairs = [], []
    inv, sh, rows = idx.inv, idx.sh, idx.rows
    for r in query_rows:
        q = shingles(r["canon"])
        if not q:
            continue
        cand = collections.Counter()
        for g in q:
            for i in inv.get(g, ()):
                cand[i] += 1
        nq, cq = len(q), r["canon"]
        best_j, best_i = 0.0, -1
        for i, ov in cand.items():
            j = jacc(q, sh[i], ov)
            if j > best_j:
                best_j, best_i = j, i
            if len(cq) < min_sub_len:
                continue
            t = rows[i]
            if len(t["canon"]) < min_sub_len or t["canon"] == cq:
                continue
            if ov == nq and cq in t["canon"]:
                sub_pairs.append((r, t, "query_in_moe"))
            elif ov == len(sh[i]) and t["canon"] in cq:
                sub_pairs.append((r, t, "moe_in_query"))
        if best_i >= 0:
            best_pairs.append((round(best_j, 4), r, rows[best_i]))
    best_pairs.sort(key=lambda x: -x[0])
    return best_pairs, sub_pairs


def pair_json(j, a, b):
    """⚠️ 本文は入れない。uid のみ。"""
    return {"jaccard5gram": j,
            "a": {"split": a["split"], "source": a["source"], "id": a["id"],
                  "len_canon": len(a["canon"])},
            "moe": {"id": b["id"], "spk": b["spk"], "len_canon": len(b["canon"])}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", default=None,
                    help="MOE metadata.csv。省略時は HF から metadata.csv だけ取得")
    ap.add_argument("--splits", default=",".join(SPLITS))
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    meta = args.metadata
    revision = None
    if meta is None:
        from huggingface_hub import HfApi, hf_hub_download
        revision = HfApi().dataset_info(REPO).sha
        meta = hf_hub_download(REPO, "metadata.csv", repo_type="dataset",
                               revision=revision)
    moe = load_moe(meta)
    splits = [s for s in args.splits.split(",") if s]
    data = {n: load_split(n) for n in splits}

    rep: dict = {
        "task": "B-12 教師の事前学習テキスト (MOE-Speech) との重複検査",
        "repro": "uv run python scripts/b12_moe_overlap.py",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "why": ("B-10 は教師の FT テキストしか見ていない。看板の SCOREQ 教師比 0.611 は "
                "heldout 24 文で測っており、その 24 文が教師の事前学習で見られていたら"
                "汚染になる。"),
        "moe": {
            "repo": REPO, "file": "metadata.csv", "revision": revision,
            # ⚠️ HF キャッシュの絶対パスは OSS 公開の JSON に残さない。同一性は sha256 で担保する
            "local_path": os.path.join("~", os.path.relpath(meta, os.path.expanduser("~"))),
            "sha256": sha256(meta),
            "bytes": os.path.getsize(meta),
            "rows": len(moe),
            "unique_norm": len({r["norm"] for r in moe}),
            "unique_canon": len({r["canon"] for r in moe}),
            "speakers": len({r["spk"] for r in moe}),
            "note": ("配布 metadata は 60,233 行。教師 v7 が実際に使ったのは 59,694 発話"
                     "(handoff §5.1) なので、ここでの照合は**上位集合**に対するもの。"
                     "重複を過小評価する方向の誤差は無い。"),
            "audio_downloaded": False,
        },
        "normalization": {
            "same_as": "scripts/b10_overlap.py の norm() / canon()",
            "norm": "NFKC + 空白全除去",
            "canon": "norm からさらに句読点・括弧・長音記号等を除去",
            "near_dup": f"canon の文字 5-gram Jaccard / 部分文字列包含 (>= {MEANINGFUL_SUB_LEN} 文字)",
        },
        "inputs": {f"corpus_{n}.tsv": {
            "rows": len(data[n]),
            "sha256": sha256(os.path.join(SPLIT_DIR, f"corpus_{n}.tsv"))} for n in splits},
    }

    idx = Index(moe)
    moe_by_norm = collections.defaultdict(list)
    moe_by_canon = collections.defaultdict(list)
    for r in moe:
        moe_by_norm[r["norm"]].append(r)
        moe_by_canon[r["canon"]].append(r)

    res = {}
    for n in splits:
        rows = data[n]
        exact_norm = [(r, moe_by_norm[r["norm"]][0]) for r in rows if r["norm"] in moe_by_norm]
        exact_canon = [(r, moe_by_canon[r["canon"]][0]) for r in rows
                       if r["canon"] in moe_by_canon and r["norm"] not in moe_by_norm]
        best, subs = compare(rows, idx)
        seen, subs_u = set(), []
        for a, b, kind in subs:
            k = (a["id"], b["id"], kind)
            if k in seen:
                continue
            seen.add(k)
            subs_u.append((a, b, kind))
        meaningful = [(a, b, k) for a, b, k in subs_u
                      if min(len(a["canon"]), len(b["canon"])) >= MEANINGFUL_SUB_LEN]
        res[n] = {
            "n_query_rows": len(rows),
            "exact_match_nfkc_nospace": {
                "count": len(exact_norm),
                "query_ids": sorted(a["id"] for a, _ in exact_norm)[:200],
                "pairs": [pair_json(1.0, a, b) for a, b in exact_norm[:50]]},
            "exact_match_after_punct_strip_only": {
                "count": len(exact_canon),
                "query_ids": sorted(a["id"] for a, _ in exact_canon)[:200],
                "pairs": [pair_json(1.0, a, b) for a, b in exact_canon[:50]]},
            "jaccard5gram": {
                "max": best[0][0] if best else None,
                "n_ge_0.90": sum(1 for j, _, _ in best if j >= 0.90),
                "n_ge_0.70": sum(1 for j, _, _ in best if j >= 0.70),
                "n_ge_0.50": sum(1 for j, _, _ in best if j >= 0.50),
                "n_ge_0.30": sum(1 for j, _, _ in best if j >= 0.30),
                "top": [pair_json(j, a, b) for j, a, b in best[:args.top]]},
            "substring_containment": {
                "count": len(subs_u), "count_meaningful": len(meaningful),
                "min_len_chars": 5, "meaningful_min_contained_len": MEANINGFUL_SUB_LEN,
                "meaningful_query_ids": sorted({a["id"] for a, _, _ in meaningful})[:200],
                "pairs": [{"direction": k, "a": {"split": a["split"], "source": a["source"],
                                                 "id": a["id"]},
                           "moe": {"id": b["id"], "spk": b["spk"]},
                           "contained_len": min(len(a["canon"]), len(b["canon"]))}
                          for a, b, k in sorted(
                              meaningful,
                              key=lambda x: -min(len(x[0]["canon"]), len(x[1]["canon"])))[:100]]},
        }
    rep["overlap_vs_moe"] = res

    # ---- 陽性対照: 検出器が本当に効くことを示す ---------------------------
    # 「0 件」は検出器が壊れていても 0 件になる。MOE の行そのものを query に
    # 入れて、exact / Jaccard=1.0 の両方で拾えることを assert する。
    import random  # noqa: PLC0415
    rnd = random.Random(0)
    probe_src = rnd.sample(range(len(moe)), 200)
    probe = [{"split": "_positive_control", "source": "moe", "id": moe[i]["id"],
              "norm": moe[i]["norm"], "canon": moe[i]["canon"]} for i in probe_src]
    pc_exact = sum(1 for r in probe if r["norm"] in moe_by_norm)
    pc_best, _ = compare(probe, idx)
    pc_j1 = sum(1 for j, _, _ in pc_best if j >= 0.9999)
    assert pc_exact == len(probe), (pc_exact, len(probe))
    assert pc_j1 == len([r for r in probe if shingles(r["canon"])]), (pc_j1, len(probe))
    rep["detector_positive_control"] = {
        "what": "MOE の行 200 件をそのまま query に入れて検出できるか",
        "n": len(probe), "n_exact_norm": pc_exact, "n_jaccard_eq_1.0": pc_j1,
        "verdict": "検出器は動いている。よって split 側の 0 件は検出漏れではない。",
    }

    # ---- 看板の 24 文 (SCOREQ 教師比 0.611 を測った文) を個別に確認 -------------
    if os.path.exists(EVAL_JSON):
        ev = json.load(open(EVAL_JSON, encoding="utf-8"))
        uids = [u["uid"] for u in ev["utterances"]]
        ho = {r["id"]: r for r in data.get("heldout", [])}
        qrows = [ho[u] for u in uids if u in ho]
        bests, esubs = compare(qrows, idx)
        best_by_id = {}
        for j, a, b in bests:
            if a["id"] not in best_by_id or j > best_by_id[a["id"]][0]:
                best_by_id[a["id"]] = (j, b)
        sub_by_id = collections.defaultdict(list)
        for a, b, k in esubs:
            n_ = min(len(a["canon"]), len(b["canon"]))
            if n_ >= MEANINGFUL_SUB_LEN:
                sub_by_id[a["id"]].append({"direction": k, "moe_id": b["id"],
                                           "contained_len": n_})
        per = []
        for u in uids:
            r = ho.get(u)
            if r is None:
                per.append({"uid": u, "in_heldout": False})
                continue
            j, b = best_by_id.get(u, (0.0, None))
            per.append({
                "uid": u, "in_heldout": True, "source": r["source"],
                "exact_norm": r["norm"] in moe_by_norm,
                "exact_canon": r["canon"] in moe_by_canon,
                "best_jaccard5gram": j,
                "best_moe_id": b["id"] if b else None,
                "len_canon": len(r["canon"]),
                "n_meaningful_substring": len(sub_by_id.get(u, [])),
                "substring_hits": sub_by_id.get(u, [])[:10],
            })
        rep["eval24"] = {
            "source": os.path.relpath(EVAL_JSON, ROOT),   # ⚠️ 絶対パスを JSON に残さない（OSS 公開）
            "scoreq_ratio_reported": ev["quality"]["scoreq_synthetic_nr"]
                                       ["ratio_student_over_teacher"]["ratio"],
            "n": len(uids),
            "n_exact_norm": sum(1 for p in per if p.get("exact_norm")),
            "n_exact_canon": sum(1 for p in per if p.get("exact_canon")),
            "n_jaccard_ge_0.70": sum(1 for p in per if p.get("best_jaccard5gram", 0) >= 0.70),
            "n_jaccard_ge_0.50": sum(1 for p in per if p.get("best_jaccard5gram", 0) >= 0.50),
            "max_jaccard": max((p.get("best_jaccard5gram", 0) for p in per), default=None),
            "n_with_meaningful_substring": sum(
                1 for p in per if p.get("n_meaningful_substring", 0)),
            "note": ("24 文は scripts/eval_student.py pick_rows() が "
                     "corpus_heldout.tsv から B-10 除外を引いた上で seed 0 で無作為抽出したもの。"),
            "per_utterance": per,
        }

    rep["caveats"] = [
        "照合したのは配布版 metadata (60,233 行)。教師 v7 の実使用は 59,694 発話で、"
        "filtered で落ちた 539 行は配布版に含まれる。上位集合なので取りこぼしは無い。",
        "教師の事前学習は 6 言語 497,519 発話だが、日本語以外 (437,825 発話) は"
        "本文が日本語 split と重なり得ないので照合していない。",
        "5-gram 転置インデックスを候補生成に使うので、包含される側が 5 文字未満のペアは"
        "構造上見つからない (MEANINGFUL_SUB_LEN=8 なので結論には影響しない)。",
        "MOE-Speech の本文は JSON に一切書いていない (ライセンス上再配布不可)。uid のみ。",
    ]
    rep["elapsed_sec"] = round(time.time() - t0, 2)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    print(f"[positive control] {rep['detector_positive_control']['n_exact_norm']}/"
          f"{rep['detector_positive_control']['n']} exact, "
          f"{rep['detector_positive_control']['n_jaccard_eq_1.0']} J=1.0")
    print(f"[moe] rows={len(moe)} unique_norm={rep['moe']['unique_norm']} "
          f"sha256={rep['moe']['sha256'][:16]}…")
    for n in splits:
        b = res[n]
        print(f"[{n} vs MOE] rows={b['n_query_rows']}  "
              f"exact={b['exact_match_nfkc_nospace']['count']}  "
              f"punct-only-exact={b['exact_match_after_punct_strip_only']['count']}  "
              f"maxJ={b['jaccard5gram']['max']}  "
              f"J>=0.9:{b['jaccard5gram']['n_ge_0.90']}  "
              f"J>=0.7:{b['jaccard5gram']['n_ge_0.70']}  "
              f"J>=0.5:{b['jaccard5gram']['n_ge_0.50']}  "
              f"substr(意味あり)={b['substring_containment']['count_meaningful']}")
    if "eval24" in rep:
        e = rep["eval24"]
        print(f"[eval24] n={e['n']}  exact={e['n_exact_norm']}  "
              f"punct-only-exact={e['n_exact_canon']}  "
              f"J>=0.7:{e['n_jaccard_ge_0.70']}  J>=0.5:{e['n_jaccard_ge_0.50']}  "
              f"maxJ={e['max_jaccard']}  "
              f"substr(意味あり)={e['n_with_meaningful_substring']}")
    print(f"-> {args.out}  ({rep['elapsed_sec']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
