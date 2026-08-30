"""測定機構そのものの対照実験。

陽性対照 P: 戦略 A 自身の分割をそのまま「戦略」として流す → 全指標が完全一致するはず。
            ここが 1.0 にならないなら、指標が「一致」を検出できていない。
陰性対照 N: 1 文字ずつ切るだけの戦略 → 全指標が大きく崩れるはず。
            ここが崩れないなら、指標が「不一致」を検出できていない。
陰性対照 W: 別の辞書 (piper-plus 側 entries.tsv) を使う → G-DIC が落ちるはず。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import os
import sys

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import measure as M  # noqa: E402
import pyopenjtalk  # noqa: E402

DIC, MAXLEN, NROWS, _ = D.load_dic()
rows = []
with open(M.CORPUS, encoding="utf-8") as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            rows.append(p[2])
N = 400
rows = rows[:N]


def seg_oracle(text, morphs):
    return [(m["char_span"][0], m["char_span"][1], ",".join(m["features"]),
             m["is_unknown"]) for m in morphs]


def seg_char(text, morphs):
    out = []
    for i, ch in enumerate(text):
        e = DIC.get(ch)
        if e:
            ent = min(e, key=lambda x: x[3])
            out.append((i, i + 1, ch + "," + ent[4], False))
        else:
            out.append((i, i + 1, ch + "," + D.UNK_FEATURE.format(surf=ch), True))
    return out


def run(name, segfn):
    ap_ok = ph_ok = pron_ok = 0
    ap_ed = ap_n = ph_ed = ph_n = 0
    for text in rows:
        feats_a, morphs_a = pyopenjtalk.run_mecab_detailed(text)
        njd_a = pyopenjtalk.apply_postprocessing(
            text, pyopenjtalk.run_njd_from_mecab(feats_a))
        ap_a = M.accent_phrases(njd_a)
        lab_a = pyopenjtalk.make_label(njd_a)
        ph_a = [l.split("-", 1)[1].split("+", 1)[0] for l in lab_a]
        pron_a = "".join(M.pron_of(f.split(",")) for f in feats_a)

        seg = segfn(text, morphs_a)
        feats_b = [f for _, _, f, _ in seg if not M.is_space_symbol(f.split(","))]
        njd_b = pyopenjtalk.apply_postprocessing(
            text, pyopenjtalk.run_njd_from_mecab(feats_b))
        ap_b = M.accent_phrases(njd_b)
        lab_b = pyopenjtalk.make_label(njd_b)
        ph_b = [l.split("-", 1)[1].split("+", 1)[0] for l in lab_b]
        pron_b = "".join(M.pron_of(f.split(",")) for f in feats_b)

        ap_ok += ap_a == ap_b
        ph_ok += ph_a == ph_b
        pron_ok += pron_a == pron_b
        ap_ed += M.levenshtein(ap_a, ap_b)[0]
        ap_n += len(ap_a)
        ph_ed += M.levenshtein(ph_a, ph_b)[0]
        ph_n += len(ph_a)
    print(f"{name:28s} n={len(rows)}  pron_exact={pron_ok / len(rows):.4f}  "
          f"phoneme_exact={ph_ok / len(rows):.4f}  PER={ph_ed / ph_n:.4f}  "
          f"accphrase_exact={ap_ok / len(rows):.4f}  accphrase_ER={ap_ed / ap_n:.4f}")
    return ap_ok / len(rows)


print("# 対照実験（先頭 %d 文）" % N)
p = run("P 陽性対照 (A 自身)", seg_oracle)
n = run("N 陰性対照 (1 文字ずつ)", seg_char)
for m in M.MODES:
    run("  " + m, lambda t, mo, m=m: D.segment(t, DIC, MAXLEN, m))
assert p == 1.0, "陽性対照が 1.0 にならない = 指標が一致を検出できていない"
assert n < 0.20, "陰性対照が崩れない = 指標が不一致を検出できていない"
print("PASS: 陽性対照 1.0000 / 陰性対照 %.4f" % n)

# --- 陰性対照 W: 別辞書だと G-DIC が落ちること -----------------------------
old = os.path.join(_WORK, "entries.tsv")
if os.path.exists(old):
    DIC2, _, NR2, _ = D.load_dic(old)
    hit = tot = 0
    for text in rows[:300]:
        _, morphs = pyopenjtalk.run_mecab_detailed(text)
        for m in morphs:
            if m["is_unknown"]:
                continue
            tot += 1
            fs = m["features"]
            e = DIC2.get(fs[0])
            if e and any(x[4] == ",".join(fs[1:]) for x in e):
                hit += 1
    print(f"W 陰性対照: piper-plus 側 entries.tsv (rows={NR2}) で G-DIC = {hit}/{tot} "
          f"({hit / tot:.4f})  ← 1.0000 にならないことが、辞書取り違えを検出できる証拠")
    assert hit < tot, "別辞書でも G-DIC が満点 = ゲートが辞書の取り違えを検出できていない"
