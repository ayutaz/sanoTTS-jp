#!/usr/bin/env python3
"""A-2: `prosody_features` (A1/A2/A3) をラベル生成でどう扱うかを決めるための実測。

**問題**: 教師は A1/A2/A3 を受け取るが、デバイス側の中間表現
（ひらがな + `[` `]` `#` + `°`）はこれを持たない。論文の生徒 Dα は音素IDしか見ない。
生徒が学ぶのは「音素ID → 教師が prosody 込みで出した dT」なので、
**同じ音素ID列に対して教師が異なる dT を返すなら、その分は原理的に学習不能**になる。

測るもの:

  stage a  コーパス 23,454 行で音素ID列の重複と prosody の衝突を数える
           （+ アクセント句を切り出して「ID からは区別できない prosody」の量を数える）
  stage b  held-out 300 文で 実 prosody / ゼロ / None の 3 通りの dT を比較
  stage c  音素ID列を固定したまま prosody だけ差し替えて dT のばらつきを測る
           c1: 平板/尾高の入れ替え（ID 列からは区別できない唯一の言語学的曖昧性）
           c2: 同じ長さの別文の prosody を丸ごと移植（上限の目安）
  stage d  実 prosody とゼロで教師音声を合成して UTMOS を比べる

実行:
    uv run python scripts/a2_prosody.py phonemize     # 音素化キャッシュを作る
    uv run python scripts/a2_prosody.py a b c
    uv run --extra eval python scripts/a2_prosody.py d
    uv run python scripts/a2_prosody.py merge         # reports/a2_prosody.json を作る

前提: `teacher-inference` skill の 6 項目。duration-only 高速経路は
`infer()` と bit 一致することを stage b の冒頭で毎回検証する。
"""

from __future__ import annotations

import collections
import csv
import glob
import json
import pathlib
import pickle
import random
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import torch

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
CKPT = "epoch=499-step=22000.ckpt"
ROOT = pathlib.Path("/Users/s19447/Desktop/saanoTTS-jp")
WORK = pathlib.Path(
    "/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/"
    "3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/phaseA"
)
WORK.mkdir(parents=True, exist_ok=True)
CACHE = WORK / "corpus_phonemes.pkl"
SR = 22050

import piper_train.vits.models as _models  # noqa: E402

assert _models.__file__.startswith(PIPER_PLUS + "/src/python"), (
    f"stale な piper_train を掴んでいる: {_models.__file__}"
)
from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402


# --------------------------------------------------------------------------
# 教師
# --------------------------------------------------------------------------
def snapshot() -> str:
    hits = glob.glob(
        "/Users/s19447/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 ckpt が HF キャッシュに無い")
    return hits[0]


def load_config() -> dict:
    return json.load(open(snapshot() + "config.json"))


def load_teacher() -> SynthesizerTrn:
    ckpt = torch.load(snapshot() + CKPT, map_location="cpu", weights_only=False)
    hp = ckpt["hyper_parameters"]
    model = SynthesizerTrn(
        n_vocab=hp["num_symbols"],
        spec_channels=hp.get("filter_length", 1024) // 2 + 1,
        segment_size=hp["segment_size"] // hp.get("hop_length", 256),
        inter_channels=hp["inter_channels"],
        hidden_channels=hp["hidden_channels"],
        filter_channels=hp["filter_channels"],
        n_heads=hp["n_heads"],
        n_layers=hp["n_layers"],
        kernel_size=hp["kernel_size"],
        p_dropout=hp["p_dropout"],
        resblock=str(hp["resblock"]),
        resblock_kernel_sizes=hp["resblock_kernel_sizes"],
        resblock_dilation_sizes=hp["resblock_dilation_sizes"],
        upsample_rates=hp["upsample_rates"],
        upsample_initial_channel=hp["upsample_initial_channel"],
        upsample_kernel_sizes=hp["upsample_kernel_sizes"],
        n_speakers=hp["num_speakers"],
        n_languages=hp["num_languages"],
        gin_channels=hp["gin_channels"],
        use_sdp=hp["use_sdp"],
        prosody_dim=hp["prosody_dim"],
        spk_embed_dim=hp["spk_embed_dim"],
    )
    sd = {
        k[len("model_g.") :]: v
        for k, v in ckpt["state_dict"].items()
        if k.startswith("model_g.")
    }
    sd, _ = normalize_checkpoint_state_dict(sd, model.state_dict())
    res = model.load_state_dict(sd, strict=False)
    assert not res.missing_keys and not res.unexpected_keys
    model.eval()
    # EMA は remove_weight_norm() の *前* に適用する（順序が逆だと効かない）
    apply_ema_shadow_params(model.dec, ckpt["ema_generator_state"]["shadow_params"])
    model.dec.remove_weight_norm()
    return model


