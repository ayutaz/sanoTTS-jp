# 蒸留テキストを CC0 / PD のみにする 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 蒸留テキストから JSUT（CC-BY-SA-4.0 = 唯一の継承付き）6,472 行を外し、CC0 / パブリックドメインのみの 14,513 行で学習できる状態にする。

**Architecture:** コーパスの `source` 列にもとづく allowlist を `src/saanotts_jp/corpus_license.py` に 1 か所だけ置き、`scripts/gen_teacher_labels.py` がラベル生成時に適用する。判定結果はラベルパックの `index.jsonl` に `source` として残るので、**出荷物そのものを検査できる**。held-out は据え置く（評価は重みに入らないため）。

**Tech Stack:** Python 3.12+ / `uv run`（`pip` は使わない = D-012）/ 標準ライブラリのみ（新規依存なし）

**Spec:** [`../specs/2026-09-08-cc0-only-distillation-text-design.md`](../specs/2026-09-08-cc0-only-distillation-text-design.md)

## Global Constraints

- **Python は必ず `uv run` 経由**。`pip install` と uv を通さない python は使わない（[D-012](../../decisions.md#d-012)）。hook が deny する
- **piper-plus (`~/Documents/piper-plus`) は読み取り専用**（[D-003](../../decisions.md#d-003)）
- **`data/pack` を上書きしない。** hook が本番パックの破棄と再生成を deny する（[D-015](../../decisions.md#d-015)）。新しいパックは **`data/pack_cc0`**
- **`data/splits/corpus_*.tsv` は触らない**（spec §4.2）。⚠️ この worktree には**実体が無い**（Task 1 で symlink する）
- **`runs/v3` を触らない**（M-49 などの再現用）。新しい run は **`runs/v4`**
- **held-out は 1 行も変えない。** 凍結 SHA-256 = `72b78b8ccbd20e6e8f74694f4dd978451a3f4d1059c13389711339059c29ca7c`（2,334 行 = ヘッダ 1 + 本文 2,333）
- **数値を書くときは実測のみ。** 再現コマンドを併記する（skill `recording-measurements`）
- **ゲートには必ず陽性対照を付ける**（skill `writing-gates`）。⚠️ 陽性対照が落ちることを確認するまで、そのゲートは空虚として扱う
- **`uv run` は worktree で `uv.lock` を書き換える。** コミット前に `git checkout -- uv.lock`（`CLAUDE.md` の「開発環境のルール」）
- **`make -C csrc fft` は `csrc/fft_bench.json` を書き換える。** 同様に戻す
- **採番**: この計画で新しく振るのは **D-056 / M-113** 以降。⚠️ 未マージの他ブランチが D-053 / C-070 / M-108 まで使っているので、**書く直前に `git show origin/<branch>:docs/decisions.md` で最大値を再確認する**（[D-054](../../decisions.md#d-054) の表）

## File Structure

| ファイル | 責務 |
|---|---|
| `src/saanotts_jp/corpus_license.py`（新規） | **判定表と判定関数だけ。** I/O もログも持たない |
| `scripts/test_corpus_license.py`（新規） | **G-L1a**: 判定ロジックの検査。⚠️ 依存ゼロ = CI に入る。⚠️ `scripts/test_*.py` は `check_ci_coverage.py` に自動収集されるので、CI に入れないと落ちる |
| `scripts/check_corpus_license.py`（新規） | **G-L1b / G-L2 / 統計レポート**。実体（パック・コーパス本文）を見る。⚠️ 自動収集されるので `EXCLUDED_SCRIPTS` に理由を書く |
| `scripts/gen_teacher_labels.py`（変更） | allowlist の適用。⚠️ 既存の `load_exclusions()`（uid 単位・教師 FT 重複）とは**別経路**で併存 |
| `.github/workflows/ci.yml`（変更） | docs job に G-L1a を 1 ステップ足す |
| `scripts/check_ci_coverage.py`（変更） | `EXCLUDED_SCRIPTS` に `scripts/check_corpus_license.py` の理由を足す |

---

### Task 1: worktree にコーパス本文を用意する（実装ではなく前提の解消）

**Files:**
- Create: `data/splits/corpus_train.tsv`（symlink）
- Create: `data/splits/corpus_heldout.tsv`（symlink）

**Interfaces:**
- Consumes: なし
- Produces: 以降の全タスクが `data/splits/corpus_{train,heldout}.tsv` を読める

⚠️ **なぜ symlink でよいか**: `.gitignore:7` が `/data/splits/*` を無視しており、
追跡されている 6 ファイルは force-add されたもの。**`corpus_*.tsv` は ignore 対象**なので
symlink がコミットに混ざることはない。

- [ ] **Step 1: ignore されていることを確認する（先に確認する）**

Run:
```bash
cd /Users/s19447/Desktop/saanoTTS-jp/.claude/worktrees/feat+commercial-use-model
git check-ignore -v data/splits/corpus_train.tsv data/splits/corpus_heldout.tsv
```
Expected: 2 行とも `.gitignore:7:/data/splits/*` が出る（= ignore 済み）。
⚠️ **出なかったら symlink を作らない。** コミットに混ざる。

- [ ] **Step 2: symlink を張る**

```bash
ln -s /Users/s19447/Desktop/saanoTTS-jp/data/splits/corpus_train.tsv   data/splits/corpus_train.tsv
ln -s /Users/s19447/Desktop/saanoTTS-jp/data/splits/corpus_heldout.tsv data/splits/corpus_heldout.tsv
```

- [ ] **Step 3: 読めることと、held-out が凍結値どおりであることを確認**

Run:
```bash
head -1 data/splits/corpus_train.tsv
shasum -a 256 data/splits/corpus_heldout.tsv
```
Expected:
```
source	id	text
72b78b8ccbd20e6e8f74694f4dd978451a3f4d1059c13389711339059c29ca7c  data/splits/corpus_heldout.tsv
```

- [ ] **Step 4: `git status` が汚れていないことを確認**

Run: `git status --short`
Expected: 空（symlink は ignore されている）。⚠️ 出たら Step 2 を戻す。

**コミットしない**（追跡対象の変更が無い）。

---

### Task 2: 判定モジュールと G-L1a

**Files:**
- Create: `src/saanotts_jp/corpus_license.py`
- Test: `scripts/test_corpus_license.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `Verdict`（`enum.Enum`）: `ALLOWED` / `DENIED` / `UNKNOWN`
  - `classify(source: str) -> tuple[Verdict, str]` — `(判定, 理由の文字列)`
  - `ALLOWED: dict[str, str]` — `source -> ライセンス識別子`
  - `DENIED: dict[str, str]` — `source -> 除外の理由`
  - Task 3（`gen_teacher_labels.py`）と Task 4（`check_corpus_license.py`）が両方これを使う

⚠️ **完全一致（exact match）にする。前方一致にしない。**
`cv/` の前方一致だと、Common Voice が将来 `europarl-VERSION-LANG.txt` 由来のファイルを
`server/data/ja/` に足したとき**自動で許可される**。europarl は
[C-029](../../decisions.md#c-029) が「CV の README が挙げる唯一の CC0 例外」と記録した
まさにその危険物。**未知の source は UNKNOWN に落として人に判断させる。**

- [ ] **Step 1: 失敗するテストを書く**

`scripts/test_corpus_license.py`:
```python
#!/usr/bin/env python3
"""G-L1a: 蒸留テキストのライセンス判定（依存ゼロ）。

**ゲートは「落ちるべきものが落ちる」ことを確かめないと意味がない。**
ここでは (a) 許可 (b) 明示的な拒否 (c) **未知** の 3 通りを見る。

実行:
    uv run --no-project --python 3.12 python scripts/test_corpus_license.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
from saanotts_jp.corpus_license import (  # noqa: E402
    ALLOWED, DENIED, Verdict, classify,
)

# 2026-09-08 に data/splits/corpus_train.tsv と corpus_heldout.tsv を
# `cut -f1 | sort | uniq -c` で数えた実在の source（和集合 16 件）。
REAL_ALLOWED = [
    "cv/sentence_collector", "cv/yumie-text-1", "cv/singleword-benchmark",
    "rohan4600", "ita/recitation324", "ita/emotion100", "curated/question_eos",
]
REAL_DENIED = [
    "jsut/basic5000", "jsut/travel1000", "jsut/utparaphrase512",
    "jsut/onomatopee300", "jsut/precedent130", "jsut/loanword128",
    "jsut/repeat500", "jsut/countersuffix26", "jsut/voiceactress100",
]


def test_allowed() -> int:
    bad = 0
    for src in REAL_ALLOWED:
        v, why = classify(src)
        if v is Verdict.ALLOWED:
            print(f"  OK  許可 {src:26s} {why}")
        else:
            print(f"  NG! {src} が許可されない: {v} / {why}")
            bad += 1
    return bad


def test_denied() -> int:
    bad = 0
    for src in REAL_DENIED:
        v, why = classify(src)
        if v is Verdict.DENIED:
            print(f"  OK  拒否 {src:26s} {why}")
        else:
            print(f"  NG! {src} が拒否されない: {v} / {why}")
            bad += 1
    return bad


def test_unknown() -> int:
    """⚠️ **ここが本体。** 未知の source は黙って通ってはいけない。

    `cv/` の前方一致で許可していると、CV が europarl 由来のファイルを足した
    ときに自動で通る（C-029: europarl は CV の README が挙げる唯一の CC0 例外）。
    """
    bad = 0
    cases = [
        "cv/europarl-v7-ja",   # ⚠️ CC0 ではない。前方一致だと通ってしまう
        "cv/some-new-file",
        "jsut/newsubset",
        "wikipedia/ja",
        "",
    ]
    for src in cases:
        v, why = classify(src)
        if v is Verdict.UNKNOWN:
            print(f"  OK  未知 {src!r:28s} {why}")
        else:
            print(f"  NG! 未知の {src!r} が {v} になった: {why}")
            bad += 1
    return bad


def test_tables_disjoint() -> int:
    """同じ source が両方の表に居たら、判定は表の順序に依存してしまう。"""
    overlap = set(ALLOWED) & set(DENIED)
    if overlap:
        print(f"  NG! ALLOWED と DENIED が重複: {sorted(overlap)}")
        return 1
    print(f"  OK  表は排他（許可 {len(ALLOWED)} / 拒否 {len(DENIED)}）")
    return 0


def test_positive_control() -> int:
    """⚠️ **陽性対照**: 表から 1 件抜くと、その source は UNKNOWN に落ちる。

    これが落ちなければ、上の 3 つのテストは**表を見ていない**ことになる。
    """
    victim = "rohan4600"
    saved = ALLOWED.pop(victim)
    try:
        v, _ = classify(victim)
        if v is Verdict.UNKNOWN:
            print(f"  OK  陽性対照: 表から {victim} を抜くと UNKNOWN になる")
            return 0
        print(f"  NG! 陽性対照が効かない（{victim} を抜いても {v}）= **判定は表を見ていない**")
        return 1
    finally:
        ALLOWED[victim] = saved


def main() -> int:
    bad = (test_allowed() + test_denied() + test_unknown()
           + test_tables_disjoint() + test_positive_control())
    print()
    print("⚠️ 見ていないもの: **判定が実際に適用されたか**"
          "（gen_teacher_labels.py が呼び忘れてもこのテストは通る）。"
          "それは G-L1b = scripts/check_corpus_license.py --pack が見る。")
    print()
    print("すべて期待通り" if bad == 0 else f"{bad} 件 NG")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: テストが失敗することを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/test_corpus_license.py
```
Expected: FAIL — `ModuleNotFoundError: No module named 'saanotts_jp.corpus_license'`

- [ ] **Step 3: 最小の実装を書く**

`src/saanotts_jp/corpus_license.py`:
```python
"""蒸留テキストのライセンス判定（D-054 / spec 2026-09-08）。

**モデルの重みに継承 (copyleft) が及ぶ経路を蒸留テキストから消す**ための表。
JSUT ver1.1 のテキストは CC-BY-SA-4.0 で、本プロジェクトが使う素材の中で
**唯一の継承付き**（D-035 の「残るリスク」）。

⚠️ **完全一致で判定する。前方一致にしない。**
`cv/` の前方一致だと、Common Voice が `europarl-VERSION-LANG.txt` 由来の
ファイルを足したとき自動で許可される。europarl は CV の README が挙げる
**唯一の CC0 例外**（C-029）。**未知の source は UNKNOWN に落として人に判断させる。**

⚠️ **この表はライセンスだけを見る。** 教師の FT テキストとの重複除外
（uid 単位・B-10）は `gen_teacher_labels.py` の `load_exclusions()` が別に行う。
"""

from __future__ import annotations

import enum


class Verdict(enum.Enum):
    """判定。**UNKNOWN は「拒否」ではなく「人に聞く」**。"""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


# source -> ライセンス識別子。
# ⚠️ 一次ソースで確認した値だけを書く（C-029 / C-030 / C-031 は
#    台帳の `verified: false` を転記して 3 件とも間違えた）。
ALLOWED: dict[str, str] = {
    # Common Voice ja — CC0-1.0。一次ソースは common-voice の README（C-029）
    "cv/sentence_collector": "CC0-1.0",
    "cv/singleword-benchmark": "CC0-1.0",
    # ⚠️ 個別の CC0 waiver は確認できていない（探した範囲: PR #3968 の本文 /
    #    リポジトリ内 `yumie` 全文検索 0 件）。CV の一括規約でのみ担保。
    #    **継承付きではない**のでゴールを脅かさず、外すと 13,092 行で論文水準を下回る
    #    ため残す（spec §8）
    "cv/yumie-text-1": "CC0-1.0",
    # ROHAN4600 — 一次ソースに「ライセンスはパブリックドメインです．」+ CC0 バッジ
    "rohan4600": "CC0-1.0",
    # ITA — 一次ソース（mmorise/ita-corpus README）は「パブリックドメインです．」。
    # ⚠️ **CC0 の付与ではない**（C-073）。二次情報の「CC BY-SA 4.0」は誤り
    "ita/recitation324": "PD",
    "ita/emotion100": "PD",
    # 自作（疑問 EOS の 4 種）
    "curated/question_eos": "MIT",
}

# source -> 除外の理由。
# ⚠️ **「表に無い」と「明示的に拒否」を区別する**ため、拒否も列挙する。
#    列挙しないと、JSUT の subset が UNKNOWN になって「人に聞く」側に落ちる。
DENIED: dict[str, str] = {
    f"jsut/{sub}": "JSUT ver1.1 = CC-BY-SA-4.0（唯一の継承付き。D-054）"
    for sub in ("basic5000", "travel1000", "utparaphrase512", "onomatopee300",
                "loanword128", "repeat500", "countersuffix26", "voiceactress100")
}
# ⚠️ precedent130 だけは PD とされているが、**一緒に落とす**（2026-09-08 ユーザー判断）。
# 117 行のために PD かどうかを一次ソースで検証する手間が見合わない
DENIED["jsut/precedent130"] = (
    "JSUT の一部。PD とされるが検証せず一緒に落とす（117 行。D-054）")


def classify(source: str) -> tuple[Verdict, str]:
    """`(判定, 理由)` を返す。"""
    if source in ALLOWED:
        return Verdict.ALLOWED, ALLOWED[source]
    if source in DENIED:
        return Verdict.DENIED, DENIED[source]
    return Verdict.UNKNOWN, (
        f"表に無い source {source!r}。ライセンスを一次ソースで確認して "
        f"src/saanotts_jp/corpus_license.py の ALLOWED か DENIED に足すこと")
```

⚠️ **`is_allowed()` のような真偽値の簡易版は作らない。**
呼び出し側は **DENIED（意図した除外）と UNKNOWN（人に聞く）を区別しなければならない**
のに、真偽値にすると両方 `False` に潰れる。区別を潰す API を用意すると、
いつか誰かがそれを使って未知の source を黙って落とす。

- [ ] **Step 4: テストが通ることを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/test_corpus_license.py
```
Expected: PASS — 許可 7 / 拒否 9 / 未知 5 / 表は排他 / 陽性対照、最後に `すべて期待通り`

- [ ] **Step 5: CI に足す**

`.github/workflows/ci.yml` の docs job、`blob → .rodata ヘッダの変換` ステップの**直後**に:
```yaml
      - name: 蒸留テキストのライセンス判定（G-L1a・陽性対照つき）
        run: uv run --no-project --python 3.12 python scripts/test_corpus_license.py
```

- [ ] **Step 6: CI カバレッジのゲートが通ることを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py --self-test
```
Expected: 両方 PASS。⚠️ Step 5 を飛ばすと
`scripts/test_corpus_license.py は CI で回らず、EXCLUDED_SCRIPTS に理由も無い` で落ちる
（`SCRIPT_GLOBS` が `scripts/test_*.py` を自動収集する）。

- [ ] **Step 7: コミット**

```bash
git checkout -- uv.lock
git add src/saanotts_jp/corpus_license.py scripts/test_corpus_license.py .github/workflows/ci.yml
git commit -m "feat: 蒸留テキストのライセンス判定表と G-L1a（未知の source は UNKNOWN に落とす）"
```

---

### Task 3: ラベル生成に適用する

**Files:**
- Modify: `scripts/gen_teacher_labels.py`（`load_exclusions` の定義付近と、`main()` の行フィルタ部分）

**Interfaces:**
- Consumes: Task 2 の `classify(source) -> (Verdict, str)` と `Verdict`
- Produces: `--license-filter / --no-license-filter` フラグ。既定は**有効**
  ⚠️ **2026-09-09 訂正（このプランは今も生きている。過去の記述ではない）**:
  この「既定は有効」固定は、レビューで「`--split heldout --out data/pack_heldout`
  が既定のまま 717 行を黙って落とす」欠陥として指摘され、実装は
  `resolve_license_filter(split, explicit)` に直っている（D-056）。
  **今の実際の既定**: 評価専用 split（`heldout` / `sibdense`。
  `gen_teacher_labels_filter.EVAL_ONLY_SPLITS`）は **OFF**、
  それ以外（`train` を含む）は引き続き **ON**。`--license-filter` /
  `--no-license-filter` を明示すれば常にそちらが勝つ。Step 5 のコード片は
  この訂正前の形のまま残してある（提案として書いた当時の記録）

⚠️ **既存の `load_exclusions()` を置き換えない。** あれは uid 単位で
「教師の FT テキストとの重複」を外す（B-10 = 丸暗記を測らないため）。**目的が違うので併存。**

⚠️ **UNKNOWN は落とさず、実行を止める。** 黙って行が減るのが一番危ない
（C-018 で「ヘッダ行が発話として通った」のと同じ形）。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/test_corpus_license.py` の末尾（`main()` の直前）に追加:
```python
def test_applied_in_label_gen() -> int:
    """⚠️ **判定表が gen_teacher_labels.py に実際に配線されているか。**

    G-L1a は表しか見ないので、呼び忘れを捕まえられない。ここでは
    「絞り込み関数が export されていて、行のリストを正しく削る」ことを見る。
    **教師モデルは読み込まない**（重いので）。
    """
    import importlib
    m = importlib.import_module("gen_teacher_labels_filter")
    rows = [
        ["cv/sentence_collector", "a", "あ"],
        ["jsut/basic5000", "b", "い"],
        ["rohan4600", "c", "う"],
        ["ita/emotion100", "d", "え"],
    ]
    kept, dropped = m.filter_by_license(rows)
    bad = 0
    if [r[1] for r in kept] != ["a", "c", "d"]:
        print(f"  NG! 絞り込みの結果が違う: {[r[1] for r in kept]}")
        bad += 1
    else:
        print("  OK  jsut/basic5000 の 1 行だけが落ちた")
    if dropped.get(("jsut/basic5000", "denied")) != 1:
        print(f"  NG! 内訳が取れていない: {dict(dropped)}")
        bad += 1
    else:
        print("  OK  落ちた内訳が source ごとに数えられている")

    # ⚠️ UNKNOWN は落とさず止める
    try:
        m.filter_by_license([["wikipedia/ja", "x", "お"]])
    except SystemExit as exc:
        print(f"  OK  未知の source で実行が止まる: {str(exc)[:40]}…")
    else:
        print("  NG! 未知の source が**黙って落ちた**（止まらなければ行数が静かに減る）")
        bad += 1
    return bad
```
そして `main()` の合計に `+ test_applied_in_label_gen()` を足す。

⚠️ **`gen_teacher_labels.py` は import すると torch を掴む**ので、絞り込みだけを
`scripts/gen_teacher_labels_filter.py` に切り出して、両方から import する。

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run --no-project --python 3.12 python scripts/test_corpus_license.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gen_teacher_labels_filter'`

- [ ] **Step 3: 絞り込みを実装する**

`scripts/gen_teacher_labels_filter.py`（新規）:
```python
#!/usr/bin/env python3
"""ラベル生成の行フィルタ（ライセンス）。

⚠️ **`gen_teacher_labels.py` から切り出してある。** あちらは import すると
torch と教師 ckpt を掴むので、**依存ゼロで検査できるようにここに置く**。
"""

from __future__ import annotations

import collections
import sys

sys.path.insert(0, "src")
from saanotts_jp.corpus_license import Verdict, classify  # noqa: E402


def filter_by_license(
    rows: list[list[str]],
) -> tuple[list[list[str]], collections.Counter]:
    """`source` 列が許可されている行だけを返す。

    ⚠️ **UNKNOWN は落とさず SystemExit で止める。**
    黙って行が減るのが一番危ない（C-018 と同じ形）。
    """
    kept: list[list[str]] = []
    dropped: collections.Counter = collections.Counter()
    unknown: dict[str, int] = collections.Counter()
    for r in rows:
        source = r[0]
        verdict, why = classify(source)
        if verdict is Verdict.ALLOWED:
            kept.append(r)
        elif verdict is Verdict.DENIED:
            dropped[(source, verdict.value)] += 1
        else:
            unknown[source] += 1
    if unknown:
        detail = ", ".join(f"{s} ({n} 行)" for s, n in sorted(unknown.items()))
        raise SystemExit(
            f"表に無い source が {len(unknown)} 種 / {sum(unknown.values())} 行ある: "
            f"{detail}\n"
            "ライセンスを一次ソースで確認して "
            "src/saanotts_jp/corpus_license.py の ALLOWED か DENIED に足すこと。\n"
            "⚠️ 黙って落とすと、行数が静かに減ったことに誰も気づかない。")
    return kept, dropped
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run --no-project --python 3.12 python scripts/test_corpus_license.py`
Expected: PASS（`OK  jsut/basic5000 の 1 行だけが落ちた` などが出る）

- [ ] **Step 5: `gen_teacher_labels.py` に配線する**

`scripts/gen_teacher_labels.py` の `main()`、`excluded = load_exclusions()` の**直前**に
ライセンス絞り込みを入れる。現在のコードはこうなっている（`178`〜`190` 行付近）:
```python
    rows = [r for r in csv.reader(
        open(f"data/splits/corpus_{args.split}.tsv"), delimiter="\t")
        if r and r[-1] and r[0] != "source"]
    excluded = load_exclusions()
```
これを次に置き換える:
```python
    rows = [r for r in csv.reader(
        open(f"data/splits/corpus_{args.split}.tsv"), delimiter="\t")
        if r and r[-1] and r[0] != "source"]

    # ⚠️ ライセンス絞り込み（D-054）。**下の load_exclusions() とは目的が違う**:
    #    あちらは uid 単位で「教師の FT テキストとの重複」を外す（B-10）。
    n_raw = len(rows)
    if args.license_filter:
        rows, lic_dropped = filter_by_license(rows)
        for (src, why), n in sorted(lic_dropped.items()):
            print(f"  ライセンス除外 {src:26s} {n:6,} 行  ({why})")
        print(f"  ライセンス除外 合計 {n_raw - len(rows):,} 行 / {n_raw:,} 行")
    else:
        print("  ⚠️ --no-license-filter: ライセンス絞り込みを **していない**")

    excluded = load_exclusions()
```
`import` に足す（ファイル冒頭の import 群の末尾）:
```python
from gen_teacher_labels_filter import filter_by_license
```
`argparse` に足す（`--utts-per-shard` の直後）:
```python
    ap.add_argument("--no-license-filter", dest="license_filter",
                    action="store_false",
                    help="⚠️ ライセンス絞り込みを切る（v3 の再現用。既定は有効）")
```

⚠️ **2026-09-09 訂正**: 上のヘルプ文言「既定は有効」も、Step 1〜5 で示した
`args.license_filter` の bool 直読みも、**この後 D-056 で置き換わった**（この
プランは今も有効で、Task 6〜8 は未実行）。実装済みの `gen_teacher_labels.py` は
`--license-filter` / `--no-license-filter` の**両方とも `default=None`** にし、
どちらも指定しなければ `resolve_license_filter(args.split, args.license_filter)`
（`scripts/gen_teacher_labels_filter.py`）が **split から既定を決める**
（評価専用の `heldout` / `sibdense` は OFF、それ以外は ON）。上のコード片は
提案当時の形のまま残す。

- [ ] **Step 6: 実データで行数を確認する（教師は読まない）**

⚠️ ラベル生成の本体は教師 ckpt を読むので重い。**絞り込みだけを確認する:**

Run:
```bash
uv run --no-project --python 3.12 python - <<'PY'
import csv, sys
sys.path.insert(0, "scripts")
from gen_teacher_labels_filter import filter_by_license
for split in ("train", "heldout"):
    rows = [r for r in csv.reader(
        open(f"data/splits/corpus_{split}.tsv"), delimiter="\t")
        if r and r[-1] and r[0] != "source"]
    kept, dropped = filter_by_license(rows)
    print(f"{split:8s} {len(rows):6,} → {len(kept):6,}  (除外 {sum(dropped.values()):,})")
PY
```
Expected:
```
train    20,985 → 14,513  (除外 6,472)
heldout   2,333 →  1,616  (除外 717)
```
⚠️ **held-out の 1,616 はここでは「参考」。** 実際の held-out パックは
`--no-license-filter` で作る（spec §4.3。評価は重みに入らないので JSUT を残す）。
**これは Task 5 の判断点で扱う。**

- [ ] **Step 7: コミット**

```bash
git checkout -- uv.lock
git add scripts/gen_teacher_labels_filter.py scripts/gen_teacher_labels.py scripts/test_corpus_license.py
git commit -m "feat: ラベル生成にライセンス絞り込みを配線した（未知の source では止まる）"
```

---

### Task 4: 実体を見るゲートと統計レポート（G-L1b / G-L2）

**Files:**
- Create: `scripts/check_corpus_license.py`
- Modify: `scripts/check_ci_coverage.py`（`EXCLUDED_SCRIPTS` に 1 行）

**Interfaces:**
- Consumes: Task 2 の `classify` / `Verdict`
- Produces: CLI 3 モード
  - `--pack <dir>` — **G-L1b**: パックの `index.jsonl` に許可外の `source` が 0 件
  - `--heldout` — **G-L2**: `corpus_heldout.tsv` の SHA-256 が凍結値と一致
  - `--report` — 統計レポート（**ゲートではない**）

⚠️ **G-L1a との違いが本質**: G-L1a は表を見るだけなので、`gen_teacher_labels.py` が
判定を**呼び忘れても通る**。G-L1b は出荷物（パック）を見るので呼び忘れを捕まえる。

- [ ] **Step 1: 失敗するテストを書く**

⚠️ このスクリプト自身の陽性対照は**スクリプトの中に持たせる**
（パックが要るので `scripts/test_*.py` には置けない）。`--self-test` を作る。

`scripts/check_corpus_license.py` の受け入れ条件を先に固定するため、
まず**期待する出力**を書き下す:

```
$ uv run --no-project --python 3.12 python scripts/check_corpus_license.py --self-test
陽性対照: 許可外の source を 1 件混ぜた index.jsonl が検出された（jsut/basic5000）
陽性対照: SHA-256 を 1 文字変えた held-out が検出された
OK  --self-test: 2 件の陽性対照がどちらも落ちた
```

- [ ] **Step 2: テストが失敗することを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --self-test
```
Expected: FAIL — `No such file or directory: 'scripts/check_corpus_license.py'`

- [ ] **Step 3: 実装する**

`scripts/check_corpus_license.py`:
```python
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


def check_pack(root: pathlib.Path) -> int:
    """G-L1b: パックの index.jsonl に許可外の source が無いか。"""
    idx = root / "index.jsonl"
    if not idx.exists():
        print(f"NG! {idx} が無い")
        return 1
    seen: collections.Counter = collections.Counter()
    bad: collections.Counter = collections.Counter()
    for ln in idx.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        source = json.loads(ln)["source"]
        seen[source] += 1
        if classify(source)[0] is not Verdict.ALLOWED:
            bad[source] += 1
    if not seen:
        print(f"NG! {idx} が空 = **この検査は何も見ていない**")
        return 1
    for src, n in sorted(seen.items()):
        mark = "NG!" if src in bad else "   "
        print(f"  {mark} {src:26s} {n:6,} 発話  ({classify(src)[1]})")
    if bad:
        print(f"\nNG! 許可外の source が {sum(bad.values()):,} 発話 "
              f"({len(bad)} 種): {sorted(bad)}")
        return 1
    print(f"\nOK  {sum(seen.values()):,} 発話すべて許可された source "
          f"（{len(seen)} 種）")
    print("⚠️ 見ていないもの: **音**と**品質**。行が正しいことは音が良いことではない")
    return 0


def check_heldout() -> int:
    """G-L2: held-out が 1 行も変わっていないか。"""
    p = pathlib.Path("data/splits/corpus_heldout.tsv")
    if not p.exists():
        print(f"NG! {p} が無い（worktree なら symlink を張る。計画 Task 1）")
        return 1
    raw = p.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    lines = len(raw.decode("utf-8").splitlines())
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
    """⚠️ **陽性対照。** 落ちるべきものが落ちるか。"""
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
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
            print("陽性対照: 許可外の source を 1 件混ぜた index.jsonl が"
                  "検出された（jsut/basic5000）")
        else:
            print("NG! 陽性対照が効かない = **--pack は何も見ていない**")
            bad += 1

        # 空の index.jsonl も落ちること（空虚に通らないことの確認）
        (root / "index.jsonl").write_text("", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            if check_pack(root) == 0:
                print("NG! 空の index.jsonl が通った = **空虚に通るゲート**")
                bad += 1

    if HELDOUT_SHA256 == hashlib.sha256(b"").hexdigest():
        print("NG! 凍結 SHA-256 が空ファイルのハッシュになっている")
        bad += 1
    else:
        print("陽性対照: SHA-256 を 1 文字変えた held-out が検出された")

    if bad == 0:
        print("OK  --self-test: 2 件の陽性対照がどちらも落ちた")
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
```

- [ ] **Step 4: 陽性対照が通ることを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --self-test
```
Expected: PASS — `OK  --self-test: 2 件の陽性対照がどちらも落ちた`

- [ ] **Step 5: G-L2 と統計レポートを実データで回す**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --heldout
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --report
```
Expected: `--heldout` は 2 行とも OK。`--report` は `20,985 行 → 14,513 行（除外 6,472）` と
2 つの文字種・文長の表が出る。

- [ ] **Step 6: CI カバレッジの除外表に足す**

`scripts/check_ci_coverage.py` の `EXCLUDED_SCRIPTS`、`scripts/test_discriminator.py` の
行の**直後**に:
```python
    "scripts/check_corpus_license.py": "ラベルパック（data/pack_cc0）とコーパス本文（data/splits/*.tsv。どちらも git 管理外）。⚠️ 表だけの検査は scripts/test_corpus_license.py が CI で回している",
```

- [ ] **Step 7: ゲートが通ることを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py --self-test
```
Expected: 両方 PASS。`--  scripts/check_corpus_license.py: ラベルパック…` の行が出る。

- [ ] **Step 8: コミット**

```bash
git checkout -- uv.lock
git add scripts/check_corpus_license.py scripts/check_ci_coverage.py
git commit -m "feat: G-L1b / G-L2 と統計レポート（パックとコーパス本文を実体で検査）"
```

---

### Task 5: 判断点 — 統計を記録し、学習に進むかを決める

**Files:**
- Modify: `docs/measurements.md`（新しい M-番号）
- Modify: `docs/decisions.md`（新しい D-番号）
- Modify: `docs/README.md` / `CONTRIBUTING.md` / `.claude/skills/recording-measurements/SKILL.md`（採番）

**Interfaces:**
- Consumes: Task 3 Step 6 と Task 4 Step 5 の出力
- Produces: 判断（Task 6 に進むか、声の確定を待つか）

⚠️ **ここで止まる。** spec §5 の判断点。**ユーザーの判断が要る**（2026-09-08 に
「両方を並べて後で決める」と決まっている）。

- [ ] **Step 1: 採番の衝突を確認する**

Run:
```bash
git fetch origin --prune
git show origin/feat/8mb-survey-jdict-hardening:docs/measurements.md > /tmp/a.md
git show origin/fix/dependabot-deps:docs/measurements.md > /tmp/b.md
grep -o '^## M-[0-9]*' /tmp/a.md | tail -1
grep -o '^## M-[0-9]*' /tmp/b.md | tail -1
grep -o '^## M-[0-9]*' docs/measurements.md | tail -1
```
⚠️ **3 つの最大値より大きい番号を使う。** 2026-09-09 時点では
他が M-108 / M-105、こちらが M-112 なので **M-113**。

- [ ] **Step 2: 実測を記録する**

`docs/measurements.md` の末尾に追記。**Task 3 Step 6 と Task 4 Step 5 の出力を
そのまま貼る**（`recording-measurements` skill: 再現コマンドと実際の出力を必ず併記）。
書く内容:

| 項目 | 値 |
|---|---:|
| train（v3） | 20,985 行 |
| train（CC0/PD のみ） | 14,513 行 |
| 除外 | 6,472 行（30.84%） |
| 論文の英語版 | 14,343 行 |
| held-out | **2,333 行のまま（据え置き）** |

⚠️ 併記すること: **文字種と文長の差**（Task 4 の `--report` 出力）/
**失う多様性軸 525 行**（助数詞 23 / カタカナ語 115 / オノマトペ 270 / 法令文 117）/
**⚠️ 音素カバレッジは未測定**（OpenJTalk が要るのでラベル生成後）。

- [ ] **Step 3: 採番のカウンタを更新してゲートを通す**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_doc_counters.py
```
Expected: `実体: M-113（98 件）/ …` と 4 つの OK。
⚠️ 落ちたら `docs/README.md`（2 箇所）と `CONTRIBUTING.md`（2 箇所）と
`.claude/skills/recording-measurements/SKILL.md`（1 箇所）の宣言値を直す。

- [ ] **Step 4: ユーザーに判断を仰ぐ**

**ここで作業を止めて報告する。** 聞くこと:
1. この統計で **Task 6（ラベル再生成 + 学習）に進むか**、声の確定を待つか
2. **多様性軸の補充**（助数詞 / カタカナ語 / オノマトペ）を先にやるか
   — ⚠️ spec §7「**先に測る。測って落ちた軸だけ補う**」

- [ ] **Step 5: 判断を D-番号として記録してコミット**

```bash
git checkout -- uv.lock
git add docs/measurements.md docs/decisions.md docs/README.md CONTRIBUTING.md .claude/skills/recording-measurements/SKILL.md
git commit -m "docs: CC0/PD のみの蒸留テキストの統計と、学習に進むかの判断"
```

---

### Task 6: ラベル再生成（**Task 5 の判断が「進む」のときだけ**）

**Files:**
- Create: `data/pack_cc0/`（git 管理外）

**Interfaces:**
- Consumes: Task 3 の絞り込み、Task 4 の G-L1b
- Produces: `data/pack_cc0`（学習用）。held-out パックは**作り直さない**（既存の `data/pack_heldout` をそのまま使う）

⚠️ **`data/pack` を上書きしない**（[D-015](../../decisions.md#d-015)。hook が deny する）。
⚠️ **ラベル生成は CPU。MPS を使わない**（CPU と MPS は bit 一致しない = M-21）。

- [ ] **Step 1: 少数で通し、13 個の既存ゲートが発火しないことを確認**

Run:
```bash
uv run python scripts/gen_teacher_labels.py --split train --limit 32 --out /tmp/pack_smoke
```
Expected: `ライセンス除外 jsut/... ` の内訳が出て、`--limit 32` で打ち切られる。
⚠️ **先頭 1 件を目で見る**（C-018: ヘッダ行が発話として通った事故）:
```bash
head -1 /tmp/pack_smoke/index.jsonl
```
Expected: `seq: 0` の `source` が `cv/` か `rohan4600` か `ita/` か `curated/` で、
`text` が実際の日本語の文。⚠️ `"text": "text"` なら**ヘッダ行が混ざっている**。

- [ ] **Step 2: G-L1b がスモークパックを通すことを確認**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --pack /tmp/pack_smoke
```
Expected: `OK  32 発話すべて許可された source`

- [ ] **Step 3: 本番のラベル生成**

Run:
```bash
uv run python scripts/gen_teacher_labels.py --split train --out data/pack_cc0
```
Expected: `train: 14,4xx 行`（14,513 から B-10 の教師 FT 重複を引いた数）。
⚠️ **所要時間は測って記録する。** `CLAUDE.md` の「約 40 分」は 20,894 文のときの値で、
行数が 30% 減るので**別の数になる**。

- [ ] **Step 4: G-L1b を本番パックに回す**

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_corpus_license.py --pack data/pack_cc0
```
Expected: `OK  14,4xx 発話すべて許可された source（7 種）`
⚠️ **`jsut/` の行が 1 件でも出たら止める。**

- [ ] **Step 5: 音素カバレッジを測る（Task 5 で測れなかったもの）**

Run:
```bash
uv run python - <<'PY'
import sys; sys.path.insert(0, "src")
import numpy as np
from saanotts_jp.labelpack import PackReader
for name in ("data/pack", "data/pack_cc0"):
    r = PackReader(name)
    ids = np.concatenate([r.ids[r.offsets[i]:r.offsets[i + 1]]
                          for i in range(len(r))])
    u, c = np.unique(ids, return_counts=True)
    print(f"{name:18s} 発話 {len(r):6,} / ユニーク音素ID {len(u):3d} / "
          f"最小出現 {c.min():,} (id={u[c.argmin()]})")
PY
```
⚠️ **`data/pack` は読むだけ**（hook が deny するのは破棄と再生成）。
⚠️ **ユニーク音素 ID が減っていたら止めて報告する** — 語彙の穴は学習前に分かる唯一の欠陥。

- [ ] **Step 6: コミットは無い（パックは git 管理外）**

代わりに **M-番号として記録**する（行数・所要時間・音素カバレッジ・SHA-256）。

---

### Task 7: 学習と品質ゲート（**Task 6 の後**）

**Files:**
- Create: `runs/v4/`（git 管理外）

**Interfaces:**
- Consumes: `data/pack_cc0`
- Produces: `runs/v4/stage4.pt`

⚠️ **`runs/v3` を触らない。** M-49 などの再現用。

- [ ] **Step 1: 学習 4 段**

Run:
```bash
uv run python scripts/train_student.py --run runs/v4 --all --steps 20000
```
⚠️ **Stage 3 は 80,000 step**（[D-037](../../decisions.md#d-037) = v3 と同じ構成にする。
そうしないと「JSUT を外した影響」と「step 数の違い」が混ざる）。
`--steps` の与え方は `scripts/train_student.py --help` で確認する。

- [ ] **Step 2: held-out で合成**

Run:
```bash
uv run python scripts/synthesize_student.py --ckpt runs/v4/stage4.pt \
    --texts data/splits/corpus_heldout.tsv --limit 24 --out reports/student_wav_v4
```

- [ ] **Step 3: 品質を v3 と比べる**

Run:
```bash
uv run python scripts/release_metrics.py --help
```
⚠️ **G1（前処理の一致）を必ず通す。** [C-038](../../decisions.md#c-038) で、
生徒だけ前後 0.3 秒のパディングが無い音声を教師と比べて 3 件の数値を間違えた。
**「教師側の値が過去の記録と一致した」は、生徒側の前処理が揃っている証拠にならない。**

比べる項目（v3 の値は [M-61](../../measurements.md#m-61) / [M-59](../../measurements.md#m-59)）:

| 指標 | v3 |
|---|---:|
| SCOREQ synthetic/nr（教師比） | 0.6444 [0.6044, 0.6843] |
| DNSMOS OVRL（教師比） | 0.7969 [0.7756, 0.8173] |
| かな CER | 0.1671 |
| アクセント符号一致 | 37/37 |
| int8 最小 SNR | 25.72 dB |

⚠️ **n=24 なので CI を必ず併記する。** 「下がった / 上がった」を点推定で言わない
（[C-004](../../decisions.md#c-004) / [C-017](../../decisions.md#c-017)）。

- [ ] **Step 4: 結果を M-番号として記録し、次を決める**

⚠️ **ここも判断点。** 品質が落ちていたら (a) 多様性軸を補充して再学習
(b) JSUT を戻す (c) 受け入れる のどれかをユーザーが決める。**私が決めない。**

- [ ] **Step 5: コミット**

```bash
git checkout -- uv.lock
git add docs/measurements.md docs/decisions.md docs/README.md
git commit -m "docs: CC0/PD のみで学習した v4 の品質を v3 と比べた"
```

---

### Task 8: 出荷物の再凍結（**Task 7 で品質が受け入れられたときだけ**）

> ✅ **完走した。⚠️ ただし計画の 3 倍以上に広がった**（2026-09-10）。
> 計画は「`NOTICE.md` / `LICENSE-MODEL.md` / `MODEL_CARD.md` を直す」だけだったが、
> 実際に出荷可能にするには次が要った:
>
> | 計画に無かったもの | なぜ要ったか |
> |---|---|
> | **firmware 10 本**（計画は暗黙に 3 本） | `v0.3.1` が **27 資産**を配り始めた（小容量 8/4/2 MB + UART0 版。⚠️ **「26」と書いていたのは `SHA256SUMS.txt` を数えていなかった** — `gh release view v0.3.1` で数えると 27）。3 本だと**後退**になる |
> | **出荷用ビルドの作り直し** | `build_kanji` は QEMU 用（DIO + 起動時発話 + UART0）で**配れなかった**（[M-122](../../measurements.md#m-122)） |
> | **`LICENSE-APACHE-2.0.txt`** | [C-073](../../decisions.md#c-073) で AISHELL-3 を必須帰属に足した結果、Apache-2.0 §4(a) の全文同梱が発生（[C-081](../../decisions.md#c-081)） |
> | **`web/index.html` の帰属ブロック** | 同じ写しを置き去りにしていた（[C-080](../../decisions.md#c-080)。CI の G-W7 が捕まえた） |
> | **`saanotts-jp-v4-samples.zip`** | `v0.3.x` が配っていたので揃えた。⚠️ **NOTICE は md から生成**した（手で写すと C-080 の再発） |
> | **`v0.3.0` / `v0.3.1` の帰属差し替え** | 同じ欠陥が過去のリリースにも及んでいた。⚠️ **アップロードは未実行** |
> | **実機での確認** | 計画に無かったが、[M-123](../../measurements.md#m-123) / [M-124](../../measurements.md#m-124) で焼いた |
>
> **合計 28 資産**（`SHA256SUMS.txt` が覆うのは 27 本）。
> ⚠️ **タグは `v1.0.0`**（[D-059](../../decisions.md#d-059)）。⚠️ **Release は未実行 / 音は未聴取。**

**Files:**
- Modify: `NOTICE.md` / `LICENSE-MODEL.md`（JSUT の行を (A) 必須ブロックから外す）
- Modify: `MODEL_CARD.md`

**Interfaces:**
- Consumes: `runs/v4/stage4.pt`
- Produces: golden 資産 / int8 blob / firmware / 更新されたライセンス文

- [ ] **Step 1: golden と int8 blob を作り直す**

⚠️ **手順は既存のスクリプトに従う。** 先に確認する:
```bash
ls scripts/ | grep -iE "blob|golden|export"
```
⚠️ **blob の version フィールドは v2 のまま**（[C-057](../../decisions.md#c-057)）。

- [ ] **Step 2: C99 コアの全ゲートを通す**

Run:
```bash
make -C csrc all-test
```
Expected: golden / stream / fft / int8 / int8-golden / int8-e2e / arena / g2p / pad /
line / erf / range が全部 PASS。
⚠️ **`stream` が held-out 24 文 × 3 レーンの bit 一致を見る**（最強のゲート）。

- [ ] **Step 3: ライセンス文から JSUT を外す**

`LICENSE-MODEL.md` §3.1 (A) の「蒸留に使用したテキストコーパス」から
**JSUT の 2 行を削除**し、§5「既知の法的リスク」を**節ごと削除**する。
`NOTICE.md` / `MODEL_CARD.md` の写しも同様。§6 の表からも JSUT の行を外す。

⚠️ **v0.3.0 以前の資産は JSUT 込みで学習されている。** ライセンス文を
差し替えるなら**どのバージョンからか**を明記する。

Run:
```bash
uv run --no-project --python 3.12 python scripts/check_doc_links.py
uv run --no-project --python 3.12 python scripts/check_doc_counters.py
```

- [ ] **Step 4: コミット**

```bash
git checkout -- uv.lock csrc/fft_bench.json
git add NOTICE.md LICENSE-MODEL.md MODEL_CARD.md docs/
git commit -m "docs: 蒸留テキストが CC0/PD のみになったので継承リスクの節を外した"
```

---

## ⚠️ この計画が扱わないこと

| | 理由 |
|---|---|
| **教師の声の差し替え**（つくよみちゃん → 別の声） | ⚠️ **2026-09-10 に「やらない」と決まった**（[D-058](../../decisions.md#d-058)）。つくよみちゃんの条件（出力の用途制限 4 項目 + コピーレフト）を**受け入れて配布する**。**D-054 のゴールはそれに合わせて改めた** |
| **教師の再学習** | piper-plus 側の作業。本リポジトリの範囲外（読み取り専用 = D-003） |
| `data/splits/corpus_*.tsv` の再生成 | split 生成スクリプトがリポジトリに無い（spec §4.2） |
| 多様性軸の補充 | **先に測る**（spec §7）。Task 5 / Task 7 の判断点で決める |
| [C-072](../../decisions.md#c-072) / [C-073](../../decisions.md#c-073) のライセンス文訂正 | **既に済んでいる**（コミット `87e5f87`） |
