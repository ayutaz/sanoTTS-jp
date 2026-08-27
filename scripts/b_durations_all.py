#!/usr/bin/env python3
"""教師の duration だけを全コーパスに対して取る（B-4 / B-7 / B-8 / B-9 の共通土台）。

`infer()` のうち **decoder と flow を通らない前半だけ**を実行する。
`models.py:1037-1050` の 5 行と同一で、`w = exp(logw) * x_mask * length_scale`
までが duration の全て。decoder を飛ばすので 1 文あたりのコストが桁で下がり、
23,457 行を現実的な時間で回せる。

⚠️ **「同一のはず」で済ませない。** `--verify N` で full `infer()` と
**bit 完全一致**を照合してから本走行する（既定で 64 文）。

実行:
    uv run python scripts/b_durations_all.py --verify 64 --out reports/durations
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import sys
import time
import warnings

import numpy as np
import torch

warnings.filterwarnings("ignore")

import os

#: piper-plus の checkout。**環境変数で差し替えられる**（他人の環境でも動くように）。
#: 既定は開発者のローカルパスだが、clone した人は `PIPER_PLUS_ROOT` を設定する。
PIPER_PLUS = os.environ.get("PIPER_PLUS_ROOT",
                            os.path.expanduser("~/Documents/piper-plus"))
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import kana_g2p as K  # noqa: E402
import gen_teacher_labels as G  # noqa: E402


@torch.no_grad()
def duration_only(teacher, ids: list[int]) -> torch.Tensor:
    """`infer()` の duration 部分だけ。models.py:1037-1050 と同一。"""
    x = torch.tensor([ids])
    x_lengths = torch.tensor([len(ids)])
    lid = torch.tensor([0])
    prosody = torch.zeros(1, len(ids), 3)          # A-2: zeros（D-014）
    g = teacher._get_global_conditioning(None, lid, speaker_embeddings=None)
    x_e, m_p, logs_p, x_mask = teacher.enc_p(x, x_lengths, g=g)
    x_dp = teacher._prepare_prosody_input(x_e, x_mask, prosody, lid=lid)
    logw = teacher.dp(x_dp, x_mask, g=g, reverse=True, noise_scale=0.0)
    w = torch.exp(logw) * x_mask * 1.0
    return w.squeeze(1)[0]


@torch.no_grad()
def full_infer_durations(teacher, ids: list[int]) -> torch.Tensor:
    out = teacher.infer(
        torch.tensor([ids]), torch.tensor([len(ids)]),
        lid=torch.tensor([0]), noise_scale=0.0, noise_scale_w=0.0,
        length_scale=1.0, prosody_features=torch.zeros(1, len(ids), 3),
        speaker_embeddings=None,
    )
    return out.durations[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="train,heldout,embedded")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verify", type=int, default=64)
    ap.add_argument("--out", default="reports/durations")
    args = ap.parse_args()

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    snap = G.snapshot()
    ckpt = torch.load(snap + G.CKPT, map_location="cpu", weights_only=False)
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]
    num_symbols = ckpt["hyper_parameters"]["num_symbols"]
    teacher = G.build_teacher(ckpt)
    print(f"教師 ready / mora テーブル {len(table)} エントリ")

    rows: list[tuple[str, str, str]] = []
    for sp in args.splits.split(","):
        for r in csv.reader(open(f"data/splits/corpus_{sp}.tsv"), delimiter="\t"):
            if r and r[-1] and r[0] != "source":
                rows.append((sp, r[1] if len(r) >= 3 else "", r[-1]))
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows):,} 行")

    # --- 照合: duration_only == full infer（bit 完全一致でなければ止める） ---
    n_ver = 0
    for sp, uid, text in rows:
        if n_ver >= args.verify:
            break
        try:
            ids = G.encode_intermediate(K.text_to_intermediate(text, table), pim)
        except KeyError:
            continue
        if max(ids) >= num_symbols:
            continue
        a, b = duration_only(teacher, ids), full_infer_durations(teacher, ids)
        if not torch.equal(a, b):
            raise SystemExit(
                f"NG: duration_only が infer() と一致しない ({uid}) "
                f"max|diff|={float((a-b).abs().max()):.3e}")
        n_ver += 1
    print(f"✅ duration_only == infer() が {n_ver} 文で bit 完全一致")

    # --- 本走行 ---
    all_ids: list[np.ndarray] = []
    all_d: list[np.ndarray] = []
    index: list[dict] = []
    rejected: list[dict] = []
    t0 = time.perf_counter()
    off = 0
    for i, (sp, uid, text) in enumerate(rows):
        try:
            tokens = K.text_to_intermediate(text, table)
            ids = G.encode_intermediate(tokens, pim)
            if max(ids) >= num_symbols:
                raise KeyError(f"音素ID {max(ids)} >= num_symbols {num_symbols}")
        except KeyError as exc:
            rejected.append({"split": sp, "uid": uid, "text": text, "why": str(exc)})
            continue
        d = duration_only(teacher, ids).numpy().astype(np.float32)
        assert len(d) == len(ids), (len(d), len(ids))
        all_ids.append(np.asarray(ids, dtype=np.int16))
        all_d.append(d)
        index.append({"split": sp, "uid": uid, "text": text,
                      "n_tokens": len(tokens), "n_ids": len(ids),
                      "off": off, "frames": int(np.ceil(d).sum())})
        off += len(ids)
        if (i + 1) % 1000 == 0:
            el = time.perf_counter() - t0
            print(f"  {i+1:>6}/{len(rows)}  {el/(i+1)*1000:.1f} ms/文  "
                  f"残り {(len(rows)-i-1)*el/(i+1)/60:.1f} 分")

    import pathlib
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    np.savez(outdir / "durations.npz",
             ids=np.concatenate(all_ids), dT=np.concatenate(all_d),
             offsets=np.array([r["off"] for r in index] + [off], dtype=np.int64))
    with open(outdir / "index.jsonl", "w") as f:
        for r in index:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {
        "n_rows_read": len(rows), "n_kept": len(index), "n_rejected": len(rejected),
        "verified_against_full_infer": n_ver,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "id_to_phoneme": {str(v[0]): k for k, v in pim.items() if v[0] < num_symbols},
        "num_symbols": num_symbols,
        "environment": {"python": platform.python_version(),
                        "torch": torch.__version__, "device": "cpu",
                        "platform": platform.platform()},
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"],
                                     capture_output=True, text=True).stdout.strip(),
        "rejected": rejected[:200],
    }
    json.dump(meta, open(outdir / "meta.json", "w"), ensure_ascii=False, indent=2)
    print(f"\n採用 {len(index):,} / 棄却 {len(rejected)} → {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
