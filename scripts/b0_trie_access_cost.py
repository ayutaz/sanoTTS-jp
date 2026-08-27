"""Instrumented Darts common-prefix-search over the real NAIST-JDIC sys.dic.
Counts random 4/8-byte reads and distinct 64 KB flash pages touched per sentence.
"""
import struct, sys, numpy as np, collections
P="~/Documents/piper-plus/build/share/open_jtalk/dic/sys.dic"
raw=open(P,"rb").read()
magic,version,dtype,lexsize,lsize,rsize,dsize,tsize,fsize,dummy=struct.unpack("<10I",raw[:40])
DARTS_OFF=72; TOK_OFF=72+dsize; FEAT_OFF=72+dsize+tsize
darts=np.frombuffer(raw,dtype=np.int32,count=dsize//4,offset=DARTS_OFF)
base=darts[0::2]; check=darts[1::2].view(np.uint32)
N=len(base)

PAGE=65536
def unit_off(i): return DARTS_OFF + i*8

class Stat:
    def __init__(s):
        s.reads=0; s.pages=set(); s.tok_reads=0; s.feat_reads=0

def cps(kb, start_pos, st):
    """common prefix search from byte position start_pos; returns list of (len, value)"""
    res=[]
    b=int(base[0]); st.reads+=1; st.pages.add(unit_off(0)//PAGE)
    for k in range(start_pos, len(kb)):
        # check terminal at current node
        p=b
        if 0<=p<N:
            st.reads+=1; st.pages.add(unit_off(p)//PAGE)
            if np.uint32(b & 0xFFFFFFFF)==check[p]:
                n=int(base[p])
                if n<0: res.append((k-start_pos, -n-1))
        c=kb[k]
        p=b+c+1
        if not (0<=p<N): return res
        st.reads+=1; st.pages.add(unit_off(p)//PAGE)
        if np.uint32(b & 0xFFFFFFFF)!=check[p]: return res
        b=int(base[p])
    p=b
    if 0<=p<N:
        st.reads+=1; st.pages.add(unit_off(p)//PAGE)
        if np.uint32(b & 0xFFFFFFFF)==check[p]:
            n=int(base[p])
            if n<0: res.append((len(kb)-start_pos, -n-1))
    return res

def token_and_feature(v, st):
    ti=v>>8; cnt=v&0xff
    out=[]
    for i in range(ti,ti+cnt):
        o=TOK_OFF+i*16
        st.tok_reads+=1; st.pages.add(o//PAGE)
        lc,rc,pos,cost,fo,comp=struct.unpack("<HHHhII",raw[o:o+16])
        fabs=FEAT_OFF+fo
        st.feat_reads+=1; st.pages.add(fabs//PAGE)
        e=raw.index(b"\0",fabs)
        st.pages.add(e//PAGE)
        out.append(raw[fabs:e].decode("utf-8","replace"))
    return out

lines=[l.split("\t")[2].strip() for l in open(sys.argv[1],encoding="utf-8") if l.count("\t")>=2]
import random; random.seed(0)
sample=random.sample(lines, min(300,len(lines)))
tot_reads=[]; tot_pages=[]; tot_tok=[]; tot_chars=[]; tot_cands=[]
for s in sample:
    st=Stat(); kb=s.encode("utf-8"); ncand=0
    for i in range(len(kb)):
        if (kb[i]&0xC0)==0x80: continue   # only start lattice at char boundaries
        r=cps(kb,i,st)
        ncand+=len(r)
        for _,v in r: token_and_feature(v,st)
    tot_reads.append(st.reads); tot_pages.append(len(st.pages))
    tot_tok.append(st.tok_reads); tot_chars.append(len(s)); tot_cands.append(ncand)
import statistics as S
print(f"sentences={len(sample)}  mean_chars={S.mean(tot_chars):.1f}")
print(f"trie unit reads / sentence : mean={S.mean(tot_reads):.0f} median={S.median(tot_reads):.0f} max={max(tot_reads)}")
print(f"token+feature reads / sent : mean={S.mean(tot_tok):.0f} max={max(tot_tok)}")
print(f"dict candidates / sentence : mean={S.mean(tot_cands):.0f} max={max(tot_cands)}")
print(f"distinct 64KB pages / sent : mean={S.mean(tot_pages):.0f} median={S.median(tot_pages):.0f} max={max(tot_pages)}")
print(f"reads per char             : {S.mean(tot_reads)/S.mean(tot_chars):.1f}")
