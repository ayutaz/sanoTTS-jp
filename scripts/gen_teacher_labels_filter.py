#!/usr/bin/env python3
"""ラベル生成の行フィルタ（ライセンス）。

⚠️ **`gen_teacher_labels.py` から切り出してある。** あちらは import すると
torch と教師 ckpt を掴むので、**依存ゼロで検査できるようにここに置く**。
"""

from __future__ import annotations

import collections
import sys

sys.path.insert(0, "src")
from saanotts_jp.corpus_license import Verdict, classify  # noqa: E402


def filter_by_license(
    rows: list[list[str]],
) -> tuple[list[list[str]], collections.Counter]:
    """`source` 列が許可されている行だけを返す。

    ⚠️ **UNKNOWN は落とさず SystemExit で止める。**
    黙って行が減るのが一番危ない（C-018 と同じ形）。
    """
    kept: list[list[str]] = []
    dropped: collections.Counter = collections.Counter()
    unknown: dict[str, int] = collections.Counter()
    for r in rows:
        source = r[0]
        verdict, why = classify(source)
        if verdict is Verdict.ALLOWED:
            kept.append(r)
        elif verdict is Verdict.DENIED:
            # ⚠️ **キーの第2要素は `classify()` が返す理由文そのもの**にする。
            #    かつて `verdict.value`（= 定数 "denied"）を積んでいて、実行ログの
            #    括弧の中に「なぜ拒否されたか」が一切出ていなかった（レビュー指摘）。
            dropped[(source, why)] += 1
        else:
            unknown[source] += 1
    if unknown:
        detail = ", ".join(f"{s} ({n} 行)" for s, n in sorted(unknown.items()))
        raise SystemExit(
            f"表に無い source が {len(unknown)} 種 / {sum(unknown.values())} 行ある: "
            f"{detail}\n"
            "ライセンスを一次ソースで確認して "
            "src/saanotts_jp/corpus_license.py の ALLOWED か DENIED に足すこと。\n"
            "⚠️ 黙って落とすと、行数が静かに減ったことに誰も気づかない。")
    return kept, dropped


def resolve_license_filter(split: str, explicit: bool | None) -> tuple[bool, str]:
    """`--split` と `--license-filter`/`--no-license-filter` から、実際に
    絞り込みを掛けるかどうかを**構造的に**決める。

    ⚠️ **held-out を絞り込んではいけない**（設計 §4.3 / D-056）。held-out は
    評価専用で重みに入らないためライセンスの論点が発生せず、外すと
    2,333 → 1,616 行になって v3 の M-49 / M-59 / M-61 と比較できなくなる。
    `license_filter=True` を既定にしたまま `--split` だけで分岐しなかったのが
    レビュー指摘の欠陥（`--split heldout --out data/pack_heldout` が
    717 行を静かに落としていた）。

    `explicit` が `None`（コマンドラインで `--license-filter` /
    `--no-license-filter` のどちらも指定していない）のときだけ、
    `split` に応じた既定値を返す。明示指定は常に勝つ。
    """
    if explicit is not None:
        flag = "--license-filter" if explicit else "--no-license-filter"
        return explicit, f"{flag} で明示指定"
    if split == "heldout":
        return False, "heldout の既定 = OFF（評価専用・据え置き。設計 §4.3 / D-056）"
    return True, "train の既定 = ON（D-054）"
