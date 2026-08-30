"""OpenJTalk の text2mecab が掛ける半角→全角写像を実測して han2zen.json に落とす。

ASCII 0x21..0x7E を 1 文字ずつ MeCab に食わせ、出てきた表層を読むだけ。
`~`→`〜`(U+301C) / `-`→`−`(U+2212) / `"`→`”` のように単純な +0xFEE0 ではないので、
表を書き下さずに実測する。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import json
import os

import pyopenjtalk

SP = os.path.dirname(os.path.abspath(__file__))
m = {}
for o in range(0x21, 0x7F):
    ch = chr(o)
    feats, _ = pyopenjtalk.run_mecab_detailed(ch)
    m[ch] = "".join(x.split(",")[0] for x in feats)
bad = {k: v for k, v in m.items() if len(v) != 1}
assert not bad, f"1:1 でない写像があると文字オフセットがずれる: {bad}"
json.dump(m, open(os.path.join(_WORK, "han2zen.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print(f"wrote han2zen.json  entries={len(m)}  all 1:1")
print({k: m[k] for k in "aA1?~-\\'\"`([{"})
