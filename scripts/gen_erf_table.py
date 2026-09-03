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

T5-G4（2026-09-03）: 導関数表は **h = 2^-5 を掛けた値**（kSaanErfDh = erf'(x)·h）で出す。
使う側の `kSaanErfD[i] * h` の乗算が 1 要素あたり 2 回消える。2^-5 倍は float で正確
（指数が 5 減るだけ。最小値 1.27e-7·2^-5 ≈ 4e-9 は正規数）なので、**旧表の float 値 × h と
bit 一致する** — それを崩さないために、`%.9g` で 9 桁に丸めた旧リテラルを float32 に落として
から 2^-5 を掛け、その float32 を `%.9g` で出す（float32 は 9 桁で往復する）。
double の erf'(x) に直接 2^-5 を掛けて丸めると、二重丸めで旧表と 1 ulp ずれうる。
⚠️ 旧表との bit 一致は erf_test.c が検査する（凍結コピー kRefErfD × h と全 129 節点で比較）。
"""
from __future__ import annotations

import hashlib
import math
import struct
import sys

H_INV = 32          # 1/h
X_MAX = 4.0
N = int(X_MAX * H_INV)   # 区間数 128 → 節点 129


def fmt(v: float) -> str:
    s = "%.9g" % v
    if "." not in s and "e" not in s:
        s += ".0"
    return s + "f"


def f32(v: float) -> float:
    """double → 最も近い float32（C コンパイラが float リテラルにするのと同じ丸め）。"""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def prescale_h(d: float) -> float:
    """erf'(x) → erf'(x)·h を、**旧表の float 値 × h と bit 一致するように**作る（T5-G4）。
    旧表のリテラル（%.9g の 9 桁）を float32 に落とし、2^-5 を掛ける（float32 で正確）。"""
    lit = float(fmt(d)[:-1])          # 旧表と同じリテラル文字列 → double
    return f32(lit) * (1.0 / H_INV)   # float32 × 2^-5 は float32 のまま正確


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
    dh = [prescale_h(x) for x in d]   # T5-G4: erf'(x)·h（h = 1/32）
    assert all(f32(v) == v for v in dh), "事前スケール後の値が float32 で表せない"
    assert H_INV & (H_INV - 1) == 0, "h は 2 の冪でないと float で正確に掛けられない"
    body = table("kSaanErfV", v) + "\n\n" + table("kSaanErfDh", dh) + "\n"
    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    out = f'''/* 自動生成 — 編集しない（scripts/gen_erf_table.py。本文 sha256 {sha}…）
 *
 *   uv run --no-project python scripts/gen_erf_table.py > csrc/erf_table.h
 *
 * erf の 3 次 Hermite 補間表（S3）。x = i / {H_INV}（i = 0..{N}、[0, {X_MAX:g}]）の
 * erf(x)（kSaanErfV）と **erf'(x) · h**（kSaanErfDh。erf'(x) = 2/√π · exp(−x²)、h = 1/{H_INV}。
 * T5-G4 で h を掛けた値にした。旧 kSaanErfD[i] * h と bit 一致 — erf_test.c が検査する）。
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
