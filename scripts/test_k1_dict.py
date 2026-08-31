"""K-1 辞書エンコーダのテスト。

計画: docs/plan/k1-kanji-implementation-plan.md K-1
受け入れ条件 G1〜G5（すべて陰性対照つき）。

    uv run python scripts/test_k1_dict.py

⚠️ **TDD で書いている。** 実装 (`src/saanotts_jp/k1_dict.py`) より先にここを書き、
落ちることを確認してから実装する。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

FAILED: list[str] = []


def _dict_dir() -> str:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "k1"))
    import k0_freeze_dict
    return k0_freeze_dict.resolve_dict_dir()


def _char_bin() -> bytes:
    import os
    return pathlib.Path(os.path.join(_dict_dir(), "char.bin")).read_bytes()


def _unk_dic() -> bytes:
    import os
    return pathlib.Path(os.path.join(_dict_dir(), "unk.dic")).read_bytes()


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "NG "
    print(f"  {mark} {name}" + (f"    {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------- モーラ符号化

def test_mora_codec() -> None:
    """pron（カタカナ）を 1 モーラ 1 バイトに詰めて往復する。

    K-1 §2-2: 出現するモーラ記号は 288 種。上位 254 種を 1 バイト、
    残りをエスケープ。複合語は ':' で区切られる。
    """
    from saanotts_jp.k1_dict import MoraCodec

    print("\n=== モーラ符号化 ===")

    corpus = ["キョー", "テンキ", "ヒ’ト", "ムカシ:ムカシ", "イチバン:ウエ", "ア", "ンー"]
    codec = MoraCodec.from_prons(corpus)

    for s in corpus:
        check(f"往復: {s}", codec.decode(codec.encode(s)) == s,
              f"{codec.encode(s).hex()}")

    # 拗音は 2 文字で 1 モーラ
    check("拗音は 1 モーラ = 1 バイト", len(codec.encode("キョ")) == 1,
          f"len={len(codec.encode('キョ'))}")
    check("長音も 1 モーラ", len(codec.encode("キョー")) == 2)

    # 複合語の区切りが保たれる
    check("複合語の区切り", codec.decode(codec.encode("ムカシ:ムカシ")) == "ムカシ:ムカシ")

    # 表に無い記号はエスケープして往復する
    rare = "キョー" + "ヷ"          # ヷ は corpus に無い
    check("表に無い記号もエスケープで往復", codec.decode(codec.encode(rare)) == rare)

    # 表は 254 種を超えない
    big = ["".join(chr(0x30A1 + i) for i in range(80)) for _ in range(1)]
    c2 = MoraCodec.from_prons(big)
    check("1 バイト表は 254 種まで", len(c2.table) <= 254, f"{len(c2.table)}")


# ---------------------------------------------------------------- 文字 ID 鍵

def test_key_codec() -> None:
    """見出し語の鍵を UTF-8 バイトではなく文字 ID で持つ。

    K-1 §6-2: 上位 255 文字を 1 バイト、残りを 0xFF + 2 バイトにすると
    trie のノードが 2,075,882 → 1,178,163、サイズが −42.3% になる。
    """
    from saanotts_jp.k1_dict import KeyCodec

    print("\n=== 文字 ID 鍵 ===")

    surfaces = ["今日", "今日は", "電源", "電源を", "天気", "，", "齟齬"]
    codec = KeyCodec.from_surfaces(surfaces)

    for s in surfaces:
        check(f"往復: {s}", codec.decode(codec.encode(s)) == s,
              codec.encode(s).hex())

    check("頻出文字は 1 文字 1 バイト", len(codec.encode("今日")) == 2,
          f"len={len(codec.encode('今日'))}")

    # 表に無い文字はエスケープ（3 バイト）でも往復する
    check("表に無い文字も往復", codec.decode(codec.encode("蜃気楼")) == "蜃気楼")

    # **接頭符号であること** — trie の終端が文字の途中に落ちてはいけない
    bad = []
    for a in surfaces + ["蜃気楼", "彁"]:
        for b in surfaces + ["蜃気楼", "彁"]:
            ea, eb = codec.encode(a), codec.encode(b)
            if eb.startswith(ea) and not b.startswith(a):
                bad.append((a, b))
    check("接頭符号（符号の接頭 ⇒ 文字列の接頭）", not bad, str(bad[:3]))

    # 255 文字を超える語彙でエスケープが発生する
    many = ["".join(chr(0x4E00 + i) for i in range(3)) for i in range(0, 400)]
    c2 = KeyCodec.from_surfaces(many)
    check("1 バイト表は 255 文字まで", len(c2.table) <= 255, f"{len(c2.table)}")
    check("語彙が大きくても往復する",
          all(c2.decode(c2.encode(x)) == x for x in many))


# ---------------------------------------------------------------- LOUDS

def _brute(keys: set[bytes], seq: bytes, start: int) -> list[int]:
    """総当りの参照実装。seq[start:] の接頭辞のうち鍵になっている長さ。"""
    return [n for n in range(1, len(seq) - start + 1) if seq[start:start + n] in keys]


def test_louds_search() -> None:
    """G3: common-prefix-search が総当りと一致する。

    ⚠️ この形のゲートは調査で 2 回空虚になった（K-1 §3）。
       (a) ヒットが 17 件しか出ず「両方とも空」を一致と数えた
       (b) テスト文に現れないノードを壊しても落ちなかった
    なので **ヒット数と多バイトヒット数を必ず併記する**。
    """
    from saanotts_jp.k1_dict import Louds

    print("\n=== LOUDS: common-prefix-search（G3）===")

    keys = [b"a", b"ab", b"abc", b"abd", b"b", b"bc", b"xyz",
            b"\x01\x02", b"\x01\x02\x03", b"\x01"]
    kset = set(keys)
    trie = Louds.build(keys)

    seqs = [b"abcabd", b"abdxyz", b"bcbca", b"\x01\x02\x03\x01\x02", b"zzz", b""]
    positions = hits = multi = 0
    bad = []
    for seq in seqs:
        for i in range(len(seq)):
            got = [n for n, _rank in trie.common_prefix_search(seq, i)]
            exp = _brute(kset, seq, i)
            positions += 1
            hits += len(got)
            multi += sum(1 for n in got if n >= 2)
            if got != exp:
                bad.append((seq, i, got, exp))
    check("総当りと一致", not bad, f"不一致 {len(bad)} 件 {bad[:2]}")
    check("ゲートが空虚でない（ヒット > 0）", hits > 0, f"照合 {positions} 位置 / ヒット {hits}")
    check("多バイトのヒットが十分ある", multi >= 5, f"2 バイト以上のヒット {multi} 件")

    # 終端 rank は LOUDS 順で一意・連番
    ranks = sorted(r for seq in seqs for i in range(len(seq))
                   for _n, r in trie.common_prefix_search(seq, i))
    check("終端 rank が範囲内", all(0 <= r < len(kset) for r in ranks),
          f"max={max(ranks) if ranks else '-'} / 鍵 {len(kset)}")

    # rank から鍵を引き戻せる
    check("rank → 鍵 が往復する",
          all(trie.key_of(r) in kset for r in set(ranks)))


def test_louds_negative_control() -> None:
    """G4: 陰性対照 — **実際にヒットした経路上の**ラベルを壊すと G3 が落ちる。"""
    from saanotts_jp.k1_dict import Louds

    print("\n=== LOUDS: 陰性対照（G4）===")
    keys = [b"abc", b"abd", b"ab", b"b", b"xyz"]
    kset = set(keys)
    trie = Louds.build(keys)
    seq = b"abcabdxyz"

    def mismatches(t) -> int:
        n = 0
        for i in range(len(seq)):
            got = [x for x, _ in t.common_prefix_search(seq, i)]
            if got != _brute(kset, seq, i):
                n += 1
        return n

    check("壊す前は一致", mismatches(trie) == 0)

    # ヒットに実際に使われたノードを特定して壊す
    used = [node for i in range(len(seq)) for node in trie.visited_nodes(seq, i)]
    check("ヒット経路のノードを特定できた", len(used) > 0, f"{len(used)} ノード")
    broken = trie.with_broken_label(used[len(used) // 2])
    check("ヒット経路を壊すと落ちる", mismatches(broken) > 0,
          f"不一致 {mismatches(broken)} 件")


def test_louds_serialize() -> None:
    """バイト列に落として読み戻しても同じ答えを返す。"""
    from saanotts_jp.k1_dict import Louds

    print("\n=== LOUDS: 直列化 ===")
    keys = [b"a", b"ab", b"abc", b"b", b"bc", b"xyz"]
    trie = Louds.build(keys)
    blob = trie.to_bytes()
    back = Louds.from_bytes(blob)

    seq = b"abcbcxyz"
    same = all(trie.common_prefix_search(seq, i) == back.common_prefix_search(seq, i)
               for i in range(len(seq)))
    check("直列化して読み戻しても同じ", same, f"{len(blob)} B")
    check("バイト列が空でない", len(blob) > 0)


# ---------------------------------------------------------------- 辞書 blob

def _sample_entries():
    """小さいが形式のすべての枝を踏む entry 集合。"""
    from saanotts_jp.k1_dict import Entry
    P6 = ("名詞", "一般", "*", "*", "*", "*")
    P6b = ("動詞", "自立", "*", "*", "一段", "基本形")
    return [
        # surface, lc, rc, wcost, pos6, posid, orig, read, pron, acc, chain
        Entry("今日", 1345, 1345, 6455, P6, 38, "今日", "キョウ", "キョー", "1/2", "C3"),
        Entry("今日", 1300, 1300, 5000, P6, 38, "今日", "コンニチ", "コンニチ", "0/4", "C1"),
        Entry("は", 284, 284, 3143, P6, 16, "は", "ハ", "ワ", "0/1", "名詞%F1"),
        Entry("電源", 1345, 1345, 4000, P6, 38, "電源", "デンゲン", "デンゲン", "0/4", "C1"),
        # orig が見出し語と違う（活用形）
        Entry("食べる", 772, 772, 5000, P6b, 31, "食べる", "タベル", "タベル", "2/3", "*"),
        Entry("食べ", 773, 773, 5100, P6b, 31, "食べる", "タベ", "タベ", "2/2", "*"),
        # read != pron（は→ワ 型）、複合語
        Entry("昔々", 1375, 1375, 381, P6, 67, "昔:々", "ムカシ:ムカシ",
              "ムカシ:ムカシ", "0/3:0/3", "C2"),
        # acc が */*
        Entry("。", 5, 5, 1526, ("記号", "句点", "*", "*", "*", "*"), 4,
              "。", "。", "。", "*/*", "*"),
    ]


def test_blob_roundtrip() -> None:
    """G1: blob から復元した 11 フィールドが元と一致する。"""
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 辞書 blob: 往復（G1）===")
    entries = _sample_entries()
    blob = DictBlob.build(entries)
    back = DictBlob.from_bytes(blob.to_bytes())

    got = back.all_entries()
    check("エントリ数が一致", len(got) == len(entries), f"{len(got)} / {len(entries)}")

    exp_sorted = sorted(entries, key=lambda e: (e.surface,))
    got_sorted = sorted(got, key=lambda e: (e.surface,))
    diffs = [(a, b) for a, b in zip(exp_sorted, got_sorted) if a != b]
    check("11 フィールドすべて一致", not diffs, f"不一致 {len(diffs)} 件 {diffs[:1]}")

    # 見出し語から引ける
    e = back.lookup("今日")
    check("同綴り 2 件が順序どおり引ける",
          len(e) == 2 and e[0].wcost == 6455 and e[1].wcost == 5000,
          str([x.wcost for x in e]))
    check("引けない語は空", back.lookup("存在しない") == [])


def test_blob_negative_control() -> None:
    """G2: 陰性対照 — レコードを 1 バイト壊すと G1 が落ちる。"""
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 辞書 blob: 陰性対照（G2）===")
    entries = _sample_entries()
    raw = DictBlob.build(entries).to_bytes()

    ok = DictBlob.from_bytes(raw).all_entries()
    check("壊す前は一致", sorted(ok, key=lambda e: e.surface)
          == sorted(entries, key=lambda e: e.surface))

    off = DictBlob.record_region(raw)
    check("レコード領域が空でない", off[1] > off[0], f"{off}")
    broken = bytearray(raw)
    broken[off[0] + 1] ^= 0xFF
    try:
        got = DictBlob.from_bytes(bytes(broken)).all_entries()
        differs = sorted(got, key=lambda e: e.surface) != sorted(entries, key=lambda e: e.surface)
    except Exception:
        differs = True          # 復元できないのも「落ちた」
    check("1 バイト壊すと復元結果が変わる", differs)


def test_pool_offset_checkpoint() -> None:
    """G5: チェックポイントからのオフセット復元が materialise 版と一致する。

    K-1 §4-3: 間隔 256 だと 1 回の復元で最大 2,295 B 読む。**32 間隔**にする。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 値プールのオフセット（G5）===")
    entries = _sample_entries() * 20          # チェックポイントを跨がせる
    b = DictBlob.build(entries)

    check("チェックポイント間隔は 32", b.CHECKPOINT == 32, f"{b.CHECKPOINT}")
    mat = b.pool_offsets_materialised()
    ck = [b.pool_offset_from_checkpoint(i) for i in range(len(mat))]
    check("復元が materialise 版と一致", mat == ck,
          f"{sum(1 for a, c in zip(mat, ck) if a != c)} 件ずれ / {len(mat)} 件")

    broken = b.with_broken_checkpoint(1)
    ck2 = [broken.pool_offset_from_checkpoint(i) for i in range(len(mat))]
    check("陰性対照: チェックポイントをずらすと落ちる", ck2 != mat,
          f"{sum(1 for a, c in zip(mat, ck2) if a != c)} 件ずれ")


