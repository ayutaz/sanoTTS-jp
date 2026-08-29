#!/usr/bin/env python3
"""Phase 6: 生徒を int8 に量子化し、blob サイズと品質劣化を測る。

論文の方式（`sanoTTS.txt:261-264`）:

* **symmetric int8 / per-output-channel** の重み
* activations は per-frame
* **Embeddings / normalization affines / iSTFT support code は fp32 のまま**

**int8 blob のバイト数が層構成の検算になる。**
論文は 2 blob で `280,288` + `399,544` = **679,832 B**（英語・語彙 157）。
日本語は語彙 57 なので埋め込みが 100 行少ない。

⚠️ **ここでやるのは PTQ（学習後量子化）のシミュレーション**であって、
ESP32 のカーネルではない。「int8 に落としても品質がどれだけ落ちるか」と
「実際に何バイトになるか」を測るのが目的。

実行:
    uv run python scripts/quantize_student.py --ckpt runs/v3/stage4.pt \
        --out reports/quant_v2
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, "src")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration  # noqa: E402
from saanotts_jp.ptq import dequantize, quantize_tensor  # noqa: E402
from saanotts_jp.vocab import V as VOCAB  # noqa: E402

#: fp32 のまま残すもの（論文の指定）
FP32_KINDS = (nn.Embedding, nn.LayerNorm)


# ⚠️ 量子化の規則は `src/saanotts_jp/ptq.py` に一本化した。
# ここと `scripts/export_c_weights.py` に同じ式を書き写さないこと。


def walk(module: nn.Module, prefix: str = ""):
    for name, child in module.named_children():
        yield from walk(child, f"{prefix}{name}.")
    for name, p in module.named_parameters(recurse=False):
        yield f"{prefix}{name}", p, module


def quantize_module(m: nn.Module) -> dict:
    """モジュールを量子化し、blob のバイト内訳を返す（重みは in-place で置換）。"""
    int8_bytes = fp32_bytes = 0
    n_int8 = n_fp32 = 0
    scale_bytes = 0
    detail = []
    with torch.no_grad():
        for name, p, owner in walk(m):
            keep_fp32 = (isinstance(owner, FP32_KINDS)
                         or p.dim() < 2          # bias / LayerScale gamma
                         )
            if keep_fp32:
                fp32_bytes += p.numel() * 4
                n_fp32 += p.numel()
                detail.append({"name": name, "n": p.numel(), "dtype": "fp32",
                               "why": ("embedding/norm" if isinstance(owner, FP32_KINDS)
                                       else "1-D (bias/scale)")})
                continue
            q, sc = quantize_tensor(p.data)
            p.data.copy_(dequantize(q, sc, p.shape))
            int8_bytes += q.size
            scale_bytes += sc.size * 4
            n_int8 += p.numel()
            detail.append({"name": name, "n": p.numel(), "dtype": "int8",
                           "out_channels": int(sc.size)})
    return {"int8_weight_bytes": int8_bytes, "scale_bytes": scale_bytes,
            "fp32_bytes": fp32_bytes,
            "total_bytes": int8_bytes + scale_bytes + fp32_bytes,
            "n_params_int8": n_int8, "n_params_fp32": n_fp32,
            "n_params": n_int8 + n_fp32, "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", default="reports/quant")
    ap.add_argument("--probe-utts", type=int, default=8)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    blobs, fp32_models, int8_models = {}, {}, {}
    for name, cls in (("duration", Duration), ("acoustic", Acoustic),
                      ("decoder", Decoder)):
        fp = cls(); fp.load_state_dict(ck["state"][name]); fp.eval()
        q = cls(); q.load_state_dict(ck["state"][name]); q.eval()
        blobs[name] = quantize_module(q)
        fp32_models[name], int8_models[name] = fp, q

    # 論文の 2 blob 構成: blob1 = duration + acoustic, blob2 = decoder
    b1 = blobs["duration"]["total_bytes"] + blobs["acoustic"]["total_bytes"]
    b2 = blobs["decoder"]["total_bytes"]
    PAPER = {"blob1": 280288, "blob2": 399544}

    # --- 量子化で出力がどれだけ動くか（同じ入力で fp32 と比較） ---
    rng = np.random.default_rng(0)
    probes = []
    for _ in range(args.probe_utts):
        n = int(rng.integers(30, 120))
        x = torch.from_numpy(rng.integers(0, VOCAB, size=(1, n))).long()
        with torch.no_grad():
            lf = fp32_models["duration"](x); lq = int8_models["duration"](x)
            d = torch.clamp(torch.round(torch.exp(lf)), 1, 80).long()
            cf = fp32_models["acoustic"](x, d); cq = int8_models["acoustic"](x, d)
            yf = Decoder.istft(*fp32_models["decoder"](cf))
            yq = Decoder.istft(*int8_models["decoder"](cq))

        def snr(a, b):
            return float(10 * torch.log10(a.pow(2).mean()
                                          / (a - b).pow(2).mean().clamp_min(1e-20)))
        probes.append({"n_ids": n, "log_d_snr_db": snr(lf, lq),
                       "c_snr_db": snr(cf, cq), "wave_snr_db": snr(yf, yq),
                       "d_exact_match": bool(torch.equal(
                           torch.clamp(torch.round(torch.exp(lf)), 1, 80),
                           torch.clamp(torch.round(torch.exp(lq)), 1, 80)))}) 

    rep = {
        "ckpt": args.ckpt,
        "scheme": "symmetric int8 / per-output-channel。"
                  "embedding・LayerNorm・1-D (bias/LayerScale) は fp32（論文の指定）",
        "vocab": VOCAB,
        "blobs": {k: {kk: vv for kk, vv in v.items() if kk != "detail"}
                  for k, v in blobs.items()},
        "two_blob_layout": {
            "blob1_duration_plus_acoustic": b1,
            "blob2_decoder": b2, "total": b1 + b2,
            "paper_en_vocab157": {**PAPER, "total": sum(PAPER.values())},
            "delta_vs_paper": {"blob1": b1 - PAPER["blob1"],
                               "blob2": b2 - PAPER["blob2"],
                               "total": (b1 + b2) - sum(PAPER.values())},
        },
        "quantization_error": {
            "n_probes": len(probes),
            "log_d_snr_db": float(np.mean([p["log_d_snr_db"] for p in probes])),
            "c_snr_db": float(np.mean([p["c_snr_db"] for p in probes])),
            "wave_snr_db": float(np.mean([p["wave_snr_db"] for p in probes])),
            "d_exact_match": sum(p["d_exact_match"] for p in probes),
            "probes": probes,
        },
        "warnings": [
            "**PTQ のシミュレーションであって ESP32 のカーネルではない。**"
            "実機の演算順序・丸めは別に検証が要る",
            "**プローブはランダムな音素ID列**。実テキストでの品質劣化は "
            "eval_student.py を int8 モデルで回して測ること",
            "論文の blob サイズは英語（語彙 157）。日本語は 57 なので"
            "埋め込みが 100 行少ない（fp32 なので差は大きい）",
        ],
        "repro": f"uv run python scripts/quantize_student.py --ckpt {args.ckpt} --out {args.out}",
    }
    (outdir / "quant.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    torch.save({"state": {k: m.state_dict() for k, m in int8_models.items()},
                "note": "fake-quantized (int8 に丸めた値を fp32 で保持)"},
               outdir / "student_int8_sim.pt")

    print(f"=== blob サイズ（語彙 {VOCAB}）===")
    for k, v in blobs.items():
        print(f"  {k:<10} int8 {v['int8_weight_bytes']:>7,} B + scale "
              f"{v['scale_bytes']:>6,} B + fp32 {v['fp32_bytes']:>7,} B "
              f"= {v['total_bytes']:>7,} B")
    t = rep["two_blob_layout"]
    print(f"\n  blob1 (duration+acoustic) {b1:>7,} B  論文 {PAPER['blob1']:>7,} B  "
          f"差 {t['delta_vs_paper']['blob1']:+,}")
    print(f"  blob2 (decoder)           {b2:>7,} B  論文 {PAPER['blob2']:>7,} B  "
          f"差 {t['delta_vs_paper']['blob2']:+,}")
    print(f"  合計                      {b1+b2:>7,} B  論文 {sum(PAPER.values()):>7,} B  "
          f"差 {t['delta_vs_paper']['total']:+,}")
    q = rep["quantization_error"]
    print(f"\n=== 量子化誤差（ランダム音素列 {q['n_probes']} 本）===")
    print(f"  log_d  SNR {q['log_d_snr_db']:>6.1f} dB   "
          f"d が完全一致した本数 {q['d_exact_match']}/{q['n_probes']}")
    print(f"  c      SNR {q['c_snr_db']:>6.1f} dB")
    print(f"  波形   SNR {q['wave_snr_db']:>6.1f} dB")
    print(f"\n→ {outdir}/quant.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
