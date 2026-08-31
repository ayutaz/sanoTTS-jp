"""K-4b: NJD チェーンの検証ベクタを作る。

MeCab の出力（形態素の feature 文字列）を入力に、
`mecab2njd` + `apply_original_rule_before_chaining` + `njd_set_*` を通した
**NJD ノード列**が期待値。

⚠️ **入力と期待値は同じ経路から取る。** 入力を `run_mecab()`、期待値を
`run_frontend()`（テキストから引き直す）にすると、経路が 2 本になって
食い違いの原因が切り分けられない。ここでは
`features = run_mecab(text)` / `expected = run_njd_from_mecab(features)` にして、
**両者が `run_frontend(text, use_vanilla=True)` と一致することを別に数える**。

⚠️ **`use_vanilla=True`**（素の C 実装 + フォークの chaining 前ルールまで）。
K-4 の 4 段は**この後**に適用されるので、ここでは通さない。

⚠️ **数詞・助数詞の文を明示的に足してある。** held-out からの一様抽出だけだと
`njd_set_digit` を抜いても 21 / 600 しか落ちず、**陰性対照がほぼ空虚**になる。
「1つ / 1個 / 1人」で読みが変わる軸は B-0 が挙げたもので、
**抜けても音では気づけない**。

出力（既定 `csrc/k4b_vectors.bin`）:

    magic "K4B1" u32 / n_cases u32
    ケース × n:
        n_feat u32
        入力: MeCab の feature 文字列 × n（u16 長 + UTF-8、"表層,素性..." 形式）
        n_njd u32
        期待: NJD ノード × n
              string, pos, pos_group1..3, ctype, cform, orig, read, pron,
              chain_rule（u16 長 + UTF-8）
              acc i32, mora_size i32, chain_flag i32
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from k1_paths import HELDOUT  # noqa: E402

NJD_FIELDS = ["string", "pos", "pos_group1", "pos_group2", "pos_group3",
              "ctype", "cform", "orig", "read", "pron", "chain_rule"]

# 数詞・助数詞・分数・日付・時刻・金額。`njd_set_digit` と
# `apply_original_rule_before_chaining` の分数規則を踏ませるためのもの。
DIGIT_TEXTS = [
    "りんごを1つください。", "みかんを1個買った。", "1人で行きます。",
    "3人で2個ずつ分けた。", "8人が10日間で1万2千円使った。",
    "2026年8月31日の午前10時30分に始まる。",
    "3分の1を4分の3で割る。", "10分の7は0.7です。",
    "1分間に60回、1時間で3600回。", "第1回から第12回まで。",
    "100円と1000円と1万円。", "5cmと3kgと2リットル。",
    "1番目と2番目と3番目。", "0.5パーセント上がった。",
    "西暦2000年1月1日。", "1階から20階まで。",
    "6人分の1斤を8等分する。", "午後2時15分に1本だけ。",
    "3割2分5厘の打率。", "2分の1の確率で当たる。",
]


# `apply_original_rule_before_chaining` の 12 規則のうち、held-out からの
# 一様抽出では踏めないものを狙って踏む文。⚠️ **候補は当てずっぽうで書かず、
# `--only-texts` で当ててから採用すること**（実際、最初に書いた候補の多くは
# MeCab が 1 語にまとめてしまって規則に届かなかった）。
COVERAGE_TEXTS = [
    # 不足→ブソク
    "栄養不足になりやすい。", "情報不足で判断できない。", "練習不足が響いた。",
    # 〇〇→マル（2 文字以上の連続だけ）
    "〇〇さんが来ました。", "住所は〇〇県〇〇市です。", "〇〇〇に入る言葉を答えよ。",
    # 球→ダマ（直前が漢字＋ひらがな）
    "決め球を投げる。", "隠し球があった。", "落ち球に気をつける。",
    # 分数 フン/プン→ブン と ブ→ブン
    "何分の1か分からない。", "数分の1に減った。", "十分の1になる。",
    "二分の三を計算する。", "三分の五だ。", "五分の四を求める。",
]


def _s(x: str) -> bytes:
    b = str(x).encode("utf-8")
    return struct.pack("<H", len(b)) + b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=600)
    ap.add_argument("--out", default=str(HERE.parent.parent / "csrc/k4b_vectors.bin"))
    ap.add_argument("--only-texts", help="1 行 1 文のファイルだけからベクタを作る"
                                         "（規則の当たりを探すとき用）")
    a = ap.parse_args()

    import pyopenjtalk

    if a.only_texts:
        texts = [ln.strip() for ln in
                 pathlib.Path(a.only_texts).read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        print(f"指定された {len(texts)} 文だけを使う")
    else:
        texts = []
        with open(HELDOUT, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3:
                    texts.append(p[2])
        texts = [texts[int(i * len(texts) / a.cases)] for i in range(a.cases)]
        texts += DIGIT_TEXTS + COVERAGE_TEXTS
        print(f"held-out {a.cases} 文 + 数詞 {len(DIGIT_TEXTS)} 文 "
              f"+ 規則被覆 {len(COVERAGE_TEXTS)} 文")

    cases = []
    n_same = 0
    for t in texts:
        try:
            feats = pyopenjtalk.run_mecab(t)
            njd = pyopenjtalk.run_njd_from_mecab(feats)
            ref = pyopenjtalk.run_frontend(t, use_vanilla=True)
        except Exception:
            continue
        if not feats or not njd:
            continue
        # ⚠️ 「features → NJD」が「テキスト → NJD」と同じであることを毎回数える。
        #    ここがずれていたら、G14a が通っても本番経路とは違うものを見ている。
        if [tuple(str(n[k]) for k in NJD_FIELDS) + (n["acc"], n["mora_size"],
                                                    n["chain_flag"]) for n in njd] == \
           [tuple(str(n[k]) for k in NJD_FIELDS) + (n["acc"], n["mora_size"],
                                                    n["chain_flag"]) for n in ref]:
            n_same += 1
        cases.append((feats, njd))

    print(f"ケース {len(cases)}")
    print(f"run_njd_from_mecab(run_mecab(t)) == run_frontend(t): "
          f"{n_same} / {len(cases)}")
    if n_same != len(cases):
        print("  ⚠️ **経路が一致していない。** G14a が通っても本番と同じものを"
              "見ている保証が無い")
    n_njd = sum(len(n) for _f, n in cases)
    print(f"NJD ノード合計 {n_njd:,d}")

    out = bytearray(struct.pack("<4sI", b"K4B1", len(cases)))
    for feats, njd in cases:
        out += struct.pack("<I", len(feats))
        for f in feats:
            out += _s(f)
        out += struct.pack("<I", len(njd))
        for nd in njd:
            for k in NJD_FIELDS:
                out += _s(nd[k])
            out += struct.pack("<iii", int(nd["acc"]), int(nd["mora_size"]),
                               int(nd["chain_flag"]))
    pathlib.Path(a.out).write_bytes(bytes(out))
    print(f"書き出した → {a.out} ({len(out):,d} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
