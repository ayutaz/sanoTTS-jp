"""K-0 G0-2: 使う辞書の同一性を manifest に凍結する。

D-042 で「`.venv` 側の sys.dic を正とする」と決めた。このマシンには 3 リビジョンが
同居していて（C-045）、**取り違えると測定が別物になる**。凍結して照合できるようにする。

    uv run python scripts/k1/k0_freeze_dict.py          # 現在の辞書を凍結
    uv run python scripts/k1/k0_verify_dict.py          # 照合（G0-2 / G0-3）

⚠️ **上書きは明示的に。** 既存の manifest と中身が違うときは `--force` が要る。
辞書が黙って入れ替わったのを「凍結し直した」で流すと C-045 が再発する。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "dict_manifest.json")

FILES = ["sys.dic", "matrix.bin", "char.bin", "unk.dic",
         "left-id.def", "right-id.def", "pos-id.def", "rewrite.def"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sysdic_header(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read(72)
    (magic, version, dtype, lexsize, lsize, rsize,
     dsize, tsize, fsize, _d) = struct.unpack("<10I", raw[:40])
    return dict(lexsize=lexsize, lsize=lsize, rsize=rsize,
                darts_bytes=dsize, token_bytes=tsize, feature_bytes=fsize,
                charset=raw[40:72].split(b"\0")[0].decode())


def describe(dic_dir: str) -> dict:
    out = {"files": {}}
    for n in FILES:
        p = os.path.join(dic_dir, n)
        if not os.path.exists(p):
            continue
        out["files"][n] = {"bytes": os.path.getsize(p), "sha256": sha256(p)}
    out["sys_dic_header"] = sysdic_header(os.path.join(dic_dir, "sys.dic"))
    return out


def resolve_dict_dir() -> str:
    """pyopenjtalk が **実際に引く**辞書。ここが唯一の正。"""
    import pyopenjtalk
    d = pyopenjtalk.OPEN_JTALK_DICT_DIR
    return d.decode() if isinstance(d, bytes) else str(d)


def main() -> int:
    force = "--force" in sys.argv
    dic = resolve_dict_dir()
    print(f"辞書: {dic}")
    desc = describe(dic)

    import pyopenjtalk
    m = {
        "_comment": (
            "K-0 / D-042 で凍結した辞書の同一性。scripts/k1/k0_verify_dict.py が照合する。"
            "このマシンには 3 リビジョンが同居している（C-045）ので、取り違え検出が要る。"),
        "frozen_by": "scripts/k1/k0_freeze_dict.py",
        "decision": "D-042",
        "environment": {
            "pyopenjtalk_version": getattr(pyopenjtalk, "__version__", "unknown"),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        **desc,
    }

    if os.path.exists(MANIFEST) and not force:
        old = json.load(open(MANIFEST, encoding="utf-8"))
        if old.get("files") != m["files"]:
            print("\n⚠️ **既存 manifest と辞書が違う。**")
            for n in FILES:
                a = old.get("files", {}).get(n, {}).get("sha256")
                b = m["files"].get(n, {}).get("sha256")
                if a != b:
                    print(f"    {n:14s} 凍結時 {str(a)[:16]}… / 現在 {str(b)[:16]}…")
            print("\n  辞書が入れ替わったのか、意図した更新なのかを**先に確かめること**。")
            print("  意図した更新なら --force を付けて凍結し直す。")
            return 1
        print("既存 manifest と一致。何もしない。")
        return 0

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"\n凍結した → {MANIFEST}")
    print(f"  lexsize = {desc['sys_dic_header']['lexsize']:,d}")
    print(f"  sys.dic = {desc['files']['sys.dic']['bytes']:,d} B")
    print(f"  sha256  = {desc['files']['sys.dic']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
