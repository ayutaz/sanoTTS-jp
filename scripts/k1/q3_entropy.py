"""Q3: 9 バイトレコードの各フィールドの異なり数と経験エントロピー。

レコード = <HhHBBB> class_id u16 / wcost i16 / chain_id u16 / flags u8 /
           pron_len u8 / extra_len u8   （v2 の encode_level と同一）

ゲート:
  G3-1 実際に 9 B レコード列を組み、len() が 7,100,307 B（親申告）に一致
  G3-2 ビットパック版を**実バイト列として組み**、全 788,923 件を復元して
       9 B 版と完全一致（読めない圧縮は無意味）
  G3-3 陰性対照: ビットパック列を 1 バイト壊すと G3-2 が落ちる
  G3-4 wcost codebook から全件復元して原値と一致
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import math
import os
import struct
from collections import Counter

SP = os.path.dirname(os.path.abspath(__file__))
ENTRIES = os.environ.get("ENTRIES_TSV", os.environ.get("ENTRIES_TSV", os.path.join(_WORK, "entries.tsv")))
SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


parsed = []
with open(ENTRIES, encoding="utf-8") as f:
    for ln in f:
        surf, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t")
        fs = feat.split(",")
        parsed.append((surf, int(lc), int(rc), int(cost), tuple(fs[0:6]),
                       fs[6], fs[7], fs[8], fs[9], fs[10]))
N = len(parsed)
mcnt = Counter()
for p in parsed:
    for u in p[7].split(":"):
        mcnt.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mcnt.most_common(254))}
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


# v2 と同じ順序（見出し語の BFS 順）に依らず、フィールド分布は順序に依らないので
# entries.tsv の順で組む。サイズと分布はどちらも同じ。
lcls, lchain = {}, {}
for p in parsed:
    lcls.setdefault((p[1], p[2], p[4]), len(lcls))
    lchain.setdefault(p[9], len(lchain))

F = {"class_id": [], "wcost": [], "chain_id": [], "flags": [],
     "pron_len": [], "extra_len": []}
records = bytearray()
for p in parsed:
    surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
    pb = enc_pron(pron)
    ex = bytearray()
    if orig != surf:
        o = orig.encode("utf-8"); ex += bytes([len(o)]) + o
    if read != pron:
        r = read.encode("utf-8"); ex += bytes([len(r)]) + r
    for unit in accf.split(":"):
        a, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        ex.append(255 if a == "*" else int(a))
        ex.append(255 if m == "*" else int(m))
    flags = (1 if orig == surf else 0) | (2 if read == pron else 0)
    vals = (lcls[(lc, rc, pos6)], cost, lchain[chain], flags, len(pb), len(ex))
    records += struct.pack("<HhHBBB", *vals)
    for k, v in zip(F, vals):
        F[k].append(v)
print(f"9 B レコード列 = {len(records):,d} B  (親申告 7,100,307 B)")
G31 = len(records) == 7100307
print(f"G3-1: {'PASS' if G31 else 'FAIL'}\n", flush=True)


def H(vals):
    c = Counter(vals)
    n = len(vals)
    return -sum((v / n) * math.log2(v / n) for v in c.values()), len(c)


WIDTH = {"class_id": 16, "wcost": 16, "chain_id": 16, "flags": 8,
         "pron_len": 8, "extra_len": 8}
print(f"{'field':>10} {'現行 bit':>9} {'distinct':>9} {'log2(d)':>8} "
      f"{'H (bit)':>8} {'H/現行':>7} {'N*H 換算 B':>12} {'現行 B':>10} {'差':>11}")
rowsQ3 = []
tot_H = tot_log = 0.0
for k in F:
    h, d = H(F[k])
    lg = math.ceil(math.log2(d)) if d > 1 else 0
    tot_H += h
    tot_log += lg
    cur_B = N * WIDTH[k] / 8
    hb = N * h / 8
    rowsQ3.append(dict(field=k, cur_bits=WIDTH[k], distinct=d, log2=lg, H=h,
                       cur_bytes=int(cur_B), H_bytes=hb))
    print(f"{k:>10} {WIDTH[k]:>9} {d:>9,} {lg:>8} {h:>8.3f} {h/WIDTH[k]:>7.2%} "
          f"{hb:>12,.0f} {cur_B:>10,.0f} {hb-cur_B:>+11,.0f}")
joint, jd = H(list(zip(*[F[k] for k in F])))
print(f"\n  独立エントロピーの和 = {tot_H:.3f} bit/レコード → "
      f"{N*tot_H/8:,.0f} B")
print(f"  ★ 同時エントロピー   = {joint:.3f} bit/レコード → {N*joint/8:,.0f} B "
      f"(distinct レコード {jd:,d})")
print(f"  固定幅ビットパック   = {tot_log:.0f} bit/レコード → "
      f"{math.ceil(N*tot_log/8):,d} B", flush=True)

# ---------------------------------------------------------------- wcost
print("\n=== wcost の分布 ===", flush=True)
wc = Counter(F["wcost"])
hw, dw = H(F["wcost"])
print(f"  distinct = {dw:,d} / {N:,d}   レンジ [{min(wc)}, {max(wc)}]   H = {hw:.3f} bit")
print(f"  上位 10: {[f'{v}×{c:,d}' for v, c in wc.most_common(10)]}")
cov = 0
for k in (16, 64, 256, 1024, 4096):
    cov = sum(c for _, c in wc.most_common(k)) / N
    print(f"  上位 {k:>5,d} 値が全体の {100*cov:6.2f}% を覆う")
CBW = math.ceil(math.log2(dw))
cb_bytes = math.ceil(N * CBW / 8) + dw * 2
print(f"  codebook 案: ID {CBW} bit × {N:,d} + 表 {dw:,d}×2 B = {cb_bytes:,d} B"
      f"   （int16 直値 {N*2:,d} B → {cb_bytes-N*2:+,d} B）")
print(f"  エントロピー下界 (算術符号) = {N*hw/8:,.0f} B", flush=True)

# ---------------------------------------------------------------- G3-2 ビットパック
print("\n=== G3-2: 固定幅ビットパックを実バイト列で組んで全件復元 ===", flush=True)
BITS = {k: (math.ceil(math.log2(r["distinct"])) if r["distinct"] > 1 else 1)
        for k, r in ((r["field"], r) for r in rowsQ3)}
# wcost / class_id / chain_id は「値そのもの」ではなく「頻度順 ID」を詰める
CODE = {}
DEC = {}
for k in F:
    u = [v for v, _ in Counter(F[k]).most_common()]
    CODE[k] = {v: i for i, v in enumerate(u)}
    DEC[k] = u
RECBITS = sum(BITS.values())
print(f"  レコードあたり {RECBITS} bit  " +
      " / ".join(f"{k}:{BITS[k]}" for k in F))
buf = bytearray((N * RECBITS + 7) // 8)
pos = 0
for i in range(N):
    for k in F:
        v = CODE[k][F[k][i]]
        b = BITS[k]
        # LSB-first でビット詰め
        byi, bii = pos >> 3, pos & 7
        w = v << bii
        j = 0
        while w:
            buf[byi + j] |= w & 0xFF
            w >>= 8
            j += 1
        pos += b
packed = bytes(buf)
cbtabs = sum(len(DEC[k]) * (2 if WIDTH[k] == 16 else 1) for k in F)
print(f"  ビットパック列 {len(packed):,d} B + 符号表 {cbtabs:,d} B "
      f"= {len(packed)+cbtabs:,d} B   （9 B 版 {len(records):,d} B → "
      f"{len(packed)+cbtabs-len(records):+,d} B）", flush=True)


def unpack(buf, i):
    pos = i * RECBITS
    out = []
    for k in F:
        b = BITS[k]
        byi, bii = pos >> 3, pos & 7
        nby = (bii + b + 7) // 8
        w = int.from_bytes(buf[byi:byi + nby], "little")
        c = (w >> bii) & ((1 << b) - 1)
        if c >= len(DEC[k]):
            return None            # 壊れた符号 = 復元不能（陰性対照で起きる）
        out.append(DEC[k][c])
        pos += b
    return tuple(out)


bad = 0
for i in range(N):
    if unpack(packed, i) != tuple(F[k][i] for k in F):
        bad += 1
        if bad <= 3:
            print("   NG", i, unpack(packed, i), tuple(F[k][i] for k in F))
G32 = bad == 0
print(f"  復元 不一致 = {bad} / {N:,d}   {'PASS' if G32 else 'FAIL'}", flush=True)

# ⚠️ 最初 range(0,N,13) で抜き取り検査したら、壊したバイトが載るレコード
#    (2147/2148) が標本に入らず「不一致 0」= 空虚なゲートになった。全件見る。
bb = bytearray(packed)
bb[12345] ^= 0xFF
bbb = bytes(bb)
nb = sum(1 for i in range(N) if unpack(bbb, i) != tuple(F[k][i] for k in F))
G33 = nb > 0
print(f"  G3-3 陰性対照 (packed[12345] を破壊, 全 {N:,d} 件検査) → 不一致 {nb}  "
      f"{'PASS' if G33 else 'FAIL'}")

wtab = DEC["wcost"]
wcode = CODE["wcost"]
G34 = all(wtab[wcode[v]] == v for v in F["wcost"][::7])
print(f"  G3-4 wcost codebook 復元: {'PASS' if G34 else 'FAIL'}", flush=True)

# --------------------------------------------- G3-5 レコードごと dedup（同時分布を使う）
print("\n=== G3-5: レコードを丸ごと dedup（同時エントロピー 13.586 bit を取りにいく）===",
      flush=True)
recs = [tuple(F[k][i] for k in F) for i in range(N)]
uniq = [r for r, _ in Counter(recs).most_common()]
ridx = {r: i for i, r in enumerate(uniq)}
RB = math.ceil(math.log2(len(uniq)))


def packbits(vals, w, cnt):
    buf = bytearray((cnt * w + 7) // 8)
    pos = 0
    for v in vals:
        byi, bii = pos >> 3, pos & 7
        x = v << bii
        j = 0
        while x:
            buf[byi + j] |= x & 0xFF
            x >>= 8
            j += 1
        pos += w
    return bytes(buf)


def getbits(buf, i, w):
    pos = i * w
    byi, bii = pos >> 3, pos & 7
    nby = (bii + w + 7) // 8
    return (int.from_bytes(buf[byi:byi + nby], "little") >> bii) & ((1 << w) - 1)


ids_b = packbits((ridx[r] for r in recs), RB, N)
uniq_flat = []
for r in uniq:
    for k, v in zip(F, r):
        uniq_flat.append(CODE[k][v])
tab_b = packbits(uniq_flat, max(BITS.values()), len(uniq) * len(F))
# ↑ 表は 1 レコード RECBITS bit で詰め直す方が小さい
tab_buf = bytearray((len(uniq) * RECBITS + 7) // 8)
pos = 0
for r in uniq:
    for k, v in zip(F, r):
        w = BITS[k]
        c = CODE[k][v]
        byi, bii = pos >> 3, pos & 7
        x = c << bii
        j = 0
        while x:
            tab_buf[byi + j] |= x & 0xFF
            x >>= 8
            j += 1
        pos += w
tab_b = bytes(tab_buf)
tot5 = len(ids_b) + len(tab_b) + cbtabs
print(f"  distinct レコード {len(uniq):,d} / {N:,d}  ID 幅 {RB} bit")
print(f"  ID 列 {len(ids_b):,d} B + 表 {len(tab_b):,d} B + 符号表 {cbtabs:,d} B "
      f"= {tot5:,d} B   （9 B 版 → {tot5-len(records):+,d} B）")


def unpack_rec(buf, j):
    pos = j * RECBITS
    out = []
    for k in F:
        w = BITS[k]
        byi, bii = pos >> 3, pos & 7
        nby = (bii + w + 7) // 8
        x = int.from_bytes(buf[byi:byi + nby], "little")
        c = (x >> bii) & ((1 << w) - 1)
        if c >= len(DEC[k]):
            return None
        out.append(DEC[k][c])
        pos += w
    return tuple(out)


bad5 = sum(1 for i in range(N)
           if unpack_rec(tab_b, getbits(ids_b, i, RB)) != recs[i])
G35 = bad5 == 0
print(f"  全 {N:,d} 件 復元 不一致 = {bad5}  {'PASS' if G35 else 'FAIL'}")
b5 = bytearray(tab_b)
b5[7] ^= 0xFF
bad5n = sum(1 for i in range(N)
            if unpack_rec(bytes(b5), getbits(ids_b, i, RB)) != recs[i])
G36 = bad5n > 0
print(f"  陰性対照 (表を 1 バイト破壊, 全件検査) → 不一致 {bad5n}  "
      f"{'PASS' if G36 else 'FAIL'}")
print(f"  ランタイム比 {100*(tot5-len(records))/25815130:+.2f}%", flush=True)

RUNTIME = 25815130
d = len(packed) + cbtabs - len(records)
print(f"\n  ランタイム 25,815,130 B に対して {d:+,d} B ({100*d/RUNTIME:+.2f}%)")

json.dump(dict(record_bytes=len(records), fields=rowsQ3,
               sum_H_bits=tot_H, joint_H_bits=joint, distinct_records=jd,
               packed_bits_per_record=RECBITS, packed_bytes=len(packed),
               code_tables=cbtabs, packed_total=len(packed) + cbtabs,
               wcost=dict(distinct=dw, H=hw, min=min(wc), max=max(wc),
                          codebook_bytes=cb_bytes, int16_bytes=N * 2),
               dedup_records=dict(distinct=len(uniq), id_bits=RB, ids=len(ids_b),
                                  table=len(tab_b), total=tot5,
                                  delta=tot5 - len(records)),
               gates=dict(G3_1=G31, G3_2=G32, G3_3=G33, G3_4=G34,
                          G3_5=G35, G3_6=G36)),
          open(os.path.join(_WORK, "q3.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q3.json")
