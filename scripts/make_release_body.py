#!/usr/bin/env python3
"""リリースノートの md を**リリースページ用の本文**に変換する。

    uv run --no-project python scripts/make_release_body.py \
        docs/release-notes/v1.0.0.md > /tmp/body.md

## なぜ要るのか

`docs/release-notes/*.md` は**リポジトリの中**にあるので `../decisions.md#c-081` の
ような相対リンクが効く。**リリースページでは効かない**（別のドメインの別のページ）。
実際に `v0.3.1` の本文は**相対リンク 0 本**で、手で書き直されていた。
**手で書き直すと、本文が 2 つに分かれて片方が古くなる**（[C-080](../docs/decisions.md#c-080) の形）。

→ **1 つの md から機械的に作る。**

## 何をするか

- 相対リンク `](../x.md#y)` を `](https://github.com/<repo>/blob/main/docs/x.md#y)` に直す
- それ以外は**触らない**（本文は md のまま）

⚠️ **見ないもの**: リンク先が実在するか（`scripts/check_doc_links.py` が repo 側で見る）。
⚠️ **ブランチは `main` を指す。** マージ前にリリースすると**リンクが切れる**。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = "ayutaz/sanoTTS-jp"
# `](../decisions.md#c-081)` / `](../measurements.md#m-125)` の形
REL = re.compile(r"\]\(\.\./([A-Za-z0-9_./-]+\.md)(#[A-Za-z0-9_-]+)?\)")


def convert(text: str, repo: str = REPO, branch: str = "main") -> tuple[str, int]:
    n = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        anchor = m.group(2) or ""
        return f"](https://github.com/{repo}/blob/{branch}/docs/{m.group(1)}{anchor})"

    return REL.sub(sub, text), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notes")
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    text = pathlib.Path(a.notes).read_text(encoding="utf-8")
    out, n = convert(text, branch=a.branch)
    left = re.findall(r"\]\((?!http|#)([^)]+)\)", out)
    if left:
        print(f"NG! 相対リンクが {len(left)} 本残った: {left[:5]}", file=sys.stderr)
        return 1
    print(f"相対リンク {n} 本を絶対 URL（{a.branch}）に直した", file=sys.stderr)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
