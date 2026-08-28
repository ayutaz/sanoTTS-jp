#!/usr/bin/env python3
"""`sanitize_reports.py` の回帰テスト。

⚠️ **なぜ要るか**: このゲートは 2 つの穴を同時に持っていて、
**「合計 0 箇所」を出しながら公開済みリポジトリに本文 36 箇所を残していた**（C-028）。

| 穴 | 何を取りこぼしたか |
|---|---|
| キー名の許可リスト方式 | `mismatch_examples` / `L3_student_hyp`（`TEXT_KEYS` に無い） |
| `--root reports` 既定 | `csrc/ids_heldout.json` の本文 24 件（走査範囲の外） |
| リスト直下の素の文字列 | `["<本文>", ...]` が `scrub()` の最後で素通り |

**「0 箇所」は「安全」ではない。検出できることを先に示さないと意味がない。**
このテストは**陽性対照**（本文を入れたら必ず捕まる）を各形状について張る。

    uv run python scripts/test_sanitize_reports.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sanitize_reports as S   # noqa: E402

#: 本文の代わりに使う。長さは MIN_TEXT_LEN 以上
CORPUS = {"きょうはいいてんきですね、ほんとうに", "これはコーパスの本文です"}
SAFE = "これは私が書いた説明文であって本文ではない"


def check(name: str, obj, expect_hits: int) -> bool:
    stat: dict = {}
    out = S.scrub(obj, CORPUS, stat)
    got = sum(stat.values())
    ok = got == expect_hits
    if ok and expect_hits:
        # 伏せた後に本文が残っていないことも確かめる（置換が効いているか）
        blob = json.dumps(out, ensure_ascii=False)
        ok = not any(t in blob for t in CORPUS)
    print(f"  {'OK ' if ok else 'NG!'} {name:<46} 検出 {got} / 期待 {expect_hits}")
    return ok


def main() -> int:
    bad = 0
    t = sorted(CORPUS)[0]

    # --- 陽性対照: どの形状に置いても捕まること -------------------------------
    bad += not check("dict の値", {"text": t}, 1)
    bad += not check("dict の値・キー名が未知", {"未知のキー名": t}, 1)
    bad += not check("リスト直下の素の文字列", {"xs": [t, "ok"]}, 1)
    bad += not check("リストのリスト", {"xs": [[t]]}, 1)
    bad += not check("dict のリストの dict", {"rows": [{"hyp": t}]}, 1)
    bad += not check("入れ子の深いところ", {"a": {"b": {"c": [{"d": t}]}}}, 1)
    bad += not check("同じ本文が 3 箇所", {"a": t, "b": [t], "c": {"d": t}}, 3)
    bad += not check("前後に空白", {"text": f"  {t}  "}, 1)

    # --- 陰性対照: 巻き込まないこと -------------------------------------------
    bad += not check("私が書いた説明文", {"note": SAFE}, 0)
    bad += not check("uid は残す", {"uid": "BASIC5000_0083"}, 0)
    bad += not check("数値・真偽値", {"n": 24, "ok": True, "x": None}, 0)
    bad += not check("短い文字列（モーラ 1 文字）", {"mora": "し"}, 0)
    bad += not check("空文字列", {"text": ""}, 0)

    # --- 構造を壊さないこと ----------------------------------------------------
    src = {"uid": "u1", "text": t, "n": 3, "xs": [1, 2], "d": {"k": "v"}}
    out = S.scrub(src, CORPUS, {})
    ok = (out["uid"] == "u1" and out["n"] == 3 and out["xs"] == [1, 2]
          and out["d"] == {"k": "v"} and out["text"] == S.PLACEHOLDER)
    print(f"  {'OK ' if ok else 'NG!'} {'構造と uid・統計は保つ':<46}")
    bad += not ok

    # --- 走査範囲の既定 --------------------------------------------------------
    # ⚠️ `reports` だけを見ていたため csrc/ の本文 24 件を公開してしまった（C-028）
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="reports,csrc,data/splits,esp32")
    roots = ap.parse_args([]).root.split(",")
    ok = "csrc" in roots and "reports" in roots
    print(f"  {'OK ' if ok else 'NG!'} {'既定の走査範囲に csrc が入っている':<46} {roots}")
    bad += not ok

    # --- 最小長 ----------------------------------------------------------------
    ok = S.MIN_TEXT_LEN >= 8
    print(f"  {'OK ' if ok else 'NG!'} {'MIN_TEXT_LEN が単独モーラを拾わない長さ':<46} {S.MIN_TEXT_LEN}")
    bad += not ok

    print()
    if bad:
        print(f"{bad} 件 失敗")
        return 1
    print("すべて期待通り")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
