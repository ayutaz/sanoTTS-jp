"""PTQ（学習後量子化）の規則。**ここが唯一の定義**。

⚠️ **量子化の規則を 2 か所に書かない。** 以前は `scripts/quantize_student.py`
（PTQ シミュレーション）と `scripts/export_c_weights.py`（C 用 blob）が
同じ式を別々に持っていた。今は偶然どちらも同じ 52 テンソルに落ちるが、
層を足したときに黙ってずれる（CLAUDE.md C-020 と同じ事故形）。

方式（論文 §II-C）: **symmetric int8 / per-output-channel**。
出力チャネルは dim 0。scale は fp32 で 1 出力チャネルにつき 1 個。

⚠️ 丸めは `torch.round` = **half-to-even**。C 側は `rintf` で合わせてある
（`roundf` にすると 544,292 値のうち 5 個が食い違う。int8_test の 2c が検出する）。
"""

from __future__ import annotations

import numpy as np
import torch


def quantize_tensor(w: torch.Tensor) -> tuple[np.ndarray, np.ndarray]:
    """`w` を [cout, inner] に潰して int8 化する。

    返り値は `(q [cout, inner] int8, scale [cout] fp32)`。
    全ゼロ行の scale は 1（0 割りを避ける。C 側と同じ規則）。
    """
    flat = w.reshape(w.shape[0], -1).to(torch.float32)
    scale = flat.abs().amax(dim=1) / 127.0
    scale = torch.where(scale == 0, torch.ones_like(scale), scale)
    q = torch.clamp(torch.round(flat / scale[:, None]), -127, 127).to(torch.int8)
    return q.numpy(), scale.numpy().astype(np.float32)


def dequantize(q: np.ndarray, scale: np.ndarray, shape) -> torch.Tensor:
    """`quantize_tensor` の逆。fake-quant（量子化誤差を載せた fp32）を作るのに使う。"""
    return torch.from_numpy(
        (q.astype(np.float32) * scale[:, None])).reshape(shape).to(torch.float32)
