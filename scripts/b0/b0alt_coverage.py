"""Hybrid coverage curve: top-N surface dict + kana fallback for OOV."""
import os, sys, json, collections
DIC="~/Documents/piper-plus/build/share/open_jtalk/dic"
os.environ["OPEN_JTALK_DICT_DIR"]=DIC
B0="<scratch>"
ALT="<scratch>"
import pyopenjtalk
KANA=set("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽぁぃぅぇぉゃゅょっゎゐゑー"
         "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポァィゥェォャュョッヮヴヵヶー"
         "、。！？「」『』・…‥，．")
def tok(path):
    out=[]
    with open(path,encoding="utf-8") as f:
        f.readline()
        for ln in f:
            c=ln.rstrip("\n").split("\t")
            if len(c)<3: continue
            nj=pyopenjtalk.run_frontend(c[2])
            out.append([n["string"] for n in nj])
    return out
train=tok(B0+"/corpus_train.tsv")
held =tok(B0+"/corpus_heldout.tsv")
emb  =tok(B0+"/corpus_embedded.tsv")
freq=collections.Counter()
for s in train: freq.update(s)
ranked=[w for w,_ in freq.most_common()]
print("train tokens", sum(len(s) for s in train), "distinct", len(freq))
res={}
for N in [1000,2000,5000,10000,15000,20000,len(ranked)]:
    D=set(ranked[:N])
    row={}
    for name,corp in (("heldout",held),("embedded",emb)):
        nt=ct=0; ns=cs=0; cs_kana=0
        for s in corp:
            nt+=len(s); ok=True; okk=True
            for w in s:
                if w in D: ct+=1
                else:
                    ok=False
                    if not all(ch in KANA for ch in w): okk=False
            ns+=1; cs+= ok; cs_kana += (ok or okk)
        row[name]=dict(token_coverage_pct=round(100*ct/nt,2),
                       sentence_all_in_dict_pct=round(100*cs/ns,2),
                       sentence_dict_or_kana_pct=round(100*cs_kana/ns,2))
    res[N]=row
    print(N, json.dumps(row))
json.dump(res, open(ALT+"/coverage.json","w"), indent=1)
