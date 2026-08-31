#!/usr/bin/env python3
"""ドキュメントが名乗る M / D / C の範囲と件数が、実体と一致するかを検査する。

    uv run python scripts/check_doc_counters.py

⚠️ **これは「手で直すのを忘れる」ことへの対策**である。番号は測定や訂正を足すたびに
増えるので、**索引を書いた瞬間から古くなる**。実際にこのプロジェクトでは
`M-1〜M-51` / `D-001〜D-034` / `C-001〜C-027` が 4 つのファイルに残っていた（C-042）。
しかも **C-042 を書いた直後にまた 1 つずれた**（訂正を足せば C の最大値が動くので当然）。

**2 つ見る:**

1. **範囲と件数** —「M-1〜M-79」「訂正は 51 件」が実体と一致するか
2. **引用アンカー** — *有名な主張が、正しい M/D 番号と一緒に書かれているか*

2 を足したのは、**1 が通るのに引用が全部間違っている状態が実際に起きた**ため
（C-052）。最大番号 M-79 で一括置換した跡が 3 ファイル 10 箇所に残っていて、
「漢字文から合成まで完走（M-79）」= 実際は M-76、「0.32%（M-79）」= 実際は M-77 の
ようになっていた。**範囲だけ見るゲートは、この形を原理的に捕まえられない**
（M-79 は実在するので）。

⚠️ **このゲートは「番号」しか見ない。** 中身が古いことは検出できない
（例: サイズの実測値がコードと食い違っている、など）。
⚠️ **アンカーは網羅ではない。** 表に無い引用は誰も見ていない。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 走査するファイルと、そこに書かれうる表記のゆれ
TARGETS = [
    "README.md", "README.en.md", "docs/README.md", "CLAUDE.md",
    # ⚠️ 外の人が最初に読む。訂正件数とゲート件数の両方を名乗る
    "CONTRIBUTING.md",
    # ⚠️ skill も件数を名乗る。実際に C-001〜C-027 のまま取り残されていた
    ".claude/skills/recording-measurements/SKILL.md",
]

# 「M-1〜M-63」「M-1–M-63」「D-001〜D-040」「C-001–C-039」を拾う。
# ⚠️ **接頭辞ごとに桁数まで固定する。** `D-0*1` のように緩くすると
#    Phase の「D-1〜D-3」を決定番号と誤検出する（実際に踏んだ）。
RANGES = {
    "M": re.compile(r"\bM-1\s*[〜–~-]\s*(?:M-)?(\d+)"),
    "D": re.compile(r"\bD-001\s*[〜–~-]\s*(?:D-)?0*(\d+)"),
    "C": re.compile(r"\bC-001\s*[〜–~-]\s*(?:C-)?0*(\d+)"),
}
# 「**39 件**記録」「**39 entries** record」
COUNT_JA = re.compile(r"\*\*(\d+) 件\*\*記録|訂正は (\d+) 件")
COUNT_EN = re.compile(r"\*\*(\d+) entries\*\* record")

# ---------------------------------------------------------------- 引用アンカー
#
# 「この主張を書くなら、この番号と一緒でなければならない」の対応表。
# ⚠️ **番号は主張の 1 行前〜2 行後のどこかにあればよい**（表の行と本文の折り返しで
#    引用が隣の行に落ちるのは普通なので）。誤引用は「近くに正しい番号が 1 つも無い」
#    形で現れるので、この窓でも捕まる。
# ⚠️ **主張の文言が変わればアンカーは黙って効かなくなる**（陰性対照が無い）ので、
#    「1 つも一致しなかったアンカー」を警告として出す。
WINDOW_BEFORE, WINDOW_AFTER = 1, 2
ANCHORS = [
    (re.compile(r"ホストと違う音素[はが] 0\.32%"),          "M-77"),
    (re.compile(r"0\.32% of phonemes differ"),             "M-77"),
    (re.compile(r"漢字文から合成まで完走"),                  "M-76"),
    (re.compile(r"synthesizes from kanji end to end"),      "M-76"),
    (re.compile(r"「音の測定」は済んだ"),                    "M-78"),
    (re.compile(r"DRAM が 19,304 B"),                       "M-79"),
]

# アンカーを探すファイル。⚠️ **TARGETS より広い** — 読者が最初に見るのは
#    README と実機手順書で、そこが一番古くなる。
ANCHOR_TARGETS = TARGETS + [
    "MODEL_CARD.md", "esp32/README.md", "esp32/TESTING.md",
    "docs/plan/k1-kanji-implementation-plan.md",
    "docs/plan/phase0-1-implementation-plan.md",
    "docs/research/k1-kanji-katakana-ondevice.md",
]


# hook のケース数を名乗るファイル（実測で拾った 10 箇所ぶん）
HOOK_TARGETS = [
    "README.md", "README.en.md", "CONTRIBUTING.md", "CLAUDE.md",
    "docs/README.md", "docs/decisions.md", "docs/upstream-sanotts.md",
]


def check_anchors(paths: list[str]) -> tuple[list[str], list[str]]:
    """(ずれている箇所, 一度も一致しなかったアンカー)"""
    bad: list[str] = []
    hits = [0] * len(ANCHORS)
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"{rel}: 存在しない")
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            for k, (rx, want) in enumerate(ANCHORS):
                if not rx.search(line):
                    continue
                hits[k] += 1
                lo = max(0, i - 1 - WINDOW_BEFORE)
                near = "\n".join(lines[lo:i + WINDOW_AFTER])
                if want not in near:
                    bad.append(f"{rel}:{i}  「{rx.pattern}」の引用の近くに "
                               f"{want} が無い")
    dead = [ANCHORS[k][0].pattern for k, n in enumerate(hits) if n == 0]
    return bad, dead


# ------------------------------------------------------- 空虚だったゲートの件数
# README が「空虚に通っていたゲートが N 件」と名乗る。⚠️ **これも書いた瞬間から
# 古くなる**（実際に 6 のまま 10 件になっていた）。表の行を数えて突き合わせる。
GATE_SKILL = ".claude/skills/writing-gates/SKILL.md"
GATE_SECTION = "## 実際に空虚だったゲート"
# ⚠️ **太字を必須にしたら 1 件も一致せず、検査が空虚に通った**（README は
#    `**6 件**` ではなく `6 件` と書いてあった）。`\*{0,2}` で両方拾い、
#    **一致 0 件を NG として報告する**。
GATE_CLAIM_JA = re.compile(r"欠陥が潜んでいた例が\s*\*{0,2}(\d+) 件")
GATE_CLAIM_EN = re.compile(r"\*{0,2}(\w+)\*{0,2} defects hid behind green tests")
EN_NUM = {"Six": 6, "Seven": 7, "Eight": 8, "Nine": 9, "Ten": 10,
          "Eleven": 11, "Twelve": 12}


def vacuous_gate_rows() -> int:
    """`writing-gates` の「実際に空虚だったゲート」表の行数。"""
    text = (ROOT / GATE_SKILL).read_text(encoding="utf-8")
    if GATE_SECTION not in text:
        raise SystemExit(f"{GATE_SKILL} に「{GATE_SECTION}」が無い")
    body = text.split(GATE_SECTION, 1)[1]
    body = re.split(r"^#{2,3} ", body, maxsplit=1, flags=re.M)[0]
    rows = [ln for ln in body.splitlines()
            if ln.startswith("| ") and not re.match(r"^\|[\s|:-]+\|?$", ln)]
    # 1 行目はヘッダ（| ゲート | 何を測っていたか | …）
    return max(0, len(rows) - 1)


def check_gate_count(paths: list[str], want: int) -> list[str]:
    bad: list[str] = []
    seen = 0
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for m in GATE_CLAIM_JA.finditer(text):
            seen += 1
            if int(m.group(1)) != want:
                bad.append(f"{rel}: 空虚なゲート {m.group(1)} 件 → 実体は {want} 件")
        for m in GATE_CLAIM_EN.finditer(text):
            seen += 1
            got = EN_NUM.get(m.group(1))
            if got is None:
                bad.append(f"{rel}: 「{m.group(1)} defects」を数に読めない")
            elif got != want:
                bad.append(f"{rel}: {m.group(1)} defects → 実体は {want} 件")
    # ⚠️ **1 件も見つからないのは「一致した」ではなく「検査が効いていない」。**
    if seen == 0:
        bad.append("件数の主張がどのファイルにも見つからない = **この検査は空虚**")
    return bad


# ---------------------------------------------------------- hook の回帰ケース数
# 「回帰 83 ケース」が 10 箇所に残っていた（実体 94）。**テストの CASES を
# import して数える** ので、ケースを足したら勝手に検出される。
HOOK_CLAIM = re.compile(r"(?:回帰\s*)?(\d+)\s*(?:ケース|cases|regression cases)")
HOOK_CONTEXT = ("guard_bash", "hook の回帰", "hook regression", "回帰 ",
                "regression cases")   # ⚠️ en は guard_bash が前の行にある


def hook_case_count() -> int:
    import importlib.util
    t = ROOT / ".claude" / "hooks" / "test_guard_bash.py"
    spec = importlib.util.spec_from_file_location("_tgb", t)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return len(mod.CASES)


def check_hook_count(paths: list[str], want: int) -> list[str]:
    """⚠️ **行に hook の話だと分かる語がある行だけ**見る。
    「83 ケース」は他の文脈でも出るので、無条件に拾うと誤検知する。"""
    bad: list[str] = []
    seen = 0
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not any(k in line for k in HOOK_CONTEXT):
                continue
            for m in HOOK_CLAIM.finditer(line):
                got = int(m.group(1))
                if got in (6,):        # commit ガードの 6 件は別の数
                    continue
                seen += 1
                if got != want:
                    bad.append(f"{rel}:{i}  hook の回帰 {got} ケース → 実体は {want}")
    if seen == 0:
        bad.append("hook のケース数の主張が見つからない = **この検査は空虚**")
    return bad


def actual() -> dict[str, tuple[int, int]]:
    """{prefix: (最大番号, 件数)}"""
    out = {}
    for prefix, path in (("M", "docs/measurements.md"),
                         ("D", "docs/decisions.md"),
                         ("C", "docs/decisions.md")):
        text = (ROOT / path).read_text(encoding="utf-8")
        nums = sorted({int(m) for m in
                       re.findall(rf"^## {prefix}-0*(\d+)", text, re.M)})
        if not nums:
            raise SystemExit(f"{path} に {prefix}-NNN の見出しが 1 つも無い")
        out[prefix] = (nums[-1], len(nums))
    return out


def check(paths: list[str], truth: dict[str, tuple[int, int]]) -> list[str]:
    bad: list[str] = []
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"{rel}: 存在しない")
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for prefix, rx in RANGES.items():
                for got in rx.findall(line):
                    want = truth[prefix][0]
                    if int(got) != want:
                        bad.append(f"{rel}:{i}  {prefix}-…〜{prefix}-{got} → 実体は {want}")
            for m in COUNT_JA.finditer(line):
                got = m.group(1) or m.group(2)
                if int(got) != truth["C"][1]:
                    bad.append(f"{rel}:{i}  訂正 {got} 件 → 実体は {truth['C'][1]} 件")
            for m in COUNT_EN.finditer(line):
                if int(m.group(1)) != truth["C"][1]:
                    bad.append(f"{rel}:{i}  {m.group(1)} entries → 実体は {truth['C'][1]}")
    return bad


def main() -> int:
    truth = actual()
    print(f"実体: M-{truth['M'][0]}（{truth['M'][1]} 件）/ "
          f"D-{truth['D'][0]:03d}（{truth['D'][1]} 件）/ "
          f"C-{truth['C'][0]:03d}（{truth['C'][1]} 件）")

    # ⚠️ **陽性対照。** 検出器が壊れていれば「0 件」は無意味なので、
    #    わざと 1 つずらしたテキストを先に食わせて、必ず捕まることを示す。
    probe = ROOT / "docs" / ".counter_probe.md"
    probe.write_text(
        f"M-1〜M-{truth['M'][0] + 1} / D-001〜D-{truth['D'][0] + 1:03d} / "
        f"C-001〜C-{truth['C'][0] + 1:03d} と**{truth['C'][1] + 1} 件**記録 / "
        f"訂正は {truth['C'][1] + 1} 件\n",
        encoding="utf-8")
    try:
        control = check(["docs/.counter_probe.md"], truth)
    finally:
        probe.unlink()
    if len(control) < 5:
        print(f"NG! 陽性対照が {len(control)} 件しか捕まらない（5 件ずらしてある）"
              f" = **このゲートは空虚**")
        for c in control:
            print("     " + c)
        return 1
    print(f"陽性対照: わざとずらした 5 箇所すべてを検出")

    bad = check(TARGETS, truth)
    if bad:
        print(f"\nNG! 番号が実体とずれている（{len(bad)} 箇所）:")
        for b in bad:
            print("  " + b)
        return 1
    print(f"OK  {len(TARGETS)} ファイルの番号がすべて実体と一致")

    # --- 引用アンカー（陽性対照つき）-------------------------------------
    probe.write_text(
        "ホストと違う音素は 0.32% だ（M-1）\n"
        "0.32% of phonemes differ from the host (M-1)\n"
        "漢字文から合成まで完走した（M-1）\n"
        "QEMU synthesizes from kanji end to end (M-1)\n"
        "「音の測定」は済んだ（M-1）\n"
        "DRAM が 19,304 B 溢れた（M-1）\n",
        encoding="utf-8")
    try:
        ctrl, _ = check_anchors(["docs/.counter_probe.md"])
    finally:
        probe.unlink()
    if len(ctrl) != len(ANCHORS):
        print(f"NG! 陽性対照が {len(ctrl)}/{len(ANCHORS)} しか捕まらない"
              f" = **アンカー検査は空虚**")
        for c in ctrl:
            print("     " + c)
        return 1
    print(f"陽性対照: 番号をわざと間違えた {len(ANCHORS)} 件すべてを検出")

    abad, dead = check_anchors(ANCHOR_TARGETS)
    for d in dead:
        print(f"⚠️ アンカー「{d}」はどのファイルにも一致しない = **効いていない**")
    if abad:
        print(f"\nNG! 引用の番号が間違っている（{len(abad)} 箇所）:")
        for b in abad:
            print("  " + b)
        return 1
    print(f"OK  {len(ANCHOR_TARGETS)} ファイルの引用アンカー {len(ANCHORS)} 件が一致")

    # --- 空虚だったゲートの件数（陽性対照つき）---------------------------
    nrows = vacuous_gate_rows()
    probe.write_text(
        f"欠陥が潜んでいた例が {nrows + 1} 件あり\n\n"
        f"Six defects hid behind green tests\n", encoding="utf-8")
    try:
        gctrl = check_gate_count(["docs/.counter_probe.md"], nrows)
    finally:
        probe.unlink()
    if len(gctrl) != 2:
        print(f"NG! 陽性対照が {len(gctrl)}/2 しか捕まらない = **件数検査は空虚**")
        return 1
    print(f"陽性対照: 件数をわざと間違えた 2 件を検出")
    gbad = check_gate_count(["README.md", "README.en.md", "CONTRIBUTING.md"], nrows)
    if gbad:
        print(f"\nNG! 空虚だったゲートの件数がずれている（実体 {nrows} 件）:")
        for b in gbad:
            print("  " + b)
        return 1
    print(f"OK  空虚だったゲートの件数 {nrows} 件が README と一致")

    # --- hook の回帰ケース数（陽性対照つき）------------------------------
    ncases = hook_case_count()
    probe.write_text(f"hook の回帰 {ncases + 1} ケース\n", encoding="utf-8")
    try:
        hctrl = check_hook_count(["docs/.counter_probe.md"], ncases)
    finally:
        probe.unlink()
    if len(hctrl) != 1:
        print(f"NG! 陽性対照が {len(hctrl)}/1 = **ケース数検査は空虚**")
        return 1
    print("陽性対照: ケース数をわざと 1 ずらしたものを検出")
    hbad = check_hook_count(HOOK_TARGETS, ncases)
    if hbad:
        print(f"\nNG! hook の回帰ケース数がずれている（実体 {ncases}）:")
        for b in hbad:
            print("  " + b)
        return 1
    print(f"OK  hook の回帰ケース数 {ncases} が全ドキュメントと一致")
    print("⚠️ 見ていないもの: 数値の中身が古いこと（サイズ・実測値など）と、"
          "アンカー表に無い引用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
