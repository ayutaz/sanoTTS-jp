# -*- coding: utf-8 -*-
"""B-0: カバー率測定用コーパスの構築 (統合 -> NFKC -> 重複排除 -> train/heldout 分割)"""
import unicodedata, collections, hashlib, json, random, re, sys, os

SC = "<scratch>"
OUT = SC + "/b0"
SEED = 20260826

SOURCES = []
for l in open(SC+"/pool.tsv", encoding="utf-8").read().split("\n"):
    if not l.strip(): continue
    p = l.split("\t")
    assert len(p) == 3, p
    SOURCES.append((p[0], p[1], p[2]))
for fn, tag in [("cv_ja_sentence_collector.txt", "cv/sentence_collector"),
                ("cv_ja_yumie-text-1.txt",       "cv/yumie-text-1"),
                ("cv_ja_singleword-benchmark.txt","cv/singleword-benchmark")]:
    for i, l in enumerate(open(SC+"/"+fn, encoding="utf-8").read().split("\n")):
        if not l.strip(): continue
        SOURCES.append((tag, f"{tag.split('/')[1]}_{i+1:05d}", l))

raw_n = len(SOURCES)

# ---- NFKC 正規化 -------------------------------------------------------
def norm(t):
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("　", " ")          # 全角空白 -> 半角 (NFKC は全角空白を残す)
    t = re.sub(r"\s+", " ", t).strip()    # 連続空白の畳み込み
    return t

rows = []
empty_after_norm = 0
for src, rid, text in SOURCES:
    n = norm(text)
    if not n:
        empty_after_norm += 1
        continue
    rows.append({"source": src, "id": rid, "text": n, "orig": text})

changed_by_nfkc = sum(1 for r in rows if r["text"] != r["orig"])

# ---- 完全一致の重複排除 (先勝ち: pool -> CV の順) -----------------------
seen = {}
dups = []
uniq = []
for r in rows:
    k = r["text"]
    if k in seen:
        dups.append((seen[k]["source"], r["source"]))
        continue
    seen[k] = r
    uniq.append(r)

dup_pairs = collections.Counter(dups)

# ---- 文字種統計 --------------------------------------------------------
def cclass(ch):
    o = ord(ch)
    if 0x3040 <= o <= 0x309F: return "hiragana"
    if 0x30A0 <= o <= 0x30FF or o == 0x30FC: return "katakana"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF: return "kanji"
    if ch.isdigit() and o < 128: return "digit"
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z"): return "latin"
    if ch == " ": return "space"
    return "punct_other"

KANA = lambda t: any(cclass(c) in ("hiragana", "katakana") for c in t)

