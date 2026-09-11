#!/usr/bin/env python3
"""**帰属義務の成果物**が実体として在るかを検査する（G-A1 / G-A2）。

    uv run --no-project python scripts/check_attribution.py
    uv run --no-project python scripts/check_attribution.py --self-test   # 陽性対照 6 件

## なぜ要るのか — **2 回続けて同じ穴に落ちた**

`LICENSE-MODEL.md` §3.1 は再配布者への**義務**を書いている。ところが

- [C-080](../docs/decisions.md#c-080): §3.1 に 3 素材を足したとき、**同じブロックの写しを
  `web/index.html` に置き去りにした**。GitHub Pages に置くことも再配布なので、
  公開中のページが帰属義務のある 3 素材を欠いたまま配られていた。
- [C-081](../docs/decisions.md#c-081): §3.1 が「Apache-2.0 の全文を同梱せよ」と書いた
  その日、**`LICENSE-APACHE-2.0.txt` がリポジトリに 1 つも無かった**。

C-081 の末尾には「⚠️ **ゲートが無い**」と自分で書いてあった。**これがそのゲートである。**

## 何を見るか

**G-A1 — 写しが正典と一字一句一致するか。**
正典は `LICENSE-MODEL.md` §3.1 の **(A) ブロック**（最初のコードフェンス）。
写しは `NOTICE.md` と `web/index.html`。⚠️ **`NOTICE.md` はリリース資産
`NOTICE.txt` の中身そのもの**で、法的な成果物としては index.html より重い。
にもかかわらず **C-081 の時点でこの対を見ているゲートは 1 本も無かった**
（`check_web_gates.sh` の G-W7 は `web/index.html` しか見ない。しかも emcc が
要るので手元では回らない）。

**G-A2 — §3.1 が「在る」と書いたライセンス全文が、書いてある姿で在るか。**
`✅ …[`X`](X)… に在る（… sha256 `<prefix>…` / <N> 行 <M> B）` の形の主張を
**本文から読み取って**、実ファイルと突き合わせる。⚠️ **数値をこの
スクリプトに焼かない** — 焼くと「doc を直したがゲートが古い」形の乖離になる。

## 見ないもの

- **リリース資産の中身**。⚠️ 配布中の `v0.3.0` / `v0.3.1` は今も帰属が足りていない
  （C-081。差し替え待ち）。このゲートは**リポジトリの現在の内容**だけを見る。
- **`samples.zip` の中の `NOTICE.txt`**。作るときに md から生成しているが、
  出来上がった zip を開いて照合してはいない。
- **義務そのものの正しさ**（どの素材にどの条件が届くか）。それは D-055 / C-073 の判断。
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 正典の (A) ブロック: 見出し `### 3.1` 以降の**最初のコードフェンス**。
# ⚠️ **行番号で切らない**（§3.1 の上に 1 行入ると黙ってずれ、ずれた先も同じ行数ある）。
CANON = ("LICENSE-MODEL.md", re.compile(r"^### 3\.1 "), re.compile(r"^### "))
# 写し: (ファイル, 抽出の仕方)
COPY_FENCE = ("NOTICE.md", re.compile(r"^### \(A\) "), re.compile(r"^### "))
COPY_HTML = "web/index.html"

# G-A2 の主張: `[`X`](X)` … sha256 `<prefix>…` … <N> 行/lines <M> B/bytes
#
# ⚠️ **言語で絞らないこと。** 最初は段落に `に在る` を要求していたが、
#    **同じ主張が英語節にもある**（`LICENSE-MODEL.md` の §Obligations）。
#    日本語だけ見ると、**英語側の sha256 を書き換えても誰も気づかない。**
CLAIM = re.compile(
    r"\[`(?P<name>[^`]+)`\]\((?P<path>[^)]+)\)"                    # リンク
    r".*?sha256 `(?P<sha>[0-9a-f]{8,})…?`"                          # sha256 の頭
    # ⚠️ 区切りの読点まで含めて緩める。日本語は「202 行 11,358 B」、
    #    英語は「202 lines, 11,358 bytes」で**カンマが入る**。
    #    これを見落として、**英語の主張を 1 度取りこぼした**（この行を書いた直後に実測）。
    r".*?(?P<lines>[\d,]+)\s*(?:行|lines)[,、]?\s*(?P<bytes>[\d,]+)\s*(?:B|bytes)",
    re.DOTALL)

FLOOR = 20   # ⚠️ 空 == 空 で満点を取らせない。実体は 26 行（C-073 で 22 → 26）


def fence(text: str, start: re.Pattern[str], stop: re.Pattern[str]) -> list[str]:
    """`start` に一致する見出し以降、`stop` の手前までの**最初のコードフェンス**の中身。"""
    out: list[str] = []
    on = infence = False
    for line in text.splitlines():
        if not on:
            on = bool(start.match(line))
            continue
        if stop.match(line):
            break
        if line.startswith("```"):
            if infence:
                break
            infence = True
            continue
        if infence:
            out.append(line)
    return out


def pre_verbatim(text: str) -> list[str]:
    """`<pre class="verbatim">` … `</pre>` の中身（開き閉じと同じ行の本文も拾う）。"""
    i = text.find('<pre class="verbatim">')
    if i < 0:
        return []
    body = text[i + len('<pre class="verbatim">'):]
    j = body.find("</pre>")
    if j < 0:
        return []
    return body[:j].strip("\n").splitlines()


def claims(text: str) -> list[dict[str, str]]:
    """「この全文はここに在る」と断言している主張を拾う（**日英どちらも**）。

    鍵は **`sha256` を書いていること**。`[`LICENSE`](LICENSE)` のような
    義務ではないリンクは sha256 を持たないので入らない。
    ⚠️ **`に在る` で絞ってはいけない** — 英語節の同じ主張が抜ける。
    """
    out = []
    for para in re.split(r"\n\s*\n", text):
        if "sha256" not in para:
            continue
        m = CLAIM.search(para)
        if m:
            out.append(m.groupdict())
    return out


def check(files: dict[str, str]) -> list[str]:
    """NG の一覧を返す（空なら OK）。`files` は パス → 内容。"""
    ng: list[str] = []

    canon_name, start, stop = CANON
    canon = fence(files[canon_name], start, stop)
    if len(canon) < FLOOR:
        # ⚠️ ここで返さないと、以降の diff が「空 == 空」で全部通ってしまう。
        return [f"{canon_name} §3.1 の (A) ブロックの抽出が {len(canon)} 行 "
                f"（{FLOOR} 行未満）。**抽出か本文のどちらかが壊れている**"]

    # --- G-A1 写しが一字一句一致するか ---
    copies: dict[str, list[str]] = {}
    name, cstart, cstop = COPY_FENCE
    copies[name] = fence(files[name], cstart, cstop)
    copies[COPY_HTML] = pre_verbatim(files[COPY_HTML])

    for path, got in copies.items():
        if len(got) < FLOOR:
            ng.append(f"{path} の帰属ブロックの抽出が {len(got)} 行（{FLOOR} 行未満）。"
                      f"**抽出の側を直すこと**（0 行なら diff は「一致」になる）")
            continue
        if got != canon:
            only_canon = [l for l in canon if l not in got]
            only_copy = [l for l in got if l not in canon]
            ng.append(f"{path} の帰属ブロックが {canon_name} §3.1 と違う "
                      f"（{len(canon)} 行 vs {len(got)} 行）。**1 行でも欠けると "
                      f"その素材の条件に違反する**")
            for l in only_canon[:6]:
                ng.append(f"    正典にしか無い: {l!r}")
            for l in only_copy[:6]:
                ng.append(f"    写しにしか無い: {l!r}")

    # --- G-A2 「在る」と書いた全文が、書いてある姿で在るか ---
    cs = claims(files[canon_name])
    if not cs:
        # ⚠️ **主張が見つからないのを OK にしない。** C-081 は「義務を文章にしたが
        #    成果物が無い」形で、その裏返し（成果物はあるが主張が消えた）も同じ劣化。
        ng.append(f"{canon_name} に「全文は … に在る（sha256 …）」の主張が 1 件も無い。"
                  f"**Apache-2.0 §4(a) の同梱義務を書いた段落が消えていないか確かめること**")
    for c in cs:
        p = ROOT / c["path"]
        if not p.exists():
            ng.append(f"{canon_name} が「{c['name']} に在る」と書いているが、"
                      f"**{c['path']} が無い**（C-081 と同じ形）")
            continue
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        n_lines = len(raw.decode("utf-8").splitlines())
        n_bytes = len(raw)
        want_lines = int(c["lines"].replace(",", ""))
        want_bytes = int(c["bytes"].replace(",", ""))
        if not sha.startswith(c["sha"]):
            ng.append(f"{c['path']} の sha256 が {canon_name} の記載と違う "
                      f"（記載 {c['sha']}… / 実体 {sha[:len(c['sha'])]}…）。"
                      f"**ライセンス全文は一字でも違えば「全文の同梱」ではない**")
        if n_lines != want_lines or n_bytes != want_bytes:
            ng.append(f"{c['path']} の寸法が {canon_name} の記載と違う "
                      f"（記載 {want_lines} 行 {want_bytes:,} B / "
                      f"実体 {n_lines} 行 {n_bytes:,} B）")
    return ng


def load() -> dict[str, str]:
    paths = [CANON[0], COPY_FENCE[0], COPY_HTML]
    out = {}
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            sys.exit(f"NG! {rel} が無い")
        out[rel] = p.read_text(encoding="utf-8")
    return out


def self_test() -> int:
    """陽性対照。**落ちるべき壊し方が本当に落ちるか。**

    ⚠️ 壊すのはメモリ上の写しだけ（ファイルは触らない）。
    """
    base = load()
    canon_name = CANON[0]
    copy_name = COPY_FENCE[0]
    canon = fence(base[canon_name], CANON[1], CANON[2])
    victim = canon[3]        # ⚠️ 空行や末尾ではなく**本文の行**を選ぶ
    assert victim.strip(), f"4 行目が空だった: {victim!r}"

    cases: list[tuple[str, dict[str, str]]] = []

    # 1. NOTICE.md の写しから本文 1 行を消す
    m = dict(base)
    m[copy_name] = base[copy_name].replace(victim + "\n", "", 1)
    cases.append((f"{copy_name} から本文 1 行を消す", m))

    # 2. web/index.html の写しから本文 1 行を消す
    m = dict(base)
    m[COPY_HTML] = base[COPY_HTML].replace(victim + "\n", "", 1)
    cases.append((f"{COPY_HTML} から本文 1 行を消す", m))

    # 3. 写しの 1 文字を変える（行数は変わらない = 件数の検査では捕まらない）
    #
    # ⚠️ **`victim` の 1 行だけを `replace` してはいけない。** 最初にこう書いたら
    #    **陽性対照が通ってしまった**（= ゲートが空虚だと出た）。原因は
    #    `つくよみちゃんコーパス` が `NOTICE.md` に **5 か所ある**ことで、
    #    `replace(..., 1)` が (A) ブロックの外（散文の見出し）に当たっていた。
    #    **ブロック全体を鍵にして、その中で 1 文字変える。**
    block = "\n".join(canon)
    assert base[copy_name].count(block) == 1, "写しの中にブロックが 1 つだけ在ること"
    mutated_block = block.replace(victim, victim[:-1] + "X", 1)
    assert mutated_block != block and \
        len(mutated_block.splitlines()) == len(canon), "行数を変えずに 1 文字だけ"
    m = dict(base)
    m[copy_name] = base[copy_name].replace(block, mutated_block, 1)
    cases.append((f"{copy_name} の 1 文字を変える（行数は同じ）", m))

    # 4. 記載の sha256 を 1 文字変える
    m = dict(base)
    cs = claims(base[canon_name])
    assert cs, "主張が拾えていない"
    sha = cs[0]["sha"]
    m[canon_name] = base[canon_name].replace(f"sha256 `{sha}…`",
                                             f"sha256 `{sha[:-1]}0…`", 1)
    cases.append(("記載の sha256 を 1 文字変える", m))

    # 5. 記載の行数を変える
    m = dict(base)
    m[canon_name] = base[canon_name].replace(f"{cs[0]['lines']} 行", "999 行", 1)
    cases.append(("記載の行数を変える", m))

    # 6. 「全文は … に在る」の主張を**全部**消す（日英ともに）
    #    ⚠️ 1 件だけ消すと**もう 1 件が残って通る**ので、陽性対照にならない。
    m = dict(base)
    m[canon_name] = re.sub(r"sha256 `[0-9a-f]{8,}…?`", "(削除)", base[canon_name])
    assert not claims(m[canon_name]), "主張が残っている（陽性対照が空虚）"
    cases.append(("同梱義務の主張を全部消す", m))

    # 7. **英語節だけ** sha256 を書き換える（日本語側は正しいまま）
    #    ⚠️ かつては日本語段落しか見ていなかったので、**これが通っていた**。
    m = dict(base)
    #    ⚠️ 「最後に出てくる sha256」を書き換える（英語節は日本語節より後ろにある）。
    i = base[canon_name].rfind(f"sha256 `{sha}…`")
    assert i > 0, "英語側の sha256 が見つからない"
    j = base[canon_name].find(f"sha256 `{sha}…`")
    assert i != j, "sha256 の主張が 1 件しかない（英語節が消えている？）"
    m[canon_name] = (base[canon_name][:i]
                     + f"sha256 `{sha[:-1]}1…`"
                     + base[canon_name][i + len(f"sha256 `{sha}…`"):])
    cases.append(("英語節だけ sha256 を書き換える", m))

    print("陽性対照:")
    bad = 0
    for label, mutated in cases:
        got = check(mutated)
        if got:
            print(f"  OK  {label} → 落ちた（{got[0][:72]}…）")
        else:
            print(f"  NG! {label} → **通ってしまった**（このゲートは空虚）")
            bad += 1

    # 陰性対照: 素の状態は通ること
    got = check(base)
    if got:
        print("  NG! 陰性対照（無改変）が落ちた:")
        for l in got:
            print(f"        {l}")
        bad += 1
    else:
        print("  OK  陰性対照: 無改変では通る")

    print(f"\n{'OK' if not bad else 'NG!'} 陽性対照 {len(cases) - (bad and 0)} 件 "
          f"/ 失敗 {bad}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="陽性対照 7 件 + 陰性対照")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    files = load()
    canon = fence(files[CANON[0]], CANON[1], CANON[2])
    ng = check(files)
    if ng:
        print("NG! 帰属義務の成果物がずれている:")
        for l in ng:
            print(f"  {l}")
        return 1
    cs = claims(files[CANON[0]])
    print(f"OK  G-A1 帰属ブロック（{len(canon)} 行）が 3 か所で一字一句一致 "
          f"— {CANON[0]} §3.1 / {COPY_FENCE[0]} / {COPY_HTML}")
    print(f"OK  G-A2 同梱義務の主張 {len(cs)} 件（日英）がすべて実体と一致:")
    for c in cs:
        print(f"      {c['path']}: sha256 {c['sha']}… / {c['lines']} 行 {c['bytes']} B")
    print("\n⚠️ 見ていないもの: リリース資産の中身（v0.3.0 / v0.3.1 は今も帰属が"
          "足りていない = C-081）/ samples.zip の中の NOTICE.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
