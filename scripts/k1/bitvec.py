"""rank/select 索引を**実バイト列として作る**。サイズは len() で取る。

v2 の見積り式は superblock 512 bit + block u8 だったが、512 bit の superblock 内の
先行 1 の個数は最大 448 で **u8 に入らない**。ここでは superblock 256 bit にして
block u8 が必ず入るようにする（superblock 内の先行 1 は最大 192）。
G_BV でこの索引が numpy の cumsum と一致することを検査する。
"""
import numpy as np

SUPER = 256      # bits per superblock (u32 counter)
BLOCK = 64       # bits per block      (u8 counter, relative to superblock)
SEL_SAMPLE = 512  # select 標本の間隔


def pack_bits(bits_np):
    """bits_np: 0/1 の uint8 配列 → LSB-first のバイト列"""
    return np.packbits(bits_np, bitorder="little").tobytes()


def build_rank(bits_np):
    """(superblock_bytes, block_bytes) を実バイト列で返す。"""
    n = bits_np.shape[0]
    cs = np.concatenate([[0], np.cumsum(bits_np, dtype=np.int64)])
    n_super = n // SUPER + 1
    n_block = n // BLOCK + 1
    sup = cs[np.arange(n_super) * SUPER].astype(np.uint32)
    blk_abs = cs[np.arange(n_block) * BLOCK]
    sup_of_blk = sup[(np.arange(n_block) * BLOCK) // SUPER].astype(np.int64)
    rel = blk_abs - sup_of_blk
    assert rel.max() < 256, f"block counter overflow: {rel.max()}"
    return sup.tobytes(), rel.astype(np.uint8).tobytes()


def build_select0_sample(bits_np):
    """SEL_SAMPLE 個ごとの 0 の位置を u32 で。"""
    z = np.flatnonzero(bits_np == 0)
    return z[::SEL_SAMPLE].astype(np.uint32).tobytes(), int(z.shape[0])


class BV:
    """バイト列だけを見る rank/select。読み出しは全部 _rd() を通す（Q4 の計測用）。"""

    def __init__(self, bits_bytes, sup, blk, nbits, sel0=None, n_zeros=0, base=0,
                 name="bv", probe=None):
        self.b = bits_bytes
        self.sup = sup
        self.blk = blk
        self.nbits = nbits
        self.sel0 = sel0
        self.n_zeros = n_zeros
        self.base = base          # 辞書イメージ中の bits の開始オフセット
        self.name = name
        self.probe = probe        # Q4 用フック f(array_name, offset, nbytes)

    # --- 実バイト読み出し ---
    def _rd(self, arr, arrname, off, n):
        if self.probe is not None:
            self.probe(arrname, off, n)
        return arr[off:off + n]

    def bit(self, i):
        byte = self._rd(self.b, self.name + ".bits", i >> 3, 1)[0]
        return (byte >> (i & 7)) & 1

    def rank1(self, i):
        """[0, i) の 1 の個数"""
        s = i // SUPER
        v = int.from_bytes(self._rd(self.sup, self.name + ".sup", s * 4, 4), "little")
        bl = i // BLOCK
        v += self._rd(self.blk, self.name + ".blk", bl, 1)[0]
        lo = bl * BLOCK
        nb = (i - lo) >> 3
        if nb:
            w = self._rd(self.b, self.name + ".bits", lo >> 3, nb)
            v += int.from_bytes(w, "little").bit_count()
        rem = (i - lo) & 7
        if rem:
            byte = self._rd(self.b, self.name + ".bits", (lo >> 3) + nb, 1)[0]
            v += (byte & ((1 << rem) - 1)).bit_count()
        return v

    def rank0(self, i):
        return i - self.rank1(i)

    def select0(self, k):
        """k 番目 (0-origin) の 0 の位置"""
        lo = int.from_bytes(
            self._rd(self.sel0, self.name + ".sel0", (k // SEL_SAMPLE) * 4, 4), "little")
        # lo から前方走査（標本間隔ぶん）。ブロック単位で 0 の個数を数える
        want = k
        # superblock 単位で飛ばす
        s = lo // SUPER
        while (s + 1) * SUPER <= self.nbits:
            z = (s + 1) * SUPER - int.from_bytes(
                self._rd(self.sup, self.name + ".sup", (s + 1) * 4, 4), "little")
            if z > want:
                break
            s += 1
        pos = s * SUPER
        base1 = int.from_bytes(self._rd(self.sup, self.name + ".sup", s * 4, 4), "little")
        # block 単位
        bl = s * (SUPER // BLOCK)
        while (bl + 1) * BLOCK <= self.nbits and (bl + 1) % (SUPER // BLOCK) != 0:
            z = (bl + 1) * BLOCK - (base1 + self._rd(self.blk, self.name + ".blk",
                                                     bl + 1, 1)[0])
            if z > want:
                break
            bl += 1
        pos = bl * BLOCK
        cnt0 = pos - (base1 + self._rd(self.blk, self.name + ".blk", bl, 1)[0])
        # バイト単位
        p = pos >> 3
        while p < len(self.b):
            byte = self._rd(self.b, self.name + ".bits", p, 1)[0]
            z = 8 - byte.bit_count()
            if cnt0 + z > want:
                for j in range(8):
                    if not ((byte >> j) & 1):
                        if cnt0 == want:
                            return p * 8 + j
                        cnt0 += 1
                break
            cnt0 += z
            p += 1
        return -1


def make_bv(bits_np, name, with_select0=False):
    bb = pack_bits(bits_np)
    sup, blk = build_rank(bits_np)
    sel0, nz = (build_select0_sample(bits_np) if with_select0 else (None, 0))
    return BV(bb, sup, blk, int(bits_np.shape[0]), sel0, nz, name=name), \
        dict(bits=len(bb), sup=len(sup), blk=len(blk),
             sel0=(len(sel0) if sel0 else 0))
