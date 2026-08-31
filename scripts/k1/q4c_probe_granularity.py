"""リードの 2,243 probes/文 と私の 7,546 reads/文 を突き合わせる。

差は「1 probe を何と数えるか」の可能性が高い。2 通りで同時に数える:
  logical : trie の論理操作 1 回 = 1（select0 / rank1 / label 取得 / 終端ビット判定）
  byte    : rank/select 実装が出すバイト範囲アクセス 1 回 = 1（私の Q4 の定義）
どちらも trie のみ（value pool / matrix は含めない = リードの条件に合わせる）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os, statistics, sys
import numpy as np
from trie_common import get_trie
from tries import PlainLouds

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
PAGE, LINE = 65536, 32
t = get_trie()
PL = PlainLouds(t["label"], t["term"], t["cs"])
parts = PL.parts()
BASE, o = {}, 0
for k in ("louds.bits", "louds.sup", "louds.blk", "louds.sel0", "labels",
          "term.bits", "term.sup", "term.blk"):
    BASE[k] = o; o += parts[k]
print(f"trie イメージ {o:,d} B / {(o+PAGE-1)//PAGE} ページ")

BYTES = []
PL.probe = lambda a, off, n: BYTES.append((a, off, n))
PL.bv.probe = PL.probe; PL.tbv.probe = PL.probe

LOG = [0]
_s0, _r1, _bit, _lab = PL.bv.select0, PL.bv.rank1, PL.tbv.bit, PL._label
def s0(k):  LOG[0] += 1; return _s0(k)
def r1(i):  LOG[0] += 1; return _r1(i)
def tb(i):  LOG[0] += 1; return _bit(i)
def lb(c):  LOG[0] += 1; return _lab(c)
PL.bv.select0, PL.bv.rank1, PL.tbv.bit, PL._label = s0, r1, tb, lb

texts = []
with open(HELDOUT, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[2]: texts.append(p[2])

rows = []
for tx in texts:
    BYTES.clear(); LOG[0] = 0
    kb = tx.encode("utf-8")
    for i in range(len(kb)):
        PL.common_prefix_search(kb, i)
    pg, ln_ = set(), set()
    for a, off, n in BYTES:
        s = BASE[a] + off
        pg.update(range(s // PAGE, (s + n - 1) // PAGE + 1))
        ln_.update(range(s // LINE, (s + n - 1) // LINE + 1))
    rows.append((LOG[0], len(BYTES), len(pg), len(ln_)))

def st(j):
    v = sorted(r[j] for r in rows)
    return statistics.mean(v), v[len(v)//2], v[int(0.95*(len(v)-1))], v[-1]

print(f"n = {len(rows)} 文（held-out 全体）/ trie のみ・value pool 無し\n")
print(f"{'指標':>22} {'平均':>9} {'中央値':>8} {'p95':>8} {'最大':>8}  {'リード':>16}  {'B-0 darts':>10}")
ref = {0: "2,243 / 4,808 / 8,025", 2: "35.3 / 51 / 64", 3: "343.7 / 641 / 951"}
for j, nm in ((0, "論理 probe"), (1, "バイトアクセス"), (2, "64 KB ページ"), (3, "32 B ライン")):
    m, md, p95, mx = st(j)
    print(f"{nm:>22} {m:>9.1f} {md:>8.0f} {p95:>8.0f} {mx:>8.0f}  {ref.get(j,''):>16}  "
          f"{'~420' if j==0 else ('91' if j==2 else ''):>10}")
