"""R-3 陽性対照（高速版）: 同じ探索ロジックで目標 blob を 360,864 B（うちの実測）に
すると案C が出ることを確かめる。W を 70..82 に絞っただけでコードパスは同一。"""
P2, CIN, OUT = 331308, 40, 1539
def build(W,E,K,R,nblk,naff,gper,head,r):
    p  = CIN*W*3 + W + nblk*(W*K) + nblk*(W*E+E) + nblk*(E*W+W)
    p += nblk*(CIN*R+R) + nblk*(R*W+W) + nblk*(W if gper else 1) + nblk*naff*2*W
    p += (W*OUT+OUT) if head=="dense" else (W*r+r+r*OUT+OUT)
    b  = W + nblk*(E+W) + nblk*(R+W) + nblk*(W if gper else 1) + nblk*naff*2*W
    b += OUT if head=="dense" else r+OUT
    c  = W + nblk*(W+E+W+R+W) + (OUT if head=="dense" else r+OUT)
    return p, b, c, (p-b)+4*b+4*c
def search(target, Ws):
    hits=[]
    for W in Ws:
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
                    assert p == P2
                    if blob == target: hits.append((W,E,K,R,nblk,naff,gper,head,r,b,c,blob))
    return hits
h = search(360864, range(70,83))
print(f"陽性対照 target=360,864 B, W∈[70,82]: {len(h)} 件")
for x in h[:10]:
    print("  W=%3d E=%4d K=%2d R=%2d nblk=%d naff=%d gper=%d head=%-5s r=%3d B=%d C=%d blob=%d" % x)
assert any(x[:9]==(76,304,7,12,5,0,0,"fact",48) for x in h), "案C を見つけられない = 空虚な探索"
print("→ 案C (W=76,E=304,K=7,R=12,5ブロック,naff=0,LayerScale スカラ,factored r=48) を発見。探索器は機能している。")
h2 = search(399544, range(70,83))
print(f"同じ探索器で target=399,544 B, W∈[70,82]: {len(h2)} 件")
