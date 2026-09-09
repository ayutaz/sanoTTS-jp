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


#: **評価専用の split の集合。** 評価専用 = 重みに入らない = ライセンスの論点が
#: 発生しない split。`heldout` だけを見ていたのがレビュー指摘の欠陥
#: （`sibdense` も heldout から派生した評価専用 split で、同じ穴が開いていた。
#: `b6_build_evalset.py` が既定 `--split heldout` で読み、`corpus_sibdense.tsv`
#: を書く。221 行中 71 行が `jsut/*` = 32%）。**新しい評価専用 split を足すとき
#: はここに追記すること** — 1 箇所に集めることで、次の split が
#: `resolve_license_filter` を素通りして静かに縮む再発を防ぐ。
EVAL_ONLY_SPLITS: frozenset[str] = frozenset({"heldout", "sibdense"})


def resolve_license_filter(split: str, explicit: bool | None) -> tuple[bool, str]:
    """`--split` と `--license-filter`/`--no-license-filter` から、実際に
    絞り込みを掛けるかどうかを**構造的に**決める。

    ⚠️ **評価専用の split を絞り込んではいけない**（設計 §4.3 / D-056）。
    評価専用（`EVAL_ONLY_SPLITS`）は重みに入らないためライセンスの論点が
    発生せず、外すと held-out で 2,333 → 1,616 行になって v3 の
    M-49 / M-59 / M-61 と比較できなくなる。`license_filter=True` を既定にした
    まま `--split` だけで分岐しなかったのがレビュー指摘の欠陥
    （`--split heldout --out data/pack_heldout` が 717 行を静かに落としていた）。
    ⚠️ **判定は `split == "heldout"` という 1 つの文字列一致ではなく
    `split in EVAL_ONLY_SPLITS` で行う** — `sibdense` のような他の評価専用
    split が増えても同じ穴が開かないようにするため。

    `explicit` が `None`（コマンドラインで `--license-filter` /
    `--no-license-filter` のどちらも指定していない）のときだけ、
    `split` に応じた既定値を返す。明示指定は常に勝つ。

    ⚠️ **`EVAL_ONLY_SPLITS` に無い split（`train` 含む、将来の未知の split も）は
    既定 ON（絞り込む）側に倒す。** 「評価専用と分かっている split だけを
    OFF にし、それ以外は安全側（copyleft テキストを混ぜない）に倒す」という
    設計で、未知の split 名を新しく足したときに黙って学習データへ
    copyleft テキストが混入する事故を防ぐ。
    """
    if explicit is not None:
        flag = "--license-filter" if explicit else "--no-license-filter"
        return explicit, f"{flag} で明示指定"
    if split in EVAL_ONLY_SPLITS:
        return False, (
            f"{split} の既定 = OFF（評価専用・据え置き。設計 §4.3 / D-056）")
    return True, f"{split} の既定 = ON（D-054。評価専用と分かっていない split の安全側デフォルト）"
