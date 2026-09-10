#!/usr/bin/env python3
"""`pyproject.toml` の制約を `uv.lock` の固定版が満たしているか。

⚠️ **このゲートが埋めている穴**（[C-071](../docs/decisions.md#c-071)）:
`ci.yml` の 6 job のうち 4 job は `uv run --no-project`、`python` job は
`uv pip install` で作った即席 venv、`golden` は python を呼ばない。**どれ 1 つとして
`uv.lock` / `pyproject.toml` を解決しない。** D-053 で依存を上げたときの中心的な変更を
守るゲートが 1 本も無かった。

⚠️ **捕まえられるのは 1 つの壊れ方だけ**: **`pyproject.toml` の制約を厳しくしたのに
`uv.lock` を作り直していない**（下限を上げた / パッケージを足した → 固定版が制約を
満たさなくなる）。D-053 の実際の修正 `huggingface-hub>=1.5.0` を書いて `uv lock` を
忘れた場合がこれに当たる。

⚠️⚠️ **D-053 が踏んだ罠そのものは捕まえられない。**
「上限を `<1.0` → `<2.0` に緩めるだけでは lock は 1 mm も動かない」
（[M-111](../docs/measurements.md#m-111) §1）という形は、**緩めた後も
固定版 0.36.2 は `<2.0` を満たす**ので、宣言と lock は食い違っていない。
**制約を緩めることは不整合ではなく「効かない」だけ**なので、この形の検出には
lock を実際に解決し直す（`uv lock`）しかなく、それは piper-plus の絶対パスが要る
CI では原理的にできない。**このゲートを「依存の変更は守られている」と読まないこと。**

⚠️ **見ていないもの**:
- **lock が実際に解決するか**（`uv lock --check`）。`[tool.uv.sources]` が
  piper-plus を**絶対パス**で指すので、そのクローンが無い CI では原理的に測れない
- **推移的依存の整合**（lock が持つ requires-dist の充足）。見ているのは
  `pyproject.toml` が直接名指しした要件だけ
- **環境マーカー**。今は 1 件も無く、**出てきたら未対応として落とす**（黙って飛ばさない）
- **インストールできるか / 動くか**。版の文字列しか見ていない

依存ゼロ（tomllib は 3.11+ の標準ライブラリ）。ネットワーク不要。

    uv run --no-project --python 3.12 python scripts/check_lock_vs_pyproject.py
    uv run --no-project --python 3.12 python scripts/check_lock_vs_pyproject.py --self-test
"""

from __future__ import annotations

import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# PEP 503 の正規化（`Foo_Bar.baz` と `foo-bar-baz` を同じ名前とみなす）
_NORM = re.compile(r"[-_.]+")

# `name[extra1,extra2] >=1.0, <2.0` — マーカー（`;`）は**わざと受け付けない**
_REQ = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"(?P<spec>[^;]*)$"
)

# `>=1.5.0` / `<2.0` — ワイルドカードや `~=` `===` は**未対応として落とす**
_SPEC = re.compile(r"^(?P<op>==|!=|<=|>=|<|>)\s*(?P<ver>[^,\s]+)$")

# `1.30.0` / `0.0.1.1` / `5.0.0rc3` / `1.2.3.post1` / `1.2.3.dev0` / `2.13.0+cpu`
# ⚠️ ローカル版（`+cpu`）は**順序比較では無視する**（PEP 440 の `>=` / `<` と同じ）。
#    torch を PyTorch の index から引くと実際に `+cpu` が付くので、
#    これを未対応にすると偽陽性で CI が止まる。
_VER = re.compile(
    r"^(?P<rel>\d+(?:\.\d+)*)"
    r"(?:(?P<pre>a|b|rc)(?P<pren>\d+))?"
    r"(?:\.(?P<post>post|dev)(?P<postn>\d+))?"
    r"(?:\+(?P<local>[A-Za-z0-9.]+))?$"
)

