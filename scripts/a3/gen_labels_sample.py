#!/usr/bin/env python3
"""A-3: ラベルパック形式の実測用に、教師から本物のラベルを N 文ぶん取る。

teacher-inference skill の 6 項目に従う。
出力は fp32 の生ラベル（.npz 1 文 1 ファイル、無圧縮）。
以降の形式比較はここから変換して測る。
"""
from __future__ import annotations

import argparse, csv, glob, json, hashlib, sys, time, warnings
import numpy as np
import torch

warnings.filterwarnings("ignore")
PP = "~/Documents/piper-plus"
CKPT_NAME = "epoch=499-step=22000.ckpt"

from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402
import piper_train.vits.models as _m  # noqa: E402

assert _m.__file__.startswith(PP + "/src/python"), _m.__file__


def snap_dir() -> str:
    hits = glob.glob(
        "~/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    assert hits, "ckpt snapshot が無い"
    return hits[0]


def build(ckpt):
    hp = ckpt["hyper_parameters"]
    m = SynthesizerTrn(
        n_vocab=hp["num_symbols"],
        spec_channels=hp.get("filter_length", 1024) // 2 + 1,
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
    sd = {k[len("model_g."):]: v for k, v in ckpt["state_dict"].items() if k.startswith("model_g.")}
    sd, stats = normalize_checkpoint_state_dict(sd, m.state_dict())
    r = m.load_state_dict(sd, strict=False)
    assert not r.missing_keys and not r.unexpected_keys
    m.eval()
    apply_ema_shadow_params(m.dec, ckpt["ema_generator_state"]["shadow_params"])  # 順序が命
    m.dec.remove_weight_norm()
    return m, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=128)
    ap.add_argument("--seed", type=int, default=20260826)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import os
    os.makedirs(a.out, exist_ok=True)
    snap = snap_dir()
    ckpt = torch.load(snap + CKPT_NAME, map_location="cpu", weights_only=False)
    cfg = json.load(open(snap + "config.json"))
    pid = cfg["phoneme_id_map"]
    lim = cfg.get("language_id_map") or {c: i for i, c in enumerate(["ja","en","zh","es","fr","pt"])}
    teacher, nstats = build(ckpt)
    print(f"teacher ready normalize={nstats}", flush=True)

    rows = list(csv.DictReader(open("./data/splits/corpus_train.tsv"),
                               delimiter="\t"))
    # B-1（かな無し行が中国語音素になる）を避けるため、かなを 1 文字以上含む行に限定。
    kana = lambda s: any("ぁ" <= c <= "ゟ" or "ァ" <= c <= "ヿ" for c in s)
    rows = [r for r in rows if kana(r["text"])]
    rng = np.random.default_rng(a.seed)
    idx = rng.choice(len(rows), size=a.n, replace=False)
    sel = [rows[int(i)] for i in idx]

    index = []
    t0 = time.perf_counter()
    for k, r in enumerate(sel):
        text = r["text"]
        ids, pinfo = text_to_phoneme_ids_and_prosody(text, pid, language="ja", language_id_map=lim)
        pv = [[p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0] for p in pinfo]
        assert len(pv) == len(ids)
        assert max(ids) < 173, (text, max(ids))
        with torch.no_grad():
            o = teacher.infer(
                torch.tensor([ids]), torch.tensor([len(ids)]),
                lid=torch.tensor([0]), noise_scale=0.0, noise_scale_w=0.0, length_scale=1.0,
                prosody_features=torch.tensor([pv], dtype=torch.float32),
                speaker_embeddings=None)
        yT = o.audio[0, 0].numpy().astype(np.float32)
        zT = o.latents[0][0].numpy().astype(np.float32)   # (192, T)
        dT = o.durations[0].numpy().astype(np.float32)
        assert zT.shape[0] == 192
        assert zT.shape[1] * 256 == yT.shape[0]
        assert dT.shape[0] == len(ids)
        np.savez(f"{a.out}/{k:06d}.npz",
                 phoneme_ids=np.asarray(ids, np.int32),
                 prosody=np.asarray(pv, np.int16),
                 dT=dT, zT=zT, yT=yT)
        index.append(dict(seq=k, source=r["source"], id=r["id"], text=text,
                          n_ids=len(ids), frames=int(zT.shape[1]),
                          samples=int(yT.shape[0]), seconds=yT.shape[0] / 22050))
        if (k + 1) % 16 == 0:
            print(f"  {k+1}/{a.n}  {(time.perf_counter()-t0)/(k+1)*1000:.0f} ms/文", flush=True)
    el = time.perf_counter() - t0
    json.dump(dict(n=a.n, seed=a.seed, elapsed_s=el, ms_per_utt=el / a.n * 1000,
                   python=sys.version.split()[0], torch=torch.__version__,
                   ckpt_sha256_prefix="f375c749", rows=index),
              open(f"{a.out}/index.json", "w"), ensure_ascii=False, indent=1)
    print(f"done {a.n} 文 / {el:.1f} s / {el/a.n*1000:.0f} ms/文")


if __name__ == "__main__":
    main()
