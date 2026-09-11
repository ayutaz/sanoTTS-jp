#!/usr/bin/env python3
"""G-L1b / G-L2 と統計レポート（蒸留テキストのライセンス。D-054）。

    uv run --no-project --python 3.12 python scripts/check_corpus_license.py --self-test
    uv run --no-project --python 3.12 python scripts/check_corpus_license.py --pack data/pack_cc0
    uv run --no-project --python 3.12 python scripts/check_corpus_license.py --heldout
    uv run --no-project --python 3.12 python scripts/check_corpus_license.py --report

⚠️ **CI では回らない**（ラベルパックとコーパス本文はどちらも git 管理外）。
`scripts/check_ci_coverage.py` の `EXCLUDED_SCRIPTS` に理由を書いてある。
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unicodedata

sys.path.insert(0, "src")
from saanotts_jp.corpus_license import Verdict, classify  # noqa: E402

# 2026-09-08 実測。`shasum -a 256 data/splits/corpus_heldout.tsv`
HELDOUT_SHA256 = "72b78b8ccbd20e6e8f74694f4dd978451a3f4d1059c13389711339059c29ca7c"
HELDOUT_LINES = 2334  # ヘッダ 1 + 本文 2,333
HELDOUT_PATH = pathlib.Path("data/splits/corpus_heldout.tsv")


def check_pack(root: pathlib.Path) -> int:
    """G-L1b: パックの index.jsonl に許可外の source が無いか。

    ⚠️ **壊れた行は `NG!` として報告し、トレースバックで落ちない。**
    `json.loads(ln)["source"]` が例外を出すと、それまでは Python の
    トレースバックがそのまま出ていた（exit code は非 0 のままなので
    誤って通ることは無いが、どのファイルの何行目が壊れているかを
    運用者が読めなかった）。
    """
    idx = root / "index.jsonl"
    if not idx.exists():
        print(f"NG! {idx} が無い")
        return 1
    seen: collections.Counter = collections.Counter()
    bad: collections.Counter = collections.Counter()
    n_malformed = 0
    for lineno, ln in enumerate(idx.read_text(encoding="utf-8").splitlines(), start=1):
        if not ln.strip():
            continue
        try:
            source = json.loads(ln)["source"]
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"NG! {idx}:{lineno} が壊れている（{exc}）: {ln[:80]!r}")
            n_malformed += 1
            continue
        seen[source] += 1
        if classify(source)[0] is not Verdict.ALLOWED:
            bad[source] += 1
    if not seen and not n_malformed:
        print(f"NG! {idx} が空 = **この検査は何も見ていない**")
        return 1
    for src, n in sorted(seen.items()):
        mark = "NG!" if src in bad else "   "
        print(f"  {mark} {src:26s} {n:6,} 発話  ({classify(src)[1]})")
    if bad:
        print(f"\nNG! 許可外の source が {sum(bad.values()):,} 発話 "
              f"({len(bad)} 種): {sorted(bad)}")
    if n_malformed:
        print(f"\nNG! 壊れた行が {idx} に {n_malformed} 行ある（上に行番号を列挙した）")
    if bad or n_malformed:
        return 1
    print(f"\nOK  {sum(seen.values()):,} 発話すべて許可された source "
          f"（{len(seen)} 種）")
    print("⚠️ 見ていないもの: **音**と**品質**。行が正しいことは音が良いことではない")
    return 0


def check_heldout(path: pathlib.Path | None = None) -> int:
    """G-L2: held-out が 1 行も変わっていないか。

    `path` を渡すと held-out の代わりにそのファイルを検査する（`--self-test` の
    陽性対照専用。**本番の凍結ファイルには一切書き込まない**）。
    """
    p = path if path is not None else HELDOUT_PATH
    if not p.exists():
        print(f"NG! {p} が無い（worktree なら symlink を張る。計画 Task 1）")
        return 1
    raw = p.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    # ⚠️ bytes のまま splitlines() する（decode しない）。UTF-8 の継続バイトに
    #    0x0A は現れないので行数は文字列版と一致し、かつ**壊れたバイト列でも
    #    例外を出さずに NG を返せる**（自己テストの 1 バイト反転で実際に踏んだ）。
    lines = len(raw.splitlines())
    bad = 0
    if got != HELDOUT_SHA256:
        print(f"NG! held-out の SHA-256 が違う\n  期待 {HELDOUT_SHA256}\n  実際 {got}")
        bad += 1
    else:
        print(f"OK  held-out の SHA-256 が凍結値と一致 ({got[:16]}…)")
    if lines != HELDOUT_LINES:
        print(f"NG! held-out の行数が違う（期待 {HELDOUT_LINES} / 実際 {lines}）")
        bad += 1
    else:
        print(f"OK  held-out は {lines:,} 行（ヘッダ 1 + 本文 {lines - 1:,}）")
    if bad == 0:
        print("⚠️ **held-out は意図的に JSUT を残してある**（評価は重みに入らない。"
              "外すと v3 と品質比較ができなくなる。spec §4.3）")
    return bad


def _char_classes(text: str) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for ch in text:
        if "぀" <= ch <= "ゟ":
            c["ひらがな"] += 1
        elif "゠" <= ch <= "ヿ":
            c["カタカナ"] += 1
        elif "一" <= ch <= "鿿":
            c["漢字"] += 1
        elif ch.isascii() and ch.isalnum():
            c["英数"] += 1
        elif unicodedata.category(ch).startswith("P"):
            c["約物"] += 1
        else:
            c["その他"] += 1
    return c


def report() -> int:
    """統計レポート。⚠️ **ゲートではない**（しきい値が無いので落ちない）。"""
    rows_all, rows_kept = [], []
    for r in csv.reader(open("data/splits/corpus_train.tsv"), delimiter="\t"):
        if not (r and r[-1] and r[0] != "source"):
            continue
        rows_all.append(r)
        if classify(r[0])[0] is Verdict.ALLOWED:
            rows_kept.append(r)

    print(f"train: {len(rows_all):,} 行 → {len(rows_kept):,} 行 "
          f"（除外 {len(rows_all) - len(rows_kept):,}）\n")

    for label, rows in (("v3 (JSUT 込み)", rows_all), ("CC0/PD のみ", rows_kept)):
        n = len(rows)
        lens = sorted(len(r[-1]) for r in rows)
        cc: collections.Counter = collections.Counter()
        for r in rows:
            cc += _char_classes(r[-1])
        tot = sum(cc.values())
        print(f"=== {label} ===")
        print(f"  文長  中央 {lens[n // 2]} / 平均 {sum(lens) / n:.1f} / "
              f"最短 {lens[0]} / 最長 {lens[-1]}")
        print("  文字種 " + " / ".join(
            f"{k} {v / tot * 100:.1f}%" for k, v in cc.most_common()))
        print()

    print("⚠️ **これは代理指標である。**")
    print("   音素カバレッジは中間表現が要る（OpenJTalk 依存）ので、"
          "ラベル生成後に **パックの tokens.npz** で測ること（計画 Task 6）。")
    print("⚠️ 失う多様性軸は 525 行: 助数詞 23 / カタカナ語 115 / "
          "オノマトペ 270 / 法令文 117。**効いているかは誰も測っていない**")
    return 0


def self_test() -> int:
    """⚠️ **陽性対照 4 件。** 落ちるべきものが落ちるか。"""
    bad = 0

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)

        # 陽性対照 1/4: 許可外の source を 1 件混ぜた index.jsonl
        (root / "index.jsonl").write_text(
            json.dumps({"seq": 0, "uid": "a", "source": "cv/sentence_collector"},
                       ensure_ascii=False) + "\n"
            + json.dumps({"seq": 1, "uid": "b", "source": "jsut/basic5000"},
                         ensure_ascii=False) + "\n",
            encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_pack(root)
        if rc != 0 and "jsut/basic5000" in buf.getvalue():
            print("陽性対照 1/4: 許可外の source を 1 件混ぜた index.jsonl が"
                  "検出された（jsut/basic5000）")
        else:
            print("NG! 陽性対照 1/4 が効かない = **--pack は何も見ていない**")
            bad += 1

        # 陽性対照 2/4: 空の index.jsonl も落ちること（空虚に通らないことの確認）
        (root / "index.jsonl").write_text("", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = check_pack(root)
        if rc != 0:
            print("陽性対照 2/4: 空の index.jsonl が検出された（空虚に通るゲートではない）")
        else:
            print("NG! 陽性対照 2/4 が効かない = **空の index.jsonl が通った**")
            bad += 1

        # 陽性対照 3/4: 壊れた行（不正な JSON / source キー欠落）がトレースバック
        # ではなく `NG!` として、ファイル名と行番号つきで報告されるか。
        (root / "index.jsonl").write_text(
            json.dumps({"seq": 0, "uid": "a", "source": "cv/sentence_collector"},
                       ensure_ascii=False) + "\n"
            + "{not valid json\n"
            + json.dumps({"seq": 2, "uid": "c"}, ensure_ascii=False) + "\n",  # source 欠落
            encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check_pack(root)
        out = buf.getvalue()
        if (rc != 0 and f"NG! {root / 'index.jsonl'}:2" in out
                and f"NG! {root / 'index.jsonl'}:3" in out):
            print("陽性対照 3/4: 壊れた行 2 件（不正な JSON / source 欠落）が"
                  "行番号つきで検出された（トレースバックで落ちなかった）")
        else:
            print("NG! 陽性対照 3/4 が効かない = **壊れた行が行番号つきで報告されない**")
            bad += 1

    # 陽性対照 4/4: held-out を 1 バイト変えたコピーが検出されるか。
    # ⚠️ **本番の凍結ファイルには一切書き込まない**（読むだけ）。
    #    変異体は tempfile 上にのみ作る。
    if not HELDOUT_PATH.exists():
        print(f"NG! 陽性対照 4/4 を回せなかった（{HELDOUT_PATH} が無い）")
        bad += 1
    else:
        mutated = bytearray(HELDOUT_PATH.read_bytes())
        mutated[0] ^= 0xFF  # 先頭 1 バイトを反転 = 内容が変わったことを保証する
        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as tf:
            tf.write(bytes(mutated))
            tmp_path = pathlib.Path(tf.name)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = check_heldout(tmp_path)
            if rc != 0:
                print("陽性対照 4/4: SHA-256 を 1 バイト変えた held-out が検出された")
            else:
                print("NG! 陽性対照 4/4 が効かない = **held-out の改変を検出できない**")
                bad += 1
        finally:
            tmp_path.unlink(missing_ok=True)

    if bad == 0:
        print("OK  --self-test: 4 件の陽性対照がすべて落ちた")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", type=pathlib.Path, help="G-L1b: パックを検査")
    ap.add_argument("--heldout", action="store_true", help="G-L2: held-out の凍結")
    ap.add_argument("--report", action="store_true", help="統計（ゲートではない）")
    ap.add_argument("--self-test", action="store_true", help="陽性対照")
    args = ap.parse_args()
    if not any((args.pack, args.heldout, args.report, args.self_test)):
        ap.error("--pack / --heldout / --report / --self-test のどれかを指定する")
    bad = 0
    if args.self_test:
        bad += self_test()
    if args.pack:
        bad += check_pack(args.pack)
    if args.heldout:
        bad += check_heldout()
    if args.report:
        bad += report()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
