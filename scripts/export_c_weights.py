#!/usr/bin/env python3
"""生徒の重みを C99 コア用のフラットなバイナリに書き出す（Phase D）。

形式は**自己記述的**にする。ヘッダにテンソルの名前・shape・dtype・offset を持ち、
C 側は名前で引く。**C とスクリプトで shape を二重に書かない**
（片方だけ直して黙ってずれるのを防ぐ）。

```
magic "SAAN" | version u32 | n_tensors u32 | header_bytes u32
[ name(64B, NUL 終端) | dtype u32 | ndim u32 | dims[4] u32 | offset u64 | nbytes u64 ] × n
payload（16 B 境界に揃える）
```

dtype: 0 = fp32 / 1 = int8 / 2 = fp32 scale（int8 の per-output-channel）

実行:
    uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt \
        --out csrc/student.bin --golden csrc/golden.bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import struct
import sys

import numpy as np
import torch

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration  # noqa: E402
from saanotts_jp.vocab import V as VOCAB  # noqa: E402

MAGIC = b"SAAN"
VERSION = 1
NAME_LEN = 64
DT_F32, DT_I8, DT_SCALE = 0, 1, 2


class Writer:
    def __init__(self) -> None:
        self.entries: list[tuple[str, int, tuple[int, ...], bytes]] = []

    def add(self, name: str, arr: np.ndarray, dtype: int) -> None:
        if len(name.encode()) >= NAME_LEN:
            raise ValueError(f"名前が長すぎる: {name}")
        if arr.ndim > 4:
            raise ValueError(f"{name}: ndim {arr.ndim} > 4")
        self.entries.append((name, dtype, tuple(arr.shape),
                             np.ascontiguousarray(arr).tobytes()))

    def write(self, path: pathlib.Path) -> dict:
        n = len(self.entries)
        ent_size = NAME_LEN + 4 + 4 + 16 + 8 + 8
        header_bytes = 16 + n * ent_size
        pad = (-header_bytes) % 16
        header_bytes += pad

        blobs, offsets, off = [], [], header_bytes
        for _, _, _, b in self.entries:
            offsets.append(off)
            blobs.append(b)
            off += len(b) + (-len(b)) % 16

        buf = bytearray()
        buf += MAGIC + struct.pack("<III", VERSION, n, header_bytes)
        for (name, dt, dims, b), o in zip(self.entries, offsets, strict=True):
            nm = name.encode().ljust(NAME_LEN, b"\0")
            d4 = list(dims) + [0] * (4 - len(dims))
            buf += nm + struct.pack("<II4IQQ", dt, len(dims), *d4, o, len(b))
        buf += b"\0" * pad
        for b in blobs:
            buf += b + b"\0" * ((-len(b)) % 16)

        path.write_bytes(bytes(buf))
        return {"path": str(path), "n_tensors": n, "bytes": len(buf),
                "sha256": hashlib.sha256(bytes(buf)).hexdigest()}


def quantize(w: torch.Tensor):
    """symmetric int8 / per-output-channel（`quantize_student.py` と同一）。"""
    flat = w.reshape(w.shape[0], -1).to(torch.float32)
    scale = flat.abs().amax(dim=1) / 127.0
    scale = torch.where(scale == 0, torch.ones_like(scale), scale)
    q = torch.clamp(torch.round(flat / scale[:, None]), -127, 127).to(torch.int8)
    return q.reshape(w.shape).numpy(), scale.numpy().astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", default="csrc/student.bin")
    ap.add_argument("--golden", default="csrc/golden.bin")
    ap.add_argument("--int8", action="store_true",
                    help="重みを int8 にする（既定は fp32。まず fp32 で C を通す）")
    ap.add_argument("--golden-text", default="今日は良い天気ですね。")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    models = {}
    for name, cls in (("duration", Duration), ("acoustic", Acoustic),
                      ("decoder", Decoder)):
        m = cls(); m.load_state_dict(ck["state"][name]); m.eval()
        models[name] = m

    w = Writer()
    for mod, m in models.items():
        for k, v in m.state_dict().items():
            full = f"{mod}.{k}"
            # 埋め込み・1-D（bias / LayerNorm / LayerScale）は fp32 のまま（論文の指定）
            if args.int8 and v.dim() >= 2 and "emb" not in k and "pos" not in k:
                q, sc = quantize(v)
                w.add(full, q, DT_I8)
                w.add(full + ".scale", sc, DT_SCALE)
            else:
                w.add(full, v.numpy().astype(np.float32), DT_F32)
    meta = w.write(pathlib.Path(args.out))

    # --- ゴールデン: 1 文の中間出力を全部落とす（C 側の突き合わせ用） ---
    import gen_teacher_labels as G
    import kana_g2p as K
    from saanotts_jp.vocab import map_ids

    table = K.build_mora_table(); G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
    ids = map_ids(G.encode_intermediate(
        K.text_to_intermediate(args.golden_text, table), pim))

    S_V, CLIP_LO, CLIP_HI = 1.2187, 1, 80
    x = torch.from_numpy(ids).long()[None]
    with torch.no_grad():
        log_d = models["duration"](x)
        r = torch.exp(log_d)
        d_hat = torch.clamp(torch.round(S_V * r), CLIP_LO, CLIP_HI).long()
        c = models["acoustic"](x, d_hat)
        mag, cos, sin = models["decoder"](c)
        pcm = Decoder.istft(mag, cos, sin)

    g = Writer()
    g.add("in.ids", ids.astype(np.int32).astype(np.float32), DT_F32)
    g.add("out.log_d", log_d[0].numpy(), DT_F32)
    g.add("out.d_hat", d_hat[0].numpy().astype(np.float32), DT_F32)
    g.add("out.c", c[0].numpy(), DT_F32)
    g.add("out.mag", mag[0].numpy(), DT_F32)
    g.add("out.cos", cos[0].numpy(), DT_F32)
    g.add("out.sin", sin[0].numpy(), DT_F32)
    g.add("out.pcm", pcm[0].numpy(), DT_F32)
    gmeta = g.write(pathlib.Path(args.golden))

    rep = {
        "ckpt": args.ckpt, "vocab": VOCAB, "int8": args.int8,
        "weights": meta, "golden": {**gmeta, "text": args.golden_text,
                                    "n_ids": len(ids), "frames": int(d_hat.sum()),
                                    "samples": int(pcm.shape[-1])},
        "s_v": S_V, "clip": [CLIP_LO, CLIP_HI],
        "format": "SAAN v1。ヘッダに name/dtype/shape/offset を持つ自己記述形式",
        "repro": f"uv run python scripts/export_c_weights.py --ckpt {args.ckpt}",
    }
    pathlib.Path("csrc/export.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"重み  : {meta['n_tensors']:>3} tensor / {meta['bytes']:>9,} B  "
          f"sha256 {meta['sha256'][:16]}…")
    print(f"golden: {gmeta['n_tensors']:>3} tensor / {gmeta['bytes']:>9,} B  "
          f"「{args.golden_text}」 {len(ids)} ids → {int(d_hat.sum())} frames "
          f"→ {int(pcm.shape[-1])} sample ({pcm.shape[-1]/22050:.3f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
