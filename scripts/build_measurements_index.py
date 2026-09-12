#!/usr/bin/env python3
"""`docs/measurements.md` の索引を見出しから作り直す。

    uv run --no-project python scripts/build_measurements_index.py
    uv run --no-project python scripts/build_measurements_index.py --check   # 古くないか見るだけ

## なぜ要るのか

`measurements.md` は **12,000 行を超える**。読む人は「M-90 はどこ」を探すのに
ファイル全体を grep することになる。索引が要る。

⚠️ **索引を手で書いてはいけない。** 節は測るたびに増えるので、手で書いた索引は
**必ず古くなる**（[C-042](decisions.md#c-042) と同じ形 = 同じ数字が複数箇所にあって片方が腐る）。
**見出しから機械的に作る。**

⚠️ **測定は 1 つも消さない。** ここは数値の一次ソースで、古い測定も
「**当時の条件で何が出たか**」の記録である。新しい測定が古いものを訂正するときは
C 番号で残す、という運用になっている。**整理 = 削除ではなく索引。**

⚠️ **見ないもの**: 節の中身が正しいか / 番号が実体と合っているか
（それは `scripts/check_doc_counters.py`）。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "measurements.md"

HEAD = re.compile(r"^## (M-\d+)\.?\s*(.*)")
OPEN = "<!-- ⚠️ この索引は scripts/build_measurements_index.py が見出しから作る。手で書かない -->"
CLOSE = "</details>"


def short_title(title: str) -> str:
    """見出しは長いので、最初の「—」か「（」で切る。"""
    s = re.split(r"\s*—|\s*（", title)[0].strip()
    return s.replace("**", "").rstrip("。")[:76]


def build(lines: list[str]) -> tuple[list[str], int]:
    rows = []
    first = None
    for i, l in enumerate(lines):
        m = HEAD.match(l)
        if m:
            if first is None:
                first = i
            rows.append((m.group(1), short_title(m.group(2))))
    if first is None:
        raise SystemExit("NG! `## M-` の見出しが 1 つも無い")
    toc = [OPEN, "<details>",
           f"<summary><b>索引（{len(rows)} 件）</b> — "
           "⚠️ <b>新しいものほど下</b>。食い違ったら<b>下</b>が正</summary>",
           "", "| # | 何を測ったか |", "|---|---|"]
    toc += [f"| [{num}](#{num.lower()}) | {t} |" for num, t in rows]
    toc += ["", CLOSE, ""]
    return toc, first


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="書き換えずに古いかどうかだけ見る")
    a = ap.parse_args()

    lines = DOC.read_text(encoding="utf-8").split("\n")
    toc, first = build(lines)

    start = next((k for k, l in enumerate(lines) if l == OPEN), None)
    if start is None:
        new = lines[:first] + toc + lines[first:]
        where = "入れた"
    else:
        end = next(k for k in range(start, len(lines)) if lines[k] == CLOSE)
        new = lines[:start] + toc + lines[end + 2:]
        where = "差し替えた"

    n = sum(1 for l in toc if l.startswith("| [M-"))
    if new == lines:
        print(f"OK  索引は最新（{n} 件）")
        return 0
    if a.check:
        print(f"NG! 索引が古い（{n} 件に作り直す必要がある）"
              " — `uv run --no-project python scripts/build_measurements_index.py` を回すこと")
        return 1
    DOC.write_text("\n".join(new), encoding="utf-8")
    print(f"OK  索引を{where}（{n} 件 / 全体 {len(new):,} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
