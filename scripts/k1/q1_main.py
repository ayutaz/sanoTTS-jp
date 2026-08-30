"""Q1: trie の tail / path 圧縮。**実バイト列を作って len() で測る。**

ゲート:
  G1-1 素 LOUDS の CPS が総当り参照と完全一致（全 677,700 見出し語）
  G1-2 tail 圧縮 LOUDS の CPS が総当り参照と完全一致（各構成）
  G1-3 陰性対照: **実際にヒットに使われた** tail のバイトを壊すと G1-2 が落ちる
  G1-4 陰性対照: **実際にヒットに使われた** labels を壊すと G1-2 が落ちる
  G1-5 ヒット件数が 0 でないこと（空虚なゲートの防止）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import sys
import time
import numpy as np
from collections import Counter

from trie_common import get_trie, load_surfaces
from tries import PlainLouds, build_patricia, TailLouds

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
NS = int(sys.argv[1]) if len(sys.argv) > 1 else 120

t = get_trie()
label, term, cs = t["label"], t["term"], t["cs"]
n = label.shape[0]
deg = (cs[1:] - cs[:-1]).astype(np.int64)
print(f"ノード {n:,d} / 終端 {int(term.sum()):,d}", flush=True)

dc = Counter(deg.tolist())
print("\n=== 次数の分布（全 2,075,882 ノード）===")
for d in sorted(dc)[:8]:
    print(f"  deg={d:<3d} {dc[d]:>10,d}  ({100*dc[d]/n:5.2f}%)")
print(f"  deg>=8  {sum(v for k, v in dc.items() if k >= 8):>10,d}")
one_child_nonterm = int(((deg == 1) & (term == 0)).sum())
print(f"\n  ★ 子が 1 つ かつ 非終端（畳める）= {one_child_nonterm:,d} "
      f"({100*one_child_nonterm/n:.2f}%)")
print(f"    子が 1 つ かつ 終端（畳めない）  = {int(((deg==1)&(term==1)).sum()):,d}",
      flush=True)

print("\n=== 素 LOUDS（実バイト列）===", flush=True)
PL = PlainLouds(label, term, cs)
pp = PL.parts()
for k, v in pp.items():
    print(f"  {k:20s} {v:>12,d} B")
plain_total = sum(pp.values())
print(f"  {'合計':20s} {plain_total:>12,d} B     (親申告の現行 LOUDS = 3,000,303 B)",
      flush=True)

# ---------------------------------------------------------------- 掃引
print("\n=== 連鎖長のしきい値 × tail 参照方式 の掃引 ===", flush=True)
rows = []
best = None
for mc in (1, 2, 3, 4, 5, 6, 8, 12):
    pat = build_patricia(label, term, cs, min_chain=mc)
    nc = pat["labels"].shape[0]
    tl = np.array([len(x) for x in pat["tails"]], dtype=np.int64)
    nz = [x for x in pat["tails"] if x]
    for dedup in (False, True):
        for mode in ("offset", "id"):
            if not dedup and mode == "id":
                continue
            TLx = TailLouds(pat, dedup=dedup, ptr_mode=mode)
            p = TLx.parts()
            tot = sum(p.values())
            rows.append(dict(min_chain=mc, dedup=dedup, mode=mode, nodes=nc,
                             tail_nodes=int((tl > 0).sum()),
                             distinct=len(set(nz)), total=tot, parts=p,
                             delta=tot - plain_total))
            if best is None or tot < best["total"]:
                best = rows[-1] | {"obj": TLx, "pat": pat}
    print(f"  min_chain={mc}: ノード {nc:>9,d} / tail ノード {int((tl>0).sum()):>8,d} "
          f"/ distinct {len(set(nz)):>7,d}", flush=True)

print(f"\n{'min_chain':>9} {'dedup':>6} {'mode':>7} {'nodes':>10} {'tailN':>9} "
      f"{'total B':>12} {'vs 素 LOUDS':>14}")
for r in rows:
    print(f"{r['min_chain']:>9} {str(r['dedup']):>6} {r['mode']:>7} {r['nodes']:>10,} "
          f"{r['tail_nodes']:>9,} {r['total']:>12,} {r['delta']:>+14,}")
print(f"\n  最小 = min_chain={best['min_chain']} / dedup={best['dedup']} / "
      f"mode={best['mode']} → {best['total']:,d} B "
      f"({best['delta']:+,d} B = {100*best['delta']/plain_total:+.2f}%)", flush=True)
print("  内訳:")
for k, v in best["parts"].items():
    if v:
        print(f"    {k:20s} {v:>12,d} B")

# ---------------------------------------------------------------- CPS ゲート
print("\n=== G1: common-prefix-search の同一性 ===", flush=True)
surfaces = load_surfaces()
sset = set(surfaces)
maxlen = max(len(s) for s in surfaces)
texts = []
with open(HELDOUT, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[2]:
            texts.append(p[2])
gtexts = texts[:NS]
print(f"  見出し語 {len(sset):,d} / 最長 {maxlen} B / 検証文 {len(gtexts)} "
      f"(held-out 全 {len(texts)} 文の先頭)", flush=True)

# 検証する構成: 素 / min_chain=1(全畳み,dedup) / 最小構成
pat1 = build_patricia(label, term, cs, min_chain=1)
CAND = [("plain", PL),
        ("tail mc=1 dedup offset", TailLouds(pat1, dedup=True, ptr_mode="offset")),
        (f"tail mc={best['min_chain']} dedup={best['dedup']} {best['mode']} (最小)",
         best["obj"])]

t1 = time.time()
npos = nhit = 0
bad = {k: 0 for k, _ in CAND}
hit_nodes = {k: [] for k, _ in CAND}
for tx in gtexts:
    kb = tx.encode("utf-8")
    for i in range(len(kb)):
        exp = [ln for ln in range(1, min(maxlen, len(kb) - i) + 1)
               if kb[i:i + ln] in sset]
        npos += 1
        nhit += len(exp)
        for k, obj in CAND:
            got = obj.common_prefix_search(kb, i)
            hit_nodes[k].extend(v for _, v in got)
            if [ln for ln, _ in got] != exp:
                bad[k] += 1
print(f"  照合位置 {npos:,d} / 参照ヒット {nhit:,d}   ({time.time()-t1:.1f}s)")
for k, _ in CAND:
    print(f"  {k:42s} 不一致 {bad[k]}  {'PASS' if bad[k]==0 else 'FAIL'}")
G11 = bad["plain"] == 0
G12 = all(v == 0 for v in bad.values())
G15 = nhit > 0
print(f"  G1-5 ヒットが 0 でない: {'PASS' if G15 else 'FAIL'} (hits={nhit:,d})", flush=True)

# ---------------------------------------------------------------- 陰性対照
print("\n=== G1-3 / G1-4 陰性対照 ===", flush=True)
TT = CAND[-1][1]
sub = gtexts[:40]


def mism(obj, ts):
    b = 0
    for tx in ts:
        kb = tx.encode("utf-8")
        for i in range(len(kb)):
            exp = [ln for ln in range(1, min(maxlen, len(kb) - i) + 1)
                   if kb[i:i + ln] in sset]
            if [ln for ln, _ in obj.common_prefix_search(kb, i)] != exp:
                b += 1
    return b


base = mism(TT, sub)
# ★ ヒットに実際に使われたノードのうち tail を持つものを壊す
hits = []
for tx in sub:
    kb = tx.encode("utf-8")
    for i in range(len(kb)):
        hits.extend(v for _, v in TT.common_prefix_search(kb, i))
tail_hits = [v for v in hits if TT.hbv.bit(v)]
print(f"  基準（無傷）の不一致 = {base}")
print(f"  ヒットしたノード {len(hits):,d} / うち tail を持つもの {len(tail_hits):,d}")
if tail_hits:
    v = tail_hits[0]
    k = TT.hbv.rank1(v)
    o = int.from_bytes(TT.ptr[k * TT.W:(k + 1) * TT.W], "little")
    if TT.mode == "id":
        o = int.from_bytes(TT.idtab[o * TT.IW:(o + 1) * TT.IW], "little")
    old = TT.pool
    bp = bytearray(old)
    bp[o + 1] ^= 0xFF                 # tail の 1 バイト目（長さバイトの次）
    TT.pool = bytes(bp)
    m3 = mism(TT, sub)
    TT.pool = old
    G13 = m3 > base
    print(f"  G1-3 ヒットに使われた tail_pool[{o+1}] を破壊 → 不一致 {m3}  "
          f"{'PASS' if G13 else 'FAIL'}")
else:
    G13 = False
    print("  G1-3 FAIL: tail 付きヒットノードが 0（ゲートが空虚）")

v = hits[0]
old = TT.labels
bl = bytearray(old)
bl[v] ^= 0x01
TT.labels = bytes(bl)
m4 = mism(TT, sub)
TT.labels = old
G14 = m4 > base
print(f"  G1-4 ヒットに使われた labels[{v}] を破壊 → 不一致 {m4}  "
      f"{'PASS' if G14 else 'FAIL'}", flush=True)

json.dump(dict(
    n_nodes=n, n_terminal=int(term.sum()),
    deg_hist={str(k): v for k, v in sorted(dc.items())[:12]},
    one_child_nonterminal=one_child_nonterm,
    one_child_terminal=int(((deg == 1) & (term == 1)).sum()),
    plain_parts=pp, plain_total=plain_total,
    sweep=[{k: v for k, v in r.items() if k != "obj"} for r in rows],
    best={k: v for k, v in best.items() if k not in ("obj", "pat")},
    gates=dict(G1_1=G11, G1_2=G12, G1_3=G13, G1_4=G14, G1_5=G15,
               positions=npos, hits=nhit, sentences=len(gtexts),
               per_variant={k: bad[k] for k, _ in CAND}),
), open(os.path.join(_WORK, "q1.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q1.json")
