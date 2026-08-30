"""ドメイン外での未知語率とカタカナの扱いを測る。

B-0 は自分で「held-out はドメイン内」と限界を書いている
(`docs/research/b0-g2p-footprint.md` §7-3)。実際 held-out の未知語率は
フル辞書で 0.00% だった。**それは辞書が強いのではなく、コーパスが辞書由来だから**
かもしれない。デプロイ先の文（組み込みの通知文）と、別ドメインの文で測り直す。

未知語は誤読ではなく**無音で脱落する**ので、この率がそのまま
「文が黙って壊れる率」になる。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import unicodedata
from collections import Counter

import pyopenjtalk

ROOT = (_ROOT + "")
PP = os.path.expanduser("~/Documents/piper-plus")


def load_tsv(path, col=2):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) > col and p[col].strip():
                out.append(p[col])
    return out


def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


SETS = {
    "held-out (ドメイン内)": load_tsv(f"{ROOT}/data/splits/corpus_heldout.tsv"),
    "embedded (デプロイ先の通知文)": load_tsv(f"{ROOT}/data/splits/corpus_embedded.tsv"),
    "piper-plus 評価文 (ドメイン外)":
        load_lines(f"{PP}/scripts/evaluation/evaluation_texts_ja.txt"),
}

KATA = lambda c: 0x30A1 <= ord(c) <= 0x30FA


def is_kata_tok(s):
    return s and all(KATA(c) or c == "ー" for c in s)


print(f"{'集合':32s} {'文':>6} {'token':>8} {'未知語':>8} {'率':>7} "
      f"{'未知を含む文':>12} {'率':>7}")
rows = {}
for name, texts in SETS.items():
    if not texts:
        print(f"{name:32s}  (ファイルが無い)")
        continue
    # ⚠️ 先頭 N 件を取ってはいけない。corpus_heldout.tsv はソースごとの
    #    連続ブロックで、行 0..946 が全部 cv/sentence_collector。
    #    先頭 600 件で測った「未知語 0.00%」は単一ソースの値だった。
    tot = unk = 0
    sent_bad = 0
    unk_examples = Counter()
    kata_tok = kata_unk = 0
    short_feat = 0
    for t in texts:
        try:
            _, ms = pyopenjtalk.run_mecab_detailed(t)
        except Exception:
            continue
        if not ms:
            continue
        bad = False
        for m in ms:
            tot += 1
            s = m["surface"]
            if is_kata_tok(s):
                kata_tok += 1
            if len(m["features"]) < 12:
                short_feat += 1
            if m["is_unknown"]:
                unk += 1
                bad = True
                unk_examples[s] += 1
                if is_kata_tok(s):
                    kata_unk += 1
        if bad:
            sent_bad += 1
    rows[name] = dict(sent=len(texts), tok=tot, unk=unk, sent_bad=sent_bad,
                      kata_tok=kata_tok, kata_unk=kata_unk, short_feat=short_feat,
                      ex=unk_examples.most_common(12))
    print(f"{name:32s} {len(texts):>6,d} {tot:>8,d} {unk:>8,d} "
          f"{100*unk/max(tot,1):>6.2f}% {sent_bad:>12,d} "
          f"{100*sent_bad/max(len(texts),1):>6.2f}%  "
          f"(feature<12 の morph: {short_feat})")

print("\n=== 未知語の実例（無音で消えるもの）===")
for name, r in rows.items():
    if r["ex"]:
        print(f"  {name}: {[e[0] for e in r['ex']]}")
    else:
        print(f"  {name}: 未知語なし")

print("\n=== カタカナ token ===")
for name, r in rows.items():
    print(f"  {name:32s} カタカナ token {r['kata_tok']:>6,d} "
          f"/ うち未知語 {r['kata_unk']:>5,d}")

print("\n=== 実際に消えるのか（陰性対照つき）===")
probe = ["齟齬が生じました。", "蜃気楼が見えます。", "電源を入れてください。"]
for t in probe:
    ph = pyopenjtalk.g2p(t)
    _, ms = pyopenjtalk.run_mecab_detailed(t)
    unkn = [m["surface"] for m in ms if m["is_unknown"]]
    print(f"  {t}")
    print(f"    未知語 = {unkn}")
    print(f"    音素   = {ph}")