def prosody_tensor(pv) -> torch.Tensor:
    return torch.tensor([[list(p) for p in pv]], dtype=torch.float32)


@torch.no_grad()
def durations_only(model, ids, prosody):
    """`infer()` の duration 部分だけを実行する。full infer と bit 一致（stage b で検証）。"""
    x = torch.tensor([ids])
    x_lengths = torch.tensor([len(ids)])
    lid = torch.tensor([0])
    g = model._get_global_conditioning(None, lid, speaker_embeddings=None)
    xe, _m_p, _logs_p, x_mask = model.enc_p(x, x_lengths, g=g)
    x_dp = model._prepare_prosody_input(xe, x_mask, prosody, lid=lid)
    logw = model.dp(x_dp, x_mask, g=g, reverse=True, noise_scale=0.0)
    return (torch.exp(logw) * x_mask * 1.0).squeeze(1)


@torch.no_grad()
def full_infer(model, ids, prosody):
    return model.infer(
        torch.tensor([ids]),
        torch.tensor([len(ids)]),
        lid=torch.tensor([0]),  # ja。焼き込まれていないので必須
        noise_scale=0.0,
        noise_scale_w=0.0,
        length_scale=1.0,
        prosody_features=prosody,
        speaker_embeddings=None,  # 何を渡しても無視される
    )


# --------------------------------------------------------------------------
# 音素化キャッシュ
# --------------------------------------------------------------------------
def cmd_phonemize() -> None:
    cfg = load_config()
    pim = cfg["phoneme_id_map"]
    lim = cfg.get("language_id_map") or {
        c: i for i, c in enumerate(["ja", "en", "zh", "es", "fr", "pt"])
    }
    rows = []
    for split in ("train", "heldout", "embedded"):
        with open(ROOT / f"data/splits/corpus_{split}.tsv") as f:
            r = csv.reader(f, delimiter="\t")
            next(r)
            rows += [(split, row[1], row[-1]) for row in r if row and row[-1]]
    out = []
    t0 = time.time()
    for split, uid, text in rows:
        ids, pros = text_to_phoneme_ids_and_prosody(
            text, pim, language="ja", language_id_map=lim
        )
        pv = tuple((p["a1"], p["a2"], p["a3"]) if p else (0, 0, 0) for p in pros)
        out.append(
            {"split": split, "id": uid, "text": text, "ids": tuple(ids), "pros": pv}
        )
    pickle.dump(out, open(CACHE, "wb"))
    print(f"phonemize: {len(out)} 行 / {time.time() - t0:.1f}s → {CACHE}")


def load_cache():
    if not CACHE.exists():
        raise SystemExit("先に `a2_prosody.py phonemize` を実行すること")
    return pickle.load(open(CACHE, "rb"))


# --------------------------------------------------------------------------
# トークンの役割（ckpt の phoneme_id_map から実測で引く）
# --------------------------------------------------------------------------
def token_roles(cfg):
    """id → 役割。PUA を戻して canonical なトークン名で分類する。"""
    pim = cfg["phoneme_id_map"]
    from piper_plus_g2p.encode.pua import CHAR2TOKEN  # noqa: PLC0415

    inv = {v[0]: k for k, v in pim.items()}
    name = {i: CHAR2TOKEN.get(c, c) for i, c in inv.items()}
    T = {n: i for i, n in name.items()}
    mora_end = {
        T[k]
        for k in ("a", "i", "u", "e", "o", "A", "I", "U", "E", "O",
                  "N", "N_m", "N_n", "N_ng", "N_uvular", "cl")
        if k in T
    }
    # 中国語 / 英語へ誤ルーティングされた行を弾くための印
    foreign = {
        i for i, n in name.items()
        if n.startswith("tone")
        or n in ("æ", "ɑ", "ə", "ɛ", "ɪ", "ɹ", "ʊ", "ʌ", "ˈ", "ˌ", "ː")
    }
    return {
        "name": name, "PAD": T["_"], "RISE": T["["], "FALL": T["]"],
        "HASH": T["#"], "BOS": T["^"], "mora_end": mora_end, "foreign": foreign,
        "nonmora": {T["_"], T["["], T["]"], T["#"], T["^"]}
        | {T[k] for k in ("$", "?", "!") if k in T},
    }


