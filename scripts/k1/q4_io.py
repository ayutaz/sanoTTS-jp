"""Q4: フラッシュのアクセスパターン（esp_partition_mmap / 64 KB MMU ページ /
32 B キャッシュライン）を、LOUDS 形式で**実際に計測**する。

やること:
  1. 現行 v2 形式の全パートに実オフセットを割り当てて 1 枚のイメージにする
  2. common-prefix-search + エントリ引きを行い、**バイト読み出しを全部記録**する
  3. 文ごとに (a) ランダム読み (b) 触れた 64 KB ページ数 (c) 触れた 32 B ライン数

B-0（元の darts）の申告値: trie 読み ~420 / token・feature 読み 165 /
**1 文あたり 64 KB ページ 91 枚**。LOUDS はポインタ追跡が多いので悪化しうる。

ゲート:
  G4-1 探索結果が総当り参照と一致（測っている経路が本物であること）
  G4-2 記録した読み出しが必ずイメージの範囲内で、パートの境界をまたがない
  G4-3 陰性対照: probe を外すと読み出し 0 件になる（= probe が実際に効いている）
  G4-4 陰性対照: 全パートを 1 ページに畳んだ配置ではページ数が 1 になる
       （= ページ数の計算がオフセットに反応している）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import statistics
import struct
import sys
import time
from collections import Counter, defaultdict

import numpy as np

from trie_common import get_trie, load_surfaces
from tries import PlainLouds

SP = os.path.dirname(os.path.abspath(__file__))
HELDOUT = (_ROOT + "/data/splits/corpus_heldout.tsv")
NS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
PAGE = 65536
LINE = 32

# ---------------------------------------------------------------- 構造
t = get_trie()
label, term, cs = t["label"], t["term"], t["cs"]
n = label.shape[0]
PL = PlainLouds(label, term, cs)
parts = PL.parts()

# 見出し語ごとのエントリ数（BFS 終端順）。v2 と同じ並び。
surfaces = load_surfaces()
sset = set(surfaces)
maxlen = max(len(s) for s in surfaces)
cnt_by_surface = Counter()
with open(os.environ.get("ENTRIES_TSV", os.path.join(_WORK, "entries.tsv")), encoding="utf-8") as f:
    for ln in f:
        cnt_by_surface[ln.split("\t", 1)[0]] += 1
# BFS 終端順の見出し語列を復元
term_idx = np.flatnonzero(term)
# ノード -> 見出し語（親を遡って組む）。BFS なので親は必ず小さい番号
par = np.zeros(n, dtype=np.int64)
deg = (cs[1:] - cs[:-1]).astype(np.int64)
par[1:] = np.repeat(np.arange(n), deg)
lab_l = label.tolist()
par_l = par.tolist()
counts = []
for nd in term_idx.tolist():
    b = bytearray()
    v = nd
    while v:
        b.append(lab_l[v])
        v = par_l[v]
    counts.append(cnt_by_surface[bytes(reversed(b)).decode("utf-8")])
N_SURF = len(counts)
N_ENT = sum(counts)
assert N_ENT == sum(cnt_by_surface.values()), N_ENT
print(f"見出し語 {N_SURF:,d} / エントリ {N_ENT:,d}", flush=True)

# ⚠️ 以前は v2 の json をそのまま読んでいたが、辞書リビジョンを切り替えると
#    合わなくなるので**データから組み直す**。既定辞書では v2 と一致することを確かめる。
SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


rows = defaultdict(list)
mcnt = Counter()
raw = []
lcls, lchain, pos6_tab = {}, {}, {}
with open(os.environ.get("ENTRIES_TSV", os.path.join(_WORK, "entries.tsv")), encoding="utf-8") as f:
    for ln in f:
        surf, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t")
        fs = feat.split(",")
        raw.append((surf, fs[6], fs[7], fs[8], fs[9]))
        pos6 = tuple(fs[0:6])
        pos6_tab.setdefault(pos6, len(pos6_tab))
        lcls.setdefault((int(lc), int(rc), pos6), len(lcls))
        lchain.setdefault(fs[10], len(lchain))
        for u in fs[8].split(":"):
            mcnt.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mcnt.most_common(254))}
inv_mora = {v: k for k, v in mora_ids.items()}
for surf, orig, read, pron, accf in raw:
    pl = 0
    for k, unit in enumerate(pron.split(":")):
        if k:
            pl += 1
        for m in split_moras(unit):
            pl += 1 if m in mora_ids else 2 + len(m.encode("utf-8"))
    el = 2 * (accf.count(":") + 1)
    if orig != surf:
        el += 1 + len(orig.encode("utf-8"))
    if read != pron:
        el += 1 + len(read.encode("utf-8"))
    rows[surf].append(pl + el)
flat_len = []
for nd in term_idx.tolist():
    b = bytearray()
    v = nd
    while v:
        b.append(lab_l[v])
        v = par_l[v]
    flat_len.extend(rows[bytes(reversed(b)).decode("utf-8")])
assert len(flat_len) == N_ENT
pool_off = np.concatenate([[0], np.cumsum(np.array(flat_len, dtype=np.int64))])

# --- v2 の各パートを**データから組み直す**（辞書リビジョンに追随させるため）---
pos6_blob = b"".join((",".join(k) + "\0").encode("utf-8")
                     for k, _ in sorted(pos6_tab.items(), key=lambda kv: kv[1]))
chain_blob = b"".join((k + "\0").encode("utf-8")
                      for k, _ in sorted(lchain.items(), key=lambda kv: kv[1]))
mora_blob = b"".join((inv_mora[i] + "\0").encode("utf-8") for i in range(len(mora_ids)))
V2 = {
    "surface_entry_count": N_SURF,
    "surface_count_checkpoint": ((N_SURF + 255) // 256) * 4,
    "entry_records_9B": N_ENT * 9,
    "pool_offset_checkpoint": ((N_ENT + 255) // 256) * 4,
    "value_pool": int(pool_off[-1]),
    "class_table": len(lcls) * 6,
    "pos6_table": len(pos6_blob),
    "chain_rule_table": len(chain_blob),
    "mora_table": len(mora_blob),
}
REF = json.load(open(os.path.join(_WORK, "tts_dict_v2.json")))["full"]["parts"]
same = {k: (V2[k], REF[k]) for k in V2 if V2[k] != REF[k]}
print(f"  組み直したパートと v2 json の差: "
      f"{'一致' if not same else same}", flush=True)

LAYOUT = [(k, parts[k]) for k in
          ("louds.bits", "louds.sup", "louds.blk", "louds.sel0", "labels",
           "term.bits", "term.sup", "term.blk")] + \
         [(k, V2[k]) for k in
          ("surface_entry_count", "surface_count_checkpoint", "entry_records_9B",
           "pool_offset_checkpoint", "value_pool", "class_table", "pos6_table",
           "chain_rule_table", "mora_table")]
BASE = {}
o = 0
for nm, sz in LAYOUT:
    BASE[nm] = o
    o += sz
IMG = o
ent_base = np.concatenate([[0], np.cumsum(np.array(counts, dtype=np.int64))])
print(f"辞書イメージ = {IMG:,d} B ({IMG/1048576:.2f} MiB) / {len(LAYOUT)} パート")
print(f"  ページ数 {(IMG+PAGE-1)//PAGE:,d} (64 KB) / value_pool {V2['value_pool']:,d} B",
      flush=True)

# ---------------------------------------------------------------- probe
READS = []


def probe(arr, off, nb):
    READS.append((arr, off, nb))


PL.probe = probe
PL.bv.probe = probe
PL.tbv.probe = probe

# v2 は 256 ごと。ここが I/O を支配しうるので掃引する。
CKPT_S = int(os.environ.get("CKPT_S", 256))
CKPT_P = int(os.environ.get("CKPT_P", 256))


def lookup_entries(sid, explicit_offsets=False):
    """見出し語 ID → その見出し語のエントリを全部読む（v2 の形式どおり）。

    v2 は「エントリ先頭添字」も「pool オフセット」も持たず、256 ごとの
    チェックポイントから**走査**して復元する。その読み出しも全部数える。
    """
    if explicit_offsets:
        probe("surface_entry_ptr", sid * 4, 4)
        probe("surface_entry_ptr", (sid + 1) * 4, 4)
        base = int(ent_base[sid])
        k = int(ent_base[sid + 1]) - base
    else:
        ck = sid // CKPT_S
        probe("surface_count_checkpoint", ck * 4, 4)
        r = sid % CKPT_S
        if r:
            probe("surface_entry_count", ck * CKPT_S, r)     # 連続走査
        probe("surface_entry_count", sid, 1)
        base = int(ent_base[sid])
        k = counts[sid]
    if explicit_offsets:
        probe("pool_ptr", base * 4, 4)
        po = int(pool_off[base])
    else:
        ck2 = base // CKPT_P
        probe("pool_offset_checkpoint", ck2 * 4, 4)
        r2 = base % CKPT_P
        if r2:
            probe("entry_records_9B", ck2 * CKPT_P * 9, r2 * 9)  # 長さ欄の走査
        po = int(pool_off[base])
    for j in range(k):
        probe("entry_records_9B", (base + j) * 9, 9)
        L = flat_len[base + j]
        probe("value_pool", po, L)
        po += L
        probe("class_table", 0, 6)      # class_id → (lc, rc, pos6) の 6 B
    return k


def run(texts, explicit_offsets=False, do_lookup=True, check=False):
    per = []
    bad = 0
    for tx in texts:
        READS.clear()
        kb = tx.encode("utf-8")
        nhit = 0
        for i in range(len(kb)):
            got = PL.common_prefix_search(kb, i)
            if check:
                exp = [ln for ln in range(1, min(maxlen, len(kb) - i) + 1)
                       if kb[i:i + ln] in sset]
                if [ln for ln, _ in got] != exp:
                    bad += 1
            for _, nd in got:
                nhit += 1
                if do_lookup:
                    lookup_entries(PL.surface_id(nd), explicit_offsets)
        pages, lines, nb = set(), set(), 0
        for a, off, l in READS:
            b0 = BASE.get(a)
            if b0 is None:            # 明示オフセット案の追加配列
                b0 = IMG
            s = b0 + off
            e = s + l
            nb += l
            pages.update(range(s // PAGE, (e - 1) // PAGE + 1))
            lines.update(range(s // LINE, (e - 1) // LINE + 1))
        per.append(dict(reads=len(READS), bytes=nb, pages=len(pages),
                        lines=len(lines), hits=nhit, chars=len(tx)))
    return per, bad


texts = []
with open(HELDOUT, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3 and p[2]:
            texts.append(p[2])
gt = texts[:NS]
print(f"\n計測文 = {len(gt)} (held-out 全 {len(texts)} 文の先頭)", flush=True)


def stat(per, key):
    v = sorted(x[key] for x in per)
    return (statistics.mean(v), statistics.median(v),
            v[int(0.95 * (len(v) - 1))], v[-1])


t0 = time.time()
per_full, bad = run(gt, explicit_offsets=False, do_lookup=True, check=True)
G41 = bad == 0
print(f"G4-1 探索結果が総当り参照と一致: {'PASS' if G41 else 'FAIL'} (不一致 {bad})"
      f"   ({time.time()-t0:.1f}s)", flush=True)

print("\n=== 1 文あたり（LOUDS + v2 のチェックポイント走査。B-0 と同じ土俵）===")
print(f"{'指標':>22} {'平均':>10} {'中央値':>9} {'p95':>10} {'最大':>10}  B-0(darts)")
B0 = {"reads": "~585", "pages": "91", "lines": "—", "bytes": "—"}
for k, nm in (("reads", "ランダム読み"), ("bytes", "読んだバイト"),
              ("pages", "64 KB ページ"), ("lines", "32 B ライン")):
    m, md, p95, mx = stat(per_full, k)
    print(f"{nm:>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}  {B0[k]:>10}")
m, md, p95, mx = stat(per_full, "hits")
print(f"{'辞書ヒット数':>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}")
m, md, p95, mx = stat(per_full, "chars")
print(f"{'文字数':>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}", flush=True)

# 内訳
agg = Counter()
aggb = Counter()
for tx in gt[:60]:
    READS.clear()
    kb = tx.encode("utf-8")
    for i in range(len(kb)):
        for _, nd in PL.common_prefix_search(kb, i):
            lookup_entries(PL.surface_id(nd))
    for a, off, l in READS:
        agg[a] += 1
        aggb[a] += l
tot_r = sum(agg.values())
print(f"\n=== 読み出しの内訳（先頭 60 文の合計 {tot_r:,d} 回）===")
for a, c in agg.most_common():
    print(f"  {a:28s} {c:>9,d} 回 ({100*c/tot_r:5.2f}%)  {aggb[a]:>10,d} B")

# trie 部分 / エントリ部分の切り分け
TRIE = {"louds.bits", "louds.sup", "louds.blk", "louds.sel0", "labels",
        "term.bits", "term.sup", "term.blk"}
per_trie, _ = run(gt, do_lookup=False)
print("\n=== trie 探索だけ（エントリ引き無し）===")
for k, nm in (("reads", "ランダム読み"), ("pages", "64 KB ページ"),
              ("lines", "32 B ライン")):
    m, md, p95, mx = stat(per_trie, k)
    print(f"{nm:>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}")

# ---------------------------------------------------------------- idvar trie の I/O
print("\n=== Q1 で最小だった idvar 符号化 trie の trie 部分だけ ===", flush=True)
from trie_common import build_trie
cnt_ch = Counter()
for s in surfaces:
    cnt_ch.update(s.decode("utf-8"))
cid = {c: i for i, (c, _) in enumerate(cnt_ch.most_common())}


def enc_idvar(s):
    out = bytearray()
    for c in s:
        i = cid.get(c)
        if i is None:
            out += b"\xff\xff\xff\xff"
        elif i < 255:
            out.append(i)
        else:
            out += b"\xff" + i.to_bytes(2, "big")
    return bytes(out)


kv = sorted(set(enc_idvar(s.decode("utf-8")) for s in surfaces))
tv = build_trie(kv)
PV = PlainLouds(tv["label"], tv["term"], tv["cs"])
pv = PV.parts()
BV_ = {}
o = 0
for k in ("louds.bits", "louds.sup", "louds.blk", "louds.sel0", "labels",
          "term.bits", "term.sup", "term.blk"):
    BV_[k] = o
    o += pv[k]
IMGV = o
RV = []
PV.probe = lambda a, off, nb: RV.append((a, off, nb))
PV.bv.probe = PV.probe
PV.tbv.probe = PV.probe
per_v = []
for tx in gt:
    RV.clear()
    kb = enc_idvar(tx)
    boff = [0]
    for c in tx:
        boff.append(len(enc_idvar(tx[:len(boff)])))
    for b0 in boff[:-1]:
        PV.common_prefix_search(kb, b0)
    pages, lines, nb = set(), set(), 0
    for a, off, l in RV:
        s = BV_[a] + off
        nb += l
        pages.update(range(s // PAGE, (s + l - 1) // PAGE + 1))
        lines.update(range(s // LINE, (s + l - 1) // LINE + 1))
    per_v.append(dict(reads=len(RV), bytes=nb, pages=len(pages), lines=len(lines)))
print(f"  trie イメージ {IMGV:,d} B（utf8 trie は "
      f"{sum(parts.values()):,d} B）")
for k, nm in (("reads", "ランダム読み"), ("pages", "64 KB ページ"),
              ("lines", "32 B ライン")):
    m, md, p95, mx = stat(per_v, k)
    print(f"{nm:>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}")

# 明示オフセット案
per_exp, _ = run(gt, explicit_offsets=True, do_lookup=True)
print("\n=== 参考: 明示オフセット表を足した場合（+ 6.2 MB のフラッシュと引き換え）===")
for k, nm in (("reads", "ランダム読み"), ("bytes", "読んだバイト"),
              ("pages", "64 KB ページ"), ("lines", "32 B ライン")):
    m, md, p95, mx = stat(per_exp, k)
    print(f"{nm:>22} {m:>10.1f} {md:>9.0f} {p95:>10.0f} {mx:>10.0f}")

# ------------------------------------------------- checkpoint 間隔の掃引
print("\n=== checkpoint 間隔の掃引（オフセット復元が I/O を支配するか）===", flush=True)
print(f"{'間隔':>8} {'表の追加B':>12} {'読み/文':>10} {'バイト/文':>12} "
      f"{'ページ/文':>10} {'ライン/文':>10}")
sweep_rows = []
for iv in (256, 128, 64, 32, 16):
    CKPT_S = CKPT_P = iv
    globals()['CKPT_S'] = iv; globals()['CKPT_P'] = iv
    tab = ((N_SURF + iv - 1) // iv) * 4 + ((N_ENT + iv - 1) // iv) * 4
    pr, _ = run(gt, do_lookup=True)
    r = dict(interval=iv, table_bytes=tab, reads=stat(pr, "reads")[0],
             bytes=stat(pr, "bytes")[0], pages=stat(pr, "pages")[0],
             lines=stat(pr, "lines")[0])
    sweep_rows.append(r)
    print(f"{iv:>8} {tab:>12,} {r['reads']:>10.1f} {r['bytes']:>12,.0f} "
          f"{r['pages']:>10.1f} {r['lines']:>10.1f}", flush=True)
globals()['CKPT_S'] = 256; globals()['CKPT_P'] = 256
pr, _ = run(gt, do_lookup=True, explicit_offsets=True)
tab_e = N_SURF * 4 + N_ENT * 4
sweep_rows.append(dict(interval="explicit", table_bytes=tab_e,
                       reads=stat(pr, "reads")[0], bytes=stat(pr, "bytes")[0],
                       pages=stat(pr, "pages")[0], lines=stat(pr, "lines")[0]))
print(f"{'明示':>8} {tab_e:>12,} {stat(pr,'reads')[0]:>10.1f} "
      f"{stat(pr,'bytes')[0]:>12,.0f} {stat(pr,'pages')[0]:>10.1f} "
      f"{stat(pr,'lines')[0]:>10.1f}", flush=True)

# ---------------------------------------------------------------- 陰性対照
print("\n=== 陰性対照 ===", flush=True)
PL.probe = None
PL.bv.probe = None
PL.tbv.probe = None
READS.clear()
for i in range(len(gt[0].encode("utf-8"))):
    PL.common_prefix_search(gt[0].encode("utf-8"), i)
G43 = len(READS) == 0
print(f"  G4-3 probe を外すと読み出し 0 件: {len(READS)} 件 "
      f"{'PASS' if G43 else 'FAIL'}")
PL.probe = probe
PL.bv.probe = probe
PL.tbv.probe = probe
READS.clear()
for i in range(len(gt[0].encode("utf-8"))):
    PL.common_prefix_search(gt[0].encode("utf-8"), i)
print(f"       probe を戻すと {len(READS):,d} 件")

# ⚠️ 最初「全パートの base を 0 に畳めばページ数 1 になるはず」と書いたが**誤り**。
#    パート内のオフセットが 11 MB まで伸びるので畳んでも 1 ページにならない。
#    ページ計算そのものが PAGE に反応しているかを直接確かめる。
savedP = PAGE
PAGE = 1 << 30                     # イメージ全体より大きいページ
per_one, _ = run(gt[:20], do_lookup=True)
mp = statistics.mean(x["pages"] for x in per_one)
PAGE = LINE                        # ページ = ライン なら両者一致するはず
per_two, _ = run(gt[:20], do_lookup=True)
same = all(x["pages"] == x["lines"] for x in per_two)
PAGE = savedP
per_chk, _ = run(gt[:20], do_lookup=True)
G44 = mp == 1.0 and same
print(f"  G4-4a PAGE=1 GiB → 1 文あたりページ数 = {mp:.2f} (1.00 であること)")
print(f"  G4-4b PAGE=32 B → ページ数 == ライン数: {same}")
print(f"       PAGE=64 KB に戻すと {statistics.mean(x['pages'] for x in per_chk):.2f}  "
      f"{'PASS' if G44 else 'FAIL'}", flush=True)

json.dump(dict(image_bytes=IMG, layout={k: [BASE[k], dict(LAYOUT)[k]] for k, _ in LAYOUT},
               n_sentences=len(gt),
               full={k: stat(per_full, k) for k in
                     ("reads", "bytes", "pages", "lines", "hits", "chars")},
               trie_only={k: stat(per_trie, k) for k in ("reads", "pages", "lines")},
               explicit_offsets={k: stat(per_exp, k) for k in
                                 ("reads", "bytes", "pages", "lines")},
               breakdown={k: [agg[k], aggb[k]] for k in agg},
               ckpt_sweep=sweep_rows,
               gates=dict(G4_1=G41, G4_3=G43, G4_4=G44)),
          open(os.path.join(_WORK, "q4.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote q4.json")
