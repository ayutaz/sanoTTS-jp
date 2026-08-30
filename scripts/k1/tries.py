"""LOUDS trie（素）と PATRICIA / tail 圧縮 trie を、**実バイト列として**組む。

どちらも common-prefix-search をバイト配列だけから answer する。
Python オブジェクト（dict / list of children）は探索中に一切見ない。
"""
import numpy as np
from bitvec import make_bv, BV


# =====================================================================  素の LOUDS
class PlainLouds:
    def __init__(self, label, term, cs, probe=None):
        n = label.shape[0]
        self.n = n
        deg = (cs[1:] - cs[:-1]).astype(np.int64)
        # LOUDS ビット列: "10" + 各ノード(BFS 順) の 1^deg 0
        bits = np.ones(2 + int(deg.sum()) + n, dtype=np.uint8)
        bits[1] = 0
        pos = 2
        # ベクトル化: 各ノードの 0 の位置 = 2 + cumsum(deg+1) - 1
        zero_pos = 2 + np.cumsum(deg + 1) - 1
        bits[zero_pos] = 0
        self.bitlen = int(bits.shape[0])
        self.bv, self.p_louds = make_bv(bits, "louds", with_select0=True)
        self.tbv, self.p_term = make_bv(term.astype(np.uint8), "term", with_select0=False)
        self.labels = label.tobytes()
        self.probe = probe
        self.bv.probe = probe
        self.tbv.probe = probe

    def parts(self):
        return {
            "louds.bits": self.p_louds["bits"], "louds.sup": self.p_louds["sup"],
            "louds.blk": self.p_louds["blk"], "louds.sel0": self.p_louds["sel0"],
            "labels": len(self.labels),
            "term.bits": self.p_term["bits"], "term.sup": self.p_term["sup"],
            "term.blk": self.p_term["blk"],
        }

    def _label(self, c):
        if self.probe:
            self.probe("labels", c, 1)
        return self.labels[c]

    def child(self, v, ch):
        s0 = self.bv.select0(v)
        s1 = self.bv.select0(v + 1)
        deg = s1 - s0 - 1
        if deg <= 0:
            return None
        c0 = self.bv.rank1(s0 + 1)
        lo, hi = 0, deg - 1
        while lo <= hi:                      # ラベル昇順なので二分探索
            m = (lo + hi) // 2
            L = self._label(c0 + m)
            if L == ch:
                return c0 + m
            if L < ch:
                lo = m + 1
            else:
                hi = m - 1
        return None

    def common_prefix_search(self, kb, start):
        out = []
        v = 0
        for k in range(start, len(kb)):
            nx = self.child(v, kb[k])
            if nx is None:
                break
            v = nx
            if self.tbv.bit(v):
                out.append((k - start + 1, v))
        return out

    def surface_id(self, node):
        return self.tbv.rank1(node)


# ==============================================================  PATRICIA (tail 圧縮)
def build_patricia(label, term, cs, min_chain=1):
    """keep なノードだけ残し、単一子の非終端ノードを tail に畳む。

    min_chain: 連鎖の長さがこれ未満なら**畳まない**（ポインタ代が合わないため）。
               min_chain=1 で従来どおり全部畳む。

    返り値: dict(labels, term, deg, tails(list[bytes]), 元ノード番号)
    """
    n = label.shape[0]
    deg = (cs[1:] - cs[:-1]).astype(np.int64)
    collaps = (term == 0) & (deg == 1)
    collaps[0] = False
    only_child = np.where(deg == 1, cs[:-1] + 1, -1)   # BFS 順は子が連番

    if min_chain > 1:
        # 連鎖ごとの総長を出し、短い連鎖は畳まない
        cl = np.zeros(n, dtype=np.int32)
        col = collaps.tolist()
        oc = only_child.tolist()
        cl_l = [0] * n
        for i in range(n - 1, 0, -1):
            if col[i]:
                c = oc[i]
                cl_l[i] = 1 + (cl_l[c] if col[c] else 0)
        par = np.zeros(n, dtype=np.int64)
        par[1:] = np.repeat(np.arange(n), deg)          # BFS: 子は連番
        par_l = par.tolist()
        tot = [0] * n
        for i in range(1, n):
            if col[i]:
                p = par_l[i]
                tot[i] = tot[p] if col[p] else cl_l[i]
        collaps = collaps & (np.array(tot, dtype=np.int64) >= min_chain)

    keep = ~collaps
    keep[0] = True

    order = [0]
    p_lab = [0]
    p_tail = [b""]
    p_term = [int(term[0])]
    p_deg = []
    head = 0
    lab_py = label.tolist()
    while head < len(order):
        v = order[head]
        head += 1
        c0 = int(cs[v]) + 1
        d = int(deg[v])
        p_deg.append(d)
        for j in range(d):
            c = c0 + j
            first = lab_py[c]
            t = bytearray()
            while not keep[c]:
                c = int(only_child[c])
                t.append(lab_py[c])
            order.append(c)
            p_lab.append(first)
            p_tail.append(bytes(t))
            p_term.append(int(term[c]))
    return dict(order=np.array(order, dtype=np.int64),
                labels=np.array(p_lab, dtype=np.uint8),
                term=np.array(p_term, dtype=np.uint8),
                deg=np.array(p_deg, dtype=np.int64),
                tails=p_tail,
                n_collapsed=int((~keep).sum()))


