import collections, os, sys, json
SRC="~/Documents/piper-plus/build/share/open_jtalk/dic"

# corpus surface freq
freq=collections.Counter()
for ln in open("corpus_tokfreq.tsv",encoding="utf-8"):
    p=ln.rstrip("\n").split("\t"); freq[p[1]] += int(p[0])

# dict entries grouped by surface
bysurf=collections.defaultdict(list)
for ln in open("entries.tsv",encoding="utf-8"):
    s,lc,rc,pid,cost,feat = ln.rstrip("\n").split("\t")
    bysurf[s].append((int(lc),int(rc),int(cost),feat))

# ranking: corpus freq desc, then (not in corpus) by min wcost asc
in_corpus = [s for s,_ in freq.most_common() if s in bysurf]
rest = sorted((s for s in bysurf if s not in freq), key=lambda s: (min(e[2] for e in bysurf[s]), len(s)))
ranked = in_corpus + rest
print("ranked surfaces:", len(ranked), "in-corpus:", len(in_corpus))

def emit(N, variant, path):
    n_entries=0; lids=set(); rids=set()
    with open(path,"w",encoding="utf-8",newline="\n") as f:
        for s in ranked[:N]:
            for lc,rc,cost,feat in bysurf[s]:
                if variant in ("noorig","noread"):
                    fs=feat.split(",")
                    if len(fs)>=11:
                        k=fs[9].count(":")+1
                        if variant=="noorig" and k==1:
                            fs[6]="*"
                        if variant=="noread":
                            fs[7]=":".join(["*"]*k)
                        feat=",".join(fs)
                elif variant=="tts3":
                    fs=feat.split(",")
                    if len(fs)>=11:
                        k=fs[9].count(":")+1
                        if k==1:
                            fs[6]="*"      # 原形: safe to blank for non-chained
                        # chained: 原形 IS the surface segmenter in NJDNode_load -> keep
                        fs[7]=":".join(["*"]*k)   # 読み
                        feat=",".join(fs)
                elif variant in ("tts","tts2"):
                    fs=feat.split(",")
                    if len(fs)>=11:
                        if variant=="tts2":
                            # preserve ':' arity of chained (compound) entries -
                            # NJDNode_load splits orig/read/pron by ':' in lockstep
                            # with the accent field, so arity must match.
                            k=fs[9].count(":")+1
                            blank=":".join(["*"]*k)
                        else:
                            blank="*"
                        fs[6]=blank   # 原形
                        fs[7]=blank   # 読み
                        feat=",".join(fs)
                f.write(f"{s},{lc},{rc},{cost},{feat}\n")
                n_entries+=1; lids.add(lc); rids.add(rc)
    return n_entries, len(lids), len(rids)

out={}
for N in [int(x) for x in sys.argv[1:]]:
    for variant in ("noorig","noread"):
        d=f"in_{N}_{variant}"
        os.makedirs(d,exist_ok=True)
        for fn in ("left-id.def","right-id.def","pos-id.def","rewrite.def","matrix.bin","char.bin","unk.dic"):
            if not os.path.exists(f"{d}/{fn}"):
                os.link(f"{SRC}/{fn}", f"{d}/{fn}")
        open(f"{d}/dicrc","w").write("cost-factor = 800\nbos-feature = BOS/EOS,*,*,*,*,*,*,*,*,*,*\neval-size = 8\nunk-eval-size = 4\nconfig-charset = UTF-8\n")
        ne,nl,nr = emit(N, variant, f"{d}/dic.csv")
        out[f"{N}_{variant}"]={"surfaces":min(N,len(ranked)),"entries":ne,"distinct_lid":nl,"distinct_rid":nr,
                               "csv_bytes":os.path.getsize(f"{d}/dic.csv")}
        print(f"{N:>7} {variant:<5} entries={ne:>7} lid={nl:>5} rid={nr:>5} csv={os.path.getsize(f'{d}/dic.csv'):>12,}")
json.dump(out, open("prune_input.json","w"), indent=1, ensure_ascii=False)
