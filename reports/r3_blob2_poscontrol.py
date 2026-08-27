"""R-3 陽性対照: 同じ探索器で、目標 blob を**うちの実測 360,864 B** にしたとき
案C（W=76,E=304,K=7,R=12,5ブロック,naff=0,LayerScale スカラ,factored head r=48）を
実際に発見できることを確かめる。見つからなければ探索器が空虚。"""
P2, CIN, OUT = 331308, 40, 1539
def build(W,E,K,R,nblk,naff,gper,head,r):
    p  = CIN*W*3 + W + nblk*(W*K) + nblk*(W*E+E) + nblk*(E*W+W)
    p += nblk*(CIN*R+R) + nblk*(R*W+W) + nblk*(W if gper else 1) + nblk*naff*2*W
    p += (W*OUT+OUT) if head=="dense" else (W*r+r+r*OUT+OUT)
    b  = W + nblk*(E+W) + nblk*(R+W) + nblk*(W if gper else 1) + nblk*naff*2*W
    b += OUT if head=="dense" else r+OUT
    c  = W + nblk*(W+E+W+R+W) + (OUT if head=="dense" else r+OUT)
    return p, b, c, (p-b)+4*b+4*c

def search(target_blob):
    hits=[]
    for W in range(24,257):
      for K in (3,5,7,9,11):
        for R in range(2,65):
          for nblk in (3,4,5,6,7,8):
            for naff in (0,1,2):
              for gper in (0,1):
                for head,rs in (("dense",[0]),("fact",range(8,161))):
                  for r in rs:
                    p0,_,_,_ = build(W,0,K,R,nblk,naff,gper,head,r)
                    per_E = 2*nblk*W + nblk
                    if (P2-p0) % per_E: continue
                    E = (P2-p0)//per_E
                    if E < W or E > 16*W: continue
                    p,b,c,blob = build(W,E,K,R,nblk,naff,gper,head,r)
                    if blob == target_blob: hits.append((W,E,K,R,nblk,naff,gper,head,r,b,c,blob))
    return hits

h = search(360864)
print(f"陽性対照 (target=360,864 B): {len(h)} 件")
for x in h[:10]:
    print("  W=%3d E=%4d K=%2d R=%2d nblk=%d naff=%d gper=%d head=%-5s r=%3d B=%d C=%d blob=%d" % x)
assert any(x[:9]==(76,304,7,12,5,0,0,"fact",48) for x in h), "案C を見つけられない探索器"
print("→ 案C を発見。探索器は空虚ではない。")

print("\n=== どの層を fp32 のまま残せば 38,680 B の差が埋まるか（Δ = 3X − 4O）===")
W,E,K,R,NB,r = 76,304,7,12,5,48
layers = {
  "inp Conv1d(40,76,k3)": (CIN*W*3, W),
  "dw ×5":               (NB*(W*K), NB*W),
  "pw1 ×5":              (NB*(W*E), NB*E),
  "pw2 ×5":              (NB*(E*W), NB*W),
  "cdown ×5":            (NB*(CIN*R), NB*R),
  "cup ×5":              (NB*(R*W), NB*W),
  "hdown":               (W*r, r),
  "hout":                (r*OUT, OUT),
}
import itertools
print(f"{'層':22s} {'weights X':>10s} {'out ch O':>9s} {'Δ=3X-4O':>10s}")
for k,(X,O) in layers.items():
    print(f"{k:22s} {X:10,d} {O:9,d} {3*X-4*O:10,d}")
GAP = 38680
best=[]
names=list(layers)
for n in range(1,len(names)+1):
    for combo in itertools.combinations(names, n):
        d = sum(3*layers[c][0]-4*layers[c][1] for c in combo)
        best.append((abs(d-GAP), d, combo))
best.sort()
print(f"\n目標 Δ = {GAP:,} に最も近い組み合わせ（上位 6）")
for a,d,combo in best[:6]:
    print(f"  |誤差|={a:6,d}  Δ={d:7,d}  {' + '.join(combo)}")
print("→ 完全一致は 0 件（最小誤差 %d B）。「一部の層を fp32 で残した」でも説明できない。" % best[0][0])
