#!/usr/bin/env python3
"""B-9: 中間表現経路で実際に出現する音素を数え、デプロイ語彙を凍結する。

**旧測定は A-1 以前の経路（`MultilingualPhonemizer`）だったので測り直す。**
生徒の埋め込み表サイズ（論文の deployed vocabulary = 157 entries）と
「音素カバレッジ 100%」の主張がここに乗る。

実行:
    uv run python scripts/b9_phoneme_inventory.py --out reports/b9_vocab.json
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/b9_vocab.json")
    args = ap.parse_args()

    snap = G.snapshot()
    cfg = json.load(open(snap + "config.json"))
    pim = cfg["phoneme_id_map"]
    import torch
    num_symbols = torch.load(snap + G.CKPT, map_location="cpu",
                             weights_only=False)["hyper_parameters"]["num_symbols"]

    table = K.build_mora_table()
    G.ENCODE_TABLE = table

    id2tok = {}
    for ch, v in pim.items():
        if v[0] < num_symbols:
            id2tok[v[0]] = pua.CHAR2TOKEN.get(ch, ch)

    counts: collections.Counter = collections.Counter()
    per_split: dict[str, collections.Counter] = {}
    n_rows = n_ok = 0
    rejected: list[dict] = []
    for sp in ("train", "heldout", "embedded"):
        c: collections.Counter = collections.Counter()
        for r in csv.reader(open(f"data/splits/corpus_{sp}.tsv"), delimiter="\t"):
            if not r or not r[-1] or r[0] == "source":
                continue
            n_rows += 1
            try:
                ids = G.encode_intermediate(K.text_to_intermediate(r[-1], table), pim)
                if max(ids) >= num_symbols:
                    raise KeyError(f"id {max(ids)} >= {num_symbols}")
            except KeyError as exc:
                rejected.append({"split": sp, "uid": r[1] if len(r) >= 3 else "",
                                 "text": r[-1], "why": str(exc)})
                continue
            n_ok += 1
            c.update(ids)
        per_split[sp] = c
        counts.update(c)

    used = sorted(counts)
    unused = sorted(set(id2tok) - set(used))
    out = {
        "n_rows": n_rows, "n_ok": n_ok, "n_rejected": len(rejected),
        "num_symbols": num_symbols,
        "n_used": len(used), "n_unused": len(unused),
        "used": [{"id": i, "tok": id2tok.get(i, "?"), "n": counts[i],
                  "share": counts[i] / sum(counts.values())} for i in
                 sorted(used, key=lambda i: -counts[i])],
        "unused": [{"id": i, "tok": id2tok.get(i, "?")} for i in unused],
        "rejected_sample": rejected[:50],
        "path": "kanji -> intermediate (kana_g2p) -> phoneme ids (A-1 / D-014)",
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=2)
    print(f"{n_ok:,}/{n_rows:,} 行 / 使用 {len(used)} 音素 / 未使用 {len(unused)}")
    print("使用:", " ".join(f"{id2tok.get(i,'?')}({counts[i]})" for i in
                            sorted(used, key=lambda i: -counts[i])[:25]), "…")
    print("未使用:", " ".join(id2tok.get(i, "?") for i in unused))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
