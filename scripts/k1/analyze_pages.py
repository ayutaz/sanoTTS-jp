import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
SP=(_WORK + "")
def load(f):
    with open(os.path.join(_WORK,f),encoding="utf-8") as fh:
        h=fh.readline().rstrip("\n").split("\t")
        r=[[int(x) for x in l.rstrip("\n").split("\t")] for l in fh if l.strip()]
    return {k:[x[h.index(k)] for x in r] for k in h}, len(r)
def pc(xs,p):
    s=sorted(xs); k=(len(s)-1)*p/100.0; lo=int(k); hi=min(lo+1,len(s)-1)
    return s[lo]+(s[hi]-s[lo])*(k-lo)
def d(n,xs,u=""):
    m=len(xs); print(f"  {n:<24s} mean={sum(xs)/m:>9.2f} med={pc(xs,50):>8.1f} "
                     f"p95={pc(xs,95):>8.1f} max={max(xs):>8d} min={min(xs):>6d} {u}")

A,n=load("pg_onebest.tsv")
print(f"=== sys.dic への実アクセス (one-best, n={n}) ===")
d("64 KiB ページ (合計)",A["pages64k"],"page")
d("  うち trie",A["pg_trie"],"page")
d("  うち token 配列",A["pg_tok"],"page")
d("  うち feature 文字列",A["pg_feat"],"page")
d("32 B ライン",A["lines32b"],"line")
print(f"  32 B ライン × 32 = mean {sum(A['lines32b'])/n*32:,.0f} B / max {max(A['lines32b'])*32:,d} B")
print(f"  64 KiB ページ × 64 KiB = mean {sum(A['pages64k'])/n*65536/1024:,.0f} KiB / "
      f"max {max(A['pages64k'])*65536/1024:,.0f} KiB")

B,n2=load("pg_nbest.tsv")
print(f"\n=== MECAB_NBEST を立てた場合 (n={n2}) ===")
d("ノード数 (one-best)",A["nodes"],"node")
d("ノード数 (nbest)",B["nodes"],"node")
d("Path 数 (one-best)",A["paths"],"path")
d("**Path 数 (nbest)**",B["paths"],"path")
r=[p/nd for p,nd in zip(B["paths"],B["nodes"])]
print(f"  Path / ノード           mean={sum(r)/n2:>9.2f} med={pc(r,50):>8.2f} max={max(r):>8.2f}")
for sz,nm in ((40,"host arm64 sizeof(Path)=40 B"),(24,"ESP32-S3 sizeof(Path)=24 B")):
    print(f"  算術 {nm}: mean {sum(B['paths'])/n2*sz:>10,.0f} B / "
          f"p95 {pc(B['paths'],95)*sz:>10,.0f} B / max {max(B['paths'])*sz:>10,.0f} B")
print(f"  PATH_FREELIST_SIZE=2048 のチャンク粒度込み (max {max(B['paths'])} path):")
for sz,nm in ((40,"host"),(24,"ESP32-S3")):
    ck=-(-max(B["paths"])//2048)
    print(f"    {nm}: {ck} チャンク × 2048 × {sz} B = {ck*2048*sz:,d} B")
# ゲート: one-best と nbest でノード数が同じ (同じ lattice を見ている証拠)
same=sum(1 for a,b in zip(A["nodes"],B["nodes"]) if a==b)
print(f"\n  ゲート: one-best と nbest のノード数一致 {same}/{n} "
      f"{'PASS' if same==n else '(差あり=nbest が lattice も変える)'}")
print(f"  ゲート: one-best の Path は全文 0 -> {'PASS' if max(A['paths'])==0 else 'FAIL'}")
print(f"  陽性対照: nbest の Path は全文 >0 -> {'PASS' if min(B['paths'])>0 else 'FAIL'}")
