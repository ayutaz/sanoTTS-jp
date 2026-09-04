#!/usr/bin/env python3
"""Dependabot が名指しした**脆弱 API が、このリポジトリの実経路で呼ばれないこと**を測る。

## なぜ要るのか

`uv.lock` 由来の Dependabot アラート 5 件（transformers 4 件 / nltk 1 件）を調べた結果、
**advisory が名指しした関数はどれも呼ばれていない**ことが分かった（D-053 / M-105）。
⚠️ **この事実は書いた瞬間から腐る。** nltk も piper-plus も上流で動くので、
経路が変われば散文の「呼ばれない」は黙って嘘になる。だからゲートにする。

⚠️ **このゲートは「パッケージが安全」を主張しない。** 主張はただ一つ:

> 下の 10 個の関数は、`text_to_phoneme_ids_and_prosody` の実経路で **1 度も呼ばれない**。

`nltk.pathsec.ENFORCE` は出荷既定値が `True` だが、それを根拠にはしていない
（`AveragedPerceptron.load('/etc/hosts')` は ENFORCE=True でも止まらない = advisory は正しい）。
言えるのは「**パッケージは動くが脆弱関数は呼ばない**」だけ。

## 何を見ているか

| 節 | 何 |
|---|---|
| G1 | 10 sink + 陰性対照 1 個が**実物に存在し、ラップできた**か（上流のリネームで落ちる） |
| G2 | **陽性対照（nltk）**: `AveragedPerceptron.save` / `.load` を故意に呼ぶと 2 件観測できる |
| G3 | **陽性対照（transformers）**: 4 sink を故意に呼ぶと 4 件観測できる |
| G4 | 実経路を通す（実コーパスのラテン文字行 9 文 + 純日本語 1 文） |
| G5a | 実経路を通した**後**もラッパが全部生きているか（途中で剥がれたら 0 件は空虚） |
| G5 | **10 sink の発火が 0 件** |
| G6 | `PerceptronTagger.load_from_json`（advisory 自身が挙げるガード済み関数）が実経路で **≥ 1 件** |
| G7 | **対照**: 純日本語だけを新規プロセスで通すと `load_from_json` も **0 件** |

**G6 と G7 が対になっている。** G6 が 0 になったら「安全になった」ではなく
**経路が変わった**合図（ラテン文字が英語経路に入らなくなった / g2p_en が外れた）で、
そのとき G5 の「0 件」は何も測っていない。G7 は「カウンタが常時 0 を返す壊れ方」を潰す。

## 陽性対照 — **実行して落ちるのを見たものだけ**を書いてある

`--self-test` が 5 つの壊し方を**子プロセスで実際に走らせ、exit≠0 と期待した NG 行**を確かめる。
G2/G3 がプロセス内の陽性対照（ラッパが数えること）なのに対し、こちらは
**ゲート自身が落ちること**の対照。実測（**5/5 が落ちた**。下の表の 5 行と
`INJECTIONS` の 5 件と `--self-test` の出力「5/5」は同じ数でなければならない）:

| `--inject` | 何を壊すか | 落ちる節 |
|---|---|---|
| `missing` | sink の 1 つを存在しない名前にする（= 上流のリネーム） | G1 WRAP-MISSING |
| `dead-counter` | ラッパが increment しない（= 検出器の死） | G2 |
| `route` | `load_from_json` が `AveragedPerceptron.load` を呼ぶ（= 上流が経路を変えた） | G5 |
| `ja-route` | 実経路に純日本語しか流さない（= ラテン文字が英語経路に入らない） | G6 |
| `unwrap` | 実経路の直前に sink の属性を素に戻す（= 以降ラッパを通らない） | G5a |

⚠️ `route` は `functools.wraps` でラッパの `_cve_gate` まで写すので **G1b を素通りする**。
「ラッパは生きているが sink が呼ばれた」という本物の壊れ方でしか G5 は落ちない。

## 見ていないもの

- **この 10 個以外の関数**。新しい CVE が別の関数を名指ししたら、ここに足さないと見えない
- **他の呼び出し口**。同じ英語経路に入りうる呼び出しは、このゲート自身を除いて
  **7 ファイル 10 箇所**ある（2026-09-04 に下のコマンドで数えた。10 箇所すべてが
  `language_id_map=lim`（6 言語）を渡す = ラテン文字を含む文が `g2p_en` →
  nltk の POS tagger に入る形）。**このゲートが通すのは自分自身の 1 経路だけ。**
  ⚠️ **行番号を書かない**（依存や引数を 1 行足すたびにずれる。C-042 と同じ形）:

      grep -rn '= text_to_phoneme_ids_and_prosody(' scripts/ | grep -v test_cve_reach
      # → 10 行。`grep -rln ... | grep -v test_cve_reach | wc -l` で 7 ファイル
- **transformers 側の実経路**。transformers は
  `piper_train.export_onnx` → pytorch_lightning → `torchmetrics/functional/text/bert.py` の
  トップレベル import と、`faster_whisper` → ctranslate2 経由で **import されるだけ**。
  G3 はラッパが刺さっていることを示すだけで、`from_pretrained` が実際の呼び出しで
  横取りされる様子を端から端まで通したわけではない（引数が不正なので即例外になる）
- **バージョンが上がった後も同じか**。これは「今の環境で呼ばれない」を測るだけ

## 前提（⚠️ CI の docs job では回せない）

- `~/Documents/piper-plus`（`PIPER_PLUS_ROOT` で差し替え可）
- 教師 ckpt の snapshot にある `config.json`（`phoneme_id_map`。⚠️ ckpt 本体は読まない）
- `nltk_data` の `averaged_perceptron_tagger_eng`（g2p_en が使う。**キャッシュ済みでないと落ちる**）
- `data/splits/corpus_embedded.tsv`。⚠️ **これは git tracked**（184 行・本文つき。
  `git ls-files -s data/splits/corpus_embedded.tsv` が blob を返す）。
  かつてここに「git 管理外」と書いていたが**誤り**で、**CI で回せない理由にはならない**。
  回せない理由は上の 3 つで、なかでも教師 snapshot が決定的（private repo 由来）

**ネットワークは塞いだ状態で回す**（`socket.connect` / `create_connection` /
`getaddrinfo` を潰す）。塞いだまま通ることを実測した（試行 0 件）。
⚠️ **`g2p_en` は import 時に `nltk.download` を呼ぶ**ので、`nltk_data` が未取得の環境では
落ちる。**だから CI の docs job（依存ゼロ・ネットワーク無しが売り）には入れていない**
（`scripts/check_ci_coverage.py` の `EXCLUDED_SCRIPTS` に理由つきで登録してある）。

    uv run python scripts/test_cve_reach.py               # 本体
    uv run python scripts/test_cve_reach.py --self-test   # 陽性対照 5 件

⚠️ **所要時間は書かない。** かつて「壁時計 / n=2 で本体 5.4〜6.1 s / `--self-test`
   21.4〜21.8 s」と書いたが、別の測り手が同じ起動形で測ると 6.5〜9.2 s / 26.8〜29.1 s
   （n=3）で範囲外だった。あの値は **user CPU 時間**にほぼ一致しており、壁時計だと
   書いたのが誤り。このホストは他プロセスとの競合で 1.5 倍ずれるので、
   **負荷条件を書けない時間は残さない**。`--self-test` が本体を子プロセスで 5 回
   回すぶん本体より長い、という関係だけが言える。
"""

