#!/usr/bin/env python3
"""held-out の音素ID列を SAAN 形式で書き出す（C 側の end-to-end 検証用）。

`csrc/int8_e2e_test` が「fp32 経路 vs int8 経路」を **1 文ではなく複数文**で
測るのに使う。1 文（golden）だけだと量子化誤差のばらつきが見えない
（実測で 24 文の波形 SNR は 23.3〜31.3 dB に散る）。

⚠️ **`data/pack` / `data/pack_heldout` は読まない**（D-015 で凍結されている）。
テキストは `data/splits/corpus_heldout.tsv` から読み、
`synthesize_student.py --limit 24` と**同じ選び方**（教師 FT 除外つき）にする。

実行:
    uv run python scripts/export_c_ids.py --limit 24 --out csrc/ids_heldout.bin
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import struct
import sys

import numpy as np

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
from saanotts_jp.vocab import map_ids  # noqa: E402

MAGIC = b"SAAN"
VERSION = 1
NAME_LEN = 64
DT_F32 = 0


def write_saan(path: pathlib.Path, entries: list[tuple[str, np.ndarray]]) -> dict:
    """`scripts/export_c_weights.py` の Writer と同じ v1 形式（fp32 のみ）。"""
    n = len(entries)
    ent_size = NAME_LEN + 4 + 4 + 16 + 8 + 8
    header_bytes = 16 + n * ent_size
    pad = (-header_bytes) % 16
    header_bytes += pad

    blobs, offsets, off = [], [], header_bytes
    for _, arr in entries:
        b = np.ascontiguousarray(arr, dtype=np.float32).tobytes()
        offsets.append(off)
        blobs.append(b)
        off += len(b) + (-len(b)) % 16

    buf = bytearray()
    buf += MAGIC + struct.pack("<III", VERSION, n, header_bytes)
    for (name, arr), o, b in zip(entries, offsets, blobs, strict=True):
        if len(name.encode()) >= NAME_LEN:
            raise ValueError(f"名前が長すぎる: {name}")
        nm = name.encode().ljust(NAME_LEN, b"\0")
        d4 = list(arr.shape) + [0] * (4 - arr.ndim)
        buf += nm + struct.pack("<II4IQQ", DT_F32, arr.ndim, *d4, o, len(b))
    buf += b"\0" * pad
    for b in blobs:
        buf += b + b"\0" * ((-len(b)) % 16)

    path.write_bytes(bytes(buf))
    return {"path": str(path), "n_tensors": n, "bytes": len(buf),
            "sha256": hashlib.sha256(bytes(buf)).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--texts", default="data/splits/corpus_heldout.tsv")
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--out", default="csrc/ids_heldout.bin")
    ap.add_argument("--report", default="csrc/ids_heldout.json")
    args = ap.parse_args()

    import gen_teacher_labels as G
    import kana_g2p as K

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
    excluded = G.load_exclusions()

    rows: list[tuple[str, str]] = []
    for r in csv.reader(open(args.texts), delimiter="\t"):
        if not r or not r[-1] or r[0] == "source":
            continue
        if len(r) >= 3 and r[1] in excluded:
            continue          # B-10: 教師の学習テキストは評価に使わない
        rows.append((r[1] if len(r) >= 3 else f"utt_{len(rows):04d}", r[-1]))
        if args.limit and len(rows) >= args.limit:
            break

    entries: list[tuple[str, np.ndarray]] = []
    index = []
    for k, (uid, text) in enumerate(rows):
        ids = map_ids(G.encode_intermediate(K.text_to_intermediate(text, table), pim))
        entries.append((f"ids.{k:03d}", ids.astype(np.int32).astype(np.float32)))
        index.append({"k": k, "uid": uid, "text": text, "n_ids": int(len(ids))})

    meta = write_saan(pathlib.Path(args.out), entries)
    rep = {"n_utts": len(entries), "texts": args.texts, "limit": args.limit,
           "blob": meta, "utts": index,
           "repro": (f"uv run python scripts/export_c_ids.py --limit {args.limit}"
                     f" --out {args.out}")}
    pathlib.Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"{len(entries)} 文 / {meta['bytes']:,} B  sha256 {meta['sha256'][:16]}…")
    print(f"ids 長: min {min(u['n_ids'] for u in index)} / "
          f"max {max(u['n_ids'] for u in index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
