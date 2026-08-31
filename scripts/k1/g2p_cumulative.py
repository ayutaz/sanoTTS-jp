"""鎖分解: vanilla に後処理を実行順で 1 段ずつ足していったときの一致率。

⚠️ M-49 と同じ罠 — 「置換 (leave-one-out)」と「鎖 (cumulative)」で
   主因の順位が入れ替わりうる。両方を出して並べる。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, sys
import pyopenjtalk as P

sys.path.insert(0, (_WORK + ""))
from g2p_ablate import CORPUS, STAGE_NAMES, UNITS, run_chain, wilson

SCRATCH = (_WORK + "/")


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

    # 実行順の prefix
    prefixes = {f"+{i}:{'+'.join(STAGE_NAMES[:i])}" if i else "vanilla(0段)":
                set(STAGE_NAMES[:i]) for i in range(len(STAGE_NAMES) + 1)}
    # 語彙 2 段を最後に回した順序（C 実装に載る段を先に入れる = Q4 の順序）
    order2 = ["filler_accent", "suppress_u_long", "retreat_acc_nuc",
              "acc_after_chaining", "odori", "predict_nani", "sudachi_kanji_yomi"]
    prefixes2 = {f"B{i}:{'+'.join(order2[:i]) or 'vanilla'}": set(order2[:i])
                 for i in range(len(order2) + 1)}

    allv = {**prefixes, **prefixes2}
    dis = {v: {u: 0 for u in UNITS} for v in allv}

    with P._resolve_jtalk(None) as jt:
        for i, (_, _, text) in enumerate(rows):
            ref_lab = P.extract_fullcontext(text)
            ref = {u: UNITS[u](ref_lab, text) for u in UNITS}
            raw = jt.run_frontend(text)
            for v, en in allv.items():
                lab = run_chain(text, raw, jt, en)
                for u in UNITS:
                    if UNITS[u](lab, text) != ref[u]:
                        dis[v][u] += 1
            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    out = {"n": n, "orderA_execution": {}, "orderB_lexical_last": {}}
    for v in prefixes:
        out["orderA_execution"][v] = {u: {"agree": n - dis[v][u], "rate": (n - dis[v][u]) / n,
                                          "ci95": wilson(n - dis[v][u], n)} for u in UNITS}
    for v in prefixes2:
        out["orderB_lexical_last"][v] = {u: {"agree": n - dis[v][u], "rate": (n - dis[v][u]) / n,
                                             "ci95": wilson(n - dis[v][u], n)} for u in UNITS}
    with open(SCRATCH + "cumulative_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    units = ["U1_phoneme", "U2_accphrase", "U3_pipertok", "U4_marks"]
    for key in ("orderA_execution", "orderB_lexical_last"):
        print(f"\n== {key} ==")
        prev = None
        for v, d in out[key].items():
            line = f"{v[:58]:60s}" + "".join(f"{d[u]['rate']*100:9.2f}%" for u in units)
            if prev is not None:
                line += "   Δ U3 " + f"{(d['U3_pipertok']['rate']-prev)*100:+.2f}pt"
            prev = d["U3_pipertok"]["rate"]
            print(line)


if __name__ == "__main__":
    main()