from __future__ import annotations

import argparse
import functools
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIPER_PLUS = os.environ.get(
    "PIPER_PLUS_ROOT", os.path.expanduser("~/Documents/piper-plus")
)
SNAP_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/"
    "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
)
CORPUS = ROOT / "data" / "splits" / "corpus_embedded.tsv"

#: 6 言語。`language="ja"` でもこれを渡すと multilingual に auto-promote され、
#: ラテン文字を含む文が英語経路（g2p_en → nltk の POS tagger）に入る。
LANGS = ["ja", "en", "zh", "es", "fr", "pt"]

#: 純日本語の対照文（G4 の中の陰性側 / G7 の子プロセス）
JA_ONLY = ["今日は良い天気ですね。", "きょうはいいてんきですね。"]

# --- advisory が名指しした sink ------------------------------------------------
#
# nltk  CVE-2026-81726 (#5, high, nltk <= 3.10.3。修正版のリリースが存在しない)
# tfm   CVE-2026-1839 (#1) / CVE-2026-4372 (#2) / CVE-2026-5241 (#3) / CVE-2026-9856 (#4)
#
# ⚠️ **見つからなければ NG。** 上流でリネームされたら「ラップし忘れ」ではなく
#    「advisory が指す関数が消えた or 名前が変わった」なので、黙って skip してはいけない。
NLTK_SINKS = [
    ("nltk.parse.transitionparser", "TransitionParser", "train"),
    ("nltk.parse.transitionparser", "TransitionParser", "parse"),
    ("nltk.tag.perceptron", "AveragedPerceptron", "save"),
    ("nltk.tag.perceptron", "AveragedPerceptron", "load"),
    ("nltk.tag.perceptron", "PerceptronTagger", "save_to_json"),
    ("nltk.classify.maxent", None, "save_maxent_params"),   # モジュール直下の関数
]
TFM_SINKS = [
    ("transformers", "PreTrainedModel", "from_pretrained"),
    ("transformers", "PreTrainedModel", "save_pretrained"),
    # ⚠️ **`PretrainedConfig` は旧名で、transformers 5.16.1 では `PreTrainedConfig` の
    #    エイリアス**（同一オブジェクト。実測: `PretrainedConfig is PreTrainedConfig` → True。
    #    G1 の出力も `transformers.configuration_utils.PreTrainedConfig.from_pretrained` と出る）。
    #
    #      uv run --no-project --python .venv/bin/python python -c \
    #        "from transformers import PretrainedConfig as A, PreTrainedConfig as B; print(A is B)"
    #
    #    **旧名のまま置いてあるのは advisory の文面と 1 対 1 で突き合わせるため。**
    #    上流がエイリアスを落としたら G1 が WRAP-MISSING を出すが、それは
    #    「advisory の関数が消えた」ではなく**エイリアスが消えただけ**なので、
    #    正しい対応は `PreTrainedConfig` に書き換えること（ゲートを緩めない）。
    ("transformers", "PretrainedConfig", "from_pretrained"),
    ("transformers", "Trainer", "__init__"),
]
#: advisory 自身が「pathsec でガード済み」の陰性対照として挙げている関数。
#: **実経路で発火する**ので、これが 0 になったら経路が変わった合図（G6）。
GUARDED = ("nltk.tag.perceptron", "PerceptronTagger", "load_from_json")

