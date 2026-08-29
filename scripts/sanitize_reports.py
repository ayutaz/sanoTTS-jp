#!/usr/bin/env python3
"""公開前に reports から**コーパス本文**を落とす。

⚠️ **なぜ要るか**: `.gitignore` で `data/splits/corpus_*.tsv` は除外しているが、
`reports/*.json` に本文が漏れていた（実測 CV 316 件 / JSUT 315 件）。

| ソース | ライセンス | 再配布 |
|---|---|---|
| ROHAN4600 / ITA | CC0 / PD | ✅ |
| **Common Voice** | CC0 だが**根拠が弱い**（repo は MPL-2.0、文自体の CC0 明示なし） | ⚠️ |
| **JSUT** | subset 別 **CC-BY-SA**（継承が要る） | ⚠️ |

**uid と統計は残す。** uid があれば、コーパスを自分で取得した人は本文を復元でき、
再現性は落ちない。**本文そのものを再配布しないのが目的。**

実行:
    uv run python scripts/sanitize_reports.py --check    # 検出のみ
    uv run python scripts/sanitize_reports.py --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess

#: ⚠️ **キー名で判定しない。** かつて許可リスト方式（下記）だったが、
#: リストに無いキー（`mismatch_examples` / `L3_student_hyp`）の本文を素通しし、
#: **公開済みリポジトリに 32 箇所の本文を残した**（C-028）。
#: 判定は「値がコーパス本文と一致するか」だけで行う。キー名は報告にのみ使う。
#: 旧: TEXT_KEYS = ("text", "ref", "student_hyp", ...)
PLACEHOLDER = "<redacted: corpus text>"

#: 照合する最小文字数。これ未満は「本文の再配布」に当たらないので見ない。
#: ⚠️ 下げると単独モーラ・単語が誤検知される（C-028）。
MIN_TEXT_LEN = 8


def load_corpus_texts() -> set[str]:
    out: set[str] = set()
    for sp in ("train", "heldout", "embedded", "sibdense"):
        p = pathlib.Path(f"data/splits/corpus_{sp}.tsv")
        if not p.exists():
            continue
        for r in csv.reader(open(p), delimiter="\t"):
            # ⚠️ **短い文字列を照合対象にしない。** 単独のモーラ（`し` など）が
            #    コーパス行と一致すると、無関係な評価データまで伏せてしまう
            #    （C-028 の修正時に実際に踏んだ）。**再配布が問題になるのは文であって字ではない。**
            if r and r[0] != "source" and len(r) >= 3:
                t = r[2].strip()
                if len(t) >= MIN_TEXT_LEN:
                    out.add(t)
    return out


def scrub(obj, texts: set[str], stat: dict):
    """本文を含む値を置き換える。**構造は変えない**（統計や uid は残す）。"""
    if isinstance(obj, dict):
        return {k: (_redact(k, v, texts, stat) if isinstance(v, str)
                    else scrub(v, texts, stat))
                for k, v in obj.items()}
    if isinstance(obj, list):
        # ⚠️ **リスト直下の素の文字列も検査する。**
        #    以前は `scrub(x)` に丸投げしていたため、文字列が最後の `return obj` で
        #    素通りし、`mismatch_examples: ["<本文>", ...]` を取りこぼした（C-028）。
        return [(_redact("[]", x, texts, stat) if isinstance(x, str)
                 else scrub(x, texts, stat)) for x in obj]
    if isinstance(obj, str):
        return _redact("<root>", obj, texts, stat)
    return obj


def _redact(key: str, val: str, texts: set[str], stat: dict) -> str:
    s = val.strip()
    # ⚠️ **キー名を条件にしない**（C-028）。値がコーパス本文なら、どのキーでも落とす。
    if s in texts or _contains(s, texts):
        stat[key] = stat.get(key, 0) + 1
        return PLACEHOLDER
    return val


def _contains(s: str, texts: set[str]) -> bool:
    """本文の一部（先頭 N 文字など）が埋まっている場合も拾う。"""
    if len(s) < 10:
        return False
    return any(s in t or t in s for t in texts if len(t) >= 10)


def _git_ignored(path) -> bool:
    """`path` が git の追跡外か。**`.gitignore` を自前で解釈しない** — git に聞く。

    ⚠️ git が無い / リポジトリ外なら **False（＝走査する）** を返す。
    判定できないときに「安全」に倒すと、本番で検出が黙って止まる。
    """
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    # ⚠️ **既定で追跡対象すべてを見る**（C-028）。`reports` だけを見ていたため
    #    `csrc/ids_heldout.json` の本文 24 件を取りこぼし、公開してしまった。
    ap.add_argument("--root", default="reports,csrc,data/splits,esp32",
                    help="複数指定は --root A --root B ではなくカンマ区切り")
    args = ap.parse_args()

    texts = load_corpus_texts()
    if not texts:
        raise SystemExit("コーパスが読めない。data/splits/ が要る")
    print(f"コーパス本文 {len(texts):,} 件を照合対象にする")

    total = 0
    roots = [pathlib.Path(x) for x in args.root.split(",")]
    # ⚠️ **git が追跡しないものは走査しない。** 公開されないので伏せる必要が無く、
    # 毎回検出されると「21 箇所」が常態化して**本物の漏洩を見落とす**（狼少年になる）。
    # ⚠️ ただし「追跡外だから安全」を鵜呑みにしない — `git check-ignore` に
    # 実際に問い合わせる。`.gitignore` を消した瞬間に検出が復活するのが正しい。
    cand = sorted(f for r in roots for f in r.rglob("*.json"))
    files = [f for f in cand if not _git_ignored(f)]
    n_skip = len(cand) - len(files)
    if n_skip:
        print(f"⚠️ git 追跡外の {n_skip} ファイルは走査しない（公開されないため）")
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        stat: dict = {}
        out = scrub(d, texts, stat)
        n = sum(stat.values())
        if not n:
            continue
        total += n
        print(f"  {str(f):<44} {n:>5} 箇所  {stat}")
        if args.apply:
            f.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"\n合計 {total} 箇所" + ("（書き換えた）" if args.apply
                                    else "（--apply で書き換える）"))
    return 1 if (total and not args.apply) else 0


if __name__ == "__main__":
    raise SystemExit(main())
