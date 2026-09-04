"""K-5 / M-71 の「最長文 100 文」を **train + held-out** から取り出す（`csrc/oj_worst.bin` の素）。

    uv run python scripts/k1/oj_worst_texts.py --out csrc/oj_worst_texts.txt

⚠️ **なぜ要るか。** `csrc/oj_worst.bin` は `make -C csrc oj-heap`（G22〜G24）の前提だが、
**生成規則も producer もリポジトリのどこにも無かった**（M-100 §14）。
`clean` は消すのに作り直せないので、**M-71 の再現コマンドが clean な checkout から
そのまま失敗する**状態だった。ここで経路を復元する。

⚠️ **これは「当時と同じ 100 文」である保証ではない。** 復元が正しいかは、
`make -C csrc oj-heap` が M-71 の **ヒープ最大 97,325 B** を再現するかで判定する。

⚠️ **出力はコーパス本文なので git に入れない**（`.gitignore` / hook が守る）。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from k1_paths import HELDOUT, TRAIN    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=100, help="長い方から何文取るか（M-71 は 100）")
    a = ap.parse_args()

    # ⚠️ **train も入れる。** held-out だけだとラベル最大 211 本 / ヒープ 94,658 B で頭打ちになり、
    #    M-71 の 214 本 / 97,325 B に**届かない**（held-out 全 2,333 文で確かめた）。
    #    train + held-out の長い方から 100 文（92 が train / 8 が held-out）で M-71 が完全再現する。
    # ⚠️ **これは安全側ではなく過小側だった** — worst case を 2,667 B 低く測っていた（C-061）。
    texts = []
    for path in (TRAIN, HELDOUT):
        with open(path, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3:
                    texts.append(p[2])
    # ⚠️ **文字数で並べる**（バイト数ではない）。M-71 は「最長文 98 文字」と記録している。
    texts.sort(key=len, reverse=True)
    pick = texts[:a.n]
    pathlib.Path(a.out).write_text("\n".join(pick) + "\n", encoding="utf-8")
    print(f"train + held-out {len(texts):,d} 文 → 長い方から {len(pick)} 文 → {a.out}")
    print(f"  最長 {len(pick[0])} 文字 / 最短 {len(pick[-1])} 文字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
