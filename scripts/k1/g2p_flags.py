"""Q2 の API フラグ版 + Q4 の「C に落とせる構成」+ Q3 の残差分類。

g2p_ablate.py が内部 7 段を直接叩くのに対し、こちらは依頼どおり
extract_fullcontext の公開フラグを 1 つずつ既定からずらす。
"""

from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import copy
import json
import sys
from collections import Counter, defaultdict

import pyopenjtalk as P
from pyopenjtalk import make_label

sys.path.insert(0, (_WORK + ""))
from g2p_ablate import (  # noqa: E402
    CORPUS, STAGES, STAGE_NAMES, UNITS, run_chain, wilson, piper_tokens,
)

SCRATCH = (_WORK + "/")

# --- 依頼された公開フラグ。既定から 1 つずつずらす ---------------------------
FLAG_VARIANTS = {
    "default": {},
    "use_vanilla=True": {"use_vanilla": True},
    "use_sudachi_kanji_yomi=False": {"use_sudachi_kanji_yomi": False},
    "predict_nani=False": {"predict_nani": False},
    "both_lexical=False": {"use_sudachi_kanji_yomi": False, "predict_nani": False},
    "use_read_as_pron=True": {"use_read_as_pron": True},
    "revert_long_vowels=True": {"revert_long_vowels": True},
    "revert_yotsugana=True": {"revert_yotsugana": True},
    "use_tsqyomi=True": {"use_tsqyomi": True},
    "run_marine=True": {"run_marine": True},
}

DEVOICE = {"I": "i", "U": "u", "A": "a", "E": "e", "O": "o"}


def norm_devoice(seq):
    return tuple(DEVOICE.get(x, x) for x in seq)


def classify(text, ref_toks, got_toks):
    """不一致 1 文を分類する。ref = 既定経路、got = 比較対象。"""
    ref_ph = [t for t in ref_toks if t not in ("[", "]", "#")]
    got_ph = [t for t in got_toks if t not in ("[", "]", "#")]
    ph_same = ref_ph == got_ph
    ph_same_devoiced = norm_devoice(ref_ph) == norm_devoice(got_ph)

    cats = []
    if not ph_same:
        if ph_same_devoiced:
            cats.append("phoneme:devoicing_only")
        elif len(ref_ph) != len(got_ph):
            cats.append("phoneme:reading_length_differs")
        else:
            cats.append("phoneme:reading_same_length")
    # 記号側
    def marks(toks):
        out, n = [], 0
        for t in toks:
            if t in ("[", "]", "#"):
                out.append((t, n))
            else:
                n += 1
        return out
    rm, gm = marks(ref_toks), marks(got_toks)
    if ph_same and rm != gm:
        rn = Counter(m for m, _ in rm)
        gn = Counter(m for m, _ in gm)
        if rn["#"] != gn["#"]:
            cats.append("accent:phrase_boundary_count")
        elif rn["]"] != gn["]"]:
            cats.append("accent:nucleus_count")
        elif [m for m, _ in rm] == [m for m, _ in gm]:
            cats.append("accent:position_only")
        else:
            cats.append("accent:mark_order")
    elif (not ph_same) and rm != gm:
        cats.append("accent:secondary_to_phoneme_change")
    if not cats:
        cats.append("identical")
    return cats


def main():
    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        assert hdr[:3] == ["source", "id", "text"], hdr
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3 and p[2].strip():
                rows.append((p[0], p[1], p[2]))
    n = len(rows)

    # 使えないフラグを先に落とす（1 文で試す）
    usable, unusable = {}, {}
    for name, kw in FLAG_VARIANTS.items():
        try:
            P.extract_fullcontext(rows[0][2], **kw)
            usable[name] = kw
        except Exception as e:  # noqa: BLE001
            unusable[name] = f"{type(e).__name__}: {e}"

    # C に落とせる構成 = 内部 7 段のうち語彙資源を要する 2 段だけ落としたもの
    C_FEASIBLE = set(STAGE_NAMES) - {"predict_nani", "sudachi_kanji_yomi"}

    dis = {v: {u: 0 for u in UNITS} for v in list(usable) + ["c_feasible"]}
    per_source = defaultdict(lambda: {"n": 0, "vanilla_u3": 0, "cfeas_u3": 0,
                                      "vanilla_u1": 0, "cfeas_u1": 0})
    cat_vanilla, cat_cfeas = Counter(), Counter()
    ex_vanilla, ex_cfeas = [], []
    marine_note = None

    with P._resolve_jtalk(None) as jt:
        for i, (src, uid, text) in enumerate(rows):
            ref_lab = P.extract_fullcontext(text)
            ref = {u: UNITS[u](ref_lab, text) for u in UNITS}
            ref_toks = piper_tokens(ref_lab, text)

            per_source[src]["n"] += 1
            for name, kw in usable.items():
                if name == "default":
                    continue
                lab = P.extract_fullcontext(text, **kw)
                for u in UNITS:
                    if UNITS[u](lab, text) != ref[u]:
                        dis[name][u] += 1
                if name == "use_vanilla=True":
                    toks = piper_tokens(lab, text)
                    if toks == ref_toks:
                        per_source[src]["vanilla_u3"] += 1
                    else:
                        for c in classify(text, ref_toks, toks):
                            cat_vanilla[c] += 1
                        if len(ex_vanilla) < 3000:
                            ex_vanilla.append({"src": src, "uid": uid, "text": text,
                                               "ref": ref_toks, "got": toks,
                                               "cats": classify(text, ref_toks, toks)})
                    if UNITS["U1_phoneme"](lab, text) == ref["U1_phoneme"]:
                        per_source[src]["vanilla_u1"] += 1

            raw = jt.run_frontend(text)
            lab_c = run_chain(text, raw, jt, C_FEASIBLE)
            toks_c = piper_tokens(lab_c, text)
            for u in UNITS:
                if UNITS[u](lab_c, text) != ref[u]:
                    dis["c_feasible"][u] += 1
            if toks_c == ref_toks:
                per_source[src]["cfeas_u3"] += 1
            else:
                for c in classify(text, ref_toks, toks_c):
                    cat_cfeas[c] += 1
                if len(ex_cfeas) < 3000:
                    ex_cfeas.append({"src": src, "uid": uid, "text": text,
                                     "ref": ref_toks, "got": toks_c,
                                     "cats": classify(text, ref_toks, toks_c)})
            if UNITS["U1_phoneme"](lab_c, text) == ref["U1_phoneme"]:
                per_source[src]["cfeas_u1"] += 1

            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    out = {"n": n, "unusable_flags": unusable, "variants": {}, "per_source": {},
           "cat_vanilla": dict(cat_vanilla), "cat_cfeasible": dict(cat_cfeas)}
    for v in dis:
        if v == "default":
            continue
        out["variants"][v] = {}
        for u in UNITS:
            k = n - dis[v][u]
            lo, hi = wilson(k, n)
            out["variants"][v][u] = {"agree": k, "n": n, "rate": k / n,
                                     "ci95": [lo, hi], "disagree": dis[v][u]}
    for s, d in per_source.items():
        out["per_source"][s] = d

    with open(SCRATCH + "flags_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(SCRATCH + "flags_examples.json", "w", encoding="utf-8") as f:
        json.dump({"vanilla": ex_vanilla, "c_feasible": ex_cfeas}, f, ensure_ascii=False)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
