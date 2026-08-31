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

import struct
from collections import Counter
from typing import Iterable, NamedTuple

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
        self.has_rank_index = False
        self.max_block_count = 0
        self._sup = self._blk = self._sel = b""
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

        # ⚠️ **終端 rank は BFS 順であって辞書順ではない。**
        #    ここを取り違えると lookup が静かに別のエントリを返す（TDD で検出）。
        key_of_node: dict[int, bytes] = {0: b""}
        for n in order:
            for b in sorted(kids[n]):
                key_of_node[kids[n][b]] = key_of_node[n] + bytes([b])
        term_keys = [key_of_node[n] for n in order if term_flag[n]]

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
        return cls(bits, bitlen, labels, term, term_keys)

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
        """終端 rank から鍵を戻す。**rank は BFS 順**（辞書順ではない）。"""
        if self._keys is None:
            raise ValueError("鍵を保持していない（from_bytes で読んだ場合）")
        return self._keys[rank]

    def terminal_keys(self) -> list[bytes]:
        """終端 rank 順の鍵。値の並び順はこれに合わせること。

        直列化した blob から読み戻した場合は **trie を BFS で辿って復元する**。
        鍵は trie そのものが持っているので、別に文字列表を置くのは冗長
        （370,863 entries では 3,881,011 B = blob の 33% に相当した）。
        """
        if self._keys is not None:
            return list(self._keys)
        keys: list[bytes] = []
        # BFS。ノード番号は BFS 順なので、番号順に親から埋まる。
        prefix: dict[int, bytes] = {0: b""}
        for node in range(len(self.labels)):
            if node:
                pass
            p = self._sel0[node] + 1
            while p < self.bitlen and self._bit(p):
                c = self._rank1[p]
                if c < len(self.labels):
                    prefix[c] = prefix[node] + bytes([self.labels[c]])
                p += 1
            if self._tbit(node):
                keys.append(prefix[node])
        self._keys = keys
        return list(keys)

    def with_broken_label(self, node: int) -> "Louds":
        """陰性対照用: 指定ノードのラベルを 1 ビット反転した複製を返す。"""
        lab = bytearray(self.labels)
        lab[node] ^= 0x01
        return Louds(bytearray(self.bits), self.bitlen, lab,
                     bytearray(self.term), self._keys)

    # --- rank / select 索引 -------------------------------------------------
    #
    # 端末は blob を flash から mmap して読む。索引を起動時に作ると RAM を食うので
    # **blob に入れて持ち歩く**（K-1 §2-2 のサイズ計上にも入っている）。
    #
    # ⚠️ superblock は **256 bit**。512 bit にすると superblock 内の先行 1 の数が
    #    最大 448 になり **u8 に入らない**（K-1 §6-1 の訂正）。
    #
    SUPER_BITS = 256
    BLOCK_BITS = 64
    SELECT_STEP = 512

    def build_rank_index(self) -> tuple[bytes, bytes, bytes]:
        sup = bytearray()
        blk = bytearray()
        sel = bytearray()
        total = 0
        zeros = 0
        self.max_block_count = 0
        for i in range(0, self.bitlen, self.BLOCK_BITS):
            if i % self.SUPER_BITS == 0:
                sup += total.to_bytes(4, "little")
                base = total
            c = total - base
            if c > self.max_block_count:
                self.max_block_count = c
            blk.append(c)
            for j in range(i, min(i + self.BLOCK_BITS, self.bitlen)):
                if self._bit(j):
                    total += 1
                else:
                    if zeros % self.SELECT_STEP == 0:
                        sel += j.to_bytes(4, "little")
                    zeros += 1
        return bytes(sup), bytes(blk), bytes(sel)

    def rank1_indexed(self, i: int) -> int:
        """索引だけを使った rank1（C が同じ手順でやる）。"""
        s = int.from_bytes(self._sup[4 * (i // self.SUPER_BITS):
                                     4 * (i // self.SUPER_BITS) + 4], "little")
        s += self._blk[i // self.BLOCK_BITS]
        for j in range(i - i % self.BLOCK_BITS, i):
            s += self._bit(j)
        return s

    def select0_indexed(self, k: int) -> int:
        """索引だけを使った select0。"""
        base = k // self.SELECT_STEP
        pos = int.from_bytes(self._sel[4 * base:4 * base + 4], "little")
        need = k - base * self.SELECT_STEP
        while need:
            if not self._bit(pos):
                need -= 1
            pos += 1
        while self._bit(pos):
            pos += 1
        return pos

    # --- 直列化 -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        sup, blk, sel = self.build_rank_index()
        head = struct.pack("<IIIII", self.bitlen, len(self.labels),
                           len(sup), len(blk), len(sel))
        return (head + bytes(self.bits) + bytes(self.labels) + bytes(self.term)
                + sup + blk + sel)

    @classmethod
    def from_bytes(cls, blob: bytes) -> "Louds":
        bitlen, n_nodes, n_sup, n_blk, n_sel = struct.unpack("<IIIII", blob[:20])
        o = 20
        nb = (bitlen + 7) // 8
        bits = bytearray(blob[o:o + nb]); o += nb
        labels = bytearray(blob[o:o + n_nodes]); o += n_nodes
        nt = (n_nodes + 7) // 8
        term = bytearray(blob[o:o + nt]); o += nt
        d = cls(bits, bitlen, labels, term, None)
        d._sup = blob[o:o + n_sup]; o += n_sup
        d._blk = blob[o:o + n_blk]; o += n_blk
        d._sel = blob[o:o + n_sel]
        d.has_rank_index = True
        d.max_block_count = max(d._blk) if d._blk else 0
        return d


class ConnMatrix:
    """MeCab の接続コスト行列。

    ⚠️ **索引は `flat[rc_prev + lsize * lc_cur]`。**
    MeCab 本家のソースは `matrix_[lcAttr + lsize_*rcAttr]` と書いてあるが、
    **この辞書では合わない**。MeCab 自身が申告する link_cost から逆算して
    1,537/1,537 = 100.00% で確定した式がこちら（K-1 §9-3）。
    他の 3 通りの索引式はいずれも一致 1% 未満だった。

    ⚠️ `link_cost` は `word_cost` を含む（`link_cost = trans + word_cost`）。
    生の遷移コストと比べると**陰性対照が壊れる**（実際に踏んだ）。
    """

    def __init__(self, lsize: int, rsize: int, data: bytes) -> None:
        self.lsize = lsize
        self.rsize = rsize
        self.data = data
        if len(data) != 2 * lsize * rsize:
            raise ValueError(f"行列の大きさが合わない: {len(data)} != "
                             f"2*{lsize}*{rsize}")

    @classmethod
    def from_matrix_bin(cls, raw: bytes) -> "ConnMatrix":
        """`matrix.bin` をそのまま受ける（先頭 4 B が lsize/rsize）。"""
        lsize = int.from_bytes(raw[0:2], "little")
        rsize = int.from_bytes(raw[2:4], "little")
        return cls(lsize, rsize, raw[4:4 + 2 * lsize * rsize])

    def trans(self, rc_prev: int, lc_cur: int) -> int:
        i = 2 * (rc_prev + self.lsize * lc_cur)
        return int.from_bytes(self.data[i:i + 2], "little", signed=True)

    def to_section(self) -> bytes:
        return (self.lsize.to_bytes(2, "little") + self.rsize.to_bytes(2, "little")
                + self.data)

    @classmethod
    def from_section(cls, blob: bytes) -> "ConnMatrix":
        return cls.from_matrix_bin(blob)


class Entry(NamedTuple):
    """辞書 1 エントリ。sys.dic の token + feature 11 列に対応する。"""
    surface: str
    lc: int
    rc: int
    wcost: int
    pos6: tuple                 # (品詞, 細分類1..3, 活用型, 活用形)
    posid: int
    orig: str
    read: str
    pron: str
    acc: str                    # "1/2" / "0/3:0/3" / "*/*"
    chain: str


def _encode_acc(acc: str) -> list[tuple[int, int]]:
    out = []
    for unit in acc.split(":"):
        a, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        out.append((255 if a == "*" else int(a), 255 if m == "*" else int(m)))
    return out


def _decode_acc(vals: list[tuple[int, int]]) -> str:
    return ":".join(("*" if a == 255 else str(a)) + "/" + ("*" if m == 255 else str(m))
                    for a, m in vals)


class DictBlob:
    """TTS 専用の辞書バイナリ。

    レコードは固定長、可変長の値は 1 本のプールに置く。
    オフセットは **32 エントリおきのチェックポイント + レコードの長さ欄**から復元する
    （K-1 §4-3: 256 間隔だと 1 回の復元で最大 2,295 B 読むことになる）。

    `orig` / `read` は多くが冗長なのでフラグで持ち、例外だけプールに置く
    （K-1 §2-2: `orig` は 63.73% が見出し語と一致、`read` は 82.88% が `pron` と一致）。
    """

    CHECKPOINT = 32

    _F_ORIG_EQ_SURFACE = 0x01
    _F_READ_EQ_PRON = 0x02

    def __init__(self, *, keys: KeyCodec, moras: MoraCodec, louds: Louds,
                 classes: list[tuple], chains: list[str], counts: list[int],
                 records: bytes, pool: bytes, surfaces: list[str],
                 matrix: "ConnMatrix | None" = None) -> None:
        self.keys = keys
        self.moras = moras
        self.louds = louds
        self.classes = classes
        self.chains = chains
        self.counts = counts
        self.records = records
        self.pool = pool
        self.matrix = matrix
        if surfaces is None:
            surfaces = [self.keys.decode(k) for k in louds.terminal_keys()]
        self.surfaces = surfaces
        self._start = [0]
        for c in counts:
            self._start.append(self._start[-1] + c)

    # --- 構築 ---------------------------------------------------------------
    @classmethod
    def build(cls, entries: list[Entry],
              matrix: "ConnMatrix | None" = None) -> "DictBlob":
        keys = KeyCodec.from_surfaces(e.surface for e in entries)
        moras = MoraCodec.from_prons(
            [e.pron for e in entries] + [e.read for e in entries])

        by: dict[str, list[Entry]] = {}
        for e in entries:
            by.setdefault(e.surface, []).append(e)
        surfaces = sorted(by)
        louds = Louds.build([keys.encode(s) for s in surfaces])
        # ⚠️ **値の並びは LOUDS の終端 rank 順に合わせる。** 辞書順ではない。
        order = [keys.decode(k) for k in louds.terminal_keys()]
        assert sorted(order) == surfaces, "終端鍵と見出し語集合が食い違う"
        surf_id = {s: i for i, s in enumerate(order)}

        classes: dict[tuple, int] = {}
        chains: dict[str, int] = {}
        flat: list[Entry] = []
        counts: list[int] = []
        for s in order:
            counts.append(len(by[s]))
            flat.extend(by[s])
        for e in flat:
            classes.setdefault((e.lc, e.rc, e.pos6, e.posid), len(classes))
            chains.setdefault(e.chain, len(chains))

        records = bytearray()
        pool = bytearray()
        for e in flat:
            flags = 0
            extra = bytearray()
            if e.orig == e.surface:
                flags |= cls._F_ORIG_EQ_SURFACE
            elif e.orig in surf_id:
                # E1: 見出し語集合にある orig は ID で持つ（K-1 §6-3: 99.79%）
                extra += b"\x01" + surf_id[e.orig].to_bytes(3, "little")
            else:
                b = e.orig.encode("utf-8")
                extra += b"\x00" + bytes([len(b)]) + b
            if e.read == e.pron:
                flags |= cls._F_READ_EQ_PRON
            else:
                rb = moras.encode(e.read)     # E3: read もモーラ ID 列で持つ
                extra += bytes([len(rb)]) + rb
            pb = moras.encode(e.pron)
            accs = _encode_acc(e.acc)
            for a, m in accs:
                extra += bytes([a, m])
            if len(pb) > 255 or len(extra) > 255:
                raise ValueError(f"可変長が 255 B を超えた: {e.surface}")
            records += struct.pack(
                "<HhHBBB", classes[(e.lc, e.rc, e.pos6, e.posid)], e.wcost,
                chains[e.chain], flags, len(pb), len(extra))
            pool += pb + extra

        inv_c = [None] * len(classes)
        for k, v in classes.items():
            inv_c[v] = k
        inv_ch = [None] * len(chains)
        for k, v in chains.items():
            inv_ch[v] = k
        return cls(keys=keys, moras=moras, louds=louds, classes=inv_c,
                   chains=inv_ch, counts=counts, records=bytes(records),
                   pool=bytes(pool), surfaces=order, matrix=matrix)

    # --- オフセット ---------------------------------------------------------
    RECORD_SIZE = 9

    def _lens(self, i: int) -> tuple[int, int]:
        _c, _w, _ch, _f, pl, el = struct.unpack(
            "<HhHBBB", self.records[i * self.RECORD_SIZE:(i + 1) * self.RECORD_SIZE])
        return pl, el

    def pool_offsets_materialised(self) -> list[int]:
        off = [0]
        for i in range(len(self.records) // self.RECORD_SIZE):
            pl, el = self._lens(i)
            off.append(off[-1] + pl + el)
        return off[:-1]

    def checkpoints(self) -> list[int]:
        if getattr(self, "_ckpt", None) is None:
            mat = self.pool_offsets_materialised()
            self._ckpt = [mat[i] for i in range(0, len(mat), self.CHECKPOINT)]
        return self._ckpt

    def pool_offset_from_checkpoint(self, i: int) -> int:
        """端末がやる復元: 直近のチェックポイント + 途中のレコード長を足す。"""
        base = i // self.CHECKPOINT
        off = self.checkpoints()[base]
        for j in range(base * self.CHECKPOINT, i):
            pl, el = self._lens(j)
            off += pl + el
        return off

    def surface_checkpoints(self) -> bytes:
        """32 見出し語おきの累積エントリ数。C が entry_range を O(1) 近くで引くため。"""
        out = bytearray()
        tot = 0
        for i, c in enumerate(self.counts):
            if i % self.CHECKPOINT == 0:
                out += struct.pack("<I", tot)
            tot += c
        return bytes(out)

    def entry_start_from_checkpoint(self, rank: int) -> int:
        ck = getattr(self, "_surfck", None)
        if ck is None:
            ck = self._surfck = self.surface_checkpoints()
        base = rank // self.CHECKPOINT
        off = struct.unpack("<I", ck[4 * base:4 * base + 4])[0]
        for j in range(base * self.CHECKPOINT, rank):
            off += self.counts[j]
        return off

    def with_broken_surfck(self, idx: int) -> "DictBlob":
        import copy
        d = copy.copy(self)
        ck = bytearray(self.surface_checkpoints())
        v = struct.unpack("<I", ck[4 * idx:4 * idx + 4])[0]
        ck[4 * idx:4 * idx + 4] = struct.pack("<I", v + 1)
        d._surfck = bytes(ck)
        return d

    def with_broken_checkpoint(self, idx: int) -> "DictBlob":
        import copy
        d = copy.copy(self)
        ck = list(self.checkpoints())
        ck[idx] += 1
        d._ckpt = ck
        return d

    # --- 復元 ---------------------------------------------------------------
    def _entry_at(self, i: int, surface: str) -> Entry:
        cid, wcost, chid, flags, pl, el = struct.unpack(
            "<HhHBBB", self.records[i * self.RECORD_SIZE:(i + 1) * self.RECORD_SIZE])
        o = self.pool_offset_from_checkpoint(i)
        pb = self.pool[o:o + pl]
        ex = self.pool[o + pl:o + pl + el]
        pron = self.moras.decode(pb)
        j = 0
        if flags & self._F_ORIG_EQ_SURFACE:
            orig = surface
        elif ex[j] == 1:
            orig = self.surfaces[int.from_bytes(ex[j + 1:j + 4], "little")]
            j += 4
        else:
            n = ex[j + 1]
            orig = ex[j + 2:j + 2 + n].decode("utf-8")
            j += 2 + n
        if flags & self._F_READ_EQ_PRON:
            read = pron
        else:
            n = ex[j]
            read = self.moras.decode(ex[j + 1:j + 1 + n])
            j += 1 + n
        accs = []
        while j < len(ex):
            accs.append((ex[j], ex[j + 1]))
            j += 2
        lc, rc, pos6, posid = self.classes[cid]
        return Entry(surface, lc, rc, wcost, pos6, posid, orig, read, pron,
                     _decode_acc(accs), self.chains[chid])

    def lookup(self, surface: str) -> list[Entry]:
        res = self.louds.common_prefix_search(self.keys.encode(surface), 0)
        want = len(self.keys.encode(surface))
        for length, rank in res:
            if length == want:
                return [self._entry_at(i, surface)
                        for i in range(self._start[rank], self._start[rank + 1])]
        return []

    def all_entries(self) -> list[Entry]:
        out = []
        for rank, s in enumerate(self.surfaces):
            for i in range(self._start[rank], self._start[rank + 1]):
                out.append(self._entry_at(i, s))
        return out

    # --- 直列化 -------------------------------------------------------------
    #
    # C から読める平坦形式。ヘッダの直後にセクション表を置き、各セクションは
    # **16 バイト境界**に並べる（esp_partition_mmap が返すポインタの整列に合わせる。
    # esp32/partitions.csv の model パーティションと同じ理由）。
    #
    #   0  magic "K1D1" (4)
    #   4  version u16 / n_sections u16
    #   8  section[n] = { name[8], offset u32, length u32 }   … 16 B ずつ
    #      各セクション本体（16 B 境界）
    #
    VERSION = 1
    # ⚠️ 見出し語の文字列表は持たない。trie の鍵がそれ自身なので冗長
    #    （370,863 entries では 3,881,011 B = blob の 33%）。
    _SEC_NAMES = ("keytab", "keyesc", "moratab", "louds", "counts",
                  "classes", "chains", "records", "pool", "matrix", "surfck")

    @staticmethod
    def _pack_strtab(items) -> bytes:
        """NUL 区切りの文字列表。"""
        return b"".join(x.encode("utf-8") + b"\0" for x in items)

    @staticmethod
    def _unpack_strtab(blob: bytes) -> list[str]:
        if not blob:
            return []
        parts = blob.split(b"\0")
        if parts and parts[-1] == b"":
            parts.pop()
        return [p.decode("utf-8") for p in parts]

    def _section_payloads(self) -> dict:
        cls_blob = bytearray()
        pos6_strs: list[str] = []
        pos6_id: dict[tuple, int] = {}
        for lc, rc, pos6, posid in self.classes:
            if pos6 not in pos6_id:
                pos6_id[pos6] = len(pos6_strs)
                pos6_strs.append(",".join(pos6))
            cls_blob += struct.pack("<HHHH", lc, rc, pos6_id[pos6], posid)
        return {
            "keytab": self._pack_strtab(self.keys.table),
            "keyesc": self._pack_strtab(self.keys.esc_table),
            "moratab": self._pack_strtab(self.moras.table),
            "louds": self.louds.to_bytes(),
            "counts": bytes(self.counts),
            "classes": (struct.pack("<I", len(self.classes)) + bytes(cls_blob)
                        + self._pack_strtab(pos6_strs)),
            "chains": self._pack_strtab(self.chains),
            "records": self.records,
            "pool": self.pool,
            **({"matrix": self.matrix.to_section()} if self.matrix else {}),
            "surfck": self.surface_checkpoints(),
        }

    def to_bytes(self) -> bytes:
        pay = self._section_payloads()
        names = [n for n in self._SEC_NAMES if n in pay]
        head_len = 8 + 16 * len(names)
        out = bytearray(struct.pack("<4sHH", b"K1D1", self.VERSION, len(names)))
        table = bytearray()
        body = bytearray()
        off = (head_len + 15) // 16 * 16
        for n in names:
            pad = (-off) % 16
            body += b"\0" * pad
            off += pad
            table += struct.pack("<8sII", n.encode("ascii"), off, len(pay[n]))
            body += pay[n]
            off += len(pay[n])
        out += table
        out += b"\0" * ((-len(out)) % 16)
        out += body
        return bytes(out)

    @classmethod
    def sections(cls, raw: bytes) -> dict:
        magic, ver, n = struct.unpack("<4sHH", raw[:8])
        if magic != b"K1D1":
            raise ValueError(f"magic が違う: {magic!r}")
        if ver != cls.VERSION:
            raise ValueError(f"version が違う: {ver}")
        out = {}
        for i in range(n):
            name, off, ln = struct.unpack("<8sII", raw[8 + 16 * i: 24 + 16 * i])
            out[name.rstrip(b"\0").decode("ascii")] = (off, ln)
        return out

    @classmethod
    def record_region(cls, raw: bytes) -> tuple:
        """レコード領域の (開始, 終了)。陰性対照が壊す場所を知るために使う。"""
        o, l = cls.sections(raw)["records"]
        return o, o + l

    @classmethod
    def from_bytes(cls, raw: bytes) -> "DictBlob":
        secs = cls.sections(raw)

        def sec(n: str) -> bytes:
            o, l = secs[n]
            return raw[o:o + l]

        cb = sec("classes")
        n_cls = struct.unpack("<I", cb[:4])[0]
        pos6_strs = cls._unpack_strtab(cb[4 + 8 * n_cls:])
        pos6 = [tuple(x.split(",")) for x in pos6_strs]
        classes = []
        for i in range(n_cls):
            lc, rc, p6, posid = struct.unpack("<HHHH", cb[4 + 8 * i: 12 + 8 * i])
            classes.append((lc, rc, pos6[p6], posid))

        return cls(
            keys=KeyCodec(cls._unpack_strtab(sec("keytab")),
                          cls._unpack_strtab(sec("keyesc"))),
            moras=MoraCodec(cls._unpack_strtab(sec("moratab"))),
            louds=Louds.from_bytes(sec("louds")),
            classes=classes,
            chains=cls._unpack_strtab(sec("chains")),
            counts=list(sec("counts")),
            records=sec("records"),
            pool=sec("pool"),
            surfaces=None,            # trie から復元する
            matrix=(ConnMatrix.from_section(sec("matrix"))
                    if "matrix" in secs else None),
        )
