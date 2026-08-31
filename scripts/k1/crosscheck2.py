"""ゲート v2: 不一致の出どころを切り分ける。

pyopenjtalk_plus は既定で fork 独自の後処理 (Sudachi 読み補正 / 「何」推定 /
normalize_for_mecab) を挟む。saan_probe は素の OpenJTalk なので、
比較は use_vanilla=True 側で行う必要がある。
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
cols = {}
with open(os.path.join(_WORK, "probe_venv.tsv"), encoding="utf-8") as f:
    f.readline()
    rows = [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]
for k in ("labels", "njd_nodes", "morphs"):
    i = hdr.index(k)
    cols[k] = [int(r[i]) for r in rows]

n = len(texts)
assert len(rows) == n

jt = pyopenjtalk._global_jtalk if hasattr(pyopenjtalk, "_global_jtalk") else None


def cmp(name, py, pr):
    mis = [i for i in range(n) if py[i] != pr[i]]
    print(f"{name:<44s} 一致 {n-len(mis):>5d}/{n} = {(n-len(mis))/n:.6f}")
    for i in mis[:4]:
        print(f"      idx={i} py={py[i]} probe={pr[i]}  {texts[i][:34]}")
    return mis


print("=== 1. 既定の pyopenjtalk_plus (fork の後処理あり) ===")
py_lab_def = [len(pyopenjtalk.extract_fullcontext(t)) for t in texts]
mis_def = cmp("ラベル数 (既定)", py_lab_def, cols["labels"])

print("\n=== 2. use_vanilla=True + fork 後処理を全部切る ===")
kw = dict(use_vanilla=True, use_sudachi_kanji_yomi=False, predict_nani=False)
py_lab_van = [len(pyopenjtalk.extract_fullcontext(t, **kw)) for t in texts]
py_njd_van = [len(pyopenjtalk.run_frontend(t, **kw)) for t in texts]
mis_van = cmp("ラベル数 (vanilla)", py_lab_van, cols["labels"])
mis_njd_van = cmp("NJD ノード数 (vanilla)", py_njd_van, cols["njd_nodes"])

print("\n=== 3. mecab 形態素数（正規化の影響を見る） ===")
ojt = pyopenjtalk.OpenJTalk(dn_mecab=pyopenjtalk.OPEN_JTALK_DICT_DIR)
py_morphs = [len(ojt.run_mecab(t)) for t in texts]
mis_mor = cmp("mecab 形態素数", py_morphs, cols["morphs"])

print("\n=== 4. normalize_for_mecab が入力を書き換える文の数 ===")
changed = [i for i, t in enumerate(texts) if ojt.normalize_for_mecab(t) != t]
print(f"書き換えられた文: {len(changed)}/{n}")
for i in changed[:4]:
    print(f"      idx={i}: {texts[i][:30]!r} -> {ojt.normalize_for_mecab(texts[i])[:30]!r}")
print(f"ラベル不一致 (vanilla) のうち正規化で書き換わった文: "
      f"{len(set(mis_van) & set(changed))}/{len(mis_van)}")

print("\n=== 陽性対照 / 陰性対照 ===")
shifted = cols["labels"][1:] + cols["labels"][:1]
ms = sum(1 for a, b in zip(py_lab_van, shifted) if a != b)
print(f"陽性対照 (1 行ずらす)     : 不一致 {ms}/{n}  {'PASS' if ms > n*0.5 else 'FAIL'}")
mz = sum(1 for a, b in zip(cols['labels'], cols['labels']) if a != b)
print(f"陰性対照 (自分自身)       : 不一致 {mz}/{n}  {'PASS' if mz == 0 else 'FAIL'}")