def split_phrases(ids, pros):
    """実 prosody の a2 リセットでアクセント句に切る（正解側の分割）。"""
    phrases = []
    cur = None
    last = None
    for i, p in enumerate(pros):
        if p[1] == 0:
            continue
        if p != last:
            if p[1] == 1:
                if cur:
                    phrases.append(cur)
                cur = {"start": i, "end": i, "moras": [p]}
            elif cur is not None:
                cur["moras"].append(p)
            else:
                cur = {"start": i, "end": i, "moras": [p]}
            last = p
        if cur is not None:
            cur["end"] = i
    if cur is not None:
        phrases.append(cur)
    for ph in phrases:
        seg = ids[ph["start"] : ph["end"] + 1]
        ph["has_fall"] = 9 in seg
        ph["has_rise"] = 8 in seg
        n = len(ph["moras"])
        ph["n_moras"] = n
        ph["accent_type"] = ph["moras"][0][1] - ph["moras"][0][0]
        ph["heiban"] = ph["accent_type"] == 0
        ph["odaka"] = ph["accent_type"] == n
        ph["consistent"] = (
            [m[1] for m in ph["moras"]] == list(range(1, n + 1))
            and all(m[2] == n - m[1] + 1 for m in ph["moras"])
            and all(m[0] == m[1] - ph["accent_type"] for m in ph["moras"])
        )
    return phrases


def decode_prosody(ids, R):
    """**音素ID列だけ**から A1/A2/A3 を復元する。

    デバイスが持つ情報（= 音素ID列）だけで prosody がどこまで決まるかを測るため。
    規則:
      - モーラ = 子音* + モーラ終端トークン（母音 / N* / cl）
      - `[` はアクセント句の第 1 モーラの直後に立つ → 句頭が分かる
      - `#` はアクセント句境界（モーラ内部にも現れるのでモーラは切らない）
      - `]` は核の直後 → アクセント型。`]` が無い句は尾高（型 = モーラ数）
    """
    moras, cur, marks = [], [], []
    for idx, i in enumerate(ids):
        if i == R["PAD"]:
            continue
        if i in (R["RISE"], R["FALL"]):
            marks.append((len(moras), i))
            continue
        if i == R["HASH"]:
            marks.append((len(moras), R["HASH"]))
            continue
        if i in R["nonmora"]:
            if cur:
                moras.append(cur)
                cur = []
            continue
        cur.append(idx)
        if i in R["mora_end"]:
            moras.append(cur)
            cur = []
    if cur:
        moras.append(cur)
    n = len(moras)
    starts = {0} if n else set()
    for after, kind in marks:
        if kind == R["RISE"] and after >= 1:
            starts.add(after - 1)
        elif kind == R["HASH"]:
            starts.add(after)
    starts = sorted(s for s in starts if s < n)
    out = [(0, 0, 0)] * len(ids)
    for si, s in enumerate(starts):
        e = starts[si + 1] - 1 if si + 1 < len(starts) else n - 1
        length = e - s + 1
        typ = length  # `]` が無ければ尾高
        for after, kind in marks:
            if kind == R["FALL"] and s < after <= e + 1:
                typ = after - s
                break
        for j in range(s, e + 1):
            a2 = j - s + 1
            for t in moras[j]:
                out[t] = (a2 - typ, a2, length - a2 + 1)
    return tuple(out)


