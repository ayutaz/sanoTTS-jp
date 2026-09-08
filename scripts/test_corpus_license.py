#!/usr/bin/env python3
"""G-L1a: 蒸留テキストのライセンス判定（依存ゼロ）。

**ゲートは「落ちるべきものが落ちる」ことを確かめないと意味がない。**
ここでは (a) 許可 (b) 明示的な拒否 (c) **未知** の 3 通りを見る。

実行:
    uv run --no-project --python 3.12 python scripts/test_corpus_license.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
from saanotts_jp.corpus_license import (  # noqa: E402
    ALLOWED, DENIED, Verdict, classify,
)

# 2026-09-08 に data/splits/corpus_train.tsv と corpus_heldout.tsv を
# `cut -f1 | sort | uniq -c` で数えた実在の source（和集合 16 件）。
REAL_ALLOWED = [
    "cv/sentence_collector", "cv/yumie-text-1", "cv/singleword-benchmark",
    "rohan4600", "ita/recitation324", "ita/emotion100", "curated/question_eos",
]
REAL_DENIED = [
    "jsut/basic5000", "jsut/travel1000", "jsut/utparaphrase512",
    "jsut/onomatopee300", "jsut/precedent130", "jsut/loanword128",
    "jsut/repeat500", "jsut/countersuffix26", "jsut/voiceactress100",
]


def test_allowed() -> int:
    bad = 0
    for src in REAL_ALLOWED:
        v, why = classify(src)
        if v is Verdict.ALLOWED:
            print(f"  OK  許可 {src:26s} {why}")
        else:
            print(f"  NG! {src} が許可されない: {v} / {why}")
            bad += 1
    return bad


def test_denied() -> int:
    bad = 0
    for src in REAL_DENIED:
        v, why = classify(src)
        if v is Verdict.DENIED:
            print(f"  OK  拒否 {src:26s} {why}")
        else:
            print(f"  NG! {src} が拒否されない: {v} / {why}")
            bad += 1
    return bad


def test_unknown() -> int:
    """⚠️ **ここが本体。** 未知の source は黙って通ってはいけない。

    `cv/` の前方一致で許可していると、CV が europarl 由来のファイルを足した
    ときに自動で通る（C-029: europarl は CV の README が挙げる唯一の CC0 例外）。
    """
    bad = 0
    cases = [
        "cv/europarl-v7-ja",   # ⚠️ CC0 ではない。前方一致だと通ってしまう
        "cv/some-new-file",
        "jsut/newsubset",
        "wikipedia/ja",
        "",
    ]
    for src in cases:
        v, why = classify(src)
        if v is Verdict.UNKNOWN:
            print(f"  OK  未知 {src!r:28s} {why}")
        else:
            print(f"  NG! 未知の {src!r} が {v} になった: {why}")
            bad += 1
    return bad


def test_tables_disjoint() -> int:
    """同じ source が両方の表に居たら、判定は表の順序に依存してしまう。"""
    overlap = set(ALLOWED) & set(DENIED)
    if overlap:
        print(f"  NG! ALLOWED と DENIED が重複: {sorted(overlap)}")
        return 1
    print(f"  OK  表は排他（許可 {len(ALLOWED)} / 拒否 {len(DENIED)}）")
    return 0


def test_positive_control() -> int:
    """⚠️ **陽性対照**: 表から 1 件抜くと、その source は UNKNOWN に落ちる。

    これが落ちなければ、上の 3 つのテストは**表を見ていない**ことになる。
    """
    victim = "rohan4600"
    saved = ALLOWED.pop(victim)
    try:
        v, _ = classify(victim)
        if v is Verdict.UNKNOWN:
            print(f"  OK  陽性対照: 表から {victim} を抜くと UNKNOWN になる")
            return 0
        print(f"  NG! 陽性対照が効かない（{victim} を抜いても {v}）= **判定は表を見ていない**")
        return 1
    finally:
        ALLOWED[victim] = saved


def test_applied_in_label_gen() -> int:
    """⚠️ **判定表が gen_teacher_labels.py に実際に配線されているか。**

    G-L1a は表しか見ないので、呼び忘れを捕まえられない。ここでは
    「絞り込み関数が export されていて、行のリストを正しく削る」ことを見る。
    **教師モデルは読み込まない**（重いので）。
    """
    import importlib
    m = importlib.import_module("gen_teacher_labels_filter")
    rows = [
        ["cv/sentence_collector", "a", "あ"],
        ["jsut/basic5000", "b", "い"],
        ["rohan4600", "c", "う"],
        ["ita/emotion100", "d", "え"],
    ]
    kept, dropped = m.filter_by_license(rows)
    bad = 0
    if [r[1] for r in kept] != ["a", "c", "d"]:
        print(f"  NG! 絞り込みの結果が違う: {[r[1] for r in kept]}")
        bad += 1
    else:
        print("  OK  jsut/basic5000 の 1 行だけが落ちた")
    # ⚠️ キーの第2要素は `classify()` の理由文そのもの（`verdict.value` の
    #    定数 "denied" ではない）。かつてそこに定数を積んでいて、実行ログの
    #    括弧の中に「なぜ拒否されたか」が一切出ていなかった（レビュー指摘）。
    basic5000_reason = DENIED["jsut/basic5000"]
    if dropped.get(("jsut/basic5000", basic5000_reason)) != 1:
        print(f"  NG! 内訳が取れていない、または理由文が積まれていない: {dict(dropped)}")
        bad += 1
    else:
        print("  OK  落ちた内訳が source と理由文で数えられている"
              f"（{basic5000_reason!r}）")

    # ⚠️ UNKNOWN は落とさず止める
    try:
        m.filter_by_license([["wikipedia/ja", "x", "お"]])
    except SystemExit as exc:
        print(f"  OK  未知の source で実行が止まる: {str(exc)[:40]}…")
    else:
        print("  NG! 未知の source が**黙って落ちた**（止まらなければ行数が静かに減る）")
        bad += 1
    return bad


def test_heldout_default_not_filtered() -> int:
    """⚠️ **レビュー指摘の再発防止**: `--split heldout` は既定でライセンス絞り込み
    が **OFF** でなければならない。

    held-out は評価専用で重みに入らないため、ライセンスの論点が発生しない
    （設計 `docs/superpowers/specs/2026-09-08-cc0-only-distillation-text-design.md`
    §4.3 / D-056）。ON のままにすると `--split heldout --out data/pack_heldout`
    （`CLAUDE.md` / `docs/README.md` / `docs/measurements.md` /
    `deploy/vastai_bootstrap.sh` が指示する形そのもの）が
    2,333 行中 717 行（jsut/*）を黙って落とす。**`train` は既定 ON のまま**
    でなければならない（そちらは重みに入るので D-054 の絞り込みが必要）。
    """
    import importlib
    m = importlib.import_module("gen_teacher_labels_filter")
    bad = 0
    cases = [
        # (split, 明示指定, 期待する絞り込みの ON/OFF)
        ("heldout", None, False),   # ← ここが今回のレビュー指摘の核心
        ("train", None, True),
        ("heldout", True, True),    # 明示指定は常に勝つ
        ("heldout", False, False),
        ("train", False, False),
        ("train", True, True),
    ]
    for split, explicit, expected in cases:
        on, reason = m.resolve_license_filter(split, explicit)
        if on != expected:
            print(f"  NG! resolve_license_filter({split!r}, {explicit!r}) = {on}"
                  f"（期待 {expected}）/ {reason}")
            bad += 1
        else:
            print(f"  OK  resolve_license_filter({split!r}, {explicit!r})"
                  f" = {on}  ({reason})")
    return bad


def main() -> int:
    bad = (test_allowed() + test_denied() + test_unknown()
           + test_tables_disjoint() + test_positive_control()
           + test_applied_in_label_gen() + test_heldout_default_not_filtered())
    print()
    print("⚠️ 見ていないもの: **判定が実際に適用されたか**"
          "（gen_teacher_labels.py が呼び忘れてもこのテストは通る）。"
          "それは G-L1b = scripts/check_corpus_license.py --pack が見る。")
    print()
    print("すべて期待通り" if bad == 0 else f"{bad} 件 NG")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
