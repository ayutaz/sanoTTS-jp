"""R-3: 生徒 Gγ の層ごとパラメータ数を実測し、csrc の重みブロブと照合する。"""
import sys, os, struct, json, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import torch
from saanotts_jp._param_reference import Decoder, Acoustic, Duration, Erho

g = Decoder()
rows = collections.OrderedDict()
for n, p in g.named_parameters():
    rows[n] = (tuple(p.shape), p.numel())
tot = sum(v[1] for v in rows.values())
print("=== 生徒 Gγ（_param_reference.Decoder）層ごと ===")
grp = collections.OrderedDict()
for n, (s, c) in rows.items():
    key = n.split(".")[0]
    grp.setdefault(key, [0, []])
    grp[key][0] += c
    grp[key][1].append((n, s, c))
for k, (c, items) in grp.items():
    print(f"{k:8s} {c:8,d}")
    for n, s, cc in items:
        print(f"         {n:22s} {str(s):20s} {cc:8,d}")
print(f"{'TOTAL':8s} {tot:8,d}   (論文 Table I 331,308 との差 {tot-331308:+d})")

# ---- MAC/audio-second（フレームレート 86.133 fps、hop256@22050）----
FPS = 22050.0 / 256.0
W, E, K, R, CIN, r, OUT = 76, 304, 7, 12, 40, 48, 1539
mac = {}
mac["inp Conv1d(40,76,k=3)"] = CIN*W*3
mac["dw x5"] = 5*(W*K)
mac["pw1 x5"] = 5*(W*E)
mac["pw2 x5"] = 5*(E*W)
mac["cdown x5"] = 5*(CIN*R)
mac["cup x5"] = 5*(R*W)
mac["hdown"] = W*r
mac["hout"] = r*OUT
per_frame = sum(mac.values())
print("\n=== 生徒 Gγ の MAC/frame と MMAC/audio-second（重み数＝MAC/frame、bias 除く）===")
for k, v in mac.items():
    print(f"  {k:26s} {v:9,d} MAC/frame   {v*FPS/1e6:7.3f} MMAC/s")
print(f"  {'合計':26s} {per_frame:9,d} MAC/frame   {per_frame*FPS/1e6:7.3f} MMAC/s")

# ---- csrc/student.bin のヘッダを読んで decoder テンソルを一覧（SAAN v1 自己記述）----
print("\n=== csrc/student.bin の decoder テンソル（照合）===")
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "csrc", "student.bin")
blob = open(p, "rb").read()
print("  magic:", blob[:8])
