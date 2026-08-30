"""任意の open_jtalk 辞書ディレクトリから全エントリを取り出す。

戻り値 1 要素:
    (surface, lc, rc, wcost, pos6, orig, read, pron, acc, chain_rule)
"""
import os
import struct

import numpy as np


def load_entries(dic_dir):
    raw = open(os.path.join(dic_dir, "sys.dic"), "rb").read()
    (magic, version, dtype, lexsize, lsize, rsize,
     dsize, tsize, fsize, _d) = struct.unpack("<10I", raw[:40])
    charset = raw[40:72].split(b"\0")[0].decode()
    off = 72
    darts = raw[off:off + dsize]; off += dsize
    tok = raw[off:off + tsize]; off += tsize
    feat = raw[off:off + fsize]
    assert off + fsize == len(raw)

    u = np.frombuffer(darts, dtype=np.uint32)
    base = u[0::2].view(np.int32).astype(np.int64)
    check = u[1::2].astype(np.int64)
    N = base.size

    parent = [np.array([-1], dtype=np.int64)]
    chars = [np.array([0], dtype=np.int64)]
    pos_l = np.array([0], dtype=np.int64)
    id_l = np.array([0], dtype=np.int64)
    nid = 1
    tn, tv = [], []
    while pos_l.size:
        b = base[pos_l]
        ok = (b >= 0) & (b < N)
        p = np.where(ok, b, 0)
        hit = ok & (check[p] == (b & 0xFFFFFFFF)) & (base[p] < 0)
        if hit.any():
            tn.append(id_l[hit]); tv.append(-base[p[hit]] - 1)
        np_, pa_, ch_ = [], [], []
        for s in range(0, pos_l.size, 65536):
            bs = b[s:s + 65536]; ids = id_l[s:s + 65536]
            cand = bs[:, None] + np.arange(1, 257, dtype=np.int64)[None, :]
            v = (cand >= 0) & (cand < N)
            safe = np.where(v, cand, 0)
            m = v & (check[safe] == (bs[:, None] & 0xFFFFFFFF))
            r, c = np.nonzero(m)
            if r.size:
                np_.append(cand[r, c]); pa_.append(ids[r]); ch_.append(c)
        if not np_:
            break
        npos = np.concatenate(np_)
        n = npos.size
        ids = np.arange(nid, nid + n, dtype=np.int64); nid += n
        parent.append(np.concatenate(pa_))
        chars.append(np.concatenate(ch_).astype(np.int64))
        pos_l, id_l = npos, ids

    par = np.concatenate(parent); chs = np.concatenate(chars)
    TN = np.concatenate(tn); TV = np.concatenate(tv)

    out = []
    for nd, val in zip(TN, TV):
        k = bytearray(); x = int(nd)
        while x > 0:
            k.append(int(chs[x])); x = int(par[x])
        k.reverse()
        surf = bytes(k).decode("utf-8", "replace")
        val = int(val); size, idx = val & 0xFF, val >> 8
        for j in range(size):
            i = idx + j
            lc, rc, _pid, cost, fo, _c = struct.unpack("<HHHhII", tok[i * 16:(i + 1) * 16])
            e = feat.index(b"\0", fo)
            fs = feat[fo:e].decode(charset, "replace").split(",")
            assert len(fs) == 11, fs
            out.append((surf, lc, rc, cost, tuple(fs[0:6]),
                        fs[6], fs[7], fs[8], fs[9], fs[10]))
    assert len(out) == lexsize, (len(out), lexsize)
    return out
