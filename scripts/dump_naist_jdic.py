#!/usr/bin/env python3
"""Enumerate every entry of a MeCab/NAIST-JDIC `sys.dic`, surface included.

Why this exists (B-0):
  `sys.dic`'s token array holds NO surface form - only (lcAttr, rcAttr, posid,
  wcost, feature_offset, compound).  The surface strings live exclusively in the
  double-array (darts) trie, so the only way to get a (surface -> feature) table
  is to walk the trie.

Darts format (Taku Kudo's Darts 0.32, as bundled in MeCab):
  8 B per unit: int32 base, uint32 check.  Node identity is the array index.
  Transition on byte c:   p = base[node] + c + 1 ; valid iff check[p] == base[node]
  Terminal (code 0):      p = base[node]         ; valid iff check[p] == base[node]
                                                    and base[p] < 0
                          value = -base[p] - 1
  MeCab packs the value as  (token_index << 8) | n_tokens_with_this_surface.

Verified 2026-08-26 against
  ~/Documents/piper-plus/build/share/open_jtalk/dic/sys.dic
    677,700 surfaces -> 788,923 tokens == header lexsize, every token index hit
  and against pyopenjtalk 0.4.1-post8 run_frontend on 20,000 sampled surfaces
    (17,908 / 18,009 exact; the rest are frontend post-processing, not parse errors)

Usage:
  python dump_naist_jdic.py <sys.dic> <out.tsv>
"""
import io
import struct
import sys
import time

import numpy as np

FEATURE_FIELDS = (
    "pos1", "pos2", "pos3", "pos4", "ctype", "cform",
    "base_form", "read", "pron", "accent", "chain",
)


def load(path):
    raw = open(path, "rb").read()
    (magic, version, dtype, lexsize, lsize, rsize,
     dsize, tsize, fsize, _dummy) = struct.unpack("<10I", raw[:40])
    charset = raw[40:72].split(b"\0")[0].decode()
    assert 72 + dsize + tsize + fsize == len(raw), "truncated sys.dic"
    assert tsize // 16 == lexsize, f"tsize/16={tsize//16} != lexsize={lexsize}"
    off = 72
    d = np.frombuffer(raw, dtype=np.int32, count=dsize // 4, offset=off)
    base = d[0::2].astype(np.int64)
    check = d[1::2].view(np.uint32).astype(np.int64)  # uint32 semantics, int64 storage
    off += dsize
    tok = raw[off:off + tsize]
    off += tsize
    feat = raw[off:off + fsize]
    return dict(lexsize=lexsize, version=version, charset=charset,
                base=base, check=check, tok=tok, feat=feat,
                dsize=dsize, tsize=tsize, fsize=fsize)


def walk(base, check, chunk=65536, verbose=True):
    """Breadth-first walk of the trie.  Yields (surface_bytes, packed_value)."""
    n = len(base)
    codes = np.arange(1, 257, dtype=np.int64)
    pos = np.array([0], dtype=np.int64)
    keys = [b""]
    depth = 0
    out = []
    while len(pos):
        b = base[pos]
        bu = b & 0xFFFFFFFF
        ok = np.nonzero((b >= 0) & (b < n))[0]
        if len(ok):
            tp = b[ok]
            hit = (check[tp] == bu[ok]) & (base[tp] < 0)
            for j in np.nonzero(hit)[0]:
                i = int(ok[j])
                out.append((keys[i], int(-base[b[i]] - 1)))
        npos, nkeys = [], []
        for s in range(0, len(pos), chunk):
            bb, bbu = b[s:s + chunk], bu[s:s + chunk]
            cand = bb[:, None] + codes[None, :]
            inr = (cand >= 0) & (cand < n)
            match = inr & (check[np.where(inr, cand, 0)] == bbu[:, None])
            r, c = np.nonzero(match)
            if len(r):
                npos.append(cand[r, c])
                blk = keys[s:s + chunk]
                nkeys.extend(blk[ri] + bytes((ci,)) for ri, ci in zip(r.tolist(), c.tolist()))
        pos = np.concatenate(npos) if npos else np.array([], dtype=np.int64)
        keys = nkeys
        depth += 1
    if verbose:
        print(f"  trie walked, max depth {depth}, {len(out)} distinct surfaces", flush=True)
    return out


def main():
    dic, out_path = sys.argv[1], sys.argv[2]
    t0 = time.time()
    D = load(dic)
    print(f"{dic}: lexsize={D['lexsize']} charset={D['charset']} "
          f"darts={D['dsize']} token={D['tsize']} feature={D['fsize']}", flush=True)
    surfaces = walk(D["base"], D["check"])

    tok, feat, cs = D["tok"], D["feat"], D["charset"]
    rows = []
    covered = 0
    for kb, v in surfaces:
        ti, cnt = v >> 8, v & 0xFF
        covered += cnt
        s = kb.decode(cs, "replace")
        for i in range(ti, ti + cnt):
            lc, rc, posid, cost, fo, _comp = struct.unpack("<HHHhII", tok[i * 16:(i + 1) * 16])
            e = feat.index(b"\0", fo)
            rows.append((i, s, lc, rc, posid, cost, feat[fo:e].decode(cs, "replace"), e - fo))

    # --- self-checks: these must hold or the walk missed part of the trie
    assert covered == D["lexsize"], f"covered {covered} != lexsize {D['lexsize']}"
    assert len({r[0] for r in rows}) == D["lexsize"], "token indices not contiguous/complete"
    assert sum(r[7] for r in rows) + D["lexsize"] == D["fsize"], "feature bytes unaccounted"

    rows.sort(key=lambda r: r[0])
    bad = 0
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(("token_index", "surface", "lcAttr", "rcAttr", "posid", "wcost")
                           + FEATURE_FIELDS + ("feat_len",)) + "\n")
        for i, s, lc, rc, posid, cost, f, fl in rows:
            p = f.split(",")
            if len(p) != len(FEATURE_FIELDS):
                bad += 1
                p = (p + ["*"] * len(FEATURE_FIELDS))[:len(FEATURE_FIELDS)]
            fh.write("\t".join([str(i), s.replace("\t", " "), str(lc), str(rc), str(posid), str(cost)]
                               + [x.replace("\t", " ") for x in p] + [str(fl)]) + "\n")
    print(f"wrote {out_path}: {len(rows)} tokens / {len(surfaces)} surfaces, "
          f"{bad} malformed features, {time.time() - t0:.1f}s", flush=True)
    # NOTE: read this file back with csv.QUOTE_NONE - surfaces contain bare '"'.


if __name__ == "__main__":
    main()
