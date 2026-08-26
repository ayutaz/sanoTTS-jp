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
# アクセント句の切り出し
# --------------------------------------------------------------------------
def split_phrases(ids, pros):
    """(a1,a2,a3) の a2 リセットでアクセント句に切る。

    Returns: list of dict(start, end, moras=[(a1,a2,a3)...], has_fall)
    `has_fall` はその句に `]` (id 9) が含まれるか。
    """
    phrases = []
    cur = None
    for i, (tid, p) in enumerate(zip(ids, pros)):
        a1, a2, a3 = p
        if a2 == 0:  # prosody なし（pad / 記号 / BOS / EOS）
            continue
        if cur is None or a2 < cur["moras"][-1][1] or a2 == cur["moras"][-1][1] - 0:
            pass
        if cur is not None and a2 <= cur["moras"][-1][1] and p != cur["moras"][-1]:
            if a2 == 1:
                phrases.append(cur)
                cur = None
        if cur is None:
            cur = {"start": i, "end": i, "moras": [p]}
        else:
            if p != cur["moras"][-1]:
                cur["moras"].append(p)
            cur["end"] = i
    if cur is not None:
        phrases.append(cur)
    for ph in phrases:
        ph["has_fall"] = 9 in ids[ph["start"] : ph["end"] + 1]
        ph["has_rise"] = 8 in ids[ph["start"] : ph["end"] + 1]
        n = len(ph["moras"])
        ph["n_moras"] = n
        a1s = [m[0] for m in ph["moras"]]
        a2s = [m[1] for m in ph["moras"]]
        ph["accent_type"] = a2s[0] - a1s[0]  # a1 = a2 - accent_type
        ph["heiban"] = ph["accent_type"] == 0
        ph["odaka"] = ph["accent_type"] == n
    return phrases


# --------------------------------------------------------------------------
# stage a
# --------------------------------------------------------------------------
def stage_a() -> dict:
    rows = load_cache()
    print(f"[stage a] 行数 {len(rows)}")

    by_ids = collections.defaultdict(list)
    for r in rows:
        by_ids[r["ids"]].append(r)
    dups = {k: v for k, v in by_ids.items() if len(v) > 1}
    conflicts = [
        (k, v) for k, v in dups.items() if len({r["pros"] for r in v}) > 1
    ]
    print(f"  unique 音素ID列 {len(by_ids)} / 重複グループ {len(dups)} / "
          f"prosody が食い違う重複 {len(conflicts)}")

    # `#` (id 7) は実際に出るか
    n_hash = sum(r["ids"].count(7) for r in rows)
    n_rise = sum(r["ids"].count(8) for r in rows)
    n_fall = sum(r["ids"].count(9) for r in rows)
    print(f"  記号出現: '#'={n_hash}  '['={n_rise}  ']'={n_fall}")

    # アクセント句の分類
    cnt = collections.Counter()
    amb_rows = 0
    for r in rows:
        phs = split_phrases(r["ids"], r["pros"])
        row_amb = False
        for ph in phs:
            cnt["phrases"] += 1
            if ph["has_fall"]:
                cnt["with_fall"] += 1
            else:
                cnt["no_fall"] += 1
                if ph["heiban"]:
                    cnt["no_fall_heiban"] += 1
                    row_amb = True
                elif ph["odaka"]:
                    cnt["no_fall_odaka"] += 1
                    row_amb = True
                else:
                    cnt["no_fall_other"] += 1
        amb_rows += row_amb
    print(f"  アクセント句 {cnt['phrases']}  ']' あり {cnt['with_fall']}  "
          f"']' なし {cnt['no_fall']}")
    print(f"    ']' なしの内訳: 平板 {cnt['no_fall_heiban']} / 尾高 "
          f"{cnt['no_fall_odaka']} / その他 {cnt['no_fall_other']}")
    print(f"  平板/尾高の曖昧性を含む行: {amb_rows} ({amb_rows / len(rows) * 100:.1f}%)")

    return {
        "rows": len(rows),
        "unique_id_sequences": len(by_ids),
        "duplicate_groups": len(dups),
        "duplicate_rows": sum(len(v) for v in dups.values()),
        "duplicate_groups_with_prosody_conflict": len(conflicts),
        "token_counts": {"hash_7": n_hash, "rise_8": n_rise, "fall_9": n_fall},
        "accent_phrases": dict(cnt),
        "rows_with_heiban_odaka_ambiguity": amb_rows,
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
