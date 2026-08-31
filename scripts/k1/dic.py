"""NAIST-JDIC entries.tsv のローダと、Viterbi を使わない分割戦略。

entries.tsv の 1 行: surface \t lc \t rc \t posid \t wcost \t feature(11 フィールド)
MeCab が出す feature 文字列は先頭に surface が付いた 12 フィールドなので、
`surface + "," + feature` で run_njd_from_mecab に渡せる形になる。
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402

import os
import sys

SP = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 事前に置かれていた entries.tsv は piper-plus 側の sys.dic (103,082,017 B) から
# 作られていて、pyopenjtalk が実際に読む sys.dic (103,131,410 B) とは別物だった。
# 必ず pyopenjtalk が読む方から作った entries_pj.tsv を使う。
ENTRIES = os.path.join(_WORK, "entries_pj.tsv")




# ---------------------------------------------------------------------------
# OpenJTalk の text2mecab が MeCab の前に掛ける半角→全角の写像。
# **実測で作った**（ASCII 0x21..0x7E を 1 文字ずつ MeCab に食わせて表層を読んだ）。
# 再現: scratchpad/han2zen.json を作ったコード。全 94 文字が 1:1 なので
# 文字オフセットはずれない。⚠️ 半角カナ (U+FF61..FF9F) は未対応
# （held-out 2,333 文には 1 文字も無いことを実測済み）。
import json as _json
_H2Z = _json.load(open(os.path.join(_WORK, "han2zen.json"), encoding="utf-8"))


def han2zen(text: str) -> str:
    return "".join(_H2Z.get(c, c) for c in text)



def load_dic(path: str = ENTRIES):
    """surface -> list[(lc, rc, posid, wcost, feature)] と最大表層長を返す。"""
    dic: dict[str, list] = {}
    maxlen = 0
    n_rows = 0
    n_badfields = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) != 6:
                n_badfields += 1
                continue
            surf, lc, rc, pid, cost, feat = p
            n_rows += 1
            if len(surf) > maxlen:
                maxlen = len(surf)
            dic.setdefault(surf, []).append(
                (int(lc), int(rc), int(pid), int(cost), feat))
    return dic, maxlen, n_rows, n_badfields


# ---------------------------------------------------------------------------
# 文字クラス（未知語のフォールバックをまとめる範囲を決めるためだけに使う）
# MeCab の char.bin のカテゴリではない。**簡略版であることを明示する。**
def charclass(ch: str) -> str:
    o = ord(ch)
    if 0x3041 <= o <= 0x309F:
        return "HIRAGANA"
    if 0x30A1 <= o <= 0x30FF or o == 0x30FC:
        return "KATAKANA"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return "KANJI"
    if ch.isdigit():
        return "NUMERIC"
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or (0xFF21 <= o <= 0xFF5A):
        return "ALPHA"
    if ch.isspace():
        return "SPACE"
    return "SYMBOL"


UNK_FEATURE = "名詞,一般,*,*,*,*,{surf},*,*,*/*,*"


def segment(text: str, dic, maxlen: int, mode: str):
    """辞書引きだけで分割する。戻り値: list[(start, end, feature12, is_unk)]。

    mode:
      'longest_first'   最長一致 / 同表層は辞書の登場順で先頭（コストを一切見ない）
      'longest_wcost'   最長一致 / 同表層のうち wcost 最小（= 依頼の戦略 B）
      'min_wcost'       全長さの候補から wcost 最小（長さを優先しない）
      'min_density'     全長さの候補から wcost/文字数 最小（= 依頼の戦略 C の変種）
    """
    n = len(text)
    out = []
    i = 0
    while i < n:
        hi = min(maxlen, n - i)
        best = None  # (surface, entry)
        if mode in ("longest_first", "longest_wcost"):
            for L in range(hi, 0, -1):
                s = text[i:i + L]
                e = dic.get(s)
                if e is not None:
                    ent = e[0] if mode == "longest_first" else min(e, key=lambda x: x[3])
                    best = (s, ent)
                    break
        else:
            key = (lambda s, x: x[3]) if mode == "min_wcost" else (lambda s, x: x[3] / len(s))
            bk = None
            for L in range(1, hi + 1):
                s = text[i:i + L]
                e = dic.get(s)
                if e is None:
                    continue
                for ent in e:
                    k = key(s, ent)
                    if bk is None or k < bk:
                        bk = k
                        best = (s, ent)
        if best is not None:
            s, ent = best
            out.append((i, i + len(s), s + "," + ent[4], False))
            i += len(s)
            continue
        # --- 未知語フォールバック（簡略版・MeCab の unk.dic とは別物）------
        cls = charclass(text[i])
        j = i + 1
        while j < n and charclass(text[j]) == cls and text[j] not in dic:
            j += 1
        s = text[i:j]
        out.append((i, j, s + "," + UNK_FEATURE.format(surf=s), True))
        i = j
    return out
