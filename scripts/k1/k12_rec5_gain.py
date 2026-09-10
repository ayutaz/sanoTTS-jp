"""M-108 §5: `rec5` で浮いた枠に entries を増やすと、読みの精度がどれだけ良くなるか。

    uv run python scripts/k1/k12_rec5_gain.py [--grid "438750,538000"]

⚠️ **16 MB の出荷構成で測る**（生 int16 の `matrix` + `char`）。
   `k11_fit_2mb.py` は `matrixc` + `charr` 前提なので、こちらとは別物。

⚠️⚠️ **分母を必ず出す。** M-107 で「`charr` が未知語ノード生成を殺し、
   経路なしの文をゲートが分母から黙って落として精度が良く見えた」事故を踏んでいる。
   **`ホスト既定と一致 N / M` の M が動いていたら、その比較は無効。**

⚠️ **`make` を通してから測る**（古いバイナリで測る事故を M-106 §10 で踏んだ）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import subprocess
import sys
from collections import defaultdict

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO + "/scripts/k1")
sys.path.insert(0, REPO + "/src")

from dump_entries_lib import load_entries          # noqa: E402
import k1_paths                                     # noqa: E402
from saanotts_jp.jdict import (CharProperty, ConnMatrix, DictBlob,  # noqa: E402
                               Entry, UnkDict)

D = pathlib.Path(str(k1_paths.DICT_VENV))
BASE = pathlib.Path(REPO + "/csrc/kanji_e2e_vectors.bin")
BUDGET = 13_828_096          # esp32/partitions_16mb.csv の dict 枠

ap = argparse.ArgumentParser()
ap.add_argument("--grid", default="438750,538000",
                help="entries をカンマ区切りで。既定は 出荷 と rec5 の上限")
ap.add_argument("--rec5", default="both", choices=["both", "on", "off"],
                help="both = 各点で 9 B と rec5 の両方を測る")
a = ap.parse_args()
GRID = [int(x) for x in a.grid.split(",")]

# ⚠️ **必ず make を通す。** 直接叩くと jdict.c を変えた後も古いバイナリが走る（M-106 §10）
subprocess.run(["make", "-s", "-C", REPO + "/csrc", "label_ids_test"], check=True)

raw = load_entries(str(D))
bysurf = defaultdict(list)
for r in raw:
    bysurf[r[0]].append(r)
ranked = json.loads((pathlib.Path(k1_paths.WORK) / "rank_cache.json").read_text())
ranked = [s for s in bysurf if len(s) == 1] + [s for s in ranked if len(s) != 1]
unk = UnkDict.from_unk_dic((D / "unk.dic").read_bytes())
mat = ConnMatrix.from_matrix_bin((D / "matrix.bin").read_bytes())
char = CharProperty.from_char_bin((D / "char.bin").read_bytes())

b = BASE.read_bytes()
p = 8
ndan, = struct.unpack_from("<I", b, p); p += 4
for _ in range(ndan):
    ln, = struct.unpack_from("<H", b, p); p += 2 + ln + 1
old, = struct.unpack_from("<I", b, p)
HEAD, TAIL = b[:p], b[p + 4 + old:]

print("entries\t形式\tblob\t枠 13,828,096\t文一致\t**分母**\t編集距離\t音素%")
denoms: set[str] = set()
for N in GRID:
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= N:
            break
    es = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
          for r in sub]
    modes = ([False, True] if a.rec5 == "both" else
             [True] if a.rec5 == "on" else [False])
    for use5 in modes:
        db = DictBlob.build(es, matrix=mat, char_prop=char, unk=unk)
        db.rec5 = use5
        blob = db.to_bytes()
        pathlib.Path("/tmp/k12.bin").write_bytes(
            HEAD + struct.pack("<I", len(blob)) + blob + TAIL)
        out = subprocess.run([REPO + "/csrc/label_ids_test", "/tmp/k12.bin"],
                             capture_output=True).stdout.decode("utf-8", "replace")
        m1 = re.search(r"ホスト既定と一致 (\d+) / (\d+)", out)
        m2 = re.search(r"音素だけの列\s+編集距離 (\d+) / ホスト長 (\d+) = \*\*([\d.]+)%", out)
        fits = (f"余り {BUDGET - len(blob):,d}" if len(blob) <= BUDGET
                else f"+{len(blob) - BUDGET:,d}")
        if m1:
            denoms.add(m1.group(2))
        print(f"{len(es)}\t{'rec5' if use5 else '9 B'}\t{len(blob)}\t{fits}\t"
              f"{m1.group(1) if m1 else '?'}\t{m1.group(2) if m1 else '?'}\t"
              f"{m2.group(1) if m2 else '?'}\t{m2.group(3) if m2 else '?'}",
              flush=True)

# ⚠️ **分母が動いていたら比較は無効**（M-107）。
print()
if len(denoms) == 1:
    print(f"OK  分母は全点で同じ（{denoms.pop()} 文）= 比較してよい")
else:
    print(f"NG! **分母が動いている**（{sorted(denoms)}）"
          " — 経路なしの文がゲートから落ちている疑い。**この比較は無効**（M-107 と同じ形）")
    raise SystemExit(1)
