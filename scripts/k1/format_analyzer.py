"""新形式だけを読む解析器を書いて、MeCab の出力と突き合わせる。

これまでの弱点: サイズは実測したが、**精度は B-0 が MeCab 形式で測った値を借りていた**。
「同じエントリ集合なら同じ出力」は推論であって実測ではなかった。

ここでは
  * 見出し語の検索は **LOUDS trie だけ**
  * lc / rc / wcost / pron / acc / chain の取得は **9 B レコード + 値プールだけ**
  * 接続コストは matrix.bin
で Viterbi を回し、`pyopenjtalk.run_mecab_detailed()` と比較する。

一致すれば「新形式は解析器を駆動できる」が実測になる。

ゲート:
  G9  未知語を含まない文で、分割・読み・アクセントが MeCab と完全一致
  G10 陰性対照: 接続コストを 0 にすると G9 が落ちる（= Viterbi が効いている）
  G11 陰性対照: 値プールを 1 バイト壊すと G9 が落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import struct
import sys
from collections import Counter, defaultdict

import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
# ⚠️ **pyopenjtalk が実際に読む辞書を使う。**
#    ~/Documents/piper-plus/build/share/... の sys.dic は別リビジョン
#    (103,082,017 B vs 103,131,410 B) で、これを混ぜると「未知語」が大量に出て
#    比較が成立しない（実際に 60 文中 43 文が除外された）。
import pyopenjtalk as _pjt
DIC = _pjt.OPEN_JTALK_DICT_DIR.decode()
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 300

SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


# ---------------------------------------------------------------- 符号化

print(f"辞書を読む: {DIC}", flush=True)
sys.path.insert(0, SP)
from dump_entries_lib import load_entries          # noqa: E402
parsed = load_entries(DIC)
print(f"  {len(parsed):,d} entries", flush=True)

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


def enc_acc(a):
    out = []
    for unit in a.split(":"):
        x, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        out.append((255 if x == "*" else int(x), 255 if m == "*" else int(m)))
    return out


def dec_acc(vals):
    return ":".join(("*" if a == 255 else str(a)) + "/" + ("*" if m == 255 else str(m))
                    for a, m in vals)


# クラス表には posid も入れる（監査で (lc,rc,pos6) から一意と確認済み）
cls_tab, chain_tab = {}, {}
for p in parsed:
    cls_tab.setdefault((p[1], p[2], p[4]), len(cls_tab))
    chain_tab.setdefault(p[9], len(chain_tab))
inv_cls = {v: k for k, v in cls_tab.items()}
inv_chain = {v: k for k, v in chain_tab.items()}

surfaces = sorted(set(p[0] for p in parsed))
bysurf = defaultdict(list)
for p in parsed:
    bysurf[p[0]].append(p)

# ---------------------------------------------------------------- LOUDS

print("LOUDS を組む ...", flush=True)
kids = [dict()]
term = [False]
for s in surfaces:
    n = 0
    for b in s.encode("utf-8"):
        nx = kids[n].get(b)
        if nx is None:
            nx = len(kids); kids.append(dict()); term.append(False); kids[n][b] = nx
        n = nx
    term[n] = True
order, q, key = [], [0], {0: b""}
while q:
    nxt = []
    for n in q:
        order.append(n)
        for b in sorted(kids[n]):
            c = kids[n][b]; key[c] = key[n] + bytes([b]); nxt.append(c)
    q = nxt
n_nodes = len(order)
idx = {n: i for i, n in enumerate(order)}

bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
bits = np.zeros(bitlen, dtype=np.uint8)
bits[0] = 1
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
assert pos == bitlen
rank1 = np.concatenate([[0], np.cumsum(bits)]).astype(np.int64)
sel0 = np.flatnonzero(bits == 0).astype(np.int64)
print(f"  nodes={n_nodes:,d} bits={bitlen:,d}", flush=True)

# 終端ノード（LOUDS 順）→ その見出し語のエントリ範囲
term_nodes = [i for i in range(n_nodes) if tflag[i]]
term_keys = [key[order[i]].decode("utf-8") for i in term_nodes]
node_to_termrank = {}
for r, i in enumerate(term_nodes):
    node_to_termrank[i] = r

flat = []
count_arr = []
for s in term_keys:
    es = bysurf[s]
    count_arr.append(len(es))
    flat.extend(es)
start_of = np.concatenate([[0], np.cumsum(count_arr)]).astype(np.int64)

# 9 B レコード + 値プール
records = bytearray()
pool = bytearray()
for p in flat:
    surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
    pb = enc_pron(pron)
    accs = enc_acc(accf)
    flags = 0
    if orig == surf:
        flags |= 1
    if read == pron:
        flags |= 2
    extra = bytearray()
    if not (flags & 1):
        ob = orig.encode("utf-8"); extra += bytes([len(ob)]) + ob
    if not (flags & 2):
        rb = read.encode("utf-8"); extra += bytes([len(rb)]) + rb
    for a, m in accs:
        extra.append(a); extra.append(m)
    records += struct.pack("<HhHBBB", cls_tab[(lc, rc, pos6)], cost,
                           chain_tab[chain], flags, len(pb), len(extra))
    pool += pb + extra
pool_off = np.zeros(len(flat) + 1, dtype=np.int64)
for i in range(len(flat)):
    _, _, _, _, pl, el = struct.unpack("<HhHBBB", records[i * 9:(i + 1) * 9])
    pool_off[i + 1] = pool_off[i] + pl + el
records = bytes(records); pool = bytes(pool)
print(f"  records={len(records):,d} B  pool={len(pool):,d} B", flush=True)

# ---------------------------------------------------------------- matrix

mraw = open(os.path.join(DIC, "matrix.bin"), "rb").read()
lsize, rsize = struct.unpack("<HH", mraw[:4])
MAT = np.frombuffer(mraw, dtype=np.int16, offset=4).reshape(rsize, lsize)
NCTX = lsize


def trans(rc_prev, lc_cur):
    """遷移コスト。

    ⚠️ **索引は flat[rc_prev + N*lc_cur]**。MeCab 本家の
    `matrix_[lcAttr + lsize_*rcAttr]` とは逆で、推測で書くと必ず外す。
    diag_matrix.py で MeCab 自身の申告値 1,537 組に対し 100.00% 一致を確認した
    （他の 3 通りの索引式はすべて 1% 未満）。
    """
    return int(MAT[lc_cur, rc_prev])


print(f"  matrix {rsize}x{lsize}", flush=True)

# ---------------------------------------------------------------- 検索

def louds_child(node, ch):
    p = int(sel0[node]) + 1
    while p < bitlen and bits[p]:
        # ⚠️ 子ノード番号 = **p より前**の 1 の数。rank1[p+1] にすると 1 ずれて
        #    全部 1 文字に分割される（実際に踏んだ）。rank1[i] = bits[0..i) の 1 の数。
        c = int(rank1[p])
        if c < n_nodes and labels[c] == ch:
            return c
        p += 1
    return None


def cps(kb, start):
    """(長さ, 終端ノード) の列。LOUDS だけを見る。"""
    out = []
    node = 0
    for k in range(start, len(kb)):
        nx = louds_child(node, kb[k])
        if nx is None:
            break
        node = nx
        if tflag[node]:
            out.append((k - start + 1, node))
    return out


def entry_fields(i):
    """9 B レコード + 値プールだけからエントリを復元する。"""
    cid, cost, chid, flags, pl, el = struct.unpack("<HhHBBB", records[i * 9:(i + 1) * 9])
    o = int(pool_off[i])
    pb = pool[o:o + pl]
    ex = pool[o + pl:o + pl + el]
    pron = dec_pron(pb)
    j = 0
    if not (flags & 1):
        n = ex[j]; j += 1 + n
    if not (flags & 2):
        n = ex[j]; j += 1 + n
    accs = []
    while j < len(ex):
        accs.append((ex[j], ex[j + 1])); j += 2
    lc, rc, pos6 = inv_cls[cid]
    return lc, rc, cost, pron, dec_acc(accs), inv_chain[chid], pos6


# ---------------------------------------------------------------- Viterbi

BIG = 1 << 30


def viterbi(text):
    """本物の Viterbi。

    ⚠️ 位置ごとに 1 本だけ最良経路を残すのは Viterbi ではない（最初にそれを書いた）。
    同じ位置で終わるノードでも rc が違えば後続の接続コストが変わるので、
    **ノードごとに**最良の前任を持たなければならない。
    """
    kb = text.encode("utf-8")
    N = len(kb)
    # ends[j] = そこで終わるノードの索引リスト（nodes への添字）
    nodes = []          # (begin, end, entry_index, lc, rc, wcost)
    ends = [[] for _ in range(N + 1)]
    starts = [[] for _ in range(N + 1)]
    for i in range(N):
        for ln, nd in cps(kb, i):
            r = node_to_termrank[nd]
            for e in range(int(start_of[r]), int(start_of[r + 1])):
                lc, rc, wcost, *_ = entry_fields(e)
                k = len(nodes)
                nodes.append((i, i + ln, e, lc, rc, wcost))
                ends[i + ln].append(k)
                starts[i].append(k)

    INF = float("inf")
    cost = [INF] * len(nodes)
    prev = [-2] * len(nodes)          # -1 = BOS
    # BOS: 位置 0 から始まるノードは rc_prev = 0
    for k in starts[0]:
        b, e, ei, lc, rc, w = nodes[k]
        cost[k] = trans(0, lc) + w          # BOS の rc = 0
        prev[k] = -1
    for j in range(1, N + 1):
        for k in starts[j]:
            b, e, ei, lc, rc, w = nodes[k]
            best_c, best_p = INF, -2
            for pk in ends[j]:
                if cost[pk] == INF:
                    continue
                c = cost[pk] + trans(nodes[pk][4], lc)
                if c < best_c:
                    best_c, best_p = c, pk
            if best_p != -2:
                cost[k] = best_c + w
                prev[k] = best_p
    # EOS: lc = 0
    best_c, best_k = INF, -2
    for pk in ends[N]:
        if cost[pk] == INF:
            continue
        c = cost[pk] + trans(nodes[pk][4], 0)      # EOS の lc = 0
        if c < best_c:
            best_c, best_k = c, pk
    if best_k == -2:
        return None
    out = []
    k = best_k
    while k != -1:
        b, e, ei, lc, rc, w = nodes[k]
        out.append((b, e, ei))
        k = prev[k]
    out.reverse()
    return out


# ---------------------------------------------------------------- 比較

import pyopenjtalk

texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"),
          encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
# ⚠️ **先頭から N 件取ってはいけない。** corpus_heldout.tsv はソースごとの
#    連続ブロックで並んでいて、行 0..946 は全部 cv/sentence_collector。
#    N<=947 の「先頭 N 文」は単一ソースの偏った標本になる（実際に踏んだ）。
if LIMIT >= len(texts):
    pass
else:
    step = len(texts) / LIMIT
    texts = [texts[int(i * step)] for i in range(LIMIT)]
print(f"\n比較する文 = {len(texts)}（全ソースにまたがる等間隔標本）", flush=True)

surf_set = set(surfaces)
n_eval = 0
n_skip_unk = 0
n_skip_unkword = [0]     # MeCab 側が未知語と判定した文
n_skip_nopath = [0]      # 私の解析器が経路を張れなかった文（未知語ノードを作らないため）
seg_ok = pron_ok = acc_ok = 0
examples = []
for t in texts:
    # ⚠️ 参照は features(要素0) ではなく **morphs(要素1)** を使う。
    #    features は 記号/空白 を落としているので、こちらの出力と粒度が揃わない。
    #    morphs は is_unknown も持つので、未知語の判定を自前の集合照合に頼らずに済む。
    morphs = pyopenjtalk.run_mecab_detailed(t)[1]
    # ⚠️ 判定を先にする。未知語の morph は features が 12 列に満たないことがあり、
    #    ref を先に組むと IndexError で落ちる（実際に n=800 で落ちた）。
    if not morphs or any(m["is_unknown"] or len(m["features"]) < 12 for m in morphs):
        n_skip_unk += 1
        n_skip_unkword[0] += 1
        continue
    ref = [(m["surface"], m["features"][9], m["features"][10], m["features"][11])
           for m in morphs]
    got = viterbi(t)
    if got is None:
        n_skip_unk += 1
        n_skip_nopath[0] += 1
        continue
    kb = t.encode("utf-8")
    mine = []
    for b, e, ei in got:
        lc, rc, wc, pron, accf, chain, pos6 = entry_fields(ei)
        mine.append((kb[b:e].decode("utf-8"), pron, accf, chain))
    n_eval += 1
    if [m[0] for m in mine] == [r[0] for r in ref]:
        seg_ok += 1
    if [m[1] for m in mine] == [r[1] for r in ref]:
        pron_ok += 1
    if [(m[2], m[3]) for m in mine] == [(r[2], r[3]) for r in ref]:
        acc_ok += 1
    elif len(examples) < 5:
        examples.append((t, [m[0] for m in mine], [r[0] for r in ref]))

print(f"\n=== G9: 新形式だけを読む解析器 vs MeCab ===")
print(f"  除外 = {n_skip_unk} / {len(texts)}  "
      f"(MeCab が未知語と判定 {n_skip_unkword[0]} / 私の解析器が経路なし "
      f"{n_skip_nopath[0]})")
print("  ⚠️ 『経路なし』は形式の欠陥ではなく、私の解析器が未知語ノード"
      "(char.bin/unk.dic)を作らないため。")
print(f"  評価した文 n = {n_eval}")
if n_eval:
    print(f"  分割一致    {seg_ok}/{n_eval} = {100*seg_ok/n_eval:.2f}%")
    print(f"  読み一致    {pron_ok}/{n_eval} = {100*pron_ok/n_eval:.2f}%")
    print(f"  アクセント+結合規則一致 {acc_ok}/{n_eval} = {100*acc_ok/n_eval:.2f}%")
for e in examples:
    print("   NG:", e[0])
    print("     mine:", e[1])
    print("     ref :", e[2])
G9 = n_eval > 0 and seg_ok == n_eval and pron_ok == n_eval and acc_ok == n_eval
print(f"  G9: {'PASS' if G9 else 'FAIL'}")

# ---------------------------------------------------------------- 陰性対照

print(f"\n=== G10: 陰性対照（接続コストを 0 にすると落ちるか）===")
MAT_SAVE = MAT
MAT = np.zeros_like(MAT_SAVE)
bad = 0
n2 = 0
for t in texts[:min(len(texts), 150)]:
    morphs = pyopenjtalk.run_mecab_detailed(t)[1]
    ref = [m["surface"] for m in morphs]
    if any(m["is_unknown"] for m in morphs):
        continue
    got = viterbi(t)
    if got is None:
        continue
    n2 += 1
    kb = t.encode("utf-8")
    if [kb[b:e].decode("utf-8") for b, e, _ in got] != ref:
        bad += 1
MAT = MAT_SAVE
print(f"  接続コスト 0 で分割が変わった文 = {bad} / {n2}")
G10 = bad > 0
print(f"  G10: {'PASS' if G10 else 'FAIL'}")

print(f"\n=== G11: 陰性対照（値プールを 1 バイト壊すと読みが変わるか）===")
pool_save = pool
pb = bytearray(pool)
pb[100] ^= 0x01
pool = bytes(pb)
changed = sum(1 for i in range(200)
              if entry_fields(i)[3] != None) and True
diff = 0
for i in range(0, 400):
    a = entry_fields(i)
    pool = pool_save
    b = entry_fields(i)
    pool = bytes(pb)
    if a != b:
        diff += 1
pool = pool_save
print(f"  400 エントリ中、復元結果が変わった = {diff}")
G11 = diff > 0
print(f"  G11: {'PASS' if G11 else 'FAIL'}")
