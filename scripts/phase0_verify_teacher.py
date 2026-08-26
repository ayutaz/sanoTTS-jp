#!/usr/bin/env python3
"""Phase 0: 教師モデルが決定的に (dT, zT, yT) を返すことを検証する。

論文 arXiv:2608.21378 §II の条件
(noise_scale=0, noise_scale_w=0, length_scale=1) で piper-plus の教師を回し、
蒸留ラベルが取れること・二回走らせて bit 一致することを確認する。

実行:
    /Users/s19447/Documents/piper-plus/.venv/bin/python scripts/phase0_verify_teacher.py

前提:
    - HF に `ayousanz` として認証済み (`~/.cache/huggingface/token`)
    - 教師 ckpt が HF キャッシュに取得済み
      (`ayousanz/piper-plus-zero-shot-tsukuyomi` / `epoch=499-step=22000.ckpt`)
"""

from __future__ import annotations

import glob
import json
import sys
import warnings

import torch

warnings.filterwarnings("ignore")

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
REPO_ID = "ayousanz/piper-plus-zero-shot-tsukuyomi"
CKPT_NAME = "epoch=499-step=22000.ckpt"

sys.path.insert(0, f"{PIPER_PLUS}/src/python")

from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402

# `.venv/lib/.../site-packages/piper_train/` に v1.13.0 相当の stale コピーがあり、
# sys.path.insert を忘れるとそちらが解決される（editable finder が meta_path に
# append されるため標準の PathFinder が先に走る）。掴んだ実体を必ず検証する。
import piper_train.vits.models as _models  # noqa: E402

assert _models.__file__.startswith(PIPER_PLUS + "/src/python"), (
    f"stale な piper_train を掴んでいる: {_models.__file__}"
)


