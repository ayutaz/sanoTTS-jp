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
- **`<!-- release-body: 内部の手順ここから -->` 〜 `ここまで` を外す**
- それ以外は**触らない**（本文は md のまま）

⚠️ **内部の手順をリリースページに出さない。** ノートには「タグの打ち方」と
「タグを打った**後**にやること」が書いてある（**打つ人の手順**）。これが
公開ページに出ると、読者に `gh release create` や CI の TODO を見せることになる。
**実際に 1 度出しかけた**（本文 501 行のうち 81 行が内部の手順だった）。
⚠️ **印は対で要る。** 片方だけだと**黙って何も外れない**ので、
**開きと閉じの数が合わなければ落とす**（`--self-test` の陽性対照 5 件）。

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
OPEN = "<!-- release-body: 内部の手順ここから"
CLOSE = "<!-- release-body: 内部の手順ここまで -->"


def strip_internal(text: str) -> tuple[str, int]:
    """内部の手順（印で囲った区間）を落とす。落とした行数を返す。

    ⚠️ **開きと閉じの数が合わなければ例外にする。** 片方だけ書いた場合に
    「0 行落とした」で静かに通ると、内部の手順がそのまま公開される。
    """
    lines = text.split("\n")
    n_open = sum(1 for l in lines if l.startswith(OPEN))
    n_close = sum(1 for l in lines if l.startswith(CLOSE))
    if n_open != n_close:
        raise ValueError(f"内部の手順の印が対になっていない（開き {n_open} / 閉じ {n_close}）")
    out, dropped, depth = [], 0, 0
    for l in lines:
        if l.startswith(OPEN):
            depth += 1
            dropped += 1
            continue
        if l.startswith(CLOSE):
            if depth == 0:
                raise ValueError("閉じの印が開きより先に来た")
            depth -= 1
            dropped += 1
            continue
        if depth:
            dropped += 1
        else:
            out.append(l)
    return "\n".join(out), dropped


def self_test() -> int:
    """⚠️ **陽性対照**。外す仕組みが空虚でないことを要求する。"""
    body = f"よむ\n{OPEN} -->\nうちわけ\n{CLOSE}\nよむ2\n"
    out, dropped = strip_internal(body)
    fail = 0
    if "うちわけ" in out or dropped != 3:
        print(f"NG! 1) 囲った行が落ちていない（落ちた {dropped} 行）"); fail += 1
    if "よむ" not in out or "よむ2" not in out:
        print("NG! 2) 囲っていない行まで落ちた"); fail += 1
    for name, bad in (("開きだけ", f"a\n{OPEN} -->\nb\n"),
                      ("閉じだけ", f"a\n{CLOSE}\nb\n"),
                      ("閉じが先", f"{CLOSE}\n{OPEN} -->\n")):
        try:
            strip_internal(bad)
            print(f"NG! 3) 印が壊れている（{name}）のに通った"); fail += 1
        except ValueError:
            pass
    print(f"{'NG' if fail else 'OK'} 陽性対照 5 件 / 失敗 {fail}")
    return 1 if fail else 0


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
    ap.add_argument("notes", nargs="?")
    ap.add_argument("--self-test", action="store_true",
                    help="印の扱いの陽性対照 5 件（ノートが要らない）")
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.notes:
        print("NG! ノートのパスが要る（--self-test だけは単体で回せる）", file=sys.stderr)
        return 2
    text = pathlib.Path(a.notes).read_text(encoding="utf-8")
    text, dropped = strip_internal(text)
    out, n = convert(text, branch=a.branch)
    left = re.findall(r"\]\((?!http|#)([^)]+)\)", out)
    if left:
        print(f"NG! 相対リンクが {len(left)} 本残った: {left[:5]}", file=sys.stderr)
        return 1
    print(f"相対リンク {n} 本を絶対 URL（{a.branch}）に直した / "
          f"内部の手順 {dropped} 行を外した", file=sys.stderr)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
