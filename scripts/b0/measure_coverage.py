#!/usr/bin/env python
"""B-0: 枝刈り OpenJTalk 辞書のカバー率測定。

フル NAIST-JDIC (piper-plus の build/share/open_jtalk/dic) の出力を正解とみなし、
枝刈り辞書の出力と 4 つの指標で比較する:

  1. 音素列一致率     extract_fullcontext の P3 列 (piper-plus japanese.py:30 と同じ正規表現)
  2. アクセント型一致率 run_frontend の chain_flag でアクセント句に切り、(acc, mora数) 列を比較
  3. A1/A2/A3 一致率   extract_fullcontext の /A:a1+a2+a3/ 列 (教師 duration の prosody 入力そのもの)
  4. token カバー率    フル辞書での token 表層が枝刈り語彙集合に入るか (実ビルド不要の上界)

pyopenjtalk はグローバルインスタンスをキャッシュするので、辞書ごとに別プロセスで
`dump` を回す必要がある (mode=run が自動でそうする)。

パイプライン全体 (workdir = scratchpad/b0_cov):
  1. tokfreq.py       corpus_train.tsv をフル辞書でトークナイズして表層頻度 -> train_tokfreq.tsv
  2. make_pruned_cov.py <N>...   頻度ランキングで枝刈りした dicdir (cin_<N>_{full,noread}) と vocab_<N>.json
  3. make_guard.py <N>...        + 数詞/助数詞/記号 2,705 表層を強制追加した cin_<N>_guard と vocab_<N>g.json
  4. b0/compact_matrix.py        matrix.bin / unk.dic / CSV の context id を使用分に再割当
  5. b0/mecab-dict-index -d cin_X -o cout_X -f utf-8 -t utf-8 -s   (stderr 0 行を必ず確認)
  6. rt_<N>_<V>/ に sys.dic + matrix.bin + char.bin + unk.dic を集める
  7. このスクリプトの run モード

usage:
  python measure_coverage.py dump <dictdir> <eval.tsv> <out.jsonl>
  B0_LEVELS=10000,30000 B0_VARIANTS=guard python measure_coverage.py run <workdir> <out.json>
"""
import os, sys, json, re, subprocess, csv, collections

RE_PH = re.compile(r"-(?P<ph>[^+]+)\+")
RE_A  = re.compile(r"/A:(?P<a1>[\d-]+)\+(?P<a2>[0-9]+)\+(?P<a3>[0-9]+)/")

SRC = "~/Documents/piper-plus/build/share/open_jtalk/dic"
PY  = "~/Documents/piper-plus/.venv/bin/python"


def read_tsv(path):
    rows = list(csv.reader(open(path, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE))
    assert rows[0] == ["source", "id", "text"], rows[0]
    return [(r[0], r[1], r[2]) for r in rows[1:] if len(r) >= 3 and r[2].strip()]


# ---------------------------------------------------------------- dump mode
def do_dump(dictdir, evaltsv, outpath):
    os.environ["OPEN_JTALK_DICT_DIR"] = dictdir
    import pyopenjtalk
    got = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
    assert got == dictdir, f"dict dir not honoured: {got}"
    rows = read_tsv(evaltsv)
    with open(outpath, "w", encoding="utf-8") as o:
        for src, sid, text in rows:
            rec = {"id": sid, "source": src, "text": text}
            try:
                labels = pyopenjtalk.extract_fullcontext(text)
                phs, atup = [], []
                for lab in labels:
                    m = RE_PH.search(lab)
                    phs.append(m.group("ph") if m else "?")
                    ma = RE_A.search(lab)
                    atup.append([ma.group("a1"), ma.group("a2"), ma.group("a3")] if ma else ["?", "?", "?"])
                rec["ph"] = phs
                rec["a"] = atup
                nodes = pyopenjtalk.run_frontend(text)
                toks, aps = [], []
                for n in nodes:
                    toks.append([n["string"], n["pron"], n["acc"], n["mora_size"], n["pos"]])
                    # JPCommon: chain_flag == 1 のときだけ直前のアクセント句に連結
                    if n["chain_flag"] == 1 and aps:
                        aps[-1][1] += n["mora_size"]
                    else:
                        aps.append([n["acc"], n["mora_size"]])
                rec["tok"] = toks
                rec["ap"] = [x for x in aps if x[1] > 0]     # モーラ 0 (記号) は句にしない
                rec["pron"] = "".join(n["pron"] for n in nodes)
            except Exception as e:
                rec["err"] = f"{type(e).__name__}: {e}"
            o.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"dumped {len(rows)} -> {outpath}")


def load_jsonl(p):
    return {r["id"]: r for r in (json.loads(l) for l in open(p, encoding="utf-8"))}


