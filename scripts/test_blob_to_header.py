#!/usr/bin/env python3
"""`scripts/blob_to_header.py` の回帰（stdlib だけ。CI の docs job で回す）。

    uv run --no-project python scripts/test_blob_to_header.py

何を守るか:
  G1  int8 blob → ヘッダが生成され、宣言した SHA-256 が blob の SHA-256 と一致し、
      バイト列が 1 つも欠けず（0x.., の個数 == blob のサイズ）、`aligned(16)` が付いている
  G2  **陽性対照**: fp32 blob（`.scale` テンソルが無い）は exit 1 で拒否される。
      ここが落ちなければ「fp32 を焼いて PIE が 1 命令も効かない」を防ぐ検査が空虚
  G3  `--allow-fp32` を付けたときだけ fp32 が通る
  G4  blob でないファイル（magic 違い）は dtype "unknown" で拒否される

blob は本物を使わない（git 管理外で CI に無い）。SAAN v1 のヘッダ形式
（scripts/export_c_weights.py の Writer と同じ配置）で最小のものをその場で作る。
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "blob_to_header.py"
NAME_LEN = 64
ENT = NAME_LEN + 4 + 4 + 16 + 8 + 8


def make_blob(names_dtypes: list[tuple[str, int]], payload_each: int = 48) -> bytes:
    """SAAN v1 blob。テンソルは全部 payload_each バイトのダミー。"""
    n = len(names_dtypes)
    header_bytes = 16 + n * ENT
    header_bytes += (-header_bytes) % 16
    buf = bytearray(b"SAAN" + struct.pack("<III", 1, n, header_bytes))
    off = header_bytes
    for name, dt in names_dtypes:
        buf += name.encode().ljust(NAME_LEN, b"\0")
        buf += struct.pack("<II4IQQ", dt, 1, payload_each, 0, 0, 0, off, payload_each)
        off += payload_each
    buf += b"\0" * ((-len(buf)) % 16)
    for i in range(n):
        buf += bytes((i * 7 + k) & 0xFF for k in range(payload_each))
    return bytes(buf)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def main() -> int:
    bad = 0

    def ok(msg: str) -> None:
        print(f"  OK  {msg}")

    def ng(msg: str) -> None:
        nonlocal bad
        bad += 1
        print(f"  NG! {msg}")

    if not SCRIPT.exists():
        ng(f"{SCRIPT.relative_to(ROOT)} が無い")
        print("\nNG: blob_to_header.py が無い")
        return 1

    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        i8 = make_blob([("decoder.hout.weight", 1), ("decoder.hout.weight.scale", 2),
                        ("decoder.hout.bias", 0)])
        f32 = make_blob([("decoder.hout.weight", 0), ("decoder.hout.bias", 0)])
        junk = b"RIFF" + bytes(60)
        (d / "i8.bin").write_bytes(i8)
        (d / "f32.bin").write_bytes(f32)
        (d / "junk.bin").write_bytes(junk)

        # --- G1: int8 → 生成、SHA 一致、バイト数一致、aligned(16)
        r = run(["--blob", str(d / "i8.bin"), "--out", str(d / "i8.h")])
        if r.returncode != 0:
            ng(f"G1 int8 blob で exit {r.returncode}: {r.stderr.strip()}")
        else:
            h = (d / "i8.h").read_text()
            sha = hashlib.sha256(i8).hexdigest()
            m = re.search(r'#define SAAN_MODEL_BLOB_SHA256 "([0-9a-f]{64})"', h)
            if m and m.group(1) == sha:
                ok(f"G1 宣言した SHA-256 が blob と一致 ({sha[:16]}…)")
            else:
                ng(f"G1 SHA-256 が一致しない: ヘッダ {m.group(1)[:16] if m else None}… / blob {sha[:16]}…")
            n_bytes = len(re.findall(r"0x[0-9a-f]{2},", h))
            if n_bytes == len(i8):
                ok(f"G1 バイト列 {n_bytes} 個 == blob {len(i8)} B（欠けなし）")
            else:
                ng(f"G1 バイト列 {n_bytes} 個 != blob {len(i8)} B")
            if "__attribute__((aligned(16)))" in h:
                ok("G1 aligned(16) が付いている（PIE / const float* キャストの前提）")
            else:
                ng("G1 aligned(16) が無い")
            if f"#define SAAN_MODEL_BLOB_BYTES {len(i8)}u" in h and '"int8"' in h:
                ok("G1 BYTES と dtype int8 が宣言されている")
            else:
                ng("G1 BYTES か dtype の宣言が無い")

        # --- G2: 陽性対照 — fp32 は拒否される
        r = run(["--blob", str(d / "f32.bin"), "--out", str(d / "f32.h")])
        if r.returncode == 1 and not (d / "f32.h").exists():
            ok("G2 陽性対照: fp32 blob は exit 1 で拒否され、ヘッダは書かれない")
        else:
            ng(f"G2 陽性対照が効いていない: exit {r.returncode} / ヘッダ {'あり' if (d / 'f32.h').exists() else '無し'}")

        # --- G3: --allow-fp32 で通る
        r = run(["--blob", str(d / "f32.bin"), "--out", str(d / "f32b.h"), "--allow-fp32"])
        if r.returncode == 0 and '"fp32"' in (d / "f32b.h").read_text():
            ok("G3 --allow-fp32 なら fp32 も通り、dtype fp32 と宣言される")
        else:
            ng(f"G3 --allow-fp32 が効かない: exit {r.returncode}")

        # --- G4: blob でないもの
        r = run(["--blob", str(d / "junk.bin"), "--out", str(d / "junk.h")])
        if r.returncode == 1:
            ok("G4 magic が SAAN でないファイルは拒否される")
        else:
            ng(f"G4 blob でないファイルが通った: exit {r.returncode}")

    print()
    print("NG: blob_to_header.py の回帰が落ちた" if bad else
          "OK: blob_to_header.py の回帰 4 件（陽性対照つき）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
