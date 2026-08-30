"""共有: entries.tsv からバイト単位 trie を組み、BFS 順の平坦配列にする。

v2/v3 の dict-of-dict 実装は 677,700 見出し語 / 2,075,882 ノードだと重いので、
ソート済みキー + スタックで DFS 構築 → CSR → BFS 並べ替え、をやる。
**構造そのものは v2 と同一**であることを G0 で突き合わせる。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 既定は piper-plus の build ツリー由来 (788,923 entries)。
#    pyopenjtalk が実際に読む辞書 (789,388 entries) は ENTRIES_TSV で切り替える。
ENTRIES = os.environ.get("ENTRIES_TSV", os.path.join(_WORK, "entries.tsv"))
CACHE = os.path.join(_WORK, "trie_cache_" +
                     os.path.basename(ENTRIES).replace(".tsv", "") + ".npz")


def load_surfaces():
    ss = set()
    with open(ENTRIES, encoding="utf-8") as f:
        for ln in f:
            ss.add(ln.split("\t", 1)[0])
    return sorted(s.encode("utf-8") for s in ss)


def build_trie(keys):
    """keys: ソート済みの bytes 列。DFS 構築 → BFS 順に並べ替えて返す。

    返り値（すべて BFS 順のノード番号 0..n-1、0 = root）:
      label[i]      : そのノードへ入る辺の 1 バイト（root は 0）
      term[i]       : 終端フラグ
      cs[i], ce[i]  : 子の CSR 範囲（child[cs[i]:ce[i]] が子ノード番号、label 昇順）
      child[]       : 子ノード番号
      parent[i]
    """
    n_est = 1 + sum(len(k) for k in keys)      # 上限
    label = np.zeros(n_est, dtype=np.uint8)
    parent = np.zeros(n_est, dtype=np.int64)
    term = np.zeros(n_est, dtype=np.uint8)
    parent[0] = -1
    n = 1
    stack = [0]                                # stack[d] = 深さ d のノード
    prev = b""
    for k in keys:
        # 共通接頭辞長
        m = min(len(prev), len(k))
        lcp = 0
        while lcp < m and prev[lcp] == k[lcp]:
            lcp += 1
        del stack[lcp + 1:]
        for b in k[lcp:]:
            label[n] = b
            parent[n] = stack[-1]
            stack.append(n)
            n += 1
        term[stack[-1]] = 1
        prev = k
    label = label[:n]
    parent = parent[:n]
    term = term[:n]

    # CSR（DFS 番号のまま）。作成順に親ごとのラベルは昇順になる
    nch = np.zeros(n, dtype=np.int64)
    np.add.at(nch, parent[1:], 1)
    cs = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(nch, out=cs[1:])
    fill = cs[:-1].copy()
    child = np.zeros(n - 1, dtype=np.int64)
    for i in range(1, n):                      # 作成順 = ラベル昇順（親ごと）
        p = parent[i]
        child[fill[p]] = i
        fill[p] += 1

    # BFS 並べ替え
    order = np.zeros(n, dtype=np.int64)
    newid = np.zeros(n, dtype=np.int64)
    order[0] = 0
    newid[0] = 0
    head, tail = 0, 1
    while head < tail:
        v = order[head]
        head += 1
        for j in range(cs[v], cs[v + 1]):
            c = child[j]
            newid[c] = tail
            order[tail] = c
            tail += 1
    assert tail == n

    label2 = label[order]
    term2 = term[order]
    parent2 = np.where(order == 0, -1, newid[parent[order]])
    nch2 = nch[order]
    cs2 = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(nch2, out=cs2[1:])
    # BFS 順では子は連続。cs2[i] からの並びが BFS 番号 = i の子の並び
    child2 = np.arange(1, n, dtype=np.int64)   # BFS の性質: ノード i の子は連番
    return dict(label=label2, term=term2, parent=parent2, cs=cs2, child=child2, n=n)


def get_trie():
    if os.path.exists(CACHE):
        z = np.load(CACHE)
        return {k: z[k] for k in z.files} | {"n": int(z["label"].shape[0])}
    keys = load_surfaces()
    t = build_trie(keys)
    np.savez(CACHE, label=t["label"], term=t["term"], parent=t["parent"],
             cs=t["cs"], child=t["child"])
    return t


if __name__ == "__main__":
    keys = load_surfaces()
    print(f"surfaces = {len(keys):,d}")
    t = build_trie(keys)
    n = t["n"]
    print(f"trie nodes = {n:,d}   (v2 の申告 2,075,882)")
    print(f"terminal   = {int(t['term'].sum()):,d}")
    # BFS 順で子が連番であることの検証
    cs = t["cs"]
    assert np.all(cs[1:] - cs[:-1] == np.bincount(t["parent"][1:], minlength=n))
    ok = True
    for i in range(min(n, 100000)):
        lo, hi = cs[i], cs[i + 1]
        if hi > lo:
            # 子は BFS 番号で連続 lo+1 .. hi （child2 = arange(1,n)）
            pass
    # ラベル昇順の検証（親ごと）
    lab = t["label"]
    seg = np.repeat(np.arange(n), cs[1:] - cs[:-1])
    kids = np.arange(1, n)
    bad = 0
    for s in range(0, n - 1, 1000000):
        e = min(s + 1000000, n - 1)
        sl = seg[s:e]
        lb = lab[kids[s:e]]
        same = sl[1:] == sl[:-1]
        bad += int(np.sum(same & (lb[1:] <= lb[:-1])))
    print(f"親ごとのラベル昇順違反 = {bad}  (0 であること)")
    np.savez(CACHE, label=t["label"], term=t["term"], parent=t["parent"],
             cs=t["cs"], child=t["child"])
    print(f"cached -> {CACHE}")
