import json, sys
def lev(a,b):
    if a==b: return 0
    la,lb=len(a),len(b)
    prev=list(range(lb+1))
    for i in range(1,la+1):
        cur=[i]+[0]*lb
        ai=a[i-1]
        for j in range(1,lb+1):
            cur[j]=min(prev[j]+1, cur[j-1]+1, prev[j-1]+(ai!=b[j-1]))
        prev=cur
    return prev[lb]
def acc_marks(toks):
    return [t for t in toks if t in ("[","]","#")]
def score(ref, hyp):
    n=len(ref); ph_ex=0; pha_ex=0; ed=0; tot=0; eda=0; tota=0; err=0
    acc_ex=0
    for r,h in zip(ref,hyp):
        if h["ph"]=="<<ERR>>" or r["ph"]=="<<ERR>>": err+=1; continue
        a=r["ph"].split(); b=h["ph"].split()
        ed+=lev(a,b); tot+=len(a)
        if a==b: ph_ex+=1
        ra=r["pha"].split(); ha=h["pha"].split()
        eda+=lev(ra,ha); tota+=len(ra)
        if ra==ha: pha_ex+=1
        # accent structure only: the sequence of marks with their mora index
        def sig(ts):
            out=[];m=0
            for t in ts:
                if t in ("[","]","#","_"): out.append((t,m))
                elif t not in ("^","$"): m+=1
            return out
        if sig(ra)==sig(ha): acc_ex+=1
    return dict(n=n, errors=err,
        phoneme_sentence_exact_pct=round(100*ph_ex/n,2),
        phoneme_error_rate_pct=round(100*ed/max(tot,1),3),
        phoneme_plus_accent_sentence_exact_pct=round(100*pha_ex/n,2),
        phoneme_plus_accent_error_rate_pct=round(100*eda/max(tota,1),3),
        accent_structure_sentence_exact_pct=round(100*acc_ex/n,2))
ref=json.load(open(sys.argv[1])); hyp=json.load(open(sys.argv[2]))
print(json.dumps(score(ref,hyp), ensure_ascii=False))