def test_pool_checkpoint_in_blob() -> None:
    """G5b: **プールのオフセット索引が blob に載っている**（K-6 の前提）。

    ⚠️ G5 は「Python が materialise した表と一致するか」しか見ていない。
    その表は **blob に入っていない**ので、端末は entry i のフィールドを引くのに
    レコードを 0 から全部舐めることになる（370,863 回）。
    K-6 で 1 トークンごとに引くので、**索引が blob に無いと成立しない**。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 値プールの索引が blob に載っているか（G5b）===")
    entries = _sample_entries() * 20
    b = DictBlob.build(entries)
    raw = b.to_bytes()
    secs = DictBlob.sections(raw)
    check("poolck セクションがある", "poolck" in secs, str(sorted(secs)))

    mat = b.pool_offsets_materialised()
    if "poolck" in secs:
        import struct as _s
        o, ln = secs["poolck"]
        got = list(_s.unpack(f"<{ln // 4}I", raw[o:o + ln]))
        # ⚠️ **セクションの中身を直に読む。** from_bytes 経由で比べると
        #    Python が materialise し直すので、**セクションが無くても通る**。
        want = mat[::32]
        check("セクションの値が 32 個ごとのオフセットと一致", got == want,
              f"{len(got)} 件 / 先頭 {got[:3]} vs {want[:3]}")
        check("索引の粒度が 32", len(got) == (len(mat) + 31) // 32,
              f"{len(got)} 件 / entries {len(mat)}")
    else:
        check("セクションの値が 32 個ごとのオフセットと一致", False, "セクションが無い")
        check("索引の粒度が 32", False, "セクションが無い")


def test_terminal_rank_index() -> None:
    """G5c: **終端ランクの索引が blob に載っている**（K-6 の前提）。

    ⚠️ これが無いと C 側の `term_rank()` が「ノード 0 から数える」O(n) 走査になる。
    common-prefix-search の**ヒットごとに**呼ばれるので、辞書が大きいほど効く。
    さらに K-6 では逆向き（rank → ノード）も要る — `orig` が見出し語 ID で
    格納されているため（K-1 §6-3: 99.79%）。**索引はその両方を賄う。**

    形式: 512 ノードごとに「それより前の終端の数」を u32。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 終端ランクの索引（G5c）===")
    entries = _sample_entries() * 20
    b = DictBlob.build(entries)
    raw = b.to_bytes()
    secs = DictBlob.sections(raw)
    check("termck セクションがある", "termck" in secs, str(sorted(secs)))

    n_nodes = len(b.louds.labels)
    want = []
    acc = 0
    for i in range(n_nodes):
        if i % 512 == 0:
            want.append(acc)
        if b.louds._tbit(i):
            acc += 1
    if "termck" in secs:
        import struct as _s
        o, ln = secs["termck"]
        got = list(_s.unpack(f"<{ln // 4}I", raw[o:o + ln]))
        check("値が 512 ノードごとの累積終端数と一致", got == want,
              f"{len(got)} 件 / 先頭 {got[:3]} vs {want[:3]}")
        check("粒度が 512", len(got) == (n_nodes + 511) // 512,
              f"{len(got)} 件 / ノード {n_nodes}")
    else:
        check("値が 512 ノードごとの累積終端数と一致", False, "セクションが無い")
        check("粒度が 512", False, "セクションが無い")


