"""matrix.bin の索引の張り方を、推測ではなく実測で決める。

MeCab が申告する node_cost の差分から真の遷移コストが逆算できる:
    node_cost[k] = node_cost[k-1] + link_cost[k]
    link_cost[k] = trans(rc[k-1], lc[k]) + word_cost[k]
    → trans(rc[k-1], lc[k]) = link_cost[k] - word_cost[k]
この (rc, lc, trans) の三つ組を大量に集めて、どの索引式なら全部説明できるかを試す。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import struct

import numpy as np
import pyopenjtalk

DIC = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
mraw = open(os.path.join(DIC, "matrix.bin"), "rb").read()
h0, h1 = struct.unpack("<HH", mraw[:4])
flat16 = np.frombuffer(mraw, dtype=np.int16, offset=4)
flat16u = np.frombuffer(mraw, dtype=np.uint16, offset=4)
print(f"header = ({h0}, {h1})  int16 要素数 = {flat16.size:,d} = {h0}*{h1} ? "
      f"{flat16.size == h0*h1}")

# --- 観測を集める ---
texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"),
          encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
obs = []   # (rc_prev, lc_cur, trans)
for t in texts[:120]:
    _, ms = pyopenjtalk.run_mecab_detailed(t)
    if not ms:
        continue
    prev_rc = 0
    prev_node = 0
    for m in ms:
        trans = m["link_cost"] - m["word_cost"]
        obs.append((prev_rc, m["left_id"], trans))
        prev_rc = m["right_id"]
print(f"観測した (rc, lc, trans) = {len(obs):,d} 組")
print("  例:", obs[:5])

N = h0
cands = {
    "flat[lc + N*rc]":      lambda rc, lc: flat16[lc + N * rc],
    "flat[rc + N*lc]":      lambda rc, lc: flat16[rc + N * lc],
    "flat[lc + N*rc] u16":  lambda rc, lc: flat16u[lc + N * rc],
    "flat[(lc-1)+N*(rc-1)]": lambda rc, lc: flat16[(lc - 1) + N * (rc - 1)]
                             if lc >= 1 and rc >= 1 else None,
}
print("\n=== どの索引式が観測を説明するか ===")
for name, fn in cands.items():
    ok = 0
    tot = 0
    for rc, lc, tr in obs:
        try:
            v = fn(rc, lc)
        except Exception:
            v = None
        if v is None:
            continue
        tot += 1
        if int(v) == tr:
            ok += 1
    print(f"  {name:24s} {ok:,d} / {tot:,d} = {100*ok/max(tot,1):.2f}%")

# --- 説明できないなら、値がどこにあるのかを直接探す ---
print("\n=== 逆引き: 最初の 3 観測の trans がフラット配列のどこにあるか ===")
for rc, lc, tr in obs[:3]:
    where = np.flatnonzero(flat16 == tr)
    print(f"  rc={rc} lc={lc} trans={tr}: 出現 {where.size:,d} 箇所")
    if where.size:
        for w in where[:6]:
            print(f"     idx={w}  → (idx//N, idx%N) = ({w//N}, {w%N})  "
                  f"(idx%N, idx//N) = ({w%N}, {w//N})")

# --- link_cost の定義自体を検証する ---
print("\n=== link_cost / node_cost の関係を確認 ===")
_, ms = pyopenjtalk.run_mecab_detailed("この人はなんでいつも人ごとなんだろ")
acc = 0
for m in ms:
    acc_new = acc + m["link_cost"]
    print(f"  {m['surface']:6s} wc={m['word_cost']:6d} link={m['link_cost']:6d} "
          f"node={m['node_cost']:6d}  累積={acc_new:6d}  一致={acc_new == m['node_cost']}")
    acc = m["node_cost"]
