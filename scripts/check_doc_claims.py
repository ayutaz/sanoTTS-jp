#!/usr/bin/env python3
"""ドキュメントの主張のうち、**機械で照合できる 2 つ**を見る。

どちらも 2026-09-12 に**実際に壊れていた**ので作った。既存のゲートは**構造的に見られない**:

| ゲート | 見るもの | この 2 つを見落とした理由 |
|---|---|---|
| `check_doc_links.py` | md のリンク | **コードフェンスの中を見ない** / リンクでない名指しも見ない |
| `check_doc_commands.py` | `uv run …` / `bash …` の実体 | **表のセルの素の `` `scripts/x.py` `` は拾わない**（正規表現が `uv run` 等の前置を要求し、`[^\\n|]*?` が `|` で止まる） |

---

## G-D1. **引用した実機ログの数値が、引いたログに実在するか**

⚠️ **これは実際に 2 か所で壊れていた**（[C-064](../docs/decisions.md#c-064) の 5 度目）。
`README.en.md` は **v3 のログを引きながら v4 の checksum を載せていた** —
つまり**どの実機も出していない出力**を「実機の生ログから抜粋」と書いていた。
`README.md` も 2 行（`25.69 ms` / `init 21.56 ms`）が v3 のままだった。

**checksum だけ一括置換して、同じブロックの時間を置換しない**と必ずこの形になる。
ブロックの数値が**すべて**引用先のログに在ることを要求すれば止まる。

⚠️ **見ないもの**: 数値が**同じ行**に在るか（ログのどこかに在ればよい）/
ログに無い散文の主張 / 抜粋の切り方。

## G-D2. **案内文書が名指ししたリポジトリ内のパスが実在するか**

⚠️ **これも実際に壊れていた** — 消したスクリプト 2 本が `CLAUDE.md` のゲート表に、
消したリリースノートが `.github/workflows/README.md` に残っていた。

⚠️ **見ないもの**: `docs/decisions.md` と `docs/measurements.md`（**追記専用の一次ソース**で、
消えた名前が出てくるのは**当時の記録**）/ 手元にしか無い生成物（`reports/` `runs/` など）/
piper-plus 側のパス（`src/python/…`）。

---

    uv run --no-project python scripts/check_doc_claims.py
    uv run --no-project python scripts/check_doc_claims.py --self-test   # 陽性対照 5 件
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------- G-D1
# 実機ログを引いている文書。ブロックの直後に `reports/**.log` へのリンクがある形。
LOG_DOCS = ["README.md", "README.en.md", "esp32/README.md", "esp32/TESTING.md"]

FENCE = re.compile(r"^```")
LOG_LINK = re.compile(r"\((reports/[\w./-]+\.log)\)")

# ⚠️ **16 進の checksum を先に取る**（`0x390bf4b2aef8f2ec` を `0` と `390` に割らない）
NUM = re.compile(r"0x[0-9a-fA-F]+|\d+(?:[.,]\d+)*")

# 引用行とみなす接頭辞（装置が出す行だけを見る）
DEVICE_PREFIX = ("saanotts:", "saan_spk:", "saan_dict:", "I (", "W (", "E (")

# ⚠️ **ログに無くて当然の数**: 抜粋の省略記号や、読者向けに足した注記の数。
#    ここを広げると G-D1 は空虚になるので、**増やすときは理由を書くこと。**
NUM_ALLOW: set[str] = set()


def _numbers(text: str) -> list[str]:
    """数値トークン。⚠️ **桁区切りは外して比べる**（ログは `27136`、文書は `27,136`）。"""
    return [m.group(0).replace(",", "") for m in NUM.finditer(text)]


def check_quoted_logs(docs: list[str]) -> list[str]:
    """各文書のコードブロックを、直後に引かれたログと突き合わせる。"""
    bad: list[str] = []
    checked_blocks = 0
    checked_nums = 0

    for rel in docs:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"{rel}: 走査対象が無い")
            continue
        lines = p.read_text(encoding="utf-8").split("\n")

        i = 0
        while i < len(lines):
            if not FENCE.match(lines[i]):
                i += 1
                continue
            start = i
            i += 1
            block: list[str] = []
            while i < len(lines) and not FENCE.match(lines[i]):
                block.append(lines[i])
                i += 1
            end = i
            i += 1

            # 装置のログらしい行が無いブロックは対象外
            if not any(b.lstrip().startswith(DEVICE_PREFIX) for b in block):
                continue

            # ブロックの直後 6 行以内にログへのリンクがあるか
            tail = "\n".join(lines[end : end + 6])
            m = LOG_LINK.search(tail)
            if not m:
                continue
            log_rel = m.group(1)
            log_path = ROOT / log_rel
            if not log_path.exists():
                bad.append(f"{rel}:{start + 1} 引いているログが無い: {log_rel}")
                continue
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            log_nums = set(_numbers(log_text))
            checked_blocks += 1

            for off, b in enumerate(block):
                if not b.lstrip().startswith(DEVICE_PREFIX):
                    continue
                for n in _numbers(b):
                    checked_nums += 1
                    if n in NUM_ALLOW or n in log_nums:
                        continue
                    bad.append(
                        f"{rel}:{start + 2 + off} 引いたログに無い数値 {n!r}\n"
                        f"        行  : {b.strip()}\n"
                        f"        ログ: {log_rel}"
                    )

    print(f"G-D1  ログ引用 {checked_blocks} ブロック / 数値 {checked_nums} 個を照合")
    # ⚠️ **0 ブロックなら落とす。** 正規表現が当たらないと「食い違い 0」で緑になる。
    if checked_blocks == 0:
        bad.append("G-D1: 照合できたブロックが 0 — 検出が壊れている（空虚なゲート）")
    return bad


# --------------------------------------------------------------------- G-D2
# ⚠️ **一次ソース 2 本は入れない**（追記専用。消えた名前は当時の記録）。
PATH_DOCS = [
    "CLAUDE.md", "README.md", "README.en.md", "CONTRIBUTING.md",
    "MODEL_CARD.md", "NOTICE.md", "LICENSE-MODEL.md",
    "docs/README.md", "docs/downloads.md", "docs/downloads.en.md",
    "docs/getting-started.md", "docs/getting-started.en.md",
    "docs/support-matrix.md", "docs/support-matrix.en.md",
    "docs/upstream-sanotts.md",
    "esp32/README.md", "esp32/TESTING.md",
    ".github/workflows/README.md",
]

REPO_PATH = re.compile(
    r"(?<![\w/.-])((?:docs|scripts|csrc|esp32|web|\.github|src/saanotts_jp)/[\w./-]*[\w])"
)

# 手元にしか無い / 生成物 / piper-plus 側 / 説明用の名前。**無くて当たり前。**
NOT_TRACKED = re.compile(
    r"^(reports/|runs/|data/|dict_build/|_site/"
    r"|csrc/[\w./-]*\.(bin|json|txt)$"
    r"|esp32/build|esp32/k[248]hw"
    r"|src/python/"          # piper-plus 相対（表の見出しにそう書いてある）
    r"|scripts/(xxx|yyy)\.py$"
    r"|docs/(plan|research|superpowers)(/|$)|docs/requirements\.md$"
    r"|docs/release-notes(/|$)"  # 2026-09-12 に削除。正典は GitHub Releases
    r")"
)

# ⚠️ **この見出しの節は丸ごと見ない。** 中のパスは**別リポジトリ相対**なので、
#    ここに無くて当たり前である（見出し自身がそう書いている）。
SKIP_SECTIONS = {
    "CLAUDE.md": ["## piper-plus の参照点"],
}


def check_named_paths(docs: list[str], min_paths: int = 100) -> list[str]:
    bad: list[str] = []
    seen = 0
    for rel in docs:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"{rel}: 走査対象が無い")
            continue
        skip_heads = SKIP_SECTIONS.get(rel, [])
        in_skipped = False
        for ln, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            if line.startswith("## "):
                in_skipped = any(line.startswith(h) for h in skip_heads)
            if in_skipped:
                continue
            for m in REPO_PATH.finditer(line):
                ref = m.group(1)
                # ⚠️ `csrc/golden*.bin` のような glob は、**先頭だけ**が当たる。パスではない。
                if line[m.end() : m.end() + 1] in ("*", "?"):
                    continue
                if NOT_TRACKED.match(ref):
                    continue
                seen += 1
                if not (ROOT / ref).exists():
                    bad.append(f"{rel}:{ln} 実在しないパス: {ref}\n        {line.strip()[:110]}")
    print(f"G-D2  名指しされたパス {seen} 件を照合")
    if seen < min_paths:
        bad.append(f"G-D2: 拾えたパスが {seen} 件しかない — 検出が壊れている（空虚なゲート）")
    return bad


# ----------------------------------------------------------------- self-test
def self_test() -> int:
    """⚠️ **陽性対照。** わざと壊したものが落ちなければ、このゲートは空虚である。"""
    import tempfile

    ok = True
    cases: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "reports").mkdir()
        log = tmp / "reports" / "dev.log"
        log.write_text(
            "I (100) saanotts: 定常 xRT = 0.448（中央値 / 92.88 ms）\n"
            "I (200) saanotts: 出力 PCM: 27136 sample / FNV-1a 0x390bf4b2aef8f2ec\n",
            encoding="utf-8",
        )

        def doc(body: str) -> pathlib.Path:
            f = tmp / "d.md"
            f.write_text(body, encoding="utf-8")
            return f

        good = (
            "```\n"
            "saanotts: 定常 xRT = 0.448（中央値 / 92.88 ms）\n"
            "saanotts: 出力 PCM: 27,136 sample / FNV-1a 0x390bf4b2aef8f2ec\n"
            "```\n\n*[`reports/dev.log`](reports/dev.log) から抜粋*\n"
        )

        global ROOT
        real_root = ROOT
        ROOT = tmp
        try:
            # 1) 正しい引用は通る（陰性対照）
            doc(good)
            cases.append(("正しい引用は通る", not check_quoted_logs(["d.md"])))

            # 2) checksum だけ差し替え = 今回の事故そのもの
            doc(good.replace("0x390bf4b2aef8f2ec", "0xa69a7ebbb5ccb05f"))
            cases.append(("checksum の差し替えを捕まえる", bool(check_quoted_logs(["d.md"]))))

            # 3) 時間だけ古いまま = README.md で起きた形
            doc(good.replace("92.88", "92.99"))
            cases.append(("時間の取り違えを捕まえる", bool(check_quoted_logs(["d.md"]))))

            # 4) sample 数の桁区切りは通る（誤検知しない）
            doc(good.replace("27,136", "27136"))
            cases.append(("桁区切りの有無で誤検知しない", not check_quoted_logs(["d.md"])))

            # 5) 実在するパスだけなら通る（陰性対照）
            #    ⚠️ **件数の下限を 1 に下げて呼ぶ** — 下げないと「空虚ガード」の方が
            #       先に落ちて、**本来の検出を 1 度も試さないまま緑/赤になる**。
            (tmp / "docs").mkdir()
            (tmp / "docs" / "x.md").write_text("", encoding="utf-8")
            doc("`docs/x.md` を読む\n")
            cases.append(("実在するパスは通る", not check_named_paths(["d.md"], min_paths=1)))

            # 6) 実在しないパスを捕まえる
            doc("`docs/x.md` と `scripts/gone.py` を打つ\n")
            cases.append(("実在しないパスを捕まえる",
                          bool(check_named_paths(["d.md"], min_paths=1))))

            # 7) 件数の下限そのものが効くか（正規表現が当たらなくなった形）
            doc("なにも名指ししていない\n")
            cases.append(("拾えたパスが 0 件なら落とす",
                          bool(check_named_paths(["d.md"], min_paths=1))))
        finally:
            ROOT = real_root

    print()
    for name, passed in cases:
        print(f"  {'OK  ' if passed else 'NG  '}{name}")
        ok &= passed
    print()
    if ok:
        print(f"OK  陽性対照 {len(cases)} 件すべて期待どおり")
        return 0
    print("NG  陽性対照が落ちた — このゲートは信用できない")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="陽性対照を回す")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    bad = check_quoted_logs(LOG_DOCS) + check_named_paths(PATH_DOCS)
    print()
    if bad:
        for b in bad:
            print(f"NG  {b}")
        print(f"\nNG  {len(bad)} 件")
        return 1
    print("OK  引用した数値はすべて実ログに在り、名指しされたパスはすべて実在する")
    print("⚠️ 見ていないもの: 数値が**同じ行**に在るか / ログに無い散文の主張 / "
          "一次ソース 2 本（追記専用なので対象外）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
