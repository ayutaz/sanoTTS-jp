"""C 実装のみ (use_vanilla=True) と既定経路の乖離を、後処理 7 段に帰属させる。

単位を 4 つ同時に出す（C-019 の再発防止 — 単位を混ぜない）:
  U1 音素列       : fullcontext label の -X+ 欄の列（sil/pau 込み）。文単位完全一致
  U2 アクセント句列 : label の /F:f1_f2 から取った (モーラ数, 核位置) の列。文単位完全一致
  U3 piper トークン列: piper_plus_g2p.japanese._phonemize_core と同じ規則で作った
                     音素 + 記号 [ ] # _ + 疑問 EOS の列。文単位完全一致（蒸留の入力そのもの）
  U4 アクセント記号列 : U3 のうち記号 [ ] # だけを (記号, 直前までの音素数) で表した列。文単位完全一致

出力: JSON を stdout の最後に吐く。
"""

from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import copy
import json
import re
import sys
import unicodedata
from collections import Counter

import pyopenjtalk as P
from pyopenjtalk import make_label, modify_filler_accent, predict_nani_reading
from pyopenjtalk.utils import (
    modify_acc_after_chaining,
    modify_kanji_yomi,
    process_odori_features,
    retreat_acc_nuc,
    suppress_unnatural_auxiliary_u_long_vowel,
)

CORPUS = (_ROOT + "/data/splits/corpus_heldout.tsv")

_RE_PH = re.compile(r"-(?P<ph>[^+]+)\+")
_RE_A = re.compile(r"/A:(?P<a1>[\d-]+)\+(?P<a2>[0-9]+)\+(?P<a3>[0-9]+)/")
_RE_F = re.compile(r"/F:(?P<f1>[0-9xX-]+)_(?P<f2>[0-9xX-]+)")

# ---------------------------------------------------------------- piper 側の規則
_SKIP_TOKENS = frozenset(("_", "#", "[", "]", "^", "$", "?", "?!", "?.", "?~"))


def _get_question_type(text: str) -> str:
    s = text.strip()
    if s.endswith("?!") or s.endswith("！？") or s.endswith("？！"):
        return "?!"
    if s.endswith("?.") or s.endswith("。？") or s.endswith("？。"):
        return "?."
    if s.endswith("?~") or s.endswith("～？") or s.endswith("？～"):
        return "?~"
    if s.endswith("?") or s.endswith("？"):
        return "?"
    return "$"


def _apply_n_phoneme_rules(tokens: list[str]) -> list[str]:
    result = list(tokens)
    nxt = None
    for i in range(len(result) - 1, -1, -1):
        tok = result[i]
        if tok not in _SKIP_TOKENS and tok != "N":
            nxt = tok
        elif tok == "N":
            if nxt is None:
                result[i] = "N_uvular"
            elif nxt in ("m", "my", "b", "by", "p", "py"):
                result[i] = "N_m"
            elif nxt in ("n", "ny", "t", "ty", "d", "dy", "ts", "ch"):
                result[i] = "N_n"
            elif nxt in ("k", "ky", "kw", "g", "gy", "gw"):
                result[i] = "N_ng"
            else:
                result[i] = "N_uvular"
            nxt = result[i]
    return result


def piper_tokens(labels: list[str], text: str) -> list[str]:
    """piper_plus_g2p.japanese._phonemize_core と同じ規則（labels を差し替え可能にしただけ）。"""
    tokens: list[str] = []
    qm = _get_question_type(text)
    for idx, label in enumerate(labels):
        m_ph = _RE_PH.search(label)
        if not m_ph:
            continue
        ph = m_ph.group("ph")
        if ph == "sil":
            if idx == 0:
                pass
            elif idx == len(labels) - 1 and qm and qm != "$":
                tokens.append(qm)
            continue
        if ph == "pau":
            tokens.append("_")
            continue
        tokens.append(ph)
        m_p = _RE_A.search(label)
        if m_p:
            a1, a2, a3 = int(m_p.group("a1")), int(m_p.group("a2")), int(m_p.group("a3"))
            if idx < len(labels) - 1:
                m_next = _RE_A.search(labels[idx + 1])
                a2_next = int(m_next.group("a2")) if m_next else -1
            else:
                a2_next = -1
            if (a1 == 0) and (a2_next == a2 + 1):
                tokens.append("]")
            if (a2 == a3) and (a2_next == 1):
                tokens.append("#")
            if (a2 == 1) and (a2_next == 2):
                tokens.append("[")
    return _apply_n_phoneme_rules(tokens)


# ---------------------------------------------------------------- 4 つの単位
def u1_phonemes(labels: list[str], text: str) -> tuple:
    out = []
    for lab in labels:
        m = _RE_PH.search(lab)
        if m:
            out.append(m.group("ph"))
    return tuple(out)


def u2_accent_phrases(labels: list[str], text: str) -> tuple:
    """(F1 モーラ数, F2 核位置) の列。連続する同じ値は 1 つのアクセント句とみなし畳む。

    ⚠️ 畳むので「同じ (F1,F2) のアクセント句が 2 つ連続」は 1 つに見える。
       U3/U4 はこの畳み込みを持たないので、両方を見ること。
    """
    out = []
    prev = None
    for lab in labels:
        m_ph = _RE_PH.search(lab)
        ph = m_ph.group("ph") if m_ph else None
        if ph in ("sil", "pau", None):
            prev = None
            continue
        m = _RE_F.search(lab)
        if not m:
            prev = None
            continue
        cur = (m.group("f1"), m.group("f2"))
        if cur != prev:
            out.append(cur)
        prev = cur
    return tuple(out)


def u3_piper(labels: list[str], text: str) -> tuple:
    return tuple(piper_tokens(labels, text))


