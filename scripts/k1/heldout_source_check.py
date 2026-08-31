"""held-out の先頭が単一ソースに偏っていないかを確認する。

ceiling-lane が「先頭 800 行は全部 cv/sentence_collector」と報告してきた。
私は先頭 400 行で天井を測って 91.25% と報告してしまったので、
**その偏りの大きさを自分で確かめる**（verifying-reports: repro を自分で走らせる）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
from collections import Counter

P = (_ROOT + "/data/splits/corpus_heldout.tsv")
rows = []
with open(P, encoding="utf-8") as f:
    hdr = f.readline().rstrip("\n").split("\t")
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            rows.append(p)
print(f"ヘッダ = {hdr}")
print(f"データ行 = {len(rows):,d}")
print(f"⚠️ B-0 は 2,325 行と申告 / M-9 も 2,325。実体は {len(rows):,d}")

src = [r[0] for r in rows]
print(f"\nソース内訳（全体）:")
for k, v in Counter(src).most_common():
    print(f"  {k:32s} {v:>6,d}  {100*v/len(src):5.2f}%")

for n in (400, 800, 1000):
    c = Counter(src[:n])
    print(f"\n先頭 {n} 行のソース内訳:")
    for k, v in c.most_common():
        print(f"  {k:32s} {v:>6,d}  {100*v/n:5.2f}%")

# 連続ブロックになっているか
print("\nソースの並び（連続ブロックの境界）:")
prev = None
start = 0
for i, s in enumerate(src + [None]):
    if s != prev:
        if prev is not None:
            print(f"  行 {start:>5,d}..{i-1:>5,d}  {prev}")
        prev = s
        start = i
