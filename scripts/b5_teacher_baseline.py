#!/usr/bin/env python3
"""B-5: 教師音声の品質ベースラインを測る。

**なぜ再測定するか**: 暫定計測で SCOREQ 2.06 / UTMOS 1.62 と、論文の教師 (4.68 / 4.42)
から大きく外れた。その計測は 8 文・1.3〜3.6 秒の短尺で、音素化経路も未確認だった。
**生徒は教師を超えない**ので、この値が実態なら本プロジェクトの目標値を設定し直す必要がある。

前回の失敗を避けるための設計:

* **発話長 3 秒以上**（短尺クリップは MOS 推定器を不安定にする）
* **24 文以上**（論文の diverse24 と同じ規模）
* **canonical な音素化経路**を使う（自前実装だと 2.4 倍速になる、M-5）
* **前後に無音パディング**（推定器の端の扱いを揃える）
* 教師の呼び出しは `teacher-inference` skill の 6 項目に従う

実行:
    uv run --extra eval python scripts/b5_teacher_baseline.py
"""

from __future__ import annotations

import csv
import glob
import json
import pathlib
import sys
import warnings

import numpy as np
import soundfile as sf
import torch

warnings.filterwarnings("ignore")

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
sys.path.insert(0, f"{PIPER_PLUS}/src/python")
sys.path.insert(0, f"{PIPER_PLUS}/src/python/g2p")

from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402
from piper_plus_g2p.japanese import JapanesePhonemizer  # noqa: E402

SR = 22050
CKPT = "epoch=499-step=22000.ckpt"
N_UTTERANCES = 24
MIN_SEC, MAX_SEC = 3.0, 8.0
PAD_SEC = 0.3  # 前後の無音。推定器の端の扱いを揃える

OUT_DIR = pathlib.Path("reports/b5_teacher_wav")
REPORT = pathlib.Path("reports/b5_teacher_baseline.json")


def snapshot() -> str:
    hits = glob.glob(
        "/Users/s19447/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 ckpt が HF キャッシュに無い")
    return hits[0]


def build_teacher(ckpt: dict) -> SynthesizerTrn:
    """teacher-inference skill の手順どおりに教師を組む。"""
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
    apply_ema_shadow_params(model.dec, ckpt["ema_generator_state"]["shadow_params"])
    model.dec.remove_weight_norm()
    return model


def pick_texts(n: int) -> list[str]:
    """held-out から 3〜8 秒相当の文を選ぶ。長さで層化して偏りを避ける。"""
    phonemizer = JapanesePhonemizer()
    rows = list(csv.reader(open("data/splits/corpus_heldout.tsv"), delimiter="\t"))
    cands = []
    for row in rows:
        if not row or not row[-1]:
            continue
        text = row[-1]
        tokens = [p for p in phonemizer.phonemize(text) if p not in "[]#_^$?"]
        est = tokens.__len__() / 2.0 / 8.0  # モーラ数 / 8.0 mora/s
        if MIN_SEC <= est <= MAX_SEC:
            cands.append((est, text))
    cands.sort()
    # 長さの分位から等間隔に抜く
    step = len(cands) / n
    return [cands[int(i * step)][1] for i in range(n)]


def main() -> int:
    snap = snapshot()
    ckpt = torch.load(snap + CKPT, map_location="cpu", weights_only=False)
    config = json.load(open(snap + "config.json"))
    pim = config["phoneme_id_map"]
    lim = config.get("language_id_map") or {
        c: i for i, c in enumerate(["ja", "en", "zh", "es", "fr", "pt"])
    }

    print("教師を構築（EMA 適用）…")
    teacher = build_teacher(ckpt)

    texts = pick_texts(N_UTTERANCES)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pad = np.zeros(int(PAD_SEC * SR), dtype=np.float32)

    rows = []
    print(f"\n{'#':>3} {'秒':>6} {'mora/s':>7}  テキスト")
    for i, text in enumerate(texts):
        ids, prosody = text_to_phoneme_ids_and_prosody(
            text, pim, language="ja", language_id_map=lim
        )
        assert max(ids) < ckpt["hyper_parameters"]["num_symbols"]
        pv = [
            [p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0] for p in prosody
        ]
        with torch.no_grad():
            out = teacher.infer(
                torch.tensor([ids]),
                torch.tensor([len(ids)]),
                lid=torch.tensor([0]),
                noise_scale=0.0,
                noise_scale_w=0.0,
                length_scale=1.0,
                prosody_features=torch.tensor([pv]).float(),
                speaker_embeddings=None,
            )
        wav = out.audio.squeeze().numpy().astype(np.float32)
        sec = len(wav) / SR
        # 概算モーラ数（記号を除いたトークン数の半分）
        mora = sum(1 for t in ids if t not in (0, 1, 2, 3, 7, 8, 9)) / 2.0
        rate = mora / sec
        assert 5.0 <= rate <= 11.0, f"発話速度が異常: {rate:.1f} mora/s — 音素化を疑え"

        path = OUT_DIR / f"t{i:02d}.wav"
        sf.write(path, np.concatenate([pad, wav, pad]), SR)
        rows.append({"idx": i, "text": text, "sec": round(sec, 3),
                     "mora_per_sec": round(rate, 2), "wav": str(path)})
        print(f"{i:>3} {sec:>6.2f} {rate:>7.2f}  {text[:40]}")

    secs = [r["sec"] for r in rows]
    print(f"\n{len(rows)} 文 / 合計 {sum(secs):.1f} 秒 / "
          f"平均 {np.mean(secs):.2f} 秒 (最短 {min(secs):.2f} / 最長 {max(secs):.2f})")
    print(f"発話速度 {np.mean([r['mora_per_sec'] for r in rows]):.2f} mora/s "
          f"（自然な範囲 6〜10 に収まっていること）")

    REPORT.parent.mkdir(exist_ok=True)
    json.dump({"utterances": rows, "pad_sec": PAD_SEC, "sample_rate": SR},
              open(REPORT, "w"), ensure_ascii=False, indent=1)
    print(f"\nwav: {OUT_DIR}/  manifest: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