def stats(rs, name):
    n = len(rs)
    lens = sorted(len(r["text"]) for r in rs)
    cc = collections.Counter()
    linehas = collections.Counter()
    for r in rs:
        cs = set()
        for ch in r["text"]:
            k = cclass(ch); cc[k] += 1; cs.add(k)
        for k in cs: linehas[k] += 1
    tot = sum(cc.values())
    nokana = [r for r in rs if not KANA(r["text"])]
    return {
        "name": name, "lines": n,
        "chars_total": tot,
        "chars_per_line_mean": round(tot / n, 2) if n else 0,
        "chars_per_line_median": lens[n//2] if n else 0,
        "chars_per_line_min": lens[0] if n else 0,
        "chars_per_line_max": lens[-1] if n else 0,
        "char_class_pct": {k: round(100*v/tot, 2) for k, v in cc.most_common()},
        "lines_containing_pct": {k: round(100*v/n, 2) for k, v in linehas.most_common()},
        "lines_without_any_kana": len(nokana),
        "lines_without_any_kana_pct": round(100*len(nokana)/n, 3) if n else 0,
        "by_source": dict(collections.Counter(r["source"] for r in rs).most_common()),
    }

# ---- train / heldout 分割 (source 層化 90/10) --------------------------
rng = random.Random(SEED)

def group_key(r):
    """近傍重複が構造的に確実な行は同じグループにまとめ、分割で引き裂かない。
    jsut/utparaphrase512 は UT-PARAPHRASE-sentNNN-phrase1/2 が言い換え対なので
    sentNNN 単位でまとめる (片方が train、片方が heldout になるとカバー率が水増しされる)。"""
    m = re.match(r"UT-PARAPHRASE-(sent\d+)-phrase\d+$", r["id"])
    if m: return ("utpara", m.group(1))
    return ("row", r["id"])

bysrc = collections.defaultdict(lambda: collections.defaultdict(list))
for r in uniq: bysrc[r["source"]][group_key(r)].append(r)
train, held = [], []
for src in sorted(bysrc):
    groups = list(bysrc[src].values())
    rng.shuffle(groups)
    n_rows = sum(len(g) for g in groups)
    target = round(n_rows * 0.10) if n_rows >= 10 else 0
    acc = 0
    for g in groups:
        if acc < target:
            held += g; acc += len(g)
        else:
            train += g

# ---- 近傍重複 (char 5-gram Jaccard) のリーク検査 -----------------------
def shingles(t, n=5):
    t = re.sub(r"[ 、。，．,.!?！？「」『』（）()\"'・…―ー]", "", t)
    if len(t) <= n: return {t} if t else set()
    return {t[i:i+n] for i in range(len(t)-n+1)}

tr_sh = [shingles(r["text"]) for r in train]
inv = collections.defaultdict(list)
for i, s in enumerate(tr_sh):
    for g in s: inv[g].append(i)

LEAK = 0.70
leaks = []
for r in held:
    s = shingles(r["text"])
    if not s: continue
    cand = collections.Counter()
    for g in s:
        for i in inv.get(g, ()): cand[i] += 1
    best, bi = 0.0, -1
    for i, ov in cand.most_common(50):
        j = ov / (len(s) + len(tr_sh[i]) - ov)
        if j > best: best, bi = j, i
    if best >= LEAK:
        leaks.append((r, best, train[bi]["text"]))

leak_ids = {id(r) for r, _, _ in leaks}
held2 = [r for r in held if id(r) not in leak_ids]
train2 = train + [r for r, _, _ in leaks]

# ---- 書き出し ----------------------------------------------------------
def write(path, rs):
    with open(path, "w", encoding="utf-8") as f:
        f.write("source\tid\ttext\n")
        for r in rs: f.write(f'{r["source"]}\t{r["id"]}\t{r["text"]}\n')

write(OUT+"/corpus_train.tsv", train2)
write(OUT+"/corpus_heldout.tsv", held2)

rep = {
    "seed": SEED,
    "raw_rows": raw_n,
    "empty_after_norm": empty_after_norm,
    "rows_changed_by_nfkc": changed_by_nfkc,
    "unique_after_dedup": len(uniq),
    "exact_duplicates_removed": len(dups),
    "duplicate_pairs_top": [{"kept_from": a, "dropped_from": b, "n": n}
                            for (a, b), n in dup_pairs.most_common(12)],
    "near_dup_threshold_jaccard5gram": LEAK,
    "near_dup_leaks_moved_to_train": len(leaks),
    "near_dup_examples": [{"heldout": r["text"], "jaccard": round(j,3), "train": t}
                          for r, j, t in leaks[:8]],
    "splits": {"train": stats(train2, "train"), "heldout": stats(held2, "heldout")},
    "all_unique": stats(uniq, "all_unique"),
}
json.dump(rep, open(OUT+"/corpus_stats.json","w",encoding="utf-8"),
          ensure_ascii=False, indent=2)

print(f"raw                     : {raw_n}")
print(f"NFKC で変化した行        : {changed_by_nfkc}")
print(f"正規化後に空になった行    : {empty_after_norm}")
print(f"完全一致の重複を除去      : {len(dups)}")
print(f"ユニーク                 : {len(uniq)}")
print(f"近傍重複リーク(J>={LEAK}) : {len(leaks)}  (heldout -> train へ移動)")
print(f"train                   : {len(train2)}")
print(f"heldout                 : {len(held2)}")
print("\n--- 重複の出所 top ---")
for (a,b),n in dup_pairs.most_common(12): print(f"  {n:5d}  kept={a:28s} dropped={b}")
