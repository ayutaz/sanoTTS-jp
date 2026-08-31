"""Q2: value pool の dedup と front coding。**実バイト列を作って len() で測る。**

現行 pool = 各エントリの (pron バイト列 + extra) を単に連結したもの。
extra = [orig 例外] [read 例外] [acc の (a,m) 対 × ユニット数]

ゲート:
  G2-1 現行 pool を組み直すと親申告の 11,465,780 B に一致する（再現性）
  G2-2 dedup / front-coded から**全 788,923 エントリの pron を復元**して原文と一致
  G2-3 陰性対照: front-coded プールを 1 バイト壊すと G2-2 が落ちる
  G2-4 陰性対照: 同じデコーダに「dedup していないプール」を食わせると落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import struct
from collections import Counter, defaultdict

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
        assert len(fs) == 11
        parsed.append((surf, int(lc), int(rc), int(cost), tuple(fs[0:6]),
                       fs[6], fs[7], fs[8], fs[9], fs[10]))
N = len(parsed)
print(f"entries = {N:,d}", flush=True)

mora_count = Counter()
for p in parsed:
    for u in p[7].split(":"):
        mora_count.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mora_count.most_common(254))}
inv_mora = {v: k for k, v in mora_ids.items()}
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


def dec_pron(b):
    units, i = [[]], 0
    while i < len(b):
        c = b[i]
        if c == SEP:
            units.append([]); i += 1
        elif c == ESC:
            n = b[i + 1]; units[-1].append(b[i + 2:i + 2 + n].decode("utf-8")); i += 2 + n
        else:
            units[-1].append(inv_mora[c]); i += 1
    return ":".join("".join(u) for u in units)


# ---------------------------------------------------------------- 現行 pool を再構成
prons, extras = [], []
for p in parsed:
    surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
    pb = enc_pron(pron)
    ex = bytearray()
    compound = ":" in accf
    if orig != surf:
        ob = orig.encode("utf-8"); ex += bytes([len(ob)]) + ob
    if read != pron:
        rb = read.encode("utf-8"); ex += bytes([len(rb)]) + rb
    for unit in accf.split(":"):
        a, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        ex.append(255 if a == "*" else int(a))
        ex.append(255 if m == "*" else int(m))
    prons.append(pb)
    extras.append(bytes(ex))

pool_now = b"".join(a + b for a, b in zip(prons, extras))
PRON_B = sum(len(x) for x in prons)
EXTRA_B = sum(len(x) for x in extras)
print(f"\n=== 現行 value pool の内訳（実ビルド）===")
print(f"  pron  {PRON_B:>12,d} B  ({100*PRON_B/len(pool_now):5.2f}%)")
print(f"  extra {EXTRA_B:>12,d} B  ({100*EXTRA_B/len(pool_now):5.2f}%)")
n_orig_ex = sum(1 for p in parsed if p[5] != p[0])
n_read_ex = sum(1 for p in parsed if p[6] != p[7])
acc_b = sum(2 * (p[8].count(":") + 1) for p in parsed)
print(f"    うち acc          {acc_b:>12,d} B  (エントリあたり "
      f"{acc_b/N:.2f} B)")
print(f"    うち orig/read 例外 {EXTRA_B-acc_b:>12,d} B  "
      f"(orig 例外 {n_orig_ex:,d} 件 / read 例外 {n_read_ex:,d} 件)")
print(f"  合計  {len(pool_now):>12,d} B     (親申告 11,465,780 B)")
G21 = len(pool_now) == 11465780
print(f"  G2-1 現行 pool の再現: {'PASS' if G21 else 'FAIL'}", flush=True)

# ---------------------------------------------------------------- distinct
dp = Counter(prons)
de = Counter(extras)
dpe = Counter(zip(prons, extras))
print(f"\n=== 異なり数 ===")
print(f"  distinct pron          {len(dp):>10,d} / {N:,d} ({100*len(dp)/N:5.2f}%)")
print(f"  distinct extra         {len(de):>10,d} / {N:,d} ({100*len(de)/N:5.2f}%)")
print(f"  distinct (pron,extra)  {len(dpe):>10,d} / {N:,d} ({100*len(dpe)/N:5.2f}%)")
print(f"  distinct pron の総バイト  {sum(len(k) for k in dp):>10,d} B "
      f"(現行 {PRON_B:,d} B の {100*sum(len(k) for k in dp)/PRON_B:.2f}%)", flush=True)


def W(x):
    return max(1, (max(1, x - 1).bit_length() + 7) // 8)


# ---------------------------------------------------------------- 案 A: pron だけ dedup
print("\n=== 案 A: pron を exact dedup（エントリごとにポインタ）===", flush=True)
uniq = sorted(dp)
poolA = b"".join(uniq)
offA = {}
o = 0
for u in uniq:
    offA[u] = o
    o += len(u)
WA = W(len(poolA))
ptrA = b"".join(offA[p].to_bytes(WA, "little") for p in prons)
# pron_len は既存 9B レコードに入っているのでポインタだけ足せば長さは要らない
totA = len(poolA) + len(ptrA) + EXTRA_B
print(f"  pron pool  {len(poolA):>12,d} B")
print(f"  ptr ({WA}B)  {len(ptrA):>12,d} B   ({N:,d} エントリ × {WA} B)")
print(f"  extra      {EXTRA_B:>12,d} B  （据え置き）")
print(f"  合計       {totA:>12,d} B   現行比 {totA-len(pool_now):+,d} B "
      f"({100*(totA-len(pool_now))/len(pool_now):+.2f}%)", flush=True)

# ---------------------------------------------------------------- 案 B: (pron,extra) dedup
print("\n=== 案 B: (pron, extra) 組を丸ごと dedup ===", flush=True)
uniqB = sorted(dpe)
poolB = b"".join(a + b for a, b in uniqB)
offB = {}
o = 0
for a, b in uniqB:
    offB[(a, b)] = o
    o += len(a) + len(b)
WB = W(len(poolB))
ptrB = b"".join(offB[k].to_bytes(WB, "little") for k in zip(prons, extras))
totB = len(poolB) + len(ptrB)
print(f"  pool       {len(poolB):>12,d} B")
print(f"  ptr ({WB}B)  {len(ptrB):>12,d} B")
print(f"  合計       {totB:>12,d} B   現行比 {totB-len(pool_now):+,d} B "
      f"({100*(totB-len(pool_now))/len(pool_now):+.2f}%)", flush=True)

# ---------------------------------------------------------------- 案 C: front coding
print("\n=== 案 C: distinct pron を front coding（K 個ごとに再スタート）===", flush=True)
uid = {u: j for j, u in enumerate(uniq)}
pron_id = [uid[p] for p in prons]
rowsC = []
for K in (2, 4, 8, 16, 32, 64):
    body = bytearray()
    bstart = []
    prev = b""
    for i, u in enumerate(uniq):
        if i % K == 0:
            bstart.append(len(body))
            prev = b""
        c = 0
        while c < min(len(prev), len(u)) and prev[c] == u[c] and c < 255:
            c += 1
        suf = u[c:]
        assert len(suf) < 256
        body += bytes([c, len(suf)]) + suf
        prev = u
    WC = W(len(body))
    bo = b"".join(x.to_bytes(WC, "little") for x in bstart)
    WI = W(len(uniq))
    idx = b"".join(i.to_bytes(WI, "little") for i in pron_id)
    tot = len(body) + len(bo) + len(idx) + EXTRA_B
    rowsC.append(dict(K=K, body=len(body), bucket_off=len(bo), idx=len(idx),
                      total=tot, delta=tot - len(pool_now)))
    print(f"  K={K:<3d} body {len(body):>10,d} + bucket_off {len(bo):>8,d} "
          f"+ pron_id({WI}B) {len(idx):>10,d} + extra {EXTRA_B:>10,d} "
          f"= {tot:>11,d} B  ({tot-len(pool_now):+,d})", flush=True)
bestC = min(rowsC, key=lambda r: r["total"])

# ---------------------------------------------------------------- 案 D: dedup 無し front coding
print("\n=== 案 D: dedup 無しで全 788,923 件をソートして front coding ===", flush=True)
srt = sorted(range(N), key=lambda i: prons[i])
bodyD = bytearray()
bstartD = []
KD = 16
prev = b""
posD = [0] * N
for r, i in enumerate(srt):
    if r % KD == 0:
        bstartD.append(len(bodyD))
        prev = b""
    u = prons[i]
    c = 0
    while c < min(len(prev), len(u)) and prev[c] == u[c] and c < 255:
        c += 1
    bodyD += bytes([c, len(u) - c]) + u[c:]
    prev = u
WD = W(len(bodyD))
boD = b"".join(x.to_bytes(WD, "little") for x in bstartD)
WRD = W(N)
rankD = b"".join(r.to_bytes(WRD, "little") for r in
                 (lambda inv: [inv[i] for i in range(N)])(
                     {i: r for r, i in enumerate(srt)}))
totD = len(bodyD) + len(boD) + len(rankD) + EXTRA_B
print(f"  body {len(bodyD):,d} + bucket_off {len(boD):,d} + rank({WRD}B) "
      f"{len(rankD):,d} + extra {EXTRA_B:,d} = {totD:,d} B  "
      f"({totD-len(pool_now):+,d})", flush=True)

# ---------------------------------------------------------------- G2-2 復元検査
print("\n=== G2-2: dedup / front-coded から全エントリの pron を復元 ===", flush=True)
KB = bestC["K"]
bodyB = bytearray()
bstartB = []
prev = b""
for i, u in enumerate(uniq):
    if i % KB == 0:
        bstartB.append(len(bodyB))
        prev = b""
    c = 0
    while c < min(len(prev), len(u)) and prev[c] == u[c] and c < 255:
        c += 1
    bodyB += bytes([c, len(u) - c]) + u[c:]
    prev = u
bodyB = bytes(bodyB)


def fc_get(j, body, bstart, K):
    """front-coded プールから j 番目の文字列をバイト配列だけから復元"""
    o = bstart[j // K]
    cur = b""
    for _ in range(j % K + 1):
        c, l = body[o], body[o + 1]
        cur = cur[:c] + body[o + 2:o + 2 + l]
        o += 2 + l
    return cur


# (a) プール全体を順次復号して distinct 集合と突き合わせる（O(total)）
dec, o, cur, j = [], 0, b"", 0
while o < len(bodyB):
    if j % KB == 0:
        cur = b""
    c, l = bodyB[o], bodyB[o + 1]
    cur = cur[:c] + bodyB[o + 2:o + 2 + l]
    dec.append(cur)
    o += 2 + l
    j += 1
bad = sum(1 for a, b in zip(dec, uniq) if a != b) + abs(len(dec) - len(uniq))
# (b) ランダムアクセス（bucket 再スタート）が効いているかを標本で
import random
random.seed(0)
smp = random.sample(range(N), 20000)
bad_ra = sum(1 for i in smp if fc_get(pron_id[i], bodyB, bstartB, KB) != prons[i])
G22a = bad == 0 and bad_ra == 0
print(f"  front-coded 順次復号 不一致 = {bad} / {len(uniq):,d}")
print(f"  front-coded ランダムアクセス 不一致 = {bad_ra} / {len(smp):,d}")
print(f"  → {'PASS' if G22a else 'FAIL'}")
bad2 = sum(1 for i in range(N)
           if poolA[offA[prons[i]]:offA[prons[i]] + len(prons[i])] != prons[i])
G22b = bad2 == 0
print(f"  exact-dedup 復元 不一致 = {bad2}  {'PASS' if G22b else 'FAIL'}")
# 音として意味があるか（pron 文字列に戻せるか）を 1 件目視
print(f"  先頭エントリ: surface={parsed[0][0]!r} pron={parsed[0][7]!r} "
      f"→ 復元 {dec_pron(fc_get(pron_id[0], bodyB, bstartB, KB))!r}", flush=True)

print("\n=== G2-3 / G2-4 陰性対照 ===", flush=True)
bb = bytearray(bodyB)
bb[bstartB[3] + 2] ^= 0xFF
n3 = sum(1 for i in smp
         if fc_get(pron_id[i], bytes(bb), bstartB, KB) != prons[i])
G23 = n3 > 0
print(f"  G2-3 body を 1 バイト破壊 → 不一致 {n3}  {'PASS' if G23 else 'FAIL'}")
# 陰性対照 2: dedup していない（= 重複を含む）並びを同じ添字で読むと壊れる
poolN = b"".join(prons)
offN = {}
o = 0
for p in prons:
    offN.setdefault(p, o)
    o += len(p)
n4 = sum(1 for i in range(0, N, 7)
         if poolA[offN[prons[i]]:offN[prons[i]] + len(prons[i])] != prons[i])
G24 = n4 > 0
print(f"  G2-4 dedup 済みプールを非 dedup のオフセットで読む → 不一致 {n4}  "
      f"{'PASS' if G24 else 'FAIL'}", flush=True)

RUNTIME = 25815130
print("\n=== まとめ（pool 11,465,780 B / ランタイム 25,815,130 B に対して）===")
for nm, tot in (("A pron dedup + ptr", totA), ("B (pron,extra) dedup + ptr", totB),
                (f"C distinct pron front-coded K={bestC['K']}", bestC["total"]),
                ("D 非 dedup front-coded K=16", totD)):
    d = tot - len(pool_now)
    print(f"  {nm:36s} {tot:>11,d} B  {d:>+10,d} B  "
          f"ランタイム比 {100*d/RUNTIME:+.2f}%")

json.dump(dict(pool_now=len(pool_now), pron_bytes=PRON_B, extra_bytes=EXTRA_B,
               acc_bytes=acc_b, orig_exceptions=n_orig_ex, read_exceptions=n_read_ex,
               distinct_pron=len(dp), distinct_extra=len(de), distinct_pair=len(dpe),
               distinct_pron_bytes=sum(len(k) for k in dp),
               A=dict(pool=len(poolA), ptr=len(ptrA), W=WA, total=totA),
               B=dict(pool=len(poolB), ptr=len(ptrB), W=WB, total=totB),
               C=rowsC, D=dict(body=len(bodyD), bucket_off=len(boD),
                               rank=len(rankD), total=totD),
               gates=dict(G2_1=G21, G2_2a=G22a, G2_2b=G22b, G2_3=G23, G2_4=G24)),
          open(os.path.join(_WORK, "q2.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q2.json")
