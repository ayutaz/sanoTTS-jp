"""1 文あたりのラティス規模を実測する（RAM の主要項）。

ram-lane に同じことを頼んであるが、**受け取った報告を鵜呑みにしない**ための
独立実測。verifying-reports の「repro を自分で走らせる」に相当する。

測るもの:
  * ラティスノード数 = 全バイト位置での common-prefix-search ヒットの総和
  * trie のプローブ回数（LOUDS の子探索で見たビット位置の数）
  * entry_fields の呼び出し回数（= 値プールへのランダム読み）
すべて **新形式の LOUDS を実際に叩いて**数える。
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
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 500

parsed = load_entries(DIC)
surfaces = sorted(set(p[0] for p in parsed))
from collections import defaultdict
bysurf = defaultdict(list)
for p in parsed:
    bysurf[p[0]].append(p)
print(f"entries={len(parsed):,d} surfaces={len(surfaces):,d}", flush=True)

# --- LOUDS ---
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
idx = {n: i for i, n in enumerate(order)}
bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
bits = np.zeros(bitlen, dtype=np.uint8); bits[0] = 1
pos = 2
labels = np.zeros(n_nodes, dtype=np.uint8)
tflag = np.zeros(n_nodes, dtype=np.uint8)
for n in order:
    i = idx[n]
    tflag[i] = 1 if term[n] else 0
    for b in sorted(kids[n]):
        labels[idx[kids[n][b]]] = b
        bits[pos] = 1; pos += 1
    pos += 1
rank1 = np.concatenate([[0], np.cumsum(bits)]).astype(np.int64)
sel0 = np.flatnonzero(bits == 0).astype(np.int64)
term_nodes = [i for i in range(n_nodes) if tflag[i]]
t2r = {i: r for r, i in enumerate(term_nodes)}
term_keys = [None] * len(term_nodes)
key = {0: b""}
for n in order:
    for b in sorted(kids[n]):
        key[kids[n][b]] = key[n] + bytes([b])
for r, i in enumerate(term_nodes):
    term_keys[r] = key[order[i]].decode("utf-8")
counts = [len(bysurf[s]) for s in term_keys]
start_of = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
print(f"nodes={n_nodes:,d}", flush=True)

PROBES = [0]
PAGES = set()
LINES = set()
PAGE, LINE = 65536, 32
# 各配列の仮想オフセット（実機の配置を模す）
OFF_BITS = 0
OFF_LABELS = (bitlen + 7) // 8
OFF_TERM = OFF_LABELS + n_nodes
OFF_REC = OFF_TERM + (n_nodes + 7) // 8


def touch(off):
    PAGES.add(off // PAGE); LINES.add(off // LINE)


def child(node, ch):
    p = int(sel0[node]) + 1
    while p < bitlen and bits[p]:
        PROBES[0] += 1
        touch(OFF_BITS + p // 8)
        c = int(rank1[p])
        touch(OFF_LABELS + c)
        if labels[c] == ch:
            return c
        p += 1
    return None


def cps(kb, start):
    out, node = [], 0
    for k in range(start, len(kb)):
        nx = child(node, kb[k])
        if nx is None:
            break
        node = nx
        touch(OFF_TERM + node // 8)
        if tflag[node]:
            out.append(node)
    return out


texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"),
          encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
# ⚠️ 先頭 N 件は単一ソース（行 0..946 が cv/sentence_collector）。等間隔で取る。
if LIMIT < len(texts):
    step = len(texts) / LIMIT
    texts = [texts[int(i * step)] for i in range(LIMIT)]

nodes_per, probes_per, pages_per, lines_per, chars_per, entries_per = [], [], [], [], [], []
for t in texts:
    kb = t.encode("utf-8")
    PROBES[0] = 0; PAGES.clear(); LINES.clear()
    nn = 0; ne = 0
    for i in range(len(kb)):
        for nd in cps(kb, i):
            r = t2r[nd]
            k = int(start_of[r + 1]) - int(start_of[r])
            nn += k
            ne += k
            for e in range(int(start_of[r]), int(start_of[r + 1])):
                touch(OFF_REC + e * 9)
    nodes_per.append(nn); probes_per.append(PROBES[0])
    pages_per.append(len(PAGES)); lines_per.append(len(LINES))
    chars_per.append(len(t)); entries_per.append(ne)


def stat(name, a, unit=""):
    a = np.array(a)
    print(f"  {name:28s} mean {a.mean():9.1f} median {np.median(a):8.1f} "
          f"p95 {np.percentile(a,95):9.1f} max {a.max():9.0f} {unit}")


print(f"\n=== 1 文あたり（held-out {len(texts)} 文）===")
stat("文字数", chars_per, "chars")
stat("ラティスノード数", nodes_per, "nodes")
stat("trie プローブ回数", probes_per, "probes")
stat("触れた 64KB ページ", pages_per, "pages")
stat("触れた 32B キャッシュライン", lines_per, "lines")

nodes = np.array(nodes_per)
chars = np.array(chars_per)
print(f"\n  1 文字あたりのノード数: mean {(nodes/chars).mean():.2f}")
print(f"  最長文: {chars.max()} 文字 / そのノード数 "
      f"{nodes[int(np.argmax(chars))]:,d}")
print(f"  最大ノード数の文: {nodes.max():,d} nodes ({chars[int(np.argmax(nodes))]} 文字)")

print("\n=== ノード 1 個あたりのバイト数を仮定した RAM（算術。実測ではない）===")
for sz in (16, 24, 32, 48):
    print(f"  {sz} B/node → mean {nodes.mean()*sz/1024:7.1f} KB / "
          f"p95 {np.percentile(nodes,95)*sz/1024:7.1f} KB / "
          f"max {nodes.max()*sz/1024:7.1f} KB")
print("  ⚠️ mecab の Node 構造体サイズは未確認。ram-lane の報告と突き合わせること。")
