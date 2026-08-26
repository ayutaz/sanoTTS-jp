"""Pure rule kana -> phoneme, using ONLY the OpenJTalk jpcommon mora table (162 entries).
No dictionary. This is the MCU-side lower bound for the 'kana input' design.
"""
import re, json, sys
OJ="/Users/s19447/Documents/piper-plus/build/o/src/openjtalk_external/lib/open_jtalk/src"
def load_mora():
    lines=open(OJ+"/jpcommon/jpcommon_rule_utf_8.h",encoding="utf-8").read().split("\n")
    i=[n for n,l in enumerate(lines) if "jpcommon_mora_list[]" in l][0]
    t={}
    for l in lines[i+1:]:
        if "};" in l: break
        q=re.findall(r'"([^"]*)"', l)
        if len(q)==3: t[q[0]]=(q[1],q[2])
        elif len(q)==2 and "NULL" in l: t[q[0]]=(None,q[1])   # vowel slot NULL: single phoneme
    return t
MORA=load_mora()
VOICELESS=set("k ky kw s sh t ty ts ch h hy f p py".split())
def g2p(kana, devoice=True):
    out=[]; i=0; n=len(kana)
    while i<n:
        c=kana[i]
        if c in "、，,":
            out.append("pau"); i+=1; continue
        if c in "。．.!?！？":
            i+=1; continue
        if c=="ッ": out.append("cl"); i+=1; continue
        if c=="ン": out.append("N"); i+=1; continue
        if c=="ー":
            for t in reversed(out):
                if t in "aiueo": out.append(t); break
            else: out.append("a")
            i+=1; continue
        if i+1<n and kana[i:i+2] in MORA:
            cons,v=MORA[kana[i:i+2]]
            if cons is not None: out.append(cons)
            out.append(v); i+=2; continue
        if c in MORA:
            cons,v=MORA[c]
            if cons is not None: out.append(cons)
            out.append(v); i+=1; continue
        i+=1
    if devoice:
        for k,t in enumerate(out):
            if t in ("i","u"):
                prev = out[k-1] if k>0 else None
                nxt  = out[k+1] if k+1<len(out) else None
                if prev in VOICELESS and (nxt in VOICELESS or nxt is None or nxt=="pau"):
                    out[k]=t.upper()
    return out

if __name__=="__main__":
    ALT="/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0alt"
    def lev(a,b):
        if a==b: return 0
        lb=len(b); prev=list(range(lb+1))
        for i in range(1,len(a)+1):
            cur=[i]+[0]*lb; ai=a[i-1]
            for j in range(1,lb+1): cur[j]=min(prev[j]+1,cur[j-1]+1,prev[j-1]+(ai!=b[j-1]))
            prev=cur
        return prev[lb]
    def norm(t): return {"I":"i","U":"u","A":"a","E":"e","O":"o"}.get(t,t)
    for split in ("heldout","embedded"):
        ref=json.load(open(f"{ALT}/ref_{split}.json"))
        for dv in (True,False):
            ed=tot=ex=edn=totn=exn=0
            for r in ref:
                x=r["ph"].split(); y=g2p(r["kana"], devoice=dv)
                ed+=lev(x,y); tot+=len(x); ex+=(x==y)
                xn=[norm(t) for t in x]; yn=[norm(t) for t in y]
                edn+=lev(xn,yn); totn+=len(xn); exn+=(xn==yn)
            n=len(ref)
            print(f"{split:9s} devoice={dv!s:5s} PER {100*ed/tot:6.3f}%  sent-exact {100*ex/n:6.2f}%   | devoicing-normalised PER {100*edn/totn:6.3f}%  sent-exact {100*exn/n:6.2f}%")
