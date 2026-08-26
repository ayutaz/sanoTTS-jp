#!/usr/bin/env python3
"""PreToolUse(Bash) ガード。

このプロジェクトで実際に起きた事故を防ぐ。どれも**例外を出さずに壊れる**種類のもの:

1. **piper-plus への書き込み** — 教師の供給元は読み取り専用の前提だが、
   実際にサブエージェントが `build/share/open_jtalk/dic/dicrc` を作ってしまった。
   `permissions.deny` は Edit/Write ツールしか止められないので、シェル経由をここで塞ぐ。
2. **`pip install`** — `uv.lock` と実環境が黙って乖離する（D-012）。
3. **uv を経由しない python** — `.venv/lib/.../site-packages/piper_train/` に
   v1.13.0 相当の stale なコピーが実在し、間違った方を掴んでも例外が出ない（M-1.1）。

stdin に hook の JSON を受け取り、判定を JSON で返す。
"""

from __future__ import annotations

import json
import re
import sys

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"

# コマンド位置 = 行頭 / シェル区切りの直後 / sudo・env 等のラッパの直後。
# ここに限定しないと `grep "pip install"` のような引用符内の文字列を誤検知する。
#
# バッククォートによるコマンド置換は**意図的に含めない**。レガシー記法である一方、
# このプロジェクトは日本語ドキュメントを heredoc で書くことが多く、
# Markdown のコードスパン `pip install` を誤検知してしまう（実際に踏んだ）。
# 現代的な $(...) だけを見る。
CMD_POS = r"(?:^|[;&|]\s*|\$\(\s*|(?:sudo|env|time|nohup|xargs)\s+)"

# 書き込み・変更を伴うコマンド。読み取り (cat/grep/ls/find/git log …) は素通しする。
WRITE_COMMANDS = (
    "rm", "mv", "cp", "touch", "mkdir", "rmdir", "ln", "chmod", "chown",
    "tee", "truncate", "install", "sed", "dd", "unzip", "tar", "patch",
)

# piper-plus の作業ツリーを変えてしまう git サブコマンド
GIT_MUTATING = (
    "checkout", "switch", "commit", "add", "reset", "restore", "merge",
    "rebase", "stash", "clean", "apply", "cherry-pick", "revert", "pull",
)


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


# NOTE: 以前は `permissionDecision: "ask"` を返すヘルパも持っていたが全廃した。
# `ask` は `defaultMode: dontAsk` を上書きして毎回プロンプトを出すため、
# 作業のたびに止まる（C-013）。このプロジェクトのガードはどれも判断の余地が無いので
# すべて `deny` でよい。「止めないが知らせたい」ものが出てきたら
# `{"systemMessage": "..."}` を返すこと（プロンプトを出さずに通知できる）。


def check_piper_plus_write(cmd: str) -> None:
    """piper-plus の作業ツリーを変える操作を止める。"""
    if PIPER_PLUS not in cmd:
        return

    # リダイレクト:  > path  /  >> path  （piper-plus 配下を指すもの）
    if re.search(r">>?\s*[\"']?" + re.escape(PIPER_PLUS), cmd):
        deny(
            f"piper-plus への書き込みリダイレクトを検出しました。\n"
            f"{PIPER_PLUS} は**読み取り専用の依存**です（docs/decisions.md D-003）。\n"
            f"成果物は saanoTTS-jp 側に書いてください。"
        )

    # 破壊的コマンド + piper-plus パス。
    # **コマンド位置**（行頭 or シェル区切りの直後）でのみ照合する。
    # 任意の空白の後ろで照合すると `grep "rm /path/..."` のような
    # 引用符の中の文字列まで拾ってしまう（実際に誤検知した）。
    for word in WRITE_COMMANDS:
        if re.search(rf"{CMD_POS}{re.escape(word)}\s+[^;&|]*{re.escape(PIPER_PLUS)}", cmd):
            deny(
                f"`{word}` が piper-plus のパスを対象にしています。\n"
                f"{PIPER_PLUS} は**読み取り専用の依存**です（docs/decisions.md D-003）。\n"
                f"過去にサブエージェントが辞書ディレクトリに dicrc を作る事故がありました。"
            )

    # piper-plus の作業ツリーを動かす git
    for sub in GIT_MUTATING:
        if re.search(rf"git\s+(-C\s+[\"']?{re.escape(PIPER_PLUS)}\S*\s+)?{sub}\b", cmd) and (
            f"-C {PIPER_PLUS}" in cmd or f'-C "{PIPER_PLUS}' in cmd or f"cd {PIPER_PLUS}" in cmd
        ):
            deny(
                f"piper-plus で `git {sub}` を実行しようとしています。\n"
                f"教師 ckpt は v2.0 HEAD でそのまま動くので checkout は不要です（D-003）。"
            )


def check_pip(cmd: str) -> None:
    """pip install を止めて uv add に誘導する。"""
    if re.search(CMD_POS + r"(pip|pip3)\s+install\b", cmd):
        # uv 自身が内部で pip を使う形 (`uv pip install`) は許す
        if re.search(r"\buv\s+pip\s+install\b", cmd):
            return
        deny(
            "`pip install` は使いません。**`uv add <pkg>`** を使ってください。\n"
            "pyproject.toml / uv.lock に記録されないと環境が再現できません（D-012）。\n"
            "一時的な確認だけなら `uv run --with <pkg> python ...` が使えます。"
        )


def check_python_invocation(cmd: str) -> None:
    """uv を経由しない python を止める。

    **`ask` ではなく `deny` にしている。** `ask` だと `defaultMode: dontAsk` でも
    確認が挟まり作業のたびに止まる。「uv を経由しない python はそもそもあり得ない」
    のがプロジェクトの方針（D-012）なので、`pip install` と同じく機械的に塞ぐ。
    """
    if f"{PIPER_PLUS}/.venv/bin/python" in cmd:
        deny(
            "piper-plus の venv の python を直接使おうとしています。\n"
            "本プロジェクトは **`uv run python`** が正です（D-012）。\n"
            "piper-plus 側の venv には v1.13.0 相当の stale な piper_train が\n"
            "混入しており、sys.path.insert を忘れると黙ってそちらが読まれます（M-1.1）。"
        )

    # uv を経由せずに python を起動している。`-c` や heredoc のワンライナーも対象。
    # 使い捨てのつもりでも教師を読む処理が混ざれば stale piper_train を掴む。
    if re.search(CMD_POS + r"(python|python3)\s+(?![^;&|]*\buv\b)", cmd):
        deny(
            "`uv run` を経由しない python は使いません。**`uv run python`** を使ってください（D-012）。\n"
            "`pip install` を使わないのと同じ理由で、環境を uv.lock に固定しておく必要があります。"
        )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 解釈できない入力でツールを止めない

    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return 0

    check_piper_plus_write(cmd)
    check_pip(cmd)
    check_python_invocation(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
