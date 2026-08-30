"""K-1: **本番エンコーダ**で予算に入るエントリ数を二分探索する。

K-0 の `k0_fit_point.py` は研究時のサイズ模型（byte 鍵の LOUDS）だった。
本番エンコーダは文字 ID 鍵で見出し語表も持たないので、実測はそれより小さい。
D-042 のエントリ数を見直す材料を出す。

⚠️ 予算境界での内挿は禁止（C-009）。実際に組んで測る。
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))

from dump_entries_lib import load_entries          # noqa: E402
from k1_paths import TRAIN                         # noqa: E402
from saanotts_jp.k1_dict import DictBlob, Entry    # noqa: E402

import k0_freeze_dict                              # noqa: E402
import pyopenjtalk                                 # noqa: E402

dic = k0_freeze_dict.resolve_dict_dir()
raw = load_entries(dic)
bysurf = defaultdict(list)
for r in raw:
    bysurf[r[0]].append(r)
freq = Counter()
with open(TRAIN, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            for ft in pyopenjtalk.run_mecab_detailed(p[2])[0]:
                s = ft.split(",", 1)[0]
                if s in bysurf:
                    freq[s] += 1
ranked = [s for s, _ in freq.most_common()]
seen = set(ranked)
ranked += sorted((s for s in bysurf if s not in seen),
                 key=lambda s: (min(e[3] for e in bysurf[s]), len(s)))
print(f"辞書 {dic}\n  {len(raw):,d} entries / {len(ranked):,d} 見出し語")


def size_of(target: int) -> tuple[int, int, int]:
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= target:
            break
    es = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
          for r in sub]
    b = DictBlob.build(es).to_bytes()
    return len(b), len(es), len(set(e.surface for e in es))


BUDGETS = [("16 MB / A（OTA 無し）← D-042", 13_828_096),
           ("16 MB / B（OTA2）", 11_730_944)]
print(f"\n{'予算':30s} {'B':>12s} {'入る entries':>13s} {'見出し語':>10s} "
      f"{'実サイズ':>12s} {'余り':>10s}")
for name, budget in BUDGETS:
    lo, hi, best = 100_000, 800_000, None
    while lo <= hi:
        mid = (lo + hi) // 2
        sz, ne, ns = size_of(mid)
        if sz <= budget:
            best = (ne, ns, sz); lo = mid + 10_000
        else:
            hi = mid - 10_000
    if best:
        ne, ns, sz = best
        print(f"{name:30s} {budget:>12,d} {ne:>13,d} {ns:>10,d} {sz:>12,d} "
              f"{budget-sz:>10,d}")
print("""
⚠️ 精度は未測定。B-0 の実測点で挟むこと（400,000 → 音素 95.53% / アクセント 89.29%）。
⚠️ 全 789,388 entries は 16,727,469 B = 15.95 MiB で、16 MB / A の予算には入らない。""")
