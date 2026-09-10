#!/usr/bin/env python3
"""リリースノートの資産表（名前 / サイズ / SHA-256 の頭）を**実物と突き合わせる**。

    uv run --no-project python scripts/check_release_table.py \
        --notes docs/release-notes/v1.0.0.md --dir <資産を置いたディレクトリ>
    uv run --no-project python scripts/check_release_table.py --self-test

⚠️ **CI では回らない**（資産が git 管理外。`scripts/check_ci_coverage.py` に理由つきで登録）。
**タグを打つ直前に手で回すこと。**

## なぜ要るのか — **実際に 3 行ずれていた**

`docs/release-notes/v1.0.0.md` の表は 28 本の**サイズと SHA-256 の頭 16 桁**を載せている。
資産を作った直後に書いたので当時は正しかったが、その後

- `LICENSE-MODEL.md` を直した（[C-084](../docs/decisions.md#c-084) の引用の強調）
- `MODEL_CARD.md` を直した（[C-086](../docs/decisions.md#c-086) の版）
- `saanotts-jp-v4-samples.zip` を作り直した（中の `NOTICE.txt` を [C-081](../docs/decisions.md#c-081) で差し替え）

で **3 行が実物と食い違った。** ⚠️ **読者は表の SHA で「落とした物が正しいか」を確かめる。**
表がずれていると、**正しい資産を「壊れている」と判断させる。**

## 何を見ないか

- **表に無い資産**（`SHA256SUMS.txt` は自分自身の行を持たないので、表にも無いのが正しい）
- **中身が正しいか。** 「そのモデルの説明か」は機械では見られない（C-086 はそれで起きた）
- **リリースに実在するか。** それは `scripts/check_release_assets.py`（名前だけ・要ネットワーク）
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAME = re.compile(r"^\|\s*`([^`]+\.(?:bin|pt|zip|txt|md))`\s*\|")
SHA16 = re.compile(r"`([0-9a-f]{16})`")
SIZE = re.compile(r"\|\s*([\d,]+)\s*B\s*\|")


def check(notes: str, files: dict[str, bytes]) -> tuple[list[str], int]:
    """(食い違いの一覧, 照合できた本数)。`files` は 資産名 → 中身。"""
    bad: list[str] = []
    seen: set[str] = set()
    for line in notes.splitlines():
        m = NAME.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in seen or name not in files:
            # ⚠️ 表には**別の版の資産**も出る（v3 の名前など）。手元に無いものは飛ばす。
            continue
        seen.add(name)
        raw = files[name]
        real = hashlib.sha256(raw).hexdigest()
        probs = []
        shas = SHA16.findall(line)
        if shas and not any(real.startswith(s) for s in shas):
            probs.append(f"SHA-256 の記載 {shas} → 実体 {real[:16]}")
        sz = SIZE.search(line)
        if sz and int(sz.group(1).replace(",", "")) != len(raw):
            probs.append(f"サイズの記載 {sz.group(1)} B → 実体 {len(raw):,} B")
        if probs:
            bad.append(f"{name}: " + " / ".join(probs))
    return bad, len(seen)


def self_test() -> int:
    """陽性対照。**ずれを本当に捕まえるか。**"""
    with tempfile.TemporaryDirectory() as d:
        body = b"x" * 10
        real = hashlib.sha256(body).hexdigest()
        cases = [
            ("無改変（陰性対照）",
             f"| `a.bin` | 10 B | `{real[:16]}` | 中身 |", 0),
            ("SHA が 1 文字違う",
             f"| `a.bin` | 10 B | `{real[:15]}0` | 中身 |", 1),
            ("サイズが違う",
             f"| `a.bin` | 11 B | `{real[:16]}` | 中身 |", 1),
            ("両方違う",
             f"| `a.bin` | 11 B | `{real[:15]}0` | 中身 |", 1),
            # ⚠️ **表に無い資産で「0 件」を取らせない**: 名前が違えば照合本数が 0 になる。
            ("名前が表に無い（照合 0 本 = 空虚）",
             f"| `b.bin` | 10 B | `{real[:16]}` | 中身 |", 0),
        ]
        print("陽性対照:")
        ng = 0
        for label, row, want in cases:
            got, n = check(row, {"a.bin": body})
            mark = "OK " if len(got) == want else "NG!"
            if len(got) != want:
                ng += 1
            note = f"食い違い {len(got)}（期待 {want}）/ 照合 {n} 本"
            print(f"  {mark} {label} → {note}")
            if label.startswith("名前が表に無い") and n != 0:
                print("      NG! 照合 0 本にならない"); ng += 1
        # ⚠️ 「照合 0 本」は OK ではない。本体側で必ず落とす（下記 main）
        print(f"\n{'OK' if not ng else 'NG!'} 陽性対照 {len(cases)} 件 / 失敗 {ng}")
        return 1 if ng else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", help="リリースノートの md")
    ap.add_argument("--dir", help="資産を置いたディレクトリ")
    ap.add_argument("--self-test", action="store_true", help="陽性対照 5 件")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not (a.notes and a.dir):
        print("NG! --notes と --dir が要る（--self-test だけは単体で回せる）")
        return 2

    notes = pathlib.Path(a.notes).read_text(encoding="utf-8")
    d = pathlib.Path(a.dir)
    if not d.is_dir():
        print(f"NG! {d} が無い")
        return 1
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}
    bad, n = check(notes, files)

    # ⚠️ **「0 件」を無条件に緑にしない。** 名前が 1 つも一致しなければ照合 0 本で
    #    「食い違い 0」が出る（C-028 の形）。
    if n == 0:
        print(f"NG! 表と {d} で名前が 1 つも一致しなかった = **何も照合していない**")
        return 1
    print(f"{a.notes} の表と {d} の {len(files)} 本を突き合わせた（照合できたのは {n} 本）")
    if bad:
        print(f"\nNG! {len(bad)} 本が表と食い違う:")
        for b in bad:
            print("  " + b)
        return 1
    print(f"OK  {n} 本すべてサイズと SHA-256 が表どおり")
    if n < len(files):
        # ⚠️ 表に SHA を書いていない資産があるのは正常（SHA256SUMS.txt）だが、**数を出す**。
        rest = sorted(set(files) - {x for x in files if x})  # 実体は下で出す
        print(f"--  表に SHA の行が無い資産 {len(files) - n} 本"
              f"（`SHA256SUMS.txt` は自分自身の行を持たないので正常）")
    print("⚠️ 見ていないもの: **中身がその版の説明になっているか**（C-086）/ "
          "**リリースに実在するか**（check_release_assets.py）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
