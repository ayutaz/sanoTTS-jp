"""Build kana-keyed (reading-keyed) MeCab dictionaries at several vocab levels."""
import sys, os, collections, json
B0="/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0"
ALT="/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0alt"
SRC=B0+"/full/in/all.csv"
KANA=set("アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポァィゥェォャュョッーヮヴヵヶ")

# --- reading frequency from the TRAIN split only (held-out honesty)
freq=collections.Counter()
for ln in open(B0+"/corpus_tokfreq.tsv",encoding="utf-8"):
    p=ln.rstrip("\n").split("\t")
    if len(p)>=4: freq[p[3].replace("’","").replace(":","")] += int(p[0])

kana_rows=[]; sym_rows=[]
seen=set()
n_nonkana=0; n_dup=0
for ln in open(SRC, encoding="utf-8"):
    p=ln.rstrip("\n").split(",",4)
    surf, lid, rid, cost, feat = p[0], p[1], p[2], p[3], p[4]
    fs=feat.split(",")
    if len(fs)!=11: continue
    pos1,pos2,pos3,pos4,ctype,cform,orig,read,pron,acc,chain = fs
    key = pron.replace("’","").replace(":","")
    if key and all(ch in KANA for ch in key):
        pc = pron.replace("’","")
        nf=",".join([pos1,pos2,pos3,pos4,ctype,cform,pc,pc,pron,acc,chain])
        sig=(key,lid,rid,nf)
        if sig in seen: n_dup+=1; continue
        seen.add(sig)
        kana_rows.append((key,lid,rid,cost,nf))
    else:
        n_nonkana+=1
        sym_rows.append((surf,lid,rid,cost,feat))   # punctuation / symbols kept verbatim
print(f"kana entries={len(kana_rows)} symbol entries={len(sym_rows)} exact_dups_collapsed={n_dup}")

bysurf=collections.defaultdict(list)
for r in kana_rows: bysurf[r[0]].append(r)
print("distinct kana surfaces:", len(bysurf))

in_corpus=[s for s,_ in freq.most_common() if s in bysurf]
rest=sorted((s for s in bysurf if s not in freq), key=lambda s:(min(int(e[3]) for e in bysurf[s]), len(s)))
ranked=in_corpus+rest
print("ranked:", len(ranked), "in-corpus:", len(in_corpus))

def emit(N, path):
    n=0
    with open(path,"w",encoding="utf-8",newline="\n") as f:
        for s in ranked[:N]:
            for k,l,r,c,nf in bysurf[s]:
                f.write(f"{k},{l},{r},{c},{nf}\n"); n+=1
        for k,l,r,c,nf in sym_rows:
            f.write(f"{k},{l},{r},{c},{nf}\n"); n+=1
    return n

SRCD="/Users/s19447/Documents/piper-plus/build/share/open_jtalk/dic"
import shutil
out={}
for N in [int(x) for x in sys.argv[1:]]:
    d=f"{ALT}/kana_{N}"
    os.makedirs(d, exist_ok=True)
    for fn in ("left-id.def","right-id.def","pos-id.def","rewrite.def","char.bin","unk.dic","matrix.bin"):
        if not os.path.exists(f"{d}/{fn}"): shutil.copyfile(f"{SRCD}/{fn}", f"{d}/{fn}")
    open(f"{d}/dicrc","w").write("cost-factor = 800\nbos-feature = BOS/EOS,*,*,*,*,*,*,*,*,*,*\neval-size = 8\nunk-eval-size = 4\nconfig-charset = UTF-8\n")
    ne=emit(N, f"{d}/dic.csv")
    out[N]={"kana_surfaces":min(N,len(ranked)),"entries":ne,"csv_bytes":os.path.getsize(f"{d}/dic.csv")}
    print(f"{N:>8} entries={ne:>8} csv={os.path.getsize(f'{d}/dic.csv'):>12,}")
json.dump(out, open(f"{ALT}/kana_input.json","w"), indent=1)