def test_blob_layout() -> None:
    """C から読める平坦な形式であること。

    K-2 は C でこの blob を読む。pickle のような Python 固有の容れ物は使えない。
    セクション表を持ち、各セクションは 16 バイト境界に置く
    （`esp32/partitions.csv` が model パーティションに課しているのと同じ理由）。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== blob の配置（C から読めるか）===")
    raw = DictBlob.build(_sample_entries()).to_bytes()

    check("magic が K1D1", raw[:4] == b"K1D1", raw[:4].hex())
    secs = DictBlob.sections(raw)
    check("セクション表が読める", len(secs) > 0, f"{len(secs)} 個: {sorted(secs)}")

    need = {"records", "pool", "louds", "counts"}
    check("必要なセクションがある", need <= set(secs), f"欠け: {need - set(secs)}")

    misaligned = {n: o for n, (o, _l) in secs.items() if o % 16}
    check("全セクションが 16 バイト境界", not misaligned, str(misaligned))

    inside = all(0 <= o and o + l <= len(raw) for o, l in secs.values())
    check("全セクションが blob 内に収まる", inside)

    check("pickle を使っていない", b"pickle" not in raw and raw[8:10] != b"\x80\x05",
          "先頭 10 B: " + raw[:10].hex())

    # レコード領域はセクション表から引ける（陰性対照が壊す場所）
    ro, rl = secs["records"]
    check("records の長さがレコード数と整合",
          rl % DictBlob.RECORD_SIZE == 0 and rl // DictBlob.RECORD_SIZE == 8,
          f"{rl} B / {DictBlob.RECORD_SIZE} = {rl // DictBlob.RECORD_SIZE}")


def test_no_redundant_surface_table() -> None:
    """見出し語の文字列を二重に持たない。

    LOUDS の鍵がすでに見出し語そのものなので、別の文字列表を持つのは冗長。
    370,863 entries の実測ではこの表だけで 3,881,011 B（blob の 33%）あった。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 見出し語表の冗長性 ===")
    entries = _sample_entries()
    raw = DictBlob.build(entries).to_bytes()
    secs = DictBlob.sections(raw)

    check("surfid セクションを持たない", "surfid" not in secs, str(sorted(secs)))

    back = DictBlob.from_bytes(raw)
    check("読み戻した trie から鍵を復元できる",
          len(back.louds.terminal_keys()) == len(set(e.surface for e in entries)),
          f"{len(back.louds.terminal_keys())}")
    check("見出し語が一致",
          sorted(back.surfaces) == sorted(set(e.surface for e in entries)))
    check("往復は保たれる",
          sorted(back.all_entries(), key=lambda e: (e.surface, e.wcost))
          == sorted(entries, key=lambda e: (e.surface, e.wcost)))


