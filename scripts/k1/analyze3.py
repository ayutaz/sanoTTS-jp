"""最終集計 + ゲート。CPS ヒット / ノード / 512 上限 / lookup 回数。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import sys

SP = (_WORK + "")


def load(path):
    with open(path, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        rows = [[int(x) for x in ln.rstrip("\n").split("\t")] for ln in f if ln.strip()]
    return {k: [r[hdr.index(k)] for r in rows] for k in hdr}, len(rows)


def pct(xs, p):
    s = sorted(xs); k = (len(s) - 1) * p / 100.0
    lo = int(k); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(name, xs, unit=""):
    n = len(xs)
    print(f"  {name:<28s} mean={sum(xs)/n:>9.2f} med={pct(xs,50):>8.1f} "
          f"p95={pct(xs,95):>8.1f} p99={pct(xs,99):>8.1f} max={max(xs):>8d} min={min(xs):>6d} {unit}")


texts = open(os.path.join(_WORK, "heldout_text.txt"), encoding="utf-8").read().splitlines()
fails = []

for tag, fn in (("A: .venv pyopenjtalk (sys.dic 103,131,410 B)", "probe_venv.tsv"),
                ("B: piper-plus build/share (sys.dic 103,082,017 B)", "probe_pp.tsv")):
    C, n = load(os.path.join(_WORK, fn))
    print("=" * 92)
    print(f"### {tag}   n={n} 文")
    print("=" * 92)
    dist("入力 文字数", C["in_chars"], "codepoint")
    dist("text2mecab 後バイト数", C["mecab_bytes"], "B")
    print()
    dist("CPS ヒット数 (key)", C["cps_hits"], "key")
    dist("lookup() 呼び出し回数", C["lookup_calls"], "call")
    dist("1 CPS の最大ヒット数", C["cps_max"], "key")
    print()
    dist("lattice ノード合計", C["nodes_total"], "node")
    dist("  辞書ノード", C["nodes_dict"], "node")
    dist("  未知語ノード", C["nodes_unk"], "node")
    ratio = [d / h for d, h in zip(C["nodes_dict"], C["cps_hits"]) if h > 0]
    print(f"  {'辞書ノード / CPS ヒット':<28s} mean={sum(ratio)/len(ratio):>9.2f} "
          f"med={pct(ratio,50):>8.2f} max={max(ratio):>8.2f}   "
          f"(1 key に複数 Token がぶら下がる)")
    lc = [l / c for l, c in zip(C["lookup_calls"], C["in_chars"])]
    print(f"  {'lookup 回数 / 入力文字':<28s} mean={sum(lc)/n:>9.4f} min={min(lc):.4f} max={max(lc):.4f}")
    print()

    # --- ゲート ---
    print("  [ゲート]")
    g1 = max(C["cps_max"]) < 512
    print(f"   G1 CPS 結果が kResultsSize=512 で切り捨てられていない: "
          f"最大 {max(C['cps_max'])} < 512 -> {'PASS' if g1 else 'FAIL(ノードが黙って欠落)'}")
    if not g1:
        fails.append("G1")
    g2 = all(p == 0 for p in C["paths"])
    print(f"   G2 Path が 1 個も確保されていない (one-best): max={max(C['paths'])} "
          f"-> {'PASS' if g2 else 'FAIL'}")
    if not g2:
        fails.append("G2")
    g3 = all(b == 2 for b in C["nodes_bos_eos"])
    print(f"   G3 BOS/EOS がちょうど 2 個: -> {'PASS' if g3 else 'FAIL'}")
    if not g3:
        fails.append("G3")
    sums = [d + u + b for d, u, b in zip(C["nodes_dict"], C["nodes_unk"], C["nodes_bos_eos"])]
    g4 = sums == C["nodes_total"]
    nmis = sum(1 for a, b in zip(sums, C["nodes_total"]) if a != b)
    print(f"   G4 lattice 走査の内訳合計 == newNode() 呼び出し回数: 不一致 {nmis}/{n} "
          f"-> {'PASS' if g4 else 'FAIL'}")
    if not g4:
        fails.append("G4")
    # 陽性対照: 内訳をわざと 1 ずらすと G4 は落ちるか
    bad = [s + 1 for s in sums]
    nmis_bad = sum(1 for a, b in zip(bad, C["nodes_total"]) if a != b)
    print(f"      陽性対照 (内訳を +1 して同じ検査): 不一致 {nmis_bad}/{n} "
          f"-> {'PASS(検査は機能している)' if nmis_bad == n else 'FAIL(検査が空虚)'}")
    if nmis_bad != n:
        fails.append("G4-pos")
    g5 = min(C["nodes_total"]) > 0 and min(C["labels"]) > 0
    print(f"   G5 すべての文でノードとラベルが 1 以上: nodes_min={min(C['nodes_total'])} "
          f"labels_min={min(C['labels'])} -> {'PASS' if g5 else 'FAIL'}")
    if not g5:
        fails.append("G5")
    # 陰性対照: 全部 0 のダミー列は G5 に落ちること
    print(f"      陰性対照 (ダミーの全 0 列): "
          f"{'PASS(落ちる)' if not (min([0]*n) > 0) else 'FAIL'}")
    print()

print("=" * 92)
print(f"総合ゲート: {'PASS' if not fails else 'FAIL ' + ','.join(fails)}")
sys.exit(0 if not fails else 1)
