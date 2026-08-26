"""Option 2: precompiled phrase set. Measure real bytes/sentence."""
import sys, os, json, statistics, zlib
PP="/Users/s19447/Documents/piper-plus"
os.environ.setdefault("OPEN_JTALK_DICT_DIR", PP+"/build/share/open_jtalk/dic")
sys.path.insert(0, PP+"/src/python")
sys.path.insert(0, PP+"/src/python/g2p")
import piper_plus_g2p.japanese as J
assert J.__file__.startswith(PP+"/src/python/g2p"), J.__file__
ph = J.JapanesePhonemizer()

def rows(p):
    out=[]
    with open(p, encoding="utf-8") as f:
        h=f.readline()
        for ln in f:
            c=ln.rstrip("\n").split("\t")
            if len(c)>=3: out.append((c[0],c[1],c[2]))
    return out

res={}
for name,path in [("embedded","corpus_embedded.tsv"),("heldout","corpus_heldout.tsv"),("train","corpus_train.tsv")]:
    B="/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0/"+path
    rs=rows(B)
    lens=[]; toks=set(); seqs=[]; chars=[]
    for src,i,t in rs:
        try:
            tk=ph.phonemize(t)
        except Exception as e:
            continue
        lens.append(len(tk)); toks.update(tk); seqs.append(tk); chars.append(len(t))
    lens.sort()
    n=len(lens)
    # raw 1 byte per phoneme id + 1 terminator
    raw=sum(lens)+n
    # concatenated blob + uint16 offset index
    idx=2*(n+1)
    # gzip of the whole blob
    blob=b"".join(bytes([hash(x)&0xff for x in s])+b"\x00" for s in seqs)  # placeholder
    # real blob using an id map
    vocab=sorted(toks); vid={t:i+1 for i,t in enumerate(vocab)}
    blob=b"".join(bytes(vid[x] for x in s)+b"\x00" for s in seqs)
    gz=len(zlib.compress(blob,9))
    res[name]=dict(sentences=n, mean_chars=round(statistics.mean(chars),1),
        mean_phonemes=round(statistics.mean(lens),1), median_phonemes=lens[n//2],
        p95_phonemes=lens[int(n*0.95)], max_phonemes=lens[-1],
        distinct_tokens=len(toks),
        bytes_raw_1B_per_ph_plus_nul=raw, bytes_index_uint16=idx,
        bytes_total=raw+idx, bytes_per_sentence=round((raw+idx)/n,1),
        blob_deflate=gz, blob_deflate_per_sentence=round(gz/n,1))
    print(name, json.dumps(res[name], ensure_ascii=False))
json.dump(res, open("/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0alt/opt2.json","w"), ensure_ascii=False, indent=1)
