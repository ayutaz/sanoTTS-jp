"""K-4b: Open JTalk の NJD 系ソースを `csrc/openjtalk/` に取り込む。

⚠️ **取り込み元は「ホストで実際に動いている実装」でなければならない。**
本プロジェクトのラベル生成は **pyopenjtalk-plus**（`pyopenjtalk` のフォーク）を
使っている。素の `pyopenjtalk` から取ると **22 / 34 ファイルが食い違い**、
`njd_set_accent_phrase` の Rule 13 などが抜けて G14a が落ちる（C-048）。

    uv run python scripts/k1/k4b_vendor.py --sdist <pyopenjtalk_plus-*.tar.gz>
    uv run python scripts/k1/k4b_vendor.py --src <展開済み>/lib/open_jtalk/src

`--check` を付けると取り込まず、**いま置いてあるものが上流と一致するか**だけ見る。

⚠️ **改変は `PATCHES` に列挙したものだけ。** `--check` は「上流 + PATCHES」と
突き合わせるので、**表に無い改変は落ちる**。改変を足したら
`make -C csrc k4b` と `make -C csrc k5` を通し直すこと。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
DEST = HERE.parent.parent / "csrc/openjtalk"

# 取り込むモジュール（ディレクトリ名）。`mecab` は含めない — K-1/K-2 が置き換える。
MODULES = [
    "text2mecab", "mecab2njd", "njd",
    "njd_set_pronunciation", "njd_set_digit", "njd_set_accent_phrase",
    "njd_set_accent_type", "njd_set_unvoiced_vowel", "njd_set_long_vowel",
    "njd2jpcommon", "jpcommon",
]

# 文字コード別ヘッダのうち UTF-8 以外は取らない。
SKIP_MARKS = ("_shift_jis", "_euc_jp", "_ascii")

# ⚠️ **取り込んだコードへの改変は、ここに列挙したものだけ。**
# `--check` は「上流 + この表」と突き合わせるので、**表に無い改変は落ちる**。
# 改変を足したら **必ず `make -C csrc k4b` と `make -C csrc k5` を通し直す**。
PATCHES = [
    (
        "jpcommon_label.c",
        "#define MAXBUFLEN 1024",
        "#define MAXBUFLEN 256",
        "K-5: フルコンテキストラベル 1 本あたりの固定バッファ。"
        "1 文で最大 214 本確保するので、1024 だと 219,136 B を占めて"
        "**1 文ピークの 83.7%** になる。実測の最長ラベルは **166 B**"
        "（35,097 本。うち最長文 100 文を含む）。溢れても上流が snprintf で"
        "打ち切り、`Label buffer exceeded` を stderr に出すので**黙って壊れない**。",
    ),
]


def _wanted(name: str) -> bool:
    if not name.endswith((".c", ".h")):
        return False
    return not any(m in name for m in SKIP_MARKS)


def collect(src: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for mod in MODULES:
        d = src / mod
        if not d.is_dir():
            sys.exit(f"NG: モジュールが無い: {d}")
        files += sorted(p for p in d.iterdir() if p.is_file() and _wanted(p.name))
    cop = src / "COPYING"
    if not cop.is_file():
        sys.exit(f"NG: COPYING が無い: {cop}")
    return files + [cop]


def patched(p: pathlib.Path) -> bytes:
    """上流のバイト列に PATCHES を当てたもの。**当たらなければ落とす** —
    上流が変わって置換が空振りしたのを黙って見逃すと、詰めたつもりで
    1024 のまま動く（そして誰も気づかない）。"""
    b = p.read_bytes()
    for name, old, new, _why in PATCHES:
        if p.name != name:
            continue
        o, n = old.encode(), new.encode()
        if b.count(o) != 1:
            sys.exit(f"NG: {name} に `{old}` が {b.count(o)} 箇所。"
                     f"上流が変わった可能性がある")
        b = b.replace(o, n)
    return b


def digest(files: list[pathlib.Path]) -> str:
    """全ファイルを名前順に連結した SHA-256。取り込みの同一性を 1 個の値で示す。"""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda q: q.name):
        h.update(patched(p))
    return h.hexdigest()


def write_provenance(dist: str, files: list[pathlib.Path], sha: str) -> None:
    n_src = len([p for p in files if p.name != "COPYING"])
    lines = [p.read_text(encoding="utf-8", errors="replace").count("\n")
             for p in files if p.name != "COPYING"]
    plist = "\n".join(
        f"| `{n}` | `{o}` → `{w}` | {y} |" for n, o, w, y in PATCHES) or \
        "| — | — | 改変なし |"
    body = f"""# csrc/openjtalk — 取り込んだ第三者コード

