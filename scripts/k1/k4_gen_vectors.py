"""K-4: アクセント規則 4 段の C 移植を検証するベクタを作る。

移植対象（K-1 §5-2 の実測）— どれも NJD ノード列だけを見る純関数:

    modify_filler_accent                       21 行 / 外部定数なし / LOO 84 文
    suppress_unnatural_auxiliary_u_long_vowel  27 行 / _DAN_MAP 76 件 / LOO 0 文
    retreat_acc_nuc                            36 行 / 外部定数なし / LOO 214 文
    modify_acc_after_chaining                  42 行 / 外部定数なし / LOO 204 文

出力（既定 `csrc/k4_vectors.bin`）:

    magic "K4V1" u32 / n_cases u32 / n_stages u32
    _DAN_MAP: n u32 →（かな 1 文字 UTF-8 / 母音 1 B）× n
    ケース × n:
        n_nodes u32
        入力ノード × n:  pos, ctype, cform, orig, pron, read（u16 長 + UTF-8）
                        acc i32 / mora_size i32 / chain_flag i32
        期待出力（全 4 段）:      (acc i32, chain_flag i32, pron) × n
        期待出力（k 段を抜く）×4: 同上

⚠️ **これらの関数はノードを破壊的に書き換える。** 段ごとに入力を deepcopy して
作らないと、期待値が互いに汚染される。
⚠️ **`suppress_u_long` はこのコーパスで 1 文も動かさない**（LOO 0）。
   その段の陰性対照は**空虚になる**ので、そう報告する（PASS にしない）。
"""
from __future__ import annotations

import argparse
import copy
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from k1_paths import HELDOUT  # noqa: E402

STAGE_NAMES = ["modify_filler_accent",
               "suppress_unnatural_auxiliary_u_long_vowel",
               "retreat_acc_nuc",
               "modify_acc_after_chaining"]

FIELDS = ["pos", "ctype", "cform", "orig", "pron", "read"]


def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=600)
    ap.add_argument("--out", default=str(HERE.parent.parent / "csrc/k4_vectors.bin"))
    a = ap.parse_args()

    import pyopenjtalk
    from pyopenjtalk import utils as pjt_utils
    stages = [getattr(pyopenjtalk, n) for n in STAGE_NAMES]
    dan = pjt_utils._DAN_MAP
    print(f"_DAN_MAP {len(dan)} 件 / 段 {len(stages)}")

    texts = []
    with open(HELDOUT, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                texts.append(p[2])
    texts = [texts[int(i * len(texts) / a.cases)] for i in range(a.cases)]

    def run(nodes, skip=None):
        """4 段を順に適用する。skip を指定するとその段だけ飛ばす。"""
        cur = copy.deepcopy(nodes)
        for i, fn in enumerate(stages):
            if i == skip:
                continue
            cur = fn(cur)
        return cur

    cases = []
    n_changed = [0] * len(stages)
    for t in texts:
        try:
            # ⚠️ 4 段が適用される **前** の NJD が要る。use_vanilla=True で素の C 実装を得る
            base = pyopenjtalk.run_frontend(t, use_vanilla=True)
        except Exception:
            continue
        if not base:
            continue
        full = run(base)
        omits = [run(base, skip=k) for k in range(len(stages))]
        for k in range(len(stages)):
            if [(x["acc"], x["chain_flag"], x["pron"]) for x in omits[k]] != \
               [(x["acc"], x["chain_flag"], x["pron"]) for x in full]:
                n_changed[k] += 1
        cases.append((copy.deepcopy(base), full, omits))

    print(f"ケース {len(cases)}")
    print("段ごとに『抜くと結果が変わる』文の数（= 陰性対照の強さ）:")
    for n, c in zip(STAGE_NAMES, n_changed):
        mark = "" if c else "   ⚠️ **このコーパスでは効かない。陰性対照は空虚**"
        print(f"  {n:45s} {c:>5d} 文{mark}")

    out = bytearray(struct.pack("<4sII", b"K4V1", len(cases), len(stages)))
    out += struct.pack("<I", len(dan))
    for k, v in sorted(dan.items()):
        out += _pack_str(k) + v.encode("ascii")[:1]
    for base, full, omits in cases:
        out += struct.pack("<I", len(base))
        for nd in base:
            for f in FIELDS:
                out += _pack_str(str(nd[f]))
            out += struct.pack("<iii", int(nd["acc"]), int(nd["mora_size"]),
                               int(nd["chain_flag"]))
        for group in [full] + omits:
            for nd in group:
                out += struct.pack("<ii", int(nd["acc"]), int(nd["chain_flag"]))
                out += _pack_str(str(nd["pron"]))
    pathlib.Path(a.out).write_bytes(bytes(out))
    print(f"書き出した → {a.out} ({len(out):,d} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
