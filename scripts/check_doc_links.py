#!/usr/bin/env python3
"""md の**相対リンクが実在するか**を検査する。

    uv run python scripts/check_doc_links.py

⚠️ **これは OSS 公開の最低線**である。README からリンクしたファイルが無いと、
読者は最初の 30 秒で詰まる。実際にこのプロジェクトでは、リリースを 1 本足した
だけで**ダウンロードリンク 5 本が全部壊れた**（C-052。あれは外部 URL なので
このゲートでは捕まらないが、同じ形の劣化である）。

**見ないもの:**

- **外部 URL**（http / https / mailto / file）。ネットワークに出ないため。
  ⚠️ **`releases/latest` が壊れる形の劣化は、このゲートでは捕まらない**
- **コードフェンスの中**と**インラインコードの中**。`clip_[1,80](round(·))` の
  ような数式が Markdown のリンク構文と同形になるため（GitHub も同じ扱い）
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_PARTS = (".venv", "node_modules", "openjtalk", ".k1work", ".git",
              "managed_components")   # ESP-IDF Component Registry の取得物（git 管理外）
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
INLINE_CODE = re.compile(r"`[^`]*`")
EXTERNAL = ("http://", "https://", "#", "mailto:", "file://")


def scan(md: pathlib.Path) -> tuple[list[str], int]:
    """(壊れているリンク, 検査した本数)"""
    bad: list[str] = []
    n = 0
    infence = False
    for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            infence = not infence
            continue
        if infence:
            continue
        # ⚠️ インラインコードを先に落とす。落とさないと数式を拾う
        for tgt in LINK.findall(INLINE_CODE.sub("``", line)):
            if tgt.startswith(EXTERNAL):
                continue
            n += 1
            t = tgt.split("#")[0]
            if t and not (md.parent / t).exists():
                bad.append(f"{md.relative_to(ROOT)}:{i}  → {tgt}")
    return bad, n


def walk(root: pathlib.Path) -> tuple[list[str], int, int]:
    bad: list[str] = []
    n = files = 0
    for md in sorted(root.rglob("*.md")):
        if any(x in md.parts for x in SKIP_PARTS):
            continue
        b, k = scan(md)
        bad += b
        n += k
        files += 1
    return bad, n, files


def main() -> int:
    # ⚠️ **陽性対照。** 「0 本壊れている」が検出器の無能でないことを先に示す。
    #    C-052 では、パターンが 1 件も一致しないまま「OK」と出るゲートを書いた。
    probe_dir = ROOT / "docs" / ".linkprobe"
    probe_dir.mkdir(exist_ok=True)
    probe = probe_dir / "probe.md"
    probe.write_text(
        "[実在しない](./nope.md) と [実在する](./there.md)\n"
        "`[1,80](round(x))` はインラインコードなので数えない\n"
        "```\n[1,80](round(x))\n```\n"
        "[外部](https://example.com/nope) は数えない\n",
        encoding="utf-8")
    (probe_dir / "there.md").write_text("ok\n", encoding="utf-8")
    try:
        cbad, cn = scan(probe)
    finally:
        for f in probe_dir.iterdir():
            f.unlink()
        probe_dir.rmdir()
    if len(cbad) != 1 or cn != 2:
        print(f"NG! 陽性対照が壊れ {len(cbad)} 本 / 検査 {cn} 本"
              f"（期待 1 / 2）= **このゲートは空虚**")
        for c in cbad:
            print("     " + c)
        return 1
    print("陽性対照: 壊れた 1 本を検出し、フェンス・インラインコード・外部は数えない")

    bad, n, files = walk(ROOT)
    print(f"{files} ファイルの相対リンク {n} 本を検査")
    if bad:
        print(f"\nNG! {len(bad)} 本が実在しない:")
        for b in bad:
            print("  " + b)
        return 1
    print("OK  全部実在する")
    print("⚠️ 見ていないもの: **外部 URL**（リリース資産が消えても気づかない）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
