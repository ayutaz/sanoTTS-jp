"""チームリードの指摘 (1) の検証: LOUDS の子ノード添字の off-by-one。

主張: 1 ビットが位置 p にあるとき、そこから辿る子ノードは rank1[p] であって
      rank1[p+1] ではない。症状は「クラッシュせず、文が 1 文字ずつに砕ける」。

やること（主張の追認ではなく実証）:
  G5-1 陽性対照: ヒットの**長さ分布**を出す。多文字の見出し語が実際に見つかって
       いるか。1 文字ヒットしか無いなら私のゲートは空虚だった
  G5-2 陰性対照: rank1(p+1) を注入して、私の総当りゲートが**落ちる**ことを見る
  G5-3 注入版の症状が「1 文字に砕ける」であることを長さ分布で確認
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import sys
from collections import Counter

import numpy as np

from trie_common import get_trie, load_surfaces
from tries import PlainLouds

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
NS = int(sys.argv[1]) if len(sys.argv) > 1 else 300

t = get_trie()
PL = PlainLouds(t["label"], t["term"], t["cs"])
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
gt = texts[:NS]
print(f"検証文 {len(gt)} / 見出し語 {len(sset):,d}", flush=True)


def sweep(obj):
    """総当り参照との不一致数と、返したヒットの長さ分布（バイト長・文字長）"""
    bad = 0
    blen = Counter()
    clen = Counter()
    nhit = 0
    for tx in gt:
        kb = tx.encode("utf-8")
        # バイト位置 -> 文字位置
        b2c = {}
        for i in range(len(tx) + 1):
            b2c[len(tx[:i].encode("utf-8"))] = i
        for i in range(len(kb)):
            exp = [ln for ln in range(1, min(maxlen, len(kb) - i) + 1)
                   if kb[i:i + ln] in sset]
            got = [ln for ln, _ in obj.common_prefix_search(kb, i)]
            if got != exp:
                bad += 1
            for ln in got:
                nhit += 1
                blen[ln] += 1
                if i in b2c and (i + ln) in b2c:
                    clen[b2c[i + ln] - b2c[i]] += 1
    return bad, blen, clen, nhit


print("\n=== G5-1 陽性対照: 私の実装 rank1(s0+1) = rank1(p) ===", flush=True)
bad0, bl0, cl0, nh0 = sweep(PL)
print(f"  総当りとの不一致 = {bad0}   ヒット総数 = {nh0:,d}")
print("  ヒットの**文字長**分布:")
tot = sum(cl0.values())
for k in sorted(cl0):
    print(f"    {k:>2d} 文字: {cl0[k]:>7,d}  ({100*cl0[k]/tot:5.2f}%)")
multi = sum(v for k, v in cl0.items() if k >= 2)
G51 = bad0 == 0 and multi > 0
print(f"  ★ 2 文字以上の見出し語ヒット = {multi:,d} / {tot:,d} "
      f"({100*multi/tot:.2f}%)")
print(f"  最長ヒット = {max(cl0)} 文字")
print(f"  G5-1: {'PASS' if G51 else 'FAIL'}", flush=True)

print("\n=== G5-2 / G5-3 陰性対照: rank1(p+1) を注入する ===", flush=True)


class OffByOne(PlainLouds):
    """リードが踏んだ実装（子ノード = rank1(p+1)）をそのまま入れる"""

    def child(self, v, ch):
        s0 = self.bv.select0(v)
        s1 = self.bv.select0(v + 1)
        deg = s1 - s0 - 1
        if deg <= 0:
            return None
        c0 = self.bv.rank1(s0 + 2)          # ★ ここだけ +1 ずらす
        lo, hi = 0, deg - 1
        while lo <= hi:
            m = (lo + hi) // 2
            L = self._label(c0 + m)
            if L == ch:
                return c0 + m
            if L < ch:
                lo = m + 1
            else:
                hi = m - 1
        return None


OB = OffByOne(t["label"], t["term"], t["cs"])
bad1, bl1, cl1, nh1 = sweep(OB)
G52 = bad1 > 0
print(f"  総当りとの不一致 = {bad1}   ヒット総数 = {nh1:,d}  "
      f"{'PASS (ゲートが落ちる)' if G52 else 'FAIL (ゲートが空虚だった)'}")
if cl1:
    tot1 = sum(cl1.values())
    print("  注入版のヒット文字長分布:")
    for k in sorted(cl1)[:6]:
        print(f"    {k:>2d} 文字: {cl1[k]:>7,d}  ({100*cl1[k]/tot1:5.2f}%)")
    m1 = sum(v for k, v in cl1.items() if k >= 2)
    print(f"  ★ 2 文字以上 = {m1:,d} / {tot1:,d} ({100*m1/tot1:.2f}%)  "
          f"（無傷版は {100*multi/tot:.2f}%）")
    G53 = (m1 / tot1) < (multi / tot)
else:
    print("  注入版はヒットを 1 件も返さない")
    G53 = True
print(f"  G5-3 「1 文字に砕ける」症状の再現: {'PASS' if G53 else 'FAIL'}", flush=True)