def test_connection_matrix() -> None:
    """K-2 の Viterbi に要る接続行列を blob が運ぶ。

    ⚠️ **索引は `flat[rc_prev + lsize * lc_cur]`。** MeCab 本家のソースの式
    (`matrix_[lcAttr + lsize_*rcAttr]`) ではこの辞書に合わない
    （K-1 §9-3: 一致 0.91% 対 100.00%）。**推測で書くと必ず外す。**
    """
    from saanotts_jp.k1_dict import ConnMatrix, DictBlob

    print("\n=== 接続行列 ===")

    # 3x3 の小さい行列。値は位置が分かるように仕込む
    lsize = rsize = 3
    flat = [0] * (lsize * rsize)
    for rc in range(rsize):
        for lc in range(lsize):
            flat[rc + lsize * lc] = 100 * rc + lc
    raw = (lsize.to_bytes(2, "little") + rsize.to_bytes(2, "little")
           + b"".join(int(v).to_bytes(2, "little", signed=True) for v in flat))
    m = ConnMatrix.from_matrix_bin(raw)

    check("lsize / rsize", (m.lsize, m.rsize) == (3, 3), f"{m.lsize}x{m.rsize}")
    ok = all(m.trans(rc, lc) == 100 * rc + lc
             for rc in range(rsize) for lc in range(lsize))
    check("trans(rc_prev, lc_cur) が自己整合", ok,
          f"trans(1,2)={m.trans(1,2)}（102 が正）")
    # ⚠️ **上は循環している** — 自分で決めた配置を読み戻しているだけ。
    #    実際の matrix.bin と合っているかは MeCab の申告値でしか確かめられない。
    #    それは scripts/k1/k2_gate.py の G-MATRIX で実データを使って検証する。
    check("転置と区別できる", m.trans(1, 2) != m.trans(2, 1),
          f"{m.trans(1,2)} vs {m.trans(2,1)}")

    # blob に載って往復する
    entries = _sample_entries()
    blob = DictBlob.build(entries, matrix=m)
    back = DictBlob.from_bytes(blob.to_bytes())
    check("blob に matrix セクションがある",
          "matrix" in DictBlob.sections(blob.to_bytes()))
    check("往復しても同じ値", back.matrix is not None
          and all(back.matrix.trans(rc, lc) == m.trans(rc, lc)
                  for rc in range(rsize) for lc in range(lsize)))
    check("行列なしでも組める（K-1 と後方互換）",
          DictBlob.from_bytes(DictBlob.build(entries).to_bytes()).matrix is None)


