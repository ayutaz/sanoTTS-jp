"""MeCab の Viterbi を自前で再実装する（戦略 D の土台）。

connection matrix を差し替えて劣化を測るために必要。まず「素の matrix.bin なら
pyopenjtalk と一致する」ことを示さないと、量子化の差なのか自分の実装のバグなのか
区別できない。**その一致率が戦略 D の上限になる。**
"""
from __future__ import annotations

import os
import sys

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import mecabres as R  # noqa: E402

MAX_GROUPING = 24


class Tok:
    """MeCab の辞書と char.bin / unk.dic をまとめて持つ。"""

    def __init__(self, dic, maxlen):
        self.dic = dic
        self.maxlen = maxlen
        self.mat, self.lsize, self.rsize = R.load_matrix()
        self.names, self.info = R.load_charprop()
        self.unk, _ = R.load_unk()
        self.space = R.charinfo(self.info, " ")

    def ci(self, ch):
        return R.charinfo(self.info, ch)

    @staticmethod
    def is_kind_of(c, other):
        """other が c の type マスクに含まれるか（MeCab の CharInfo::isKindOf）。"""
        return bool(c["type"] & (1 << other["default_type"]))

    def seek_other(self, text, i, c):
        """text[i:] のうち c と同種の文字を読み飛ばし、(次位置, 直前に見た charinfo, 個数)。"""
        n = len(text)
        cnt = 0
        cur = c
        fail = c
        while i < n:
            fail = self.ci(text[i])
            if not self.is_kind_of(cur, fail):
                break
            i += 1
            cnt += 1
            cur = fail
        return i, fail, cnt

    def lookup(self, text, i):
        """位置 i から始まりうるノードを MeCab と同じ規則で列挙する。

        戻り値: list[(begin, end, lc, rc, wcost, feature12, is_unk)]
        """
        n = len(text)
        # 1) 先頭の空白類を読み飛ばす（MeCab は space_ で seekToOtherType する）
        b2, cinfo, _ = self.seek_other(text, i, self.space)
        if b2 >= n:
            return []
        out = []
        # 2) 辞書引き（common prefix search）
        hi = min(self.maxlen, n - b2)
        for L in range(1, hi + 1):
            s = text[b2:b2 + L]
            ents = self.dic.get(s)
            if ents:
                for lc, rc, pid, cost, feat in ents:
                    out.append((b2, b2 + L, lc, rc, cost, s + "," + feat, False))
        # 3) 未知語（result が空、または invoke が立っているとき）
        if out and not cinfo["invoke"]:
            return out
        cat = self.names[cinfo["default_type"]]
        uents = self.unk.get(cat) or self.unk["DEFAULT"]

        def add(end):
            s = text[b2:end]
            for lc, rc, pid, cost, feat in uents:
                out.append((b2, end, lc, rc, cost, s + "," + feat, True))

        b3 = b2 + 1
        if b3 > n:
            add(n)
            return out
        gb3 = None
        if cinfo["group"]:
            b3g, _, clen = self.seek_other(text, b3, cinfo)
            if clen <= MAX_GROUPING:
                add(b3g)
            gb3 = b3g
        p = b3
        for k in range(1, cinfo["length"] + 1):
            if p > n:
                break
            if p != gb3:
                add(p)
            if p >= n:
                break
            if not self.is_kind_of(cinfo, self.ci(text[p])):
                break
            p += 1
        return out

    def parse(self, text, mat=None):
        """Viterbi。戻り値: list[(begin, end, feature12, is_unk)]。"""
        mat = self.mat if mat is None else mat
        ls = self.lsize
        n = len(text)
        INF = 1 << 40
        # ends[p] = list of (cost, rc, node, prev_index_in_ends[node_begin_end])
        ends = {0: [(0, 0, None, None)]}   # BOS: rcAttr=0
        for i in range(n):
            prevs = ends.get(i)
            if not prevs:
                continue
            for nd in self.lookup(text, i):
                b, e, lc, rc, wcost, feat, isunk = nd
                best = INF
                bp = None
                base = ls * lc
                for pi, (pc, prc, _, _) in enumerate(prevs):
                    c = pc + int(mat[prc + base])
                    if c < best:
                        best = c
                        bp = (i, pi)
                ends.setdefault(e, []).append((best + wcost, rc, nd, bp))
        prevs = ends.get(n)
        if not prevs:
            return None
        best = INF
        bp = None
        for pi, (pc, prc, _, _) in enumerate(prevs):
            c = pc + int(mat[prc + 0])     # EOS: lcAttr=0
            if c < best:
                best = c
                bp = (n, pi)
        path = []
        while bp is not None:
            p, pi = bp
            cost, rc, nd, nbp = ends[p][pi]
            if nd is None:
                break
            path.append((nd[0], nd[1], nd[5], nd[6]))
            bp = nbp
        path.reverse()
        return path
