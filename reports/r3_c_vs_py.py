"""R-3: csrc/student.bin の decoder テンソル shape が _param_reference.Decoder と
一致するかを機械的に照合する（名前・shape・要素数）。"""
import struct, pathlib, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import torch
from saanotts_jp._param_reference import Decoder

NAME_LEN = 64
b = pathlib.Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                              "csrc", "student.bin")).read_bytes()
ver, n, hb = struct.unpack("<III", b[4:16])
ent = NAME_LEN + 4 + 4 + 16 + 8 + 8
blob = {}
for i in range(n):
    o = 16 + i*ent
    nm = b[o:o+NAME_LEN].split(b"\0")[0].decode()
    dt, nd = struct.unpack("<II", b[o+NAME_LEN:o+NAME_LEN+8])
    d4 = struct.unpack("<4I", b[o+NAME_LEN+8:o+NAME_LEN+24])
    blob[nm] = d4[:nd]

py = {("decoder." + k): tuple(v.shape) for k, v in Decoder().state_dict().items()}
bl = {k: v for k, v in blob.items() if k.startswith("decoder.")}
print(f"python Decoder テンソル {len(py)} / blob decoder テンソル {len(bl)}")
only_py = sorted(set(py) - set(bl)); only_bl = sorted(set(bl) - set(py))
print("python にしかない:", only_py)
print("blob にしかない  :", only_bl)
mism = [(k, py[k], bl[k]) for k in sorted(set(py) & set(bl)) if tuple(py[k]) != tuple(bl[k])]
print("shape 不一致:", mism if mism else "なし")
npy = sum(int(torch.tensor(list(s)).prod()) for s in py.values())
nbl = sum(int(torch.tensor(list(s)).prod()) for s in bl.values())
print(f"要素数 python {npy:,} / blob {nbl:,}  一致={npy==nbl}")
print("OK" if not only_py and not only_bl and not mism and npy == nbl else "NG")
# 壊して落ちることの確認: わざと 1 テンソル落とした場合
bl2 = dict(bl); bl2.pop("decoder.hout.weight")
print("[破壊テスト] hout.weight を落とすと差分検出:",
      bool(set(py) - set(bl2)))
