"""Q3: 残差を具体的な文字列で見る。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, random, sys, difflib
SCRATCH = (_WORK + "/")
d = json.load(open(SCRATCH + "flags_examples.json"))
which = sys.argv[1]
k = int(sys.argv[2]) if len(sys.argv) > 2 else 40
ex = d[which]
random.seed(20260830)


def diffspan(a, b):
    """最初と最後の差分位置だけ切り出して見せる。"""
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    if not ops:
        return "(差分なし)"
    lo = max(0, min(o[1] for o in ops) - 3)
    hi = min(len(a), max(o[2] for o in ops) + 3)
    lo2 = max(0, min(o[3] for o in ops) - 3)
    hi2 = min(len(b), max(o[4] for o in ops) + 3)
    return f"…{' '.join(a[lo:hi])}…  →  …{' '.join(b[lo2:hi2])}…"


from collections import Counter, defaultdict
bycat = defaultdict(list)
for e in ex:
    bycat[e["cats"][0]].append(e)

print(f"=== {which}: 不一致 {len(ex)} 文 / 分類別 ===")
for c, lst in sorted(bycat.items(), key=lambda kv: -len(kv[1])):
    print(f"  {c:42s} {len(lst):4d}")
print()
per = max(1, k // max(1, len(bycat)))
for c, lst in sorted(bycat.items(), key=lambda kv: -len(kv[1])):
    print(f"---- {c}  (n={len(lst)}) ----")
    for e in random.sample(lst, min(per, len(lst))):
        print(f"  [{e['src']}] {e['text']}")
        print(f"     既定 → {which}:  {diffspan(e['ref'], e['got'])}")
    print()