# --------------------------------------------------------------------------
# stage a
# --------------------------------------------------------------------------
def stage_a() -> dict:
    rows = load_cache()
    R = token_roles(load_config())
    print(f"[stage a] 行数 {len(rows)}")

    ja = [r for r in rows if not any(i in R["foreign"] for i in r["ids"])]
    n_zh = sum(
        1 for r in rows
        if any(R["name"].get(i, "").startswith("tone") for i in r["ids"])
    )
    print(f"  中国語へ誤ルーティング {n_zh} 行 / 純日本語 {len(ja)} 行")

    by_ids = collections.defaultdict(list)
    for r in rows:
        by_ids[r["ids"]].append(r)
    dups = {k: v for k, v in by_ids.items() if len(v) > 1}
    conflicts = [(k, v) for k, v in dups.items() if len({r["pros"] for r in v}) > 1]
    print(f"  unique 音素ID列 {len(by_ids)} / 重複グループ {len(dups)} / "
          f"prosody が食い違う重複 {len(conflicts)}")

    n_hash = sum(r["ids"].count(R["HASH"]) for r in rows)
    n_rise = sum(r["ids"].count(R["RISE"]) for r in rows)
    n_fall = sum(r["ids"].count(R["FALL"]) for r in rows)
    print(f"  記号出現: '#'={n_hash}  '['={n_rise}  ']'={n_fall}")

    cnt = collections.Counter()
    for r in ja:
        for ph in split_phrases(r["ids"], r["pros"]):
            cnt["phrases"] += 1
            if not ph["consistent"]:
                cnt["inconsistent"] += 1
                continue
            cnt["with_fall" if ph["has_fall"] else "no_fall"] += 1
            if not ph["has_fall"]:
                cnt["no_fall_heiban" if ph["heiban"]
                    else "no_fall_odaka" if ph["odaka"] else "no_fall_other"] += 1
            if ph["n_moras"] == 1 and not ph["has_rise"]:
                cnt["one_mora_no_rise"] += 1
    print(f"  アクセント句 {cnt['phrases']}: ']' あり {cnt['with_fall']} / なし "
          f"{cnt['no_fall']}（平板 {cnt['no_fall_heiban']} / 尾高 "
          f"{cnt['no_fall_odaka']} / 他 {cnt['no_fall_other']}）")
    print(f"  '[' を持たない 1 モーラ句 = ID から見えない句境界: "
          f"{cnt['one_mora_no_rise']} ({cnt['one_mora_no_rise'] / cnt['phrases'] * 100:.2f}%)")

    # 音素ID列だけから prosody を復元できるか
    exact = tok_ok = tok_all = 0
    for r in ja:
        dec = decode_prosody(list(r["ids"]), R)
        exact += dec == r["pros"]
        tok_ok += sum(1 for a, b in zip(dec, r["pros"]) if a == b)
        tok_all += len(r["pros"])
    print(f"  ID 列のみからの prosody 復元: 文単位 {exact}/{len(ja)} = "
          f"{exact / len(ja) * 100:.2f}%  /  token {tok_ok / tok_all * 100:.3f}%")

    return {
        "rows": len(rows),
        "rows_misrouted_to_zh": n_zh,
        "rows_pure_ja": len(ja),
        "unique_id_sequences": len(by_ids),
        "duplicate_groups": len(dups),
        "duplicate_rows": sum(len(v) for v in dups.values()),
        "duplicate_groups_with_prosody_conflict": len(conflicts),
        "token_counts": {"hash_7": n_hash, "rise_8": n_rise, "fall_9": n_fall},
        "accent_phrases": dict(cnt),
        "prosody_from_ids_only": {
            "sentence_exact_pct": round(exact / len(ja) * 100, 3),
            "token_exact_pct": round(tok_ok / tok_all * 100, 4),
            "n_rows": len(ja),
        },
    }


# --------------------------------------------------------------------------
# stage b
# --------------------------------------------------------------------------
def pick_heldout(rows, n, seed=20260826, min_ids=40):
    cands = [r for r in rows if r["split"] == "heldout" and len(r["ids"]) >= min_ids]
    rng = random.Random(seed)
    rng.shuffle(cands)
    return cands[:n]


