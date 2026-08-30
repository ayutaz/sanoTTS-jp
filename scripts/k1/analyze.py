"""Q2 / Q3 / Q4 の集計。saan_probe の TSV を読む。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import sys

SP = (_WORK + "")


def load(path):
    with open(path, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        rows = []
        for ln in f:
            ln = ln.rstrip("\n")
            if not ln:
                continue
            rows.append([int(x) for x in ln.split("\t")])
    return hdr, rows


def col(hdr, rows, name):
    i = hdr.index(name)
    return [r[i] for r in rows]


def pct(xs, p):
    s = sorted(xs)
    if not s:
        return 0
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(name, xs, unit=""):
    n = len(xs)
    print(f"{name:<28s} n={n:<5d} mean={sum(xs)/n:>10.2f} median={pct(xs,50):>9.1f} "
          f"p95={pct(xs,95):>9.1f} p99={pct(xs,99):>9.1f} max={max(xs):>9d} min={min(xs):>6d} {unit}")


def report(tag, path, texts):
    hdr, rows = load(path)
    print("=" * 100)
    print(f"### {tag}   ({path})")
    print(f"n = {len(rows)} 文")
    print("=" * 100)

    chars = col(hdr, rows, "in_chars")
    mbytes = col(hdr, rows, "mecab_bytes")
    tot = col(hdr, rows, "nodes_total")
    nd = col(hdr, rows, "nodes_dict")
    nu = col(hdr, rows, "nodes_unk")
    nb = col(hdr, rows, "nodes_bos_eos")
    paths = col(hdr, rows, "paths")
    cb = col(hdr, rows, "char_bytes")
    mx = col(hdr, rows, "max_nodes_at_pos")
    njd = col(hdr, rows, "njd_nodes")
    lab = col(hdr, rows, "labels")
    mor = col(hdr, rows, "morphs")
    hb = col(hdr, rows, "heap_before")
    ha = col(hdr, rows, "heap_after")

    print("\n--- 入力 ---")
    dist("入力 文字数 (codepoint)", chars)
    dist("text2mecab 後 バイト数", mbytes, "B")

    print("\n--- Q2: Viterbi lattice ノード数 (実測) ---")
    dist("lattice ノード 合計", tot)
    dist("  うち 辞書ノード", nd)
    dist("  うち 未知語ノード", nu)
    dist("  うち BOS/EOS", nb)
    dist("Path オブジェクト", paths)
    dist("1 位置あたり最大ノード数", mx)
    per_char = [t / c for t, c in zip(tot, chars)]
    print(f"{'ノード数 / 入力文字':<28s} n={len(per_char)} mean={sum(per_char)/len(per_char):>10.2f} "
          f"median={pct(per_char,50):>9.2f} p95={pct(per_char,95):>9.2f} max={max(per_char):>9.2f}")

    print("\n--- Q3: NJD / JPCommon (実測) ---")
    dist("形態素数 (best path)", mor)
    dist("NJD ノード数", njd)
    dist("フルコンテキストラベル数", lab)

    print("\n--- Allocator::alloc() 文字列コピー ---")
    dist("char_bytes", cb, "B")

    print("\n--- C ヒープ (malloc size_in_use, 実測) ---")
    print(f"最初の文の直前 (辞書ロード直後) : {hb[0]:>12,d} B")
    print(f"最後の文の直後 (2333 文終了時)  : {ha[-1]:>12,d} B")
    print(f"高水位 (max heap_after)         : {max(ha):>12,d} B")
    print(f"辞書ロード後からの増分 (最大)    : {max(ha) - hb[0]:>12,d} B")

    print("\n--- Q4: 最長文 / 最大ノード文 ---")
    imax_c = chars.index(max(chars))
    imax_n = tot.index(max(tot))
    for lbl, i in (("最長文 (文字数最大)", imax_c), ("ノード数最大の文", imax_n)):
        print(f"{lbl}: idx={rows[i][0]} chars={chars[i]} mecab_bytes={mbytes[i]} "
              f"nodes={tot[i]} (dict={nd[i]} unk={nu[i]}) njd={njd[i]} labels={lab[i]} "
              f"max_at_pos={mx[i]}")
        print(f"    本文: {texts[rows[i][0]]}")

    print("\n--- 算術 (measurement ではない): ノード数 × sizeof ---")
    for szname, sz in (("host arm64 sizeof(Node)=112 B", 112),):
        print(f"  {szname}")
        print(f"    mean  {sum(tot)/len(tot)*sz:>12,.0f} B")
        print(f"    p95   {pct(tot,95)*sz:>12,.0f} B")
        print(f"    max   {max(tot)*sz:>12,.0f} B")
    return dict(hdr=hdr, rows=rows, tot=tot, chars=chars, njd=njd, lab=lab, mor=mor,
                mx=mx, ha=ha, hb=hb)


texts = open(os.path.join(_WORK, "heldout_text.txt"), encoding="utf-8").read().splitlines()
a = report("辞書 A: .venv の pyopenjtalk (sys.dic 103,131,410 B)",
           os.path.join(_WORK, "probe_venv.tsv"), texts)
b = report("辞書 B: piper-plus build/share (sys.dic 103,082,017 B / entries.tsv の出所)",
           os.path.join(_WORK, "probe_pp.tsv"), texts)

print("\n" + "=" * 100)
print("### 2 辞書の差")
print("=" * 100)
diff_nodes = sum(1 for x, y in zip(a["tot"], b["tot"]) if x != y)
diff_lab = sum(1 for x, y in zip(a["lab"], b["lab"]) if x != y)
print(f"lattice ノード数が違う文: {diff_nodes} / {len(a['tot'])}")
print(f"ラベル数が違う文        : {diff_lab} / {len(a['lab'])}")
print(f"A の合計ノード {sum(a['tot']):,d} / B の合計ノード {sum(b['tot']):,d}")
