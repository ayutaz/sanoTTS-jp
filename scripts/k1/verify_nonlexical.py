"""ceiling-lane の最重要の主張を独立に検証する。

主張: 「アクセントの天井 76% は語彙資源の床ではない。**外部資源ゼロの
Python 関数 5 本**の床であり、語彙 2 段だけ切れば 99.87% まで戻る」

これが本当なら、SudachiDict 217 MB と nani ONNX が載らないことは
アクセント精度の障害ではなくなり、判定が反転する。**採用する前に自分で測る。**

ゲート:
  G14 語彙 2 段 off vs 既定 の一致率（全 2,333 文）
  G15 陰性対照: use_vanilla=True なら大きく落ちること
  G16 5 本の非語彙段が「外部資源ゼロ」か（ソースを読んで import を確認）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import inspect
import os
import re
import sys

import pyopenjtalk

ROOT = (_ROOT + "")

texts = []
with open(f"{ROOT}/data/splits/corpus_heldout.tsv", encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
print(f"n = {len(texts):,d} 文（全ソース）", flush=True)


def njd(t, **kw):
    return pyopenjtalk.run_frontend(t, **kw)


def u_acc(fs):
    """(acc, mora_size) 列"""
    return [(f["acc"], f["mora_size"]) for f in fs]


def u_phrase(fs):
    """chain_flag で連結したアクセント句列 = B-0 の単位"""
    out = []
    for f in fs:
        if f.get("chain_flag", 0) == 1 and out:
            out[-1] = (out[-1][0], out[-1][1] + f["mora_size"])
        else:
            out.append((f["acc"], f["mora_size"]))
    return out


def u_pron(fs):
    return "".join(f["pron"] for f in fs)


CONFIGS = {
    "既定（全 7 段）": {},
    "語彙 2 段 off（C で到達しうる上限）":
        dict(use_sudachi_kanji_yomi=False, predict_nani=False),
    "use_vanilla=True（C 実装のみ）": dict(use_vanilla=True),
}

res = {k: dict(acc=0, phrase=0, pron=0, n=0) for k in CONFIGS if k != "既定（全 7 段）"}
for t in texts:
    try:
        base = njd(t)
    except Exception:
        continue
    ba, bp, bq = u_acc(base), u_phrase(base), u_pron(base)
    for name, kw in CONFIGS.items():
        if not kw:
            continue
        try:
            c = njd(t, **kw)
        except Exception:
            continue
        r = res[name]
        r["n"] += 1
        if u_acc(c) == ba:
            r["acc"] += 1
        if u_phrase(c) == bp:
            r["phrase"] += 1
        if u_pron(c) == bq:
            r["pron"] += 1

print(f"\n=== 既定との一致率（全 {len(texts):,d} 文）===")
print(f"{'構成':40s} {'(acc,mora)列':>14s} {'アクセント句':>14s} {'読み':>12s}")
for name, r in res.items():
    n = r["n"]
    print(f"{name:40s} {100*r['acc']/n:>13.2f}% {100*r['phrase']/n:>13.2f}% "
          f"{100*r['pron']/n:>11.2f}%  (n={n})")

lex = res["語彙 2 段 off（C で到達しうる上限）"]
van = res["use_vanilla=True（C 実装のみ）"]
print(f"\n  非語彙 5 段が担う差（アクセント句単位）= "
      f"{100*lex['phrase']/lex['n'] - 100*van['phrase']/van['n']:.2f} pt")
G14 = lex["phrase"] / lex["n"] > 0.95
G15 = van["phrase"] / van["n"] < 0.90
print(f"  G14 (語彙 2 段 off で >95%): {'PASS' if G14 else 'FAIL'}")
print(f"  G15 陰性対照 (vanilla で <90%): {'PASS' if G15 else 'FAIL'}")

# --- G16: 5 本の非語彙段が外部資源を使っていないか ---
print("\n=== G16: 非語彙 5 段は外部資源を使うか（ソースを読む）===")
STAGES = ["modify_filler_accent", "suppress_unnatural_auxiliary_u_long_vowel",
          "retreat_acc_nuc", "modify_acc_after_chaining", "process_odori_features"]
LEXICAL = ["predict_nani_reading", "modify_kanji_yomi"]
mod = sys.modules["pyopenjtalk"]
for nm in STAGES + LEXICAL:
    fn = getattr(mod, nm, None)
    if fn is None:
        for cand in dir(mod):
            if cand == nm:
                fn = getattr(mod, cand)
    if fn is None:
        print(f"  {nm:45s} (見つからない)")
        continue
    try:
        src = inspect.getsource(fn)
    except Exception as e:
        print(f"  {nm:45s} ソース取得不可: {e}")
        continue
    lines = len(src.splitlines())
    heavy = re.findall(r"sudachi|onnx|Sudachi|ONNX|torch|numpy|model|dictionary",
                       src, re.I)
    print(f"  {nm:45s} {lines:>4d} 行  重い依存の言及: "
          f"{sorted(set(h.lower() for h in heavy)) if heavy else 'なし'}")
