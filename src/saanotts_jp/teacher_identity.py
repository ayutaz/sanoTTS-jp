"""教師の同一性を固定する（vast.ai で「別の教師」を掴まないための保険）。

**ローカルと vast.ai で piper-plus のバージョンが違えば、ラベルは別物になる。**
`pyproject.toml` の path 依存はローカル絶対パスなので、リモートでは
別の clone を掴むことになる。そこで**コミットと主要ソースの SHA-256 を固定**し、
教師を触る前に照合する。

D-015 で「ラベルは一度だけ生成し SHA-256 で固定する」と決めたが、
**その前段（教師のコードが同じか）を検証する手段が無かった**。ここがそれ。

使い方:
    from saanotts_jp.teacher_identity import verify
    verify()          # 不一致なら SystemExit
    verify(strict=False)  # 差分を返すだけ
"""

from __future__ import annotations

import hashlib
import os
import pathlib

# 2026-08-27 時点のローカル checkout。origin/dev に push 済み。
PIPER_PLUS_REPO = "https://github.com/ayutaz/piper-plus.git"
PIPER_PLUS_COMMIT = "0f3b1a62fa3b9a323c92ad709288fd80b42ff18f"

# 教師ラベルの値に直接効くファイルだけを載せる（テストや docs は載せない）
SOURCE_SHA256: dict[str, str] = {
    "src/python/piper_train/vits/models.py":
        "af0df56e9159016d9bbf52194ac33e882ce29fe13e9fcf6150b1f121e93671dc",
    "src/python/piper_train/vits/mb_istft.py":
        "526849038a4859b525cd63c94ed2952c69d5582890d25b4936463361d556fa7e",
    "src/python/piper_train/vits/commons.py":
        "d5f8c29b703d61293b4967296e14f2c289357e10729df50f607c7ec2a2bed1f2",
    "src/python/piper_train/vits/modules.py":
        "95ff61d67da12c66a777295768ab4ce36b2119e9863c7ffd737669d646c1128f",
    "src/python/piper_train/vits/attentions.py":
        "3c8891341f5e7195a0b8b8073b8620ee3ced684f55072529f00d1d74e2f52808",
    "src/python/piper_train/export_onnx.py":
        "8c22527cadfa1759b3ce7da37d56fc8266505731569281f0f3bf5c7ceca46138",
    "src/python/g2p/piper_plus_g2p/encode/pua.py":
        "015459c8bdff22084fb30c35e5075f07e7fb934a742334a317c29a72011ab80e",
}

DEFAULT_ROOT = os.path.expanduser("~/Documents/piper-plus")


def piper_plus_root() -> pathlib.Path:
    """`PIPER_PLUS_ROOT` があればそれを、無ければローカルの既定パスを返す。"""
    return pathlib.Path(os.environ.get("PIPER_PLUS_ROOT", DEFAULT_ROOT))


def check(root: pathlib.Path | None = None) -> dict[str, tuple[str, str]]:
    """一致しないファイルを `{path: (expected, actual)}` で返す。空なら一致。"""
    root = root or piper_plus_root()
    bad: dict[str, tuple[str, str]] = {}
    for rel, want in SOURCE_SHA256.items():
        p = root / rel
        got = (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists()
               else "<missing>")
        if got != want:
            bad[rel] = (want, got)
    return bad


def verify(root: pathlib.Path | None = None, strict: bool = True) -> dict:
    root = root or piper_plus_root()
    bad = check(root)
    if bad and strict:
        lines = [f"教師のソースがピン留めと一致しない（root={root}）:"]
        lines += [f"  {k}\n    expected {v[0][:16]}…\n    actual   {v[1][:16]}…"
                  for k, v in bad.items()]
        lines.append(f"期待するコミット: {PIPER_PLUS_COMMIT}")
        lines.append("ラベルが別物になる。**このまま本番生成をしないこと。**")
        raise SystemExit("\n".join(lines))
    return bad
