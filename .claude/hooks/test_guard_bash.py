#!/usr/bin/env python3
"""guard_bash.py の回帰テスト。

**hook を変更したら必ず走らせること。** 誤検知があると全 Bash が止まる。

テストケースをこのファイルに置いているのは、シェルのワンライナーで書くと
テストデータ自体が guard に引っかかるため（実際に 2 回踏んだ）。

実行:
    uv run python .claude/hooks/test_guard_bash.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

GUARD = pathlib.Path(__file__).with_name("guard_bash.py")
PP = "~/Documents/piper-plus"

CASES: list[tuple[str, str, str]] = [
    # (期待, コマンド, 何を確かめているか)
    # --- piper-plus の破壊を止める ---
    ("deny", f"rm {PP}/build/share/open_jtalk/dic/dicrc", "実際に起きた事故"),
    ("deny", f"echo x " + "> " + f"{PP}/foo.txt", "リダイレクト"),
    ("deny", f"touch {PP}/a", "ファイル作成"),
    ("deny", f"mv {PP}/a {PP}/b", "移動"),
    ("deny", f"sed -i '' s/a/b/ {PP}/x.py", "in-place 編集"),
    ("deny", f"cd {PP} && git checkout v1.13.0", "作業ツリーを動かす git"),
    ("deny", f"git -C {PP} reset --hard", "同上"),
    # --- uv 以外の依存導入を止める ---
    ("deny", "pip install torch", "基本形"),
    ("deny", "pip3 install -r req.txt", "pip3"),
    ("deny", "sudo pip install x", "sudo 経由"),
    # --- uv を経由しない python を止める（ask ではなく deny。D-012 / C-013）---
    ("deny", f"{PP}/.venv/bin/python scripts/x.py", "stale piper_train のリスク"),
    ("deny", "python3 scripts/kana_g2p.py", "uv 抜きの scripts 実行"),
    ("deny", 'python3 -c "print(1)"', "使い捨てワンライナーも uv 経由にする"),
    ("deny", "python3 .claude/hooks/test_guard_bash.py", "hook のテストも同様"),
    # --- 素通しすべき（誤検知チェック）---
    ("allow", 'grep -n "pip install" docs/plan/x.md', "引用符内の pip install"),
    ("allow", f'grep -n "rm {PP}/x" docs/a.md', "引用符内の rm + piper-plus"),
    ("allow", f'rg "touch {PP}" docs/', "rg でも同様"),
    ("allow", "uv run python scripts/kana_g2p.py", "正しい実行"),
    ("allow", "uv add pyworld", "正しい依存追加"),
    ("allow", "uv pip install -e .", "uv 内部の pip は許す"),
    ("allow", "uv sync", ""),
    ("allow", f"cat {PP}/VERSION", "読み取り"),
    ("allow", f"grep -rn infer {PP}/src/python/", "読み取り"),
    ("allow", f"ls -la {PP}/build/share/open_jtalk/dic/", "読み取り"),
    ("allow", f"find {PP} -name '*.py'", "読み取り"),
    ("allow", f"git -C {PP} status --short", "非破壊 git"),
    ("allow", f"git -C {PP} log --oneline -5", "非破壊 git"),
    ("allow", "git add -A && git commit -m x", "自プロジェクトの git は自由"),
    ("allow", "rm -rf reports/tmp", "自プロジェクトの削除は自由"),
    ("allow", 'uv run python -c "print(1)"', "uv 経由なら OK"),
    ("allow", "uv run python .claude/hooks/test_guard_bash.py", "uv 経由なら OK"),
    # heredoc で日本語ドキュメントを書くときに踏んだ誤検知
    ("allow", "uv run python - <<'EOF'\ns='`pip install` も禁止'\nEOF", "コードスパン内の pip install"),
    ("allow", "uv run python - <<'EOF'\ns='`rm -rf` は危険'\nEOF", "コードスパン内の rm"),
    ("allow", f"uv run python - <<'EOF'\ns='{PP}/.venv/bin/python は使わない'\nEOF",
     "heredoc 内の piper-plus venv パス（C-011 の再発を防ぐ）"),
    ("allow", f'grep -rn "{PP}/.venv/bin/python" docs/', "grep で venv パスを探す"),
    # heredoc の本文はデータ。コマンドとして走査してはいけない（C-011 / C-015）
    ("allow", "git commit -F - <<'EOF'\npip install は禁止\npython3 x.py も禁止\nEOF",
     "コミットメッセージ内の危険な文字列"),
    ("allow", f"uv run python - <<'PY'\ns='rm {PP}/dic'\nPY", "heredoc 内の rm + piper-plus"),
    ("allow", "cat <<'EOF' > note.md\n| a | python3 b.py |\nEOF", "heredoc 内の表"),
    # heredoc の**外**は今までどおり見る
    ("deny", "cat <<'EOF' > a.txt\nhello\nEOF\npip install x", "heredoc の後ろの pip install"),
    # --- 改行を跨いだ誤検知（2026-08-27 に実際に踏んだ。C-015 と同じ根の再発） ---
    ("allow", "chmod +x deploy/run.sh\nuv run python x.py --root ~/Documents/piper-plus",
     "chmod は別行。piper-plus は次行の引数"),
    ("allow", "mkdir -p deploy\nuv run python -c \"import x\"  # ~/Documents/piper-plus",
     "mkdir は別行"),
    ("deny", "chmod -R 777 ~/Documents/piper-plus/src", "同じ行なら止める"),
    ("deny", "mkdir -p ~/Documents/piper-plus/newdir", "同じ行なら止める"),
    # --- sed の読み書き区別 ---
    ("allow", "sed -n '1,80p' ~/Documents/piper-plus/src/python/piper_train/vits/models.py",
     "sed -n は読み取り"),
    ("deny", "sed -i '' 's/a/b/' ~/Documents/piper-plus/src/python/x.py", "sed -i は書き込み"),
    ("deny", "sed --in-place 's/a/b/' ~/Documents/piper-plus/x.py", "sed --in-place は書き込み"),
    ("allow", "sed -n '1,5p' scripts/x.py", "自リポの sed -n"),
    # --- 本番ラベルパックの保護（D-015） ---
    ("deny", "rm -rf data/pack", "本番パックの削除"),
    ("deny", "rm -rf data/pack_heldout", "本番パック(heldout)の削除"),
    ("deny", "mv data/pack /tmp/old", "本番パックの移動"),
    ("allow", "rm -rf data/pack_sibdense", "検証用パックは作り直し前提なので通す"),
    ("allow", "ls data/pack", "読み取りは通す"),
    ("allow", "du -sh data/pack", "サイズ確認は通す"),
    ("allow", "cat data/pack/manifest.json", "manifest の確認は通す"),
    # --- 本番ラベルの再生成（D-015）。ローカル生成そのものは D-027 で許可 ---
    # ⚠️ **このケースは実ファイルシステムを見る**（既存パックへの追記だけを止める）。
    # 期待値は data/pack があるかどうかで変わるので、下の main() で動的に決める。
    ("__depends_on_pack__",
     "uv run python scripts/gen_teacher_labels.py --split train --out data/pack",
     "既存なら deny / 無ければ allow"),
    ("allow", "uv run python scripts/gen_teacher_labels.py --split train --limit 20 --out /tmp/p",
     "疎通確認は通す"),
    ("allow", "uv run python scripts/gen_teacher_labels.py --split sibdense --out data/pack_sibdense",
     "評価セットは通す"),
    # --- パス表記のゆれ（2026-08-27。**絶対パスしか見ていない穴があった**） ---
    ("deny", "rm -rf $HOME/Documents/piper-plus/build", "$HOME 表記も止める"),
    ("deny", "touch ${HOME}/Documents/piper-plus/x", "${HOME} 表記も止める"),
    ("allow", "ls ~/Documents/piper-plus/src", "読み取りは ~ でも通す"),
]


def decide(command: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
    )
    out = proc.stdout.strip()
    if not out:
        return "allow"
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


def main() -> int:
    import os

    failures = 0
    for expected, command, note in CASES:
        if expected == "__depends_on_pack__":
            # 既存パックへの再生成だけを止める。期待値は実際の有無で決まる
            exists = os.path.isdir("data/pack")
            expected = "deny" if exists else "allow"
            note = note + ("（今: 既存）" if exists else "（今: 無し）")
        got = decide(command)
        ok = got == expected
        failures += not ok
        mark = "OK " if ok else "NG!"
        print(f"  {mark} [{got:5}] 期待={expected:5} {command[:48]:<50}{note}")

    print()
    if failures:
        print(f"{failures}/{len(CASES)} 失敗")
        return 1
    print(f"{len(CASES)}/{len(CASES)} 期待通り")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