def test_louds_rank_index() -> None:
    """LOUDS が rank/select 索引を blob に持つ。

    端末は flash から mmap して読む。索引を起動時に作ると RAM を食うので、
    **blob に入れておく**（K-1 §2-2 のサイズ計上にも含まれている）。

    ⚠️ superblock は **256 bit**。512 bit にすると先行 1 の数が最大 448 になり
    **u8 に入らない**（compress-lane が発見。K-1 §6-1）。
    """
    from saanotts_jp.k1_dict import Louds

    print("\n=== LOUDS の rank/select 索引 ===")
    keys = [bytes([i % 251, (i * 7) % 251, (i * 13) % 251]) for i in range(500)]
    trie = Louds.build(keys)
    blob = trie.to_bytes()
    back = Louds.from_bytes(blob)

    check("索引つきでも直列化して読み戻せる",
          all(back.common_prefix_search(k, 0) == trie.common_prefix_search(k, 0)
              for k in keys[:50]))

    # 索引が実際に blob に入っている
    check("索引ぶん大きくなっている",
          back.has_rank_index, f"{len(blob):,d} B")

    # 索引だけで rank1 / select0 が正しい（線形走査と一致）
    bad_r = [i for i in range(0, back.bitlen, 97)
             if back.rank1_indexed(i) != sum(back._bit(j) for j in range(i))]
    check("rank1 が線形走査と一致", not bad_r, f"不一致 {len(bad_r)} 箇所")

    zeros = [i for i in range(back.bitlen) if not back._bit(i)]
    bad_s = [k for k in range(0, len(zeros), 31)
             if back.select0_indexed(k) != zeros[k]]
    check("select0 が線形走査と一致", not bad_s, f"不一致 {len(bad_s)} 箇所")

    # superblock 内の先行 1 の数が u8 に収まる
    check("block 内カウントが u8 に収まる", back.max_block_count <= 255,
          f"最大 {back.max_block_count}")


