"""1 文あたりヒープの内訳を、コード上の確保と突き合わせて確認する。

jpcommon_label.c:666-668 は label 1 本につき calloc(MAXBUFLEN=1024) している。
これが per-sentence の支配項かどうかを回帰で確かめる。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")

with open(os.path.join(_WORK, "probe_venv.tsv"), encoding="utf-8") as f:
    hdr = f.readline().rstrip("\n").split("\t")
    rows = [[int(x) for x in ln.rstrip("\n").split("\t")] for ln in f if ln.strip()]
C = {k: [r[hdr.index(k)] for r in rows] for k in hdr}
n = len(rows)

d_lab_only = [a - b for a, b in zip(C["heap_at_label"], C["heap_at_njd"])]
pred = [l * 1024 for l in C["labels"]]

print("ラベル生成段階のヒープ増分 vs labels × 1024 B")
print(f"{'idx':>5} {'labels':>7} {'Δheap(label段)':>15} {'labels×1024':>12} {'差':>10}")
for i in (0, 1, 100, 1000, C["labels"].index(max(C["labels"]))):
    print(f"{i:>5} {C['labels'][i]:>7} {d_lab_only[i]:>15,d} {pred[i]:>12,d} "
          f"{d_lab_only[i]-pred[i]:>10,d}")

resid = [a - b for a, b in zip(d_lab_only, pred)]
print(f"\n残差 (Δheap − labels×1024): mean {sum(resid)/n:,.0f} B  "
      f"min {min(resid):,d}  max {max(resid):,d}")
frac = [p / a for p, a in zip(pred, d_lab_only) if a > 0]
print(f"labels×1024 が Δheap に占める割合: mean {sum(frac)/len(frac):.4f} "
      f"min {min(frac):.4f} max {max(frac):.4f}  (n={len(frac)})")

# 相関（形だけの指標なので残差の絶対値も併記する — writing-gates §1）
mx = sum(pred) / n
my = sum(d_lab_only) / n
cov = sum((p - mx) * (a - my) for p, a in zip(pred, d_lab_only))
sx = sum((p - mx) ** 2 for p in pred) ** 0.5
sy = sum((a - my) ** 2 for a in d_lab_only) ** 0.5
print(f"Pearson r = {cov/(sx*sy):.6f}   (⚠️ 相関だけでは不十分なので残差も上に出した)")

# --- 陰性対照: 無関係な列と比べると相関も残差も壊れること -----------------
bogus = [c * 1024 for c in C["in_chars"]]
mb = sum(bogus) / n
covb = sum((p - mb) * (a - my) for p, a in zip(bogus, d_lab_only))
sb = sum((p - mb) ** 2 for p in bogus) ** 0.5
rb = covb / (sb * sy)
residb = [a - b for a, b in zip(d_lab_only, bogus)]
print(f"\n陰性対照 (入力文字数×1024 で説明を試みる): r={rb:.6f} "
      f"残差 mean {sum(residb)/n:,.0f} B  max {max(residb):,d}")
print("  → 残差が labels×1024 のときより桁違いに大きければ、説明が本物である証拠")

print("\n--- lattice プールの固定費 ---")
print(f"辞書ロード直後            : {C['heap_before'][0]:>10,d} B")
print(f"1 文目 parse 直後         : {C['heap_at_lattice'][0]:>10,d} B  "
      f"(Δ {C['heap_at_lattice'][0]-C['heap_before'][0]:,d} B)")
print(f"以降 Δparse が 0 の文の割合: "
      f"{sum(1 for x in (a-b for a,b in zip(C['heap_at_lattice'],C['heap_before'])) if x==0)}/{n}")
nz = [(i, a - b) for i, (a, b) in enumerate(zip(C["heap_at_lattice"], C["heap_before"])) if a - b > 0]
print(f"Δparse > 0 だった文: {len(nz)} 件 -> {nz[:10]}")
print(f"  (FreeList が新しいチャンクを確保した瞬間だけ増える = 文をまたいだプール)")
