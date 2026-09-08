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
            dropped[(source, verdict.value)] += 1
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
