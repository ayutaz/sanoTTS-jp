"""pyopenjtalk が実際に読む辞書から entries_pyojt.tsv を作る。

チームリードの指摘 (2): entries.tsv は piper-plus の build ツリー由来で
リビジョンが違う。両方測って差を出す。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import pyopenjtalk
from dump_entries_lib import load_entries

SP = os.path.dirname(os.path.abspath(__file__))
D = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
print("dic_dir =", D, flush=True)
es = load_entries(D)
print(f"entries = {len(es):,d}", flush=True)
surf = set(e[0] for e in es)
print(f"surfaces = {len(surf):,d}", flush=True)
out = os.path.join(_WORK, "entries_pyojt.tsv")
with open(out, "w", encoding="utf-8") as f:
    for (s, lc, rc, cost, pos6, orig, read, pron, acc, chain) in es:
        feat = ",".join(list(pos6) + [orig, read, pron, acc, chain])
        f.write(f"{s}\t{lc}\t{rc}\t0\t{cost}\t{feat}\n")
print("wrote", out, os.path.getsize(out), "B")
