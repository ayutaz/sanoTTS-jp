"""Q4: 文長スイープの集計。ピークが文長で有界かを見る。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")

with open(os.path.join(_WORK, "sweep.tsv"), encoding="utf-8") as f:
    hdr = f.readline().rstrip("\n").split("\t")
    rows = [[int(x) for x in ln.rstrip("\n").split("\t")] for ln in f if ln.strip()]
C = {k: [r[hdr.index(k)] for r in rows] for k in hdr}
targets = [int(x) for x in open(os.path.join(_WORK, "sweep_target.txt"))]
assert len(targets) == len(rows), f"{len(targets)} vs {len(rows)}"

d_lab = [a - b for a, b in zip(C["heap_at_label"], C["heap_before"])]

groups = {}
for i, t in enumerate(targets):
    groups.setdefault(t, []).append(i)

print(f"{'chars':>6} {'n':>3} {'nodes':>18} {'node/char':>10} {'labels':>10} "
      f"{'Δheap(ラベル段)':>20} {'B/char':>9} {'lookup':>8}")
print("-" * 96)
for t in sorted(groups):
    ii = groups[t]
    nd = [C["nodes_total"][i] for i in ii]
    lb = [C["labels"][i] for i in ii]
    hp = [d_lab[i] for i in ii]
    lk = [C["lookup_calls"][i] for i in ii]
    ch = [C["in_chars"][i] for i in ii]
    m = len(ii)
    print(f"{t:>6} {m:>3} {sum(nd)/m:>8.0f}(max {max(nd):>6}) "
          f"{sum(nd)/sum(ch):>10.2f} {sum(lb)/m:>10.1f} "
          f"{sum(hp)/m:>12,.0f}(max {max(hp):>8,}) {sum(hp)/sum(ch):>9.0f} {sum(lk)/m:>8.1f}")

print("\n1 文字あたりの係数（線形かどうか）")
xs = sorted(groups)
for t in xs:
    ii = groups[t]
    ch = sum(C["in_chars"][i] for i in ii)
    print(f"  {t:>5} chars : nodes/char {sum(C['nodes_total'][i] for i in ii)/ch:>6.2f}  "
          f"labels/char {sum(C['labels'][i] for i in ii)/ch:>6.3f}  "
          f"heap_B/char {sum(d_lab[i] for i in ii)/ch:>8.0f}")

print("\nheld-out 実文の最大 (98 文字) との比較:")
print("  スイープ 2400 文字 = 実文最大の 24.5 倍")
i2400 = groups[2400]
print(f"  2400 文字での Δheap(ラベル段) 最大: {max(d_lab[i] for i in i2400):,d} B")
print(f"  2400 文字でのノード数 最大        : {max(C['nodes_total'][i] for i in i2400):,d}")
print(f"  2400 文字でのラベル数 最大        : {max(C['labels'][i] for i in i2400):,d}")
print(f"  CPS の最大ヒット数 (全スイープ)    : {max(C['cps_max'])}  (kResultsSize=512)")
