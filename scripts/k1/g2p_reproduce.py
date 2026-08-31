"""Q1: 報告済みの 76.22% / 95.4% / 94.67% / 84.0% を、それぞれの定義で再現できるか。

定義を 3 系統ぶん実装して同時に出す。どれで報告されたかが分からないので混ぜない。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, re, sys
import pyopenjtalk as P

sys.path.insert(0, (_WORK + ""))
from g2p_ablate import CORPUS, UNITS, wilson

SCRATCH = (_WORK + "/")
_RE_PH = re.compile(r"-(?P<ph>[^+]+)\+")
_RE_A = re.compile(r"/A:(?P<a1>[\d-]+)\+(?P<a2>[0-9]+)\+(?P<a3>[0-9]+)/")


def njd_accent_phrases(njd):
    """B-0 §2-A の定義: chain_flag で連結し (核位置, モーラ数) の列にする。"""
    out = []
    for f in njd:
        if f["chain_flag"] == 1 and out:
            acc, mora = out[-1]
            out[-1] = (acc, mora + f["mora_size"])
        else:
            out.append((f["acc"], f["mora_size"]))
    return tuple(out)


def p3_seq(labels):
    return tuple(m.group("ph") for m in (_RE_PH.search(l) for l in labels) if m)


def a1_seq(labels):
    return tuple(m.group("a1") for m in (_RE_A.search(l) for l in labels) if m)


def a123_seq(labels):
    return tuple((m.group("a1"), m.group("a2"), m.group("a3"))
                 for m in (_RE_A.search(l) for l in labels) if m)


def main():
    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        assert hdr[:3] == ["source", "id", "text"], hdr
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3 and p[2].strip():
                rows.append((p[0], p[1], p[2]))

    defs = ["D1_P3列", "D2_A1列", "D3_A1A2A3列", "D4_NJDアクセント句(chain_flag)",
            "D5_U2_F欄アクセント句", "D6_piperトークン列", "D7_記号列のみ"]
    subsets = {"全 held-out": range(len(rows)),
               "先頭 800 (= M-11 が使った並び)": range(min(800, len(rows)))}

    agree = {s: {d: 0 for d in defs} for s in subsets}
    tot = {s: 0 for s in subsets}

    cache = {}
    with P._resolve_jtalk(None) as jt:
        for i, (_, _, text) in enumerate(rows):
            njd_d = P.run_frontend(text)
            njd_v = P.run_frontend(text, use_vanilla=True)
            lab_d = P.make_label(njd_d, jtalk=jt)
            lab_v = P.make_label(njd_v, jtalk=jt)
            r = {
                "D1_P3列": p3_seq(lab_d) == p3_seq(lab_v),
                "D2_A1列": a1_seq(lab_d) == a1_seq(lab_v),
                "D3_A1A2A3列": a123_seq(lab_d) == a123_seq(lab_v),
                "D4_NJDアクセント句(chain_flag)": njd_accent_phrases(njd_d) == njd_accent_phrases(njd_v),
                "D5_U2_F欄アクセント句": UNITS["U2_accphrase"](lab_d, text) == UNITS["U2_accphrase"](lab_v, text),
                "D6_piperトークン列": UNITS["U3_pipertok"](lab_d, text) == UNITS["U3_pipertok"](lab_v, text),
                "D7_記号列のみ": UNITS["U4_marks"](lab_d, text) == UNITS["U4_marks"](lab_v, text),
            }
            cache[i] = r
            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{len(rows)}", file=sys.stderr)

    for s, idxs in subsets.items():
        for i in idxs:
            tot[s] += 1
            for d in defs:
                agree[s][d] += cache[i][d]

    out = {}
    print(f"\n{'定義':38s}" + "".join(f"{s:>34s}" for s in subsets))
    for d in defs:
        line = f"{d:38s}"
        out[d] = {}
        for s in subsets:
            k, n = agree[s][d], tot[s]
            lo, hi = wilson(k, n)
            out[d][s] = {"agree": k, "n": n, "rate": k / n, "ci95": [lo, hi]}
            line += f"{k:6d}/{n:<5d}{k/n*100:6.2f}% [{lo*100:5.2f},{hi*100:5.2f}]"
        print(line)
    with open(SCRATCH + "reproduce_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
