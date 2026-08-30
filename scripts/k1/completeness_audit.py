"""形式の完全性監査 — 「解析器が辞書から読むもの」を全部持っているか。

サイズが小さいことより先に確かめるべきこと。落としたフィールドが 1 つでも
解析結果に効くなら、B-0 の精度を新形式に貼り付ける推論はそこで崩れる。

sys.dic の 1 エントリが持つのは:
    token 構造体 16 B = (lcAttr u16, rcAttr u16, posid u16, wcost i16,
                         feature offset u32, compound u32)
    + feature 文字列 11 列

v2 形式が持っているのは lc / rc / wcost / feature 11 列。
**posid を落としている。** それが復元できるかを測る。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
from collections import Counter, defaultdict

SP = os.path.dirname(os.path.abspath(__file__))

rows = []
with open(os.path.join(_WORK, "entries.tsv"), encoding="utf-8") as f:
    for ln in f:
        s, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t", 5)
        fs = feat.split(",")
        rows.append((s, int(lc), int(rc), int(pid), int(cost), tuple(fs[0:6]), fs))
print(f"entries = {len(rows):,d}")

# ---------------------------------------------------------------- posid
print("\n=== posid は落としてよいか ===")
a = set((r[1], r[2], r[5]) for r in rows)
b = set((r[1], r[2], r[3], r[5]) for r in rows)
print(f"  distinct (lc, rc, pos6)        = {len(a):,d}")
print(f"  distinct (lc, rc, posid, pos6) = {len(b):,d}")
print(f"  → posid は (lc,rc,pos6) から一意に決まる: {len(a) == len(b)}")

p_from_pos6 = defaultdict(set)
for r in rows:
    p_from_pos6[r[5]].add(r[3])
amb = {k: v for k, v in p_from_pos6.items() if len(v) > 1}
print(f"  distinct pos6 = {len(p_from_pos6):,d} / posid が一意でない pos6 = {len(amb)}")
if amb:
    for k, v in list(amb.items())[:5]:
        print("   例:", k, "->", sorted(v))

p_from_lc = defaultdict(set)
for r in rows:
    p_from_lc[r[1]].add(r[3])
amb2 = {k: v for k, v in p_from_lc.items() if len(v) > 1}
print(f"  distinct lc = {len(p_from_lc):,d} / posid が一意でない lc = {len(amb2)}")

print(f"  distinct posid 値 = {len(set(r[3] for r in rows)):,d} "
      f"(範囲 {min(r[3] for r in rows)}..{max(r[3] for r in rows)})")

# ---------------------------------------------------------------- compound
print("\n=== token の compound 欄 ===")
print("  ⚠️ dump_entries は compound を読み捨てている。mecab の sys.dic では")
print("     この欄は常に 0（ipadic 系では未使用）。実際に確かめる。")
import struct
import numpy as np
DIC = os.path.expanduser("~/Documents/piper-plus/build/share/open_jtalk/dic/sys.dic")
raw = open(DIC, "rb").read()
(magic, version, dtype, lexsize, lsize, rsize,
 dsize, tsize, fsize, _d) = struct.unpack("<10I", raw[:40])
tok = raw[72 + dsize: 72 + dsize + tsize]
arr = np.frombuffer(tok, dtype=np.uint32).reshape(-1, 4)
comp = arr[:, 3]
print(f"  compound 欄: distinct = {len(np.unique(comp))} / 値 = {np.unique(comp)[:5]}")
print(f"  lsize (left context 数) = {lsize} / rsize = {rsize}")

# ---------------------------------------------------------------- 同綴りの順序
print("\n=== 同じ見出し語の複数エントリの順序は保たれるか ===")
bysurf = defaultdict(list)
for i, r in enumerate(rows):
    bysurf[r[0]].append(i)
multi = {k: v for k, v in bysurf.items() if len(v) > 1}
contiguous = sum(1 for v in multi.values() if v == list(range(v[0], v[0] + len(v))))
print(f"  複数エントリを持つ見出し語 = {len(multi):,d}")
print(f"  そのうち token 配列上で連続している = {contiguous:,d} "
      f"({100*contiguous/len(multi):.2f}%)")
print("  → 連続しているなら『見出し語ごとに個数 + 開始位置』で順序ごと復元できる")

# ---------------------------------------------------------------- feature 列の使われ方
print("\n=== feature 11 列のうち、値域が小さくて表に潰せるもの ===")
names = ["pos", "pos_group1", "pos_group2", "pos_group3", "ctype", "cform",
         "orig", "read", "pron", "acc/mora", "chain_rule"]
for i, nm in enumerate(names):
    vals = set(r[6][i] for r in rows if len(r[6]) > i)
    print(f"  [{i:2d}] {nm:12s} distinct = {len(vals):>7,d}")

# ---------------------------------------------------------------- 結論
print("\n=== 監査結果 ===")
ok_posid = len(a) == len(b)
print(f"  posid: {'復元できる（クラス表に 1 列足すだけ）' if ok_posid else '⚠️ 復元できない。形式に追加が要る'}")
print(f"  compound: {'常に 0 なので保存不要' if len(np.unique(comp)) == 1 and comp[0] == 0 else '⚠️ 非ゼロあり。要確認'}")
print(f"  同綴り順序: {'保存できる' if contiguous == len(multi) else '⚠️ 一部で順序が復元できない'}")