SINK_LABELS = [f"{c or m.rsplit('.', 1)[-1]}.{a}" for m, c, a in NLTK_SINKS + TFM_SINKS]
GUARDED_LABEL = f"{GUARDED[1]}.{GUARDED[2]}"

FIRED: dict[str, int] = {}
NET_ATTEMPTS: list[str] = []
FAILED: list[str] = []

#: `--inject` で入れる故意の壊し方。`--self-test` が**全部を実際に走らせて落ちるのを見る**。
#: ⚠️ ここに書いてあるのは**実行して落ちるのを確認した**ものだけ（writing-gates の作法）。
INJECTIONS = {
    # 上流が sink をリネームした → G1 が WRAP-MISSING で止まる
    "missing": ("G1 WRAP-MISSING", "sink の 1 つを存在しない名前にする"),
    # ラッパが数えなくなった（= 検出器の死）→ G2 の陽性対照が落ちる
    "dead-counter": ("G2 陽性対照(nltk) が効いていない", "カウンタを increment しない"),
    # 上流が実経路から脆弱 sink を呼ぶようになった → G5 が捕まえる
    "route": ("G5 脆弱 sink が発火した", "load_from_json が AveragedPerceptron.load を呼ぶ"),
    # ラテン文字が英語経路に入らなくなった（= G5 の 0 件が空虚になる）→ G6 が捕まえる
    "ja-route": ("G6 PerceptronTagger.load_from_json が 0 件", "実経路に純日本語しか流さない"),
    # 実経路の途中で sink の属性が差し替わった（= 以降ラッパを通らない）→ G5a が捕まえる
    "unwrap": ("G5a 実経路の途中でラッパが外れた", "実経路の直前に sink の属性を素に戻す"),
}
INJECT: str | None = None
COUNT_ENABLED = True


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def ng(msg: str) -> None:
    FAILED.append(msg)
    print(f"  NG  {msg}")


def _bump(label: str) -> None:
    if COUNT_ENABLED:                       # `--inject dead-counter` で False になる
        FIRED[label] += 1


