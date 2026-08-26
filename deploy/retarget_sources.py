#!/usr/bin/env python3
"""`pyproject.toml` の piper-plus への path 依存を、このマシンのパスに向け直す。

**ローカルは絶対パスを指している。** vast.ai にはそのパスが無いので
`uv sync` が失敗する。ここで書き換える。

`[tool.uv.sources]` の 2 行だけを触り、それ以外が変わっていないことを assert する。
"""

from __future__ import annotations

import argparse
import pathlib
import re

KEYS = {"piper-train": "src/python", "piper-plus-g2p": "src/python/g2p"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="piper-plus の checkout パス")
    ap.add_argument("--file", default="pyproject.toml")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    for sub in KEYS.values():
        if not (root / sub).is_dir():
            raise SystemExit(f"{root/sub} が無い。clone に失敗している")

    p = pathlib.Path(args.file)
    before = p.read_text()
    after = before
    for key, sub in KEYS.items():
        pat = re.compile(
            rf'^({re.escape(key)}\s*=\s*\{{\s*path\s*=\s*)"[^"]*"', re.M)
        after, n = pat.subn(rf'\1"{root / sub}"', after)
        if n != 1:
            raise SystemExit(f"{key} の path 依存が 1 件見つからない（{n} 件）")

    def strip(s: str) -> str:
        return re.sub(r'path\s*=\s*"[^"]*"', 'path="X"', s)

    if strip(before) != strip(after):
        raise SystemExit("path 以外が変わっている。中止する")

    if before != after:
        p.write_text(after)
        print(f"pyproject.toml の path 依存を {root} に向けた")
    else:
        print("変更なし（既にこのパスを向いている）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
