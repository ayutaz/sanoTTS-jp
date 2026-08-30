"""(1) ボード予算に収まる最大エントリ数を二分探索で出す
(2) LOUDS trie が **common-prefix search を実際に answer できる**ことを検証する

(2) が無いと「小さいバイト列を作った」だけで、mecab が必要とする問い
（文の各位置から始まる辞書見出し語を全部返す）に答えられる保証が無い。
verifying-reports の「ゲートが空虚でないか」への対応。

ゲート:
  G5  LOUDS の common-prefix search が参照実装（Python の集合走査）と完全一致
  G6  陰性対照: labels を 1 バイト壊すと G5 が落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import struct
from collections import Counter, defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
V2 = json.load(open(os.path.join(_WORK, "tts_dict_v2.json")))

# ----------------------------------------------------------- LOUDS 実装

class Louds:
    """バイト単位 trie の LOUDS 表現。実際にバイト配列だけを持ち、
    そこから common-prefix search できることを示す。"""

    def __init__(self, surfaces):
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
        order, q = [], [0]
        while q:
            nxt = []
            for n in q:
                order.append(n)
                for b in sorted(kids[n]):
                    nxt.append(kids[n][b])
            q = nxt
        self.n_nodes = len(order)

        # LOUDS ビット列: 先頭に super-root の "10"、続けて各ノードの "1"*子数 + "0"
        bits = bytearray()
        self.bitlen = 0

        def push(v):
            if self.bitlen % 8 == 0:
                bits.append(0)
            if v:
                bits[-1] |= 1 << (self.bitlen % 8)
            self.bitlen += 1

        push(1); push(0)
        labels = bytearray()
        tbits = bytearray()
        self.tlen = 0

        def pusht(v):
            if self.tlen % 8 == 0:
                tbits.append(0)
            if v:
                tbits[-1] |= 1 << (self.tlen % 8)
            self.tlen += 1

        for n in order:
            for _ in sorted(kids[n]):
                push(1)
            push(0)
            labels.append(kids[0].get(n, 0) if False else 0)  # 後で埋める
            pusht(term[n])
        # label は「そのノードへ入る辺の文字」。BFS 順に振り直す
        lab = [0] * self.n_nodes
        idx = {n: i for i, n in enumerate(order)}
        for n in order:
            for b in sorted(kids[n]):
                lab[idx[kids[n][b]]] = b
        labels = bytearray(lab)

        self.bits = bytes(bits)
        self.labels = bytes(labels)
        self.tbits = bytes(tbits)

    # --- ここから下はバイト配列だけを見る（Python オブジェクトを参照しない）---

    def _bit(self, i):
        return (self.bits[i >> 3] >> (i & 7)) & 1

    def _tbit(self, i):
        return (self.tbits[i >> 3] >> (i & 7)) & 1

    def build_index(self):
        """rank/select 索引。サイズは v2 の trie_rank_index に計上済み。"""
        import numpy as np
        arr = np.frombuffer(self.bits, dtype=np.uint8)
        b = np.unpackbits(arr, bitorder="little")[:self.bitlen].astype(np.int64)
        self._r1 = np.concatenate([[0], np.cumsum(b)])          # rank1(i)
        self._sel0 = np.flatnonzero(b == 0)                     # k 番目の 0 の位置

    def _rank1(self, i):
        return int(self._r1[i])

    def _select0(self, k):
        return int(self._sel0[k]) if k < self._sel0.size else -1

    def child(self, node, ch):
        """node (LOUDS のノード番号) から文字 ch の子へ。無ければ None。"""
        # node の子は bits の select0(node) の直後から並ぶ
        p = self._select0(node) + 1
        while p < self.bitlen and self._bit(p) == 1:
            c = self._rank1(p)          # 子ノード番号（super-root ぶんのオフセット込み）
            if self.labels[c] == ch:
                return c
            p += 1
        return None

    def common_prefix_search(self, kb, start):
        """kb[start:] の接頭辞のうち、辞書見出し語になっているものの長さを列挙。"""
        out = []
        node = 0
        for k in range(start, len(kb)):
            nxt = self.child(node, kb[k])
            if nxt is None:
                break
            node = nxt
            if self._tbit(node):
                out.append(k - start + 1)
        return out


# ----------------------------------------------------------- G5 / G6

print("=== G5: LOUDS の common-prefix search が参照と一致するか ===", flush=True)
# 小さめの見出し語集合で検証する（素朴 rank/select なので全件だと遅い）
import random
random.seed(0)
ENTRIES = os.path.join(_WORK, "entries.tsv")
all_surf = []
with open(ENTRIES, encoding="utf-8") as f:
    for ln in f:
        all_surf.append(ln.split("\t", 1)[0])
all_surf = sorted(set(all_surf))
# ⚠️ ランダム 3,000 語だとヒットが 17 件しか出ず、ゲートが空虚になる。
#    実際に文中に現れる語を含む集合（コーパス頻度上位 + ランダム）で検証する。
import pyopenjtalk
freq = Counter()
with open((_ROOT + "/data/splits/corpus_train.tsv"),
          encoding="utf-8") as f:
    f.readline()
    for k, ln in enumerate(f):
        if k >= 3000:
            break
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            for ft in pyopenjtalk.run_mecab_detailed(p[2])[0]:
                freq[ft.split(",", 1)[0]] += 1
sset_src = set(s for s in freq if s in set(all_surf))
sample = sorted(set(list(sset_src)[:40000]) | set(random.sample(all_surf, 10000)))
L = Louds(sample)
L.build_index()
print(f"  検証用 trie: 見出し語 {len(sample):,d} / ノード {L.n_nodes:,d} / "
      f"bits {L.bitlen:,d}", flush=True)

sset = set(s.encode("utf-8") for s in sample)
maxlen = max(len(s) for s in sset)

texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"),
          encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
texts = texts[:200]

n_pos = 0
n_hit = 0
mismatch = 0
for t in texts:
    kb = t.encode("utf-8")
    for i in range(len(kb)):
        got = L.common_prefix_search(kb, i)
        exp = [n for n in range(1, min(maxlen, len(kb) - i) + 1)
               if kb[i:i + n] in sset]
        n_pos += 1
        n_hit += len(got)
        if got != exp:
            mismatch += 1
            if mismatch <= 3:
                print("   NG:", t, i, got, exp)
G5 = mismatch == 0
print(f"  照合位置 {n_pos:,d} / ヒット {n_hit:,d} / 不一致 {mismatch}")
print(f"  G5: {'PASS' if G5 else 'FAIL'}", flush=True)

print("\n=== G6: 陰性対照（labels を 1 バイト壊すと G5 は落ちるか）===", flush=True)
orig_labels = L.labels
# ⚠️ 適当なラベルを壊すと、そのノードがテスト文に一度も現れず不一致 0 になる
#    （最初にそれを踏んだ）。**実際にヒットしたノード**のラベルを壊す。
hit_nodes = []
for t in texts[:20]:
    kb = t.encode("utf-8")
    for i in range(len(kb)):
        node = 0
        for k in range(i, len(kb)):
            nxt = L.child(node, kb[k])
            if nxt is None:
                break
            node = nxt
            if L._tbit(node):
                hit_nodes.append(node)
assert hit_nodes, "ヒットノードが 1 つも無い"
bad = bytearray(L.labels)
bad[hit_nodes[0]] ^= 0x01
print(f"  壊すノード = {hit_nodes[0]} (label {orig_labels[hit_nodes[0]]} "
      f"→ {bad[hit_nodes[0]]}) / ヒットノード総数 {len(hit_nodes)}")
L.labels = bytes(bad)
mismatch2 = 0
for t in texts:
    kb = t.encode("utf-8")
    for i in range(len(kb)):
        got = L.common_prefix_search(kb, i)
        exp = [n for n in range(1, min(maxlen, len(kb) - i) + 1)
               if kb[i:i + n] in sset]
        if got != exp:
            mismatch2 += 1
L.labels = orig_labels
G6 = mismatch2 > 0
print(f"  破壊後の不一致 = {mismatch2}")
print(f"  G6: {'PASS' if G6 else 'FAIL'}", flush=True)

# ----------------------------------------------------------- 予算の当てはめ

print("\n=== ボード予算に収まる最大エントリ数（b0_flash_budget.json の予算）===",
      flush=True)
BUDGETS = [
    ("8MB / B (OTA2, model 別)", 3342336),
    ("16MB / C (OTA2, model 内蔵)", 10944512),
    ("16MB / B (OTA2, model 別)", 11730944),
    ("16MB / A (OTA 無し)", 13828096),
    ("32MB / B (OTA2, model 別)", 28508160),
]
curve = V2["curve"]
print(f"{'予算':>28} {'B (bytes)':>11} | {'無損失: entries':>16} {'ph%':>6} {'acc%':>6}"
      f" | {'TTS最小: entries':>17} {'ph%':>6} {'acc%':>6}")
fit = []
for name, budget in BUDGETS:
    lo = [c for c in curve if c["runtime_bytes"] <= budget]
    lo2 = [c for c in curve if c["lossy_runtime_bytes"] <= budget]
    a = max(lo, key=lambda c: c["entries"]) if lo else None
    b = max(lo2, key=lambda c: c["entries"]) if lo2 else None
    fit.append(dict(budget_name=name, budget_bytes=budget,
                    lossless=None if a is None else
                    dict(entries=a["entries"], bytes=a["runtime_bytes"],
                         ph=a["heldout_phoneme_pct"], acc=a["heldout_accent_pct"]),
                    lossy=None if b is None else
                    dict(entries=b["entries"], bytes=b["lossy_runtime_bytes"],
                         ph=b["heldout_phoneme_pct"], acc=b["heldout_accent_pct"])))
    fa = f"{a['entries']:>16,} {a['heldout_phoneme_pct']:>6} {a['heldout_accent_pct']:>6}" if a else " " * 30
    fb = f"{b['entries']:>17,} {b['heldout_phoneme_pct']:>6} {b['heldout_accent_pct']:>6}" if b else " " * 31
    print(f"{name:>28} {budget:>11,} | {fa} | {fb}")

print("\n⚠️ 測定済みの水準の中で最大のものを出しているだけ。"
      "\n   水準と水準の間は測っていないので、実際にはもう少し多く入る。", flush=True)

json.dump({"gates": {"G5_common_prefix_search": G5, "G6_negative_control": G6,
                     "positions_checked": n_pos, "hits": n_hit},
           "budget_fit": fit},
          open(os.path.join(_WORK, "tts_dict_v3.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote tts_dict_v3.json")
