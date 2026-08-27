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

#: 本文が入るキー（実測で特定した 5 つ）。
#: ⚠️ 新しいレポートを足したら `--check` で確認すること
TEXT_KEYS = ("text", "ref", "student_hyp", "teacher_hyp",
             "example_divergent_sentence", "word",
             "heldout", "train")   # corpus_stats.json の near_dup_examples
PLACEHOLDER = "<redacted: corpus text>"


def load_corpus_texts() -> set[str]:
    out: set[str] = set()
    for sp in ("train", "heldout", "embedded", "sibdense"):
        p = pathlib.Path(f"data/splits/corpus_{sp}.tsv")
        if not p.exists():
            continue
        for r in csv.reader(open(p), delimiter="\t"):
            if r and r[0] != "source" and len(r) >= 3:
                out.add(r[2].strip())
    return out


def scrub(obj, texts: set[str], stat: dict):
    """本文を含む値を置き換える。**構造は変えない**（統計や uid は残す）。"""
    if isinstance(obj, dict):
        return {k: (_redact(k, v, texts, stat) if isinstance(v, str)
                    else scrub(v, texts, stat))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x, texts, stat) for x in obj]
    return obj


def _redact(key: str, val: str, texts: set[str], stat: dict) -> str:
    s = val.strip()
    if key in TEXT_KEYS and (s in texts or _contains(s, texts)):
        stat[key] = stat.get(key, 0) + 1
        return PLACEHOLDER
    return val


def _contains(s: str, texts: set[str]) -> bool:
    """本文の一部（先頭 N 文字など）が埋まっている場合も拾う。"""
    if len(s) < 10:
        return False
    return any(s in t or t in s for t in texts if len(t) >= 10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", default="reports",
                    help="複数指定は --root A --root B ではなくカンマ区切り")
    args = ap.parse_args()

    texts = load_corpus_texts()
    if not texts:
        raise SystemExit("コーパスが読めない。data/splits/ が要る")
    print(f"コーパス本文 {len(texts):,} 件を照合対象にする")

    total = 0
    roots = [pathlib.Path(x) for x in args.root.split(",")]
    files = sorted(f for r in roots for f in r.rglob("*.json"))
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
