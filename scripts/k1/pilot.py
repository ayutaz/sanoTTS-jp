"""パイロット: entries.tsv が MeCab の使う辞書と同一かを確かめ、分割戦略の目視確認をする。"""
from __future__ import annotations

import os
import sys
import time

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import pyopenjtalk  # noqa: E402

t0 = time.time()
DIC, MAXLEN, NROWS, NBAD = D.load_dic()
print(f"dic surfaces={len(DIC)} rows={NROWS} bad={NBAD} maxlen={MAXLEN} load={time.time()-t0:.1f}s")

CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(SP))))), "")
CORPUS = os.path.expanduser("~/Desktop/saanoTTS-jp/data/splits/corpus_heldout.tsv")
rows = []
with open(CORPUS, encoding="utf-8") as f:
    header = next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            rows.append((p[1], p[2]))
print(f"header={header.strip()!r} corpus n={len(rows)}")

# --- 陽性対照 1: A の既知語 feature が entries.tsv に literal に存在するか ---
n_tok = n_hit = n_unk = 0
featlen = {}
for uid, text in rows[:300]:
    feats, morphs = pyopenjtalk.run_mecab_detailed(text)
    for m in morphs:
        n_tok += 1
        fs = m["features"]
        featlen[len(fs)] = featlen.get(len(fs), 0) + 1
        if m["is_unknown"]:
            n_unk += 1
            continue
        surf = fs[0]
        body = ",".join(fs[1:])
        ents = DIC.get(surf)
        if ents and any(e[4] == body for e in ents):
            n_hit += 1
print(f"[G-DIC] n=300 sent tokens={n_tok} unknown={n_unk} "
      f"known_found_in_entries={n_hit}/{n_tok - n_unk}")
print(f"        feature field counts: {featlen}")

# --- 目視 ---
for uid, text in rows[:3]:
    feats, morphs = pyopenjtalk.run_mecab_detailed(text)
    print("---", text)
    print("  A:", " | ".join(m["features"][0] for m in morphs))
    for mode in ("longest_first", "longest_wcost", "min_wcost", "min_density"):
        seg = D.segment(text, DIC, MAXLEN, mode)
        print(f"  {mode:14s}", " | ".join(s.split(",")[0] for _, _, s, _ in seg))
