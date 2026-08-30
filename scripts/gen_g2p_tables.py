#!/usr/bin/env python3
"""`csrc/g2p_table.h` を `kana_g2p.build_mora_table()` から**生成**する。

    uv run python scripts/gen_g2p_tables.py

⚠️ **テーブルを手書きしない。** C++ 側の PUA マップが Python とずれて
54 音素の ID を黙って取り違えた前例がある（C-002）。C99 の端末側 G2P が使う
表は全部ここから出す。

生成されるもの（すべて `csrc/g2p.c` だけが include する）:

* `kSaanG2pMora[195]` — かな 1〜2 文字 → 生徒インデックス 1〜2 個。
  キーは `(コードポイント − 0x3040)` の uint8 2 個で、`(c1, c2)` 昇順に並べてある
  （C 側は二分探索する）。`c2 == 0` が 1 文字キー
* `kSaanG2pMarks[10]` — `[` `]` `#` `_` `^` `$` `?` `?!` `?.` `?~` → 生徒インデックス。
  **長いものから並べる**（`?!` を `?` より先に照合する）
* `kSaanG2pNAllophone[57]` — 「後続モーラの第 1 音素」→ `ん` の異音。
  該当なしは `N_uvular`
* `SAAN_G2P_TABLE_SHA256_INIT` — `gen_g2p_vectors.table_sha256()` と**同じ関数**で
  計算したハッシュ。ベクタ側と突き合わせて「古い .h と新しいベクタ」を弾く

生成器自身のゲート（`writing-gates`）:

* 吐いた行から **Python の dict を復元して元の table と比較**する。
  ハッシュは Python 側の table を見ているだけなので、**行の吐き出しにバグがあっても
  ハッシュは一致してしまう**（例: 1 件落とす、p1 を落とす）。復元比較が唯一の検出手段
* `a..o` が 10..14 に**連続**していること、`A..O` が **+5** であることを assert
  （C 側は無声化を `id + 5` の算術でやるので、崩れると黙って別の音素になる）
* mora テーブル 195 / `ん` 異音 21 / 語彙閉包 57 を assert（`gen_g2p_vectors.sanity_checks`）
"""

from __future__ import annotations

import os
import json
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import kana_g2p as K                                     # noqa: E402
import gen_g2p_vectors as G                              # noqa: E402
from saanotts_jp.vocab import TOKENS                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "csrc" / "g2p_table.h"
# ⚠️ **同じ生成器から出す 2 つ目の表現。** C 側と隣に置いて、片方だけ古くなったら
#    目で見て分かるようにする。SHA-256 は同じ関数で計算するので、
#    ずれたら `make -C csrc g2p` の G1 と loader の検証の両方が落ちる。
OUT_JSON = ROOT / "csrc" / "g2p_table.json"

KANA_BASE = 0x3040          # キーはこの値からの差分 1 バイトで持つ
IDX = {tok: i for i, tok in enumerate(TOKENS)}


def check_vocab_layout() -> None:
    """C 側の算術が依存している語彙の並びを assert する。"""
    for i, v in enumerate("aiueo"):
        assert IDX[v] == 10 + i, f"{v} が {10 + i} でない（{IDX[v]}）。vocab.py の並びが変わった"
    for v, d in (("a", "A"), ("i", "I"), ("u", "U"), ("e", "E"), ("o", "O")):
        assert IDX[d] - IDX[v] == 5, f"{v}->{d} が +5 でない。C は id+5 で無声化する"
    assert IDX["_"] == 0 and IDX["^"] == 1 and IDX["$"] == 2
    assert IDX["N_uvular"] == 23
    assert len(TOKENS) < 128, "生徒インデックスが int8 に収まらない"


