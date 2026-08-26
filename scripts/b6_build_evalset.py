#!/usr/bin/env python3
"""B-6 の前提: 音素クラス別に十分なサンプルを持つ評価セットを作る。

B-6 の反証で分かったのは「摩擦音の実測区間長は 1〜4 フレーム / mean 2.24」で、
**8 パック程度では fricative n=16 / affricate n=7 / 無声化母音 n=3 しか取れない**
ということ。n=3 で「無声化母音 > 破裂音」と言うのは coin flip（P=0.560）だった。

したがって**クラスごとに最低 300 フレームを確保した評価セットを先に作る**。
選択は貪欲法（各ステップで最も不足しているクラスを最も埋める文を採る）。

実行:
    uv run python scripts/b6_build_evalset.py --target 400 \
        --out data/splits/corpus_sibdense.tsv
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import kana_g2p as K  # noqa: E402
import gen_teacher_labels as G  # noqa: E402
from piper_plus_g2p.encode import pua  # noqa: E402

# 音素クラス。**`z` は語頭で破擦音 [dz]、母音間で摩擦音 [z] になるが
# OpenJTalk は区別しない**ので fricative に置く（B-6 の報告に注記を残すこと）。
CLASSES: dict[str, tuple[str, ...]] = {
    "vowel":       ("a", "i", "u", "e", "o"),
    "devoiced":    ("I", "U"),
    "nasal":       ("m", "n", "ny", "my", "N_m", "N_n", "N_ng", "N_uvular"),
    "stop":        ("p", "t", "k", "b", "d", "g", "py", "ty", "ky",
                    "by", "dy", "gy", "kw", "gw"),
    "fricative":   ("s", "sh", "h", "hy", "f", "z", "v"),
    "affricate":   ("ts", "ch", "j"),
    "approximant": ("r", "ry", "w", "y"),
    "geminate":    ("cl",),
}
TOK2CLASS = {t: c for c, ts in CLASSES.items() for t in ts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="heldout")
    ap.add_argument("--target", type=int, default=400,
                    help="クラスごとの最低音素数。1 音素 >= 1 フレームなので下限になる")
    ap.add_argument("--max-sents", type=int, default=400)
    ap.add_argument("--out", default="data/splits/corpus_sibdense.tsv")
    ap.add_argument("--report", default="reports/b6_evalset.json")
    args = ap.parse_args()

    snap = G.snapshot()
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]
    import torch
    num_symbols = torch.load(snap + G.CKPT, map_location="cpu",
                             weights_only=False)["hyper_parameters"]["num_symbols"]
    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    id2tok = {v[0]: pua.CHAR2TOKEN.get(k, k) for k, v in pim.items()
              if v[0] < num_symbols}

    # B-10: 教師の FT テキストと重複する行は評価に使わない（丸暗記を測ることになる）
    excluded = G.load_exclusions()
    cands = []
    n_excluded = 0
    for r in csv.reader(open(f"data/splits/corpus_{args.split}.tsv"), delimiter="\t"):
        if not r or not r[-1] or r[0] == "source":
            continue
        if r[1] in excluded:
            n_excluded += 1
            continue
        try:
            ids = G.encode_intermediate(K.text_to_intermediate(r[-1], table), pim)
            if max(ids) >= num_symbols:
                continue
        except KeyError:
            continue
        if not (40 <= len(ids) <= 300):        # 極端に短い/長い文は避ける
            continue
        c: collections.Counter = collections.Counter()
        for i in ids:
            cl = TOK2CLASS.get(id2tok.get(i, ""))
            if cl:
                c[cl] += 1
        cands.append({"source": r[0], "uid": r[1], "text": r[-1],
                      "n_ids": len(ids), "counts": c})
    print(f"候補 {len(cands):,} 文（B-10 の汚染 {n_excluded} 行を除外）")

    have: collections.Counter = collections.Counter()
    picked: list[dict] = []
    remaining = list(cands)
    while len(picked) < args.max_sents:
        need = {c: max(0, args.target - have[c]) for c in CLASSES}
        if not any(need.values()):
            break
        # 不足量に比例した重みで採点（不足していないクラスは 0 点）
        best, best_score = None, 0.0
        for cand in remaining:
            s = sum(min(cand["counts"][c], need[c]) / args.target for c in CLASSES
                    if need[c])
            s /= cand["n_ids"] ** 0.5          # 短い文を優先し、偏りを避ける
            if s > best_score:
                best, best_score = cand, s
        if best is None:
            break
        picked.append(best)
        have.update(best["counts"])
        remaining.remove(best)

    with open(args.out, "w") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["source", "id", "text"])
        for p in picked:
            w.writerow([p["source"], p["uid"], p["text"]])

    short = [c for c in CLASSES if have[c] < args.target]
    rep = {"split": args.split, "target_per_class": args.target,
           "n_sentences": len(picked), "n_excluded_contaminated": n_excluded, "n_ids": sum(p["n_ids"] for p in picked),
           "phonemes_per_class": dict(have),
           "classes_below_target": short,
           "class_map": {k: list(v) for k, v in CLASSES.items()},
           "note": "1 音素 >= 1 フレーム（ceil(dT)）なので音素数はフレーム数の下限"}
    json.dump(rep, open(args.report, "w"), ensure_ascii=False, indent=2)
    print(f"{len(picked)} 文 / {rep['n_ids']:,} id")
    for c in CLASSES:
        flag = " ⚠️ 不足" if have[c] < args.target else ""
        print(f"  {c:<12} {have[c]:>6}{flag}")
    return 1 if short else 0


if __name__ == "__main__":
    raise SystemExit(main())
