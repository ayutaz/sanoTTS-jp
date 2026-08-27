"""Evaluate one dict dir against the full-dict reference (kana input)."""
import os, sys, json
DIC, REF, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
FIELD = sys.argv[4] if len(sys.argv)>4 else "kana"   # "kana" or "text"
os.environ["OPEN_JTALK_DICT_DIR"]=DIC
PP="~/Documents/piper-plus"
sys.path.insert(0, PP+"/src/python"); sys.path.insert(0, PP+"/src/python/g2p")
import pyopenjtalk
assert pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()==DIC
import piper_plus_g2p.japanese as J
ph=J.JapanesePhonemizer()
ref=json.load(open(REF))
res=[]
for r in ref:
    inp=r[FIELD]
    try: a=pyopenjtalk.g2p(inp)
    except Exception: a="<<ERR>>"
    try: b=" ".join(ph.phonemize(inp))
    except Exception: b="<<ERR>>"
    res.append({"ph":a,"pha":b})
json.dump(res, open(OUT,"w"), ensure_ascii=False)
print("wrote", OUT, len(res))
