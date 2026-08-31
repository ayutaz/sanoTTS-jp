"""(1) Viterbi ラティスのノード数と (2) カバー率と精度の対を測る。

「ラティスが文あたり RAM の主要項」という前提が本当かを見るために、
自前 Viterbi が実際に生成するノード数を数える。
⚠️ **KB 表示は算術**（ノード 1 個のバイト数は仮定値）。ノード数だけが実測。
"""
from __future__ import annotations

import os
import sys

import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import measure as M  # noqa: E402
import viterbi as V  # noqa: E402
import pyopenjtalk  # noqa: E402

DIC, MAXLEN, _, _ = D.load_dic()
T = V.Tok(DIC, MAXLEN)
rows = []
with open(M.CORPUS, encoding="utf-8") as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            rows.append(D.han2zen(p[2]))

nodes = np.array([sum(len(T.lookup(t, i)) for i in range(len(t))) for t in rows])
chars = np.array([len(t) for t in rows])
print(f"# ラティス (n={len(rows)} 文)")
print(f"  文字数        mean={chars.mean():.1f}  p95={np.percentile(chars, 95):.0f}  "
      f"max={chars.max()}")
print(f"  ノード数(実測) mean={nodes.mean():.1f}  p95={np.percentile(nodes, 95):.0f}  "
      f"max={nodes.max()}")
print(f"  ノード/文字    mean={(nodes / chars).mean():.2f}  max={(nodes / chars).max():.2f}")
for b in (24, 32):
    print(f"  ⚠️ 算術: {b} B/node と仮定すると mean {nodes.mean() * b / 1024:.1f} KB / "
          f"p95 {np.percentile(nodes, 95) * b / 1024:.1f} KB / "
          f"max {nodes.max() * b / 1024:.1f} KB")

print("\n# カバー率と精度の対")
for mode in ("longest_wcost", "min_density"):
    buckets = {"辞書内のみ": [0, 0, 0], "未知語あり": [0, 0, 0]}
    for text in rows:
        fa, mo = pyopenjtalk.run_mecab_detailed(text)
        njd_a = pyopenjtalk.apply_postprocessing(text, pyopenjtalk.run_njd_from_mecab(fa))
        ap_a = M.accent_phrases(njd_a)
        pron_a = "".join(M.pron_of(x.split(",")) for x in fa)
        seg = D.segment(text, DIC, MAXLEN, mode)
        fb = [f for _, _, f, _ in seg if not M.is_space_symbol(f.split(","))]
        njd_b = pyopenjtalk.apply_postprocessing(text, pyopenjtalk.run_njd_from_mecab(fb))
        ap_b = M.accent_phrases(njd_b)
        pron_b = "".join(M.pron_of(x.split(",")) for x in fb)
        clean = (not any(m["is_unknown"] for m in mo)) and (not any(u for *_, u in seg))
        k = "辞書内のみ" if clean else "未知語あり"
        buckets[k][0] += 1
        buckets[k][1] += pron_a == pron_b
        buckets[k][2] += ap_a == ap_b
    tot = sum(v[0] for v in buckets.values())
    print(f"  --- {mode}")
    for k, (n, p, a) in buckets.items():
        print(f"    {k}: n={n} ({n / tot:.4f})  読み一致={p / n:.4f} "
              f"{[round(x, 4) for x in M.wilson(p, n)]}  "
              f"アクセント句一致={a / n:.4f} {[round(x, 4) for x in M.wilson(a, n)]}")