# dev < a < b < rc < 通常リリース < post
_STAGE = {"dev": -2, "a": -1, "b": 0, "rc": 1, None: 2, "post": 3}


class Unsupported(Exception):
    """黙って飛ばさないための例外。未対応の形は NG にする。"""


def normalize(name: str) -> str:
    return _NORM.sub("-", name).lower()


def parse_version(v: str) -> tuple:
    m = _VER.match(v.strip())
    if not m:
        raise Unsupported(f"版の形が未対応: {v!r}")
    rel = tuple(int(x) for x in m.group("rel").split("."))
    if m.group("pre"):
        stage, num = _STAGE[m.group("pre")], int(m.group("pren"))
    elif m.group("post"):
        stage, num = _STAGE[m.group("post")], int(m.group("postn"))
    else:
        stage, num = _STAGE[None], 0
    return (rel, stage, num)


def _cmp_key(a: tuple, b: tuple) -> tuple[tuple, tuple]:
    """release タプルの長さを揃える（`1.2` と `1.2.0` を同値にする）。"""
    ra, rb = a[0], b[0]
    n = max(len(ra), len(rb))
    ra = ra + (0,) * (n - len(ra))
    rb = rb + (0,) * (n - len(rb))
    return (ra,) + a[1:], (rb,) + b[1:]


def satisfies(locked: str, op: str, bound: str) -> bool:
    a, b = _cmp_key(parse_version(locked), parse_version(bound))
    if op == ">=":
        return a >= b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    if op == "<":
        return a < b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    raise Unsupported(f"演算子が未対応: {op!r}")


def parse_requirement(raw: str) -> tuple[str, list[tuple[str, str]]]:
    if ";" in raw:
        raise Unsupported(
            f"環境マーカーは未対応: {raw!r} — マーカーを評価するようにゲートを"
            f"拡張してから足すこと（黙って飛ばすと lock のずれを見逃す）"
        )
    m = _REQ.match(raw)
    if not m:
        raise Unsupported(f"要件の形が未対応: {raw!r}")
    name = normalize(m.group("name"))
    spec_raw = (m.group("spec") or "").strip()
    if not spec_raw:
        return name, []
    if "*" in spec_raw:
        raise Unsupported(f"ワイルドカードは未対応: {raw!r}")
    specs = []
    for part in spec_raw.split(","):
        part = part.strip()
        if not part:
            continue
        sm = _SPEC.match(part)
        if not sm:
            raise Unsupported(f"指定子が未対応: {part!r}（要件 {raw!r}）")
        specs.append((sm.group("op"), sm.group("ver")))
    return name, specs


def collect_requirements(pyproject: dict) -> list[str]:
    proj = pyproject.get("project", {})
    reqs = list(proj.get("dependencies", []))
    for group in proj.get("optional-dependencies", {}).values():
        reqs.extend(group)
    return reqs