def stage_b(n=300) -> dict:
    rows = load_cache()
    model = load_teacher()

    # 高速経路が full infer と bit 一致することを毎回検証する
    for r in rows[:3]:
        pt = prosody_tensor(r["pros"])
        assert torch.equal(
            full_infer(model, list(r["ids"]), pt).durations,
            durations_only(model, list(r["ids"]), pt),
        ), "duration-only 高速経路が full infer と一致しない"
    print("[stage b] duration-only 高速経路 == full infer(): bit 一致 (3/3)")

    sample = pick_heldout(rows, n)
    print(f"[stage b] held-out {len(sample)} 文")

    rec = []
    t0 = time.time()
    for k, r in enumerate(sample):
        ids = list(r["ids"])
        d_real = durations_only(model, ids, prosody_tensor(r["pros"]))
        d_zero = durations_only(
            model, ids, torch.zeros(1, len(ids), 3, dtype=torch.float32)
        )
        d_none = durations_only(model, ids, None)
        rec.append(
            {
                "id": r["id"],
                "n_ids": len(ids),
                "real": d_real[0].tolist(),
                "zero": d_zero[0].tolist(),
                "none": d_none[0].tolist(),
            }
        )
        if (k + 1) % 50 == 0:
            print(f"   {k + 1}/{len(sample)}  {time.time() - t0:.0f}s", flush=True)
    pickle.dump(rec, open(WORK / "stage_b.pkl", "wb"))

    def frames(x):
        return float(np.ceil(np.asarray(x)).sum())

    out = {"n": len(rec), "conditions": {}}
    base = np.array([frames(r["real"]) for r in rec])
    for cond in ("zero", "none"):
        other = np.array([frames(r[cond]) for r in rec])
        rel = (other - base) / base * 100
        # 音素ごとの duration 差
        per = np.concatenate(
            [np.asarray(r[cond]) - np.asarray(r["real"]) for r in rec]
        )
        perr = np.concatenate(
            [
                (np.asarray(r[cond]) - np.asarray(r["real"]))
                / np.maximum(np.asarray(r["real"]), 1e-6)
                * 100
                for r in rec
            ]
        )
        out["conditions"][cond] = {
            "total_frames_delta_pct": {
                "mean": round(float(rel.mean()), 3),
                "median": round(float(np.median(rel)), 3),
                "sd": round(float(rel.std()), 3),
                "p5": round(float(np.percentile(rel, 5)), 3),
                "p95": round(float(np.percentile(rel, 95)), 3),
                "min": round(float(rel.min()), 3),
                "max": round(float(rel.max()), 3),
            },
            "rows_identical_total_frames": int((other == base).sum()),
            "per_token_delta_frames": {
                "mean_abs": round(float(np.abs(per).mean()), 4),
                "median_abs": round(float(np.median(np.abs(per))), 4),
                "p95_abs": round(float(np.percentile(np.abs(per), 95)), 4),
                "max_abs": round(float(np.abs(per).max()), 4),
            },
            "per_token_delta_pct": {
                "mean_abs": round(float(np.abs(perr).mean()), 3),
                "median_abs": round(float(np.median(np.abs(perr))), 3),
                "p95_abs": round(float(np.percentile(np.abs(perr), 95)), 3),
            },
        }
        print(f"  {cond:5s} vs real: 総フレーム {rel.mean():+.2f}% "
              f"(sd {rel.std():.2f}, p5 {np.percentile(rel, 5):+.2f}, "
              f"p95 {np.percentile(rel, 95):+.2f}) / "
              f"token 平均絶対差 {np.abs(per).mean():.3f} frame "
              f"({np.abs(perr).mean():.1f}%)")
    return out


# --------------------------------------------------------------------------
# stage c
# --------------------------------------------------------------------------
def flip_heiban_odaka(ids, pros):
    """']' を持たないアクセント句の a1 を 平板 ⇄ 尾高 に入れ替える。

    音素ID列は 1 bit も変えない。ID 列からは区別できない prosody を作る。
    """
    pros = list(pros)
    phs = split_phrases(ids, pros)
    changed = 0
    for ph in phs:
        if ph["has_fall"]:
            continue
        n = ph["n_moras"]
        if ph["heiban"]:
            new_type = n  # 平板 → 尾高
        elif ph["odaka"]:
            new_type = 0  # 尾高 → 平板
        else:
            continue
        for i in range(ph["start"], ph["end"] + 1):
            a1, a2, a3 = pros[i]
            if a2 == 0:
                continue
            pros[i] = (a2 - new_type, a2, a3)
        changed += 1
    return tuple(pros), changed