# --- ラッパ --------------------------------------------------------------------
def _install(mod_name: str, cls_name: str | None, attr: str, label: str) -> str | None:
    """sink をラップする。戻り値は「どこに刺したか」の説明、失敗なら None。

    ⚠️ **カウントは呼び出しの入口で行う**（例外で落ちる呼び出しも 1 件と数える）。
    「呼ばれたか」を測りたいのであって「成功したか」ではない。
    """
    import importlib

    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:                                    # noqa: BLE001
        print(f"       import {mod_name} に失敗: {type(e).__name__}: {e}")
        return None

    FIRED[label] = 0

    if cls_name is None:                       # モジュール直下の関数
        raw = getattr(mod, attr, None)
        if raw is None or not callable(raw):
            return None

        @functools.wraps(raw)
        def wm(*a, **kw):
            _bump(label)
            return raw(*a, **kw)

        wm._cve_gate = label                                  # noqa: SLF001
        setattr(mod, attr, wm)
        return f"{mod_name}.{attr}"

    cls = getattr(mod, cls_name, None)
    if not isinstance(cls, type):
        return None
    owner = raw = None
    for base in cls.__mro__:                   # 定義しているクラスに刺す
        if attr in base.__dict__:
            owner, raw = base, base.__dict__[attr]
            break
    if owner is None:
        return None

    if isinstance(raw, classmethod):
        fn = raw.__func__

        @functools.wraps(fn)
        def wc(cls2, *a, **kw):
            _bump(label)
            return fn(cls2, *a, **kw)

        wc._cve_gate = label                                  # noqa: SLF001
        setattr(owner, attr, classmethod(wc))
    elif callable(raw):

        @functools.wraps(raw)
        def wf(*a, **kw):
            _bump(label)
            return raw(*a, **kw)

        wf._cve_gate = label                                  # noqa: SLF001
        setattr(owner, attr, wf)
    else:
        return None
    return f"{owner.__module__}.{owner.__name__}.{attr}"


def _is_wrapped(mod_name: str, cls_name: str | None, attr: str) -> bool:
    """ラッパが**今も**そこに居るか（後から import した別モジュールに上書きされていないか）。"""
    import importlib

    obj = importlib.import_module(mod_name)
    if cls_name is not None:
        obj = getattr(obj, cls_name)
    f = getattr(obj, attr, None)
    if isinstance(f, classmethod):
        f = f.__func__
    f = getattr(f, "__func__", f)
    return getattr(f, "_cve_gate", None) is not None


def install_all() -> None:
    """全 sink + 陰性対照をラップし、G1 として報告する。**1 つでも欠けたら NG。**"""
    for mod, cls, attr in NLTK_SINKS + TFM_SINKS + [GUARDED]:
        label = f"{cls or mod.rsplit('.', 1)[-1]}.{attr}"
        where = _install(mod, cls, attr, label)
        if where is None:
            ng(f"G1 WRAP-MISSING {label:<38} 実物に見つからない（上流でリネーム？）")
        else:
            ok(f"G1 wrap {label:<43} → {where}")


def block_network() -> None:
    """ネットワークを塞ぐ。**試行があったら記録して報告する**（塞いだことを黙らない）。"""
    import socket

    def deny(name):
        def f(*a, **kw):
            NET_ATTEMPTS.append(f"{name}{a[:1]}")
            raise OSError(f"network blocked by test_cve_reach ({name})")

        return f

    socket.socket.connect = deny("socket.connect")             # type: ignore[method-assign]
    socket.socket.connect_ex = deny("socket.connect_ex")       # type: ignore[method-assign]
    socket.create_connection = deny("create_connection")       # type: ignore[assignment]
    socket.getaddrinfo = deny("getaddrinfo")                   # type: ignore[assignment]


def reset() -> None:
    for k in FIRED:
        FIRED[k] = 0


def fired_sinks() -> dict[str, int]:
    return {k: v for k, v in FIRED.items() if k in SINK_LABELS and v}


# --- 実経路 --------------------------------------------------------------------
def latin_texts() -> list[str]:
    """実コーパスからラテン文字を含む行を取る。**無ければ NG**（黙って空で通さない）。"""
    if not CORPUS.exists():
        return []
    rows = [
        l.split("\t") for l in CORPUS.read_text().splitlines()[1:] if l.strip()
    ]
    return [r[2] for r in rows if len(r) >= 3 and re.search(r"[A-Za-z]", r[2])]


