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

**version 2（S4、2026-09-02）**: int8 の conv 重み `[cout][cin][k]` は payload を
**`[cout][k][align16(cin)]` に並べ替えて 0 埋め**して書く（dims は `(cout, cin, k)` のまま。
nbytes = cout·k·cinp）。cin == 1（depthwise）はバイト列が同じなので padding しない。
C 側（csrc/saanotts_int8.c）は転置コピー無しで PIE の `ee.vld.128.ip` に直接渡せる。
⚠️ v1 の int8 blob（v0.2.0 以前のリリース）は C 側が SAAN_ERR_VERSION で拒む。
   fp32 だけの v1 blob（golden / ids）は読める。

実行:
    uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt \
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
from saanotts_jp.ptq import dequantize, quantize_tensor  # noqa: E402
from saanotts_jp.vocab import V as VOCAB  # noqa: E402

MAGIC = b"SAAN"
VERSION = 2
NAME_LEN = 64
DT_F32, DT_I8, DT_SCALE = 0, 1, 2


class Writer:
    def __init__(self) -> None:
        self.entries: list[tuple[str, int, tuple[int, ...], bytes]] = []

    def add(self, name: str, arr: np.ndarray, dtype: int,
            dims: tuple[int, ...] | None = None) -> None:
        """`dims` を渡すとヘッダの shape はそれ、payload は `arr` のバイト列になる
        （v2 の packed conv: shape は論理形 (cout, cin, k)、payload は [cout][k][cinp]）。"""
        if len(name.encode()) >= NAME_LEN:
            raise ValueError(f"名前が長すぎる: {name}")
        shape = tuple(arr.shape) if dims is None else tuple(dims)
        if len(shape) > 4:
            raise ValueError(f"{name}: ndim {len(shape)} > 4")
        self.entries.append((name, dtype, shape,
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


# 量子化の規則は `src/saanotts_jp/ptq.py`（唯一の定義）。ここに式を書き写さない。


def conv_stride_v2(cin: int) -> int:
    """C 側の SAAN_W_STRIDE(cin) と同じ規則: cin == 1 は 1、それ以外は align16(cin)。"""
    return 1 if cin == 1 else (cin + 15) // 16 * 16


def pack_conv_v2(q2d: np.ndarray, cout: int, cin: int, ksz: int) -> np.ndarray:
    """`[cout, cin*k]`（quantize_tensor の出力）→ v2 payload `[cout][k][cinp]`（0 埋め）。

    ⚠️ **値の並べ替えだけ。** 量子化の規則も丸めも触らない。C 側の `saan_pack_w_i8()` と
    同じ配置でなければならない（int8_test 2c がバイト単位で突き合わせる）。"""
    cinp = conv_stride_v2(cin)
    q3 = np.ascontiguousarray(q2d).reshape(cout, cin, ksz).transpose(0, 2, 1)   # [cout][k][cin]
    packed = np.zeros((cout, ksz, cinp), dtype=np.int8)
    packed[:, :, :cin] = q3
    return packed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", default="csrc/student.bin")
    ap.add_argument("--golden", default="csrc/golden.bin")
    ap.add_argument("--int8", action="store_true",
                    help="重みを int8 にする（既定は fp32。まず fp32 で C を通す）")
    ap.add_argument("--golden-from-quantized", action="store_true",
                    help="golden を **fake-quant した重み**で計算する（--int8 が必要）。"
                         "int8 経路の C を突き合わせる相手はこちらでないと、"
                         "層を 1 つ fp32 に置き忘れても量子化誤差に紛れて検出できない")
    ap.add_argument("--report", default="csrc/export.json",
                    help="メタ情報の書き出し先。**int8 の実行で fp32 の記録を潰さない**"
                         "（fp32: csrc/export.json / int8: csrc/export_i8.json）")
    ap.add_argument("--golden-text", default="今日は良い天気ですね。")
    args = ap.parse_args()
    if args.golden_from_quantized and not args.int8:
        raise SystemExit("--golden-from-quantized は --int8 と一緒に使う")

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    models = {}
    for name, cls in (("duration", Duration), ("acoustic", Acoustic),
                      ("decoder", Decoder)):
        m = cls(); m.load_state_dict(ck["state"][name]); m.eval()
        models[name] = m

    w = Writer()
    # blob に int8 で載せたものを控える。**fake-quant golden はこの集合をそのまま使う** —
    # 規則を 2 度書くと「blob は int8 なのに golden は fp32」がここでずれる
    faked: dict[str, dict[str, torch.Tensor]] = {}
    for mod, m in models.items():
        for k, v in m.state_dict().items():
            full = f"{mod}.{k}"
            # 埋め込み・1-D（bias / LayerNorm / LayerScale）は fp32 のまま（論文の指定）
            if args.int8 and v.dim() >= 2 and "emb" not in k and "pos" not in k:
                q2d, sc = quantize_tensor(v)
                if v.dim() != 3:
                    raise SystemExit(f"{full}: int8 の conv 重み以外（ndim {v.dim()}）は "
                                     f"v2 のレイアウト規則を決めていない")
                cout, cin, ksz = (int(d) for d in v.shape)
                w.add(full, pack_conv_v2(q2d, cout, cin, ksz), DT_I8, dims=(cout, cin, ksz))
                w.add(full + ".scale", sc, DT_SCALE)
                faked.setdefault(mod, {})[k] = dequantize(q2d, sc, v.shape)
            else:
                w.add(full, v.numpy().astype(np.float32), DT_F32)
    meta = w.write(pathlib.Path(args.out))

    n_faked = 0
    if args.golden_from_quantized:
        # ⚠️ **blob を書いたあとに書き戻す。** 順序を逆にしても値は同じ
        # （量子化は冪等）だが、blob が「量子化の一次ソース」であることを崩さない
        for mod, tensors in faked.items():
            sd = models[mod].state_dict()
            sd.update(tensors)
            models[mod].load_state_dict(sd)
            n_faked += len(tensors)
        assert n_faked == sum(len(t) for t in faked.values())

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
        "golden_from_quantized": args.golden_from_quantized,
        "n_tensors_faked": n_faked,
        "weights": meta, "golden": {**gmeta, "text": args.golden_text,
                                    "n_ids": len(ids), "frames": int(d_hat.sum()),
                                    "samples": int(pcm.shape[-1])},
        "s_v": S_V, "clip": [CLIP_LO, CLIP_HI],
        "format": "SAAN v2。ヘッダに name/dtype/shape/offset を持つ自己記述形式。"
                  "int8 conv 重みの payload は [cout][k][align16(cin)] で 0 埋め"
                  "（cin==1 の depthwise は padding 無し）。v1 との差はこのレイアウトだけ",
        "repro": ("uv run python scripts/export_c_weights.py"
                  f" --ckpt {args.ckpt} --out {args.out} --golden {args.golden}"
                  + (" --int8" if args.int8 else "")
                  + (" --golden-from-quantized" if args.golden_from_quantized else "")
                  + f" --report {args.report}"),
    }
    pathlib.Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"重み  : {meta['n_tensors']:>3} tensor / {meta['bytes']:>9,} B  "
          f"sha256 {meta['sha256'][:16]}…")
    if args.golden_from_quantized:
        print(f"golden は fake-quant（{n_faked} テンソルを逆量子化して書き戻し）")
    print(f"golden: {gmeta['n_tensors']:>3} tensor / {gmeta['bytes']:>9,} B  "
          f"「{args.golden_text}」 {len(ids)} ids → {int(d_hat.sum())} frames "
          f"→ {int(pcm.shape[-1])} sample ({pcm.shape[-1]/22050:.3f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
