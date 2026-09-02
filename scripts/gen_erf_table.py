#!/usr/bin/env python3
"""erf の 3 次 Hermite 補間表を生成する（S3。`csrc/erf_table.h`）。

    uv run --no-project python scripts/gen_erf_table.py > csrc/erf_table.h

なぜ要るか: GELU は要素ごとに `erff()` を呼ぶ（1 step に 21,664 回。M-80）。newlib の
erff は多項式 + expf の関数呼び出しで、QEMU の命令数比で 1 step の 14〜25% を使っていた。

方式: x ∈ [0, 4] を h = 1/32 で 128 区間に割り、節点の erf(x) と erf'(x) = 2/√π · exp(−x²) を
持つ。区間内は 3 次 Hermite。|x| ≥ 4 は ±1（erf(4) = 1 − 1.5e-8 は float で 1.0）。
理論誤差 h⁴/384 · max|erf⁗| ≈ (1/32)⁴/384 · 4.4 ≈ 1.1e-8（float の丸めの方が大きい）。

⚠️ 表は double で計算して float リテラルに落とす（gen_fft_tables.py と同じ流儀）。
⚠️ ゲートは `make -C csrc erf`（erff との max|Δ|。線形補間に落とした陽性対照つき）。
"""
from __future__ import annotations

import hashlib
import math
import sys

H_INV = 32          # 1/h
X_MAX = 4.0
N = int(X_MAX * H_INV)   # 区間数 128 → 節点 129


def fmt(v: float) -> str:
    s = "%.9g" % v
    if "." not in s and "e" not in s:
        s += ".0"
    return s + "f"


def table(name: str, vals: list[float], per: int = 4) -> str:
    lines = [f"static const float {name}[{len(vals)}] = {{"]
    for i in range(0, len(vals), per):
        chunk = ", ".join(fmt(v) for v in vals[i:i + per])
        lines.append("    " + chunk + ("," if i + per < len(vals) else ""))
    lines.append("};")
    return "\n".join(lines)


def main() -> int:
    xs = [i / H_INV for i in range(N + 1)]
    v = [math.erf(x) for x in xs]
    d = [2.0 / math.sqrt(math.pi) * math.exp(-x * x) for x in xs]
    body = table("kSaanErfV", v) + "\n\n" + table("kSaanErfD", d) + "\n"
    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    out = f'''/* 自動生成 — 編集しない（scripts/gen_erf_table.py。本文 sha256 {sha}…）
 *
 *   uv run --no-project python scripts/gen_erf_table.py > csrc/erf_table.h
 *
 * erf の 3 次 Hermite 補間表（S3）。x = i / {H_INV}（i = 0..{N}、[0, {X_MAX:g}]）の
 * erf(x)（kSaanErfV）と erf'(x) = 2/√π · exp(−x²)（kSaanErfD）。
 * 使う側は csrc/saanotts.c の saan_erf_approx()。ゲートは `make -C csrc erf`。 */
#ifndef SAAN_ERF_TABLE_H
#define SAAN_ERF_TABLE_H

#define SAAN_ERF_H_INV {H_INV}
#define SAAN_ERF_N     {N}          /* 区間数。節点は N + 1 */
#define SAAN_ERF_XMAX  {fmt(X_MAX)}

{body}
#endif /* SAAN_ERF_TABLE_H */
'''
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
