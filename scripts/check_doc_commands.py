#!/usr/bin/env python3
"""ドキュメントが「打て」と書いたコマンドの**実体が在るか**を検査する。

    uv run --no-project python scripts/check_doc_commands.py
    uv run --no-project python scripts/check_doc_commands.py --self-test   # 陽性対照 4 件

## なぜ要るのか

[`check_doc_links.py`](check_doc_links.py) は **md のリンク**しか見ない。
**コードフェンスの中の実行コマンドは誰も見ていない**:

    uv run python scripts/kana_g2p.py      ← このファイルが消えても誰も落ちない
    make -C csrc jdict-hard                ← このターゲットが消えても同上

⚠️ **読者が最初に打つのはここ**である。README の「はじめかた」が動かないのは
[C-040](../docs/decisions.md#c-040) で実際に踏んだ（リリースの「使い方」が 2 行とも動かなかった）。

## 何を見るか

| | |
|---|---|
| `uv run … python <path>.py` / `bash <path>.sh` | **そのファイルが在るか** |
| `make -C csrc <target>` | **`csrc/Makefile` にそのターゲットが在るか** |

## ⚠️ 見ないもの

- **コマンドが通るか。** 在るかだけ（依存が要るものは CI では走らせられない）
- **引数が正しいか。** `--expect-steps 54` のような値は見ない
- **`scripts/xxx.py` のような説明用のプレースホルダ**（`PLACEHOLDERS` で除外）
- **`csrc/` 以外の `make`**、シェル変数を含むパス
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# `uv run --no-project python scripts/x.py` / `bash scripts/x.sh` / `uv run python .claude/hooks/y.py`
SCRIPT = re.compile(
    r"(?:uv run[^\n|]*?python3?|bash|sh)\s+((?:scripts|\.claude)/[\w/.-]+\.(?:py|sh))")
MAKE = re.compile(r"make -C csrc ([a-z0-9-]+)")

# ⚠️ 説明のための架空の名前。**実体を要求しない。**
PLACEHOLDERS = {"scripts/xxx.py", "scripts/yyy.py"}

# 読者が実際に打つファイルだけを見る（計画や測定の記録は当時のコマンドなので対象外）
DOCS = ["CLAUDE.md", "docs/README.md", "CONTRIBUTING.md", "MODEL_CARD.md",
        "README.md", "README.en.md", "esp32/README.md", "esp32/TESTING.md",
        "docs/getting-started.md", "docs/getting-started.en.md"]


def make_targets(makefile: str) -> set[str]:
    t = {m.group(1) for m in re.finditer(r"^([a-z0-9-]+):", makefile, re.M)}
    for m in re.finditer(r"^\.PHONY:(.*)$", makefile, re.M):
        t |= set(m.group(1).split())
    return t


def check(docs: dict[str, str], have_file, targets: set[str]) -> tuple[list[str], int, int]:
    """(NG の一覧, 見たスクリプト数, 見た make ターゲット数)"""
    bad: list[str] = []
    seen_s: set[str] = set()
    seen_t: set[str] = set()
    for name, text in docs.items():
        for rel in SCRIPT.findall(text):
            if rel in PLACEHOLDERS:
                continue
            seen_s.add(rel)
            if not have_file(rel):
                bad.append(f"{name}: **スクリプトが無い** → {rel}")
        for t in MAKE.findall(text):
            seen_t.add(t)
            if t not in targets:
                bad.append(f"{name}: **make のターゲットが無い** → make -C csrc {t}")
    return bad, len(seen_s), len(seen_t)


def self_test() -> int:
    """陽性対照。**落ちるべき壊し方が本当に落ちるか。**"""
    targets = {"all-test", "jdict"}
    have = lambda rel: rel in {"scripts/real.py", "scripts/real.sh"}
    cases = [
        ("無改変（陰性対照）",
         "```\nuv run python scripts/real.py\nmake -C csrc all-test\n```", 0),
        ("消えたスクリプトを指す",
         "```\nuv run python scripts/gone.py\n```", 1),
        ("消えた make ターゲットを指す",
         "```\nmake -C csrc gone\n```", 1),
        ("bash で消えたものを指す",
         "```\nbash scripts/gone.sh\n```", 1),
        # ⚠️ プレースホルダで落とさない（説明文が書けなくなる）
        ("プレースホルダは数えない",
         "```\nuv run python scripts/xxx.py\n```", 0),
    ]
    print("陽性対照:")
    ng = 0
    for label, text, want in cases:
        got, ns, nt = check({"t.md": text}, have, targets)
        okng = "OK " if len(got) == want else "NG!"
        if len(got) != want:
            ng += 1
        print(f"  {okng} {label} → 検出 {len(got)}（期待 {want}）")
    print(f"\n{'OK' if not ng else 'NG!'} 陽性対照 {len(cases)} 件 / 失敗 {ng}")
    return 1 if ng else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="陽性対照 4 件 + 陰性対照")
    if ap.parse_args().self_test:
        return self_test()

    docs = {}
    for rel in DOCS:
        p = ROOT / rel
        if p.exists():
            docs[rel] = p.read_text(encoding="utf-8")
    targets = make_targets((ROOT / "csrc" / "Makefile").read_text(encoding="utf-8"))
    bad, ns, nt = check(docs, lambda rel: (ROOT / rel).exists(), targets)

    # ⚠️ **「0 件」を無条件に緑にしない。** 正規表現が 1 つも当たらなければ NG 0 になる。
    if ns < 10 or nt < 5:
        print(f"NG! 拾えたのがスクリプト {ns} 種 / make {nt} 種 = **少なすぎる**"
              f"（抽出が壊れている疑い）")
        return 1

    print(f"{len(docs)} ファイルから スクリプト {ns} 種 / make ターゲット {nt} 種を拾って検査")
    if bad:
        print(f"\nNG! {len(bad)} 件が実在しない:")
        for b in bad:
            print("  " + b)
        return 1
    print("OK  すべて実在する")
    print("⚠️ 見ていないもの: **コマンドが通るか**（在るかだけ）/ 引数の正しさ / "
          "計画・測定の記録に出てくるコマンド（当時のもの）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
