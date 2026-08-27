"""Kanji-keyed pruned dicts, ranked by TRAIN-split frequency only (held-out honest)."""
import os, sys, collections, json, shutil
B0="<scratch>"
ALT="<scratch>"
SRCD="~/Documents/piper-plus/build/share/open_jtalk/dic"
os.environ["OPEN_JTALK_DICT_DIR"]=SRCD
import pyopenjtalk
freq=collections.Counter()
with open(B0+"/corpus_train.tsv",encoding="utf-8") as f:
    f.readline()
    for ln in f:
        c=ln.rstrip("\n").split("\t")
        if len(c)>=3: freq.update(n["string"] for n in pyopenjtalk.run_frontend(c[2]))
bysurf=collections.defaultdict(list)
for ln in open(B0+"/full/in/all.csv",encoding="utf-8"):
    p=ln.rstrip("\n").split(",",4)
    bysurf[p[0]].append((p[1],p[2],p[3],p[4]))
in_c=[s for s,_ in freq.most_common() if s in bysurf]
rest=sorted((s for s in bysurf if s not in freq), key=lambda s:(min(int(e[2]) for e in bysurf[s]), len(s)))
ranked=in_c+rest
print("train distinct surfaces in dict:", len(in_c), "ranked:", len(ranked))
for N in [int(x) for x in sys.argv[1:]]:
    d=f"{ALT}/kanji_{N}"; os.makedirs(d+"/out", exist_ok=True)
    for fn in ("left-id.def","right-id.def","pos-id.def","rewrite.def","char.bin","unk.dic","matrix.bin"):
        shutil.copyfile(f"{SRCD}/{fn}", f"{d}/{fn}")
    open(f"{d}/dicrc","w").write("cost-factor = 800\nbos-feature = BOS/EOS,*,*,*,*,*,*,*,*,*,*\neval-size = 8\nunk-eval-size = 4\nconfig-charset = UTF-8\n")
    n=0
    with open(f"{d}/dic.csv","w",encoding="utf-8",newline="\n") as f:
        for s in ranked[:N]:
            for lc,rc,cost,feat in bysurf[s]:
                f.write(f"{s},{lc},{rc},{cost},{feat}\n"); n+=1
    print(f"N={N} entries={n} csv={os.path.getsize(f'{d}/dic.csv'):,}")