# ---------------------------------------------------------------- metrics
def lev(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def compare(ref, hyp, vocab=None):
    """ref/hyp: id->record. vocab: set of surfaces (None to skip coverage)."""
    n = 0
    ok_ph = ok_a = ok_ap = ok_acc_only = 0
    ph_err = ph_len = 0
    a_err = a_len = 0
    hyp_err = 0
    tok_in = tok_tot = 0
    sent_cov = 0
    fails = []
    for sid, r in ref.items():
        h = hyp.get(sid)
        n += 1
        if vocab is not None:
            surfaces = [t[0] for t in r["tok"]]
            miss = [s for s in surfaces if s not in vocab]
            tok_tot += len(surfaces)
            tok_in += len(surfaces) - len(miss)
            if not miss:
                sent_cov += 1
        if h is None or "err" in h or "err" in r:
            hyp_err += 1
            ph_err += len(r.get("ph", []))
            ph_len += len(r.get("ph", []))
            a_err += len(r.get("a", []))
            a_len += len(r.get("a", []))
            fails.append({"id": sid, "text": r["text"], "kind": "error",
                          "ref_pron": r.get("pron"), "hyp_err": (h or {}).get("err")})
            continue
        rp, hp = r["ph"], h["ph"]
        ra = ["+".join(x) for x in r["a"]]
        ha = ["+".join(x) for x in h["a"]]
        rap = [tuple(x) for x in r["ap"]]
        hap = [tuple(x) for x in h["ap"]]
        d = lev(rp, hp); ph_err += d; ph_len += len(rp)
        da = lev(ra, ha); a_err += da; a_len += len(ra)
        pheq = (rp == hp); aeq = (ra == ha); apeq = (rap == hap)
        ok_ph += pheq; ok_a += aeq; ok_ap += apeq
        if pheq and not apeq:
            ok_acc_only += 1
        if not (pheq and aeq and apeq):
            fails.append({"id": sid, "text": r["text"],
                          "kind": ("phoneme" if not pheq else ("accent" if not apeq else "A123")),
                          "ref_pron": r["pron"], "hyp_pron": h["pron"],
                          "ref_ap": r["ap"], "hyp_ap": h["ap"],
                          "ref_tok": [[t[0], t[1], t[2]] for t in r["tok"]],
                          "hyp_tok": [[t[0], t[1], t[2]] for t in h["tok"]],
                          "ph_dist": d})
    res = {
        "sentences": n,
        "phoneme_sentence_agreement_pct": round(100.0 * ok_ph / n, 3),
        "phoneme_error_rate_pct": round(100.0 * ph_err / max(ph_len, 1), 4),
        "accent_phrase_sentence_agreement_pct": round(100.0 * ok_ap / n, 3),
        "a123_sentence_agreement_pct": round(100.0 * ok_a / n, 3),
        "a123_error_rate_pct": round(100.0 * a_err / max(a_len, 1), 4),
        "phoneme_ok_but_accent_wrong": ok_acc_only,
        "hyp_exceptions": hyp_err,
    }
    if vocab is not None:
        res["token_coverage_pct"] = round(100.0 * tok_in / max(tok_tot, 1), 3)
        res["sentence_full_coverage_pct"] = round(100.0 * sent_cov / n, 3)
        res["tokens_total"] = tok_tot
        res["tokens_oov"] = tok_tot - tok_in
    return res, fails


# ---------------------------------------------------------------- run mode
LEVELS = [int(x) for x in os.environ.get("B0_LEVELS", "5000,10000,20000,30000,60000,100000,200000").split(",")]
VARIANTS = os.environ.get("B0_VARIANTS", "full,noread").split(",")
EVALSETS = {
    "heldout": "./data/splits/corpus_heldout.tsv",
    "embedded": "./data/splits/corpus_embedded.tsv",
}


def do_run(wd, outjson):
    me = os.path.abspath(__file__)
    def dump(dictdir, es, tag):
        p = f"{wd}/dmp_{tag}_{es}.jsonl"
        if not os.path.exists(p):
            subprocess.run([PY, me, "dump", dictdir, EVALSETS[es], p], check=True)
        return p
    report = {"levels": {}, "failures": {}}
    refs = {es: load_jsonl(dump(SRC, es, "REF")) for es in EVALSETS}
    for N in LEVELS:
        for V in VARIANTS:
            vf = f"{wd}/vocab_{N}g.json" if V == "guard" else f"{wd}/vocab_{N}.json"
            vocab = set(json.load(open(vf, encoding="utf-8")))
            d = os.path.abspath(f"{wd}/rt_{N}_{V}")
            size = sum(os.path.getsize(f"{d}/{f}") for f in ("sys.dic", "matrix.bin", "char.bin", "unk.dic"))
            for es in EVALSETS:
                hyp = load_jsonl(dump(d, es, f"{N}_{V}"))
                res, fails = compare(refs[es], hyp, vocab)
                res["size_bytes"] = size
                res["surfaces"] = N
                key = f"{N}_{V}_{es}"
                report["levels"][key] = res
                report["failures"][key] = fails
                print(key, json.dumps({k: v for k, v in res.items() if k != "size_bytes"}, ensure_ascii=False))
    json.dump(report, open(outjson, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        do_dump(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "run":
        do_run(sys.argv[2], sys.argv[3])
