"""照合: team-lead の 3 単位を足し、単位差と部分集合差を分離する。
さらに 76.22% / 94.67% がどの単位・どの辞書で出るかを総当たりする。

環境変数 OPEN_JTALK_DICT_DIR は **import 前に**設定すること（B-0 付録）。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json, os, re, sys

DICT = os.environ.get("SAAN_DICT")
if DICT:
    os.environ["OPEN_JTALK_DICT_DIR"] = DICT

import pyopenjtalk as P  # noqa: E402

CORPUS = (_ROOT + "/data/splits/corpus_heldout.tsv")
SCRATCH = (_WORK + "/")
_RE_PH = re.compile(r"-(?P<ph>[^+]+)\+")
_RE_A = re.compile(r"/A:(?P<a1>[\d-]+)\+(?P<a2>[0-9]+)\+(?P<a3>[0-9]+)/")
_RE_F = re.compile(r"/F:(?P<f1>[^_]+)_(?P<f2>[^#]+)#")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


# ---- NJD レベルの単位（team-lead の 3 つを含む） -----------------------------
def njd_string(njd):            # U0 (team-lead)
    return tuple(f["string"] for f in njd)


def njd_pron_concat(njd):       # U1r (team-lead) — 連結した pron
    return "".join(f["pron"] for f in njd)


def njd_pron_nomark(njd):       # U1r' — 無声化マーカ ’ を落とした pron
    return "".join(f["pron"] for f in njd).replace("’", "").replace("'", "")


def njd_acc_mora(njd):          # U3n (team-lead) — 形態素ごとの (acc, mora_size)
    return tuple((f["acc"], f["mora_size"]) for f in njd)


def njd_acc_only(njd):
    return tuple(f["acc"] for f in njd)


def njd_chain_phrase(njd):      # B-0 §2-A の定義: chain_flag で連結
    out = []
    for f in njd:
        if f["chain_flag"] == 1 and out:
            a, m = out[-1]
            out[-1] = (a, m + f["mora_size"])
        else:
            out.append((f["acc"], f["mora_size"]))
    return tuple(out)


def njd_chain_phrase_gt0(njd):  # chain_flag が -1 と 0 を区別しない実装だった場合
    out = []
    for f in njd:
        if f["chain_flag"] > 0 and out:
            a, m = out[-1]
            out[-1] = (a, m + f["mora_size"])
        else:
            out.append((f["acc"], f["mora_size"]))
    return tuple(out)


NJD_UNITS = {
    "N0_string列(分割)": njd_string,
    "N1_pron連結(読み)": njd_pron_concat,
    "N1b_pron連結(無声化マーカ除去)": njd_pron_nomark,
    "N2_(acc,mora)列/形態素": njd_acc_mora,
    "N2b_acc列/形態素": njd_acc_only,
    "N3_chainアクセント句(flag==1)": njd_chain_phrase,
    "N3b_chainアクセント句(flag>0)": njd_chain_phrase_gt0,
}


# ---- label レベルの単位 -----------------------------------------------------
def lab_p3(labs):
    return tuple(m.group("ph") for m in (_RE_PH.search(x) for x in labs) if m)


def lab_p3_nosil(labs):
    return tuple(p for p in lab_p3(labs) if p not in ("sil", "pau"))


def lab_a1(labs):
    return tuple(m.group("a1") for m in (_RE_A.search(x) for x in labs) if m)


def lab_a123(labs):
    return tuple((m.group("a1"), m.group("a2"), m.group("a3"))
                 for m in (_RE_A.search(x) for x in labs) if m)


def lab_f_perlabel(labs):
    return tuple((m.group("f1"), m.group("f2"))
                 for m in (_RE_F.search(x) for x in labs) if m)


def lab_f_dedup(labs):
    out, prev = [], None
    for x in labs:
        m = _RE_F.search(x)
        if not m:
            prev = None
            continue
        cur = (m.group("f1"), m.group("f2"))
        if cur != prev:
            out.append(cur)
        prev = cur
    return tuple(out)


LAB_UNITS = {
    "L1_P3音素列": lab_p3,
    "L1b_P3(sil/pau除去)": lab_p3_nosil,
    "L2_A1列": lab_a1,
    "L3_A1A2A3列": lab_a123,
    "L4_F欄/ラベル毎": lab_f_perlabel,
    "L5_F欄アクセント句(畳み)": lab_f_dedup,
}

SUBSETS = {"全2333": None, "先頭400": 400, "先頭800": 800, "先頭2325": 2325}


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

    names = list(NJD_UNITS) + list(LAB_UNITS)
    hit = {u: [] for u in names}          # 文ごとの一致 bool
    ndet = 0                              # 陰性対照: 検出できた文数

    with P._resolve_jtalk(None) as jt:
        for i, t in enumerate(rows):
            njd_d = P.run_frontend(t)
            njd_v = P.run_frontend(t, use_vanilla=True)
            lab_d = P.make_label(njd_d, jtalk=jt)
            lab_v = P.make_label(njd_v, jtalk=jt)
            for u, fn in NJD_UNITS.items():
                hit[u].append(fn(njd_d) == fn(njd_v))
            for u, fn in LAB_UNITS.items():
                hit[u].append(fn(lab_d) == fn(lab_v))
            # 陰性対照: 既定を 1 ノード削ったものは必ずどれかの単位で捕まるか
            if len(njd_d) > 1:
                brk = njd_d[:-1]
                if any(fn(brk) != fn(njd_d) for fn in NJD_UNITS.values()):
                    ndet += 1
            else:
                ndet += 1
            if (i + 1) % 500 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    dictdir = (P.OPEN_JTALK_DICT_DIR.decode()
               if isinstance(P.OPEN_JTALK_DICT_DIR, bytes) else str(P.OPEN_JTALK_DICT_DIR))
    sysdic = os.path.join(dictdir, "sys.dic")
    out = {"dict_dir": dictdir,
           "sys_dic_bytes": os.path.getsize(sysdic) if os.path.exists(sysdic) else None,
           "n": n, "neg_control_detected": [ndet, n], "units": {}}

    hdr2 = f"{'単位':34s}" + "".join(f"{s:>22s}" for s in SUBSETS)
    print("\ndict:", dictdir)
    print("sys.dic:", out["sys_dic_bytes"], "B    陰性対照 検出", ndet, "/", n)
    print(hdr2)
    for u in names:
        out["units"][u] = {}
        line = f"{u:34s}"
        for s, lim in SUBSETS.items():
            h = hit[u][:lim] if lim else hit[u]
            k, m = sum(h), len(h)
            lo, hi = wilson(k, m)
            out["units"][u][s] = {"agree": k, "n": m, "rate": k / m, "ci95": [lo, hi]}
            line += f"{k:5d}/{m:<5d}{k/m*100:6.2f}%"
        print(line)

    tag = os.environ.get("SAAN_TAG", "bundled")
    with open(SCRATCH + f"reconcile_{tag}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
