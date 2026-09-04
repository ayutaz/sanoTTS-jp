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
    # --- 古い ckpt で成果物を上書きするのを止める（M-102 で実際に踏んだ）---
    ("deny", "uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt",
     "M-41 の再現コマンド。既定で csrc/student.bin を v2 に戻す"),
    ("deny", "uv run python scripts/export_c_weights.py --ckpt runs/v1/stage4.pt --int8",
     "別の古い ckpt でも同じ"),
    ("deny", "uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt --out /tmp/x",
     "⚠️ --out だけそらしても --report が csrc/export.json のまま"),
    ("allow", "uv run python scripts/export_c_weights.py --ckpt runs/v3/stage4.pt",
     "**いまの ckpt（D-037）なら通す**"),
    ("allow", "uv run python scripts/export_c_weights.py --ckpt runs/v2/stage4.pt "
              "--out /tmp/repro --report /tmp/repro/export.json",
     "**出力先を全部そらせば再現できる**（測定を妨げない）"),
    ("allow", "uv run python scripts/export_c_weights.py",
     "--ckpt が無ければ既定 = 現行なので通す"),
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
    # C-025: `-i` を**コマンド文字列全体**から探していたため、同じブロックの
    # 別の行の `perl -i` に反応して piper-plus の読み取りまで deny していた。
    # C-015 / C-020 と同じ根（走査範囲を 1 コマンドに閉じていない）で 3 度目の再発。
    ("allow",
     "perl -i -pe 's/a/b/' docs/x.md\nsed -n '133p' ~/Documents/piper-plus/src/python/x.py",
     "別の行の perl -i に引きずられて sed の読み取りを止めない"),
    ("allow",
     "sed -i '' 's/a/b/' README.md; sed -n '9p' ~/Documents/piper-plus/README.md",
     "同じ行の別の sed -i に引きずられない"),
    ("deny",
     "cat README.md\nsed -i '' 's/a/b/' ~/Documents/piper-plus/src/python/x.py",
     "本物の sed -i は改行を跨いでも止める"),
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
    # --- 公式実装 (GPL-3.0) の境界（C-024。本リポジトリは MIT） ---
    # ⚠️ **ドキュメントは通す。** 数値・構成は事実で著作権の対象外であり、
    #    docs/upstream-sanotts.md は実際にこの経路で作った。
    ("allow", "gh api repos/Ampixa/sanoTTS/contents/README.md --jq '.content'",
     "上流の README は通す（事実の参照）"),
    ("allow", "gh api repos/Ampixa/sanoTTS/contents/docs/distillation-recipe.md",
     "上流の docs も通す"),
    ("allow", "gh api repos/Ampixa/sanoTTS --jq '.license.spdx_id'",
     "メタデータの照会は通す"),
    ("allow", "gh api 'repos/Ampixa/sanoTTS/git/trees/master?recursive=1' --jq '.tree[].path'",
     "ファイル名一覧は通す（中身ではない）"),
    ("deny", "gh api repos/Ampixa/sanoTTS/contents/mcu/src/snt_tts.c --jq '.content'",
     "上流の .c は GPL が伝播するので止める"),
    ("deny", "curl -s https://raw.githubusercontent.com/Ampixa/sanoTTS/master/pypkg/sanotts/models.py",
     "上流の .py も止める"),
    ("deny", "gh api repos/Ampixa/sanoTTS/contents/mcu/ports/esp32s3/sn_matvec_s8_esp32s3.S",
     "PIE アセンブリは特に危ない（表現の幅が狭い）"),
    ("deny", "git clone https://github.com/Ampixa/sanoTTS.git /tmp/up",
     "clone はツリー全体が入る"),
    ("deny", "gh repo clone Ampixa/sanoTTS", "gh repo clone も同じ"),
    ("deny", "uv add sanotts", "GPL パッケージを依存に入れない"),
    ("allow", "uv add speechmos", "DNSMOS (E-1) の依存追加は通す"),
    ("allow", "uv add torch numpy", "無関係な依存追加は通す"),
    # ⚠️ **自リポの検索を止めないこと。** 拡張子をコマンド全体から探すと
    #    「上流の名前を grep するだけ」が deny になる（作った直後に踏んだ）。
    #    C-011/C-015/C-020/C-025 と同じ病理 = 走査範囲をトークンに閉じていない。
    ("allow", 'grep -rn "Ampixa/sanoTTS" --include="*.py" .',
     "自リポを grep するだけ（--include の *.py に反応しない）"),
    ("allow", 'rg "Ampixa/sanoTTS" csrc/saanotts.c',
     "自リポの .c を検索するだけ"),
    ("allow", 'grep -n "Ampixa/sanoTTS" docs/upstream-sanotts.md',
     "自リポの docs を検索するだけ"),
    # --- commit 前のコーパス本文検査（C-028）---
    # ⚠️ 陽性対照（本文を stage して deny）は staged 状態に依存するので
    #    `test_commit_guard()` で別に張る。ここは**誤検知しないこと**だけを固定する。
    ("allow", "git commit -m 'docs: 更新'", "普通の commit は通す"),
    ("allow", "git commit --amend --no-edit", "amend も通す"),
    ("allow", "git add -A && git commit -m wip", "add してから commit も通す"),
    ("allow", "echo 'git commit -m x' >> notes.md", "文字列の中の git commit に反応しない"),
    ("allow", "grep -rn 'git commit' docs/", "grep の引数に反応しない"),
    # --- 凍結物の保護（D-041 / D-042）---
    ("deny", "rm scripts/k1/dict_manifest.json", "凍結した辞書の同一性を消させない"),
    ("deny", "rm -f csrc/g2p_table.json", "凍結した mora テーブルを消させない"),
    ("deny", "mv scripts/k1/dict_manifest.json /tmp/", "mv でも止める"),
    ("deny", "echo '{}' > scripts/k1/dict_manifest.json", "リダイレクト上書きも止める"),
    ("deny", "cat x.json >> csrc/g2p_table.json", "追記も止める"),
    # ⚠️ **誤検知しないこと**が同じくらい大事（C-011 / C-020 / C-025 で 5 回踏んだ）
    ("allow", "cat scripts/k1/dict_manifest.json", "読むのは通す"),
    ("allow", "git diff csrc/g2p_table.json", "差分を見るのは通す"),
    ("allow", "uv run python scripts/k1/k0_freeze_dict.py --force", "正規の作り直しは通す"),
    ("allow", "rm -rf .k1work", "中間生成物は消してよい"),
    ("allow", "rm scripts/k1/dict_manifest.json.bak", "別名のファイルには反応しない"),
    ("allow", "grep -n sha256 scripts/k1/dict_manifest.json", "grep も通す"),
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


