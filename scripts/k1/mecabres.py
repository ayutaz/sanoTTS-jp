"""matrix.bin / char.bin / unk.dic を読む（すべて read-only）。

pyopenjtalk が実際に読む辞書ディレクトリから取る。piper-plus 側ではない。
"""
from __future__ import annotations

import os
import struct

import numpy as np


def dict_dir() -> str:
    import pyopenjtalk
    d = pyopenjtalk.OPEN_JTALK_DICT_DIR
    return d.decode() if isinstance(d, bytes) else str(d)


def load_matrix(path: str | None = None):
    """(mat[int16, lsize*rsize], lsize, rsize) を返す。

    MeCab の連接コストは cost = mat[left.rcAttr + lsize * right.lcAttr]。
    """
    path = path or os.path.join(dict_dir(), "matrix.bin")
    b = open(path, "rb").read()
    lsize, rsize = struct.unpack("<HH", b[:4])
    mat = np.frombuffer(b[4:4 + lsize * rsize * 2], dtype="<i2")
    assert mat.size == lsize * rsize, (mat.size, lsize, rsize)
    assert 4 + mat.size * 2 == len(b), (len(b),)
    return mat.astype(np.int32).copy(), lsize, rsize


def load_charprop(path: str | None = None):
    """(names, info[np.uint32, 0xffff]) を返す。

    info の bitfield: type:18 / default_type:8 / length:4 / group:1 / invoke:1
    """
    path = path or os.path.join(dict_dir(), "char.bin")
    b = open(path, "rb").read()
    (csize,) = struct.unpack("<I", b[:4])
    off = 4
    names = []
    for i in range(csize):
        names.append(b[off:off + 32].split(b"\0")[0].decode())
        off += 32
    info = np.frombuffer(b[off:off + 4 * 0xFFFF], dtype="<u4")
    assert info.size == 0xFFFF
    assert off + info.size * 4 == len(b), (off, info.size, len(b))
    return names, info


def charinfo(info, ch: str):
    o = ord(ch)
    v = int(info[o]) if o < 0xFFFF else int(info[0])
    return {"type": v & 0x3FFFF,
            "default_type": (v >> 18) & 0xFF,
            "length": (v >> 26) & 0xF,
            "group": (v >> 30) & 1,
            "invoke": (v >> 31) & 1}


def load_unk(path: str | None = None):
    """unk.dic を darts から復元して {カテゴリ名: [(lc, rc, posid, cost, feature)]} を返す。"""
    path = path or os.path.join(dict_dir(), "unk.dic")
    b = open(path, "rb").read()
    (magic, version, dtype, lexsize, lsize, rsize,
     dsize, tsize, fsize, dummy) = struct.unpack("<10I", b[:40])
    charset = b[40:72].split(b"\0")[0].decode()
    off = 72
    darts = b[off:off + dsize]; off += dsize
    tok = b[off:off + tsize]; off += tsize
    feat = b[off:off + fsize]
    assert off + fsize == len(b)

    u = np.frombuffer(darts, dtype=np.uint32)
    base = u[0::2].view(np.int32).astype(np.int64)
    check = u[1::2].astype(np.int64)
    N = base.size

    out: dict[str, list] = {}
    # ノード数が小さいので素直に総当たりで木をたどる
    stack = [(0, b"")]
    while stack:
        nid, key = stack.pop()
        B = base[nid]
        if 0 <= B < N and check[B] == (B & 0xFFFFFFFF) and base[B] < 0:
            val = int(-base[B] - 1)
            size = val & 0xFF
            idx = val >> 8
            ents = []
            for j in range(size):
                i = idx + j
                lc, rc, pid, cost, fo, comp = struct.unpack("<HHHhII", tok[i * 16:(i + 1) * 16])
                e = feat.index(b"\0", fo)
                ents.append((lc, rc, pid, cost, feat[fo:e].decode(charset, "replace")))
            out[key.decode(charset, "replace")] = ents
        for c in range(1, 257):
            P = B + c
            if 0 <= P < N and check[P] == (B & 0xFFFFFFFF):
                # darts は base + (バイト値 + 1) を辿るので、実バイトは c-1
                stack.append((int(P), key + bytes([c - 1])))
    assert sum(len(v) for v in out.values()) == lexsize, \
        (sum(len(v) for v in out.values()), lexsize)
    return out, lexsize