class TailLouds:
    """tail 圧縮 LOUDS。tail は length-prefix つきプールに置き、
    tail を持つノードだけ has_tail ビット + ポインタで参照する。"""

    def __init__(self, pat, dedup=True, ptr_mode="offset", probe=None):
        labels = pat["labels"]
        term = pat["term"]
        deg = pat["deg"]
        tails = pat["tails"]
        n = labels.shape[0]
        self.n = n
        bits = np.ones(2 + int(deg.sum()) + n, dtype=np.uint8)
        bits[1] = 0
        bits[2 + np.cumsum(deg + 1) - 1] = 0
        self.bitlen = int(bits.shape[0])
        self.bv, self.p_louds = make_bv(bits, "clouds", with_select0=True)
        self.tbv, self.p_term = make_bv(term.astype(np.uint8), "cterm", with_select0=False)
        has = np.array([1 if t else 0 for t in tails], dtype=np.uint8)
        self.hbv, self.p_has = make_bv(has, "has_tail", with_select0=False)
        self.labels = labels.tobytes()

        # --- tail プール（1 バイト長プレフィクス）---
        # ptr_mode="offset": ノードごとにプール中のバイトオフセットを持つ
        # ptr_mode="id"    : ノードごとに tail ID（distinct 数ぶんの幅）を持ち、
        #                    ID→オフセット表を 1 段挟む
        pool = bytearray()
        ptr = []
        seen = {}
        idtab = []
        for t in tails:
            if not t:
                continue
            assert len(t) < 256, f"tail too long: {len(t)}"
            if dedup and t in seen:
                ptr.append(seen[t])
                continue
            o = len(pool)
            i = len(idtab)
            if dedup:
                seen[t] = i if ptr_mode == "id" else o
            pool += bytes([len(t)]) + t
            idtab.append(o)
            ptr.append(i if ptr_mode == "id" else o)
        self.n_tails = len(ptr)
        self.n_distinct = len(idtab)
        self.pool = bytes(pool)
        self.mode = ptr_mode
        W = max(1, (max(ptr).bit_length() + 7) // 8) if ptr else 1
        self.W = W
        self.ptr = b"".join(int(o).to_bytes(W, "little") for o in ptr)
        if ptr_mode == "id":
            IW = max(1, (max(idtab).bit_length() + 7) // 8) if idtab else 1
            self.IW = IW
            self.idtab = b"".join(int(o).to_bytes(IW, "little") for o in idtab)
        else:
            self.IW = 0
            self.idtab = b""
        self.probe = probe
        for b in (self.bv, self.tbv, self.hbv):
            b.probe = probe

    def parts(self):
        return {
            "clouds.bits": self.p_louds["bits"], "clouds.sup": self.p_louds["sup"],
            "clouds.blk": self.p_louds["blk"], "clouds.sel0": self.p_louds["sel0"],
            "labels": len(self.labels),
            "cterm.bits": self.p_term["bits"], "cterm.sup": self.p_term["sup"],
            "cterm.blk": self.p_term["blk"],
            "has_tail.bits": self.p_has["bits"], "has_tail.sup": self.p_has["sup"],
            "has_tail.blk": self.p_has["blk"],
            f"tail_ptr({self.W}B)": len(self.ptr),
            f"tail_idtab({self.IW}B)": len(self.idtab),
            "tail_pool": len(self.pool),
        }

    def _label(self, c):
        if self.probe:
            self.probe("clabels", c, 1)
        return self.labels[c]

    def tail_of(self, v):
        """v の tail を返す（バイト配列だけを見る）"""
        if not self.hbv.bit(v):
            return b""
        k = self.hbv.rank1(v)
        if self.probe:
            self.probe("tail_ptr", k * self.W, self.W)
        o = int.from_bytes(self.ptr[k * self.W:(k + 1) * self.W], "little")
        if self.mode == "id":
            if self.probe:
                self.probe("tail_idtab", o * self.IW, self.IW)
            o = int.from_bytes(self.idtab[o * self.IW:(o + 1) * self.IW], "little")
        if self.probe:
            self.probe("tail_pool", o, 1)
        ln = self.pool[o]
        if self.probe:
            self.probe("tail_pool", o + 1, ln)
        return self.pool[o + 1:o + 1 + ln]

    def child(self, v, ch):
        s0 = self.bv.select0(v)
        s1 = self.bv.select0(v + 1)
        d = s1 - s0 - 1
        if d <= 0:
            return None
        c0 = self.bv.rank1(s0 + 1)
        lo, hi = 0, d - 1
        while lo <= hi:
            m = (lo + hi) // 2
            L = self._label(c0 + m)
            if L == ch:
                return c0 + m
            if L < ch:
                lo = m + 1
            else:
                hi = m - 1
        return None

    def common_prefix_search(self, kb, start):
        out = []
        v = 0
        k = start
        while k < len(kb):
            nx = self.child(v, kb[k])
            if nx is None:
                break
            k += 1
            t = self.tail_of(nx)
            if t:
                if kb[k:k + len(t)] != t:
                    break
                k += len(t)
            v = nx
            if self.tbv.bit(v):
                out.append((k - start, v))
        return out

    def surface_id(self, node):
        return self.tbv.rank1(node)
