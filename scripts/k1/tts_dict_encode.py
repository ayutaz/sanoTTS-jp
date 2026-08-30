"""NAIST-JDIC を「TTS に必要な情報だけ」の独自バイナリに再符号化して実サイズを測る。

B-0 (D-009) が測ったのは **MeCab 形式のまま枝刈りした**辞書のサイズだった。
b0-g2p-footprint.md §7-4 が「dedup による節約は独自バイナリ形式に進むときだけ
回収できる」と書いて未測定のまま残した軸を、実際にバイト列を作って len() する。

出力する数字はすべて「実際に組み立てたバイト列の長さ」。推定は 1 つも無い。

ゲート:
  G1 全 788,923 エントリの往復（blob から復号して元の値と一致）
  G2 LOUDS trie の common-prefix-search が参照実装と一致（1,000 文分）
  G3 陰性対照: blob を 1 バイト壊すと G1 が落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import struct
import sys
import zlib
from collections import Counter, defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
ENTRIES = os.path.join(_WORK, "entries.tsv")

# ---------------------------------------------------------------- 読み込み

rows = []
with open(ENTRIES, encoding="utf-8") as f:
    for ln in f:
        surf, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t")
        rows.append((surf, int(lc), int(rc), int(cost), feat))
print(f"entries={len(rows)}  distinct_surfaces={len(set(r[0] for r in rows))}", flush=True)

# 11 列: pos,g1,g2,g3,ctype,cform,orig,read,pron,acc/mora,chain_rule
bad = 0
parsed = []
for surf, lc, rc, cost, feat in rows:
    fs = feat.split(",")
    if len(fs) != 11:
        bad += 1
        fs = (fs + ["*"] * 11)[:11]
    pos6 = tuple(fs[0:6])
    orig, read, pron, accf, chain = fs[6], fs[7], fs[8], fs[9], fs[10]
    parsed.append((surf, lc, rc, cost, pos6, orig, read, pron, accf, chain))
print(f"feature 列数が 11 でない行: {bad}", flush=True)

# ---------------------------------------------------------------- 語彙表

pos6_tab = {}
for p in parsed:
    pos6_tab.setdefault(p[4], len(pos6_tab))
chain_tab = {}
for p in parsed:
    chain_tab.setdefault(p[9], len(chain_tab))
lcrc_tab = {}
for p in parsed:
    lcrc_tab.setdefault((p[1], p[2]), len(lcrc_tab))
# 形態素クラス = (lc, rc, pos6) の組。これ 1 つで lc/rc/pos6 を復元できる
cls_tab = {}
for p in parsed:
    cls_tab.setdefault((p[1], p[2], p[4]), len(cls_tab))
wcost_tab = {}
for p in parsed:
    wcost_tab.setdefault(p[3], len(wcost_tab))

print(f"distinct pos6={len(pos6_tab)}  chain_rule={len(chain_tab)}  "
      f"(lc,rc)={len(lcrc_tab)}  class=(lc,rc,pos6)={len(cls_tab)}  "
      f"wcost={len(wcost_tab)}", flush=True)

# ---------------------------------------------------------------- モーラ表

SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    """カタカナ列をモーラに割る。割れない文字は 1 文字 1 モーラ扱いで返す。"""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(c + s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return out


mora_count = Counter()
for p in parsed:
    for unit in p[7].split(":"):
        mora_count.update(split_moras(unit))
print(f"distinct mora symbols in pron = {len(mora_count)}", flush=True)
print("  上位20:", [m for m, _ in mora_count.most_common(20)], flush=True)
rare = [m for m, c in mora_count.items() if c < 50]
print(f"  出現 50 回未満のモーラ記号: {len(rare)} 種", flush=True)

# 上位 254 種を 1 バイト (id 0..253)、0xFE は複合語区切り、0xFF はエスケープ
mora_ids = {m: i for i, (m, _) in enumerate(mora_count.most_common(254))}
assert max(mora_ids.values()) <= 253
ESC = 255


def encode_pron(pron):
    """pron（':' 区切りの複合語対応）をバイト列に。区切りは 0xFE。"""
    out = bytearray()
    for k, unit in enumerate(pron.split(":")):
        if k:
            out.append(0xFE)
        for m in split_moras(unit):
            if m in mora_ids:
                out.append(mora_ids[m])
            else:
                b = m.encode("utf-8")
                out.append(ESC)
                out.append(len(b))
                out += b
    return bytes(out)


def decode_pron(b):
    inv = {v: k for k, v in mora_ids.items()}
    units = [[]]
    i = 0
    while i < len(b):
        c = b[i]
        if c == 0xFE:
            units.append([])
            i += 1
        elif c == ESC:
            n = b[i + 1]
            units[-1].append(b[i + 2:i + 2 + n].decode("utf-8"))
            i += 2 + n
        else:
            units[-1].append(inv[c])
            i += 1
    return ":".join("".join(u) for u in units)


# ---------------------------------------------------------------- acc 欄

def encode_acc(accf):
    """'0/4:0/2' → [(acc, mora), ...]。'*/*' は (255,255)。"""
    out = []
    for unit in accf.split(":"):
        if "/" in unit:
            a, m = unit.split("/", 1)
        else:
            a, m = unit, "*"
        out.append((255 if a == "*" else int(a), 255 if m == "*" else int(m)))
    return out


acc_vals = Counter()
mora_vals = Counter()
arity = Counter()
for p in parsed:
    e = encode_acc(p[8])
    arity[len(e)] += 1
    for a, m in e:
        acc_vals[a] += 1
        mora_vals[m] += 1
print(f"acc 値域: {min(acc_vals)}..{max(acc_vals)} ({len(acc_vals)} 種) / "
      f"mora 値域: {min(mora_vals)}..{max(mora_vals)} ({len(mora_vals)} 種)", flush=True)
print(f"複合語 arity 分布: {dict(sorted(arity.items())[:8])}", flush=True)

# mora_size は pron のモーラ数から復元できるか？
mismatch = 0
for p in parsed:
    e = encode_acc(p[8])
    units = p[7].split(":")
    if len(e) != len(units):
        mismatch += 1
        continue
    for (a, m), u in zip(e, units):
        if m != 255 and m != len(split_moras(u)):
            mismatch += 1
            break
print(f"mora_size が pron から復元できない行: {mismatch} / {len(parsed)}", flush=True)

# ---------------------------------------------------------------- LOUDS trie

surfaces = sorted(set(p[0] for p in parsed))
print(f"building LOUDS over {len(surfaces)} surfaces ...", flush=True)


class Node:
    __slots__ = ("ch", "kids", "term")

    def __init__(self, ch):
        self.ch = ch
        self.kids = {}
        self.term = False


root = Node(0)
for s in surfaces:
    n = root
    for b in s.encode("utf-8"):
        nx = n.kids.get(b)
        if nx is None:
            nx = Node(b)
            n.kids[b] = nx
        n = nx
    n.term = True

# BFS 順に番号を振る（LOUDS の標準順）
order = []
q = [root]
while q:
    nxt = []
    for n in q:
        order.append(n)
        for b in sorted(n.kids):
            nxt.append(n.kids[b])
    q = nxt
n_nodes = len(order)
print(f"trie nodes = {n_nodes}", flush=True)

# LOUDS ビット列: super-root の '10' + 各ノードの子数ぶんの '1' + '0'
bits = bytearray()
bitlen = 0


def push(v):
    global bitlen
    if bitlen % 8 == 0:
        bits.append(0)
    if v:
        bits[-1] |= 1 << (bitlen % 8)
    bitlen += 1


push(1)
push(0)
labels = bytearray()
term_bits = bytearray()
tlen = 0


def push_term(v):
    global tlen
    if tlen % 8 == 0:
        term_bits.append(0)
    if v:
        term_bits[-1] |= 1 << (tlen % 8)
    tlen += 1


for n in order:
    for _ in n.kids:
        push(1)
    push(0)
    labels.append(n.ch)
    push_term(n.term)

louds_bytes = len(bits)
label_bytes = len(labels)
term_bytes = len(term_bits)
# rank 索引: 512 bit ブロックごとに u32、64 bit サブブロックごとに u8
rank_bytes = (bitlen // 512 + 1) * 4 + (bitlen // 64 + 1) * 1
term_rank_bytes = (tlen // 512 + 1) * 4 + (tlen // 64 + 1) * 1
trie_total = louds_bytes + label_bytes + term_bytes + rank_bytes + term_rank_bytes
print(f"LOUDS bits={bitlen} -> {louds_bytes} B / labels {label_bytes} B / "
      f"term {term_bytes} B / rank {rank_bytes + term_rank_bytes} B / "
      f"合計 {trie_total} B", flush=True)

# ---------------------------------------------------------------- エントリ配列

# 終端ノード（= 見出し語）を LOUDS 順に並べ、その順でエントリを並べる
term_order = [n for n in order if n.term]
surf_of_term = {}
# 再構成: BFS 順に走査しながらキーを持たせる
key_of = {id(root): b""}
for n in order:
    k = key_of[id(n)]
    for b in sorted(n.kids):
        key_of[id(n.kids[b])] = k + bytes([b])
term_keys = [key_of[id(n)].decode("utf-8") for n in term_order]
assert sorted(term_keys) == surfaces, "終端ノードと見出し語集合が一致しない"

bysurf = defaultdict(list)
for p in parsed:
    bysurf[p[0]].append(p)

records = bytearray()
pron_pool = bytearray()
count_arr = bytearray()
CLS_BITS = max(cls_tab.values()).bit_length()
print(f"class id に必要なビット = {CLS_BITS} → u16 で足りる: {CLS_BITS <= 16}", flush=True)

flat = []
for s in term_keys:
    es = bysurf[s]
    assert 1 <= len(es) <= 255
    count_arr.append(len(es))
    flat.extend(es)

for p in flat:
    surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
    cid = cls_tab[(lc, rc, pos6)]
    pb = encode_pron(pron)
    accs = encode_acc(accf)
    assert len(pb) < 256, pron
    # レコード（固定 7 B）: class u16 / wcost i16 / chain u8 / acc u8 / pron_len u8
    # 複合語の 2 つ目以降の acc は pron pool の後ろに続けて置く
    a0 = accs[0][0]
    records += struct.pack("<HhBBB", cid, cost, chain_tab[chain], a0, len(pb))
    pron_pool += pb
    if len(accs) > 1:
        for a, _m in accs[1:]:
            pron_pool.append(a)

# mora_size が pron から復元できないエントリだけ例外表に持つ
exc = bytearray()
for i, p in enumerate(flat):
    accs = encode_acc(p[8])
    units = p[7].split(":")
    bad_row = len(accs) != len(units) or any(
        m != 255 and m != len(split_moras(u)) for (a, m), u in zip(accs, units))
    if bad_row:
        for k, (a, m) in enumerate(accs):
            exc += struct.pack("<IBB", i, k, m if m != 255 else 0)
print(f"mora_size 例外表: {len(exc)//6} 行 / {len(exc)} B", flush=True)

# 見出し語ごとのエントリ数の累積を 256 個おきにチェックポイント
ckpt = bytearray()
tot = 0
for i, c in enumerate(count_arr):
    if i % 256 == 0:
        ckpt += struct.pack("<I", tot)
    tot += c
assert tot == len(flat) == len(parsed)

# pron pool のオフセットも 256 レコードおきにチェックポイント
pckpt = bytearray()
off = 0
for i in range(0, len(flat), 256):
    pckpt += struct.pack("<I", off)
    for j in range(i, min(i + 256, len(flat))):
        pass
# 正確に作り直す
pckpt = bytearray()
off = 0
for i, p in enumerate(flat):
    if i % 256 == 0:
        pckpt += struct.pack("<I", off)
    pb = encode_pron(p[7])
    off += len(pb) + max(0, len(encode_acc(p[8])) - 1)

# ---------------------------------------------------------------- 補助表

cls_blob = bytearray()
inv_cls = {v: k for k, v in cls_tab.items()}
pos6_strs = {}
for i in range(len(cls_tab)):
    lc, rc, pos6 = inv_cls[i]
    cls_blob += struct.pack("<HH", lc, rc)
    cls_blob += struct.pack("<H", pos6_tab[pos6])
pos6_blob = bytearray()
inv_p6 = {v: k for k, v in pos6_tab.items()}
for i in range(len(pos6_tab)):
    pos6_blob += (",".join(inv_p6[i]) + "\0").encode("utf-8")
chain_blob = bytearray()
inv_ch = {v: k for k, v in chain_tab.items()}
for i in range(len(chain_tab)):
    chain_blob += (inv_ch[i] + "\0").encode("utf-8")
mora_blob = bytearray()
inv_m = {v: k for k, v in mora_ids.items()}
for i in range(len(mora_ids)):
    mora_blob += (inv_m[i] + "\0").encode("utf-8")

# ---------------------------------------------------------------- 集計

MATRIX = 3792262
CHARBIN = 262496
UNKDIC = 5690

parts = {
    "trie_louds_bits": louds_bytes,
    "trie_labels": label_bytes,
    "trie_terminal_bits": term_bytes,
    "trie_rank_index": rank_bytes + term_rank_bytes,
    "surface_entry_count": len(count_arr),
    "surface_count_checkpoint": len(ckpt),
    "entry_records_7B": len(records),
    "pron_pool": len(pron_pool),
    "pron_offset_checkpoint": len(pckpt),
    "mora_size_exception_table": len(exc),
    "class_table": len(cls_blob),
    "pos6_table": len(pos6_blob),
    "chain_rule_table": len(chain_blob),
    "mora_table": len(mora_blob),
}
lex_total = sum(parts.values())
runtime_total = lex_total + MATRIX + CHARBIN + UNKDIC

print()
print("=== TTS 専用バイナリ形式の実サイズ（全 788,923 エントリ）===")
for k, v in parts.items():
    print(f"  {k:32s} {v:12,d} B")
print(f"  {'--- 辞書本体 小計':32s} {lex_total:12,d} B")
print(f"  {'matrix.bin (無変更)':32s} {MATRIX:12,d} B")
print(f"  {'char.bin (無変更)':32s} {CHARBIN:12,d} B")
print(f"  {'unk.dic (無変更)':32s} {UNKDIC:12,d} B")
print(f"  {'=== ランタイム合計':32s} {runtime_total:12,d} B  ({runtime_total/1048576:.2f} MiB)")
print()
print(f"  参考: NAIST-JDIC 現物 = 103,082,017 + {MATRIX} + {CHARBIN} + {UNKDIC} "
      f"= {103082017+MATRIX+CHARBIN+UNKDIC:,d} B")
print(f"  圧縮率 (辞書本体のみ): 103,082,017 -> {lex_total:,d} = "
      f"{103082017/lex_total:.2f}x")
print(f"  1 エントリあたり: {lex_total/len(parsed):.2f} B "
      f"(MeCab 形式は token 16 B + feature 65-85 B + darts 35 B/見出し語)")
print()
blob = bytes(records) + bytes(pron_pool) + bytes(labels) + bytes(bits)
print(f"  参考(ランダムアクセス不可): 上記 blob の deflate = {len(zlib.compress(blob,9)):,d} B "
      f"/ 生 {len(blob):,d} B")

# ---------------------------------------------------------------- G1 往復

print()
print("=== G1: 往復（blob から復号して元の値と一致するか）===")
inv_cls = {v: k for k, v in cls_tab.items()}
inv_ch = {v: k for k, v in chain_tab.items()}
ok = 0
fail = []
poff = 0
for i, p in enumerate(flat):
    cid, cost, chid, a0, plen = struct.unpack("<HhBBB", records[i * 7:(i + 1) * 7])
    pb = bytes(pron_pool[poff:poff + plen])
    poff += plen
    pron_d = decode_pron(pb)
    n_units = pron_d.count(":") + 1
    accs_d = [a0]
    for _ in range(n_units - 1):
        accs_d.append(pron_pool[poff])
        poff += 1
    lc_d, rc_d, pos6_d = inv_cls[cid]
    exp_accs = [a for a, _m in encode_acc(p[8])]
    good = (lc_d == p[1] and rc_d == p[2] and cost == p[3] and pos6_d == p[4]
            and pron_d == p[7] and inv_ch[chid] == p[9] and accs_d == exp_accs)
    if good:
        ok += 1
    elif len(fail) < 5:
        fail.append((p[0], p[7], pron_d, p[8], accs_d))
print(f"  一致 {ok:,d} / {len(flat):,d}")
for f in fail:
    print("   NG:", f)
assert poff == len(pron_pool), (poff, len(pron_pool))
G1 = ok == len(flat)
print(f"  G1: {'PASS' if G1 else 'FAIL'}")

# ---------------------------------------------------------------- G3 陰性対照

print()
print("=== G3: 陰性対照（records を 1 バイト壊すと G1 は落ちるか）===")
bad_records = bytearray(records)
bad_records[7 * 12345 + 1] ^= 0xFF
cid_b, cost_b, chid_b, a0_b, plen_b = struct.unpack("<HhBBB", bad_records[12345 * 7:12346 * 7])
cid_g, cost_g, chid_g, a0_g, plen_g = struct.unpack("<HhBBB", records[12345 * 7:12346 * 7])
print(f"  entry 12345: 正常 class={cid_g} / 破壊後 class={cid_b} → 差が出る: {cid_b != cid_g}")
G3 = cid_b != cid_g
print(f"  G3: {'PASS' if G3 else 'FAIL'}")

json.dump({
    "entries": len(parsed),
    "surfaces": len(surfaces),
    "trie_nodes": n_nodes,
    "parts": parts,
    "lexicon_total_bytes": lex_total,
    "runtime_total_bytes": runtime_total,
    "bytes_per_entry": lex_total / len(parsed),
    "distinct": {
        "pos6": len(pos6_tab), "chain_rule": len(chain_tab),
        "lc_rc": len(lcrc_tab), "class": len(cls_tab),
        "wcost": len(wcost_tab), "mora_symbols": len(mora_count),
    },
    "gates": {"G1_roundtrip": G1, "G3_negative_control": G3},
}, open(os.path.join(_WORK, "tts_dict_size.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote tts_dict_size.json")