**改変は下表の {len(PATCHES)} 件だけ。** それ以外は上流のまま。
`scripts/k1/k4b_vendor.py --check` が「上流 + 下表」と突き合わせるので、
**表に無い改変は落ちる**。

## 当てている改変

| ファイル | 置換 | なぜ |
|---|---|---|
{plist}

| | |
|---|---|
| 出所 | Open JTalk（HTS Working Group / Nagoya Institute of Technology） |
| 取得元 | **{dist}** の sdist 同梱（`lib/open_jtalk/src`） |
| ライセンス | **修正 BSD**（[`COPYING`](COPYING)。Copyright (c) 2008-2016） |
| ファイル数 | {n_src}（+ COPYING） |
| 行数 | {sum(lines):,d} |
| 全ファイル連結の SHA-256 | `{sha}` |

⚠️ **取得元は素の `pyopenjtalk` ではなく `pyopenjtalk-plus`。**
本プロジェクトのホスト側（ラベル生成・検証ベクタ）が動かしているのがこちらで、
**素の pyopenjtalk 0.4.1 とは 22 / 34 ファイルが食い違う**（C-048）。
`njd_set_accent_phrase.c` の Rule 13 のように**規則そのものが違う**ので、
取り違えると G14a が落ちる。

⚠️ **GPL-3.0 の `Ampixa/sanoTTS` とは別物**。D-032 の凍結対象ではない。

## 取り込んだモジュール

{" / ".join(f"`{m}`" for m in MODULES)}

## 取り込まなかったもの

**文字コード別のヘッダ**（`*_shift_jis.h` / `*_euc_jp.h` / `*_ascii*.h`）。
本プロジェクトは UTF-8 だけを扱う。`mecab/` も取らない（K-1 / K-2 が置き換える）。

## 改変の方針

⚠️ **勝手に改変しない。** 改変すると「ホストと一致するか」の基準が
自分の改変に依存してしまう（K-4b の G14a）。変える必要が出たら
`k4b_vendor.py` の `PATCHES` に**理由つきで**足すこと。
`--check` が「上流 + PATCHES」と突き合わせるので、表に無い改変は落ちる。

## 更新のしかた

```bash
uv run python scripts/k1/k4b_vendor.py --sdist pyopenjtalk_plus-<ver>.tar.gz
make -C csrc k4b        # ⚠️ **必ず G14a を通し直す**
```
"""
    (DEST / "PROVENANCE.md").write_text(body, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdist", help="pyopenjtalk_plus-*.tar.gz")
    ap.add_argument("--src", help="展開済みの lib/open_jtalk/src")
    ap.add_argument("--check", action="store_true",
                    help="取り込まず、いまの内容が上流と一致するかだけ見る")
    a = ap.parse_args()
    if not a.sdist and not a.src:
        sys.exit("NG: --sdist か --src のどちらかが要る")

    tmp = None
    if a.sdist:
        tmp = tempfile.mkdtemp()
        with tarfile.open(a.sdist) as tf:
            tf.extractall(tmp, filter="data")
        cands = list(pathlib.Path(tmp).glob("*/lib/open_jtalk/src"))
        if len(cands) != 1:
            sys.exit(f"NG: lib/open_jtalk/src が {len(cands)} 個見つかった")
        src, dist = cands[0], pathlib.Path(a.sdist).name.replace(".tar.gz", "")
    else:
        src = pathlib.Path(a.src).resolve()
        dist = f"{src.parents[2].name}（--src 指定）"

    files = collect(src)
    sha = digest(files)
    print(f"上流 {src}")
    print(f"  ファイル {len(files)} / 連結 SHA-256 {sha}")

    if a.check:
        bad = 0
        for p in files:
            q = DEST / p.name
            if not q.is_file():
                print(f"  NG  欠落   {p.name}"); bad += 1
            elif q.read_bytes() != patched(p):
                print(f"  NG  食違い {p.name}"); bad += 1
        extra = sorted(q.name for q in DEST.iterdir()
                       if q.suffix in (".c", ".h") and q.name not in {p.name for p in files})
        for e in extra:
            print(f"  NG  余分   {e}"); bad += 1
        print("OK  上流と一致" if bad == 0 else f"NG  {bad} 件ずれている")
        return 1 if bad else 0

    for p in files:
        (DEST / p.name).write_bytes(patched(p))
    write_provenance(dist, files, sha)
    print(f"取り込んだ → {DEST}")
    print("⚠️ **`make -C csrc k4b` を通し直すこと**")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
