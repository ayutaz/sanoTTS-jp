"""Q1b: 「バイト単位 trie」ではなく「**文字単位 trie**」にしたらどうなるか。

見出し語は全部妥当な UTF-8 なので、終端は必ず文字境界にある。したがって
文字単位 trie の common-prefix-search はバイト単位 trie と**同じ答えを返す**はず。
これを G1b で実際に突き合わせる（そこが要点。小さいだけでは無意味）。

ゲート:
  G1b-1 文字 trie の CPS がバイト trie（= 総当り参照）と完全一致
  G1b-2 陰性対照: labels を 1 シンボル壊すと落ちる
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

from trie_common import load_surfaces, build_trie
from bitvec import make_bv

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
NS = int(sys.argv[1]) if len(sys.argv) > 1 else 400

surfaces_b = load_surfaces()
surfaces = [s.decode("utf-8") for s in surfaces_b]
chars = Counter()
for s in surfaces:
    chars.update(s)
print(f"見出し語 {len(surfaces):,d} / 異なり文字 {len(chars):,d}")
cover255 = sum(c for _, c in chars.most_common(255)) / sum(chars.values())
print(f"  上位 255 文字が全出現の {100*cover255:.2f}% を覆う")
SYMW = 2 if len(chars) <= 65536 else 4
print(f"  シンボル幅 = {SYMW} B")

cid = {c: i for i, (c, _) in enumerate(chars.most_common())}
# 文字 ID 列を「ID をビッグエンディアン 2 バイト」として bytes 化し、既存の
# バイト trie ビルダを 2 バイト単位で使い回す…のではなく、専用に組む。
keys = [np.array([cid[c] for c in s], dtype=np.int32) for s in surfaces]
keys.sort(key=lambda a: a.tobytes())          # ID 順（辞書順とは違うがソートは必要）
# ソートは「同じ接頭辞が隣り合う」ことだけが要件なので ID 順で良い

n_est = 1 + sum(len(k) for k in keys)
label = np.zeros(n_est, dtype=np.int32)
parent = np.zeros(n_est, dtype=np.int64)
term = np.zeros(n_est, dtype=np.uint8)
parent[0] = -1
n = 1
stack = [0]
prev = np.zeros(0, dtype=np.int32)
for k in keys:
    m = min(len(prev), len(k))
    lcp = 0
    while lcp < m and prev[lcp] == k[lcp]:
        lcp += 1
    del stack[lcp + 1:]
    for b in k[lcp:].tolist():
        label[n] = b
        parent[n] = stack[-1]
        stack.append(n)
        n += 1
    term[stack[-1]] = 1
    prev = k
label, parent, term = label[:n], parent[:n], term[:n]
print(f"\n文字 trie ノード = {n:,d}  （バイト trie 2,075,882 の "
      f"{100*n/2075882:.1f}%）", flush=True)

nch = np.zeros(n, dtype=np.int64)
np.add.at(nch, parent[1:], 1)
cs = np.zeros(n + 1, dtype=np.int64)
np.cumsum(nch, out=cs[1:])
fill = cs[:-1].copy()
child = np.zeros(n - 1, dtype=np.int64)
for i in range(1, n):
    p = parent[i]
    child[fill[p]] = i
    fill[p] += 1
order = np.zeros(n, dtype=np.int64)
head, tail_ = 0, 1
while head < tail_:
    v = order[head]
    head += 1
    for j in range(cs[v], cs[v + 1]):
        order[tail_] = child[j]
        tail_ += 1
assert tail_ == n
label2 = label[order]
term2 = term[order]
nch2 = nch[order]
cs2 = np.zeros(n + 1, dtype=np.int64)
np.cumsum(nch2, out=cs2[1:])

deg = nch2
bits = np.ones(2 + int(deg.sum()) + n, dtype=np.uint8)
bits[1] = 0
bits[2 + np.cumsum(deg + 1) - 1] = 0
bv, p_l = make_bv(bits, "clouds", with_select0=True)
tbv, p_t = make_bv(term2.astype(np.uint8), "cterm")
labels_b = label2.astype("<u2" if SYMW == 2 else "<u4").tobytes()
parts = {"louds.bits": p_l["bits"], "louds.sup": p_l["sup"], "louds.blk": p_l["blk"],
         "louds.sel0": p_l["sel0"], f"labels({SYMW}B)": len(labels_b),
         "term.bits": p_t["bits"], "term.sup": p_t["sup"], "term.blk": p_t["blk"],
         "char_table(utf8)": sum(len(c.encode("utf-8")) + 1 for c in cid)}
print("\n=== 文字単位 LOUDS（実バイト列）===")
for k, v in parts.items():
    print(f"  {k:20s} {v:>12,d} B")
tot = sum(parts.values())
print(f"  {'合計':20s} {tot:>12,d} B   （バイト LOUDS 3,065,175 B 比 "
      f"{tot-3065175:+,d} = {100*(tot-3065175)/3065175:+.2f}%）", flush=True)


class CharLouds:
    def __init__(self, bv, tbv, labels, w):
        self.bv, self.tbv, self.labels, self.w = bv, tbv, labels, w

    def _lab(self, c):
        return int.from_bytes(self.labels[c * self.w:(c + 1) * self.w], "little")

    def child(self, v, ch):
        s0 = self.bv.select0(v)
        s1 = self.bv.select0(v + 1)
        d = s1 - s0 - 1
        if d <= 0:
            return None
        c0 = self.bv.rank1(s0 + 1)
        for m in range(d):                    # ラベルは ID 順（昇順とは限らない）
            if self._lab(c0 + m) == ch:
                return c0 + m
        return None

    def cps(self, ids, start):
        out, v = [], 0
        for k in range(start, len(ids)):
            nx = self.child(v, ids[k])
            if nx is None:
                break
            v = nx
            if self.tbv.bit(v):
                out.append(k - start + 1)
        return out


CL = CharLouds(bv, tbv, labels_b, SYMW)

print("\n=== G1b: CPS が総当り参照と一致するか ===", flush=True)
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
t0 = time.time()
npos = nhit = badc = 0
UNK = -1
for tx in gt:
    ids = [cid.get(c, UNK) for c in tx]
    for i in range(len(tx)):
        exp = [ln for ln in range(1, min(maxlen, len(tx) - i) + 1)
               if tx[i:i + ln] in sset]
        got = CL.cps(ids, i)
        npos += 1
        nhit += len(exp)
        if got != exp:
            badc += 1
            if badc <= 3:
                print("   NG:", tx, i, got, exp)
print(f"  照合位置 {npos:,d} / 参照ヒット {nhit:,d} / 不一致 {badc}  "
      f"({time.time()-t0:.1f}s)")
G1b1 = badc == 0 and nhit > 0
print(f"  G1b-1: {'PASS' if G1b1 else 'FAIL'}", flush=True)

hitn = []
for tx in gt[:40]:
    ids = [cid.get(c, UNK) for c in tx]
    for i in range(len(tx)):
        v = 0
        for k in range(i, len(tx)):
            nx = CL.child(v, ids[k])
            if nx is None:
                break
            v = nx
            if CL.tbv.bit(v):
                hitn.append(v)
old = CL.labels
b = bytearray(old)
b[hitn[0] * SYMW] ^= 0x01
CL.labels = bytes(b)
bad2 = 0
for tx in gt[:40]:
    ids = [cid.get(c, UNK) for c in tx]
    for i in range(len(tx)):
        exp = [ln for ln in range(1, min(maxlen, len(tx) - i) + 1)
               if tx[i:i + ln] in sset]
        if CL.cps(ids, i) != exp:
            bad2 += 1
CL.labels = old
G1b2 = bad2 > 0
print(f"  G1b-2 陰性対照 labels[{hitn[0]}] 破壊 → 不一致 {bad2}  "
      f"{'PASS' if G1b2 else 'FAIL'}", flush=True)

json.dump(dict(n_chars=len(chars), cover255=cover255, sym_width=SYMW,
               n_nodes=int(n), parts=parts, total=tot,
               gates=dict(G1b_1=G1b1, G1b_2=G1b2, positions=npos, hits=nhit,
                          sentences=len(gt))),
          open(os.path.join(_WORK, "q1b.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q1b.json")
