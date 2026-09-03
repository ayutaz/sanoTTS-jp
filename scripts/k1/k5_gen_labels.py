"""K-5: ホストのフルコンテキストラベルを basis として書き出す。

`oj_heap_test` が出す `oj_labels*.txt` と**そのまま `diff` できる形**にする。
これで「ラベルバッファを詰めても出力が変わらない」を、
**自分の前後比較ではなくホストとの一致**で言える。

⚠️ **経路は `oj_heap_test` と同じにする** — `make_label(run_njd_from_mecab(...))`。
K-4 の 4 段は通さない（あれは K-6 で繋ぐ）。ここで `run_frontend(t)` を使うと
経路が 2 本になって、差が「詰めたせい」なのか「経路違い」なのか切り分けられない。

    uv run python scripts/k1/k5_gen_labels.py --vectors csrc/njd_rules_vectors.bin \\
        --out csrc/oj_labels_host.txt
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys


def read_cases(path: pathlib.Path):
    b = path.read_bytes()
    if b[:4] != b"K4B1":
        sys.exit(f"NG: magic が違う: {path}")
    o = 4
    n_cases = struct.unpack_from("<I", b, o)[0]; o += 4

    def rds():
        nonlocal o
        n = struct.unpack_from("<H", b, o)[0]; o += 2
        s = b[o:o + n].decode("utf-8"); o += n
        return s

    for _ in range(n_cases):
        nf = struct.unpack_from("<I", b, o)[0]; o += 4
        feats = [rds() for _ in range(nf)]
        nn = struct.unpack_from("<I", b, o)[0]; o += 4
        for _ in range(nn):
            for _k in range(11):
                rds()
            o += 12
        yield feats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import pyopenjtalk

    lines: list[str] = []
    n_label = 0
    for i, feats in enumerate(read_cases(pathlib.Path(a.vectors))):
        njd = pyopenjtalk.run_njd_from_mecab(feats)
        labels = pyopenjtalk.make_label(njd)
        n_label += len(labels)
        lines.append(f"# case {i} labels={len(labels)}\n")
        lines += [x + "\n" for x in labels]
    pathlib.Path(a.out).write_text("".join(lines), encoding="utf-8")
    print(f"{i + 1} 文 / ラベル {n_label:,d} 本 → {a.out}")
    longest = max((len(x) for x in "".join(lines).splitlines()
                   if not x.startswith("# case")), default=0)
    print(f"最長ラベル {longest} B"
          f"（`MAXBUFLEN` はこれ + 終端より大きくないといけない）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
