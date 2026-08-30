"""戦略 D: 連接コスト行列 matrix.bin を縮めたら、どれだけ壊れるか。

手順:
  1. 自前 Viterbi (素の matrix.bin) が pyopenjtalk と一致することを示す ← これが上限
  2. matrix を量子化 / 低ランク近似して、同じ指標で劣化を測る
     - int8 対称量子化（スケール 1 つ）           3,792,258 B -> 1,896,129 B
     - int4                                        -> 948,065 B
     - 低ランク近似 rank r（float16 の 2 因子）    -> 2*1377*r*2 B
     - 全部ゼロ（= 連接コストを捨てる）            -> 0 B ★これが「Viterbi をやめる」に一番近い
指標は measure.py と同じものを使う。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import json
import os
import sys
import time

import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import dic as D  # noqa: E402
import measure as M  # noqa: E402
import viterbi as V  # noqa: E402
import pyopenjtalk  # noqa: E402


def variants(mat, lsize, rsize):
    """(名前, 行列, バイト数) のリスト。バイト数は係数の格納量だけを数える。"""
    out = [("full_int16", mat, lsize * rsize * 2)]

    a = np.abs(mat).max()
    s8 = a / 127.0
    q8 = np.rint(np.clip(mat / s8, -127, 127)).astype(np.int8)
    out.append(("int8_sym", np.rint(q8.astype(np.float64) * s8).astype(np.int32),
                lsize * rsize))

    s4 = a / 7.0
    q4 = np.rint(np.clip(mat / s4, -7, 7))
    out.append(("int4_sym", np.rint(q4 * s4).astype(np.int32), lsize * rsize // 2))

    m2 = mat.reshape(rsize, lsize).astype(np.float32)   # 行 = 右ノードの lcAttr
    u, sv, vt = np.linalg.svd(m2, full_matrices=False)
    for r in (256, 128, 64, 32, 16, 8):
        approx = (u[:, :r] * sv[:r]) @ vt[:r]
        out.append((f"lowrank_r{r}", np.rint(approx).astype(np.int32).reshape(-1),
                    (rsize * r + r * lsize) * 2))

    # 行 (右ノードの lcAttr) ごとにスケールを持つ int8。表引きは O(1) のまま。
    m2 = mat.reshape(rsize, lsize).astype(np.float64)
    rs_ = np.abs(m2).max(axis=1, keepdims=True) / 127.0
    rs_[rs_ == 0] = 1.0
    q = np.rint(np.clip(m2 / rs_, -127, 127))
    out.append(("int8_perrow", np.rint(q * rs_).astype(np.int32).reshape(-1),
                lsize * rsize + rsize * 4))

    # 低ランク因子をさらに int8 にする（因子ごとに 1 スケール）
    for r in (128, 64):
        A = (u[:, :r] * sv[:r]).astype(np.float64)
        B = vt[:r].astype(np.float64)
        sa = np.abs(A).max() / 127.0
        sb = np.abs(B).max() / 127.0
        Aq = np.rint(np.clip(A / sa, -127, 127)) * sa
        Bq = np.rint(np.clip(B / sb, -127, 127)) * sb
        out.append((f"lowrank_int8_r{r}", np.rint(Aq @ Bq).astype(np.int32).reshape(-1),
                    rsize * r + r * lsize + 8))

    out.append(("all_zero", np.zeros_like(mat), 0))
    # 陰性対照: 行列を壊したら壊れることを見せる
    rng = np.random.default_rng(0)
    out.append(("shuffled_NEGCTRL", mat[rng.permutation(mat.size)], lsize * rsize * 2))
    return out


def main():
    t0 = time.time()
    DIC, MAXLEN, _, _ = D.load_dic()
    T = V.Tok(DIC, MAXLEN)
    rows = []
    with open(M.CORPUS, encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                rows.append(D.han2zen(p[2]))
    if len(sys.argv) > 1:
        rows = rows[:int(sys.argv[1])]
    print(f"# n={len(rows)} sentences", flush=True)

    # 基準 A を一度だけ作る
    ref = []
    for text in rows:
        feats_a, morphs_a = pyopenjtalk.run_mecab_detailed(text)
        njd_a = pyopenjtalk.apply_postprocessing(
            text, pyopenjtalk.run_njd_from_mecab(feats_a))
        lab_a = pyopenjtalk.make_label(njd_a)
        ref.append({
            "text": text,
            "tok": [(m["char_span"][0], m["char_span"][1], ",".join(m["features"]))
                    for m in morphs_a],
            "pron": "".join(M.pron_of(x.split(",")) for x in feats_a),
            "ph": [l.split("-", 1)[1].split("+", 1)[0] for l in lab_a],
            "ap": M.accent_phrases(njd_a),
        })
    print(f"# reference built t={time.time() - t0:.0f}s", flush=True)

    res = {}
    for name, mm, nbytes in variants(T.mat, T.lsize, T.rsize):
        tok_ok = pron_ok = ph_ok = ap_ok = 0
        ph_ed = ph_n = ap_ed = ap_n = pron_ed = pron_n = pron_del = 0
        for r in ref:
            path = T.parse(r["text"], mm)
            tok = [(a, b, f) for a, b, f, _ in path]
            tok_ok += tok == r["tok"]
            feats_b = [f for _, _, f, _ in path
                       if not M.is_space_symbol(f.split(","))]
            njd_b = pyopenjtalk.apply_postprocessing(
                r["text"], pyopenjtalk.run_njd_from_mecab(feats_b))
            lab_b = pyopenjtalk.make_label(njd_b)
            ph_b = [l.split("-", 1)[1].split("+", 1)[0] for l in lab_b]
            ap_b = M.accent_phrases(njd_b)
            pron_b = "".join(M.pron_of(x.split(",")) for x in feats_b)
            pron_ok += pron_b == r["pron"]
            ph_ok += ph_b == r["ph"]
            ap_ok += ap_b == r["ap"]
            e, _, dl, _ = M.levenshtein(r["pron"], pron_b)
            pron_ed += e
            pron_del += dl
            pron_n += len(r["pron"])
            ph_ed += M.levenshtein(r["ph"], ph_b)[0]
            ph_n += len(r["ph"])
            ap_ed += M.levenshtein(r["ap"], ap_b)[0]
            ap_n += len(r["ap"])
        n = len(ref)
        res[name] = {
            "matrix_bytes": nbytes,
            "token_sentence_exact": tok_ok / n,
            "pron_sentence_exact": pron_ok / n,
            "pron_sentence_exact_ci95": M.wilson(pron_ok, n),
            "pron_CER": pron_ed / pron_n,
            "pron_del": pron_del,
            "phoneme_sentence_exact": ph_ok / n,
            "phoneme_error_rate": ph_ed / ph_n,
            "accphrase_sentence_exact": ap_ok / n,
            "accphrase_sentence_exact_ci95": M.wilson(ap_ok, n),
            "accphrase_error_rate": ap_ed / ap_n,
        }
        print(f"{name:14s} bytes={nbytes:9d} tok={tok_ok / n:.4f} pron={pron_ok / n:.4f} "
              f"CER={pron_ed / pron_n:.4f} ph={ph_ok / n:.4f} PER={ph_ed / ph_n:.4f} "
              f"ap={ap_ok / n:.4f} apER={ap_ed / ap_n:.4f}  t={time.time() - t0:.0f}s",
              flush=True)

    with open(os.path.join(_WORK, "strategy_d_out.json"), "w", encoding="utf-8") as f:
        json.dump({"n_sentences": len(ref), "variants": res}, f,
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
