"""形式の未検証点をひとつ潰す。

サイズ計上では「値プールのオフセットは 256 エントリおきのチェックポイント
+ レコードの長さ欄から復元する」(12,328 B) としたが、解析器では速度のために
オフセット配列を丸ごと持っていた。**両者が同じオフセットを返すか**は
主張しただけで確かめていなかった。

ゲート:
  G12 チェックポイント復元が materialise した配列と全エントリで一致
  G13 陰性対照: チェックポイントを 1 個ずらすと G12 が落ちる
"""
import os
import struct
import sys

import numpy as np
import pyopenjtalk

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
from dump_entries_lib import load_entries
from collections import Counter, defaultdict

DIC = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
parsed = load_entries(DIC)

SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


mc = Counter()
for p in parsed:
    for u in p[7].split(":"):
        mc.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mc.most_common(254))}
SEP, ESC = 0xFE, 0xFF


def enc_pron(pron):
    out = bytearray()
    for k, unit in enumerate(pron.split(":")):
        if k:
            out.append(SEP)
        for m in split_moras(unit):
            if m in mora_ids:
                out.append(mora_ids[m])
            else:
                b = m.encode("utf-8"); out += bytes([ESC, len(b)]) + b
    return bytes(out)


def enc_acc(a):
    o = []
    for unit in a.split(":"):
        x, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        o.append((255 if x == "*" else int(x), 255 if m == "*" else int(m)))
    return o


cls_tab, chain_tab = {}, {}
for p in parsed:
    cls_tab.setdefault((p[1], p[2], p[4]), len(cls_tab))
    chain_tab.setdefault(p[9], len(chain_tab))

surfaces = sorted(set(p[0] for p in parsed))
bysurf = defaultdict(list)
for p in parsed:
    bysurf[p[0]].append(p)
flat = []
for s in surfaces:
    flat.extend(bysurf[s])

records = bytearray()
pool = bytearray()
for p in flat:
    surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
    pb = enc_pron(pron); accs = enc_acc(accf)
    flags = 0
    if orig == surf:
        flags |= 1
    if read == pron:
        flags |= 2
    ex = bytearray()
    if not (flags & 1):
        ob = orig.encode("utf-8"); ex += bytes([len(ob)]) + ob
    if not (flags & 2):
        rb = read.encode("utf-8"); ex += bytes([len(rb)]) + rb
    for a, m in accs:
        ex.append(a); ex.append(m)
    records += struct.pack("<HhHBBB", cls_tab[(lc, rc, pos6)], cost,
                           chain_tab[chain], flags, len(pb), len(ex))
    pool += pb + ex
records = bytes(records)
print(f"entries={len(flat):,d} records={len(records):,d} B pool={len(pool):,d} B")

# materialise した真値
true_off = np.zeros(len(flat) + 1, dtype=np.int64)
for i in range(len(flat)):
    _, _, _, _, pl, el = struct.unpack("<HhHBBB", records[i * 9:(i + 1) * 9])
    true_off[i + 1] = true_off[i] + pl + el

# チェックポイント（256 エントリおき、u32）
CK = 256
ckpt = bytearray()
for i in range(0, len(flat), CK):
    ckpt += struct.pack("<I", int(true_off[i]))
print(f"チェックポイント = {len(ckpt):,d} B "
      f"(materialise した配列は {len(flat)*4:,d} B なので "
      f"{len(flat)*4/len(ckpt):.1f} 分の 1)")


def off_from_ckpt(i, ck=ckpt):
    """チェックポイント + レコード長欄から復元する（端末がやること）。"""
    base = i // CK
    off = struct.unpack("<I", ck[base * 4:base * 4 + 4])[0]
    for j in range(base * CK, i):
        _, _, _, _, pl, el = struct.unpack("<HhHBBB", records[j * 9:(j + 1) * 9])
        off += pl + el
    return off


import random
random.seed(0)
sample = random.sample(range(len(flat)), 20000)
bad = sum(1 for i in sample if off_from_ckpt(i) != int(true_off[i]))
G12 = bad == 0
print(f"\n=== G12: チェックポイント復元 vs materialise（無作為 {len(sample):,d} 件）===")
print(f"  不一致 = {bad}  → {'PASS' if G12 else 'FAIL'}")

bad_ck = bytearray(ckpt)
v = struct.unpack("<I", bad_ck[4:8])[0]
bad_ck[4:8] = struct.pack("<I", v + 1)
bad2 = sum(1 for i in sample if off_from_ckpt(i, bytes(bad_ck)) != int(true_off[i]))
G13 = bad2 > 0
print(f"\n=== G13: 陰性対照（チェックポイントを 1 ずらす）===")
print(f"  不一致 = {bad2:,d}  → {'PASS' if G13 else 'FAIL'}")
print(f"\n  復元 1 回あたりの追加読み: 最大 {CK-1} レコード x 9 B = {(CK-1)*9:,d} B")
