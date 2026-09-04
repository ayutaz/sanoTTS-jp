#!/usr/bin/env python3
"""ゲートが CI で回っているか、回らないなら**理由が書いてあるか**を検査する。

## なぜ要るのか — **実際にずれていた**

`csrc/Makefile` の `all-test` は 12 個のゲートを持つが、CI（`.github/workflows/ci.yml`）が
回しているのは **10 個**。残る 2 個（`stream` / `int8-e2e`）が入らないのは
**コーパス由来の `ids_heldout.bin` が git にもリリースにも無い**ため。
`all-test` の外には、さらに**辞書 / pyopenjtalk / ESP-IDF が要る**ので回せないゲートがある。
それ自体は妥当だが、**その線引きが散文でしか書かれていなかった**ので、

  - 新しいゲートを `all-test` に足しても、CI に入れ忘れたことに誰も気づかない
  - 逆に CI から外したゲートが「回っているつもり」で放置される

という壊れ方をする。実際 `range`（S9 のカーネル検査）は CI に入っていたのに
`.github/workflows/README.md` の一覧から漏れていた。

この検査は **「CI で回る」か「回らない理由が下の表にある」かのどちらかであること**を要求する。
理由の表が**実体とずれたら落ちる**（消えたゲートの言い訳が残っていても落ちる）。

## 使い方

    uv run --no-project python scripts/check_ci_coverage.py
    uv run --no-project python scripts/check_ci_coverage.py --self-test   # 陽性対照

⚠️ **これは「テストが十分か」を測るものではない。** 「実装したゲートが動く場所を持っているか」だけ。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "csrc" / "Makefile"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# --- CI で回せないゲートと、その理由 -------------------------------------------------
#
# ⚠️ **「面倒だから」は理由にならない。** 何が無いから回せないのかを書く。
#    その「無いもの」が CI で手に入るようになったら、ここから外して CI に入れる。
EXCLUDED_TARGETS: dict[str, str] = {
    # ⚠️ **訂正（C-059）。** ここには
    #    「重み blob が要る。しかも **リリースの int8 資産は blob v1 のまま**なので
    #     S4 以降のコアが SAAN_ERR_VERSION で拒む」という理由で
    #    `int8` と `int8-golden` が**除外されていた**。**これは v0.2.0 までの話で、今は誤り。**
    #    v0.3.0 の `saanotts-jp-v3-int8.bin` は **blob v2**（654,032 B。SHA-256 の頭は 2d2b8543）で、
    #    手元の v2 blob と bit 一致する。→ **2 つとも CI（`golden` job）に入れたのでここから外した。**
    #    ⚠️ **言い訳を消すだけでは不十分**だったことも記録しておく: この表から外すだけなら
    #    このゲートは「CI で回っていない」と言って落ちる。**実際に ci.yml で回して初めて外せる。**
    #
    # 以下は今も CI で回せないもの。重み blob（csrc/student.bin ほか）は git 管理外だが
    # **リリースから落とせば手に入る**ので、もう「回せない理由」にはならない。
    # ⚠️ 残る本当の理由は **ids_heldout.bin がコーパス由来で git にもリリースにも無い**こと。
    "int8-e2e": "重み blob + ids_heldout.bin（コーパス由来。リリースにも無い）",
    "int8-e2e-a8": "同上（W8A8 レーン）",
    "stream": "重み blob + ids_heldout.bin。⚠️ **held-out 24 文 × 3 レーンの bit 一致**という最強のゲートがここに居る",
    "lanes": "重み blob + SCOREQ（評価の依存）",
    # ⚠️ これも理由が弱くなった: 重み blob はリリースから落とせる（`golden` job が実際に落としている）。
    #    手元では `make -C csrc prof` が 1.5 s で通り、見ているのは**時間ではなく回数**なので
    #    CI に足せるはず。**まだ足していない**ので、ここには「未着手」と書いておく。
    "prof": "重み blob（student_i8.bin）。⚠️ リリースから落とせるので `golden` job に足せる（未着手）",
    "g2p-corpus": "コーパス本文（data/splits/*.tsv は git 管理外）",
    "kb-parity": "pyopenjtalk と piper-plus（ホスト側 G2P との突き合わせ）",
    # 辞書 13.7 MB（k1_dict.bin）と pyopenjtalk が要る
    "jdict": "辞書 blob（13.7 MB）と pyopenjtalk",
    # M-104: 8 MB 板向けの matrixa。⚠️ **同じ逆量子化値を 2 形式で持った blob を 2 本**
    #    作るので、辞書と pyopenjtalk に加えて **生成に数分**かかる。
    "matrixa": "辞書 blob と pyopenjtalk（ベクタを 2 本生成する。M-104）",
    "accent": "辞書 blob と pyopenjtalk",
    "njd-rules": "辞書 blob と pyopenjtalk",
    "oj-heap": "辞書 blob と pyopenjtalk",
    "kanji-e2e": "辞書 blob と pyopenjtalk",
    "label-ids": "辞書 blob と pyopenjtalk",
    # 手元専用
    "run-bench": "ベンチ（ゲートではない）",
    "clean": "掃除",
    "golden": "`test` の別名（CI は `make -C csrc test` を回している）",
    # ⚠️ `all-test` は上のゲートの束。**12 個のうち 10 個は CI で回っている**
    #    （line / fft / pad / g2p / erf / range は `csrc` job、
    #      test / int8 / int8-golden / arena は `golden` job）。
    #    残る 2 個は `stream` と `int8-e2e` で、どちらも `ids_heldout.bin` が要る。
    #    束ごと回すとその 2 個で落ちるので、CI は個別に呼んでいる。
    "all-test": "束（12 個中 10 個は CI が個別に回している。残り 2 個は ids_heldout.bin が要る）",
}

EXCLUDED_SCRIPTS: dict[str, str] = {
    "scripts/check_dict_blob.py": "辞書 blob 13.7 MB（git 管理外）。⚠️ **構造の検査だけなら CI に載っている** — `make -C csrc jdict-hard` が合成 blob で jdict_open の入力検査を陽性対照つきで叩く（M-100）",
    "scripts/check_esp32_template.sh": "ESP-IDF の xtensa toolchain（約 2 GB）と重み blob",
    "scripts/check_partitions.py": "重み blob と辞書 blob（大きさを突き合わせる）",
    "scripts/test_discriminator.py": "ラベルパック data/pack_sibdense（git 管理外）",
    "scripts/test_k1_dict.py": "pyopenjtalk（3 件が辞書の実体を要る）",
    "scripts/kana_g2p.py": "pyopenjtalk と piper-plus（凍結テーブルとの突き合わせ。表だけの検査は `make -C csrc g2p` が CI で回している）",
    "scripts/k1/k0_verify_dict.py": "凍結した sys.dic（103 MB。git 管理外）",
    "scripts/k1/k4b_vendor.py": "上流の sdist（pyopenjtalk-plus の tar.gz）",
    "scripts/k1/kb_route_parity.py": "pyopenjtalk と piper-plus",
    "scripts/test_text_route_parity.py":
        "OpenJTalk（漢字→かな）と教師 ckpt（private。**外した側**を再現して突き合わせるため）",
}

# ゲートとして数えるスクリプト
SCRIPT_GLOBS = ("scripts/test_*.py", "scripts/check_*.py", "scripts/check_*.sh",
                ".claude/hooks/test_*.py", "scripts/k1/k0_verify_dict.py",
                "scripts/k1/k4b_vendor.py", "scripts/k1/kb_route_parity.py",
                "scripts/kana_g2p.py")

FAILED: list[str] = []


def ng(msg: str) -> None:
    FAILED.append(msg)
    print(f"  NG  {msg}")


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def make_targets(makefile: str) -> set[str]:
    """`.PHONY` に挙がっている名前 = 人が打つゲート。ファイル生成規則は数えない。"""
    names: set[str] = set()
    # ⚠️ 継続行（行末の `\`）を先にほどく。ほどかないと 2 行目のゲートを見落とし、
    #    「CI で回っていない」と誤検出する（実際に踏んだ）。
    flat = makefile.replace("\\\n", " ")
    for m in re.finditer(r"^\.PHONY:(.*)$", flat, re.M):
        names.update(m.group(1).split())
    return names


# YAML のコメント（行頭 `#`）と、`run:` ブロックの中のシェルコメント。
# ⚠️ **`#` の直前が行頭か空白のときだけ**コメントとみなす（`v0.3.0#frag` のような
#    文字列を巻き込まないため）。YAML もシェルもこの規則で切る。
COMMENT = re.compile(r"(?m)(?:^|(?<=\s))#.*$")


def strip_comments(ci_text: str) -> str:
    """⚠️ **コメントは「CI で回っている」の証拠にならない。**

    これを入れる前は、`ci.yml` の**コメントにゲート名を書いただけ**で
    「CI で回っている」と判定していた。理由の表（EXCLUDED_*）から外すときに
    「ci.yml のコメントで言及した」だけで通ってしまう = **空虚なゲート**になる。
    実行行だけを見るために、走らない行を先に落とす。
    """
    return COMMENT.sub("", ci_text)


def ci_runs(ci_text: str) -> set[str]:
    return set(re.findall(r"make -C csrc ([a-z0-9-]+)", strip_comments(ci_text)))


def ci_scripts(ci_text: str) -> set[str]:
    """CI が**実際に実行している**スクリプト（basename で見る）。"""
    return set(re.findall(r"[\w./-]*?([\w-]+\.(?:py|sh))", strip_comments(ci_text)))


def orphan_tests() -> list[str]:
    """`*_test.c` が Makefile からもシェルスクリプトからも参照されていないもの。"""
    refs = MAKEFILE.read_text()
    for sh in (ROOT / "csrc").glob("*.sh"):
        refs += sh.read_text()
    out = []
    for c in sorted((ROOT / "csrc").glob("*_test.c")):
        if c.stem not in refs and c.name not in refs:
            out.append(str(c.relative_to(ROOT)))
    return out


def run(fake_target: str | None = None, comment_out: str | None = None) -> int:
    mk = MAKEFILE.read_text()
    ci = CI.read_text()
    if fake_target:                      # 陽性対照 1: all-test に架空のゲートを足したことにする
        mk = mk.replace("\nall-test: ", f"\n.PHONY: {fake_target}\nall-test: {fake_target} ", 1)
    if comment_out:                      # 陽性対照 2: 実行行をコメントに変えたことにする
        ci = "\n".join("#" + ln if comment_out in ln else ln
                       for ln in ci.splitlines())

    targets = make_targets(mk)
    runs = ci_runs(ci)
    print(f"csrc の .PHONY ゲート {len(targets)} 個 / CI が回すもの {len(runs)} 個\n")

    print("== ゲート（csrc/Makefile）==")
    for t in sorted(targets):
        if t in runs:
            ok(f"{t}（CI）")
        elif t in EXCLUDED_TARGETS:
            print(f"  --  {t}: {EXCLUDED_TARGETS[t]}")
        else:
            ng(f"`make -C csrc {t}` は CI で回らず、EXCLUDED_TARGETS に理由も無い")

    print("\n== スクリプト ==")
    names = {p.name: str(p.relative_to(ROOT))
             for g in SCRIPT_GLOBS for p in sorted(ROOT.glob(g))}
    in_ci = ci_scripts(ci)
    for name, rel in sorted(names.items(), key=lambda kv: kv[1]):
        if name in in_ci:
            ok(f"{rel}（CI）")
        elif rel in EXCLUDED_SCRIPTS:
            print(f"  --  {rel}: {EXCLUDED_SCRIPTS[rel]}")
        else:
            ng(f"{rel} は CI で回らず、EXCLUDED_SCRIPTS に理由も無い")

    print("\n== 言い訳の棚卸し（消えたゲートの理由が残っていないか）==")
    stale = [t for t in EXCLUDED_TARGETS if t not in targets]
    stale += [s for s in EXCLUDED_SCRIPTS if not (ROOT / s).exists()]
    if stale:
        for s in stale:
            ng(f"EXCLUDED に居るが実体が無い: {s}")
    else:
        ok(f"EXCLUDED の {len(EXCLUDED_TARGETS) + len(EXCLUDED_SCRIPTS)} 件すべて実体がある")

    print("\n== 誰も呼ばないテスト ==")
    orphans = orphan_tests()
    if orphans:
        for o in orphans:
            ng(f"どこからも参照されていない: {o}")
    else:
        ok("csrc/*_test.c はすべて Makefile かシェルスクリプトから呼ばれている")

    print()
    if FAILED:
        print(f"NG! {len(FAILED)} 件")
        return 1
    print("OK  すべてのゲートは「CI で回る」か「回らない理由が書いてある」")
    return 0


# 陽性対照。**2 つとも落ちなければならない。**
# ⚠️ 1 つ目（架空のゲート）だけでは「コメントに名前を書けば通る」形を捕まえられない。
#    2 つ目は **CI の実行行をコメントに変えても通ってしまわないか**を見る。
SELF_TESTS = [
    ("all-test に架空のゲート `zzz-fake` を足す",
     {"fake_target": "zzz-fake"}),
    ("CI の実行行をコメントに変える（コメントは「回っている」ではない）",
     {"comment_out": "check_doc_counters.py"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="陽性対照 2 件: わざと壊して、この検査が落ちることを見る")
    a = ap.parse_args()
    if a.self_test:
        bad = 0
        for label, kw in SELF_TESTS:
            print(f"== 陽性対照: {label} ==")
            FAILED.clear()
            if run(**kw) == 0:                      # type: ignore[arg-type]
                print(f"\nNG! 陽性対照が通ってしまった（{label}）= この検査は空虚\n")
                bad += 1
            else:
                print(f"\nOK  陽性対照は落ちた（{label}）\n")
        if bad:
            print(f"NG! 陽性対照 {bad}/{len(SELF_TESTS)} 件が通ってしまった")
            return 1
        print(f"OK  陽性対照 {len(SELF_TESTS)} 件すべて落ちた（検査は効いている）")
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