def stage_c(n=300) -> dict:
    rows = load_cache()
    model = load_teacher()
    sample = pick_heldout(rows, n)
    rng = random.Random(20260827)

    # c2 用: 同じトークン数の別文を探す
    by_len = collections.defaultdict(list)
    for r in rows:
        by_len[len(r["ids"])].append(r)

    c1_tot, c1_tok, c1_changed, c1_used = [], [], [], 0
    c2_tot, c2_tok, c2_used = [], [], 0
    t0 = time.time()
    for k, r in enumerate(sample):
        ids = list(r["ids"])
        d_real = durations_only(model, ids, prosody_tensor(r["pros"]))
        base = float(np.ceil(d_real[0].numpy()).sum())

        flipped, changed = flip_heiban_odaka(r["ids"], r["pros"])
        if changed:
            d = durations_only(model, ids, prosody_tensor(flipped))
            c1_tot.append((float(np.ceil(d[0].numpy()).sum()) - base) / base * 100)
            c1_tok.append(np.abs(d[0].numpy() - d_real[0].numpy()))
            c1_changed.append(changed)
            c1_used += 1

        alts = [o for o in by_len[len(ids)] if o["id"] != r["id"]]
        if alts:
            alt = rng.choice(alts)
            d = durations_only(model, ids, prosody_tensor(alt["pros"]))
            c2_tot.append((float(np.ceil(d[0].numpy()).sum()) - base) / base * 100)
            c2_tok.append(np.abs(d[0].numpy() - d_real[0].numpy()))
            c2_used += 1
        if (k + 1) % 50 == 0:
            print(f"   {k + 1}/{len(sample)}  {time.time() - t0:.0f}s", flush=True)

    def summarize(tot, tok):
        tot = np.asarray(tot)
        tok = np.concatenate(tok) if tok else np.zeros(1)
        return {
            "n": int(len(tot)),
            "total_frames_delta_pct": {
                "mean_abs": round(float(np.abs(tot).mean()), 3),
                "median_abs": round(float(np.median(np.abs(tot))), 3),
                "p95_abs": round(float(np.percentile(np.abs(tot), 95)), 3),
                "max_abs": round(float(np.abs(tot).max()), 3),
                "mean_signed": round(float(tot.mean()), 3),
            },
            "per_token_delta_frames": {
                "mean_abs": round(float(tok.mean()), 4),
                "p95_abs": round(float(np.percentile(tok, 95)), 4),
                "max_abs": round(float(tok.max()), 4),
            },
        }

    out = {
        "c1_heiban_odaka_flip": summarize(c1_tot, c1_tok),
        "c2_prosody_transplant_same_length": summarize(c2_tot, c2_tok),
    }
    out["c1_heiban_odaka_flip"]["phrases_flipped_per_row_mean"] = round(
        float(np.mean(c1_changed)), 2
    )
    print(f"[stage c] c1 (平板⇄尾高, ID固定) n={c1_used}: "
          f"総フレーム |Δ| 平均 "
          f"{out['c1_heiban_odaka_flip']['total_frames_delta_pct']['mean_abs']}%")
    print(f"          c2 (別文の prosody 移植) n={c2_used}: 総フレーム |Δ| 平均 "
          f"{out['c2_prosody_transplant_same_length']['total_frames_delta_pct']['mean_abs']}%")
    return out


