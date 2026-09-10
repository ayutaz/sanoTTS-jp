"""M-98: Open JTalk の一時ヒープの上限を、形態素数から縛るための包絡を出す。

    uv run python scripts/k1/oj_heap_budget.py

⚠️ **平均ではなく包絡（最大）を見る。** 上限は最悪ケースで決めないと守れない。
⚠️ **ここで出るのは「ids ≤ a×形態素 + b」だけ。** ids → バイト数の係数
   （197.6 B/ids + 924 B）は **QEMU の低水位の実測**であって、この
   スクリプトでは測れない（ホストのアロケータは別物）。M-98 §1 を見ること。

出た値は esp32/main/saan_kanji.h の
SAAN_KANJI_IDS_PER_TOK / SAAN_KANJI_IDS_CONST / SAAN_KANJI_MAX_INPUT_TOK に入っている。
`bash scripts/check_esp32_template.sh` §11 が予算との整合を陽性対照つきで見る。
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

# saan_kanji.h に入っている定数（**手で書き写さない**）
OJ_B_PER_IDS = 198
OJ_B_CONST = 1024


def main() -> int:
    import pyopenjtalk                                     # noqa: F401
    import kana_g2p as K                                   # noqa: E402
    from gen_g2p_vectors import intersperse                # noqa: E402
    from piper_plus_g2p.japanese import JapanesePhonemizer  # noqa: E402

    ph = JapanesePhonemizer()
    texts = []
    with open(ROOT / "data/splits/corpus_heldout.tsv", encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                texts.append(p[2])

    rows = []
    for t in texts:
        try:
            ntok = len(pyopenjtalk.run_mecab(t))
            ids = intersperse(ph.phonemize(K.normalize_input(t)))
        except Exception:
            continue
        if ntok:
            rows.append((ntok, len(ids)))
    n = len(rows)
    print(f"held-out {n:,d} 文 / 形態素 最大 {max(r[0] for r in rows)}")

    med = sorted(i / t for t, i in rows)[n // 2]
    mx = max(rows, key=lambda r: r[1] / r[0])
    print(f"ids/形態素  中央 {med:.3f} / 最大 {mx[1]/mx[0]:.3f}"
          f"（形態素 {mx[0]} → ids {mx[1]}。⚠️ 短い文では定数 +3 が効くので比は当てにしない）")

    print("\n=== 包絡 ids <= a×形態素 + b（held-out で必ず成り立つ最小の b）===")
    for a in (8, 9, 10, 12):
        b = max(i - a * t for t, i in rows)
        print(f"  a={a:2d} → b={b:4d}")

    a, b = 8, max(i - 8 * t for t, i in rows)
    speak = [t for t, i in rows if i <= 350]      # 端末が実際に喋る文（SAAN_MAX_IDS）
    print(f"\n端末が喋れる文（ids ≤ 350）: {len(speak):,d} / {n:,d}"
          f"（{100*len(speak)/n:.1f}%）形態素 最大 {max(speak)}")
    print("\n=== 上限 T の取引（a=8 / b=%d の包絡で算術）===" % b)
    print(f"  {'T':>3}  {'落とす喋れる文':>16}  {'ids 上限':>8}  {'OJ ヒープ上限':>13}")
    for T in (38, 40, 44, 45, 48, 54, 96):
        lost = sum(1 for t, i in rows if i <= 350 and t > T)
        ids_max = a * T + b
        print(f"  {T:3d}  {lost:6d} / {len(speak):<5d} ({100*lost/len(speak):5.2f}%)"
              f"  {ids_max:8d}  {OJ_B_PER_IDS*ids_max + OJ_B_CONST:13,d}")
    print("\n⚠️ **OJ ヒープの列は算術**。ids → バイト数の係数は QEMU の低水位で測った値（M-98 §1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