def phoneme_id_map() -> dict:
    hits = glob.glob(SNAP_GLOB)
    if not hits:
        raise FileNotFoundError(
            f"教師 snapshot が無い: {SNAP_GLOB}（config.json だけ要る。ckpt は読まない）"
        )
    return json.load(open(hits[0] + "config.json"))["phoneme_id_map"]


def run_path(texts: list[str], pim: dict) -> list[tuple[str, int]]:
    """multilingual auto-promote 経路を通す。`language="ja"` + 6 言語の `language_id_map`。

    呼び方は `scripts/phase0_verify_teacher.py` / `scripts/b5_teacher_baseline.py` と同じ。
    """
    sys.path.insert(0, f"{PIPER_PLUS}/src/python")
    from piper_train.infer_onnx import (          # noqa: PLC0415
        text_to_phoneme_ids_and_prosody,
    )

    lim = {c: i for i, c in enumerate(LANGS)}
    out = []
    for t in texts:
        ids, _ = text_to_phoneme_ids_and_prosody(
            t, pim, language="ja", language_id_map=lim
        )
        out.append((t, len(ids)))
    return out


# --- 子プロセス（G7 の対照） ---------------------------------------------------
def child_japanese_only() -> int:
    """純日本語だけを**新規プロセス**で通し、`load_from_json` の件数を stdout に出す。

    ⚠️ **別プロセスでなければ測れない。** g2p_en の tagger はプロセス内で 1 度しか
    load されない（`load_from_json` は 2 文目以降で発火しない）ので、同じプロセスで
    順番を変えても「純日本語では発火しない」ことは示せない。
    """
    _install(*GUARDED, GUARDED_LABEL)
    block_network()
    run_path(JA_ONLY, phoneme_id_map())
    print(f"__GUARDED__={FIRED[GUARDED_LABEL]}")
    return 0


# --- 陽性対照 ------------------------------------------------------------------
def positive_nltk() -> None:
    """`AveragedPerceptron.save` / `.load` を故意に呼んで **2 件**観測する。

    ⚠️ これが無いと G5 の「0 件」が「安全」なのか「検出器が死んでいる」のか区別できない。
    """
    import nltk.tag.perceptron as pc                            # noqa: PLC0415

    reset()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ap.json")
        ap = pc.AveragedPerceptron()
        ap.save(p)
        pc.AveragedPerceptron().load(p)
    got = FIRED["AveragedPerceptron.save"] + FIRED["AveragedPerceptron.load"]
    if got == 2 and not (set(fired_sinks()) - {
        "AveragedPerceptron.save", "AveragedPerceptron.load"
    }):
        ok(f"G2 陽性対照(nltk): save/load を呼ぶと {got} 件観測（他 sink への漏れ無し）")
    else:
        ng(f"G2 陽性対照(nltk) が効いていない: {got} 件 / 発火 {fired_sinks()}")
    reset()


def positive_transformers() -> None:
    """transformers の 4 sink を故意に呼んで **4 件**観測する。

    ⚠️ 引数は意図的に不正（存在しないローカルパス / self=None）。ラッパは**入口で数える**
    ので例外でも 1 件になる。**示せるのは「その属性を呼ぶとラッパを通る」ことだけ**で、
    実際の from_pretrained を端から端まで横取りしたわけではない。
    """
    from transformers import (                                  # noqa: PLC0415
        PretrainedConfig,
        PreTrainedModel,
        Trainer,
    )

    reset()
    nowhere = "/nonexistent/saanotts-jp-cve-gate"
    for call in (
        lambda: PreTrainedModel.from_pretrained(nowhere),
        lambda: PreTrainedModel.save_pretrained(None, nowhere),
        lambda: PretrainedConfig.from_pretrained(nowhere),
        lambda: Trainer.__init__(None),
    ):
        try:
            call()
        except BaseException:                                   # noqa: BLE001
            pass
    labels = [f"{c}.{a}" for _, c, a in TFM_SINKS]
    got = sum(FIRED[x] for x in labels)
    if got == 4 and not (set(fired_sinks()) - set(labels)):
        ok(f"G3 陽性対照(transformers): 4 sink を呼ぶと {got} 件観測（他 sink への漏れ無し）")
    else:
        ng(f"G3 陽性対照(transformers) が効いていない: {got} 件 / 発火 {fired_sinks()}")
    reset()


