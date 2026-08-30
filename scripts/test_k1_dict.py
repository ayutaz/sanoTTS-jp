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


# ---------------------------------------------------------------- 実行

def main() -> int:
    tests = [test_mora_codec, test_key_codec, test_louds_search,
             test_louds_negative_control, test_louds_serialize]
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
