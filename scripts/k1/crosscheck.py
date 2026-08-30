"""ゲート: 自作 saan_probe が pyopenjtalk と同じフロントエンドを回しているか。

これが通らなければ Q2/Q3/Q4 の数値はすべて無効。
陽性対照（わざと壊すと落ちる）と陰性対照（正しいものを巻き込まない）を同居させる。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import sys

SP = (_WORK + "")

import pyopenjtalk  # noqa: E402

texts = open(os.path.join(_WORK, "heldout_text.txt"), encoding="utf-8").read().splitlines()

hdr = open(os.path.join(_WORK, "probe_venv.tsv"), encoding="utf-8").readline().rstrip("\n").split("\t")
i_lab = hdr.index("labels")
i_njd = hdr.index("njd_nodes")
probe_lab, probe_njd = [], []
with open(os.path.join(_WORK, "probe_venv.tsv"), encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        probe_lab.append(int(p[i_lab]))
        probe_njd.append(int(p[i_njd]))

assert len(probe_lab) == len(texts), f"行数不一致 {len(probe_lab)} vs {len(texts)}"

py_lab, py_njd = [], []
for t in texts:
    py_lab.append(len(pyopenjtalk.extract_fullcontext(t)))
    py_njd.append(len(pyopenjtalk.run_frontend(t)))

n = len(texts)
mis_lab = [i for i in range(n) if py_lab[i] != probe_lab[i]]
mis_njd = [i for i in range(n) if py_njd[i] != probe_njd[i]]

print(f"n = {n} 文")
print(f"ラベル数一致  : {n - len(mis_lab)}/{n} = {(n-len(mis_lab))/n:.6f}")
print(f"NJD ノード数一致: {n - len(mis_njd)}/{n} = {(n-len(mis_njd))/n:.6f}")
for i in mis_lab[:5]:
    print(f"  ラベル不一致 idx={i} py={py_lab[i]} probe={probe_lab[i]}  {texts[i][:40]}")
for i in mis_njd[:5]:
    print(f"  NJD 不一致 idx={i} py={py_njd[i]} probe={probe_njd[i]}  {texts[i][:40]}")

# --- 陽性対照: 1 文だけずらすと必ず不一致が出ること ---------------------------
shifted = probe_lab[1:] + probe_lab[:1]
mis_shift = sum(1 for a, b in zip(py_lab, shifted) if a != b)
print(f"\n陽性対照 (probe を 1 行ずらす): 不一致 {mis_shift}/{n}  "
      f"{'PASS' if mis_shift > n * 0.5 else 'FAIL — 検査が空虚'}")

# --- 陽性対照 2: 意図的に 1 件だけ壊す -----------------------------------------
broken = list(probe_lab)
broken[7] += 1
mis_broken = sum(1 for a, b in zip(py_lab, broken) if a != b)
print(f"陽性対照 (1 件だけ +1): 不一致 {mis_broken}/{n}  "
      f"{'PASS' if mis_broken == len(mis_lab) + 1 or mis_broken >= 1 else 'FAIL'}")

# --- 陰性対照: 同じ列を自分自身と比べたら 0 件 ---------------------------------
mis_self = sum(1 for a, b in zip(probe_lab, probe_lab) if a != b)
print(f"陰性対照 (自分自身と比較): 不一致 {mis_self}/{n}  "
      f"{'PASS' if mis_self == 0 else 'FAIL'}")

# --- 中身も 1 件だけ突き合わせる（数だけ合っていても中身が違えば意味がない）---
t0 = texts[0]
lab0 = pyopenjtalk.extract_fullcontext(t0)
print(f"\n参考 (先頭 1 件の中身): {t0}")
print(f"  ラベル数 {len(lab0)} / 先頭: {lab0[0][:60]}...")

ok = (len(mis_lab) == 0 and len(mis_njd) == 0 and mis_shift > n * 0.5 and mis_self == 0)
print(f"\n総合: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
