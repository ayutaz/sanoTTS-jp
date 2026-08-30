"""G_BV: rank/select 索引が numpy の真値と一致するか + 陰性対照。"""
import numpy as np
from bitvec import make_bv

rng = np.random.default_rng(0)
for nbits, p in [(1000, 0.5), (100000, 0.5), (100000, 0.9), (100000, 0.05), (4151765, 0.5)]:
    bits = (rng.random(nbits) < p).astype(np.uint8)
    bv, parts = make_bv(bits, "t", with_select0=True)
    cs = np.concatenate([[0], np.cumsum(bits, dtype=np.int64)])
    idx = rng.integers(0, nbits + 1, size=400)
    bad_r = sum(1 for i in idx if bv.rank1(int(i)) != int(cs[i]))
    z = np.flatnonzero(bits == 0)
    ks = rng.integers(0, z.shape[0], size=400)
    bad_s = sum(1 for k in ks if bv.select0(int(k)) != int(z[k]))
    bad_b = sum(1 for i in rng.integers(0, nbits, size=400) if bv.bit(int(i)) != int(bits[i]))
    print(f"nbits={nbits:>9,} p={p}  rank NG={bad_r}  select0 NG={bad_s}  bit NG={bad_b}  "
          f"parts={parts}")
    assert bad_r == 0 and bad_s == 0 and bad_b == 0

# 陰性対照: superblock を 1 バイト壊すと rank が落ちること
bits = (rng.random(100000) < 0.5).astype(np.uint8)
bv, _ = make_bv(bits, "t", with_select0=True)
cs = np.concatenate([[0], np.cumsum(bits, dtype=np.int64)])
sup = bytearray(bv.sup)
sup[4] ^= 0x01
bv.sup = bytes(sup)
bad = sum(1 for i in range(0, 100000, 97) if bv.rank1(i) != int(cs[i]))
print(f"陰性対照(sup 1 バイト破壊): rank 不一致 = {bad}  → {'PASS' if bad > 0 else 'FAIL'}")
assert bad > 0
print("G_BV: PASS")
