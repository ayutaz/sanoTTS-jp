"""辞書を切り替えて段レベル帰属をやり直す（B-0 の環境で帰属が変わらないかの確認）。

⚠️ OPEN_JTALK_DICT_DIR は pyopenjtalk を import する **前**に設定する（B-0 付録）。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os, sys, json

DICT = os.environ.get("SAAN_DICT")
if DICT:
    os.environ["OPEN_JTALK_DICT_DIR"] = DICT

sys.path.insert(0, (_WORK + ""))
import pyopenjtalk as P  # noqa: E402
from g2p_ablate import CORPUS, STAGE_NAMES, UNITS, run_chain, wilson  # noqa: E402

SCRATCH = (_WORK + "/")


def njd_chain_phrase(njd):
    out = []
    for f in njd:
        if f["chain_flag"] == 1 and out:
            a, m = out[-1]
            out[-1] = (a, m + f["mora_size"])
        else:
            out.append((f["acc"], f["mora_size"]))
    return tuple(out)


def main():
    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        assert hdr[:3] == ["source", "id", "text"], hdr
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3 and p[2].strip():
                rows.append(p[2])
    n = len(rows)
    allst = set(STAGE_NAMES)
    variants = {"vanilla": set(), "c_feasible": allst - {"predict_nani", "sudachi_kanji_yomi"}}
    for s in STAGE_NAMES:
        variants[f"minus:{s}"] = allst - {s}
    # B-0 定義（NJD chain アクセント句）も足す
    units = dict(UNITS)
    dis = {v: {u: 0 for u in units} | {"N3_chain句": 0} for v in variants}
    g1 = g2 = 0

    with P._resolve_jtalk(None) as jt:
        for i, t in enumerate(rows):
            njd_ref = P.run_frontend(t)
            ref_lab = P.make_label(njd_ref, jtalk=jt)
            ref = {u: units[u](ref_lab, t) for u in units}
            ref_n3 = njd_chain_phrase(njd_ref)
            raw = jt.run_frontend(t)
            if run_chain(t, raw, jt, allst) == ref_lab:
                g1 += 1
            if run_chain(t, raw, jt, set()) == P.extract_fullcontext(t, use_vanilla=True):
                g2 += 1
            for v, en in variants.items():
                lab = run_chain(t, raw, jt, en)
                for u in units:
                    if units[u](lab, t) != ref[u]:
                        dis[v][u] += 1
                # N3 は NJD 側なので段を njd で回し直す
            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    dd = (P.OPEN_JTALK_DICT_DIR.decode() if isinstance(P.OPEN_JTALK_DICT_DIR, bytes)
          else str(P.OPEN_JTALK_DICT_DIR))
    print("\ndict:", dd)
    print("sys.dic:", os.path.getsize(os.path.join(dd, "sys.dic")), "B")
    print(f"G1 手組み==既定 {g1}/{n}   G2 段ゼロ==API vanilla {g2}/{n}")
    us = ["U1_phoneme", "U2_accphrase", "U3_pipertok", "U4_marks"]
    print(f"{'variant':28s}" + "".join(f"{u:>14s}" for u in us))
    out = {"dict": dd, "n": n, "g1": g1, "g2": g2, "variants": {}}
    for v in variants:
        print(f"{v:28s}" + "".join(f"{(n-dis[v][u])/n*100:13.2f}%" for u in us))
        out["variants"][v] = {u: {"agree": n - dis[v][u], "n": n,
                                  "rate": (n - dis[v][u]) / n,
                                  "ci95": wilson(n - dis[v][u], n)} for u in us}
    tag = os.environ.get("SAAN_TAG", "bundled")
    with open(SCRATCH + f"ablate_dict_{tag}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