_MARKS = ("[", "]", "#")


def u4_marks(labels: list[str], text: str) -> tuple:
    toks = piper_tokens(labels, text)
    out = []
    n_ph = 0
    for t in toks:
        if t in _MARKS:
            out.append((t, n_ph))
        else:
            n_ph += 1
    return tuple(out)


UNITS = {"U1_phoneme": u1_phonemes, "U2_accphrase": u2_accent_phrases,
         "U3_pipertok": u3_piper, "U4_marks": u4_marks}


# ---------------------------------------------------------------- 後処理 7 段
KSET = P._MULTI_READ_KANJI_SET_EXCLUDING_NANI

STAGES = [
    ("filler_accent", lambda t, n, jt: modify_filler_accent(n)),
    ("predict_nani", lambda t, n, jt: predict_nani_reading(n)),
    ("sudachi_kanji_yomi", lambda t, n, jt: modify_kanji_yomi(t, n, KSET)),
    ("suppress_u_long", lambda t, n, jt: suppress_unnatural_auxiliary_u_long_vowel(n)),
    ("retreat_acc_nuc", lambda t, n, jt: retreat_acc_nuc(n)),
    ("acc_after_chaining", lambda t, n, jt: modify_acc_after_chaining(n)),
    ("odori", lambda t, n, jt: process_odori_features(n, jtalk=jt)),
]
STAGE_NAMES = [s[0] for s in STAGES]

# API のフラグで直接切れるのは 2 つだけ。残り 5 段は use_vanilla でしか消せない。
FLAG_CONTROLLED = {"predict_nani", "sudachi_kanji_yomi"}


def run_chain(text: str, raw, jt, enabled: set[str]):
    njd = copy.deepcopy(raw)
    for name, fn in STAGES:
        if name in enabled:
            njd = fn(text, njd, jt)
    return make_label(njd, jtalk=jt)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    rows = []
    with open(CORPUS, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        assert header[:3] == ["source", "id", "text"], header  # C-018: ヘッダを発話にしない
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[2].strip():
                continue
            rows.append((parts[0], parts[1], parts[2]))
    if limit:
        rows = rows[:limit]
    n = len(rows)
    print(f"# corpus rows (header 除去済): {n}", file=sys.stderr)
    print(f"# 先頭 1 件（C-018 対策・目視）: {rows[0]!r}", file=sys.stderr)

    all_enabled = set(STAGE_NAMES)
    variants: dict[str, set[str]] = {"default": all_enabled, "vanilla": set()}
    for s in STAGE_NAMES:
        variants[f"minus:{s}"] = all_enabled - {s}
        variants[f"only:{s}"] = {s}

    # 集計
    disagree = {v: {u: 0 for u in UNITS} for v in variants}
    # 残差分析用に、default vs vanilla / minus:* の不一致文を保存
    examples: dict[str, list] = {v: [] for v in variants}
    gate_manual_ok = 0
    gate_vanilla_api_ok = 0
    gate_neg_detect = 0
    errors = []

    with P._resolve_jtalk(None) as jt:
        for i, (src, uid, text) in enumerate(rows):
            try:
                raw = jt.run_frontend(text)
                labs = {v: run_chain(text, raw, jt, en) for v, en in variants.items()}

                # G1 陽性対照: 手組みのフル鎖 == API の既定経路
                if labs["default"] == P.extract_fullcontext(text):
                    gate_manual_ok += 1
                # G2 陽性対照: 段ゼロ == API の use_vanilla=True
                if labs["vanilla"] == P.extract_fullcontext(text, use_vanilla=True):
                    gate_vanilla_api_ok += 1
                # G3 陰性対照: 意図的に壊した列を比較器に食わせると不一致を検出できるか
                broken = labs["default"][:-1] if len(labs["default"]) > 1 else ["X"]
                if any(UNITS[u](broken, text) != UNITS[u](labs["default"], text)
                       for u in UNITS):
                    gate_neg_detect += 1

                ref = {u: UNITS[u](labs["default"], text) for u in UNITS}
                for v in variants:
                    if v == "default":
                        continue
                    got = {u: UNITS[u](labs[v], text) for u in UNITS}
                    for u in UNITS:
                        if got[u] != ref[u]:
                            disagree[v][u] += 1
                            if len(examples[v]) < 4000:
                                examples[v].append(
                                    {"uid": uid, "src": src, "text": text, "unit": u,
                                     "ref": list(ref[u]), "got": list(got[u])})
            except Exception as e:  # noqa: BLE001
                errors.append({"uid": uid, "text": text, "err": f"{type(e).__name__}: {e}"})
            if (i + 1) % 250 == 0:
                print(f"  ... {i+1}/{n}", file=sys.stderr)

    result = {
        "n": n,
        "n_errors": len(errors),
        "errors": errors[:20],
        "gates": {
            "G1_manual_chain_eq_default": [gate_manual_ok, n],
            "G2_zero_stage_eq_api_vanilla": [gate_vanilla_api_ok, n],
            "G3_negative_control_detects_break": [gate_neg_detect, n],
        },
        "variants": {},
    }
    for v in variants:
        if v == "default":
            continue
        result["variants"][v] = {}
        for u in UNITS:
            k = n - disagree[v][u]
            lo, hi = wilson(k, n)
            result["variants"][v][u] = {
                "agree": k, "n": n, "rate": k / n if n else 0.0,
                "ci95": [lo, hi], "disagree": disagree[v][u],
            }

    out = (_WORK + "/ablate_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    exout = out.replace("ablate_result", "ablate_examples")
    with open(exout, "w", encoding="utf-8") as f:
        json.dump(examples, f, ensure_ascii=False)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
