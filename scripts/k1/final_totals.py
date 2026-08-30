import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, os
SP = os.path.dirname(os.path.abspath(__file__))
CHARBIN, UNKDIC = 262496, 5690

def ctx(path):
    lc, rc = set(), set()
    for ln in open(path, encoding="utf-8"):
        p = ln.split("\t")
        lc.add(p[1]); rc.add(p[2])
    return len(lc), len(rc)

for tag, ents, q1c, q3, q2, q2b in (
    ("piper-plus build (788,923)", "entries.tsv", "q1c.json", "q3.json", "q2.json", "q2b.json"),
    ("pyopenjtalk      (789,388)", "entries_pyojt.tsv", None, None, None, None)):
    print("="*72); print(tag)
    n_lc, n_rc = ctx(os.path.join(_WORK, ents))
    print(f"  context id: left {n_lc} / right {n_rc} -> matrix {4+2*n_lc*n_rc:,d} B")
