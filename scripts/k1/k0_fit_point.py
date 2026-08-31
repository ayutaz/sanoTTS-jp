"""K-0: 16 MB ボードの辞書予算に **実際に何エントリ入るか**を二分探索で測る。

K-1 §2-3 の曲線は B-0 の水準に合わせた離散点しかなく、予算 11,730,944 B は
263,000 entries (10,275,693 B) と 400,000 entries (14,467,408 B) の**間**に落ちる。
**予算境界での内挿は C-009 で禁止**なので、実際に符号化して測る。

⚠️ 精度は測れない（枝刈り辞書には未知語処理が要る。K-1 §10）。
出せるのは**サイズと、B-0 の実測点で挟んだ区間**まで。
"""
from __future__ import annotations

import os
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_entries_lib import load_entries          # noqa: E402
from k1_paths import DICT_PP, TRAIN, WORK          # noqa: E402

MATRIX_HDR, CHARBIN, UNKDIC = 4, 262496, 5690
SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


print(f"辞書: {DICT_PP}")
parsed = load_entries(DICT_PP)
print(f"entries = {len(parsed):,d}", flush=True)

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


pos6_tab = {}
for p in parsed:
    pos6_tab.setdefault(p[4], len(pos6_tab))
bysurf_all = defaultdict(list)
for p in parsed:
    bysurf_all[p[0]].append(p)


def encode_size(sub):
    """K-1 §2-2 と同じ形式で符号化し、ランタイム合計バイトを返す。"""
    surfaces = sorted(set(p[0] for p in sub))
    kids = [dict()]; term = [False]
    for s in surfaces:
        n = 0
        for b in s.encode("utf-8"):
            nx = kids[n].get(b)
            if nx is None:
                nx = len(kids); kids.append(dict()); term.append(False); kids[n][b] = nx
            n = nx
        term[n] = True
    order, q = [], [0]
    while q:
        nxt = []
        for n in q:
            order.append(n)
            for b in sorted(kids[n]):
                nxt.append(kids[n][b])
        q = nxt
    n_nodes = len(order)
    bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
    louds = (bitlen + 7) // 8
    labels = n_nodes
    tbits = (n_nodes + 7) // 8
    # ⚠️ rank 索引は C-… の訂正後の式（256 bit superblock + 64 bit block + select0 標本）
    rank = ((bitlen // 256 + 1) * 4 + (bitlen // 64 + 1)) + \
           ((n_nodes // 256 + 1) * 4 + (n_nodes // 64 + 1)) + \
           ((bitlen // 512 + 1) * 4)

    lcls, lchain = {}, {}
    flat = []
    counts = 0
    for s in surfaces:
        es = bysurf_all[s]
        counts += 1
        flat.extend(es)
    for p in flat:
        lcls.setdefault((p[1], p[2], p[4]), len(lcls))
        lchain.setdefault(p[9], len(lchain))

    rec = 9 * len(flat)
    pool = 0
    for p in flat:
        surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
        pb = enc_pron(pron); accs = enc_acc(accf)
        extra = 0
        if orig != surf:
            extra += 1 + len(orig.encode("utf-8"))
        if read != pron:
            extra += 1 + len(read.encode("utf-8"))
        extra += 2 * len(accs)
        pool += len(pb) + extra

    ckpt = ((counts + 255) // 256) * 4
    pckpt = ((len(flat) + 255) // 256) * 4
    cls_blob = 6 * len(lcls)
    pos6_blob = sum(len(",".join(k).encode("utf-8")) + 1 for k in pos6_tab)
    chain_blob = sum(len(k.encode("utf-8")) + 1 for k in lchain)
    mora_blob = sum(len(k.encode("utf-8")) + 1 for k in mora_ids)

    lex = (louds + labels + tbits + rank + counts + ckpt + rec + pool + pckpt
           + cls_blob + pos6_blob + chain_blob + mora_blob)
    n_lc = len(set(p[1] for p in flat)); n_rc = len(set(p[2] for p in flat))
    matrix = MATRIX_HDR + 2 * n_lc * n_rc
    return lex + matrix + CHARBIN + UNKDIC, len(flat), len(surfaces)


# --- ランキング（B-0 と同じ基準）------------------------------------------
import pyopenjtalk  # noqa: E402
freq = Counter()
with open(TRAIN, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            for ft in pyopenjtalk.run_mecab_detailed(p[2])[0]:
                s = ft.split(",", 1)[0]
                if s in bysurf_all:
                    freq[s] += 1
in_corpus = [s for s, _ in freq.most_common()]
rest = sorted((s for s in bysurf_all if s not in freq),
              key=lambda s: (min(e[3] for e in bysurf_all[s]), len(s)))
ranked = in_corpus + rest
print(f"ranked surfaces = {len(ranked):,d}", flush=True)


def subset(target_entries):
    sub, n = [], 0
    for s in ranked:
        es = bysurf_all[s]
        sub.extend(es); n += len(es)
        if n >= target_entries:
            break
    return sub


BUDGETS = [
    ("16 MB / B (OTA2, model 別) ← 推奨", 11_730_944),
    ("16 MB / A (OTA 無し)", 13_828_096),
    ("16 MB / C (OTA2, model 内蔵)", 10_944_512),
]

print(f"\n{'予算':34s} {'B':>12s} {'入る entries':>13s} {'見出し語':>10s} {'実サイズ':>12s} {'余り':>10s}")
out = {}
for name, budget in BUDGETS:
    lo, hi = 5_000, 500_000
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        size, ne, ns = encode_size(subset(mid))
        if size <= budget:
            best = (ne, ns, size); lo = mid + 5_000
        else:
            hi = mid - 5_000
    if best:
        ne, ns, size = best
        out[name] = best
        print(f"{name:34s} {budget:>12,d} {ne:>13,d} {ns:>10,d} {size:>12,d} "
              f"{budget-size:>10,d}")

print("""
⚠️ **精度は測っていない。** B-0 の実測点で挟むと:
     263,000 entries → 音素 91.31% / アクセント 81.55%
     400,000 entries → 音素 95.53% / アクセント 89.29%
   この 2 点の**間**であることしか言えない（内挿は禁止。C-009）。
⚠️ サイズは byte 単位の LOUDS（現行形式）。compress-lane の文字 ID 鍵を入れれば
   trie が約 42% 縮むので、さらに入る。**未検証の組み合わせ。**""")