def test_surface_count_checkpoint() -> None:
    """見出し語 rank → エントリ開始位置を O(1) 近くで引ける。

    C 側は counts を毎回先頭から足せない（見出し語 30 万件）。
    値プールと同じく **32 見出し語おきのチェックポイント**を blob に持つ。
    """
    from saanotts_jp.k1_dict import DictBlob

    print("\n=== 見出し語ごとのエントリ開始位置 ===")
    entries = _sample_entries() * 30
    b = DictBlob.build(entries)
    raw = b.to_bytes()
    check("surfck セクションがある", "surfck" in DictBlob.sections(raw),
          str(sorted(DictBlob.sections(raw))))

    back = DictBlob.from_bytes(raw)
    mat = back._start
    ck = [back.entry_start_from_checkpoint(r) for r in range(len(back.surfaces))]
    check("チェックポイント復元が一致", mat[:len(ck)] == ck,
          f"{sum(1 for a, c in zip(mat, ck) if a != c)} 件ずれ / {len(ck)} 件")

    bad = back.with_broken_surfck(0)
    ck2 = [bad.entry_start_from_checkpoint(r) for r in range(len(back.surfaces))]
    check("陰性対照: ずらすと落ちる", ck2 != ck,
          f"{sum(1 for a, c in zip(ck, ck2) if a != c)} 件ずれ")