def mora_rows(table: dict[str, list[str]]) -> list[tuple[int, int, int, int, str]]:
    """(c1, c2, p0, p1, コメント用のキー) を `(c1, c2)` 昇順で返す。"""
    rows = []
    for kana, phonemes in table.items():
        cps = [ord(c) for c in kana]
        assert 1 <= len(cps) <= 2, f"キーが 1〜2 文字でない: {kana!r}"
        assert all(0x3041 <= cp <= 0x30FC for cp in cps), \
            f"キーのコードポイントが範囲外: {kana!r}"
        assert 1 <= len(phonemes) <= 2, f"1 モーラ 1〜2 音素でない: {kana!r} {phonemes}"
        for p in phonemes:
            assert p in IDX, f"語彙に無い音素: {p!r}"
        c1 = cps[0] - KANA_BASE
        c2 = (cps[1] - KANA_BASE) if len(cps) == 2 else 0
        assert 1 <= c1 <= 255 and 0 <= c2 <= 255
        p0 = IDX[phonemes[0]]
        p1 = IDX[phonemes[1]] if len(phonemes) == 2 else -1
        rows.append((c1, c2, p0, p1, kana))
    rows.sort(key=lambda r: (r[0], r[1]))
    keys = [(r[0], r[1]) for r in rows]
    assert len(set(keys)) == len(keys), "キーが重複している"
    # ⚠️ **C は二分探索する。** 並びが (c1, c2) 昇順でないと、例外も出さずに
    #    一部のモーラだけ「表に無い」= 未知文字エラーになる（復元比較は通ってしまう）
    assert keys == sorted(keys), f"(c1, c2) 昇順でない: {keys[:5]}"
    return rows


def verify_roundtrip(rows, table: dict[str, list[str]]) -> None:
    """⚠️ **吐いた行から dict を復元して元と比較する。**

    ハッシュは Python 側の table しか見ていないので、行の吐き出しでエントリを
    落としても p1 を落としてもハッシュは一致してしまう。ここが唯一の検出手段。
    """
    back: dict[str, list[str]] = {}
    for c1, c2, p0, p1, _kana in rows:
        kana = chr(c1 + KANA_BASE) + (chr(c2 + KANA_BASE) if c2 else "")
        ph = [TOKENS[p0]] + ([TOKENS[p1]] if p1 >= 0 else [])
        assert kana not in back, f"復元でキーが衝突した: {kana!r}"
        back[kana] = ph
    assert back == table, (
        "復元した表が元と違う: 余り "
        f"{ {k: v for k, v in back.items() if table.get(k) != v} } / 不足 "
        f"{ {k: v for k, v in table.items() if back.get(k) != v} }")


def mark_rows() -> list[tuple[str, int]]:
    """記号 → 生徒インデックス。**長いものから**（`?!` を `?` より先に照合する）。"""
    marks = sorted(K.MARKS, key=lambda m: (-len(m), m))
    for m in marks:
        assert m in IDX, f"記号 {m!r} が語彙に無い"
        assert m.encode("ascii"), "記号が ASCII でない"
        assert 1 <= len(m) <= 2, f"記号が 1〜2 バイトでない: {m!r}"
    assert set(marks) == set(TOKENS[:10]), \
        f"記号集合が語彙の先頭 10 と違う: {sorted(marks)} vs {sorted(TOKENS[:10])}"
    # ⚠️ **並び順そのものが仕様。** C は上から線形に照合するので、
    #    2 バイトの `?!` `?.` `?~` が 1 バイトの `?` より後ろに来たら黙って壊れる
    lens = [len(m) for m in marks]
    assert lens == sorted(lens, reverse=True), f"記号が長さ降順でない: {marks}"
    rows = [(m, IDX[m]) for m in marks]
    assert {m for m, _ in rows} == set(K.MARKS) and len(rows) == len(K.MARKS), \
        "復元した記号集合が MARKS と違う"
    return rows


def allophone_row() -> list[int]:
    """後続モーラの第 1 音素（生徒インデックス）→ `ん` の異音。既定 `N_uvular`。

    ⚠️ **件数だけを assert しても空虚。** 21 件すべてを `N_m` に潰しても件数は 21 のままで、
    「`ん` が常に `N_m`」という C-002 型の壊れ方がそのまま通る（実際に素通りした）。
    復元して `N_ALLOPHONE` と**値まで**突き合わせる。
    """
    out = [IDX["N_uvular"]] * len(TOKENS)
    for cons, n_variant in K.N_ALLOPHONE.items():
        assert cons in IDX, f"異音規則のキー {cons!r} が語彙に無い"
        assert n_variant in IDX, f"異音 {n_variant!r} が語彙に無い"
        out[IDX[cons]] = IDX[n_variant]
    back = {TOKENS[i]: TOKENS[v] for i, v in enumerate(out) if v != IDX["N_uvular"]}
    assert back == K.N_ALLOPHONE, (
        "復元した異音規則が元と違う: 余り "
        f"{ {k: v for k, v in back.items() if K.N_ALLOPHONE.get(k) != v} } / 不足 "
        f"{ {k: v for k, v in K.N_ALLOPHONE.items() if back.get(k) != v} }")
    assert IDX["N_uvular"] not in set(K.N_ALLOPHONE.values()) | {-1}, \
        "N_uvular を明示的に持つ規則があると復元比較が効かなくなる"
    return out


