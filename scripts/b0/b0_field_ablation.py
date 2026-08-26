import os, sys, re, json, random
sys.path.insert(0, "/Users/s19447/Documents/piper-plus/src/python")
import pyopenjtalk

RE_PH = re.compile(r"-(?P<ph>[^+]+)\+")
RE_PR = re.compile(r"/A:(?P<a1>[\d-]+)\+(?P<a2>[0-9]+)\+(?P<a3>[0-9]+)/")

def sig(labels):
    out=[]
    for L in labels:
        m=RE_PH.search(L); p=RE_PR.search(L)
        out.append((m.group("ph") if m else "?",
                    (int(p.group("a1")),int(p.group("a2")),int(p.group("a3"))) if p else None))
    return tuple(out)

JT = [None]
def jt():
    if JT[0] is None:
        JT[0] = pyopenjtalk.OpenJTalk(dn_mecab=pyopenjtalk.OPEN_JTALK_DICT_DIR)
    return JT[0]
def reset(): JT[0]=None

def pipeline(text, mutate=None):
    j = jt()
    morphs = pyopenjtalk.run_mecab_detailed(text, jtalk=j)
    feats=[]
    for m in morphs:
        f=list(m["features"])
        if mutate: f=mutate(f)
        feats.append(",".join(f))
    njd = pyopenjtalk.run_njd_from_mecab(feats, jtalk=j)
    njd = pyopenjtalk.apply_postprocessing(text, njd, jtalk=j)
    return sig(pyopenjtalk.make_label(njd, jtalk=j))

def blank(idx, val="*"):
    def f(c):
        if len(c) >= 2+idx: c[1+idx]=val
        return c
    return f
def read_eq_pron(c):
    if len(c)>=10: c[8]=c[9]
    return c
def t_pg2(c):
    if len(c)>=4 and c[3]!="助数詞": c[3]="*"
    return c
def t_pg3(c):
    if len(c)>=5 and c[4] not in ("姓","名"): c[4]="*"
    return c
def t_ctype_keep(c):
    if len(c)>=6 and c[5] not in ("特殊・マス","特殊・ナイ"): c[5]="*"
    return c
def t_cform_keep(c):
    if len(c)>=7 and not (c[6].startswith("連用") or c[6]=="未然形"): c[6]="*"
    return c
def t_orig_keep(c):
    if len(c)>=8:
        o=c[7]
        if o not in ("れる","られる","すぎる","せる","させる","何") and "々" not in o and "〃" not in o and "ゝ" not in o and "ヽ" not in o and len(o)!=1:
            c[7]="*"
    return c
def compose(*fs):
    def g(c):
        for f in fs: c=f(c)
        return c
    return g

ABL = {
 "L0 baseline":          None,
 "ctype -> *":           blank(4),
 "cform -> *":           blank(5),
 "orig  -> *":           blank(6),
 "read  -> *":           blank(7),
 "read := pron":         read_eq_pron,
 "pos_group1 -> *":      blank(1),
 "pos_group2 -> *":      blank(2),
 "pos_group3 -> *":      blank(3),
 "chain_rule -> *":      blank(10),
 "pos_group2 keep 助数詞": t_pg2,
 "pos_group3 keep 姓/名":  t_pg3,
 "ctype keep 特殊・マス/ナイ": t_ctype_keep,
 "cform keep 連用*/未然形":  t_cform_keep,
 "orig  keep 5語+1字":     t_orig_keep,
 "L1s = 上4つの合成":         compose(t_ctype_keep,t_cform_keep,t_pg2,t_pg3,t_orig_keep),
 "L2 = L1s + read:=pron": compose(t_ctype_keep,t_cform_keep,t_pg2,t_pg3,t_orig_keep,read_eq_pron),
}

CT_KEEP={"特殊・マス","特殊・ナイ","サ変・スル"}
CF_KEEP={"連用形","連用タ接続","連用ゴザイ接続","連用テ接続","連用デ接続","連用ニ接続","未然形"}
import pyopenjtalk as _pj
ORIG_KEEP=set(["れる","られる","せる","させる","すぎる","ちゃう","なる","する","何"]) | set(_pj.MULTI_READ_KANJI_LIST)
def t_ctype2(c):
    if len(c)>=6 and c[5] not in CT_KEEP: c[5]="*"
    return c
