"""K-0 G0-2 / G0-3: 使う辞書が D-042 で凍結したものかを照合する。

    uv run python scripts/k1/k0_verify_dict.py

このマシンには sys.dic が 3 リビジョン同居している（C-045）。**取り違えると
測定が別物になる**（B-0 のアクセント天井は piper-plus 側で測られていて、
教師が引く辞書とは 3.77pt 違った）。K-1 のエンコーダは走る前にここを通すこと。

ゲート:
  G0-2 現在の辞書が manifest と一致する
  G0-3a 陰性対照（合成）: 1 バイト違えば検出できる
  G0-3b 陰性対照（実物）: **別リビジョンの実辞書**を渡すと落ちる
        ⚠️ 実辞書が 1 つしか無い環境では G0-3b は SKIP になる。
           SKIP を「通った」と読まないこと。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k0_freeze_dict import (FILES, MANIFEST, describe, resolve_dict_dir,  # noqa: E402
                            sha256)
from k1_paths import DICT_PP, PP  # noqa: E402


def check(dic_dir: str, man: dict) -> list[str]:
    """manifest と食い違う点を列挙する。空なら一致。"""
    bad = []
    try:
        cur = describe(dic_dir)
    except Exception as e:
        return [f"読めない: {e}"]
    for n in FILES:
        a = man.get("files", {}).get(n)
        b = cur.get("files", {}).get(n)
        if a is None and b is None:
            continue
        if a is None or b is None:
            bad.append(f"{n}: 片方に無い")
            continue
        if a["sha256"] != b["sha256"]:
            bad.append(f"{n}: sha256 {a['sha256'][:16]}… != {b['sha256'][:16]}…")
        elif a["bytes"] != b["bytes"]:
            bad.append(f"{n}: bytes {a['bytes']} != {b['bytes']}")
    if man.get("sys_dic_header", {}).get("lexsize") != \
            cur.get("sys_dic_header", {}).get("lexsize"):
        bad.append(f"lexsize {man['sys_dic_header']['lexsize']} != "
                   f"{cur['sys_dic_header']['lexsize']}")
    return bad


def main() -> int:
    if not os.path.exists(MANIFEST):
        print(f"NG: manifest が無い: {MANIFEST}")
        print("    uv run python scripts/k1/k0_freeze_dict.py で作る")
        return 1
    man = json.load(open(MANIFEST, encoding="utf-8"))
    print(f"manifest  : {os.path.relpath(MANIFEST, os.getcwd())} "
          f"(決定 {man.get('decision', '?')})")
    print(f"凍結時の環境: pyopenjtalk {man['environment']['pyopenjtalk_version']} / "
          f"python {man['environment']['python']}")
    print(f"凍結時 lexsize = {man['sys_dic_header']['lexsize']:,d} / "
          f"sha256 {man['files']['sys.dic']['sha256'][:24]}…\n")

    fails = []

    # --- G0-2 ---
    dic = resolve_dict_dir()
    print("=== G0-2: 現在の辞書が凍結したものと一致するか ===")
    print(f"  pyopenjtalk が引く辞書: {dic}")
    bad = check(dic, man)
    if bad:
        print("  **不一致**:")
        for b in bad:
            print("   ", b)
        print("\n  ⚠️ 辞書が入れ替わっている。K-1 の測定値をそのまま使わないこと。")
        fails.append("G0-2")
    else:
        print(f"  {len(man['files'])} ファイルすべて一致 → PASS")

    # --- G0-3a 合成の陰性対照（環境に依存せず必ず走る）---
    print("\n=== G0-3a: 陰性対照（1 バイト違いを検出できるか）===")
    p = os.path.join(dic, "sys.dic")
    with open(p, "rb") as f:
        head = bytearray(f.read(1 << 16))
    real = sha256(p)
    head[0x100] ^= 0x01
    h = hashlib.sha256()
    h.update(bytes(head))
    with open(p, "rb") as f:
        f.seek(1 << 16)
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    mutated = h.hexdigest()
    ok = mutated != real
    print(f"  正常 {real[:24]}…")
    print(f"  改変 {mutated[:24]}…")
    print(f"  差が出る: {ok} → {'PASS' if ok else 'FAIL'}")
    if not ok:
        fails.append("G0-3a")

    # --- G0-3b 実物の別リビジョン ---
    print("\n=== G0-3b: 陰性対照（別リビジョンの実辞書で落ちるか）===")
    others = []
    for label, d in [("piper-plus build", DICT_PP),
                     ("piper-plus venv",
                      os.path.join(PP, ".venv/lib/python3.13/site-packages/"
                                       "pyopenjtalk/dictionary"))]:
        if os.path.exists(os.path.join(d, "sys.dic")) and \
                os.path.realpath(d) != os.path.realpath(dic):
            others.append((label, d))
    if not others:
        print("  ⚠️ **SKIP** — 別リビジョンの実辞書がこの環境に無い。")
        print("     SKIP を「通った」と読まないこと。")
    else:
        for label, d in others:
            bad = check(d, man)
            print(f"  {label}: 食い違い {len(bad)} 件 → "
                  f"{'PASS（正しく落ちる）' if bad else '**FAIL（見逃した）**'}")
            if bad:
                print(f"     例: {bad[0]}")
            else:
                fails.append("G0-3b")

    print()
    if fails:
        print(f"NG! 落ちたゲート: {', '.join(fails)}")
        return 1
    print("OK  辞書は D-042 で凍結したものと一致する")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
