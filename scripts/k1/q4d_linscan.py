"""残差の切り分け: 兄弟ラベルの探索を二分探索 -> 線形走査にすると論理 probe はどう動くか。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os, statistics
from trie_common import get_trie
from tries import PlainLouds

class LinScan(PlainLouds):
    def child(self, v, ch):
        s0 = self.bv.select0(v); s1 = self.bv.select0(v + 1)
        deg = s1 - s0 - 1
        if deg <= 0: return None
        c0 = self.bv.rank1(s0 + 1)
        for m in range(deg):                       # 線形走査
            L = self._label(c0 + m)
            if L == ch: return c0 + m
            if L > ch: return None
        return None

t = get_trie()
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
texts = []
with open(HELDOUT, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[2]: texts.append(p[2])

for nm, cls in (("二分探索 (私の実装)", PlainLouds), ("線形走査", LinScan)):
    PL = cls(t["label"], t["term"], t["cs"])
    LOG = [0]
    _s0, _r1, _bit, _lab = PL.bv.select0, PL.bv.rank1, PL.tbv.bit, PL._label
    PL.bv.select0 = lambda k: (LOG.__setitem__(0, LOG[0]+1), _s0(k))[1]
    PL.bv.rank1   = lambda i: (LOG.__setitem__(0, LOG[0]+1), _r1(i))[1]
    PL.tbv.bit    = lambda i: (LOG.__setitem__(0, LOG[0]+1), _bit(i))[1]
    PL._label     = lambda c: (LOG.__setitem__(0, LOG[0]+1), _lab(c))[1]
    per = []
    for tx in texts:
        LOG[0] = 0
        kb = tx.encode("utf-8")
        for i in range(len(kb)): PL.common_prefix_search(kb, i)
        per.append(LOG[0])
    per.sort()
    print(f"{nm:>22}  平均 {statistics.mean(per):>7.1f}  中央値 {per[len(per)//2]:>6} "
          f" p95 {per[int(0.95*(len(per)-1))]:>6}  最大 {per[-1]:>6}")
print(f"{'リード申告':>22}  平均  2243.0  中央値      -  p95   4808  最大   8025")
