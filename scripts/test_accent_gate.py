#!/usr/bin/env python3
"""教師ゲートが**生徒に依存しない**ことを確かめる（C-075 の回帰）。

⚠️ **これが守れないと、モデル間のアクセント比較は分母が違うものの比較になる。**
実際に v3 と v4 で教師 Δ が 38 ペア中 14 ペア動き、最大 2.097 st ずれた
（ゲート閾値は 1.5 st なので、これだけで通過/落選が入れ替わる）。

見るもの:
  G-A1  同じ教師 F0 なら、**生徒を変えてもゲートの判定が変わらない**
  G-A2  ⚠️ **陽性対照**: 旧実装（共通マスクで判定）だと**判定が変わる**
  G-A3  `cos` は共通マスクで測る（生徒が欠測したモーラは使えないので当然）
  G-A4  教師だけ変えればゲートは変わる（ゲートが教師を見ていることの確認）

実行:
    uv run python scripts/test_accent_gate.py

⚠️ **numpy が要るので `--no-project` では動かない**（CI の `python` job で回す）。
"""

from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "src")
from saanotts_jp.accent import contrast  # noqa: E402

NAN = float("nan")


def gate(a_t, a_s, b_t, b_s, *, fixed: bool) -> tuple[bool, float]:
    """しきい値 1.5 st でゲートを判定する。`fixed=False` は旧実装（共通マスク）。"""
    st_t, st_s = [np.array(a_t, float), np.array(b_t, float)], \
                 [np.array(a_s, float), np.array(b_s, float)]
    mask = np.all([np.isfinite(t) & np.isfinite(s) for t, s in zip(st_t, st_s)], axis=0)
    t_mask = np.all([np.isfinite(t) for t in st_t], axis=0)
    c = contrast(st_t[0], st_s[0], st_t[1], st_s[1], mask,
                 teacher_mask=(t_mask if fixed else None))
    v = c["gate_teacher_st"] if fixed else c["norm_teacher_st"]
    return v >= 1.5, v


#: 教師（固定）。2 語のピッチ輪郭が最後のモーラで大きく違う。
T_A = [0.0, 0.0, 0.0, 6.0]
T_B = [0.0, 0.0, 0.0, 0.0]
#: 生徒 1: 全モーラで F0 が取れた
S1_A, S1_B = [0.0, 0.0, 0.0, 5.0], [0.0, 0.0, 0.0, 0.0]
#: 生徒 2: **最後のモーラで F0 が欠測**（ここに弁別情報がある）
S2_A, S2_B = [0.0, 0.0, 0.0, NAN], [0.0, 0.0, 0.0, 0.0]


def test_gate_is_student_independent() -> int:
    bad = 0
    g1, v1 = gate(T_A, S1_A, T_B, S1_B, fixed=True)
    g2, v2 = gate(T_A, S2_A, T_B, S2_B, fixed=True)
    if g1 == g2 and abs(v1 - v2) < 1e-9:
        print(f"  OK  G-A1 生徒を変えてもゲートは同じ（{v1:.3f} st / 判定 {g1}）")
    else:
        print(f"  NG! G-A1 生徒でゲートが動いた: {v1:.3f}/{g1} vs {v2:.3f}/{g2}")
        bad += 1
    return bad


def test_positive_control() -> int:
    """⚠️ **陽性対照。** 旧実装なら判定が変わることを示す。

    これが「変わらない」なら、この検査は**何も見ていない**（そもそも
    生徒依存が再現できていない = テストデータが弱い）。
    """
    _, v1 = gate(T_A, S1_A, T_B, S1_B, fixed=False)
    g1, _ = gate(T_A, S1_A, T_B, S1_B, fixed=False)
    g2, v2 = gate(T_A, S2_A, T_B, S2_B, fixed=False)
    if g1 != g2:
        print(f"  OK  G-A2 陽性対照: 旧実装は判定が変わる"
              f"（{v1:.3f} st→{g1} / {v2:.3f} st→{g2}）")
        return 0
    print(f"  NG! G-A2 陽性対照が効かない = **この検査は空虚**"
          f"（旧実装でも {v1:.3f}/{g1} と {v2:.3f}/{g2} で判定が同じ）")
    return 1


def test_cos_uses_common_mask() -> int:
    """`cos` は共通マスクで測る。**生徒が欠測したモーラは使えない**ので当然。"""
    st_t = [np.array(T_A, float), np.array(T_B, float)]
    st_s = [np.array(S2_A, float), np.array(S2_B, float)]
    mask = np.all([np.isfinite(t) & np.isfinite(s) for t, s in zip(st_t, st_s)], axis=0)
    t_mask = np.all([np.isfinite(t) for t in st_t], axis=0)
    c = contrast(st_t[0], st_s[0], st_t[1], st_s[1], mask, teacher_mask=t_mask)
    if c["n_morae"] == 3 and c["gate_n_morae"] == 4:
        print(f"  OK  G-A3 cos は共通 {c['n_morae']} モーラ / "
              f"ゲートは教師の {c['gate_n_morae']} モーラ")
        return 0
    print(f"  NG! G-A3 マスクが分かれていない "
          f"(n_morae={c['n_morae']} gate_n_morae={c['gate_n_morae']})")
    return 1


def test_gate_follows_teacher() -> int:
    """教師を変えればゲートは変わる（ゲートが教師を見ていることの確認）。"""
    flat_a, flat_b = [0.0, 0.0, 0.0, 0.1], [0.0, 0.0, 0.0, 0.0]
    g_big, v_big = gate(T_A, S1_A, T_B, S1_B, fixed=True)
    g_small, v_small = gate(flat_a, S1_A, flat_b, S1_B, fixed=True)
    if g_big and not g_small:
        print(f"  OK  G-A4 教師が弁別すれば通り（{v_big:.3f} st）"
              f"、しなければ落ちる（{v_small:.3f} st）")
        return 0
    print(f"  NG! G-A4 ゲートが教師を見ていない: {v_big:.3f}/{g_big} vs "
          f"{v_small:.3f}/{g_small}")
    return 1


def main() -> int:
    bad = (test_gate_is_student_independent() + test_positive_control()
           + test_cos_uses_common_mask() + test_gate_follows_teacher())
    print()
    print("⚠️ 見ていないもの: **実際の F0 抽出**（合成音が要る）と、"
          "この変更で v3 の 37/37 がどう動くか（それは実測で測る）")
    print()
    print("すべて期待通り" if bad == 0 else f"{bad} 件 NG")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
