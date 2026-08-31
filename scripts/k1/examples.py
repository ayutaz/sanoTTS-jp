"""min_density（最良の貪欲戦略）が A とどう食い違うかを具体例で見る。"""
from __future__ import annotations

import os
import sys

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import measure as M  # noqa: E402
import pyopenjtalk  # noqa: E402

DIC, MAXLEN, _, _ = D.load_dic()
rows = []
with open(M.CORPUS, encoding="utf-8") as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            rows.append(D.han2zen(p[2]))   # A も内部で掛ける正規化を B にも許す

cat = {"読みが違う": [], "読みは同じでアクセントだけ違う": [], "音が脱落した": []}
n_del_sent = 0
for text in rows:
    feats_a, morphs_a = pyopenjtalk.run_mecab_detailed(text)
    njd_a = pyopenjtalk.apply_postprocessing(text, pyopenjtalk.run_njd_from_mecab(feats_a))
    ap_a = M.accent_phrases(njd_a)
    pron_a = "".join(M.pron_of(f.split(",")) for f in feats_a)

    seg = D.segment(text, DIC, MAXLEN, "min_density")
    feats_b = [f for _, _, f, _ in seg if not M.is_space_symbol(f.split(","))]
    njd_b = pyopenjtalk.apply_postprocessing(text, pyopenjtalk.run_njd_from_mecab(feats_b))
    ap_b = M.accent_phrases(njd_b)
    pron_b = "".join(M.pron_of(f.split(",")) for f in feats_b)

    _, _, dele, _ = M.levenshtein(pron_a, pron_b)
    if dele > 0:
        n_del_sent += 1
    if pron_a != pron_b:
        k = "音が脱落した" if dele >= 2 else "読みが違う"
        if len(cat[k]) < 6:
            cat[k].append((text, pron_a, pron_b,
                           [x.split(",")[0] for x in feats_a],
                           [x.split(",")[0] for x in feats_b]))
    elif ap_a != ap_b and len(cat["読みは同じでアクセントだけ違う"]) < 6:
        cat["読みは同じでアクセントだけ違う"].append((text, ap_a, ap_b, None, None))

print(f"# 脱落（A の読みにあって B に無い文字が 1 つ以上）を含む文: "
      f"{n_del_sent}/{len(rows)} = {n_del_sent / len(rows):.4f}")
for k, v in cat.items():
    print(f"\n=== {k} ===")
    for e in v:
        print("  文  :", e[0])
        if e[3] is None:
            print("  A acc:", e[1])
            print("  B acc:", e[2])
        else:
            print("  A   :", e[1], "|", " ".join(e[3]))
            print("  B   :", e[2], "|", " ".join(e[4]))
        print()
