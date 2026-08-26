import struct, collections, re, sys
sys.path.insert(0,"/Users/s19447/Documents/piper-plus/src/python")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pyopenjtalk
from class3 import CLASS3_READS
C3=set(CLASS3_READS)
CT={"特殊・マス","特殊・ナイ","サ変・スル"}
CF={"連用形","連用タ接続","連用ゴザイ接続","連用テ接続","連用デ接続","連用ニ接続","未然形"}
OK=set(["れる","られる","せる","させる","すぎる","ちゃう","なる","する","何"])|set(pyopenjtalk.MULTI_READ_KANJI_LIST)
DIC="/Users/s19447/Documents/piper-plus/.venv/lib/python3.13/site-packages/pyopenjtalk/dictionary/sys.dic"
b=open(DIC,"rb").read()
_,_,_,lexsize,_,_,dsize,tsize,fsize,_=struct.unpack("<10I",b[:40])
off=72+dsize; tokb=b[off:off+tsize]; feat=b[off+tsize:off+tsize+fsize]
feats=[]
for i in range(lexsize):
    fo=struct.unpack_from("<I",tokb,i*16+8)[0]
    feats.append(feat[fo:feat.index(b"\0",fo)].decode("utf-8","replace").split(","))
N=len(feats); BASE=fsize; TOTAL=len(b); FLOOR=TOTAL-BASE
def blob(ss):
    u=set(ss); return sum(len(s.encode())+1 for s in u), len(u)
def keep_orig(f, kanji_any=False):
    if ":" in f[9]: return True
    o=f[6]
    if o in OK: return True
    if any(c in o for c in "々〃ゝヽゞヾ"): return True
    if kanji_any: return any(0x4E00<=ord(c)<=0x9FFF for c in o)
    return len(o)==1 and 0x4E00<=ord(o[0])<=0x9FFF
def L1z(f):
    return [f[0],f[1],("助数詞" if f[2]=="助数詞" else "*"),
            (f[4] if f[4] in CT else "*"),(f[5] if f[5] in CF else "*"),
            f[6],(f[7] if f[7] in C3 else "*"),f[8],f[9],f[10]]   # pos_group3 列削除
def L2z(f):
    g=L1z(f)
    if not keep_orig(f): g[5]="*"
    return g
def L2w(f):
    g=L1z(f)
    if not keep_orig(f,True): g[5]="*"
    return g
print(f"sys.dic={TOTAL:,}  darts+token={FLOOR:,}  feature={BASE:,}  entries={N:,}")
for nm,fn in [("L1z",L1z),("L2z",L2z),("L2w",L2w)]:
    sz,u=blob([",".join(fn(f)) for f in feats])
    print(f"{nm:<5}{sz:>12,} B  uniq={u:>8,}  feature削減 {100*(1-sz/BASE):>5.1f}%  → sys.dic {FLOOR+sz:>12,} ({100*(1-(FLOOR+sz)/TOTAL):>4.1f}% 減)  {sz/N:>5.1f} B/entry")
# binary
pron,_=blob([f[8] for f in feats])
o2=[f[6] if keep_orig(f) else "" for f in feats]; orig2,_=blob(o2)
rd=[f[7] if f[7] in C3 else "" for f in feats]; read2,_=blob(rd)
MORA=re.compile(r"[キシチニヒミリギジヂビピ][ャュョェ]|[クツフヴグ][ァィェォ]|[ウスズトドフブプヴ][ィェ]|[テデ][ィュ]|[ァ-ヴー]")
seen=set(); mp=0
for f in feats:
    p=f[8]
    if p in seen: continue
    seen.add(p); ms=MORA.findall(p)
    mp += (len(ms) if "".join(ms)==p else len(p.encode()))+1
print(f"\npron blob {pron:,} / mora-id pron {mp:,} / orig(縮約) {orig2:,} / read(class3) {read2:,}")
for nm,rec,extra in [("L3 (13B rec + pron UTF8)",13,pron+orig2+read2),
                     ("L4 (13B rec + mora-id pron)",13,mp+orig2+read2)]:
    v=N*rec+extra
    print(f"{nm:<30}{v:>12,} B  feature削減 {100*(1-v/BASE):>5.1f}%  → sys.dic {FLOOR+v:>12,}")
print(f"\n※ feature を 0 にしても darts+token で {FLOOR:,} B ({FLOOR/N:.1f} B/entry) 残る → 語彙枝刈りが必須")