def inject_route() -> None:
    """**実経路が脆弱 sink を呼ぶようになった**上流変更を模す（`--inject route`）。

    `load_from_json` の中から `AveragedPerceptron.load` を呼ぶ。⚠️ `functools.wraps` が
    ラッパの `__dict__`（`_cve_gate`）まで写すので **G1b は騙されたまま G5 が捕まえる**
    = 「ラッパは生きている / でも sink が呼ばれた」という本物の壊れ方になる。
    """
    import nltk.tag.perceptron as pc                            # noqa: PLC0415

    orig = pc.PerceptronTagger.load_from_json

    @functools.wraps(orig)
    def hacked(self, *a, **kw):
        try:
            pc.AveragedPerceptron().load("/nonexistent/saanotts-jp-cve-gate.json")
        except BaseException:                                   # noqa: BLE001
            pass
        return orig(self, *a, **kw)

    pc.PerceptronTagger.load_from_json = hacked                 # type: ignore[method-assign]


def self_test() -> int:
    """**故意に壊して落ちるのを見る。** 落ちなければ、その節は printf にすぎない。"""
    print(f"陽性対照 {len(INJECTIONS)} 件を子プロセスで実行する（各々が落ちること）\n")
    bad = 0
    for name, (marker, what) in INJECTIONS.items():
        r = subprocess.run(
            [sys.executable, __file__, "--inject", name],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        hit = marker in r.stdout
        good = r.returncode != 0 and hit
        print(f"  {'OK ' if good else 'NG '} --inject {name:<14} {what}")
        print(f"       → exit {r.returncode} / 期待した NG「{marker}」"
              f"{'を出した' if hit else 'が出なかった'}")
        if not good:
            bad += 1
            print(f"       {r.stdout[-600:]}")
    print()
    if bad:
        print(f"NG: 陽性対照 {bad}/{len(INJECTIONS)} 件が落ちなかった = その節は空虚")
        return 1
    print(f"OK: 陽性対照 {len(INJECTIONS)}/{len(INJECTIONS)} 件すべてがゲートを落とした")
    return 0


def main() -> int:
    global INJECT, COUNT_ENABLED

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--child-japanese-only", action="store_true",
        help="内部用（G7 の子プロセス）",
    )
    ap.add_argument(
        "--self-test", action="store_true",
        help="故意に壊して、このゲートが落ちるのを見る（陽性対照）",
    )
    ap.add_argument(
        "--inject", choices=sorted(INJECTIONS), default=None,
        help="内部用（--self-test が使う。1 つの壊し方を入れて回す）",
    )
    args = ap.parse_args()
    if args.child_japanese_only:
        return child_japanese_only()
    if args.self_test:
        return self_test()
    INJECT = args.inject
    if INJECT:
        print(f"⚠️ --inject {INJECT}: {INJECTIONS[INJECT][1]}（落ちるのが正しい）")
    if INJECT == "missing":
        NLTK_SINKS[4] = ("nltk.tag.perceptron", "PerceptronTagger",
                         "save_to_json_RENAMED_UPSTREAM")
    if INJECT == "dead-counter":
        COUNT_ENABLED = False

    import nltk                                                 # noqa: PLC0415

    print(f"nltk {nltk.__version__} / piper-plus {PIPER_PLUS}")

    # --- G1: ラップできたか（欠けたら NG） -----------------------------------
    install_all()
    import transformers                                         # noqa: PLC0415

    print(f"       transformers {transformers.__version__}")
    if FAILED:
        print("\nNG: sink をラップできていないので、以降の 0 件は意味を持たない")
        return 1

    block_network()

    # --- G2 / G3: 陽性対照を**先に**通す -------------------------------------
    positive_nltk()
    positive_transformers()

    # --- G1b: 陽性対照を通した後もラッパが生きているか -----------------------
    lost = [
        f"{c or m.rsplit('.', 1)[-1]}.{a}"
        for m, c, a in NLTK_SINKS + TFM_SINKS + [GUARDED]
        if not _is_wrapped(m, c, a)
    ]
    if lost:
        ng(f"G1b ラッパが外れた（誰かが上書きした）: {lost}")
    else:
        ok(f"G1b ラッパ {len(SINK_LABELS) + 1} 個が実経路の直前でも生きている")

    if INJECT == "route":
        inject_route()
    if INJECT == "unwrap":                   # ラッパを剥がす（G5 が空虚になる状況）
        import nltk.tag.perceptron as pc                        # noqa: PLC0415

        pc.AveragedPerceptron.load = (                          # type: ignore[method-assign]
            lambda self, path: None
        )

    # --- G4: 実経路を通す -----------------------------------------------------
    lat = latin_texts()
    if INJECT == "ja-route":                 # ラテン文字が英語経路に入らなくなった状況
        lat = JA_ONLY * 5
    if len(lat) < 3:
        ng(f"G4 実コーパスのラテン文字行が足りない（{len(lat)} 件）: {CORPUS}")
        print("\nNG: 実経路を通せないので 0 件は何も示さない")
        return 1
    try:
        pim = phoneme_id_map()
        rows = run_path(lat + JA_ONLY[:1], pim)
    except Exception as e:                                      # noqa: BLE001
        ng(f"G4 実経路が通らない: {type(e).__name__}: {e}")
        print("\nNG: 実経路を通せないので 0 件は何も示さない")
        return 1
    n_ids = sum(n for _, n in rows)
    if all(n > 0 for _, n in rows):
        ok(
            f"G4 実経路 {len(rows)} 文（ラテン {len(lat)} + 純日本語 1）を通した"
            f"／音素 ID 計 {n_ids} 個"
        )
    else:
        ng(f"G4 音素 ID が 0 個の文がある: {rows}")

    # --- G5a: 実経路を通した**後**もラッパが生きているか ---------------------
    # ⚠️ ここが無いと G5 の「0 件」が空虚になる。実経路は `piper_train` →
    #    `piper_plus_g2p` → `g2p_en` → nltk を初めて import するので、その途中で
    #    誰かが sink の属性を差し替えたら、以降の呼び出しはラッパを通らない。
    lost = [
        f"{c or m.rsplit('.', 1)[-1]}.{a}"
        for m, c, a in NLTK_SINKS + TFM_SINKS + [GUARDED]
        if not _is_wrapped(m, c, a)
    ]
    if lost:
        ng(f"G5a 実経路の途中でラッパが外れた（0 件は空虚）: {lost}")
    else:
        ok(f"G5a ラッパ {len(SINK_LABELS) + 1} 個が実経路の**後**も生きている")

    # --- G5: 10 sink は 0 件 --------------------------------------------------
    hit = fired_sinks()
    if not hit:
        ok(f"G5 脆弱 sink {len(SINK_LABELS)} 個の発火 0 件（nltk 6 + transformers 4）")
    else:
        ng(f"G5 脆弱 sink が発火した: {hit}")

    # --- G6: 陰性対照側は発火する（0 なら経路が変わった） --------------------
    g = FIRED[GUARDED_LABEL]
    if g >= 1:
        ok(
            f"G6 {GUARDED_LABEL} が実経路で {g} 件発火"
            "（= ラテン文字が英語経路に入っている証拠）"
        )
    else:
        ng(
            f"G6 {GUARDED_LABEL} が 0 件。**経路が変わった**ので G5 の 0 件は"
            "「呼ばれない」証拠にならない"
        )

    # --- G7: 対照 — 純日本語だけなら陰性対照も 0 件 -------------------------
    r = subprocess.run(
        [sys.executable, __file__, "--child-japanese-only"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    m = re.search(r"__GUARDED__=(\d+)", r.stdout)
    if m is None:
        ng(f"G7 子プロセスが値を返さない: exit {r.returncode} / {r.stderr[-300:]}")
    elif int(m.group(1)) == 0:
        ok(
            f"G7 対照: 純日本語 {len(JA_ONLY)} 文だけの新規プロセスでは "
            f"{GUARDED_LABEL} も 0 件（= カウンタは常時 ON ではない）"
        )
    else:
        ng(
            f"G7 対照が崩れた: 純日本語だけでも {GUARDED_LABEL} が "
            f"{m.group(1)} 件発火した"
        )

    if NET_ATTEMPTS:
        print(f"       ⚠️ ネットワーク試行 {len(NET_ATTEMPTS)} 件を塞いだ: "
              f"{NET_ATTEMPTS[:3]}")
    else:
        print("       ネットワーク試行 0 件（塞いだが誰も叩かなかった）")

    print()
    if FAILED:
        print(f"NG: CVE 到達性ゲートが {len(FAILED)} 件落ちた")
        return 1
    print(
        "OK: CVE 到達性ゲート — 脆弱 sink 10 個は実経路で 0 件"
        "（陽性対照 2 件 + 4 件 / 陰性対照 G6・G7 つき）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
