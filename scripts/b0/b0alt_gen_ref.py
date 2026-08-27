"""Reference pass with the FULL dict: phoneme seq, phoneme+accent seq, and the kana rendering."""
import os, sys, json
DIC=sys.argv[1]; SRC=sys.argv[2]; OUT=sys.argv[3]
os.environ["OPEN_JTALK_DICT_DIR"]=DIC
PP="~/Documents/piper-plus"
sys.path.insert(0, PP+"/src/python"); sys.path.insert(0, PP+"/src/python/g2p")
import pyopenjtalk
assert pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()==DIC
import piper_plus_g2p.japanese as J
ph=J.JapanesePhonemizer()

rows=[]
with open(SRC,encoding="utf-8") as f:
    head=f.readline()
    for ln in f:
        c=ln.rstrip("\n").split("\t")
        if len(c)>=3: rows.append(c[2])
out=[]
for t in rows:
    rec={"text":t}
    try: rec["ph"]=pyopenjtalk.g2p(t)
    except Exception as e: rec["ph"]="<<ERR>>"
    try: rec["pha"]=" ".join(ph.phonemize(t))
    except Exception as e: rec["pha"]="<<ERR>>"
    try:
        nj=pyopenjtalk.run_frontend(t)
        rec["kana"]="".join((n["string"] if n["pos"]=="記号" else n["pron"].replace("’","")) for n in nj)
    except Exception as e:
        rec["kana"]="<<ERR>>"
    out.append(rec)
json.dump(out, open(OUT,"w"), ensure_ascii=False)
print("wrote", OUT, len(out))
