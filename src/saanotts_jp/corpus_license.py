"""蒸留テキストのライセンス判定（D-054 / spec 2026-09-08）。

**モデルの重みに継承 (copyleft) が及ぶ経路を蒸留テキストから消す**ための表。
JSUT ver1.1 のテキストは CC-BY-SA-4.0 で、本プロジェクトが使う素材の中で
**唯一の継承付き**（D-035 の「残るリスク」）。

⚠️ **完全一致で判定する。前方一致にしない。**
`cv/` の前方一致だと、Common Voice が `europarl-VERSION-LANG.txt` 由来の
ファイルを足したとき自動で許可される。europarl は CV の README が挙げる
**唯一の CC0 例外**（C-029）。**未知の source は UNKNOWN に落として人に判断させる。**

⚠️ **この表はライセンスだけを見る。** 教師の FT テキストとの重複除外
（uid 単位・B-10）は `gen_teacher_labels.py` の `load_exclusions()` が別に行う。
"""

from __future__ import annotations

import enum


class Verdict(enum.Enum):
    """判定。**UNKNOWN は「拒否」ではなく「人に聞く」**。"""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


# source -> ライセンス識別子。
# ⚠️ 一次ソースで確認した値だけを書く（C-029 / C-030 / C-031 は
#    台帳の `verified: false` を転記して 3 件とも間違えた）。
ALLOWED: dict[str, str] = {
    # Common Voice ja — CC0-1.0。一次ソースは common-voice の README（C-029）
    "cv/sentence_collector": "CC0-1.0",
    "cv/singleword-benchmark": "CC0-1.0",
    # ⚠️ 個別の CC0 waiver は確認できていない（探した範囲: PR #3968 の本文 /
    #    リポジトリ内 `yumie` 全文検索 0 件）。CV の一括規約でのみ担保。
    #    **継承付きではない**のでゴールを脅かさず、外すと 13,092 行で論文水準を下回る
    #    ため残す（spec §8）
    "cv/yumie-text-1": "CC0-1.0",
    # ROHAN4600 — 一次ソースに「ライセンスはパブリックドメインです．」+ CC0 バッジ
    "rohan4600": "CC0-1.0",
    # ITA — 一次ソース（mmorise/ita-corpus README）は「パブリックドメインです．」。
    # ⚠️ **CC0 の付与ではない**（C-073）。二次情報の「CC BY-SA 4.0」は誤り
    "ita/recitation324": "PD",
    "ita/emotion100": "PD",
    # 自作（疑問 EOS の 4 種）
    "curated/question_eos": "MIT",
}

# source -> 除外の理由。
# ⚠️ **「表に無い」と「明示的に拒否」を区別する**ため、拒否も列挙する。
#    列挙しないと、JSUT の subset が UNKNOWN になって「人に聞く」側に落ちる。
DENIED: dict[str, str] = {
    f"jsut/{sub}": "JSUT ver1.1 = CC-BY-SA-4.0（唯一の継承付き。D-054）"
    for sub in ("basic5000", "travel1000", "utparaphrase512", "onomatopee300",
                "loanword128", "repeat500", "countersuffix26", "voiceactress100")
}
# ⚠️ precedent130 だけは PD とされているが、**一緒に落とす**（2026-09-08 ユーザー判断）。
# 117 行のために PD かどうかを一次ソースで検証する手間が見合わない
DENIED["jsut/precedent130"] = (
    "JSUT の一部。PD とされるが検証せず一緒に落とす（117 行。D-054）")


def classify(source: str) -> tuple[Verdict, str]:
    """`(判定, 理由)` を返す。"""
    if source in ALLOWED:
        return Verdict.ALLOWED, ALLOWED[source]
    if source in DENIED:
        return Verdict.DENIED, DENIED[source]
    return Verdict.UNKNOWN, (
        f"表に無い source {source!r}。ライセンスを一次ソースで確認して "
        f"src/saanotts_jp/corpus_license.py の ALLOWED か DENIED に足すこと")
