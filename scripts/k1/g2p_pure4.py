import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os, sys, json
D=os.environ.get("SAAN_DICT")
if D: os.environ["OPEN_JTALK_DICT_DIR"]=D
sys.path.insert(0,(_WORK + ""))
import pyopenjtalk as P
from g2p_ablate import CORPUS, UNITS, run_chain, wilson
PURE4={"filler_accent","suppress_u_long","retreat_acc_nuc","acc_after_chaining"}
PURE5=PURE4|{"odori"}
rows=[];f=open(CORPUS,encoding="utf-8");f.readline()
for ln in f:
    p=ln.rstrip("\n").split("\t")
    if len(p)>=3 and p[2].strip(): rows.append(p[2])
n=len(rows); V={"vanilla":set(),"pure4(odori抜き)":PURE4,"pure5(odori込み)":PURE5}
dis={v:{u:0 for u in UNITS} for v in V}
with P._resolve_jtalk(None) as jt:
    for t in rows:
        ref=P.extract_fullcontext(t); raw=jt.run_frontend(t)
        for v,en in V.items():
            lab=run_chain(t,raw,jt,en)
            for u in UNITS:
                if UNITS[u](lab,t)!=UNITS[u](ref,t): dis[v][u]+=1
dd=P.OPEN_JTALK_DICT_DIR.decode() if isinstance(P.OPEN_JTALK_DICT_DIR,bytes) else str(P.OPEN_JTALK_DICT_DIR)
print("dict sys.dic:", os.path.getsize(os.path.join(dd,"sys.dic")))
for v in V:
    o=[]
    for u in ("U2_accphrase","U3_pipertok"):
        k=n-dis[v][u]; lo,hi=wilson(k,n); o.append(f"{u} {k}/{n}={k/n*100:6.2f}% [{lo*100:.2f},{hi*100:.2f}]")
    print(f"  {v:20s} " + "   ".join(o))