def collect_locked(lock: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for pkg in lock.get("package", []):
        name, ver = pkg.get("name"), pkg.get("version")
        if name is None or ver is None:
            continue
        out[normalize(name)] = ver
    return out


def run_check(pyproject: dict, lock: dict) -> tuple[list[str], int, int]:
    """→ (NG のリスト, 検査した指定子の数, 検査した要件の数)"""
    ngs: list[str] = []
    locked = collect_locked(lock)
    reqs = collect_requirements(pyproject)
    n_spec = 0
    n_req = 0

    for raw in reqs:
        try:
            name, specs = parse_requirement(raw)
        except Unsupported as e:
            ngs.append(f"未対応の書式: {e}")
            continue
        n_req += 1
        if name not in locked:
            ngs.append(
                f"{name}: pyproject が要求しているのに uv.lock に無い"
                f"（`uv lock` を回していない）"
            )
            continue
        for op, bound in specs:
            try:
                ok = satisfies(locked[name], op, bound)
            except Unsupported as e:
                ngs.append(f"{name}: {e}")
                continue
            n_spec += 1
            if not ok:
                ngs.append(
                    f"{name}: pyproject は `{op}{bound}` を要求しているが "
                    f"uv.lock は {locked[name]} で固定されている"
                    f"（制約を書き換えて `uv lock` を回していない）"
                )

    # ⚠️ 空虚防止。要件 0 件で「OK」を出すと、パスを間違えても緑になる
    if n_req == 0:
        ngs.append("検査した要件が 0 件（pyproject を読めていない = ゲートが効いていない）")
    if not locked:
        ngs.append("uv.lock から package を 1 件も読めていない")
    return ngs, n_spec, n_req


def load_real() -> tuple[dict, dict]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return pyproject, lock


# --------------------------------------------------------------- 陽性対照
# ⚠️ **「N 件以上落ちた」では書かない**（writing-gates §11）。
#    壊し方ごとに、期待する NG の断片と 1:1 で対応させる。


def _deepcopy(d):
    import copy

    return copy.deepcopy(d)


def _mut_bound_raised(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """lock より高い下限を pyproject に書く。

    ⚠️ **D-053 が踏んだ罠（上限を緩める）ではない。** D-053 の実際の**修正**
    （`huggingface-hub>=1.5.0` に下限を上げる）を書いて `uv lock` を忘れた形。

    ⚠️ 対象を固定名で選ばない。**最初に `>=` を持つ要件**を選び、
    どれを使ったかを出力する（対象が消えたら気づけるように）。
    """
    py = _deepcopy(py)
    deps = py["project"]["dependencies"]
    for i, raw in enumerate(deps):
        name, specs = parse_requirement(raw)
        if any(op == ">=" for op, _ in specs):
            locked = collect_locked(lk)[name]
            bumped = parse_version(locked)[0][0] + 1
            deps[i] = f"{name}>={bumped}.0"
            return py, lk, f"{name} を >={bumped}.0 にした（lock は {locked}）"
    raise AssertionError("`>=` を持つ要件が 1 件も無い = 対照が対象に効いていない")


def _mut_missing(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """lock からパッケージを 1 件消す。"""
    py = _deepcopy(py)
    lk = _deepcopy(lk)
    name, _ = parse_requirement(py["project"]["dependencies"][0])
    before = len(lk["package"])
    lk["package"] = [p for p in lk["package"] if normalize(p.get("name", "")) != name]
    assert len(lk["package"]) == before - 1, f"{name} が lock に無かった = 対照が効いていない"
    return py, lk, f"{name} を lock から消した"


def _mut_unsupported_spec(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """`~=` は未対応。**黙って飛ばさず落ちる**ことを要求する。"""
    py = _deepcopy(py)
    py["project"]["dependencies"].append("torch~=2.11")
    return py, lk, "`torch~=2.11` を足した"


def _mut_marker(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """環境マーカーも未対応。同上。"""
    py = _deepcopy(py)
    py["project"]["dependencies"].append('numpy>=1.0; python_version < "3.12"')
    return py, lk, "マーカー付きの要件を足した"


def _mut_unsupported_version(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """lock 側の版が読めない形。

    ⚠️ **`2.13.0+cpu` を使わない。** ローカル版は正当なので対応させてあり、
    対照にすると落ちない（= 陽性対照ではなくなる）。
    """
    py = _deepcopy(py)
    lk = _deepcopy(lk)
    name, _ = parse_requirement(py["project"]["dependencies"][1])
    for p in lk["package"]:
        if normalize(p.get("name", "")) == name:
            p["version"] = "nightly-20260910"
            break
    else:
        raise AssertionError(f"{name} が lock に無い = 対照が効いていない")
    return py, lk, f"{name} の lock 版を `nightly-20260910` にした"


def _mut_empty(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """要件 0 件で「OK」を出さないこと。"""
    py = _deepcopy(py)
    py["project"]["dependencies"] = []
    py["project"]["optional-dependencies"] = {}
    return py, lk, "要件を全部消した"


CONTROLS = [
    ("bound-raised", _mut_bound_raised, "uv.lock は"),
    ("missing-pkg", _mut_missing, "uv.lock に無い"),
    ("unsupported-spec", _mut_unsupported_spec, "指定子が未対応"),
    ("marker", _mut_marker, "環境マーカーは未対応"),
    ("unsupported-version", _mut_unsupported_version, "版の形が未対応"),
    ("empty", _mut_empty, "検査した要件が 0 件"),
]


def _neg_local_version(py: dict, lk: dict) -> tuple[dict, dict, str]:
    """ローカル版（`+cpu`）を**巻き込まない**こと。

    ⚠️ 厳しくする修正が別の破壊を生む（C-028）。`+cpu` は PyTorch の index から
    引くと実際に付くので、これで落ちてはいけない。
    """
    lk = _deepcopy(lk)
    name, _ = parse_requirement(py["project"]["dependencies"][1])
    for p in lk["package"]:
        if normalize(p.get("name", "")) == name:
            p["version"] = p["version"] + "+cpu"
            break
    else:
        raise AssertionError(f"{name} が lock に無い = 対照が効いていない")
    return py, lk, f"{name} の lock 版に `+cpu` を付けた"


def self_test() -> int:
    py, lk = load_real()

    # 陰性対照。**「正しいものが通る」ケースを必ず 1 本入れる**（writing-gates §15）
    ngs, n_spec, n_req = run_check(py, lk)
    if ngs:
        print("NG! 陰性対照: 素の pyproject / uv.lock が落ちた")
        for x in ngs:
            print(f"     {x}")
        return 1
    print(f"  OK  陰性対照: 素の pyproject / uv.lock は通る（要件 {n_req} 件 / 指定子 {n_spec} 件）")

    py2, lk2, what = _neg_local_version(py, lk)
    ngs, _, _ = run_check(py2, lk2)
    if ngs:
        print(f"NG! 陰性対照 local-version: {what} → 落ちてはいけないのに落ちた: {ngs}")
        return 1
    print(f"  OK  陰性対照 local-version    {what} → 巻き込まない")

    bad = 0
    for label, mut, expect in CONTROLS:
        py2, lk2, what = mut(py, lk)
        ngs, _, _ = run_check(py2, lk2)
        hit = [x for x in ngs if expect in x]
        if not hit:
            print(f"NG! 陽性対照 {label}: {what} → 期待した NG「{expect}」が出なかった")
            print(f"     出た NG: {ngs or '（無し）'}")
            bad += 1
        else:
            print(f"  OK  陽性対照 {label:19s} {what}")
            print(f"       → {hit[0]}")
    if bad:
        print(f"NG! 陽性対照 {len(CONTROLS) - bad}/{len(CONTROLS)} 件しか落ちなかった")
        return 1
    print(f"\nOK: 陽性対照 {len(CONTROLS)}/{len(CONTROLS)} 件すべてがゲートを落とした")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    py, lk = load_real()
    ngs, n_spec, n_req = run_check(py, lk)
    locked = collect_locked(lk)
    print(f"pyproject の要件 {n_req} 件 / 指定子 {n_spec} 件 を uv.lock の {len(locked)} package と照合")
    if ngs:
        for x in ngs:
            print(f"NG! {x}")
        print("\n⚠️ `uv lock` を回して uv.lock を作り直すこと。")
        return 1
    print("OK  pyproject の制約はすべて uv.lock の固定版が満たしている")
    print("⚠️ 見ていないもの:")
    print("   - **制約を緩めただけの変更**（`<1.0` → `<2.0`）。緩めても固定版は制約を満たすので")
    print("     不整合にならない。D-053 が踏んだ罠はこの形で、**このゲートでは捕まらない**")
    print("   - lock が実際に解決するか（piper-plus の絶対パスが要るので CI では測れない）")
    print("   - 推移的依存の整合 / インストールできるか / 動くか")
    return 0


if __name__ == "__main__":
    sys.exit(main())
