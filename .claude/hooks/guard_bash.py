#!/usr/bin/env python3
"""PreToolUse(Bash) ガード。

このプロジェクトで実際に起きた事故を防ぐ。どれも**例外を出さずに壊れる**種類のもの:

1. **piper-plus への書き込み** — 教師の供給元は読み取り専用の前提だが、
   実際にサブエージェントが `build/share/open_jtalk/dic/dicrc` を作ってしまった。
   `permissions.deny` は Edit/Write ツールしか止められないので、シェル経由をここで塞ぐ。
2. **`pip install`** — `uv.lock` と実環境が黙って乖離する（D-012）。
3. **uv を経由しない python** — `.venv/lib/.../site-packages/piper_train/` に
   v1.13.0 相当の stale なコピーが実在し、間違った方を掴んでも例外が出ない（M-1.1）。
4. **本番ラベルパックの破棄・再生成** — ラベルは一度だけ生成して SHA-256 で固定する
   決まり（D-015）。消してしまうと、同じデバイス・同じ piper-plus commit を
   再現しない限り**同じラベルは二度と作れない**。
5. **本番ラベルパックへの再生成** — 既存ディレクトリに追記されると
   manifest と SHA-256 が実体と食い違う（D-015）。

stdin に hook の JSON を受け取り、判定を JSON で返す。
"""

from __future__ import annotations

import json
import re
import os
import sys

#: piper-plus の checkout。**環境変数で差し替えられる**（他人の環境でも動くように）。
#: 既定は `~/Documents/piper-plus`。clone した人は `PIPER_PLUS_ROOT` を設定する。
PIPER_PLUS = os.environ.get("PIPER_PLUS_ROOT",
                            os.path.expanduser("~/Documents/piper-plus"))


