"""文単位だけでなくトークン単位でも出す（B-0 §3 の「95% は文単位かトークン単位か」問題）。

+ 既定経路が 1 モーラのアクセント句を作る頻度（全 2333 文）。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, sys, difflib
import pyopenjtalk as P

sys.path.insert(0, (_WORK + ""))
from g2p_ablate import CORPUS, STAGE_NAMES, UNITS, run_chain, piper_tokens, wilson

SCRATCH = (_WORK + "/")
MARKS = ("[", "]", "#")


def err(ref, hyp):
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    e = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            e += max(i2 - i1, j2 - j1)
    return e


def has_mono_phrase(toks):
    """既定側に「# X #」= 1 音素だけのアクセント句があるか。"""
    for i in range(len(toks) - 2):
        if toks[i] == "#" and toks[i + 2] == "#":
            return True
    return False


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

    C_FEASIBLE = set(STAGE_NAMES) - {"predict_nani", "sudachi_kanji_yomi"}
    acc = {v: {"ph_ref": 0, "ph_err": 0, "mk_ref": 0, "mk_err": 0,
               "tok_ref": 0, "tok_err": 0}
           for v in ("vanilla", "c_feasible")}
    mono_default = 0
    mono_cfeas = 0

    with P._resolve_jtalk(None) as jt:
        for i, (_, _, text) in enumerate(rows):
            ref_lab = P.extract_fullcontext(text)
            ref = piper_tokens(ref_lab, text)
            raw = jt.run_frontend(text)
            if has_mono_phrase(ref):
                mono_default += 1
            variants = {"vanilla": run_chain(text, raw, jt, set()),
                        "c_feasible": run_chain(text, raw, jt, C_FEASIBLE)}
            for v, lab in variants.items():
                got = piper_tokens(lab, text)
                if v == "c_feasible" and has_mono_phrase(got):
                    mono_cfeas += 1
                rp = [t for t in ref if t not in MARKS]
                gp = [t for t in got if t not in MARKS]
                rm = [t for t in ref if t in MARKS]
                gm = [t for t in got if t in MARKS]
                a = acc[v]
                a["ph_ref"] += len(rp); a["ph_err"] += err(rp, gp)
                a["mk_ref"] += len(rm); a["mk_err"] += err(rm, gm)
                a["tok_ref"] += len(ref); a["tok_err"] += err(list(ref), list(got))
            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    out = {"n_sentences": n, "mono_phrase_default": mono_default,
           "mono_phrase_c_feasible": mono_cfeas, "token_level": {}}
    for v, a in acc.items():
        out["token_level"][v] = {
            "phoneme_tokens": a["ph_ref"], "phoneme_err": a["ph_err"],
            "phoneme_agree": 1 - a["ph_err"] / a["ph_ref"],
            "mark_tokens": a["mk_ref"], "mark_err": a["mk_err"],
            "mark_agree": 1 - a["mk_err"] / a["mk_ref"],
            "all_tokens": a["tok_ref"], "all_err": a["tok_err"],
            "all_agree": 1 - a["tok_err"] / a["tok_ref"],
        }
    with open(SCRATCH + "token_level.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