def test_commit_guard() -> int:
    """commit 前のコーパス本文検査（C-028）を、状態を作って確かめる。

    ⚠️ **陽性対照が要る。** 「0 件でした」は「安全」ではなく
    「検出できなかった」かもしれない — その取り違えで本文を公開してしまった。
    """
    import csv
    import subprocess
    import tempfile

    sys.path.insert(0, str(GUARD.parent))
    import guard_bash as G   # noqa: PLC0415

    bad = 0

    def ck(name: str, got, want) -> None:
        nonlocal bad
        ok = got == want
        bad += not ok
        print(f"  {'OK ' if ok else 'NG!'} {name:<52} {got!r}")

    # 陰性: コーパスが読めない環境では**必ず素通し**（fail open）。
    # ここが deny になると、新規 clone で一切コミットできなくなる。
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
        ck("コーパス不在なら検査しない", G.find_corpus_text_in_staged(d), [])
        ck("コーパス不在なら 0 件読む", len(G._load_corpus_texts(d)), 0)
    ck("git が無いパスでも例外を出さない", G.find_corpus_text_in_staged("/tmp"), [])

    # 陽性: 本文を staged にすると必ず捕まる
    texts = G._load_corpus_texts(".")
    if not texts:
        print("  --  コーパスが手元に無いので陽性対照は省略（CI では起こりうる）")
        return bad
    sample = next(t for t in texts if len(t) >= 12)
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
        splits = pathlib.Path(d) / "data" / "splits"
        splits.mkdir(parents=True)
        with (splits / "corpus_heldout.tsv").open("w", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["source", "id", "text"])
            w.writerow(["cv/x", "u1", sample])
        poison = pathlib.Path(d) / "p.json"
        poison.write_text(json.dumps({"utts": [{"uid": "u1", "text": sample}]},
                                     ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", d, "add", "p.json"], check=True)
        ck("staged な本文を検出する", G.find_corpus_text_in_staged(d), [("p.json", 1)])

        # 伏せれば通る（uid は残る）
        poison.write_text(json.dumps({"utts": [{"uid": "u1", "text": "<redacted: corpus text>"}]},
                                     ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", d, "add", "p.json"], check=True)
        ck("伏せた後は通す", G.find_corpus_text_in_staged(d), [])

        # ⚠️ リスト直下の素の文字列も見る（C-028 の 3 つ目の穴）
        poison.write_text(json.dumps({"xs": [sample]}, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "-C", d, "add", "p.json"], check=True)
        ck("リスト直下の素の文字列も検出する", G.find_corpus_text_in_staged(d), [("p.json", 1)])
    return bad


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

    print("\ncommit 前のコーパス本文検査（C-028）")
    if test_commit_guard():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
