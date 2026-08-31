#!/usr/bin/env python3
"""ドキュメントが「落としてこい」と書いた資産が、**実際にリリースに在るか**を検査する。

    uv run python scripts/check_release_assets.py
    uv run python scripts/check_release_assets.py --offline-ok   # 手元用

⚠️ **これは C-052 の再発防止**である。v0.2.0 を出した時点で `releases/latest` が
そちらに移り、**モデルの重みが無かった**ので README のダウンロードリンク 5 本が
全部壊れた。`check_doc_links.py` は相対リンクしか見ないので捕まえられない。

**どうやって「あるべき資産」を知るか**: ハードコードしない。
**ドキュメントの表を読む。** マークダウンの表の行で

    | `<資産名>` | ... releases/tag/<タグ> ... | ... |

の形をしているものを (タグ, 資産名) として集め、GitHub API と突き合わせる。
⚠️ **つまり README に名前を書いた瞬間、その資産は在ることを要求される。**

さらに **`releases/latest` にも全部在ること**を要求する。README の本文は
`releases/latest` を指しているので、latest に無いと同じ形で壊れる。

**見ないもの:**

- 資産の**中身**（ハッシュ）。名前が在るかだけ。⚠️ 中身の検証はリリース時の
  `SHA256SUMS.txt` にしかない
- 表に書いていない資産（リリースに余分に在っても NG にしない）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 表を読むファイル。⚠️ **増やすときは「読者に落とさせている」ファイルだけ**
DOCS = ["README.md", "README.en.md", "esp32/TESTING.md", "esp32/README.md",
        "CONTRIBUTING.md", "MODEL_CARD.md"]

ROW = re.compile(r"^\|\s*\**`([^`]+)`\**\s*\|(.*)$")
TAG = re.compile(r"releases/tag/([A-Za-z0-9._-]+)")
REPO = re.compile(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/releases")
# 資産名っぽいものだけ拾う（`main.c` や `--flag` を資産と誤認しないため）
ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(bin|pt|zip|txt|md|json)$")


def collect() -> tuple[dict[str, set[str]], str | None]:
    """({タグ: {資産名}}, owner/repo)"""
    want: dict[str, set[str]] = {}
    repo = None
    for rel in DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        m = REPO.search(text)
        if m and repo is None:
            repo = m.group(1)
        for line in text.splitlines():
            r = ROW.match(line)
            if not r:
                continue
            name, rest = r.group(1), r.group(2)
            if not ASSET.match(name):
                continue
            t = TAG.search(rest)
            if t:
                want.setdefault(t.group(1), set()).add(name)
    return want, repo


def assets_of(repo: str, ref: str) -> set[str]:
    url = (f"https://api.github.com/repos/{repo}/releases/latest" if ref == "latest"
           else f"https://api.github.com/repos/{repo}/releases/tags/{ref}")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "sanoTTS-jp-check-release-assets",
    })
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return {a["name"] for a in json.load(r).get("assets", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-ok", action="store_true",
                    help="ネットワークに出られないときに 0 で抜ける（⚠️ CI では付けない）")
    a = ap.parse_args()

    want, repo = collect()
    if not repo:
        print("NG! ドキュメントから owner/repo を読めない = **この検査は空虚**")
        return 1
    if not want:
        print("NG! 資産名を 1 つも拾えなかった = **この検査は空虚**")
        return 1
    n = sum(len(v) for v in want.values())
    print(f"{repo} / ドキュメントの表から {len(want)} タグ・{n} 件の資産名を拾った")

    try:
        have = {ref: assets_of(repo, ref) for ref in list(want) + ["latest"]}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        msg = f"⚠️ GitHub API に届かない（{type(e).__name__}: {e}）"
        if a.offline_ok:
            print(msg + " → --offline-ok なので SKIP")
            print("⚠️ **これは合格ではない。** CI 側で必ず検査すること")
            return 0
        print("NG! " + msg)
        return 1

    # ⚠️ **陽性対照。** 在るはずのないものを混ぜて、必ず落ちることを示す。
    probe_tag = next(iter(want))
    if "__no_such_asset__.bin" in have[probe_tag]:
        print("NG! 陽性対照の名前が実在してしまっている")
        return 1
    print(f"陽性対照: 実在しない名前が {probe_tag} の資産に無いことを確認")

    bad: list[str] = []
    for tag, names in sorted(want.items()):
        miss = sorted(names - have[tag])
        print(f"  {tag:>8}: 資産 {len(have[tag])} 本 / 要求 {len(names)} 本"
              f"{'  ← ' + ', '.join(miss) if miss else '  OK'}")
        bad += [f"{tag} に {m} が無い" for m in miss]

    # latest にも全部在ること（README 本文が latest を指しているため）
    all_names = set().union(*want.values())
    miss_latest = sorted(all_names - have["latest"])
    print(f"  {'latest':>8}: 資産 {len(have['latest'])} 本 / 要求 {len(all_names)} 本"
          f"{'  ← ' + ', '.join(miss_latest) if miss_latest else '  OK'}")
    bad += [f"latest に {m} が無い（README 本文が latest を指している）"
            for m in miss_latest]

    if bad:
        print(f"\nNG! {len(bad)} 件足りない:")
        for b in bad:
            print("  " + b)
        return 1
    print("OK  ドキュメントが名前を挙げた資産は全部リリースに在る")
    print("⚠️ 見ていないもの: 資産の**中身**（ハッシュはリリースの SHA256SUMS.txt のみ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