def snapshot_dir() -> str:
    """HF キャッシュ内の snapshot ディレクトリを返す (無ければダウンロード)。"""
    pattern = (
        "/Users/s19447/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    hits = glob.glob(pattern)
    if hits:
        return hits[0]
    from huggingface_hub import hf_hub_download

    for name in (CKPT_NAME, "config.json", "eval/spk_tsukuyomi.npy"):
        path = hf_hub_download(REPO_ID, name)
    return path.rsplit("/", 1)[0].rsplit("/eval", 1)[0] + "/"


def build_teacher(ckpt: dict) -> SynthesizerTrn:
    """ckpt の hyper_parameters から SynthesizerTrn を組んで重みを載せる。

    piper-plus v2.0 (HEAD) で missing/unexpected ともに 0 になることを確認済み。
    `spk_embed_dim=192` なので v2.0 の 192 次元チェックを通る。
    """
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
    generator_sd = {
        k[len("model_g.") :]: v
        for k, v in ckpt["state_dict"].items()
        if k.startswith("model_g.")
    }
    generator_sd, stats = normalize_checkpoint_state_dict(
        generator_sd, model.state_dict()
    )
    result = model.load_state_dict(generator_sd, strict=False)
    assert not result.missing_keys, f"missing keys: {result.missing_keys[:8]}"
    assert not result.unexpected_keys, f"unexpected keys: {result.unexpected_keys[:8]}"
    print(f"  state_dict: missing=0 unexpected=0  normalize stats={stats}")
    model.eval()

    # EMA は load_state_dict では適用されない。ONNX export と同じ重みにするには
    # 明示的に適用する必要がある。`remove_weight_norm()` は weight_g/weight_v を
    # 単一テンソルに融合してしまうので、**必ずその前に**呼ぶこと。
    ema_state = ckpt.get("ema_generator_state")
    assert ema_state and "shadow_params" in ema_state, "ema_generator_state が無い"
    print(
        f"  EMA: decay={ema_state['decay']} num_updates={ema_state['num_updates']} "
        f"shadow={len(ema_state['shadow_params'])} params → 適用"
    )
    apply_ema_shadow_params(model.dec, ema_state["shadow_params"])

    model.dec.remove_weight_norm()
    return model


def main() -> int:
    snap = snapshot_dir()
    print(f"snapshot: {snap}")

    ckpt = torch.load(snap + CKPT_NAME, map_location="cpu", weights_only=False)
    config = json.load(open(snap + "config.json"))
    phoneme_id_map = config["phoneme_id_map"]

    print("教師を構築:")
    teacher = build_teacher(ckpt)

    # 音素化は canonical な `text_to_phoneme_ids_and_prosody` を使う。
    # 自前で音素→ID を組んではいけない:
    #   1. 拗音・破擦音 (`ch` `sh` `ts` `ky`) は phoneme_id_map に生の文字列で
    #      入っておらず PUA (U+E000〜) 経由。canonical な表は
    #      `piper_plus_g2p.encode.pua` の TOKEN2CHAR (99 entry)。
    #      `src/python/jp_phoneme_map.py` の `get_phoneme_id_map()` は使ってはいけない
    #      ―― 58 entry / max id 57 しか返さず、実測で 54 音素の id が ckpt と食い違う。
    #   2. **`language_id_map` を渡すと multilingual に auto-promote され、
    #      トークン間に `_` の intersperse padding が入る** (len(ids) ≒ 2*tokens+3)。
    #      これを飛ばすと発話が約 2.4 倍速になる（実測: 17.7 mora/s vs 正常な 7.6〜8.4）。
    language_id_map = config.get("language_id_map") or {
        code: i for i, code in enumerate(["ja", "en", "zh", "es", "fr", "pt"])
    }
    text = "今日は良い天気ですね。散歩に行きましょう。"
    ids, prosody_info = text_to_phoneme_ids_and_prosody(
        text, phoneme_id_map, language="ja", language_id_map=language_id_map
    )
    assert max(ids) < ckpt["hyper_parameters"]["num_symbols"], (
        f"音素 ID {max(ids)} が num_symbols を超えている。config の phoneme_id_map は "
        "185 entry あるが、この ckpt は num_symbols=173 で学習されている"
    )
    print(f"テキスト: {text}")
    print(f"音素ID {len(ids)} 個 (max={max(ids)}), prosody {len(prosody_info)} 個")

    x = torch.tensor([ids])
    x_lengths = torch.tensor([len(ids)])
    # NOTE: この ckpt は num_speakers=1 で `spk_proj` / `emb_g` を state_dict に
    # 一切持たない。`speaker_embeddings` に何を渡しても bit 完全に無視される
    # （None / spk_tsukuyomi.npy / ランダム 192次元 の 3 通りで audio が bit 一致
    # することを実測済み）。話者は重みに焼き込まれているので None を渡す。
    # `eval/spk_tsukuyomi.npy` は SECS 評価専用であってモデル入力ではない。
    speaker = None
    # 実 A1/A2/A3 を渡す。ゼロテンソルは「prosody 無し」ではない ――
    # prosody_proj(0) = bias が concat されるため、None / ゼロ / 実 prosody で
    # 総フレーム数が 3 通りとも変わる。
    prosody_values = [
        [p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0]
        for p in prosody_info
    ]
    assert len(prosody_values) == len(ids), "prosody と音素IDの長さがずれている"
    prosody = torch.tensor([prosody_values], dtype=torch.float32)

    def infer():
        with torch.no_grad():
            return teacher.infer(
                x,
                x_lengths,
                lid=torch.tensor([0]),  # ja = 0。焼き込まれていないので必須
                noise_scale=0.0,  # z_p = m_p（決定的）
                noise_scale_w=0.0,  # SDP を決定的に
                length_scale=1.0,
                prosody_features=prosody,
                speaker_embeddings=speaker,
            )

    first, second = infer(), infer()
    y_t, z_t, d_t = first.audio, first.latents[0], first.durations

    print(f"\nyT audio    : {tuple(y_t.shape)}  ({y_t.shape[-1] / 22050:.3f} s)")
    print(f"zT latent   : {tuple(z_t.shape)}")
    print(f"dT durations: {tuple(d_t.shape)}")
    print(f"発話速度    : {22 / (y_t.shape[-1] / 22050):.1f} mora/s (22 モーラ)")

    checks = {
        "zT が 192ch (論文の教師潜在と一致)": z_t.shape[1] == 192,
        "hop 256 (frames × 256 == audio samples)": z_t.shape[-1] * 256
        == y_t.shape[-1],
        "audio が bit 完全一致 (決定的)": torch.equal(first.audio, second.audio),
        "z が bit 完全一致 (決定的)": torch.equal(first.latents[0], second.latents[0]),
        "dT の長さが音素数と一致": d_t.shape[-1] == len(ids),
        "発話速度が自然な範囲 (6-10 mora/s)": 6.0
        <= 22 / (y_t.shape[-1] / 22050)
        <= 10.0,
    }
    print()
    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok &= passed
    print("\n" + ("Phase 0 疎通 OK" if ok else "Phase 0 疎通 NG"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
