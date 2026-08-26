#!/usr/bin/env python3
"""A-1: ラベル生成の入力を「かな中間表現」に統一できるかを判定する。

いま経路が 2 つあり噛み合っていない:

    デバイス:   中間表現 --[mora テーブル 951 B]--> 音素ID
    ラベル生成: 漢字文   --[MultilingualPhonemizer]--> 音素ID --> 教師

蒸留では **生徒が学ぶ入力とデバイスが実際に作る入力が一致していなければならない**。
本スクリプトは held-out 2,325 文 + embedded 183 文の全件で 2 経路を突き合わせる。

    (a) canonical  : text_to_phoneme_ids_and_prosody(text, ..., language_id_map=lim)
                     = MultilingualPhonemizer + PiperEncoder
    (b) 中間表現経由: JapanesePhonemizer -> [# 正規化] -> phonemes_to_intermediate
                     -> intermediate_to_tokens -> intermediate_to_phonemes
                     -> PiperEncoder.encode_with_prosody

(a) は intersperse padding を PiperEncoder が入れる。(b) でも **同じ PiperEncoder** を
通すので、padding 込みの最終 ID 列同士をそのまま比較できる。

3 つの独立した効果を切り分けて測る:

* ``base``      : 現状の mora テーブル (116 entry) / 正規化なし
* ``ext``       : mora テーブルを拡張（外来音モーラ）
* ``ext+norm``  : さらに `#` の mora 内挿入を正規化

実行:
    uv run python scripts/a1_path_unification.py
    uv run python scripts/a1_path_unification.py --teacher 200   # 教師出力も照合
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
CKPT_NAME = "epoch=499-step=22000.ckpt"

from kana_g2p import (  # noqa: E402
    DEVOICE,
    DEVOICED_MARK,
    MARKS,
    build_mora_table,
    intermediate_to_phonemes,
    intermediate_to_tokens,
    phonemes_to_intermediate,
)

ACCENT = frozenset({"[", "]", "#"})

# --- mora テーブルの拡張 ----------------------------------------------------
#
# 現行 `build_mora_table()` の seed 列は外来音モーラを取りこぼす。原因は 2 つ:
#   1. 小書き仮名の判定集合 "ゃゅょぁぃぇぉ" に **`ぅ` が無い**ので
#      `とぅ` が `と`+`ぅ` に割れる
#   2. seed 列に `つぁ` `じぇ` `ゔぁ` `くぉ` `てゅ` 等が入っていない
#
# 下の一覧はすべて `ま_ま` キャリアで実測して 1 モーラ (C+V) になることを確認済み。
# `しぇ` `ずぃ` だけは **ひらがなキャリアだと OpenJTalk が 3 音素に割る**
# （`しぇ` → sh i e）ため、カタカナキャリア `マシェマ` から導出する。
EXTRA_MORAE_HIRAGANA = (
    "つぁ つぃ つぇ つぉ ちぇ じぇ いぇ うぉ とぅ どぅ "
    "ゔぁ ゔぃ ゔぇ ゔぉ ふゅ "
    "てゃ てゅ てょ でゃ でゅ でぇ でょ "
    "くぁ くぃ くぅ くぇ くぉ ぐぁ ぐぃ ぐぅ ぐぇ ぐぉ すぃ "
    "きぇ ぎぇ にぇ ひぇ びぇ ぴぇ みぇ りぇ"
).split()
EXTRA_MORAE_KATAKANA = {"しぇ": "シェ", "ずぃ": "ズィ"}

# `ん` の異音規則の取りこぼし。`japanese.py:_apply_n_phoneme_rules` は
# 軟口蓋音に **`kw` / `gw` を含む**が、`kana_g2p.N_ALLOPHONE` (18 件) には無い。
#   そのとき…わん、わん、ぐゎあ、… → (a) N_ng / (b) N_uvular
# 端末側の規則を 20 件にすればこの差は消える。
EXTRA_N_ALLOPHONE = {"kw": "N_ng", "gw": "N_ng"}

MORA_FINAL_PREFIXES = ("N_",)
MORA_FINAL_TOKENS = frozenset({"cl", "a", "i", "u", "e", "o", "A", "I", "U", "E", "O", "N"})


def extend_mora_table(table: dict[str, list[str]]) -> dict[str, list[str]]:
    """外来音モーラを実測で足す（ハードコードしない — C-002 の教訓）。"""
    from piper_plus_g2p.japanese import JapanesePhonemizer

    p = JapanesePhonemizer()
    out = dict(table)
    # `しぇ` `ずぃ` は **ひらがなキャリアだと 3 音素に割れる**（`sh i e` / `z u i`）。
    # カタカナ表記の綴りを、ひらがなキャリア `ま_ま` に入れると 1 モーラで取れる
    # （`マシェマ` のような全カタカナ文だと今度は `#` が湧く）。
    probes = [(m, "ま" + m + "ま", ("m", "a")) for m in EXTRA_MORAE_HIRAGANA]
    probes += [
        (h, "ま" + k + "ま", ("m", "a")) for h, k in EXTRA_MORAE_KATAKANA.items()
    ]
    for mora, carrier, (c0, c1) in probes:
        ph = [x for x in p.phonemize(carrier) if x not in MARKS]
        if len(ph) >= 5 and ph[:2] == [c0, c1] and ph[-2:] == [c0, c1]:
            core = [DEVOICE.get(x, x) for x in ph[2:-2]]
            if len(core) <= 2:
                out.setdefault(mora, core)
    return out


def is_mora_final(token: str) -> bool:
    return token in MORA_FINAL_TOKENS or token.startswith(MORA_FINAL_PREFIXES)


def normalize_phrase_marks(tokens: list[str], prosody: list) -> tuple[list, list]:
    """`#` が **モーラ内部**（子音と母音の間）に出るのを直す。

    `_phonemize_core()` は fullcontext ラベル 1 件ごとに記号を挿入するが、
    A1/A2/A3 は**モーラ単位**なので、1 モーラだけのアクセント句
    (`a2 == a3 == 1`) では子音ラベルでも条件が成立し `#` が余分に出る:

        じゃ俺は…  →  ['j', '#', 'a', '#', 'o', '[', ...]   ← `j` の直後は誤り
        すみませんね。→ [..., 'N_n', 'n', '#', 'e']            ← `#` がモーラ内で迷子

    モーラ末（母音 / N_* / cl）の直後に移し、重複は落とす。
    """
    out: list[str] = []
    out_p: list = []
    pending = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "#" and out and not is_mora_final(out[-1]) and out[-1] not in MARKS:
            pending = True
            i += 1
            continue
        out.append(t)
        out_p.append(prosody[i])
        i += 1
        if pending and is_mora_final(t):
            if not (i < len(tokens) and tokens[i] == "#"):
                out.append("#")
                out_p.append(None)
            pending = False
    return out, out_p


def snapshot_dir() -> str:
    hits = glob.glob(
        str(Path.home())
        + "/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 snapshot が HF キャッシュに無い")
    return hits[0]


def load_rows(path: Path) -> list[tuple[str, str]]:
    rows = []
    with path.open() as fh:
        assert fh.readline().startswith("source\t")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2]:
                rows.append((parts[1], parts[2]))
    return rows


def norm_devoice(seq: list[str]) -> list[str]:
    return [DEVOICE.get(p, p) for p in seq]


def classify(ph_a: list[str], ph_b: list[str]) -> str:
    if ph_a == ph_b:
        return "equal"
    if norm_devoice(ph_a) == norm_devoice(ph_b):
        return "devoicing_only"
    a_seg = [p for p in ph_a if p not in ACCENT]
    b_seg = [p for p in ph_b if p not in ACCENT]
    if a_seg == b_seg:
        return "accent_marks_only"
    if norm_devoice(a_seg) == norm_devoice(b_seg):
        return "accent_and_devoicing"
    if len(a_seg) != len(b_seg):
        return "segment_length_differs"
    return "segment_content_differs"


def to_dicts(objs) -> list:
    return [
        {"a1": p.a1, "a2": p.a2, "a3": p.a3} if p is not None else None for p in objs
    ]


def build_variant(text, jp, table, normalize):
    """(b) 経路。返り値 (status, intermediate, phonemes, prosody)。"""
    ph_ja, pr_ja = jp.phonemize_with_prosody(text)
    if normalize:
        ph_ja, pr_ja = normalize_phrase_marks(ph_ja, list(pr_ja))
    try:
        inter = "".join(phonemes_to_intermediate(ph_ja, table))
        ph_b = intermediate_to_phonemes(intermediate_to_tokens(inter, table), table)
    except KeyError as exc:
        return "not_representable", "", None, None, str(exc), ph_ja
    return "ok", inter, ph_b, list(pr_ja), None, ph_ja


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "reports" / "a1_path_unification.json"))
    args = ap.parse_args()

    from piper_plus_g2p.encode.encoder import PiperEncoder
    from piper_plus_g2p.japanese import JapanesePhonemizer
    from piper_plus_g2p.multilingual import MultilingualPhonemizer
    from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody

    import piper_plus_g2p as _g2p

    assert _g2p.__file__.startswith(PIPER_PLUS), _g2p.__file__

    snap = snapshot_dir()
    config = json.load(open(snap + "config.json"))
    pid_map = config["phoneme_id_map"]
    lim = config.get("language_id_map") or {
        c: i for i, c in enumerate(["ja", "en", "zh", "es", "fr", "pt"])
    }

    jp = JapanesePhonemizer()
    ml = MultilingualPhonemizer(sorted(lim.keys()))
    encoder = PiperEncoder(pid_map)

    table_base = build_mora_table()
    table_ext = extend_mora_table(table_base)
    from kana_g2p import N_ALLOPHONE, table_size_bytes

    n_rules_base = dict(N_ALLOPHONE)
    n_rules_ext = {**n_rules_base, **EXTRA_N_ALLOPHONE}

    variants = {
        "base": (table_base, n_rules_base, False),
        "ext": (table_ext, n_rules_ext, False),
        "ext_norm": (table_ext, n_rules_ext, True),
    }

    datasets = {
        "heldout": load_rows(ROOT / "data" / "splits" / "corpus_heldout.tsv"),
        "embedded": load_rows(ROOT / "data" / "splits" / "corpus_embedded.tsv"),
        # train も回す。ラベル一括生成にかけるのはこの 20,946 文なので、
        # 「1 文でも表現できないものが残っていないか」をここで確定させる。
        "train": load_rows(ROOT / "data" / "splits" / "corpus_train.tsv"),
    }

    summary: dict = {}
    detail_rows: dict = {}

    for dname, rows in datasets.items():
        summary[dname] = {"n": len(rows)}
        detail_rows[dname] = []
        for vname, (table, n_rules, normalize) in variants.items():
            # `intermediate_to_phonemes` は module 変数 N_ALLOPHONE を見る
            N_ALLOPHONE.clear()
            N_ALLOPHONE.update(n_rules)
            counts: Counter[str] = Counter()
            routing: Counter[str] = Counter()
            misroute_examples: list = []
            ids_eq = ids_eq_norm = pros_eq = 0
            nonja = nonja_ids_eq = 0
            t0 = time.time()
            for ri, (rid, text) in enumerate(rows):
                ids_a, pros_a = text_to_phoneme_ids_and_prosody(
                    text, pid_map, language="ja", language_id_map=lim
                )
                ph_a = ml.phonemize(text)
                langs = sorted({s["language"] for s in ml.segment_text(text)})
                is_nonja = langs != ["ja"]
                nonja += is_nonja
                routing["+".join(langs)] += 1

                # (a) 側にも同じ `#` 正規化をかけた基準。
                # 「中間表現が情報を落としているか」だけを見るための比較。
                ph_a_norm = ph_a
                ids_a_norm = ids_a
                if normalize and not is_nonja:
                    ph_a_norm, pr_a_norm = normalize_phrase_marks(
                        list(ph_a), list(jp.phonemize_with_prosody(text)[1])
                    )
                    ids_a_norm, _ = encoder.encode_with_prosody(ph_a_norm, pr_a_norm)

                status, inter, ph_b, pr_b, err, ph_src = build_variant(
                    text, jp, table, normalize
                )
                if status != "ok":
                    counts["not_representable"] += 1
                    if vname == "ext_norm":
                        detail_rows[dname].append(
                            {"id": rid, "text": text, "status": status, "error": err}
                        )
                    continue
                if ph_b != ph_src:
                    counts["roundtrip_mismatch"] += 1
                ids_b, pros_b_obj = encoder.encode_with_prosody(ph_b, pr_b)
                counts[classify(ph_a, ph_b)] += 1
                same = ids_a == ids_b
                ids_eq += same
                ids_eq_norm += ids_a_norm == ids_b
                pros_eq += pros_a == to_dicts(pros_b_obj)
                nonja_ids_eq += same and is_nonja
                if vname == "ext_norm" and is_nonja and len(misroute_examples) < 8:
                    misroute_examples.append(
                        {
                            "text": text,
                            "langs_a": langs,
                            "ph_a": " ".join(ph_a[:24]),
                            "ph_b": " ".join(ph_b[:24]),
                            "intermediate": inter,
                        }
                    )
                if vname == "ext_norm" and not same and len(detail_rows[dname]) < 300:
                    detail_rows[dname].append(
                        {
                            "id": rid,
                            "text": text,
                            "status": "ok",
                            "langs_a": langs,
                            "kind": classify(ph_a, ph_b),
                            "ids_equal_norm": ids_a_norm == ids_b,
                            "intermediate": inter,
                            "ph_a": ph_a,
                            "ph_b": ph_b,
                        }
                    )
            n = len(rows)
            summary[dname][vname] = {
                "mora_table_entries": len(table),
                "mora_table_bytes": table_size_bytes(table),
                "n_allophone_rules": len(n_rules),
                "hash_normalized": normalize,
                "ids_equal_vs_a": ids_eq,
                "ids_equal_vs_a_pct": round(100 * ids_eq / n, 3),
                "ids_equal_vs_a_normalized": ids_eq_norm,
                "ids_equal_vs_a_normalized_pct": round(100 * ids_eq_norm / n, 3),
                "prosody_equal_vs_a": pros_eq,
                "representable": n - counts["not_representable"],
                "representable_pct": round(
                    100 * (n - counts["not_representable"]) / n, 3
                ),
                "kinds": dict(counts),
                "routing_in_a": dict(routing),
                "misrouting_examples": misroute_examples,
                "rows_routed_nonja_in_a": nonja,
                "rows_routed_nonja_and_ids_equal": nonja_ids_eq,
                "elapsed_s": round(time.time() - t0, 1),
            }
            print(
                f"{dname}/{vname}: "
                f"{json.dumps(summary[dname][vname], ensure_ascii=False)}",
                flush=True,
            )

    teacher_result = None
    misroute_result = None
    if args.teacher:
        N_ALLOPHONE.clear()
        N_ALLOPHONE.update(n_rules_ext)
        teacher_result = run_teacher_check(
            snap, pid_map, lim, table_ext, jp, encoder, datasets, args.teacher
        )
        print(json.dumps(teacher_result, ensure_ascii=False, indent=2), flush=True)
        misroute_result = run_misroute_check(
            snap, pid_map, lim, table_ext, jp, ml, encoder, datasets, 30
        )
        print(json.dumps(misroute_result, ensure_ascii=False, indent=2), flush=True)

    out = {
        "task": "A-1 ラベル生成経路の統一可否",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "paths": {
            "a": "text_to_phoneme_ids_and_prosody(language='ja', language_id_map=lim)",
            "b": "JapanesePhonemizer -> [#正規化] -> phonemes_to_intermediate -> "
            "intermediate_to_tokens -> intermediate_to_phonemes -> PiperEncoder",
        },
        "variants": {
            "base": "現行 mora テーブル (116) / 正規化なし",
            "ext": f"外来音モーラ +{len(table_ext) - len(table_base)} と "
            f"`ん` 異音規則 +{len(EXTRA_N_ALLOPHONE)} (kw/gw) を足した / 正規化なし",
            "ext_norm": "拡張テーブル + 規則 + `#` の mora 内挿入を正規化",
        },
        "summary": summary,
        "teacher_check": teacher_result,
        "misrouted_rows_check": misroute_result,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    detail = Path(args.out).with_name("a1_path_unification_rows.json")
    detail.write_text(json.dumps(detail_rows, ensure_ascii=False, indent=1))
    print(f"\n書き出し: {args.out}\n         {detail}")
    return 0


def run_teacher_check(snap, pid_map, lim, table, jp, encoder, datasets, n_sent) -> dict:
    """(a) と (b) の音素ID列を教師に通し、yT/zT/dT が bit 一致するか測る。"""
    import torch

    sys.path.insert(0, f"{PIPER_PLUS}/src/python")
    from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody

    from phase0_verify_teacher import build_teacher

    ckpt = torch.load(snap + CKPT_NAME, map_location="cpu", weights_only=False)
    teacher = build_teacher(ckpt)

    def infer(ids, pros):
        vals = [
            [p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0] for p in pros
        ]
        with torch.no_grad():
            return teacher.infer(
                torch.tensor([ids]),
                torch.tensor([len(ids)]),
                lid=torch.tensor([0]),
                noise_scale=0.0,
                noise_scale_w=0.0,
                length_scale=1.0,
                prosody_features=torch.tensor([vals], dtype=torch.float32),
                speaker_embeddings=None,
            )

    rows = datasets["heldout"][:n_sent]
    stats = Counter()
    frame_delta = []
    examples = []
    t0 = time.time()
    for rid, text in rows:
        ids_a, pros_a = text_to_phoneme_ids_and_prosody(
            text, pid_map, language="ja", language_id_map=lim
        )
        status, inter, ph_b, pr_b, err, _ = build_variant(text, jp, table, True)
        if status != "ok":
            stats["skipped_not_representable"] += 1
            continue
        ids_b, pros_b_obj = encoder.encode_with_prosody(ph_b, pr_b)
        pros_b = to_dicts(pros_b_obj)
        stats["checked"] += 1
        stats["ids_equal"] += ids_a == ids_b
        oa, ob = infer(ids_a, pros_a), infer(ids_b, pros_b)
        a_eq = torch.equal(oa.audio, ob.audio)
        stats["audio_bit_equal"] += a_eq
        stats["zT_bit_equal"] += torch.equal(oa.latents[0], ob.latents[0])
        stats["dT_bit_equal"] += torch.equal(oa.durations, ob.durations)
        if not a_eq:
            fa, fb = oa.latents[0].shape[-1], ob.latents[0].shape[-1]
            frame_delta.append(fb - fa)
            if len(examples) < 15:
                examples.append(
                    {
                        "id": rid,
                        "text": text,
                        "ids_equal": ids_a == ids_b,
                        "frames_a": fa,
                        "frames_b": fb,
                        "intermediate": inter,
                    }
                )
    checked = stats["checked"]
    return {
        "sentences_requested": n_sent,
        **dict(stats),
        "audio_bit_equal_pct": round(100 * stats["audio_bit_equal"] / checked, 3)
        if checked
        else None,
        "frame_delta_when_audio_differs": frame_delta,
        "mismatch_examples": examples,
        "elapsed_s": round(time.time() - t0, 1),
    }


def run_misroute_check(snap, pid_map, lim, table, jp, ml, encoder, datasets, n_sent):
    """(a) が zh/en に誤ルーティングする行を、両経路で実際に合成して発話速度を比べる。

    「(b) は (a) と違う」だけでは (b) が正しい保証にならない。日本語として
    妥当な速度 (6-10 mora/s、M-5) で喋れているかを見る。
    """
    import torch

    sys.path.insert(0, f"{PIPER_PLUS}/src/python")
    from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody

    from phase0_verify_teacher import build_teacher

    ckpt = torch.load(snap + CKPT_NAME, map_location="cpu", weights_only=False)
    teacher = build_teacher(ckpt)

    def infer(ids, pros):
        vals = [
            [p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0] for p in pros
        ]
        with torch.no_grad():
            return teacher.infer(
                torch.tensor([ids]),
                torch.tensor([len(ids)]),
                lid=torch.tensor([0]),
                noise_scale=0.0,
                noise_scale_w=0.0,
                length_scale=1.0,
                prosody_features=torch.tensor([vals], dtype=torch.float32),
                speaker_embeddings=None,
            )

    picked = []
    for rid, text in datasets["heldout"]:
        if sorted({s["language"] for s in ml.segment_text(text)}) != ["ja"]:
            picked.append((rid, text))
        if len(picked) >= n_sent:
            break

    rows = []
    for rid, text in picked:
        ids_a, pros_a = text_to_phoneme_ids_and_prosody(
            text, pid_map, language="ja", language_id_map=lim
        )
        status, inter, ph_b, pr_b, err, _ = build_variant(text, jp, table, True)
        if status != "ok":
            continue
        ids_b, pros_b_obj = encoder.encode_with_prosody(ph_b, pr_b)
        morae = sum(1 for t in intermediate_to_tokens(inter, table) if t not in MARKS)
        sa = infer(ids_a, pros_a).audio.shape[-1] / 22050
        sb = infer(ids_b, pros_b_obj and to_dicts(pros_b_obj)).audio.shape[-1] / 22050
        rows.append(
            {
                "text": text,
                "morae": morae,
                "sec_a": round(sa, 3),
                "sec_b": round(sb, 3),
                "mora_per_s_a": round(morae / sa, 2),
                "mora_per_s_b": round(morae / sb, 2),
                "intermediate": inter,
            }
        )
    ra = [r["mora_per_s_a"] for r in rows]
    rb = [r["mora_per_s_b"] for r in rows]
    return {
        "n": len(rows),
        "note": "(a) が zh/en に誤ルーティングする行のみ。mora 数は中間表現から数えた",
        "mora_per_s_a_mean": round(sum(ra) / len(ra), 2) if ra else None,
        "mora_per_s_b_mean": round(sum(rb) / len(rb), 2) if rb else None,
        "in_natural_range_6_10_a": sum(6.0 <= x <= 10.0 for x in ra),
        "in_natural_range_6_10_b": sum(6.0 <= x <= 10.0 for x in rb),
        "rows": rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