# --------------------------------------------------------------------------
# stage d — UTMOS
# --------------------------------------------------------------------------
def stage_d(n=32) -> dict:
    import soundfile as sf

    rows = load_cache()
    model = load_teacher()
    # 3〜8 秒相当を長さで層化して選ぶ（B-5 と同じ設計。短尺は MOS 推定器を不安定にする）
    cands = [
        r
        for r in rows
        if r["split"] == "heldout" and 90 <= len(r["ids"]) <= 220
    ]
    cands.sort(key=lambda r: len(r["ids"]))
    step = max(1, len(cands) // n)
    sample = cands[::step][:n]

    outdir = ROOT / "reports/a2_prosody_wav"
    outdir.mkdir(parents=True, exist_ok=True)
    pad = np.zeros(int(0.3 * SR), dtype=np.float32)
    utts = []
    for r in sample:
        ids = list(r["ids"])
        wavs = {}
        for cond, pt in (
            ("real", prosody_tensor(r["pros"])),
            ("zero", torch.zeros(1, len(ids), 3, dtype=torch.float32)),
        ):
            out = full_infer(model, ids, pt)
            y = out.audio[0, 0].numpy().astype(np.float32)
            path = outdir / f"{r['id']}_{cond}.wav"
            sf.write(path, np.concatenate([pad, y, pad]), SR)
            wavs[cond] = {"wav": str(path), "sec": len(y) / SR}
        utts.append({"id": r["id"], "text": r["text"], **wavs})
        print(f"   {r['id']} real {wavs['real']['sec']:.2f}s / "
              f"zero {wavs['zero']['sec']:.2f}s", flush=True)

    predictor = torch.hub.load(
        "tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True
    )
    predictor.eval()
    import torchaudio

    def utmos(path):
        wav, sr = sf.read(path, dtype="float32", always_2d=True)
        t = torch.from_numpy(wav[:, 0]).unsqueeze(0)
        if sr != 16000:
            t = torchaudio.transforms.Resample(sr, 16000)(t)
        with torch.no_grad():
            return float(predictor(t, sr=16000).item())

    for u in utts:
        for cond in ("real", "zero"):
            u[cond]["utmos"] = round(utmos(u[cond]["wav"]), 4)

    res = {"n": len(utts), "utterances": utts, "summary": {}}
    for cond in ("real", "zero"):
        v = np.array([u[cond]["utmos"] for u in utts])
        s = np.array([u[cond]["sec"] for u in utts])
        res["summary"][cond] = {
            "utmos_mean": round(float(v.mean()), 4),
            "utmos_median": round(float(np.median(v)), 4),
            "utmos_sd": round(float(v.std()), 4),
            "sec_mean": round(float(s.mean()), 3),
        }
    d = np.array([u["zero"]["utmos"] - u["real"]["utmos"] for u in utts])
    sd = np.array([u["zero"]["sec"] - u["real"]["sec"] for u in utts])
    res["summary"]["zero_minus_real"] = {
        "utmos_mean": round(float(d.mean()), 4),
        "utmos_sd": round(float(d.std()), 4),
        "utmos_median": round(float(np.median(d)), 4),
        "n_zero_better": int((d > 0).sum()),
        "n_real_better": int((d < 0).sum()),
        "sec_mean": round(float(sd.mean()), 4),
        "sec_pct_mean": round(
            float(
                np.mean(
                    [
                        (u["zero"]["sec"] - u["real"]["sec"]) / u["real"]["sec"] * 100
                        for u in utts
                    ]
                )
            ),
            3,
        ),
    }
    # 対応のある t 検定（正規性は仮定しない目安。符号検定も併記）
    from math import sqrt

    tstat = float(d.mean() / (d.std(ddof=1) / sqrt(len(d)))) if d.std() > 0 else 0.0
    res["summary"]["paired_t"] = round(tstat, 3)
    print(f"[stage d] UTMOS real {res['summary']['real']['utmos_mean']:.3f} / "
          f"zero {res['summary']['zero']['utmos_mean']:.3f} / "
          f"差 {d.mean():+.4f} (sd {d.std():.4f}, t={tstat:+.2f})")
    return res


# --------------------------------------------------------------------------
def main(argv):
    stages = argv[1:] or ["a", "b", "c"]
    report = ROOT / "reports/a2_prosody.json"
    data = json.loads(report.read_text()) if report.exists() else {}
    for s in stages:
        if s == "phonemize":
            cmd_phonemize()
        elif s == "a":
            data["stage_a_corpus"] = stage_a()
        elif s == "b":
            data["stage_b_dT_conditions"] = stage_b()
        elif s == "c":
            data["stage_c_id_fixed_prosody_perturbation"] = stage_c()
        elif s == "d":
            data["stage_d_utmos"] = stage_d()
        elif s == "merge":
            pass
        else:
            raise SystemExit(f"unknown stage: {s}")
    if data:
        report.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"→ {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
