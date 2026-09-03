#!/usr/bin/env python3
"""`--text`（漢字）と `--intermediate`（かな）が**同じ生徒 index** を作るかを検査する。

    uv run python scripts/test_text_route_parity.py            # held-out 300 文
    uv run python scripts/test_text_route_parity.py -n 50       # 速く回す

## なぜ要るのか

`synthesize_student.py` の漢字経路は、かつて**教師の `phoneme_id_map` を経由**していた:

    漢字 → 中間表現 → 教師 ID（pim）→ map_ids → 生徒 index

教師は private リポジトリにあるので、**リリースの重みだけを持っている人は
`--text` を一切使えなかった**。だが教師が要るのは 2 段目だけで、`gen_g2p_vectors.encode()
（= 端末 `csrc/g2p.c` の期待値を作っているのと同じ関数）が同じ生徒 index を
教師抜きで出す。**held-out 300 文で 300/300 一致したので教師依存を外した**（M-92）。

**この検査はその等価性が壊れていないことを見る。** 壊れると、漢字で書いた文と
かなで書いた同じ文が**違う音になる**（しかもどちらもそれらしく鳴るので気づけない）。

⚠️ **OpenJTalk（フルセットアップ）と教師 ckpt が要るので CI では回せない。**
教師が要るのは「外した側」を再現して突き合わせるためで、本番の経路には要らない。
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

HELDOUT = ROOT / "data" / "splits" / "corpus_heldout.tsv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=300, help="見る文の数")
    a = ap.parse_args()

    import kana_g2p as K
    import gen_teacher_labels as G
    from gen_g2p_vectors import encode as encode_student
    from saanotts_jp.vocab import map_ids

    table, which = K.mora_table(prefer_frozen=False)
    print(f"mora テーブル: {which}（{len(table)} 件）")
    G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]

    rows: list[str] = []
    for r in csv.reader(open(HELDOUT), delimiter="\t"):
        if not r or not r[-1] or r[0] == "source":
            continue
        rows.append(r[-1])
        if len(rows) >= a.n:
            break
    if not rows:
        print(f"NG! {HELDOUT} から 1 文も読めなかった = **この検査は空虚**")
        return 1

    def both(text: str) -> tuple[list[int], list[int], str]:
        inter = K.text_to_intermediate(text, table)
        old = list(map_ids(G.encode_intermediate(inter, pim)))       # 教師経由（旧）
        s = "".join(inter)
        info = encode_student(s, table)                              # 教師なし（現行）
        if info["kind"] != 0:
            raise KeyError(f"{info['err_byte']} バイト目が読めない")
        return old, list(info["ids"]), s

    same = diff = err = 0
    bad: list[str] = []
    for text in rows:
        try:
            old, new, _ = both(text)
        except Exception as e:                                       # noqa: BLE001
            err += 1
            if len(bad) < 3:
                bad.append(f"  例外: {text[:24]} … {type(e).__name__}: {e}")
            continue
        if old == new:
            same += 1
        else:
            diff += 1
            if len(bad) < 5:
                bad.append(f"  不一致: {text[:24]}\n"
                           f"    教師経由 = {old[:12]}…({len(old)})\n"
                           f"    教師なし = {new[:12]}…({len(new)})")

    print(f"\n一致 {same} / 不一致 {diff} / 例外 {err}（n={len(rows)}）")
    for b in bad:
        print(b)

    # ⚠️ **陽性対照。** 入力を 1 文字落とせば必ず不一致になるはず。
    #    ならなければ、上の「一致」は何も見ていない。
    probe_n = min(50, len(rows))
    fired = 0
    for text in rows[:probe_n]:
        try:
            old, _, s = both(text)
            info = encode_student(s[1:], table)
            if info["kind"] != 0 or list(info["ids"]) != old:
                fired += 1
        except Exception:                                            # noqa: BLE001
            fired += 1
    print(f"\n陽性対照（中間表現を 1 文字落とす）: {probe_n} 件中 {fired} 件が不一致")
    if fired < probe_n:
        print("NG! 壊した入力が一致してしまった = **この検査は空虚**")
        return 1

    if diff or err:
        print(f"\nNG! 漢字経路とかな経路が違う ids を作る（不一致 {diff} / 例外 {err}）")
        return 1
    print(f"\nOK  漢字で書いても かなで書いても同じ生徒 index（{same}/{len(rows)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
