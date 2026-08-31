"""Q1c: 「鍵のアルファベットを変える」×「path 圧縮」の組合せを全部実ビルドする。

鍵の符号化 3 通り（どれも接頭符号なので trie の終端は必ず記号境界に来る）:
  utf8   : 生の UTF-8（現行）
  id16   : 文字を頻度順 ID にして 2 バイト BE
  idvar  : 上位 255 文字を 1 バイト、それ以外を 0xFF + 2 バイト BE

これで既存の（G1 で検証済みの）バイト trie ビルダをそのまま使い回せる。
ゲート G1c は「符号化した問い合わせ列に対する CPS が、生テキストの総当りと一致」。
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
from tries import PlainLouds, build_patricia, TailLouds

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
NS = int(sys.argv[1]) if len(sys.argv) > 1 else 300

surfaces_b = load_surfaces()
surfaces = [s.decode("utf-8") for s in surfaces_b]
sset = set(surfaces)
maxlen_ch = max(len(s) for s in surfaces)
cnt = Counter()
for s in surfaces:
    cnt.update(s)
cid = {c: i for i, (c, _) in enumerate(cnt.most_common())}
NCHAR = len(cid)
CHTAB = sum(len(c.encode("utf-8")) + 1 for c in cid)   # ID→UTF-8 の表（実バイト）
print(f"見出し語 {len(surfaces):,d} / 異なり文字 {NCHAR:,d} / 文字表 {CHTAB:,d} B",
      flush=True)


def enc_utf8(s):
    return s.encode("utf-8")


def enc_id16(s):
    return b"".join(cid[c].to_bytes(2, "big") for c in s)


def enc_idvar(s):
    out = bytearray()
    for c in s:
        i = cid[c]
        if i < 255:
            out.append(i)
        else:
            out += b"\xff" + i.to_bytes(2, "big")
    return bytes(out)


ENCS = [("utf8", enc_utf8, 0), ("id16", enc_id16, CHTAB), ("idvar", enc_idvar, CHTAB)]
results = {}
objs = {}
for name, enc, side in ENCS:
    keys = sorted(set(enc(s) for s in surfaces))
    assert len(keys) == len(sset), (name, len(keys), len(sset))   # 符号化が単射
    t = build_trie(keys)
    lab, term, cs = t["label"], t["term"], t["cs"]
    nn = lab.shape[0]
    PL = PlainLouds(lab, term, cs)
    pl_tot = sum(PL.parts().values()) + side
    row = {"nodes": nn, "plain": pl_tot, "plain_parts": PL.parts() | {"char_table": side}}
    objs[(name, "plain")] = PL
    bestv = None
    for mc in (1, 2, 3, 4, 6):
        pat = build_patricia(lab, term, cs, min_chain=mc)
        for mode in ("offset", "id"):
            TL = TailLouds(pat, dedup=True, ptr_mode=mode)
            tt = sum(TL.parts().values()) + side
            if bestv is None or tt < bestv[0]:
                bestv = (tt, mc, mode, TL.parts() | {"char_table": side}, TL)
    row["patricia"] = bestv[0]
    row["patricia_cfg"] = f"min_chain={bestv[1]} dedup mode={bestv[2]}"
    row["patricia_parts"] = bestv[3]
    objs[(name, "patricia")] = bestv[4]
    results[name] = row
    print(f"  {name:6s} ノード {nn:>10,d}  素 {pl_tot:>11,d} B  "
          f"path圧縮 {bestv[0]:>11,d} B ({bestv[1]}/{bestv[2]})", flush=True)

BASE = results["utf8"]["plain"]
print(f"\n{'符号化':>7} {'ノード':>11} {'素 LOUDS':>12} {'Δ':>12} "
      f"{'path 圧縮':>12} {'Δ':>12}")
for name, _, _ in ENCS:
    r = results[name]
    print(f"{name:>7} {r['nodes']:>11,} {r['plain']:>12,} {r['plain']-BASE:>+12,} "
          f"{r['patricia']:>12,} {r['patricia']-BASE:>+12,}")
bestk = min(results, key=lambda k: results[k]["patricia"])
print(f"\n  最小 = {bestk} + {results[bestk]['patricia_cfg']} → "
      f"{results[bestk]['patricia']:,d} B  ({results[bestk]['patricia']-BASE:+,d} B / "
      f"{100*(results[bestk]['patricia']-BASE)/BASE:+.2f}%)")
for k, v in results[bestk]["patricia_parts"].items():
    if v:
        print(f"    {k:20s} {v:>12,d} B")
print(flush=True)

# ---------------------------------------------------------------- G1c
print("=== G1c: 各構成の CPS が生テキストの総当りと一致するか ===", flush=True)
texts = []
with open(HELDOUT, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[2]:
            texts.append(p[2])
gt = texts[:NS]
ENCF = dict((n, e) for n, e, _ in ENCS)


def query_enc(name, s):
    """未知文字を含むテキストも符号化する（未知文字は trie に無い記号に落とす）"""
    if name == "utf8":
        return s.encode("utf-8"), [len(s[:i].encode("utf-8")) for i in range(len(s) + 1)]
    out = bytearray()
    boff = [0]
    for c in s:
        i = cid.get(c)
        if i is None:
            out += b"\xff\xff\xff\xff"            # trie に決して現れない
        elif name == "id16":
            out += i.to_bytes(2, "big")
        else:
            out += bytes([i]) if i < 255 else b"\xff" + i.to_bytes(2, "big")
        boff.append(len(out))
    return bytes(out), boff


gates = {}
t0 = time.time()
for key, obj in objs.items():
    name = key[0]
    bad = npos = nhit = 0
    hits_nodes = []
    for tx in gt:
        kb, boff = query_enc(name, tx)
        b2c = {b: i for i, b in enumerate(boff)}
        for i in range(len(tx)):
            exp = [ln for ln in range(1, min(maxlen_ch, len(tx) - i) + 1)
                   if tx[i:i + ln] in sset]
            got = obj.common_prefix_search(kb, boff[i])
            gc = []
            okb = True
            for bl, nd in got:
                c = b2c.get(boff[i] + bl)
                if c is None:
                    okb = False
                    break
                gc.append(c - i)
                hits_nodes.append(nd)
            npos += 1
            nhit += len(exp)
            if not okb or gc != exp:
                bad += 1
                if bad <= 2:
                    print(f"   NG {key}: {tx!r} i={i} got={gc} exp={exp}")
    gates[f"{key[0]}/{key[1]}"] = dict(mismatch=bad, positions=npos, hits=nhit)
    print(f"  {key[0]:6s}/{key[1]:9s} 位置 {npos:,d} ヒット {nhit:,d} 不一致 {bad}  "
          f"{'PASS' if bad == 0 and nhit > 0 else 'FAIL'}", flush=True)
print(f"  ({time.time()-t0:.1f}s)")

# 陰性対照: 最良構成の labels をヒットノードで壊す
print("\n=== G1c 陰性対照 ===", flush=True)
obj = objs[(bestk, "patricia")]
sub = gt[:40]


def run(o, nm):
    b = 0
    for tx in sub:
        kb, boff = query_enc(nm, tx)
        b2c = {bb: i for i, bb in enumerate(boff)}
        for i in range(len(tx)):
            exp = [ln for ln in range(1, min(maxlen_ch, len(tx) - i) + 1)
                   if tx[i:i + ln] in sset]
            got = o.common_prefix_search(kb, boff[i])
            gc = [b2c[boff[i] + bl] - i for bl, _ in got if boff[i] + bl in b2c]
            if gc != exp:
                b += 1
    return b


base = run(obj, bestk)
hn = []
for tx in sub:
    kb, boff = query_enc(bestk, tx)
    for i in range(len(tx)):
        hn.extend(nd for _, nd in obj.common_prefix_search(kb, boff[i]))
old = obj.labels
bl_ = bytearray(old)
bl_[hn[0]] ^= 0x01
obj.labels = bytes(bl_)
m = run(obj, bestk)
obj.labels = old
print(f"  基準 {base} → labels[{hn[0]}] 破壊で {m}  "
      f"{'PASS' if m > base else 'FAIL'}", flush=True)

json.dump(dict(n_chars=NCHAR, char_table_bytes=CHTAB,
               results={k: {kk: vv for kk, vv in v.items()} for k, v in results.items()},
               best=bestk, gates=gates,
               negative_control=dict(base=base, broken=m, passed=m > base)),
          open(os.path.join(_WORK, "q1c.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q1c.json")
