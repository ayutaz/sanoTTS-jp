#!/usr/bin/env python3
"""B-d: 教師ラベルがデバイス（CPU / MPS / CUDA）間で一致するかを検証する。

**なぜ要るか**: M-15 で「piper-plus venv と uv 環境で bit 完全一致」を確認したが、
**あれは CPU 同士の照合**だった。ラベル生成は vast.ai の GPU で行う予定なので
（D-012）、GPU が CPU と違う値を出すなら、ローカルで検証した内容が本番に通用しない。

TF32 / cuDNN の非決定的カーネル / 縮約順序の違いで差が出るのが典型。

**このスクリプトは vast.ai 上でも同じものを走らせること。** manifest に結果を残す。

実行:
    uv run python scripts/b4_device_parity.py                # 手元で CPU vs MPS
    uv run python scripts/b4_device_parity.py --device cuda  # vast.ai で CPU vs CUDA
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import warnings

import numpy as np
import torch

warnings.filterwarnings("ignore")

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import kana_g2p as K  # noqa: E402
import gen_teacher_labels as G  # noqa: E402
from gen_teacher_labels import build_teacher, encode_intermediate, snapshot  # noqa: E402

# 固定の検証文。長さと音素の多様性で選ぶ。**変えないこと**（比較の基準になる）
PROBE_TEXTS = [
    "今日は良い天気ですね。",
    "電源を入れてください。",
    "橋を渡る。",
    "箸を持つ。",
    "本日の会議は午後三時からです。",
    "バッテリー残量は十五パーセントです。",
    "コンピューターを再起動します。",
    "中世のイングランドでは、町という町のすべてが賑わっていた。",
]


def digest(x: torch.Tensor) -> str:
    return hashlib.sha256(x.detach().cpu().numpy().tobytes()).hexdigest()[:16]


def run_on(device: str, teacher, pim, table) -> list[dict]:
    dev = torch.device(device)
    teacher = teacher.to(dev)
    rows = []
    for text in PROBE_TEXTS:
        ids = encode_intermediate(K.text_to_intermediate(text, table), pim)
        with torch.no_grad():
            out = teacher.infer(
                torch.tensor([ids], device=dev), torch.tensor([len(ids)], device=dev),
                lid=torch.tensor([0], device=dev),
                noise_scale=0.0, noise_scale_w=0.0, length_scale=1.0,
                prosody_features=torch.zeros(1, len(ids), 3, device=dev),
                speaker_embeddings=None,
            )
        rows.append({
            "text": text, "n_ids": len(ids),
            "frames": int(out.latents[0].shape[-1]),
            "audio": digest(out.audio), "zT": digest(out.latents[0]),
            "dT": digest(out.durations),
            "audio_f32": out.audio.detach().cpu().numpy().astype(np.float64),
            "zT_f32": out.latents[0].detach().cpu().numpy().astype(np.float64),
        })
    teacher.to("cpu")
    return rows


def compare(a: list[dict], b: list[dict], name_a: str, name_b: str) -> dict:
    print(f"\n{name_a} vs {name_b}")
    print(f"  {'文':<22}{'frames':>7}{'bit':>6}{'SNR(dB)':>9}{'int16後の差':>12}")
    bit_equal = 0
    snrs: list[float] = []
    int16_diffs: list[int] = []
    for x, y in zip(a, b, strict=True):
        same_frames = x["frames"] == y["frames"]
        eq = all((x["audio"] == y["audio"], x["zT"] == y["zT"], x["dT"] == y["dT"]))
        bit_equal += eq and same_frames

        snr = float("nan")
        n_diff = -1
        if same_frames:
            xa = x["audio_f32"].ravel()
            ya = y["audio_f32"].ravel()
            n = min(xa.size, ya.size)
            err = float(((xa[:n] - ya[:n]) ** 2).mean())
            pwr = float((xa[:n] ** 2).mean())
            # 完全一致なら err == 0 → SNR は inf。これは**成功**であって NaN ではない
            snr = float("inf") if err == 0 else 10 * np.log10(pwr / err)
            # **パックは int16 で保存する（A-3）。量子化後も差が残るかが本当の問題。**
            qi = np.clip(np.round(xa[:n] * 32767), -32767, 32767).astype(np.int16)
            qj = np.clip(np.round(ya[:n] * 32767), -32767, 32767).astype(np.int16)
            n_diff = int((qi != qj).sum())
            int16_diffs.append(n_diff)
        snrs.append(snr)

        snr_s = "完全一致" if snr == float("inf") else (
            f"{snr:.1f}" if np.isfinite(snr) else "-")
        print(f"  {x['text'][:20]:<22}{'OK' if same_frames else 'NG':>7}"
              f"{'一致' if eq else '差異':>6}{snr_s:>9}"
              f"{(str(n_diff) + ' sample') if n_diff >= 0 else '-':>12}")

    finite = [s for s in snrs if np.isfinite(s)]
    total_diff = sum(int16_diffs)
    print(f"  → bit 完全一致 {bit_equal}/{len(a)}"
          + (f" / SNR min {min(finite):.1f} dB" if finite else "")
          + f" / int16 化後に差が残るサンプル 計 {total_diff}")
    return {
        "pair": f"{name_a}_vs_{name_b}",
        "bit_equal": bit_equal, "n": len(a),
        "snr_db_min": (min(finite) if finite else None),
        "snr_db_mean": (float(np.mean(finite)) if finite else None),
        "int16_differing_samples": total_diff,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None,
                    help="CPU と比較する対象。既定は利用可能な加速器を自動選択")
    ap.add_argument("--out", default="reports/b4_device_parity.json")
    args = ap.parse_args()

    other = args.device
    if other is None:
        if torch.cuda.is_available():
            other = "cuda"
        elif torch.backends.mps.is_available():
            other = "mps"

    G.ENCODE_TABLE = K.build_mora_table()
    snap = snapshot()
    ckpt = torch.load(snap + "epoch=499-step=22000.ckpt",
                      map_location="cpu", weights_only=False)
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]
    teacher = build_teacher(ckpt)

    print(f"検証文 {len(PROBE_TEXTS)} 件 / 比較対象: cpu" + (f" と {other}" if other else " のみ"))

    cpu_a = run_on("cpu", teacher, pim, G.ENCODE_TABLE)
    cpu_b = run_on("cpu", teacher, pim, G.ENCODE_TABLE)
    results = [compare(cpu_a, cpu_b, "cpu(1回目)", "cpu(2回目)")]

    if other:
        try:
            acc = run_on(other, teacher, pim, G.ENCODE_TABLE)
            results.append(compare(cpu_a, acc, "cpu", other))
        except Exception as exc:  # noqa: BLE001
            print(f"\n{other} での実行に失敗: {type(exc).__name__}: {exc}")
            results.append({"pair": f"cpu_vs_{other}", "error": str(exc)})
    else:
        print("\n加速器が無いので CPU 同士の決定性のみ確認した")

    report = {
        "probe_texts": PROBE_TEXTS,
        "environment": {
            "python": platform.python_version(), "torch": torch.__version__,
            "platform": platform.platform(),
            "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "results": results,
        "cpu_reference": [{k: v for k, v in r.items() if not k.endswith("_f32")}
                          for r in cpu_a],
    }
    import pathlib
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1))

    print(f"\n→ {args.out}")
    print("""
⚠️ **vast.ai でラベルを生成する前に、そのインスタンス上でこれを走らせること。**
CPU と GPU が bit 一致しないなら、ラベル生成は CPU に固定するか、
生成環境を manifest に記録して「この GPU で作った」ことを明示する。
どちらにせよラベルは**一度だけ**生成し、SHA-256 で固定する。""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
