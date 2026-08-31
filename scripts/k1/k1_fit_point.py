"""K-1: **本番エンコーダ**で予算に入るエントリ数を二分探索する。

⚠️ **辞書本体だけでは足りない。** 端末に置くイメージには接続行列 (3.79 MB) と
`char.bin` (262 KB) と `unk.dic` も入る。これを忘れると **C-047** を踏む
（C-046 で「630,000 entries 入る」と報告したが、行列を数えていなかった）。

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
from saanotts_jp.k1_dict import (CharProperty, ConnMatrix,  # noqa: E402
                                 DictBlob, Entry, UnkDict)

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


_D = pathlib.Path(dic)
MATRIX = ConnMatrix.from_matrix_bin((_D / "matrix.bin").read_bytes())
CHARP = CharProperty.from_char_bin((_D / "char.bin").read_bytes())
UNK = UnkDict.from_unk_dic((_D / "unk.dic").read_bytes())


def size_of(target: int) -> tuple[int, int, int]:
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= target:
            break
    es = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
          for r in sub]
    # ⚠️ **端末に置くものを全部入れて測る**（行列 / char / unk）。C-047。
    b = DictBlob.build(es, matrix=MATRIX, char_prop=CHARP, unk=UNK).to_bytes()
    return len(b), len(es), len(set(e.surface for e in es))


# K-5: 接続行列を**行ごとアフィン uint8** にすると 1,885,113 B 浮く。
# ⚠️ **ただの値引きではない。** MeCab との一致が 1,696 文中 3 文（0.18%）落ちる
#    実測がある（M-72）。予算を増やす代わりに解析が変わる、という取引。
MATRIX_SAVING = 1_885_113

BUDGETS = [("16 MB / A（OTA 無し）← D-042", 13_828_096),
           ("16 MB / B（OTA2）", 11_730_944)]
print(f"\n{'予算':30s} {'B':>12s} {'入る entries':>13s} {'見出し語':>10s} "
      f"{'実サイズ':>12s} {'余り':>10s}")
rows = []
for name, budget in [(n, b) for n, b in BUDGETS] + \
                    [(n + " + 行列 uint8", b + MATRIX_SAVING) for n, b in BUDGETS]:
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
⚠️ **接続行列 (3.79 MB) と char.bin (262 KB) と unk.dic を含めた値**。
   辞書本体だけで測ると 1.6 倍ほど多く入るように見える（C-047 で踏んだ）。
⚠️ **「+ 行列 uint8」の行はタダではない。** 行ごとアフィン uint8 に落とすと
   MeCab との一致が 1,696 文中 3 文（0.18%）落ちる（M-72）。
   **エントリを増やして得る分と、行列を粗くして失う分は別の話。**""")