def _path_variants(path: str) -> list[str]:
    """同じ場所を指す表記のゆれを全部返す。

    ⚠️ **絶対パスだけを見ていると `~` 表記が素通りする。**
    `rm -rf ~/Documents/piper-plus` は展開すれば同じ場所なのに、
    文字列としては一致しない。**実際にこの穴が開いていた**（テストが
    絶対パスしか渡していなかったので気づかなかった）。
    """
    out = [path]
    home = os.path.expanduser("~")
    if path.startswith(home + "/"):
        rel = path[len(home) + 1:]
        out += [f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"]
    return out


#: 照合に使う表記のゆれ（絶対パス / `~` / `$HOME`）
PIPER_PLUS_FORMS = _path_variants(PIPER_PLUS)

# コマンド位置 = 行頭 / シェル区切りの直後 / sudo・env 等のラッパの直後。
# ここに限定しないと `grep "pip install"` のような引用符内の文字列を誤検知する。
#
# バッククォートによるコマンド置換は**意図的に含めない**。レガシー記法である一方、
# このプロジェクトは日本語ドキュメントを heredoc で書くことが多く、
# Markdown のコードスパン `pip install` を誤検知してしまう（実際に踏んだ）。
# 現代的な $(...) だけを見る。
# `\n` も区切りに含める。シェルでは改行がコマンド区切りなので、
# 2 行目以降の先頭を見落とすと heredoc の後ろに書いた危険なコマンドを素通しする。
CMD_POS = r"(?:^|[;&|\n]\s*|\$\(\s*|(?:sudo|env|time|nohup|xargs)\s+)"

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
    """piper-plus の作業ツリーを変える操作を止める。

    ⚠️ **表記のゆれを全部見る**（絶対パス / `~/...` / `$HOME/...`）。
    """
    for form in PIPER_PLUS_FORMS:
        if form in cmd:
            _check_write_for(cmd, form)


def _check_write_for(cmd: str, PIPER_PLUS: str) -> None:

    # リダイレクト:  > path  /  >> path  （piper-plus 配下を指すもの）
    if re.search(r">>?\s*[\"']?" + re.escape(PIPER_PLUS), cmd):
        deny(
            f"piper-plus への書き込みリダイレクトを検出しました。\n"
            f"{PIPER_PLUS} は**読み取り専用の依存**です（docs/decisions.md D-003）。\n"
            f"成果物は sanoTTS-jp 側に書いてください。"
        )

    # 破壊的コマンド + piper-plus パス。
    # **コマンド位置**（行頭 or シェル区切りの直後）でのみ照合する。
    # 任意の空白の後ろで照合すると `grep "rm /path/..."` のような
    # 引用符の中の文字列まで拾ってしまう（実際に誤検知した）。
    for word in WRITE_COMMANDS:
        # ⚠️ 引数の走査に `\n` を含めない。**改行はシェルのコマンド区切り**なので、
        # 含めると「1 行目の chmod」と「2 行目の引数」を同じコマンドとして繋いでしまう
        # （C-015 で CMD_POS に `\n` を足したとき、こちら側を直し忘れて再発した）。
        hits = [m.group(0) for m in
                re.finditer(rf"{CMD_POS}{re.escape(word)}\s+[^;&|\n]*{re.escape(PIPER_PLUS)}", cmd)]
        # `sed -n 'a,bp' file` は読み取り。書き込むのは `-i` / `--in-place` のときだけ。
        # ここを一律 deny にすると piper-plus のソースが読めなくなる（実際に踏んだ）。
        # ⚠️ **`-i` は「その sed の呼び出しの中」だけを見る。** コマンド文字列全体を
        # 走査すると、同じブロックの**別の行の `perl -i`** に反応して読み取りまで
        # 止まる（C-015 / C-020 と同じ根で 3 度目の再発。C-025）。
        if word == "sed":
            hits = [h for h in hits
                    if re.search(r"(?:^|\s)(?:-[a-zA-Z]*i|--in-place)\b", h)]
        if hits:
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
    # ⚠️ **コマンド位置に固定すること。** 素の部分文字列一致にすると、
    # heredoc で docs を書くときにこのパスを含む文字列で誤検知する（C-011 の再発）。
    if any(re.search(CMD_POS + re.escape(f"{form}/.venv/bin/python"), cmd)
           for form in PIPER_PLUS_FORMS):
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


#: 公式実装（GPL-3.0）。本リポジトリは MIT なので**ソースコードを取り込めない**（C-024）
UPSTREAM_REPO = "Ampixa/sanoTTS"

#: 上流から取っても汚染しない拡張子。**事実（数値・ハイパーパラメータ）は著作権の対象外**
UPSTREAM_DOC_EXT = (".md", ".txt", ".rst", ".json", ".yml", ".yaml")

#: 取り込むと GPL が伝播する拡張子
UPSTREAM_SRC_EXT = (".c", ".h", ".cpp", ".hpp", ".cc", ".py", ".s", ".S",
                    ".ino", ".js", ".mjs", ".cmake", ".mk")


def check_upstream_gpl(cmd: str) -> None:
    """公式実装 (GPL-3.0) のソースコードを引き込むのを止める。

    **ドキュメント (.md 等) の取得は通す。** 数値・ハイパーパラメータ・
    アーキテクチャ構成は事実なので著作権の対象外で、実際に
    `docs/upstream-sanotts.md` はそうやって作った。止めるのは**ソースコード**だけ。

    ⚠️ **これは「読むな」ではなく「MIT のまま配布できなくなるぞ」という警告。**
    方針を変えて GPL-3.0 に再ライセンスするなら、この検査ごと外すこと。
    """
    if UPSTREAM_REPO.lower() not in cmd.lower():
        return

    # clone / submodule はツリー全体が入るので拡張子を問わず止める
    if re.search(r"\bgit\s+(clone|submodule\s+add)\b", cmd) or \
       re.search(r"\bgh\s+repo\s+clone\b", cmd):
        deny(
            f"{UPSTREAM_REPO} を clone しようとしています。\n"
            f"公式実装は **GPL-3.0**、本リポジトリは **MIT** です。\n"
            f"ソースツリーを持ち込むと GPL が伝播し、MIT のまま配布できなくなります。\n"
            f"**数値・ハイパーパラメータ・構成は公開ドキュメントから取れます**"
            f"（`docs/upstream-sanotts.md` はそうやって作った）。"
        )

    # ソース拡張子を名指しで取りに行っている（gh api contents / raw を curl 等）。
    # ⚠️ **拡張子は「上流を指すトークンの中」だけを見る。** コマンド全体を走査すると
    # `grep -rn "Ampixa/sanoTTS" --include="*.py" .`（自リポを検索するだけ）が
    # 止まる（実際に踏んだ。C-011/C-015/C-020/C-025 と同じ病理 = 走査範囲を
    # 切り出していない）。上流の**パスそのもの**に拡張子が付いている場合だけ deny する。
    upstream_tokens = [t for t in re.split(r"[\s'\"]+", cmd)
                       if UPSTREAM_REPO.lower() in t.lower()]
    for tok in upstream_tokens:
        ext_hit = next((e for e in UPSTREAM_SRC_EXT
                        if re.search(re.escape(e) + r"(?![a-zA-Z0-9])", tok)), None)
        if ext_hit:
            ext = ext_hit
            deny(
                f"{UPSTREAM_REPO} の `*{ext}`（ソースコード）を取得しようとしています。\n"
                f"公式実装は **GPL-3.0**、本リポジトリは **MIT** です。\n"
                f"**読んで書き直すのもグレー**です（特にアセンブリは表現の幅が狭い）。\n"
                f"公開ドキュメント（{'/'.join(UPSTREAM_DOC_EXT)}）の取得は通ります — "
                f"数値と構成は事実であって著作権の対象外です（C-024 / docs/upstream-sanotts.md）。"
            )


#: 上流の配布パッケージ。**入れると GPL のコードが .venv に入る**
UPSTREAM_PACKAGES = ("sanotts", "sanotts-web")


def check_upstream_package(cmd: str) -> None:
    """`uv add sanotts` のような形で GPL パッケージを依存に入れるのを止める。"""
    m = re.search(CMD_POS + r"uv\s+add\s+([^;&|\n]*)", cmd)
    if not m:
        return
    args = m.group(1)
    for pkg in UPSTREAM_PACKAGES:
        if re.search(r"(?:^|\s)" + re.escape(pkg) + r"(?:[=<>~\[]|\s|$)", args):
            deny(
                f"`{pkg}` は公式実装（**GPL-3.0**）の配布パッケージです。\n"
                f"依存に入れると .venv に GPL のコードが入り、"
                f"MIT の本リポジトリと両立しません。\n"
                f"比較したいだけなら**別環境**で動かし、"
                f"得た数値だけを `docs/upstream-sanotts.md` に記録してください。"
            )


#: 本番ラベルパック。**一度だけ生成して SHA-256 で固定する**（D-015）
PRODUCTION_PACKS = ("data/pack", "data/pack_heldout")

#: 破棄系のコマンド。`data/pack_sibdense` のような検証用パックは対象外
DESTRUCTIVE = ("rm", "mv", "truncate", "shred")


def check_production_pack(cmd: str) -> None:
    """本番ラベルパックの破棄を止める。

    ⚠️ **検証用パック（`data/pack_sibdense` など）は対象にしない。**
    あれは作り直しが前提。守るのは本番の 2 つだけ。
    """
    for pack in PRODUCTION_PACKS:
        # `data/pack_sibdense` に誤爆しないよう、直後が単語構成文字でないことを要求する
        target = re.escape(pack) + r"(?![\w-])"
        for word in DESTRUCTIVE:
            if re.search(rf"{CMD_POS}{re.escape(word)}\s+[^;&|\n]*{target}", cmd):
                deny(
                    f"`{word}` が本番ラベルパック `{pack}` を対象にしています。\n"
                    "**ラベルは一度だけ生成して SHA-256 で固定する**決まりです（D-015）。\n"
                    "消すと、同じデバイス・同じ piper-plus commit を再現しない限り\n"
                    "**同じラベルは二度と作れません**（CPU と GPU で bit 一致しない）。\n"
                    "作り直す必要が本当にあるなら、理由を manifest に残してから手で消してください。"
                )


def check_label_regeneration(cmd: str) -> None:
    """既にある本番パックへの再生成を止める（D-015: ラベルは一度だけ）。

    ⚠️ **ローカル生成そのものは止めない。** D-027 で手元 (M4 Max) 実行に切り替えた。
    止めるのは「既にあるパックの上書き」だけ。`gen_teacher_labels.py` 自身は
    既存ディレクトリに追記してしまうので、ここで塞ぐ。
    """
    if "gen_teacher_labels.py" not in cmd:
        return
    m = re.search(r"--out\s+(\S+)", cmd)
    if not m:
        return
    out = m.group(1).strip("\"'").rstrip("/")
    if out not in PRODUCTION_PACKS:
        return
    import os
    if not os.path.isdir(out):
        return          # まだ無いなら本番生成そのもの。通す
    deny(
        f"`{out}` は既に存在します。**ラベルは一度だけ生成する**決まりです（D-015）。\n"
        "同じディレクトリに再生成すると既存 shard に追記され、manifest と\n"
        "SHA-256 が実体と食い違ったパックができます。\n\n"
        "作り直す必要が本当にあるなら、理由を記録してから手で消してください。"
    )


_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredoc_bodies(cmd: str) -> str:
    """heredoc の本文を取り除く。**本文はデータであってコマンドではない。**

    このプロジェクトは日本語ドキュメントを heredoc で書くことが多く、
    本文に `pip install` や piper-plus のパスが**話題として**登場する。
    生のコマンド文字列をそのまま走査すると、それを実行だと誤認する
    （C-011 で 2 回、その後さらに 1 回踏んだ）。

    区切り語の行だけを残し、本文は捨てる。ネストは扱わない（実用上不要）。
    """
    lines = cmd.split("\n")
    out: list[str] = []
    pending: list[str] = []   # まだ閉じていない区切り語
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue          # 本文は捨てる
        out.append(line)
        for m in _HEREDOC_START.finditer(line):
            pending.append(m.group(2))
    return "\n".join(out)


#: コーパス本文が公開物に混入するのを **commit の直前**で止める（C-028）。
#: ⚠️ 一度これで漏らして force-push する羽目になった。
#: 検出ロジック自体は `scripts/sanitize_reports.py` にあり、
#: `scripts/test_sanitize_reports.py` が陽性/陰性対照つきで守っている。
CORPUS_SPLITS = ("train", "heldout", "embedded", "sibdense")

#: 照合の最小文字数。これ未満は「本文の再配布」に当たらない
#: （単独モーラ `し` を誤検知した実例がある。C-028）
CORPUS_MIN_LEN = 8


def _load_corpus_texts(root: str) -> set[str]:
    """コーパス本文を読む。**読めなければ空集合**（検査せず素通しする）。"""
    import csv
    out: set[str] = set()
    for sp in CORPUS_SPLITS:
        p = os.path.join(root, "data", "splits", f"corpus_{sp}.tsv")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for r in csv.reader(f, delimiter="\t"):
                    if r and r[0] != "source" and len(r) >= 3:
                        t = r[2].strip()
                        if len(t) >= CORPUS_MIN_LEN:
                            out.add(t)
        except OSError:
            continue
    return out


def find_corpus_text_in_staged(root: str = ".") -> list[tuple[str, int]]:
    """staged な JSON にコーパス本文が入っていれば [(path, 件数)] を返す。

    ⚠️ **検査できないときは必ず空を返す**（fail open）。
    コーパスが手元に無い環境（新規 clone）でコミットが全部止まると、
    ガードとして機能する以前に作業が成立しない。
    """
    import subprocess

    def _git(*a: str) -> str:
        try:
            r = subprocess.run(("git", "-C", root) + a, capture_output=True,
                               text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return r.stdout if r.returncode == 0 else ""

    staged = [p for p in _git("diff", "--cached", "--name-only").split("\n")
              if p.endswith(".json")]
    if not staged:
        return []
    texts = _load_corpus_texts(root)
    if not texts:                      # コーパスが読めない → 検査しない
        return []

    hits: list[tuple[str, int]] = []
    for path in staged:
        blob = _git("show", f":{path}")
        if not blob:
            continue
        try:
            doc = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        n = 0

        def walk(o: object) -> None:
            nonlocal n
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            elif isinstance(o, str) and o.strip() in texts:
                n += 1

        walk(doc)
        if n:
            hits.append((path, n))
    return hits


def check_corpus_text_commit(cmd: str) -> None:
    """`git commit` の直前に、staged なコーパス本文を止める（C-028）。"""
    if not re.search(CMD_POS + r"git\s+(?:-C\s+\S+\s+)?commit\b", cmd):
        return
    try:
        hits = find_corpus_text_in_staged(".")
    except Exception:                  # noqa: BLE001 — ガードで作業を止めない
        return                          # 検査に失敗したら素通し（fail open）
    if not hits:
        return
    detail = "\n".join(f"  {p}  {n} 箇所" for p, n in hits[:6])
    deny(
        f"staged なファイルに**コーパス本文**が入っています（C-028）。\n"
        f"{detail}\n\n"
        f"本リポジトリは MIT で公開済みですが、**コーパス本文は再配布できません**"
        f"（CV は license.verified: false / JSUT は subset 別 CC-BY-SA）。\n"
        f"一度これを見逃して push し、**履歴を書き換える羽目になりました**。\n\n"
        f"次で落としてから commit してください（uid と統計は残ります）:\n"
        f"  uv run python scripts/sanitize_reports.py --apply"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 解釈できない入力でツールを止めない

    raw = (payload.get("tool_input") or {}).get("command") or ""
    if not raw:
        return 0
    cmd = strip_heredoc_bodies(raw)

    check_piper_plus_write(cmd)
    check_pip(cmd)
    check_python_invocation(cmd)
    check_production_pack(cmd)
    check_label_regeneration(cmd)
    check_upstream_gpl(cmd)
    check_upstream_package(cmd)
    check_corpus_text_commit(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
