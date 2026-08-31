"""Q2b: pool の 68% を占める `extra`（orig/read 例外 + acc）を分解して測る。

Q2 で pron の dedup が**純損**（ポインタ代 2,366,769 B > 節約 1,880,664 B）と出たので、
本当の的である orig/read 例外 6,248,866 B を分解する。

ゲート:
  G2b-1 分解した各項の合計が Q2 の extra 7,827,762 B に一致
  G2b-2 各案から orig / read を**全件復元**して原文と一致
  G2b-3 陰性対照: 表を 1 エントリ壊すと G2b-2 が落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
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
mc = Counter()
for p in parsed:
    for u in p[7].split(":"):
        mc.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mc.most_common(254))}

surfaces = set(p[0] for p in parsed)
sid = {s: i for i, s in enumerate(sorted(surfaces))}

orig_ex = [(i, p[5]) for i, p in enumerate(parsed) if p[5] != p[0]]
read_ex = [(i, p[6]) for i, p in enumerate(parsed) if p[6] != p[7]]
ob = sum(1 + len(s.encode("utf-8")) for _, s in orig_ex)
rb = sum(1 + len(s.encode("utf-8")) for _, s in read_ex)
acc_b = sum(2 * (p[8].count(":") + 1) for p in parsed)
print(f"=== extra 7,827,762 B の分解（実ビルド）===")
print(f"  orig 例外 {len(orig_ex):>8,d} 件  {ob:>10,d} B  (1 件 {ob/len(orig_ex):5.2f} B)")
print(f"  read 例外 {len(read_ex):>8,d} 件  {rb:>10,d} B  (1 件 {rb/len(read_ex):5.2f} B)")
print(f"  acc      {N:>8,d} 件  {acc_b:>10,d} B")
print(f"  合計                   {ob+rb+acc_b:>10,d} B   (Q2 の extra 7,827,762 B)")
G2b1 = ob + rb + acc_b == 7827762
print(f"  G2b-1: {'PASS' if G2b1 else 'FAIL'}", flush=True)

print(f"\n=== orig 例外 {len(orig_ex):,d} 件 ===")
do = Counter(s for _, s in orig_ex)
in_dict = sum(1 for _, s in orig_ex if s in sid)
print(f"  distinct           {len(do):>10,d} ({100*len(do)/len(orig_ex):.2f}%)")
print(f"  ★ 見出し語集合に既に在る {in_dict:>10,d} ({100*in_dict/len(orig_ex):.2f}%)")
print(f"  distinct の総バイト   {sum(1+len(s.encode()) for s in do):>10,d} B", flush=True)

print(f"\n=== read 例外 {len(read_ex):,d} 件 ===")
dr = Counter(s for _, s in read_ex)
kata_ok = sum(1 for _, s in read_ex if all(m in mora_ids for m in split_moras(s)))
rb_mora = sum(1 + len(split_moras(s)) for _, s in read_ex
              if all(m in mora_ids for m in split_moras(s)))
rb_rest = sum(1 + len(s.encode("utf-8")) for _, s in read_ex
              if not all(m in mora_ids for m in split_moras(s)))
print(f"  distinct           {len(dr):>10,d} ({100*len(dr)/len(read_ex):.2f}%)")
print(f"  ★ mora 表 254 件で表せる {kata_ok:>10,d} ({100*kata_ok/len(read_ex):.2f}%)")
print(f"  mora ID 符号化した場合  {rb_mora+rb_rest:>10,d} B  "
      f"(現 {rb:,d} B → {100*(rb_mora+rb_rest-rb)/rb:+.2f}%)", flush=True)

# ---------------------------------------------------------------- 案
print("\n=== 案（現行 extra 7,827,762 B に対して）===", flush=True)
WS = 3           # 見出し語 ID 677,700 → 3 B


def W(x):
    return max(1, (max(1, x - 1).bit_length() + 7) // 8)


# E1: orig 例外を「見出し語 ID」で置く（辞書内のものだけ）。外は文字列のまま。
e1 = 0
for _, s in orig_ex:
    e1 += 1 + WS if s in sid else 1 + len(s.encode("utf-8"))
# E2: orig 例外を distinct 表 + ポインタ
uo = sorted(do)
poolo = b"".join(bytes([len(s.encode())]) + s.encode() for s in uo)
Wo = W(len(poolo))
e2 = len(poolo) + Wo * len(orig_ex)
# E3: read 例外を mora ID 符号化
e3 = rb_mora + rb_rest
# E4: read 例外を distinct 表 + ポインタ
ur = sorted(dr)
poolr = b"".join(bytes([len(s.encode())]) + s.encode() for s in ur)
Wr = W(len(poolr))
e4 = len(poolr) + Wr * len(read_ex)
# E5: acc を 1 バイトに（a,m 各 4 bit）— 実測で範囲に収まるか
amax = max(max((255 if u.split("/")[0] == "*" else int(u.split("/")[0]))
               for u in p[8].split(":")) for p in parsed if p[8])
mmax = max(max((255 if (u.split("/")[1] if "/" in u else "*") == "*" else
                int(u.split("/")[1])) for u in p[8].split(":")) for p in parsed if p[8])
print(f"  acc の実測レンジ: a の最大 {amax} / m の最大 {mmax} "
      f"(255 = '*')", flush=True)

rows = [
    ("現行", ob, rb, acc_b),
    ("E1 orig を見出し語 ID に", e1, rb, acc_b),
    ("E2 orig を distinct 表+ptr", e2, rb, acc_b),
    ("E3 read を mora ID に", ob, e3, acc_b),
    ("E4 read を distinct 表+ptr", ob, e4, acc_b),
    ("E1+E3", e1, e3, acc_b),
    ("E2+E4", e2, e4, acc_b),
    ("orig/read を捨てる (TTS 最小)", 0, 0, acc_b),
]
print(f"\n{'案':>30} {'orig':>10} {'read':>10} {'acc':>10} {'extra 計':>11} {'Δ':>11}")
for nm, a, b, c in rows:
    t = a + b + c
    print(f"{nm:>30} {a:>10,} {b:>10,} {c:>10,} {t:>11,} {t-7827762:>+11,}")

RUNTIME = 25815130
print(f"\n  ランタイム 25,815,130 B に対して:")
for nm, a, b, c in rows[1:]:
    d = (a + b + c) - 7827762
    print(f"    {nm:>30}  {d:>+11,d} B  {100*d/RUNTIME:+6.2f}%")

# ---------------------------------------------------------------- G2b-2
print("\n=== G2b-2: 復元検査 ===", flush=True)
inv_sid = sorted(surfaces)
bad1 = sum(1 for _, s in orig_ex if s in sid and inv_sid[sid[s]] != s)
oidx = {s: i for i, s in enumerate(uo)}
ooff = []
o = 0
for s in uo:
    ooff.append(o)
    o += 1 + len(s.encode())
bad2 = 0
for _, s in orig_ex:
    p = ooff[oidx[s]]
    if poolo[p + 1:p + 1 + poolo[p]].decode("utf-8") != s:
        bad2 += 1
inv_m = {v: k for k, v in mora_ids.items()}
bad3 = 0
for _, s in read_ex:
    ms = split_moras(s)
    if all(m in mora_ids for m in ms):
        if "".join(inv_m[mora_ids[m]] for m in ms) != s:
            bad3 += 1
G2b2 = bad1 == 0 and bad2 == 0 and bad3 == 0
print(f"  E1 見出し語 ID 復元 不一致 {bad1} / {in_dict:,d}")
print(f"  E2 distinct 表 復元 不一致 {bad2} / {len(orig_ex):,d}")
print(f"  E3 mora ID 復元 不一致 {bad3} / {kata_ok:,d}")
print(f"  G2b-2: {'PASS' if G2b2 else 'FAIL'}")

bp = bytearray(poolo)
bp[ooff[5] + 1] ^= 0xFF
nbad = 0
for _, s in orig_ex:
    p = ooff[oidx[s]]
    try:
        if bytes(bp)[p + 1:p + 1 + bp[p]].decode("utf-8") != s:
            nbad += 1
    except UnicodeDecodeError:
        nbad += 1
G2b3 = nbad > 0
print(f"  G2b-3 陰性対照 (表を 1 バイト破壊) → 不一致 {nbad}  "
      f"{'PASS' if G2b3 else 'FAIL'}", flush=True)

json.dump(dict(orig_ex=len(orig_ex), orig_bytes=ob, orig_distinct=len(do),
               orig_in_dict=in_dict,
               read_ex=len(read_ex), read_bytes=rb, read_distinct=len(dr),
               read_mora_encodable=kata_ok, acc_bytes=acc_b,
               acc_max_a=amax, acc_max_m=mmax,
               options={nm: dict(orig=a, read=b, acc=c, total=a + b + c,
                                 delta=a + b + c - 7827762) for nm, a, b, c in rows},
               gates=dict(G2b_1=G2b1, G2b_2=G2b2, G2b_3=G2b3)),
          open(os.path.join(_WORK, "q2b.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q2b.json")
