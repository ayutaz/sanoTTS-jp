"""K-1: TTS 専用の辞書バイナリ形式。

計画は `docs/plan/k1-kanji-implementation-plan.md` K-1、
根拠の実測は `docs/research/k1-kanji-katakana-ondevice.md`。

⚠️ **設計は調査で測って決まっている。実装で再検討しない**（計画 §0）:

- trie の鍵は **文字 ID**、**path 圧縮は入れない**（アルファベットを詰めた後は純損）
- **`pron` の dedup はしない**（ポインタ代で純損）。潰すのは **orig / read の例外**
- 値プールのオフセット チェックポイントは **32 間隔**
- `posid` は `(lc, rc, 品詞6つ組)` から一意に決まるのでクラス表に持つ
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

# 小書き仮名。直前の文字とあわせて 1 モーラになる。
SMALL_KANA = frozenset("ァィゥェォャュョヮヵヶ")

_SEP = 0xFE   # 複合語の区切り（feature の ':'）
_ESC = 0xFF   # 1 バイト表に無いモーラ
_MAX_TABLE = 254


def split_moras(s: str) -> list[str]:
    """カタカナ列をモーラに割る。割れない文字は 1 文字 1 モーラ。"""
    out: list[str] = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL_KANA:
            out.append(s[i:i + 2])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return out


class MoraCodec:
    """`pron` を 1 モーラ 1 バイトに詰める。

    出現頻度の上位 254 種を 1 バイトに割り当て、残りは
    `0xFF, 長さ, UTF-8` にエスケープする。`0xFE` は複合語の区切り。
    """

    def __init__(self, table: list[str]) -> None:
        if len(table) > _MAX_TABLE:
            raise ValueError(f"1 バイト表は {_MAX_TABLE} 種まで: {len(table)}")
        self.table = list(table)
        self._id = {m: i for i, m in enumerate(self.table)}

    @classmethod
    def from_prons(cls, prons: Iterable[str]) -> "MoraCodec":
        c: Counter[str] = Counter()
        for p in prons:
            for unit in p.split(":"):
                c.update(split_moras(unit))
        return cls([m for m, _ in c.most_common(_MAX_TABLE)])

    def encode(self, pron: str) -> bytes:
        out = bytearray()
        for k, unit in enumerate(pron.split(":")):
            if k:
                out.append(_SEP)
            for m in split_moras(unit):
                i = self._id.get(m)
                if i is None:
                    b = m.encode("utf-8")
                    out += bytes([_ESC, len(b)]) + b
                else:
                    out.append(i)
        return bytes(out)

    def decode(self, blob: bytes) -> str:
        units: list[list[str]] = [[]]
        i = 0
        while i < len(blob):
            c = blob[i]
            if c == _SEP:
                units.append([])
                i += 1
            elif c == _ESC:
                n = blob[i + 1]
                units[-1].append(blob[i + 2:i + 2 + n].decode("utf-8"))
                i += 2 + n
            else:
                units[-1].append(self.table[c])
                i += 1
        return ":".join("".join(u) for u in units)


class KeyCodec:
    """見出し語の鍵を **文字 ID** で持つ。

    K-1 §6-2 の実測: UTF-8 バイトを鍵にすると trie は 2,075,882 ノード / 3,065,175 B。
    文字 ID にすると 1,178,163 ノード / 1,768,494 B（**−42.3%**）。

    符号:

    - 頻出 **254 文字** … 1 バイト (id 0..253)
    - コーパスに在る他の文字 … `0xFF` + 2 バイト（副表の索引）
    - **それ以外の任意の文字** … `0xFE` + 3 バイト（コードポイント）

    ⚠️ **全域関数でなければならない。** 端末は入力文の任意の文字を符号化して
    trie を引くので、「表に無い文字」で失敗してはいけない。
    ⚠️ そのため 1 バイト表は 254 文字（測定時の 255 ではない）。差は無視できる。

    どの符号も先頭バイトで長さが決まるので **接頭符号**。trie の終端が
    文字の途中に落ちることはない。
    """

    _ESC_TABLE = 0xFF
    _ESC_CP = 0xFE
    _MAX_DIRECT = 254

    def __init__(self, table: list[str], esc_table: list[str]) -> None:
        if len(table) > self._MAX_DIRECT:
            raise ValueError(f"1 バイト表は {self._MAX_DIRECT} 文字まで: {len(table)}")
        if len(esc_table) > 0x10000:
            raise ValueError(f"副表は 65536 文字まで: {len(esc_table)}")
        self.table = list(table)
        self.esc_table = list(esc_table)
        self._id = {c: i for i, c in enumerate(self.table)}
        self._esc_id = {c: i for i, c in enumerate(self.esc_table)}

    @classmethod
    def from_surfaces(cls, surfaces: Iterable[str]) -> "KeyCodec":
        c: Counter[str] = Counter()
        for s in surfaces:
            c.update(s)
        direct = [ch for ch, _ in c.most_common(cls._MAX_DIRECT)]
        seen = set(direct)
        rest = [ch for ch in c if ch not in seen]
        return cls(direct, sorted(rest))

    def encode(self, key: str) -> bytes:
        out = bytearray()
        for ch in key:
            i = self._id.get(ch)
            if i is not None:
                out.append(i)
                continue
            j = self._esc_id.get(ch)
            if j is not None:
                out += bytes([self._ESC_TABLE, j >> 8, j & 0xFF])
                continue
            cp = ord(ch)
            out += bytes([self._ESC_CP, cp >> 16, (cp >> 8) & 0xFF, cp & 0xFF])
        return bytes(out)

    def decode(self, blob: bytes) -> str:
        out: list[str] = []
        i = 0
        while i < len(blob):
            c = blob[i]
            if c == self._ESC_TABLE:
                out.append(self.esc_table[(blob[i + 1] << 8) | blob[i + 2]])
                i += 3
            elif c == self._ESC_CP:
                out.append(chr((blob[i + 1] << 16) | (blob[i + 2] << 8) | blob[i + 3]))
                i += 4
            else:
                out.append(self.table[c])
                i += 1
        return "".join(out)


class Louds:
    """バイト列を鍵とする trie の LOUDS 表現。

    K-1 §2-2 の実測: double-array 23,216,752 B に対し LOUDS 一式で 3,065,175 B。

    ビット列は super-root の `10` に続けて、BFS 順に各ノードの
    「子の数だけ 1、そのあと 0」を並べる。ノード番号も BFS 順。

    ⚠️ **子ノード番号は `rank1(p)`（p より前の 1 の数）。`rank1(p+1)` ではない。**
    調査でこれを間違えたとき、症状は例外ではなく
    **「すべての語が 1 文字に分割される」**だった（K-1 §9-4）。
    """

    def __init__(self, bits: bytearray, bitlen: int, labels: bytearray,
                 term: bytearray, keys: list[bytes] | None = None) -> None:
        self.bits = bits
        self.bitlen = bitlen
        self.labels = labels
        self.term = term
        self._keys = keys
        self._reindex()

    # --- 索引（rank/select）------------------------------------------------
    def _reindex(self) -> None:
        r = [0]
        n = 0
        for i in range(self.bitlen):
            n += self._bit(i)
            r.append(n)
        self._rank1 = r
        self._sel0 = [i for i in range(self.bitlen) if not self._bit(i)]
        t = [0]
        n = 0
        for i in range(len(self.labels)):
            n += self._tbit(i)
            t.append(n)
        self._trank = t

    def _bit(self, i: int) -> int:
        return (self.bits[i >> 3] >> (i & 7)) & 1

    def _tbit(self, i: int) -> int:
        return (self.term[i >> 3] >> (i & 7)) & 1

    # --- 構築 ---------------------------------------------------------------
    @classmethod
    def build(cls, keys) -> "Louds":
        keys = sorted(set(keys))
        kids: list[dict[int, int]] = [dict()]
        term_flag = [False]
        for k in keys:
            n = 0
            for b in k:
                nx = kids[n].get(b)
                if nx is None:
                    nx = len(kids)
                    kids.append(dict())
                    term_flag.append(False)
                    kids[n][b] = nx
                n = nx
            term_flag[n] = True

        order: list[int] = []
        q = [0]
        while q:
            nxt = []
            for n in q:
                order.append(n)
                for b in sorted(kids[n]):
                    nxt.append(kids[n][b])
            q = nxt
        pos = {n: i for i, n in enumerate(order)}

        bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
        bits = bytearray((bitlen + 7) // 8)
        labels = bytearray(len(order))
        term = bytearray((len(order) + 7) // 8)

        def setbit(buf: bytearray, i: int) -> None:
            buf[i >> 3] |= 1 << (i & 7)

        setbit(bits, 0)                       # super-root
        p = 2
        for n in order:
            if term_flag[n]:
                setbit(term, pos[n])
            for b in sorted(kids[n]):
                labels[pos[kids[n][b]]] = b
                setbit(bits, p)
                p += 1
            p += 1                            # 子の並びの終端 0
        assert p == bitlen, (p, bitlen)
        return cls(bits, bitlen, labels, term, keys)

    # --- 検索 ---------------------------------------------------------------
    def _child(self, node: int, ch: int) -> int | None:
        p = self._sel0[node] + 1
        while p < self.bitlen and self._bit(p):
            c = self._rank1[p]            # ⚠️ p より前の 1 の数
            if c < len(self.labels) and self.labels[c] == ch:
                return c
            p += 1
        return None

    def common_prefix_search(self, seq: bytes, start: int) -> list[tuple[int, int]]:
        """seq[start:] の接頭辞のうち鍵になっているものを (長さ, 終端 rank) で返す。"""
        out: list[tuple[int, int]] = []
        node = 0
        for k in range(start, len(seq)):
            nxt = self._child(node, seq[k])
            if nxt is None:
                break
            node = nxt
            if self._tbit(node):
                out.append((k - start + 1, self._trank[node]))
        return out

    def visited_nodes(self, seq: bytes, start: int) -> list[int]:
        """検索中に実際に辿ったノード。陰性対照で「効く場所」を壊すために使う。"""
        out: list[int] = []
        node = 0
        for k in range(start, len(seq)):
            nxt = self._child(node, seq[k])
            if nxt is None:
                break
            node = nxt
            out.append(node)
        return out

    def key_of(self, rank: int) -> bytes:
        if self._keys is None:
            raise ValueError("鍵を保持していない（from_bytes で読んだ場合）")
        return self._keys[rank]

    def with_broken_label(self, node: int) -> "Louds":
        """陰性対照用: 指定ノードのラベルを 1 ビット反転した複製を返す。"""
        lab = bytearray(self.labels)
        lab[node] ^= 0x01
        return Louds(bytearray(self.bits), self.bitlen, lab,
                     bytearray(self.term), self._keys)

    # --- 直列化 -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        head = self.bitlen.to_bytes(4, "little") + len(self.labels).to_bytes(4, "little")
        return head + bytes(self.bits) + bytes(self.labels) + bytes(self.term)

    @classmethod
    def from_bytes(cls, blob: bytes) -> "Louds":
        bitlen = int.from_bytes(blob[0:4], "little")
        n_nodes = int.from_bytes(blob[4:8], "little")
        o = 8
        nb = (bitlen + 7) // 8
        bits = bytearray(blob[o:o + nb]); o += nb
        labels = bytearray(blob[o:o + n_nodes]); o += n_nodes
        nt = (n_nodes + 7) // 8
        term = bytearray(blob[o:o + nt])
        return cls(bits, bitlen, labels, term, None)
