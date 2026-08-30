#!/usr/bin/env python3
"""ドキュメントが名乗る M / D / C の範囲と件数が、実体と一致するかを検査する。

    uv run python scripts/check_doc_counters.py

⚠️ **これは「手で直すのを忘れる」ことへの対策**である。番号は測定や訂正を足すたびに
増えるので、**索引を書いた瞬間から古くなる**。実際にこのプロジェクトでは
`M-1〜M-51` / `D-001〜D-034` / `C-001〜C-027` が 4 つのファイルに残っていた（C-042）。
しかも **C-042 を書いた直後にまた 1 つずれた**（訂正を足せば C の最大値が動くので当然）。

⚠️ **このゲートは「番号」しか見ない。** 中身が古いことは検出できない
（例: サイズの実測値がコードと食い違っている、など）。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 走査するファイルと、そこに書かれうる表記のゆれ
TARGETS = ["README.md", "README.en.md", "docs/README.md", "CLAUDE.md"]

# 「M-1〜M-63」「M-1–M-63」「D-001〜D-040」「C-001–C-039」を拾う。
# ⚠️ **接頭辞ごとに桁数まで固定する。** `D-0*1` のように緩くすると
#    Phase の「D-1〜D-3」を決定番号と誤検出する（実際に踏んだ）。
RANGES = {
    "M": re.compile(r"\bM-1\s*[〜–~-]\s*(?:M-)?(\d+)"),
    "D": re.compile(r"\bD-001\s*[〜–~-]\s*(?:D-)?0*(\d+)"),
    "C": re.compile(r"\bC-001\s*[〜–~-]\s*(?:C-)?0*(\d+)"),
}
# 「**39 件**記録」「**39 entries** record」
COUNT_JA = re.compile(r"\*\*(\d+) 件\*\*記録")
COUNT_EN = re.compile(r"\*\*(\d+) entries\*\* record")


def actual() -> dict[str, tuple[int, int]]:
    """{prefix: (最大番号, 件数)}"""
    out = {}
    for prefix, path in (("M", "docs/measurements.md"),
                         ("D", "docs/decisions.md"),
                         ("C", "docs/decisions.md")):
        text = (ROOT / path).read_text(encoding="utf-8")
        nums = sorted({int(m) for m in
                       re.findall(rf"^## {prefix}-0*(\d+)", text, re.M)})
        if not nums:
            raise SystemExit(f"{path} に {prefix}-NNN の見出しが 1 つも無い")
        out[prefix] = (nums[-1], len(nums))
    return out


def check(paths: list[str], truth: dict[str, tuple[int, int]]) -> list[str]:
    bad: list[str] = []
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"{rel}: 存在しない")
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for prefix, rx in RANGES.items():
                for got in rx.findall(line):
                    want = truth[prefix][0]
                    if int(got) != want:
                        bad.append(f"{rel}:{i}  {prefix}-…〜{prefix}-{got} → 実体は {want}")
            for m in COUNT_JA.finditer(line) :
                if int(m.group(1)) != truth["C"][1]:
                    bad.append(f"{rel}:{i}  訂正 {m.group(1)} 件 → 実体は {truth['C'][1]} 件")
            for m in COUNT_EN.finditer(line):
                if int(m.group(1)) != truth["C"][1]:
                    bad.append(f"{rel}:{i}  {m.group(1)} entries → 実体は {truth['C'][1]}")
    return bad


def main() -> int:
    truth = actual()
    print(f"実体: M-{truth['M'][0]}（{truth['M'][1]} 件）/ "
          f"D-{truth['D'][0]:03d}（{truth['D'][1]} 件）/ "
          f"C-{truth['C'][0]:03d}（{truth['C'][1]} 件）")

    # ⚠️ **陽性対照。** 検出器が壊れていれば「0 件」は無意味なので、
    #    わざと 1 つずらしたテキストを先に食わせて、必ず捕まることを示す。
    probe = ROOT / "docs" / ".counter_probe.md"
    probe.write_text(
        f"M-1〜M-{truth['M'][0] + 1} / D-001〜D-{truth['D'][0] + 1:03d} / "
        f"C-001〜C-{truth['C'][0] + 1:03d} と**{truth['C'][1] + 1} 件**記録\n",
        encoding="utf-8")
    try:
        control = check(["docs/.counter_probe.md"], truth)
    finally:
        probe.unlink()
    if len(control) < 4:
        print(f"NG! 陽性対照が {len(control)} 件しか捕まらない（4 件ずらしてある）"
              f" = **このゲートは空虚**")
        for c in control:
            print("     " + c)
        return 1
    print(f"陽性対照: わざとずらした 4 箇所すべてを検出")

    bad = check(TARGETS, truth)
    if bad:
        print(f"\nNG! 番号が実体とずれている（{len(bad)} 箇所）:")
        for b in bad:
            print("  " + b)
        return 1
    print(f"OK  {len(TARGETS)} ファイルの番号がすべて実体と一致")
    print("⚠️ 見ていないもの: 数値の中身が古いこと（サイズ・実測値など）は検出できない")
    return 0


if __name__ == "__main__":
    sys.exit(main())
