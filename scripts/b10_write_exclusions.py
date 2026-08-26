#!/usr/bin/env python3
"""B-10 の検査結果から**除外 uid リスト**を書き出す（評価の過大評価を防ぐ）。

2 段構え:

* **tier1（必須）** `jsut/voiceactress100` と `jsut/repeat500`。
  この 2 サブセットは本文が 98/100 共通で、VOICEACTRESS100 が
  **教師（つくよみちゃんコーパス）の FT テキストそのもの**。
  評価に使うと教師の丸暗記を測ることになる。1 文字違いがあるので
  NFKC 重複排除では併合されない → **サブセット丸ごと落とす**。
* **tier3（推奨・eval のみ）** train と 5-gram Jaccard >= 0.5 または
  8 文字以上の部分文字列包含。train 側は残してよい。

⚠️ **「教師が丸暗記しているぶん過大評価になる」は現時点では未実証。**
教師側の UTMOS は汚染 10 文 1.8502 vs 非汚染 24 文 1.7478 で
差 +0.102 / p=0.334（検出可能最小差 0.279）。除外は予防措置であって、
実害が測れたわけではない。生徒ができてから別集計して確かめること。

実行:
    uv run python scripts/b10_write_exclusions.py
"""

from __future__ import annotations

import argparse
import json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/b10_overlap.json")
    ap.add_argument("--out", default="data/splits/exclusions_teacher_ft.txt")
    ap.add_argument("--tier3-out", default="data/splits/exclusions_near_dup.txt")
    args = ap.parse_args()

    rep = json.load(open(args.report))
    tiers = rep["exclusion_list"]["tiers"]

    def write(path: str, rows: list[dict], header: str) -> int:
        seen: dict[str, dict] = {}
        for r in rows:                      # sibdense は heldout の部分集合。二重計上を潰す
            seen.setdefault(r["id"], r)
        with open(path, "w") as f:
            f.write(f"# {header}\n")
            f.write(f"# 生成: uv run python scripts/b10_write_exclusions.py"
                    f" / 出典: {args.report}\n")
            # **本文は書かない。** JSUT / Common Voice のライセンス上、
            # コーパス本文はこのリポジトリに置かない（.gitignore と同じ方針）
            f.write("# uid\tsource\n")
            for uid, r in sorted(seen.items()):
                f.write(f"{uid}\t{r['source']}\n")
        return len(seen)

    n1 = write(args.out, tiers["tier1_teacher_ft_text"]["rows"],
               "tier1: 教師の FT テキストと重複（必須除外）")
    n3 = write(args.tier3_out, tiers["tier3_near_dup_or_substring_with_train"]["rows"],
               "tier3: train との近重複（eval のみ推奨除外）")
    print(f"tier1 {n1} uid → {args.out}")
    print(f"tier3 {n3} uid → {args.tier3_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
