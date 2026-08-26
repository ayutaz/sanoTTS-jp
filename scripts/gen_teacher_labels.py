#!/usr/bin/env python3
"""教師ラベルを生成してパックに書く（Phase B-b）。

**入力はかな中間表現**（Phase A-1 の決定）。デバイスが実際に使う変換器
`kana_g2p` を通して音素IDを作るので、**生徒が学ぶ入力とデバイスの出力が一致する**。
これにより B-1（かな無し行 5.36% が中国語音素になる問題）が構造的に消える。

```
漢字文 --[OpenJTalk]--> 中間表現 --[kana_g2p]--> 音素ID --> 教師 --> (dT, zT, yT)
                                     ^ デバイスと同じ
```

教師の呼び方は `.claude/skills/teacher-inference/SKILL.md` の 6 項目に従う。

実行:
    uv run python scripts/gen_teacher_labels.py --split heldout --limit 100 --out /tmp/pack
    uv run python scripts/gen_teacher_labels.py --split train --out data/pack   # 本番
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import platform
import subprocess
import sys
import time
import warnings

import numpy as np
import torch

warnings.filterwarnings("ignore")

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
sys.path.insert(0, f"{PIPER_PLUS}/src/python")
sys.path.insert(0, f"{PIPER_PLUS}/src/python/g2p")

import kana_g2p as K  # noqa: E402
from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402
from piper_plus_g2p.encode import pua  # noqa: E402
from saanotts_jp.labelpack import GateFailure, PackWriter, Utterance  # noqa: E402

import piper_train.vits.models as _models  # noqa: E402

assert _models.__file__.startswith(PIPER_PLUS + "/src/python"), _models.__file__

CKPT = "epoch=499-step=22000.ckpt"


def snapshot() -> str:
    hits = glob.glob(
        "/Users/s19447/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 ckpt が HF キャッシュに無い")
    return hits[0]


def build_teacher(ckpt: dict) -> SynthesizerTrn:
    """teacher-inference skill の手順どおり。**EMA は remove_weight_norm の前**。"""
    hp = ckpt["hyper_parameters"]
    model = SynthesizerTrn(
        n_vocab=hp["num_symbols"], spec_channels=hp.get("filter_length", 1024) // 2 + 1,
        segment_size=hp["segment_size"] // hp.get("hop_length", 256),
        inter_channels=hp["inter_channels"], hidden_channels=hp["hidden_channels"],
        filter_channels=hp["filter_channels"], n_heads=hp["n_heads"],
        n_layers=hp["n_layers"], kernel_size=hp["kernel_size"], p_dropout=hp["p_dropout"],
        resblock=str(hp["resblock"]), resblock_kernel_sizes=hp["resblock_kernel_sizes"],
        resblock_dilation_sizes=hp["resblock_dilation_sizes"],
        upsample_rates=hp["upsample_rates"],
        upsample_initial_channel=hp["upsample_initial_channel"],
        upsample_kernel_sizes=hp["upsample_kernel_sizes"],
        n_speakers=hp["num_speakers"], n_languages=hp["num_languages"],
        gin_channels=hp["gin_channels"], use_sdp=hp["use_sdp"],
        prosody_dim=hp["prosody_dim"], spk_embed_dim=hp["spk_embed_dim"],
    )
    sd = {k[len("model_g.") :]: v for k, v in ckpt["state_dict"].items()
          if k.startswith("model_g.")}
    sd, _ = normalize_checkpoint_state_dict(sd, model.state_dict())
    res = model.load_state_dict(sd, strict=False)
    assert not res.missing_keys and not res.unexpected_keys
    model.eval()
    apply_ema_shadow_params(model.dec, ckpt["ema_generator_state"]["shadow_params"])
    model.dec.remove_weight_norm()
    return model


def encode_intermediate(tokens: list[str], pim: dict, eos: str = "$") -> list[int]:
    """中間表現のトークン列 → 音素ID列。

    **`PiperEncoder._post_process` (encoder.py:172-204) と同一の規則にする。**
    教師が学習時に見た形と違う並びを渡すと、例外は出ないが duration がずれる。

    規則:
      1. 各音素の後ろに PAD を 1 つ挟む。ただし **その音素自身が PAD なら挟まない**
         （`_` が連続しない。ここを間違えると PAD が倍に増える）
      2. 先頭に `^` + PAD、末尾に EOS
    """
    pad = pim["_"][0]
    phonemes = K.intermediate_to_phonemes(tokens, ENCODE_TABLE)

    body: list[int] = []
    for p in phonemes:
        ch = pua.TOKEN2CHAR.get(p, p)
        if ch not in pim:
            raise KeyError(f"音素 {p!r} が phoneme_id_map に無い")
        pid = pim[ch][0]
        body.append(pid)
        if pid != pad:                 # ← PAD の後に PAD を入れない
            body.append(pad)

    return [pim["^"][0], pad] + body + [pim[eos][0]]


ENCODE_TABLE: dict[str, list[str]] = {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="heldout",
                    choices=["train", "heldout", "embedded"])
    ap.add_argument("--limit", type=int, default=0, help="0 = 全件")
    ap.add_argument("--out", required=True)
    ap.add_argument("--utts-per-shard", type=int, default=128)
    args = ap.parse_args()

    global ENCODE_TABLE
    ENCODE_TABLE = K.build_mora_table()
    print(f"mora テーブル {len(ENCODE_TABLE)} エントリ / "
          f"{K.table_size_bytes(ENCODE_TABLE)} B")

    snap = snapshot()
    ckpt = torch.load(snap + CKPT, map_location="cpu", weights_only=False)
    config = json.load(open(snap + "config.json"))
    pim = config["phoneme_id_map"]
    num_symbols = ckpt["hyper_parameters"]["num_symbols"]

    print("教師を構築（EMA 適用）…")
    teacher = build_teacher(ckpt)

    rows = [r for r in csv.reader(
        open(f"data/splits/corpus_{args.split}.tsv"), delimiter="\t") if r and r[-1]]
    if args.limit:
        rows = rows[: args.limit]
    print(f"{args.split}: {len(rows):,} 行")

    writer = PackWriter(args.out, utts_per_shard=args.utts_per_shard)
    rejected: list[dict] = []
    t0 = time.perf_counter()

    for i, row in enumerate(rows):
        text = row[-1]
        uid = row[1] if len(row) >= 3 else f"{args.split}_{i:06d}"
        source = row[0] if len(row) >= 3 else args.split
        try:
            tokens = K.text_to_intermediate(text, ENCODE_TABLE)
            ids = encode_intermediate(tokens, pim)
            if max(ids) >= num_symbols:
                raise KeyError(f"音素ID {max(ids)} >= num_symbols {num_symbols}")
        except KeyError as exc:
            rejected.append({"uid": uid, "text": text, "stage": "g2p", "why": str(exc)})
            continue

        # prosody は中間表現が持たないので、A-2 の決定に従い**教師には実値を渡す**。
        # ⚠️ A-2 は反証を通っていない（phase-a-decisions.md §3）。
        # 中間表現から A1/A2/A3 は復元できないので、ここではゼロを渡す。
        # 実 prosody を使う場合は漢字文から別途取る必要がある（B-c で決着させる）。
        prosody = torch.zeros(1, len(ids), 3)

        with torch.no_grad():
            out = teacher.infer(
                torch.tensor([ids]), torch.tensor([len(ids)]),
                lid=torch.tensor([0]),          # ja。焼き込まれていないので必須
                noise_scale=0.0, noise_scale_w=0.0, length_scale=1.0,
                prosody_features=prosody,
                speaker_embeddings=None,        # 何を渡しても無視される（M-3.1）
            )

        u = Utterance(
            ids=np.asarray(ids, dtype=np.int32),
            dT=out.durations[0].numpy().astype(np.float32),
            prosody=prosody[0].numpy().astype(np.int16),
            zT=out.latents[0][0].numpy().astype(np.float32),
            yT=out.audio.squeeze().numpy().astype(np.float32),
            text=text, source=source, uid=uid,
        )
        try:
            writer.add(u)
        except GateFailure as exc:
            rejected.append({"uid": uid, "text": text, "stage": "gate", "why": str(exc)})

        if (i + 1) % 200 == 0:
            el = time.perf_counter() - t0
            print(f"  {i+1:>6}/{len(rows)}  {el/(i+1)*1000:.0f} ms/文  "
                  f"採用 {writer._n}  棄却 {len(rejected)}")

    elapsed = time.perf_counter() - t0
    manifest = writer.close({
        "generator": {
            "script": "scripts/gen_teacher_labels.py",
            "argv": sys.argv[1:],
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip(),
        },
        "environment": {
            "python": platform.python_version(), "torch": torch.__version__,
            "numpy": np.__version__, "platform": platform.platform(),
            "device": "cpu",
        },
        "teacher": {
            "repo": "ayousanz/piper-plus-zero-shot-tsukuyomi", "file": CKPT,
            "snapshot": snap.rstrip("/").split("/")[-1],
            "ema_applied": True, "speaker_embeddings": None, "lid": 0,
            "noise_scale": 0.0, "noise_scale_w": 0.0, "length_scale": 1.0,
            "prosody": "zeros (A-2 未決着。B-c で決める)",
        },
        "corpus": {"split": args.split, "rows_read": len(rows),
                   "mora_table_entries": len(ENCODE_TABLE)},
        "input_path": "kanji -> intermediate (kana_g2p) -> phoneme ids",
        "rejected": rejected[:200],
        "n_rejected": len(rejected),
        "elapsed_sec": round(elapsed, 1),
    })

    print(f"\n採用 {manifest['n_utterances']:,} / 棄却 {len(rejected)} "
          f"/ {manifest['n_shards']} shard / {elapsed/max(len(rows),1)*1000:.0f} ms/文")
    if manifest["pack_gate_problems"]:
        for p in manifest["pack_gate_problems"]:
            print(f"  ⚠️ {p}")
    if rejected:
        from collections import Counter
        for why, n in Counter(r["why"][:40] for r in rejected).most_common(5):
            print(f"  棄却: {why} — {n} 件")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