def main() -> int:
    table = K.build_mora_table()
    G.sanity_checks(table)            # 195 / 21 / 57 / キー範囲 / +5
    check_vocab_layout()

    rows = mora_rows(table)
    verify_roundtrip(rows, table)
    marks = mark_rows()
    allo = allophone_row()
    sha = G.table_sha256(table)       # ⚠️ ベクタ生成器と**同じ関数**

    idx_of = {r[4]: i for i, r in enumerate(rows)}
    assert "ん" in idx_of and "ー" in idx_of, "ん / ー がテーブルに無い"

    dv = K.DEVOICED_MARK.encode("utf-8")
    assert len(dv) == 2, f"無声化マークが 2 バイトでない: {dv!r}"

    L: list[str] = []
    add = L.append
    add("/* 自動生成 — 手で編集しない。")
    add(" *")
    add(" *   uv run python scripts/gen_g2p_tables.py")
    add(" *")
    add(" * 出典: scripts/kana_g2p.py の build_mora_table() / N_ALLOPHONE / MARKS と")
    add(" *       src/saanotts_jp/vocab.py の TOKENS。**手で書き写さない**（C-002）。")
    add(" *")
    add(" * ⚠️ piper-plus の phonemizer が変わるとこの表も変わる。再生成しないと")
    add(" *    `make -C csrc g2p` の G1（SHA-256 一致）が落ちる。")
    add(" */")
    add("#ifndef SAAN_G2P_TABLE_H")
    add("#define SAAN_G2P_TABLE_H")
    add("")
    add("#include <stdint.h>")
    add("")
    add(f"#define SAAN_G2P_TABLE_ENTRIES {len(rows)}")
    add(f"#define SAAN_G2P_N_MARKS       {len(marks)}")
    add(f"#define SAAN_G2P_VOCAB         {len(TOKENS)}")
    add("")
    add("/* キーは (コードポイント - SAAN_G2P_KANA_BASE) の uint8。0 は「2 文字目なし」 */")
    add(f"#define SAAN_G2P_KANA_BASE     0x{KANA_BASE:04x}")
    add(f"#define SAAN_G2P_KANA_LO       0x{0x3041:04x}")
    add(f"#define SAAN_G2P_KANA_HI       0x{0x30FC:04x}")
    add("")
    add("/* 特別扱いするモーラの行番号（`ん` は異音規則、`ー` は直前母音の複製）。")
    add(" * ⚠️ 表の値（`ん`→N_m / `ー`→a）は**キャリア法の副産物**で、素直に引くと")
    add(" *    異音規則が丸ごと死ぬ。ただし**後続モーラの第 1 音素としては生の値を使う**。 */")
    add(f"#define SAAN_G2P_IDX_N         {idx_of['ん']}")
    add(f"#define SAAN_G2P_IDX_LONG      {idx_of['ー']}")
    add("")
    add(f"#define SAAN_G2P_ID_PAD        {IDX['_']}")
    add(f"#define SAAN_G2P_ID_BOS        {IDX['^']}")
    add(f"#define SAAN_G2P_ID_EOS        {IDX['$']}")
    add(f"#define SAAN_G2P_ID_N_UVULAR   {IDX['N_uvular']}")
    add("/* 平母音 a i u e o が連続していること / 無声化が +5 であることは")
    add(" * scripts/gen_g2p_tables.py の check_vocab_layout() が assert する */")
    add(f"#define SAAN_G2P_VOWEL_LO      {IDX['a']}")
    add(f"#define SAAN_G2P_VOWEL_HI      {IDX['o']}")
    add(f"#define SAAN_G2P_DEVOICE_STEP  {IDX['A'] - IDX['a']}")
    add("")
    add(f"/* 無声化マーク {K.DEVOICED_MARK!r} = U+{ord(K.DEVOICED_MARK):04X} の UTF-8 */")
    add(f"#define SAAN_G2P_DEVOICE_B0    0x{dv[0]:02x}")
    add(f"#define SAAN_G2P_DEVOICE_B1    0x{dv[1]:02x}")
    add("")
    add("/* p1 < 0 は「音素は 1 個」 */")
    add("typedef struct { uint8_t c1, c2; int8_t p0, p1; } saan_g2p_mora;")
    add("")
    add("/* (c1, c2) 昇順。C 側は二分探索する（2 文字キー → 1 文字キーの順に引く） */")
    add(f"static const saan_g2p_mora kSaanG2pMora[SAAN_G2P_TABLE_ENTRIES] = {{")
    for c1, c2, p0, p1, kana in rows:
        ph = " ".join(table[kana])
        add(f"    {{ 0x{c1:02x}, 0x{c2:02x}, {p0:3d}, {p1:3d} }},   /* {kana} -> {ph} */")
    add("};")
    add("")
    add("typedef struct { uint8_t len, b0, b1; int8_t id; } saan_g2p_mark;")
    add("")
    add("/* ⚠️ **長いものから並べてある。** 上から順に照合すること")
    add(" *    （`?!` を `?` より先に見ないと `?` + 未知文字 `!` になる） */")
    add("static const saan_g2p_mark kSaanG2pMarks[SAAN_G2P_N_MARKS] = {")
    for m, i in marks:
        b = m.encode("ascii")
        b0 = b[0]
        b1 = b[1] if len(b) == 2 else 0
        add(f"    {{ {len(b)}, 0x{b0:02x}, 0x{b1:02x}, {i:3d} }},   /* {m!r} */")
    add("};")
    add("")
    add("/* 後続モーラの**生の**第 1 音素 → `ん` の異音。該当なしは N_uvular。")
    add(" * ⚠️ 後続の `ん`/`ー`/`っ` を再帰的に解決しない（生の N_m / a / cl を引く）。 */")
    add("static const uint8_t kSaanG2pNAllophone[SAAN_G2P_VOCAB] = {")
    for i in range(0, len(allo), 8):
        chunk = ", ".join(f"{v:2d}" for v in allo[i : i + 8])
        add(f"    {chunk},")
    add("};")
    add("")
    add("/* sha256(mora 195 + ん 異音 21 + 語彙 57 の正準シリアライズ)。")
    add(" * scripts/gen_g2p_vectors.py の table_sha256() と**同じ関数**で計算している。 */")
    add(f"/* {sha} */")
    add("#define SAAN_G2P_TABLE_SHA256_INIT { \\")
    hb = bytes.fromhex(sha)
    for i in range(0, 32, 8):
        line = ", ".join(f"0x{b:02x}" for b in hb[i : i + 8])
        add(f"    {line}{',' if i + 8 < 32 else ''} \\")
    add("}")
    add("")
    add("#endif /* SAAN_G2P_TABLE_H */")

    text = "\n".join(L) + "\n"
    OUT.write_text(text, encoding="utf-8")

    # --- Python 側の凍結テーブル（**OpenJTalk が無くても読める**）-------------
    # ⚠️ `build_mora_table()` は piper-plus の phonemizer を呼ぶので、
    #    piper-plus を持っていない人は 1 文字も変換できない。リリースの重みだけで
    #    合成できるようにするために、同じ表を JSON でも凍結する（C-041）。
    # ⚠️ **`N_ALLOPHONE` は build_mora_table() が 3 件足したあとの値**を書く
    #    （kw / gw / v）。素の 18 件を書くと異音規則が静かに欠ける。
    OUT_JSON.write_text(json.dumps({
        "_comment": "自動生成 — 手で編集しない（uv run python scripts/gen_g2p_tables.py）。"
                    "kana_g2p.load_frozen_mora_table() が sha256 を検証して読む。",
        "sha256": sha,
        "mora": {k: table[k] for k in sorted(table)},
        "n_allophone": {k: K.N_ALLOPHONE[k] for k in sorted(K.N_ALLOPHONE)},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 書いたものが読み返せて、生の表と**完全に一致する**ことをその場で確かめる。
    # ⚠️ これが無いと「書けた」だけで「読める」を確認していないことになる。
    frozen = K.load_frozen_mora_table()
    assert frozen == table, "凍結テーブルが build_mora_table() と一致しない"


    n_mora_bytes = len(rows) * 4
    n_mark_bytes = len(marks) * 4
    print(f"mora {len(rows)} 行 ({n_mora_bytes} B) / 記号 {len(marks)} 件 "
          f"({n_mark_bytes} B) / 異音 {len(TOKENS)} B")
    print(f"sha256(table) = {sha}")
    print(f"{OUT.relative_to(ROOT)}: {len(text.encode('utf-8')):,} B "
          f"(テーブル実体 {n_mora_bytes + n_mark_bytes + len(TOKENS)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