def t_cform2(c):
    if len(c)>=7 and c[6] not in CF_KEEP: c[6]="*"
    return c
def t_orig2(c):
    if len(c)>=8:
        o=c[7]
        # 踊り字と MULTI_READ 対象・機能語だけ残す。それ以外の1字漢字も残す(同形異音判定のため)
        if o in ORIG_KEEP: return c
        if any(ch in o for ch in "々〃ゝヽゞヾ"): return c
        if len(o)==1 and 0x4E00 <= ord(o) <= 0x9FFF: return c
        c[7]="*"
    return c
ABL["ctype keep 3値"]=t_ctype2
ABL["cform keep 連用6+未然形"]=t_cform2
ABL["orig keep 機能語+単漢字+踊り字"]=t_orig2
ABL["L1x = ctype3+cform7+pg2+pg3*+read*"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),blank(7))
ABL["L2x = L1x + orig縮約"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),blank(7),t_orig2)

def t_orig3(c):
    if len(c)>=12 and ":" in c[10]: return c      # 複合エントリは orig が表層分割を担う
    return t_orig2(c)
ABL["orig keep +複合エントリ"]=t_orig3
ABL["L2y = L1x + orig(複合保護)"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),blank(7),t_orig3)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from class3 import CLASS3_READS
C3=set(CLASS3_READS)
def t_read2(c):
    if len(c)>=12 and c[8] not in C3: c[8]="*"
    return c
ABL["read keep class3読み"]=t_read2
ABL["L1z = ctype3+cform7+pg2+pg3*+read(class3)"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),t_read2)
ABL["L2z = L1z + orig(複合保護)"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),t_read2,t_orig3)

def t_orig4(c):
    if len(c)>=12:
        if ":" in c[10]: return c
        o=c[7]
        if o in ORIG_KEEP: return c
        if any(ch in o for ch in "々〃ゝヽゞヾ"): return c
        if any(0x4E00<=ord(ch)<=0x9FFF for ch in o): return c
        c[7]="*"
    return c
ABL["L2w = L1z + orig(漢字含む語は保持)"]=compose(t_ctype2,t_cform2,t_pg2,blank(3),t_read2,t_orig4)

name = sys.argv[1]; N=int(sys.argv[2])
mut = ABL[name]
lines=[l.split("\t")[2].strip() for l in open(os.environ.get("B0_CORPUS","/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/pool.tsv")) if len(l.split("\t"))>2]
random.seed(0); random.shuffle(lines); lines=lines[:N]

base=json.load(open(os.environ.get("B0_BASE","/tmp/b0_base.json"))) if name!="L0 baseline" else None
if name=="L0 baseline":
    out=[]
    for t in lines:
        out.append([[p,a] for p,a in pipeline(t)])
    json.dump(out, open(os.environ.get("B0_BASE","/tmp/b0_base.json"),"w"), ensure_ascii=False)
    # cross-check vs extract_fullcontext
    mm=sum(1 for t in lines[:300] if tuple((p,tuple(a) if a else None) for p,a in pipeline(t))
                                  != sig(pyopenjtalk.extract_fullcontext(t)))
    print(json.dumps({"name":name,"lines":len(lines),"xcheck_mismatch_300":mm}, ensure_ascii=False))
    sys.exit()

dl=0; dph=0; da=0; err=0; ex=[]
for t,b in zip(lines, base):
    bb=tuple((p, tuple(a) if a else None) for p,a in b)
    try:
        s=pipeline(t, mut)
    except Exception as e:
        err+=1; reset(); dl+=1
        if len(ex)<3: ex.append((t,"EXC:"+str(e)[:60]))
        continue
    if s!=bb:
        dl+=1
        if len(s)==len(bb):
            for (p1,a1),(p2,a2) in zip(bb,s):
                if p1!=p2: dph+=1
                if a1!=a2: da+=1
        else:
            dph+=abs(len(s)-len(bb))
        if len(ex)<3:
            d=[(i,x,y) for i,(x,y) in enumerate(zip(bb,s)) if x!=y][:2]
            ex.append((t,str(d)))
print(json.dumps({"name":name,"lines":len(lines),"diff_lines":dl,"diff_ph":dph,"diff_A":da,"errors":err,"examples":ex}, ensure_ascii=False))