# ---------------------------------------------------------------- 未知語（K-3）

def test_char_property() -> None:
    """char.bin の文字カテゴリを読む。

    形式は実データで確認した: u32 カテゴリ数 / 32 B ずつのカテゴリ名 /
    **65,535 件**の CharInfo（u32 のビットフィールド）。
    ⚠️ 65,536 ではない。**U+FFFF 以上は表に無い** — サロゲートペア漢字
    （𡈽 / 𩸽）が未知語になるのはこれが理由（C-044）。

    CharInfo は type:18 / default_type:8 / length:4 / group:1 / invoke:1。
    """
    from saanotts_jp.k1_dict import CharProperty

    print("\n=== 文字カテゴリ（char.bin）===")
    cp = CharProperty.from_char_bin(_char_bin())

    check("カテゴリ数", len(cp.names) == 11, str(cp.names))
    check("先頭は DEFAULT", cp.names[0] == "DEFAULT")
    check("表は 65,535 件", cp.n_codepoints == 65535, f"{cp.n_codepoints}")

    # ⚠️ 自分の符号化を読み戻すのではなく、**日本語として正しい分類**を確かめる
    for ch, want in [("あ", "HIRAGANA"), ("ア", "KATAKANA"), ("ｱ", "KATAKANA"),
                     ("漢", "KANJI"), ("1", "NUMERIC"), ("Ａ", "ALPHA"),
                     ("、", "SYMBOL"), (" ", "SPACE")]:
        got = cp.names[cp.default_type(ord(ch))]
        check(f"{ch} は {want}", got == want, got)

    check("KANJI は invoke しない", cp.invoke(ord("漢")) == 0)
    check("SYMBOL は invoke する", cp.invoke(ord("、")) == 1)
    check("ひらがなは group する", cp.group(ord("あ")) == 1)

    # 表の外
    check("U+FFFF 以上は DEFAULT に落ちる",
          cp.names[cp.default_type(0x21F3D)] == "DEFAULT",
          cp.names[cp.default_type(0x21F3D)])


