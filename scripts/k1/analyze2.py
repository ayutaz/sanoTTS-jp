"""Q2/Q3/Q4 の最終集計。ヒープの段階別ピークも出す。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")


def load(path):
    with open(path, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        rows = [[int(x) for x in ln.rstrip("\n").split("\t")] for ln in f if ln.strip()]
    return hdr, rows


def pct(xs, p):
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo = int(k); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(name, xs, unit=""):
    n = len(xs)
    print(f"  {name:<26s} n={n:<5d} mean={sum(xs)/n:>9.2f} med={pct(xs,50):>8.1f} "
          f"p95={pct(xs,95):>8.1f} p99={pct(xs,99):>8.1f} max={max(xs):>8d} min={min(xs):>6d} {unit}")


texts = open(os.path.join(_WORK, "heldout_text.txt"), encoding="utf-8").read().splitlines()

for tag, fn in (("A: .venv pyopenjtalk 辞書 (sys.dic 103,131,410 B)", "probe_venv.tsv"),
                ("B: piper-plus build/share 辞書 (sys.dic 103,082,017 B)", "probe_pp.tsv")):
    hdr, rows = load(os.path.join(_WORK, fn))
    C = {k: [r[hdr.index(k)] for r in rows] for k in hdr}
    n = len(rows)
    print("=" * 96)
    print(f"### {tag}    n={n} 文")
    print("=" * 96)

    print("\n[入力]")
    dist("入力 文字数", C["in_chars"], "codepoint")
    dist("text2mecab 後バイト数", C["mecab_bytes"], "B")

    print("\n[Q2] Viterbi lattice ノード数 (実測 = Allocator::newNode() 呼び出し回数)")
    dist("ノード合計", C["nodes_total"], "node")
    dist("  辞書ノード", C["nodes_dict"], "node")
    dist("  未知語ノード", C["nodes_unk"], "node")
    dist("  BOS/EOS", C["nodes_bos_eos"], "node")
    dist("Path オブジェクト", C["paths"], "path")
    dist("1 位置あたり最大ノード", C["max_nodes_at_pos"], "node")
    pc = [t / c for t, c in zip(C["nodes_total"], C["in_chars"])]
    print(f"  {'ノード/入力文字':<26s} n={n:<5d} mean={sum(pc)/n:>9.2f} med={pct(pc,50):>8.2f} "
          f"p95={pct(pc,95):>8.2f} p99={pct(pc,99):>8.2f} max={max(pc):>8.2f}")
    pb = [t / b for t, b in zip(C["nodes_total"], C["mecab_bytes"])]
    print(f"  {'ノード/入力バイト':<26s} n={n:<5d} mean={sum(pb)/n:>9.2f} med={pct(pb,50):>8.2f} "
          f"p95={pct(pb,95):>8.2f} max={max(pb):>8.2f}")

    print("\n[Q3] NJD / JPCommon (実測)")
    dist("形態素数 (best path)", C["morphs"], "morph")
    dist("NJD ノード数", C["njd_nodes"], "node")
    dist("フルコンテキストラベル数", C["labels"], "label")

    print("\n[ヒープ] malloc size_in_use (実測・C プロセスのみ / Python 不介在)")
    base = C["heap_before"][0]
    print(f"  辞書ロード直後 (1 文目の直前)      : {base:>10,d} B")
    d_lat = [a - b for a, b in zip(C["heap_at_lattice"], C["heap_before"])]
    d_njd = [a - b for a, b in zip(C["heap_at_njd"], C["heap_before"])]
    d_lab = [a - b for a, b in zip(C["heap_at_label"], C["heap_before"])]
    dist("Δ parse 直後 (lattice)", d_lat, "B")
    dist("Δ NJD 構築後", d_njd, "B")
    dist("Δ ラベル生成後 (段階ピーク)", d_lab, "B")
    print(f"  高水位 heap_at_label の最大        : {max(C['heap_at_label']):>10,d} B")
    print(f"  最終 (2333 文終了時)               : {C['heap_after'][-1]:>10,d} B")
    print(f"  高水位 − 辞書ロード直後            : {max(C['heap_at_label']) - base:>10,d} B")

    print("\n[Q4] 最長文 / 最大ノード文")
    i_c = C["in_chars"].index(max(C["in_chars"]))
    i_n = C["nodes_total"].index(max(C["nodes_total"]))
    i_l = C["labels"].index(max(C["labels"]))
    for lbl, i in (("最長文", i_c), ("最大ノード文", i_n), ("最大ラベル文", i_l)):
        print(f"  {lbl}: idx={C['idx'][i]} chars={C['in_chars'][i]} bytes={C['mecab_bytes'][i]} "
              f"nodes={C['nodes_total'][i]} njd={C['njd_nodes'][i]} labels={C['labels'][i]} "
              f"Δheap_label={d_lab[i]:,d} B")
        print(f"      {texts[C['idx'][i]][:70]}")

    print("\n[算術 — 実測ではない] ノード数 × sizeof(mecab_node_t)")
    for arch, sz in (("host arm64 (実測 112 B)", 112), ("ESP32-S3 xtensa (実測 72 B)", 72)):
        t = C["nodes_total"]
        print(f"  {arch}: mean {sum(t)/n*sz:>10,.0f} B / p95 {pct(t,95)*sz:>10,.0f} B "
              f"/ max {max(t)*sz:>10,.0f} B")
    print("  FreeList はチャンク単位 (NODE_FREELIST_SIZE=512 ノード) で確保し、"
          "文をまたいで解放しない")
    for arch, sz in (("host arm64", 112), ("ESP32-S3", 72)):
        mx = max(C["nodes_total"])
        chunks = -(-mx // 512)
        print(f"    {arch}: max {mx} node -> チャンク {chunks} 個 × 512 × {sz} B "
              f"= {chunks*512*sz:,d} B")
    print("  begin_nodes_/end_nodes_ (Node* の vector 2 本, len+4 要素)")
    for arch, ps in (("host arm64", 8), ("ESP32-S3", 4)):
        mb = max(C["mecab_bytes"])
        print(f"    {arch}: 2 × {ps} B × ({mb}+4) = {2*ps*(mb+4):,d} B  (最長文)")
    print()
