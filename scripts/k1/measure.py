"""Viterbi は要るのか — 同じ辞書の上で分割戦略を差し替えて一致率を測る。

戦略 A (基準): pyopenjtalk.run_mecab_detailed() = MeCab の完全な Viterbi
              (1377x1377 connection matrix + unk.dic/char.bin)
戦略 B/C     : 辞書引きだけ（接続コストなし・ラティスなし・バックトラックなし）

出力は JSON と人が読む表。全ゲートに陽性/陰性対照を付ける。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import json
import os
import sys
import time

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import pyopenjtalk  # noqa: E402

CORPUS = os.path.expanduser("~/Desktop/saanoTTS-jp/data/splits/corpus_heldout.tsv")
MODES = ["longest_first", "longest_wcost", "min_wcost", "min_density"]


# ---------------------------------------------------------------------------
def wilson(k, n, z=1.96):
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def levenshtein(a, b):
    """(distance, n_sub, n_del, n_ins)。del = a にあって b に無い = 脱落。"""
    la, lb = len(a), len(b)
    if la == 0:
        return lb, 0, 0, lb
    if lb == 0:
        return la, 0, la, 0
    # DP 表を持って backtrace する（脱落を別勘定したいので）
    prev = [(j, 0, 0, j) for j in range(lb + 1)]
    for i in range(1, la + 1):
        cur = [(i, 0, i, 0)] + [None] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            d_sub = prev[j - 1]
            if ai == b[j - 1]:
                cand = (d_sub[0], d_sub[1], d_sub[2], d_sub[3])
            else:
                cand = (d_sub[0] + 1, d_sub[1] + 1, d_sub[2], d_sub[3])
            d_del = prev[j]      # a[i-1] を消す
            c_del = (d_del[0] + 1, d_del[1], d_del[2] + 1, d_del[3])
            d_ins = cur[j - 1]   # b[j-1] を挿入
            c_ins = (d_ins[0] + 1, d_ins[1], d_ins[2], d_ins[3] + 1)
            cur[j] = min(cand, c_del, c_ins, key=lambda t: t[0])
        prev = cur
    return prev[lb]


_A_RE = None


def label_ph_acc(labels):
    """full-context label 列 → [(音素, "A1+A2+A3"), ...]。

    A1/A2/A3 はアクセント核までの距離・アクセント句内モーラ位置・句のモーラ数で、
    **これが実際に喋られる韻律そのもの**。トークン境界の取り方に依存しない。
    """
    global _A_RE
    if _A_RE is None:
        import re
        _A_RE = re.compile(r"/A:([^/]*)")
    out = []
    for l in labels:
        ph = l.split("-", 1)[1].split("+", 1)[0]
        m = _A_RE.search(l)
        a = m.group(1) if m else ""
        out.append((ph, a))
    return out


def accent_phrases(njd):
    """njd features → [(アクセント句の pron, アクセント型), ...]。

    chain_flag == 1 が「前のアクセント句に繋がる」。**トークン境界の取り方に依存しない**
    ので、分割が違っても喋られる韻律が同じなら一致する。
    """
    out = []
    for f in njd:
        if f["chain_flag"] == 1 and out:
            out[-1][0].append(f["pron"])
        else:
            out.append([[f["pron"]], f["acc"]])
    return [("".join(p), a) for p, a in out]


def is_space_symbol(fields):
    return len(fields) >= 3 and fields[1] == "記号" and fields[2] == "空白"


# ⚠️ 未知語 (unk.dic) の feature は 7 フィールドしかなく read/pron/acc/chain を持たない。
# **これが「未知語が誤読ではなく無音で消える」の正体**なので、埋めずに空扱いにする。
def pron_of(fields):
    return fields[9] if len(fields) >= 12 else ""


def acc_of(fields):
    return (fields[10], fields[11]) if len(fields) >= 12 else ("<UNK>", "<UNK>")


# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    DIC, MAXLEN, NROWS, NBAD = D.load_dic()
    print(f"# dic: surfaces={len(DIC)} rows={NROWS} bad_lines={NBAD} maxlen={MAXLEN} "
          f"load={time.time() - t0:.1f}s", flush=True)

    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        header = next(f)          # ヘッダ行は必ず捨てる（C-018）
        assert header.rstrip("\n").split("\t")[:3] == ["source", "id", "text"], header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                rows.append((p[0], p[1], p[2]))
    if len(sys.argv) > 1:
        rows = rows[:int(sys.argv[1])]
    print(f"# corpus: n={len(rows)} sentences (header dropped)", flush=True)
    print(f"# first row (目視用): {rows[0]}", flush=True)

    # ------------------------------------------------------------------
    # 集計器
    st = {m: {
        "bnd_tp": 0, "bnd_a": 0, "bnd_b": 0,
        "span_tp": 0, "span_a": 0, "span_b": 0,
        "pron_sent_ok": 0, "pron_ed": 0, "pron_sub": 0, "pron_del": 0, "pron_ins": 0,
        "pron_len_a": 0,
        "acc_sent_ok": 0, "acc_ed": 0, "acc_len_a": 0,
        "seg_sent_ok": 0,
        "unk_tok": 0, "unk_char": 0, "unk_sent": 0,
        "njd_sent_ok": 0, "njd_ed": 0, "njd_len_a": 0,
        "ph_sent_ok": 0, "ph_ed": 0, "ph_len_a": 0,
        "lab_sent_ok": 0,
        "pa_sent_ok": 0, "pa_ed": 0, "pa_len_a": 0,
        "ap_sent_ok": 0, "ap_ed": 0, "ap_len_a": 0,
        "ok_flags": [],
    } for m in MODES}

    a_unk_tok = a_unk_sent = a_tok = 0
    n_sent = 0
    gate_dic_hit = gate_dic_tot = 0
    gate_feats_ok = 0
    gate_frontend_ok = 0
    gate_norm_ok = 0
    examples = {m: [] for m in MODES}

    for si, (src, uid, raw) in enumerate(rows):
        # OpenJTalk は MeCab の前に半角→全角を掛ける。B/C にも同じ前処理を許す
        # （94 エントリの表なので端末でも持てる）。A は内部で正規化するので不変。
        text = D.han2zen(raw)
        feats_a, morphs_a = pyopenjtalk.run_mecab_detailed(text)
        if [",".join(m["features"]) for m in
                pyopenjtalk.run_mecab_detailed(raw)[1]] == \
           [",".join(m["features"]) for m in morphs_a]:
            gate_norm_ok += 1
        n_sent += 1

        # --- ゲート G-FEAT: morphs から features を再構成できるか（フィルタ規則の確認）
        recon = [",".join(m["features"]) for m in morphs_a
                 if not is_space_symbol(m["features"])]
        if recon == feats_a:
            gate_feats_ok += 1

        # --- ゲート G-DIC: A の既知語 feature が entries_pj.tsv に literal に在るか
        for m in morphs_a:
            a_tok += 1
            if m["is_unknown"]:
                continue
            gate_dic_tot += 1
            fs = m["features"]
            ents = DIC.get(fs[0])
            if ents and any(e[4] == ",".join(fs[1:]) for e in ents):
                gate_dic_hit += 1

        n_unk_a = sum(1 for m in morphs_a if m["is_unknown"])
        a_unk_tok += n_unk_a
        if n_unk_a:
            a_unk_sent += 1

        spans_a = set((m["char_span"][0], m["char_span"][1]) for m in morphs_a)
        bnds_a = set(m["char_span"][0] for m in morphs_a if m["char_span"][0] != 0)

        fa = [f.split(",") for f in feats_a]
        pron_a = "".join(pron_of(x) for x in fa)
        acc_a = [acc_of(x) for x in fa]

        njd_a = pyopenjtalk.run_njd_from_mecab(feats_a)
        njd_a = pyopenjtalk.apply_postprocessing(text, njd_a)
        njd_a_seq = [(f["pron"], f["acc"], f["mora_size"], f["chain_rule"]) for f in njd_a]
        lab_a = pyopenjtalk.make_label(njd_a)
        ph_a = [l.split("-", 1)[1].split("+", 1)[0] for l in lab_a]
        pa_a = label_ph_acc(lab_a)
        ap_a = accent_phrases(njd_a)

        # --- ゲート G-FRONTEND: A の経路が run_frontend と同一か（陽性対照）
        if si < 200:
            ref = pyopenjtalk.run_frontend(text)
            if [(f["string"], f["pron"], f["acc"], f["chain_rule"]) for f in ref] == \
               [(f["string"], f["pron"], f["acc"], f["chain_rule"]) for f in njd_a]:
                gate_frontend_ok += 1

        for m in MODES:
            s = st[m]
            seg = D.segment(text, DIC, MAXLEN, m)
            spans_b = set((a, b) for a, b, _, _ in seg)
            bnds_b = set(a for a, _, _, _ in seg if a != 0)
            s["bnd_tp"] += len(bnds_a & bnds_b)
            s["bnd_a"] += len(bnds_a)
            s["bnd_b"] += len(bnds_b)
            s["span_tp"] += len(spans_a & spans_b)
            s["span_a"] += len(spans_a)
            s["span_b"] += len(spans_b)
            if spans_a == spans_b:
                s["seg_sent_ok"] += 1

            n_unk_b = sum(1 for _, _, _, u in seg if u)
            if n_unk_b:
                s["unk_sent"] += 1
                s["unk_tok"] += n_unk_b
                s["unk_char"] += sum(b - a for a, b, _, u in seg if u)

            feats_b = [f for _, _, f, _ in seg if not is_space_symbol(f.split(","))]
            fb = [f.split(",") for f in feats_b]
            pron_b = "".join(pron_of(x) for x in fb)
            acc_b = [acc_of(x) for x in fb]

            ed, sub, dele, ins = levenshtein(pron_a, pron_b)
            s["pron_ed"] += ed
            s["pron_sub"] += sub
            s["pron_del"] += dele
            s["pron_ins"] += ins
            s["pron_len_a"] += len(pron_a)
            if pron_a == pron_b:
                s["pron_sent_ok"] += 1
            elif len(examples[m]) < 15:
                examples[m].append({"text": text, "A": pron_a, "B": pron_b})

            ed2 = levenshtein(acc_a, acc_b)[0]
            s["acc_ed"] += ed2
            s["acc_len_a"] += len(acc_a)
            if acc_a == acc_b:
                s["acc_sent_ok"] += 1

            njd_b = pyopenjtalk.run_njd_from_mecab(feats_b)
            njd_b = pyopenjtalk.apply_postprocessing(text, njd_b)
            njd_b_seq = [(f["pron"], f["acc"], f["mora_size"], f["chain_rule"]) for f in njd_b]
            s["njd_ed"] += levenshtein(njd_a_seq, njd_b_seq)[0]
            s["njd_len_a"] += len(njd_a_seq)
            if njd_a_seq == njd_b_seq:
                s["njd_sent_ok"] += 1

            lab_b = pyopenjtalk.make_label(njd_b)
            ph_b = [l.split("-", 1)[1].split("+", 1)[0] for l in lab_b]
            pa_b = label_ph_acc(lab_b)
            s["pa_ed"] += levenshtein(pa_a, pa_b)[0]
            s["pa_len_a"] += len(pa_a)
            pa_ok = pa_a == pa_b
            if pa_ok:
                s["pa_sent_ok"] += 1
            s["ok_flags"].append(1 if pa_ok else 0)
            ap_b = accent_phrases(njd_b)
            s["ap_ed"] += levenshtein(ap_a, ap_b)[0]
            s["ap_len_a"] += len(ap_a)
            if ap_a == ap_b:
                s["ap_sent_ok"] += 1
            s["ph_ed"] += levenshtein(ph_a, ph_b)[0]
            s["ph_len_a"] += len(ph_a)
            if ph_a == ph_b:
                s["ph_sent_ok"] += 1
            if lab_a == lab_b:
                s["lab_sent_ok"] += 1

        if (si + 1) % 200 == 0:
            print(f"#   {si + 1}/{len(rows)} t={time.time() - t0:.0f}s", flush=True)

    # ------------------------------------------------------------------
    out = {
        "n_sentences": n_sent,
        "dic": {"surfaces": len(DIC), "rows": NROWS, "maxlen": MAXLEN},
        "gates": {
            "G-DIC_known_feature_in_entries": [gate_dic_hit, gate_dic_tot],
            "G-FEAT_morphs_filter_reproduces_features": [gate_feats_ok, n_sent],
            "G-FRONTEND_A_equals_run_frontend": [gate_frontend_ok, min(200, n_sent)],
            "G-NORM_A_invariant_under_han2zen": [gate_norm_ok, n_sent],
        },
        "A": {"tokens": a_tok, "unknown_tokens": a_unk_tok,
              "sentences_with_unknown": a_unk_sent},
        "strategies": {},
        "examples": examples,
        "per_sentence_phacc_ok": {m: st[m]["ok_flags"] for m in MODES},
    }
    for m in MODES:
        s = st[m]
        p = s["bnd_tp"] / s["bnd_b"] if s["bnd_b"] else 0.0
        r = s["bnd_tp"] / s["bnd_a"] if s["bnd_a"] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        sp = s["span_tp"] / s["span_b"] if s["span_b"] else 0.0
        sr = s["span_tp"] / s["span_a"] if s["span_a"] else 0.0
        sf1 = 2 * sp * sr / (sp + sr) if sp + sr else 0.0
        out["strategies"][m] = {
            "boundary_P": p, "boundary_R": r, "boundary_F1": f1,
            "boundary_n_A": s["bnd_a"], "boundary_n_B": s["bnd_b"],
            "span_P": sp, "span_R": sr, "span_F1": sf1,
            "span_n_A": s["span_a"], "span_n_B": s["span_b"],
            "seg_sentence_exact": s["seg_sent_ok"] / n_sent,
            "pron_sentence_exact": s["pron_sent_ok"] / n_sent,
            "pron_CER": s["pron_ed"] / s["pron_len_a"],
            "pron_sub": s["pron_sub"], "pron_del": s["pron_del"],
            "pron_ins": s["pron_ins"], "pron_ref_chars": s["pron_len_a"],
            "acc_sentence_exact": s["acc_sent_ok"] / n_sent,
            "acc_token_error_rate": s["acc_ed"] / s["acc_len_a"],
            "acc_ref_tokens": s["acc_len_a"],
            "njd_sentence_exact": s["njd_sent_ok"] / n_sent,
            "njd_token_error_rate": s["njd_ed"] / s["njd_len_a"],
            "njd_ref_tokens": s["njd_len_a"],
            "phoneme_sentence_exact": s["ph_sent_ok"] / n_sent,
            "phoneme_error_rate": s["ph_ed"] / s["ph_len_a"],
            "phoneme_ref": s["ph_len_a"],
            "fullcontext_label_sentence_exact": s["lab_sent_ok"] / n_sent,
            "phacc_sentence_exact": s["pa_sent_ok"] / n_sent,
            "phacc_sentence_exact_ci95": wilson(s["pa_sent_ok"], n_sent),
            "accphrase_sentence_exact": s["ap_sent_ok"] / n_sent,
            "accphrase_sentence_exact_ci95": wilson(s["ap_sent_ok"], n_sent),
            "accphrase_error_rate": s["ap_ed"] / s["ap_len_a"],
            "accphrase_ref": s["ap_len_a"],
            "phacc_error_rate": s["pa_ed"] / s["pa_len_a"],
            "phacc_ref": s["pa_len_a"],
            "fallback_unk_tokens": s["unk_tok"],
            "fallback_unk_chars": s["unk_char"],
            "fallback_unk_sentences": s["unk_sent"],
        }

    with open(os.path.join(_WORK, "measure_out.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "examples"},
                     ensure_ascii=False, indent=1))
    print(f"# total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
