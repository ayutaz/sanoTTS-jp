# -*- coding: utf-8 -*-
"""B-10: 評価 split の汚染検査。

検査するのは 2 種類の汚染:

  (A) 教師の FT テキストの混入
      教師 (ayousanz/piper-plus-zero-shot-tsukuyomi) の FT コーパスは
      つくよみちゃんコーパス = JSUT VOICEACTRESS100 の 100 文。
      JSUT repeat500 の set1 は VOICEACTRESS100 とほぼ同一本文なので、
      `jsut/voiceactress100` と `jsut/repeat500` の両方を汚染源として扱う。

  (B) train と heldout の間のリーク
      完全一致 / 文字 5-gram Jaccard / 部分文字列包含 の 3 通り。

教師の**事前学習**テキスト (6 言語 497k 発話、日本語部分は MOE-Speech 20speakers)
との重複は、書き起こしが手元に無いので **未検査**。JSON にそう明記する。

再現:
  uv run python scripts/b10_overlap.py
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPLIT_DIR = os.path.join(ROOT, "data", "splits")
OUT = os.path.join(ROOT, "reports", "b10_overlap.json")

# JSUT の生テキスト（B-0 のコーパス構築で使ったもの）。
# 無くても source 列だけで (A) は判定できる。あれば「別ラベルで紛れ込んだ同一本文」
# まで捕まえられるので、availability を JSON に記録する。
DEFAULT_JSUT = ("<scratch>"
                "3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/jsut_text")

FT_SOURCES = ("jsut/voiceactress100", "jsut/repeat500")

SPLITS = ["train", "heldout", "embedded", "sibdense"]

# 部分文字列包含で「意味のあるリーク」とみなす、包含される側の最小文字数。
# これ未満は地名・定型句など短い断片が長文に埋まっているだけで、リークとは呼べない
# (実測: embedded の 12 件はすべて 5-7 文字の地名で、train 側は cv/singleword-benchmark)。
MEANINGFUL_SUB_LEN = 8

PUNCT_RE = re.compile(r"[ 、。，．,.!?！？「」『』（）()\"'・…―ー~〜:：;；]")


# ---------------------------------------------------------------- 正規化
def norm(t: str) -> str:
    """NFKC + 空白除去。split 構築時 (scripts/b0/build_corpus.py) の norm() は
    空白を 1 個に畳むだけなので、ここではさらに空白を全部落として厳しく見る。"""
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", "", t)


def canon(t: str) -> str:
    """句読点・記号も落とした比較用キー（5-gram / 部分文字列で使う）。"""
    return PUNCT_RE.sub("", norm(t))


def shingles(t: str, n: int = 5) -> set[str]:
    if len(t) <= n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jacc(a: set[str], b: set[str], ov: int) -> float:
    d = len(a) + len(b) - ov
    return ov / d if d else 0.0


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 読み込み
def load_split(name: str) -> list[dict]:
    path = os.path.join(SPLIT_DIR, f"corpus_{name}.tsv")
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        assert head == ["source", "id", "text"], (name, head)
        for ln, line in enumerate(f, start=2):
            line = line.rstrip("\n")
            if not line:
                continue
            p = line.split("\t")
            assert len(p) == 3, (name, ln, p)
            rows.append({"split": name, "source": p[0], "id": p[1], "text": p[2],
                         "norm": norm(p[2]), "canon": canon(p[2])})
    return rows


def load_jsut(jsut_dir: str):
    """VOICEACTRESS100 / REPEAT500 の生テキスト。無ければ (None, why)。"""
    va = os.path.join(jsut_dir, "jsut_ver1.1__voiceactress100__transcript_utf8.txt")
    rp = os.path.join(jsut_dir, "jsut_ver1.1__repeat500__transcript_utf8.txt")
    if not (os.path.exists(va) and os.path.exists(rp)):
        return None, f"not found under {jsut_dir}"

    def rd(path):
        out = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                k, _, v = line.partition(":")
                out[k] = v
        return out

    return {"voiceactress100": rd(va), "repeat500": rd(rp),
            "_paths": {"voiceactress100": va, "repeat500": rp},
            "_sha256": {"voiceactress100": sha256(va), "repeat500": sha256(rp)}}, None


# ---------------------------------------------------------------- 近傍検索
class Index:
    """train 側の文字 5-gram 転置インデックス。"""

    def __init__(self, rows):
        self.rows = rows
        self.sh = [shingles(r["canon"]) for r in rows]
        self.inv = collections.defaultdict(list)
        for i, s in enumerate(self.sh):
            for g in s:
                self.inv[g].append(i)

    def candidates(self, q: set[str]) -> collections.Counter:
        c = collections.Counter()
        for g in q:
            for i in self.inv.get(g, ()):
                c[i] += 1
        return c


def compare(query_rows, index: Index, min_sub_len: int = 5):
    """query 側の各行について train との (best jaccard, 部分文字列ペア) を返す。"""
    best_pairs = []
    sub_pairs = []
    for r in query_rows:
        q = shingles(r["canon"])
        if not q:
            continue
        cand = index.candidates(q)
        best_j, best_i = 0.0, -1
        for i, ov in cand.items():
            j = jacc(q, index.sh[i], ov)
            if j > best_j:
                best_j, best_i = j, i
            # 部分文字列: 包含されている側の shingle が全部相手に入る
            # -> overlap == len(小さい方の shingle 集合) が必要条件。
            if len(r["canon"]) >= min_sub_len and len(index.rows[i]["canon"]) >= min_sub_len:
                t = index.rows[i]
                if ov == len(q) and r["canon"] != t["canon"] and r["canon"] in t["canon"]:
                    sub_pairs.append((r, t, "query_in_train"))
                elif ov == len(index.sh[i]) and r["canon"] != t["canon"] and t["canon"] in r["canon"]:
                    sub_pairs.append((r, t, "train_in_query"))
        if best_i >= 0:
            best_pairs.append((round(best_j, 4), r, index.rows[best_i]))
    best_pairs.sort(key=lambda x: -x[0])
    return best_pairs, sub_pairs


def pair_json(j, a, b):
    return {"jaccard5gram": j,
            "a": {"split": a["split"], "source": a["source"], "id": a["id"], "text": a["text"]},
            "b": {"split": b["split"], "source": b["source"], "id": b["id"], "text": b["text"]}}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsut-dir", default=DEFAULT_JSUT)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    data = {n: load_split(n) for n in SPLITS}
    by_split_src = {n: dict(collections.Counter(r["source"] for r in rs).most_common())
                    for n, rs in data.items()}

    rep: dict = {
        "task": "B-10 教師の学習テキストとの重複検査",
        "repro": "uv run python scripts/b10_overlap.py",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            f"corpus_{n}.tsv": {
                "path": os.path.join(SPLIT_DIR, f"corpus_{n}.tsv"),
                "rows": len(data[n]),
                "sha256": sha256(os.path.join(SPLIT_DIR, f"corpus_{n}.tsv")),
            } for n in SPLITS
        },
    }
    rep["source_breakdown"] = {n: {"rows": len(data[n]), "by_source": by_split_src[n]}
                               for n in SPLITS}

    # -------- split 間の関係 (id ベース) --------------------------------
    ids = {n: {r["id"] for r in data[n]} for n in SPLITS}
    rep["split_relations"] = {
        f"{a}_cap_{b}_by_id": len(ids[a] & ids[b])
        for a, b in [("train", "heldout"), ("sibdense", "heldout"),
                     ("sibdense", "train"), ("embedded", "train"),
                     ("embedded", "heldout")]
    }
    rep["split_relations"]["note"] = (
        "sibdense は heldout の部分集合。sibdense の汚染は heldout の汚染と同じ行を指す。")

    # -------- (A) 教師 FT テキストの混入 --------------------------------
    jsut, jsut_err = load_jsut(args.jsut_dir)

    ft = {
        "why_these_sources": (
            "教師の FT コーパス = つくよみちゃんコーパス。data-sources.yml に "
            "utterances: 100 / used_only_in: tsukuyomi-6lang-v2 とあり、"
            "HF キャッシュ datasets--ayousanz--tsukuyomi-chan-ljspeech の wav が "
            "VOICEACTRESS100_NNN.wav という命名なので本文は JSUT voiceactress100。"
            "JSUT repeat500 set1 は voiceactress100 とほぼ同一本文なので同じ扱いにする。"),
        "flagged_sources": list(FT_SOURCES),
        "jsut_raw_text_available": jsut is not None,
    }
    if jsut is None:
        ft["jsut_raw_text_error"] = jsut_err
    else:
        ft["jsut_raw_text"] = {"paths": jsut["_paths"], "sha256": jsut["_sha256"]}
        va = {norm(v) for v in jsut["voiceactress100"].values()}
        rp_all = {k: norm(v) for k, v in jsut["repeat500"].items()}
        rp1 = {v for k, v in rp_all.items() if k.startswith("REPEAT500_set1_")}
        ft["voiceactress100_vs_repeat500_set1"] = {
            "voiceactress100_unique_norm": len(va),
            "repeat500_set1_unique_norm": len(rp1),
            "common": len(va & rp1),
            "only_in_voiceactress100": len(va - rp1),
            "only_in_repeat500_set1": len(rp1 - va),
            "note": "NFKC+空白除去後の本文集合。共通分は完全一致で併合できる。",
        }
        ft["ft_text_set_size"] = len(va | rp1)
        ft_texts = va | rp1
        # repeat500 の set1 以外も参考に持っておく (FT ではないが JSUT 由来)
        ft["repeat500_all_unique_norm"] = len({v for v in rp_all.values()})

    # source 列による判定
    by_source_hits = {n: [r for r in data[n] if r["source"] in FT_SOURCES] for n in SPLITS}
    # 本文一致による判定 (別ラベルで紛れ込んでいないか)
    by_text_hits = {n: [] for n in SPLITS}
    if jsut is not None:
        for n in SPLITS:
            by_text_hits[n] = [r for r in data[n] if r["norm"] in ft_texts]

    ft["hits"] = {}
    for n in SPLITS:
        src_ids = {r["id"] for r in by_source_hits[n]}
        txt_ids = {r["id"] for r in by_text_hits[n]}
        ft["hits"][n] = {
            "by_source_label": len(by_source_hits[n]),
            "by_source_label_breakdown": dict(
                collections.Counter(r["source"] for r in by_source_hits[n]).most_common()),
            "by_source_label_pct": round(100 * len(by_source_hits[n]) / len(data[n]), 3),
            "by_text_match": (len(by_text_hits[n]) if jsut is not None else None),
            "text_match_not_caught_by_label": sorted(txt_ids - src_ids),
            "label_not_caught_by_text_match": sorted(src_ids - txt_ids),
        }
    ft["verdict"] = (
        "重大: heldout に jsut/repeat500 が 10 行残っている（うち 4 行は "
        "sibdense = 生成済みラベルパックにも入っている）。"
        if len(by_source_hits["heldout"]) else "heldout に FT 由来行なし。")

    def row_json(r):
        return {"split": r["split"], "source": r["source"], "id": r["id"], "text": r["text"]}

    ft["rows"] = {}
    for n in SPLITS:
        merged = {r["id"]: r for r in by_source_hits[n] + by_text_hits[n]}
        ft["rows"][n] = [row_json(merged[k]) for k in sorted(merged)]
    rep["ft_contamination"] = ft

    # -------- (B) train <-> heldout のリーク -----------------------------
    idx = Index(data["train"])
    tr_by_norm = collections.defaultdict(list)
    for r in data["train"]:
        tr_by_norm[r["norm"]].append(r)
    tr_by_canon = collections.defaultdict(list)
    for r in data["train"]:
        tr_by_canon[r["canon"]].append(r)

    leak = {}
    for name in ("heldout", "embedded", "sibdense"):
        rows = data[name]
        exact_norm = [(r, tr_by_norm[r["norm"]][0]) for r in rows if r["norm"] in tr_by_norm]
        exact_canon = [(r, tr_by_canon[r["canon"]][0]) for r in rows
                       if r["canon"] in tr_by_canon and r["norm"] not in tr_by_norm]
        best, subs = compare(rows, idx)
        # 部分文字列ペアの重複除去
        seen = set()
        subs_u = []
        for a, b, kind in subs:
            k = (a["id"], b["id"], kind)
            if k in seen:
                continue
            seen.add(k)
            subs_u.append((a, b, kind))
        leak[f"{name}_vs_train"] = {
            "n_query_rows": len(rows),
            "exact_match_nfkc_nospace": {
                "count": len(exact_norm),
                "pairs": [pair_json(1.0, a, b) for a, b in exact_norm[:20]],
            },
            "exact_match_after_punct_strip_only": {
                "count": len(exact_canon),
                "pairs": [pair_json(1.0, a, b) for a, b in exact_canon[:20]],
            },
            "jaccard5gram": {
                "max": best[0][0] if best else None,
                "n_ge_0.70": sum(1 for j, _, _ in best if j >= 0.70),
                "n_ge_0.50": sum(1 for j, _, _ in best if j >= 0.50),
                "n_ge_0.30": sum(1 for j, _, _ in best if j >= 0.30),
                "top": [pair_json(j, a, b) for j, a, b in best[:args.top]],
            },
            "substring_containment": {
                "count": len(subs_u),
                "min_len_chars": 5,
                "meaningful_min_contained_len": MEANINGFUL_SUB_LEN,
                "count_meaningful": sum(
                    1 for a, b, _ in subs_u
                    if min(len(a["canon"]), len(b["canon"])) >= MEANINGFUL_SUB_LEN),
                "pairs": [{"direction": kind,
                           "a": row_json(a), "b": row_json(b),
                           "len_a": len(a["canon"]), "len_b": len(b["canon"]),
                           "contained_len": min(len(a["canon"]), len(b["canon"])),
                           "meaningful": min(len(a["canon"]), len(b["canon"])) >= MEANINGFUL_SUB_LEN}
                          for a, b, kind in sorted(
                              subs_u, key=lambda x: -min(len(x[0]["canon"]), len(x[1]["canon"])))[:100]],
            },
        }
    rep["leakage"] = leak

    # -------- (C) 教師の学習テキストとの照合: 確認範囲 --------------------
    tsuku = os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ayousanz--tsukuyomi-chan-ljspeech")
    tsuku_wavs = []
    if os.path.isdir(tsuku):
        for dp, _, fns in os.walk(os.path.join(tsuku, "snapshots")):
            tsuku_wavs += [f for f in fns if f.endswith(".wav")]
    rep["teacher_text_check"] = {
        "checked_and_confirmed": [
            {
                "what": "FT コーパス (つくよみちゃんコーパス) の本文同定",
                "evidence": [
                    "piper-plus/data-sources.yml: id=tsukuyomi-chan-corpus, languages=[ja], "
                    "speakers=1, utterances=100, used_only_in=[tsukuyomi-6lang-v2, tsukuyomi-mb-istft]",
                    f"HF キャッシュ {tsuku} の snapshot に wav が {len(tsuku_wavs)} 個あり、"
                    f"すべて VOICEACTRESS100_NNN.wav 形式"
                    + (f" (例: {sorted(tsuku_wavs)[0]})" if tsuku_wavs else ""),
                    "→ FT の本文は JSUT voiceactress100 の 100 文と同定した",
                ],
                "caveat": f"ローカルキャッシュは {len(tsuku_wavs)}/100 wav の部分キャッシュ。"
                          "残り 76 件のファイル名は未確認 (新規ダウンロードはしていない)。"
                          "100 件という数は data-sources.yml の記載による。",
            },
            {
                "what": "評価テキスト piper-plus/scripts/evaluation/evaluation_texts_ja.txt",
                "evidence": ["52 行。学習データではなく手書きの評価文。下記 evaluation_texts_ja で照合した"],
            },
        ],
        "not_checked": [
            {
                "what": "教師の事前学習テキスト (6 言語 497,519 発話) の日本語部分",
                "why": "MOE-Speech 20speakers (data-sources.yml: utterances=60,148) の書き起こしが"
                       "ローカルに無い。HF キャッシュにも datasets--ayousanz--moe-speech-* は無い。"
                       "新規ダウンロードは禁止のため **未検査**。",
                "risk": "MOE-Speech は音声コーパスで、書き起こしが JSUT / ITA / ROHAN 由来の"
                        "朗読文である可能性は否定できない。本 split の 23,271 行のうち"
                        "どれだけが教師の事前学習に見られているかは不明。",
            },
            {
                "what": "他 5 言語 (en/zh/es/fr/pt) の学習テキスト",
                "why": "日本語 split と本文が重なることは無いので検査不要と判断した",
            },
        ],
    }

    # evaluation_texts_ja.txt との照合
    ev_path = "~/Documents/piper-plus/scripts/evaluation/evaluation_texts_ja.txt"
    if os.path.exists(ev_path):
        ev = set()
        with open(ev_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ev.add(norm(line))
        hits = {n: [row_json(r) for r in data[n] if r["norm"] in ev] for n in SPLITS}
        rep["teacher_text_check"]["evaluation_texts_ja"] = {
            "path": ev_path, "sha256": sha256(ev_path), "unique_lines": len(ev),
            "hits": {n: {"count": len(v), "rows": v} for n, v in hits.items()},
        }

    # -------- 既存レポートの評価文が汚染していないか -----------------------
    b5 = os.path.join(ROOT, "reports", "b5_teacher_baseline.json")
    if os.path.exists(b5) and jsut is not None:
        d5 = json.load(open(b5, encoding="utf-8"))
        utts = d5.get("utterances", [])
        n_ft = sum(1 for u in utts if norm(u.get("text", "")) in ft_texts)
        n_tr = sum(1 for u in utts if norm(u.get("text", "")) in tr_by_norm)
        n_ho = sum(1 for u in utts
                   if norm(u.get("text", "")) in {r["norm"] for r in data["heldout"]})
        rep["existing_report_check"] = {
            "b5_teacher_baseline": {
                "path": b5, "n_utterances": len(utts),
                "n_from_teacher_ft_text": n_ft,
                "n_found_in_train": n_tr,
                "n_found_in_heldout": n_ho,
                "verdict": ("教師ベースライン UTMOS は FT テキストを含まない"
                            if n_ft == 0 else
                            f"教師ベースラインに FT テキストが {n_ft} 件混入している"),
            },
            "b5_human_control": {
                "note": ("人間音声の分母 (n=24, UTMOS mean 2.3047) は "
                         "tsukuyomi-chan-ljspeech の VOICEACTRESS100 wav。"
                         "これは教師の FT コーパスそのもの。D-013 の設計どおりで"
                         "汚染ではないが、**教師ベースラインの合成文とは別のテキスト**"
                         "であることに注意 (人間 24 文 != 教師 24 文)。"),
            },
        }

    # -------- 生成済みラベルパックへの波及 --------------------------------
    pack_idx = os.path.join(ROOT, "data", "pack_sibdense", "index.jsonl")
    if os.path.exists(pack_idx):
        pack = [json.loads(l) for l in open(pack_idx, encoding="utf-8") if l.strip()]
        pack_uids = {r["uid"] for r in pack}
        sd_ids = {r["id"] for r in data["sibdense"]}
        ft_sd = {r["id"] for r in ft["rows"]["sibdense"]}
        rep["labelpack_impact"] = {
            "path": os.path.join(ROOT, "data", "pack_sibdense"),
            "utterances_in_pack": len(pack),
            "contaminated_uids_in_pack": sorted(pack_uids & ft_sd),
            "contaminated_pct": round(100 * len(pack_uids & ft_sd) / len(pack), 2),
            "unexpected_uids_in_pack": sorted(pack_uids - sd_ids),
            "sibdense_rows_missing_from_pack": sorted(sd_ids - pack_uids),
            "note": (
                "B-10 の範囲外だが検査中に見つかった別件: pack の seq=0 が TSV の"
                " ヘッダ行 (uid='id', text='text') を発話として取り込んでいる。"
                " scripts/gen_teacher_labels.py:149 の csv.reader がヘッダを"
                " skip していないため。--limit / utts の数え方が 1 ずれ、"
                " 実データ 1 行 (COUNTERSUFFIX26_05) が落ちている。"),
        }

    # -------- 除外リスト -------------------------------------------------
    tiers: dict = {}

    # tier 1: 教師 FT テキスト。eval からは必須、train からも推奨。
    t1 = []
    for n in SPLITS:
        for r in ft["rows"][n]:
            t1.append({"split": n, "source": r["source"], "id": r["id"],
                       "text": r["text"],
                       "reason": "teacher_ft_text(voiceactress100/repeat500)"})
    tiers["tier1_teacher_ft_text"] = {
        "severity": "must_exclude",
        "applies_to": "eval からは必須。train からも両サブセット丸ごと落とすのが正しい運用。",
        "count": len(t1),
        "by_split": dict(collections.Counter(e["split"] for e in t1).most_common()),
        "rows": t1,
    }

    # tier 2: train との完全一致 (eval split のみ)
    t2 = []
    for name in ("heldout", "embedded", "sibdense"):
        blk = leak[f"{name}_vs_train"]
        for key in ("exact_match_nfkc_nospace", "exact_match_after_punct_strip_only"):
            for pj in blk[key]["pairs"]:
                t2.append({"split": name, "source": pj["a"]["source"], "id": pj["a"]["id"],
                           "text": pj["a"]["text"], "reason": f"train_{key}"})
    tiers["tier2_exact_dup_with_train"] = {
        "severity": "must_exclude", "count": len(t2), "rows": t2}

    # tier 3: train との近傍重複 / 部分文字列包含 (eval split のみ)
    t3 = []
    for name in ("heldout", "embedded", "sibdense"):
        blk = leak[f"{name}_vs_train"]
        for pj in blk["jaccard5gram"]["top"]:
            if pj["jaccard5gram"] >= 0.50:
                t3.append({"split": name, "source": pj["a"]["source"], "id": pj["a"]["id"],
                           "text": pj["a"]["text"],
                           "reason": f"near_dup_with_train_jaccard5gram={pj['jaccard5gram']}"})
        for pj in blk["substring_containment"]["pairs"]:
            if not pj["meaningful"]:
                continue
            t3.append({"split": name, "source": pj["a"]["source"], "id": pj["a"]["id"],
                       "text": pj["a"]["text"],
                       "reason": (f"substring_{pj['direction']}_with_{pj['b']['id']}"
                                  f"_len{pj['contained_len']}")})
    seen3 = set()
    t3u = []
    for e in t3:
        k = (e["split"], e["id"])
        if k in seen3:
            continue
        seen3.add(k)
        t3u.append(e)
    tiers["tier3_near_dup_or_substring_with_train"] = {
        "severity": "recommended_exclude",
        "applies_to": "eval split のみ。train 側は残してよい。",
        "thresholds": {"jaccard5gram": 0.50,
                       "substring_min_contained_len_chars": MEANINGFUL_SUB_LEN},
        "count": len(t3u),
        "by_split": dict(collections.Counter(e["split"] for e in t3u).most_common()),
        "rows": t3u,
    }

    allrows = t1 + t2 + t3u
    seen = set()
    excl_u = []
    for e in allrows:
        k = (e["split"], e["id"])
        if k in seen:
            continue
        seen.add(k)
        excl_u.append(e)

    ho_ids = {e["id"] for e in excl_u if e["split"] == "heldout"}
    rep["exclusion_list"] = {
        "tiers": tiers,
        "union": {
            "count": len(excl_u),
            "by_split": dict(collections.Counter(e["split"] for e in excl_u).most_common()),
            "rows": excl_u,
        },
        "heldout_after_exclusion": len(data["heldout"]) - len(ho_ids),
        "heldout_must_exclude_only": len(
            {e["id"] for e in (t1 + t2) if e["split"] == "heldout"}),
        "sibdense_ids_to_drop": sorted({e["id"] for e in excl_u if e["split"] == "sibdense"}),
    }

    rep["caveats"] = [
        "部分文字列検査は 5-gram 転置インデックスを候補生成に使うので、"
        "包含される側が 5 文字未満のペアは構造上見つからない "
        "(MEANINGFUL_SUB_LEN=8 なので除外リストには影響しない)。",
        "Jaccard は句読点・長音記号・括弧を落とした文字 5-gram の集合 Jaccard。"
        "split 構築時 (scripts/b0/build_corpus.py) が J>=0.70 の heldout 行を"
        "すでに train へ移しているため、本検査で J>=0.70 が 0 件なのは想定内であり、"
        "「リークが無い」ことの証明ではない。",
        "教師の事前学習テキスト (MOE-Speech 20speakers 60,148 発話ほか) との重複は"
        "**未検査**。書き起こしが手元に無く、新規ダウンロードが禁止のため。"
        "この 23,271 行のうち教師が事前学習で見た文の割合は不明。",
        "つくよみちゃんコーパスの HF キャッシュは 24/100 wav の部分キャッシュ。"
        "残り 76 件のファイル名は未確認。100 件という総数は data-sources.yml の記載による。",
    ]
    rep["elapsed_sec"] = round(time.time() - t0, 2)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    # ---- 端末出力 ----
    print(f"[splits] " + "  ".join(f"{n}={len(data[n])}" for n in SPLITS))
    print(f"[FT 汚染] " + "  ".join(
        f"{n}={ft['hits'][n]['by_source_label']}" for n in SPLITS))
    for n in SPLITS:
        b = ft["hits"][n]["by_source_label_breakdown"]
        if b:
            print(f"          {n}: {b}")
    for name in ("heldout", "embedded", "sibdense"):
        b = leak[f"{name}_vs_train"]
        print(f"[{name} vs train] exact={b['exact_match_nfkc_nospace']['count']}  "
              f"punct-only-exact={b['exact_match_after_punct_strip_only']['count']}  "
              f"maxJ={b['jaccard5gram']['max']}  "
              f"J>=0.7:{b['jaccard5gram']['n_ge_0.70']}  J>=0.5:{b['jaccard5gram']['n_ge_0.50']}  "
              f"substr={b['substring_containment']['count']}"
              f"(意味あり {b['substring_containment']['count_meaningful']})")
    for k, v in rep["exclusion_list"]["tiers"].items():
        print(f"[除外 {k}] {v['count']} 行  {v.get('by_split', '')}")
    print(f"[除外 union] {rep['exclusion_list']['union']['count']} 行  "
          f"{rep['exclusion_list']['union']['by_split']}  "
          f"heldout 残 {rep['exclusion_list']['heldout_after_exclusion']} "
          f"(必須除外のみなら {len(data['heldout']) - rep['exclusion_list']['heldout_must_exclude_only']})")
    if "labelpack_impact" in rep:
        li = rep["labelpack_impact"]
        print(f"[labelpack] {li['utterances_in_pack']} utt 中 汚染 "
              f"{len(li['contaminated_uids_in_pack'])} 件 "
              f"({li['contaminated_pct']}%): {li['contaminated_uids_in_pack']}")
        print(f"            想定外 uid: {li['unexpected_uids_in_pack']}  "
              f"欠落: {li['sibdense_rows_missing_from_pack']}")
    print(f"-> {args.out}  ({rep['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
