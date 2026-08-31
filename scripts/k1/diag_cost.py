"""MeCab 自身が申告する word_cost / left_id / right_id / link_cost と
私の再構成を突き合わせて、食い違いの出どころを 1 箇所に絞る。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import struct
import sys

import numpy as np
import pyopenjtalk

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
from dump_entries_lib import load_entries

DIC = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
mraw = open(os.path.join(DIC, "matrix.bin"), "rb").read()
lsize, rsize = struct.unpack("<HH", mraw[:4])
MAT = np.frombuffer(mraw, dtype=np.int16, offset=4).reshape(rsize, lsize)
print(f"matrix header: lsize={lsize} rsize={rsize}  bytes={len(mraw):,d}")

entries = load_entries(DIC)
by = {}
for e in entries:
    by.setdefault(e[0], []).append(e)
print(f"entries={len(entries):,d}")

TEXT = "この人はなんでいつも人ごとなんだろ"
feats, morphs = pyopenjtalk.run_mecab_detailed(TEXT)

print("\n=== word_cost / left_id / right_id の一致 ===")
bad = 0
for m in morphs:
    s = m["surface"]
    cands = by.get(s, [])
    hit = [c for c in cands
           if c[1] == m["left_id"] and c[2] == m["right_id"] and c[3] == m["word_cost"]]
    ok = bool(hit)
    if not ok:
        bad += 1
    print(f"  {s:6s} mecab(lc={m['left_id']:4d} rc={m['right_id']:4d} "
          f"wc={m['word_cost']:6d} unk={m['is_unknown']})  "
          f"辞書側候補={len(cands)}  一致={ok}")
    if not ok and cands:
        for c in cands[:3]:
            print(f"        辞書: lc={c[1]:4d} rc={c[2]:4d} wc={c[3]:6d}  {c[4]}")
print(f"  一致しなかった token = {bad} / {len(morphs)}")

print("\n=== link_cost が MAT[rc_prev, lc_cur] と一致するか ===")
prev_rc = 0
mism = 0
for m in morphs:
    lc = m["left_id"]
    got = int(MAT[prev_rc, lc])
    exp = m["link_cost"]
    flag = "OK" if got == exp else "**NG**"
    if got != exp:
        mism += 1
    print(f"  {m['surface']:6s} MAT[{prev_rc:4d},{lc:4d}]={got:6d}  "
          f"mecab link_cost={exp:6d}  {flag}")
    prev_rc = m["right_id"]
print(f"  不一致 = {mism} / {len(morphs)}")

print("\n=== 転置したらどうか（陰性対照）===")
prev_rc = 0
mism_t = 0
for m in morphs:
    lc = m["left_id"]
    got = int(MAT[lc, prev_rc])
    if got != m["link_cost"]:
        mism_t += 1
    prev_rc = m["right_id"]
print(f"  転置での不一致 = {mism_t} / {len(morphs)}  "
      f"→ 正しい向きが一意に決まる: {mism == 0 and mism_t > 0}")

print("\n=== 未知語ノードの寄与 ===")
n_unk = sum(1 for m in morphs if m["is_unknown"])
print(f"  この文の未知語 token = {n_unk}")
tot_unk = tot = 0
with open((_ROOT + "/data/splits/corpus_heldout.tsv"),
          encoding="utf-8") as f:
    f.readline()
    n = 0
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) < 3:
            continue
        n += 1
        if n > 300:
            break
        _, ms = pyopenjtalk.run_mecab_detailed(p[2])
        tot += len(ms)
        tot_unk += sum(1 for x in ms if x["is_unknown"])
print(f"  held-out 300 文: token {tot:,d} / 未知語 {tot_unk:,d} "
      f"= {100*tot_unk/tot:.2f}%")
