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
              "managed_components",   # ESP-IDF Component Registry の取得物（git 管理外）
              # ⚠️ **git 管理外のスクラッチ。** superpowers の SDD が計画のタスク本文を
              #    切り出した `.md` を置く。切り出された断片は元ファイル（docs/ 直下）
              #    からの相対リンクを保持しているので、**別の深さから見ると必ず壊れる**。
              #    これは断片の問題であって docs の問題ではない。
              #    ⚠️ CI は新規 clone なのでこのディレクトリ自体が存在せず、
              #    **手元でだけ落ちていた**（2026-09-09 に踏んだ）。
              ".superpowers")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
INLINE_CODE = re.compile(r"`[^`]*`")
EXTERNAL = ("http://", "https://", "#", "mailto:", "file://")

# ⚠️ **ラベルとアンカーが食い違うリンク**（`[C-085](decisions.md#c-083)`）。
#    上のリンク検査は**ファイルの実在だけ**を見るので、番号の一括置換で
#    **ラベルだけ動いた**形は 1 件も捕まらない。実際に踏んだ（[C-064] / C-085 の作業中に 4 件）。
#    `[M-125](measurements.md#m-124)` のように、範囲の末尾を一括置換で巻き添えにする。
NUMLINK = re.compile(r"\[\**([MDC])-(\d+)\**\]\(([^)]*?)#([mdc])-(\d+)\)")


def scan_anchors(md: pathlib.Path) -> tuple[list[str], int]:
    """(ラベルとアンカーが食い違うリンク, 検査した本数)"""
    bad: list[str] = []
    n = 0
    infence = False
    for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            infence = not infence
            continue
        if infence:
            continue
        # ⚠️ **インラインコードを落とす**（`scan` と同じ理由）。落とさないと
        #    「壊れたリンクの例」を本文に書けなくなる — 実際に C-085 を書いた
        #    直後に、**自分の説明文の例を自分が捕まえた。**
        for m in NUMLINK.finditer(INLINE_CODE.sub("``", line)):
            kind, num, _, akind, anum = m.groups()
            n += 1
            if kind.lower() != akind or int(num) != int(anum):
                bad.append(f"{md.relative_to(ROOT)}:{i}  {m.group(0)}")
    return bad, n


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


def walk(root: pathlib.Path) -> tuple[list[str], int, int, list[str], int]:
    bad: list[str] = []
    abad: list[str] = []
    n = files = an = 0
    for md in sorted(root.rglob("*.md")):
        if any(x in md.parts for x in SKIP_PARTS):
            continue
        b, k = scan(md)
        bad += b
        n += k
        ab, ak = scan_anchors(md)
        abad += ab
        an += ak
        files += 1
    return bad, n, files, abad, an


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

    # ⚠️ **アンカー検査の陽性対照。** ラベルだけずれた 1 本を必ず捕まえること。
    probe_dir.mkdir(exist_ok=True)
    probe.write_text(
        "[M-125](measurements.md#m-124) はラベルとアンカーが違う\n"
        "[M-124](measurements.md#m-124) は合っている\n"
        "[C-83](decisions.md#c-083) は 0 埋めの違いだけなので合っている\n"
        "`[M-7](x.md#m-6)` はインラインコードなので数えない\n"
        "```\n[M-9](x.md#m-8)\n```\n",
        encoding="utf-8")
    try:
        abad, an = scan_anchors(probe)
    finally:
        probe.unlink()
        probe_dir.rmdir()
    if len(abad) != 1 or an != 3:
        print(f"NG! アンカーの陽性対照が {len(abad)} 件 / 検査 {an} 本"
              f"（期待 1 / 3）= **この検査は空虚**")
        for c in abad:
            print("     " + c)
        return 1
    print("陽性対照: ラベルだけずれた 1 本を検出し、0 埋めの差・インラインコード・フェンスは数えない")

    bad, n, files, abad, an = walk(ROOT)
    print(f"{files} ファイルの相対リンク {n} 本 / 番号アンカー {an} 本を検査")
    rc = 0
    if bad:
        print(f"\nNG! {len(bad)} 本が実在しない:")
        for b in bad:
            print("  " + b)
        rc = 1
    else:
        print("OK  全部実在する")
    if abad:
        # ⚠️ **一括置換の巻き添え**（C-064 / C-085）。ラベルとアンカーのどちらが
        #    正しいかは機械には分からないので、**両方を出して人に選ばせる**。
        print(f"\nNG! {len(abad)} 本でラベルとアンカーが食い違う"
              f"（番号の一括置換でラベルだけ動いた形。C-064 / C-085）:")
        for b in abad:
            print("  " + b)
        rc = 1
    else:
        print(f"OK  番号リンク {an} 本すべてラベルとアンカーが一致")
    print("⚠️ 見ていないもの: **外部 URL**（リリース資産が消えても気づかない）/ "
          "**アンカーが実在するか**（食い違いだけを見る）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
