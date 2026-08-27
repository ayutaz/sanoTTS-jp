"""R-3: 「チャネル切り出し (channel slicing) で初期化できるか」を機械的に判定する。

判定規則（conv 重み `(out, in/groups, k)`）:
  生徒テンソル S が 教師テンソル T の切り出しになれる ⇔ 同じ ndim、
  カーネル長が一致（k_S == k_T）、かつ全次元 dim_S <= dim_T。
kernel が違えば「細くする」だけでは作れない（時間方向の受容野が変わる）。
"""
import glob, torch, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from saanotts_jp._param_reference import Decoder

snap = glob.glob("/Users/s19447/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/")[0]
sd = torch.load(snap + "epoch=499-step=22000.ckpt", map_location="cpu",
                weights_only=False)["state_dict"]
T = {}
for k, v in sd.items():
    if not k.startswith("model_g.dec."): continue
    n = k[len("model_g.dec."):]
    if n.startswith(("pqmf.", "istft.")): continue          # 非パラメータ buffer
    if n.endswith("weight_g"): continue                     # weight_norm の g（融合で消える）
    T[n.replace("weight_v", "weight")] = tuple(v.shape)

S = {k: tuple(v.shape) for k, v in Decoder().state_dict().items()}
Sw = {k: s for k, s in S.items() if len(s) == 3}            # conv 重みだけ
Tw = {k: s for k, s in T.items() if len(s) == 3}
print(f"教師 dec の conv 重み {len(Tw)} 本 / 生徒 Gγ の conv 重み {len(Sw)} 本\n")

def sliceable(s, t):
    return len(s) == len(t) and s[2] == t[2] and all(a <= b for a, b in zip(s, t))

rows = []
for sn, ss in Sw.items():
    cands = [tn for tn, ts in Tw.items() if sliceable(ss, ts)]
    rows.append((sn, ss, cands))
print(f"{'生徒テンソル':24s} {'shape':16s} 切り出し可能な教師テンソル")
nz = 0
for sn, ss, c in rows:
    print(f"{sn:24s} {str(ss):16s} {c if c else '— なし'}")
    if c: nz += 1
print(f"\n形状として切り出せる生徒テンソル: {nz}/{len(rows)}")
print("教師側の候補になった層:", sorted({t for _,_,c in rows for t in c}))
print("\n教師 conv のカーネル長の分布:",
      sorted({s[2] for s in Tw.values()}))
print("生徒 conv のカーネル長の分布:", sorted({s[2] for s in Sw.values()}))