def test_unk_dict() -> None:
    """unk.dic を読む。

    ⚠️ **未知語の feature は 7 列しかなく `read` / `pron` / `acc` / `chain` を持たない。**
    これが「未知語は誤読ではなく無音で消える」の正体（C-044）。
    """
    from saanotts_jp.k1_dict import UnkDict

    print("\n=== 未知語辞書（unk.dic）===")
    u = UnkDict.from_unk_dic(_unk_dic())

    check("カテゴリ名で引ける", len(u.by_category("KANJI")) > 0,
          f"KANJI に {len(u.by_category('KANJI'))} 件")
    check("DEFAULT がある", len(u.by_category("DEFAULT")) > 0)
    check("全件で 40 件前後", 30 <= u.n_entries <= 60, f"{u.n_entries} 件")

    e = u.by_category("KANJI")[0]
    check("lc / rc / wcost を持つ", e.lc > 0 and e.rc > 0, f"lc={e.lc} rc={e.rc} wcost={e.wcost}")
    check("feature が 7 列（読みを持たない）", e.n_feature_fields == 7,
          f"{e.n_feature_fields} 列: {e.feature}")


def test_blob_carries_unknown_tables() -> None:
    """blob が未知語処理に要る 2 表を運ぶ（K-3）。"""
    from saanotts_jp.k1_dict import CharProperty, DictBlob, UnkDict

    print("\n=== blob が char / unk を運ぶ ===")
    cp = CharProperty.from_char_bin(_char_bin())
    uk = UnkDict.from_unk_dic(_unk_dic())
    raw = DictBlob.build(_sample_entries(), char_prop=cp, unk=uk).to_bytes()
    secs = DictBlob.sections(raw)
    check("char セクションがある", "char" in secs, str(sorted(secs)))
    check("unk セクションがある", "unk" in secs)

    back = DictBlob.from_bytes(raw)
    check("char が往復する",
          back.char_prop is not None
          and back.char_prop.names == cp.names
          and back.char_prop.default_type(ord("漢")) == cp.default_type(ord("漢")))
    check("unk が往復する",
          back.unk is not None and back.unk.n_entries == uk.n_entries
          and [e.wcost for e in back.unk.by_category("KANJI")]
              == [e.wcost for e in uk.by_category("KANJI")])
    check("無しでも組める（K-1/K-2 と後方互換）",
          DictBlob.from_bytes(DictBlob.build(_sample_entries()).to_bytes()).unk is None)


# ---------------------------------------------------------------- 実行

def main() -> int:
    tests = [test_mora_codec, test_key_codec, test_louds_search,
             test_louds_negative_control, test_louds_serialize,
             test_blob_roundtrip, test_blob_negative_control,
             test_pool_offset_checkpoint, test_pool_checkpoint_in_blob,
             test_terminal_rank_index,
             test_blob_layout,
             test_no_redundant_surface_table, test_connection_matrix,
             test_louds_rank_index, test_surface_count_checkpoint,
             test_char_property, test_unk_dict,
             test_blob_carries_unknown_tables]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            print(f"  NG  {t.__name__} が例外: {type(e).__name__}: {e}")
            FAILED.append(t.__name__)
    print()
    if FAILED:
        print(f"NG! {len(FAILED)} 件: {', '.join(FAILED)}")
        return 1
    print("OK  すべて通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
