#!/usr/bin/env python3
"""D-6: 波形ラベル `yT` に EMA を当てるかを決める。

教師 ckpt は `ema_generator_state` (decay 0.9995 / num_updates 11000) を持つ。
`load_state_dict` では適用されないので `apply_ema_shadow_params()` を
**`remove_weight_norm()` より前に**呼ぶ必要がある。適用有無で `yT` は変わり
(`zT` / `dT` は decoder を通らないので変わらない)、生徒はどちらかの音を真似る。

このスクリプトが測ること:

1. EMA あり / なし の 2 教師を作り、**順序（EMA → remove_weight_norm）を assert** する。
   さらに**逆順の陰性対照**を作り、逆順だと EMA が 1 つも当たらないことを重み比較で示す。
2. 同じ 24 文（`reports/b5_teacher_baseline.json` と同一のテキスト）で両方の `yT` を生成。
   `zT` / `dT` の bit 一致を確認し、`yT` の SNR を測る。
3. UTMOS と SCOREQ を両方測り、**対応のある検定**（paired t / Wilcoxon）で p を出す。n=24。
4. 摩擦音区間の 2–8 kHz スペクトル平坦度 (SFM) を比較する（日本語は摩擦音が多い）。

入力経路は **ラベル生成と同じ** kana_g2p + prosody ゼロ（D-014）。
b5 は canonical G2P + 実 prosody なので、b5 の UTMOS 値とは直接比較しない。

実行:
    uv run --extra eval python scripts/d6_ema_ablation.py
"""

from __future__ import annotations

import glob
import json
import pathlib
import platform
import subprocess
import sys
import time
import warnings

import numpy as np
import soundfile as sf
import torch
from scipy import stats

warnings.filterwarnings("ignore")

import os

#: piper-plus の checkout。**環境変数で差し替えられる**（他人の環境でも動くように）。
#: 既定は開発者のローカルパスだが、clone した人は `PIPER_PLUS_ROOT` を設定する。
PIPER_PLUS = os.environ.get("PIPER_PLUS_ROOT",
                            os.path.expanduser("~/Documents/piper-plus"))
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
sys.path.insert(0, f"{PIPER_PLUS}/src/python")
sys.path.insert(0, f"{PIPER_PLUS}/src/python/g2p")

import kana_g2p as K  # noqa: E402
import piper_train.vits.models as _models  # noqa: E402
from piper_plus_g2p.encode import pua  # noqa: E402
from piper_train.export_onnx import apply_ema_shadow_params  # noqa: E402
from piper_train.vits.commons import normalize_checkpoint_state_dict  # noqa: E402
from piper_train.vits.models import SynthesizerTrn  # noqa: E402

# stale な piper_train (M-1.1) を掴んでいないことを確認
assert _models.__file__.startswith(PIPER_PLUS + "/src/python"), _models.__file__

CKPT = "epoch=499-step=22000.ckpt"
SR = 22050
HOP = 256
N_FFT = 1024
PAD_SEC = 0.3  # b5 と同じ。MOS 推定器の端の扱いを揃える
UTMOS_SR = 16000

# 論文式7 のノイズ注入集合を日本語向けに拡張したもの（CLAUDE.md）。
# 無声化母音 I / U は音響的にほぼ摩擦雑音なので含める。
S_JA = {"s", "sh", "ts", "ch", "z", "j", "h", "hy", "f", "I", "U"}
VOWELS = {"a", "i", "u", "e", "o"}
SFM_LO_HZ, SFM_HI_HZ = 2000.0, 8000.0

B5_REPORT = pathlib.Path("reports/b5_teacher_baseline.json")
OUT_DIR = pathlib.Path("reports/d6_ema_wav")
REPORT = pathlib.Path("reports/d6_ema.json")


# --------------------------------------------------------------------------
# 教師の構築
# --------------------------------------------------------------------------
def snapshot() -> str:
    hits = glob.glob(
        "~/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 ckpt が HF キャッシュに無い")
    return hits[0]


def _weight_norm_param_names(module: torch.nn.Module) -> set[str]:
    """weight_norm 由来のパラメータ名（weight_g/weight_v 系）を集める。"""
    return {
        n
        for n, _ in module.named_parameters()
        if n.endswith(("weight_g", "weight_v", "original0", "original1"))
    }


def _make_model(ckpt: dict) -> SynthesizerTrn:
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
    assert not res.missing_keys and not res.unexpected_keys, (
        res.missing_keys[:5],
        res.unexpected_keys[:5],
    )
    model.eval()
    return model


def build_teacher(ckpt: dict, mode: str) -> tuple[SynthesizerTrn, dict]:
    """mode: "ema" / "noema" / "ema_wrong_order"（陰性対照）。

    "ema" は EMA を `remove_weight_norm()` の **前**に当てる。順序が正しいことを
    「適用前に weight_g/weight_v 系のパラメータが実在する」ことで assert する
    （`remove_weight_norm()` 後だと 0 個になり、shadow のキーが当たらない）。
    """
    model = _make_model(ckpt)
    shadow = ckpt["ema_generator_state"]["shadow_params"]
    info: dict = {"mode": mode, "n_shadow_params": len(shadow)}

    if mode == "ema":
        wn_before = _weight_norm_param_names(model.dec)
        assert wn_before, "remove_weight_norm 済みの decoder に EMA を当てようとしている"
        info["dec_weight_norm_params_before_apply"] = len(wn_before)
        applied, skipped = apply_ema_shadow_params(model.dec, shadow)
        info["applied"] = int(applied)
        info["skipped"] = int(skipped)
        assert applied == len(shadow) and skipped == 0, (applied, skipped)
        with torch.no_grad():
            model.dec.remove_weight_norm()
        assert not _weight_norm_param_names(model.dec)
        info["order"] = "apply_ema -> remove_weight_norm (canonical)"
    elif mode == "noema":
        with torch.no_grad():
            model.dec.remove_weight_norm()
        info["applied"] = 0
        info["skipped"] = 0
        info["order"] = "remove_weight_norm only (EMA 未適用)"
    elif mode == "ema_wrong_order":
        with torch.no_grad():
            model.dec.remove_weight_norm()
        assert not _weight_norm_param_names(model.dec)
        applied, skipped = apply_ema_shadow_params(model.dec, shadow)
        info["applied"] = int(applied)
        info["skipped"] = int(skipped)
        info["order"] = "remove_weight_norm -> apply_ema (陰性対照。当たらないはず)"
    else:
        raise ValueError(mode)
    return model, info


def dec_weight_diff(a: SynthesizerTrn, b: SynthesizerTrn) -> dict:
    """decoder の重みが何個違うか（bit 単位）。"""
    sa, sb = a.dec.state_dict(), b.dec.state_dict()
    assert sa.keys() == sb.keys()
    diff = [k for k in sa if not torch.equal(sa[k], sb[k])]
    max_rel = 0.0
    for k in diff:
        denom = sa[k].abs().max().item() or 1.0
        max_rel = max(max_rel, (sa[k] - sb[k]).abs().max().item() / denom)
    return {
        "n_tensors": len(sa),
        "n_differing": len(diff),
        "max_rel_abs_diff": round(max_rel, 8),
        "examples": diff[:5],
    }


# --------------------------------------------------------------------------
# 入力（ラベル生成と同じ経路: kana_g2p + prosody ゼロ）
# --------------------------------------------------------------------------
def encode_intermediate(tokens: list[str], pim: dict, table: dict) -> list[int]:
    """`scripts/gen_teacher_labels.py` と同一。PAD 規則を外すと音素ID一致が 0% になる。"""
    pad = pim["_"][0]
    phonemes = K.intermediate_to_phonemes(tokens, table)
    body: list[int] = []
    for p in phonemes:
        ch = pua.TOKEN2CHAR.get(p, p)
        if ch not in pim:
            raise KeyError(f"音素 {p!r} が phoneme_id_map に無い")
        pid = pim[ch][0]
        body.append(pid)
        if pid != pad:  # その音素自身が PAD なら後ろに挟まない
            body.append(pad)
    return [pim["^"][0], pad] + body + [pim["$"][0]]


def id_to_token_map(pim: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for ch, ids in pim.items():
        for i in ids:
            out.setdefault(int(i), pua.CHAR2TOKEN.get(ch, ch))
    return out


# --------------------------------------------------------------------------
# スペクトル平坦度
# --------------------------------------------------------------------------
def stft_power(wav: np.ndarray) -> np.ndarray:
    """[n_frames, n_bins] のパワースペクトル。hop 256 なのでモデルのフレーム格子に一致。"""
    import librosa

    spec = librosa.stft(
        wav.astype(np.float32), n_fft=N_FFT, hop_length=HOP, win_length=N_FFT,
        window="hann", center=True,
    )
    return (np.abs(spec) ** 2).T  # [frames, bins]


def sfm_per_frame(power: np.ndarray) -> np.ndarray:
    """各フレームの 2–8 kHz スペクトル平坦度（幾何平均 / 算術平均）。"""
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    band = (freqs >= SFM_LO_HZ) & (freqs <= SFM_HI_HZ)
    p = power[:, band] + 1e-12
    return np.exp(np.log(p).mean(axis=1)) / p.mean(axis=1)


def segment_frames(dT: np.ndarray) -> np.ndarray:
    """`cumsum(ceil(dT))` から各音素の [start, end) フレームを作る。"""
    ends = np.cumsum(np.ceil(dT)).astype(np.int64)
    starts = np.concatenate([[0], ends[:-1]])
    return np.stack([starts, ends], axis=1)


def class_sfm(wav: np.ndarray, dT: np.ndarray, tokens: list[str],
              wanted: set[str], min_frames: int = 2) -> tuple[float, int, int]:
    """指定音素クラスの区間だけを集めた SFM の平均。(mean, n_segments, n_frames)。"""
    sfm = sfm_per_frame(stft_power(wav))
    spans = segment_frames(dT)
    vals: list[float] = []
    n_seg = 0
    for (s, e), tok in zip(spans, tokens, strict=True):
        if tok not in wanted:
            continue
        s, e = int(s), min(int(e), len(sfm))
        if e - s < min_frames:
            continue
        n_seg += 1
        vals.extend(sfm[s:e].tolist())
    if not vals:
        return float("nan"), 0, 0
    return float(np.mean(vals)), n_seg, len(vals)


# --------------------------------------------------------------------------
# 統計
# --------------------------------------------------------------------------
def mde_dz(n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """n 対の paired t 検定で power 0.8 を得るのに必要な効果量 dz（noncentral t で解く）。"""
    df = n - 1
    tcrit = stats.t.ppf(1 - alpha / 2, df)

    def pw(dz: float) -> float:
        nc = dz * np.sqrt(n)
        return (1 - stats.nct.cdf(tcrit, df, nc)) + stats.nct.cdf(-tcrit, df, nc)

    lo, hi = 0.0, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if pw(mid) < power:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def paired_stats(name: str, a: list[float], b: list[float]) -> dict:
    """a = EMA あり, b = EMA なし。diff = a - b。"""
    arr_a, arr_b = np.asarray(a, float), np.asarray(b, float)
    d = arr_a - arr_b
    n = len(d)
    sd = float(d.std(ddof=1))
    se = sd / np.sqrt(n) if n > 1 else float("nan")
    t_stat, p_t = stats.ttest_rel(arr_a, arr_b)
    try:
        w_stat, p_w = stats.wilcoxon(arr_a, arr_b)
    except ValueError:
        w_stat, p_w = float("nan"), float("nan")
    ci = stats.t.ppf(0.975, n - 1) * se
    dz = float(d.mean() / sd) if sd > 0 else float("nan")
    mde = mde_dz(n)
    out = {
        "n": n,
        "ema_mean": round(float(arr_a.mean()), 4),
        "ema_sd": round(float(arr_a.std(ddof=1)), 4),
        "noema_mean": round(float(arr_b.mean()), 4),
        "noema_sd": round(float(arr_b.std(ddof=1)), 4),
        "mean_diff_ema_minus_noema": round(float(d.mean()), 4),
        "diff_sd": round(sd, 4),
        "diff_ci95": [round(float(d.mean() - ci), 4), round(float(d.mean() + ci), 4)],
        "n_pairs_ema_better": int((d > 0).sum()),
        "n_pairs_noema_better": int((d < 0).sum()),
        "paired_t": round(float(t_stat), 4),
        "p_paired_t": round(float(p_t), 6),
        "wilcoxon_W": float(w_stat),
        "p_wilcoxon": round(float(p_w), 6),
        "cohens_dz": round(dz, 4),
        "mde_dz_at_power80": round(mde, 4),
        "mde_absolute_at_power80": round(mde * sd, 4),
    }
    print(
        f"\n{name}  n={n}\n"
        f"  EMA あり {out['ema_mean']:.4f} ± {out['ema_sd']:.4f}   "
        f"EMA なし {out['noema_mean']:.4f} ± {out['noema_sd']:.4f}\n"
        f"  差 (あり−なし) {out['mean_diff_ema_minus_noema']:+.4f}  "
        f"95%CI [{out['diff_ci95'][0]:+.4f}, {out['diff_ci95'][1]:+.4f}]  "
        f"dz={out['cohens_dz']:+.3f}\n"
        f"  paired t p={out['p_paired_t']:.4f}   Wilcoxon p={out['p_wilcoxon']:.4f}   "
        f"あり優位 {out['n_pairs_ema_better']}/{n} 対\n"
        f"  n={n} で power .8 を得るのに必要な差 = {out['mde_absolute_at_power80']:.4f} "
        f"(dz {out['mde_dz_at_power80']:.2f})"
    )
    return out


# --------------------------------------------------------------------------
def load_16k(path: str) -> torch.Tensor:
    import torchaudio

    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    t = torch.from_numpy(wav[:, 0]).unsqueeze(0)
    if sr != UTMOS_SR:
        t = torchaudio.transforms.Resample(sr, UTMOS_SR)(t)
    return t


def measure_utmos(paths: list[str]) -> list[float]:
    predictor = torch.hub.load(
        "tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True
    )
    predictor.eval()
    out = []
    for p in paths:
        with torch.no_grad():
            out.append(float(predictor(load_16k(p), sr=UTMOS_SR).item()))
    return out


def canonical_path_check(
    m_ema: SynthesizerTrn, m_no: SynthesizerTrn, texts: list[str],
    pim: dict, lim: dict,
) -> dict:
    """頑健性チェック: canonical G2P + 実 prosody（= b5 / M-2.5 と同じ条件）でも測る。

    本体は D-014 のラベル生成経路（kana_g2p + prosody ゼロ）で測っているので、
    UTMOS の差が入力経路に依存しないかを確かめる。記録済みの M-2.5「SNR 12.53 dB」
    がどちらの条件のものかを切り分ける目的も兼ねる。
    """
    from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: PLC0415

    from saanotts_jp.scoreq_metric import score_files  # noqa: PLC0415

    for sub in ("ema_canon", "noema_canon"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)
    pad = np.zeros(int(PAD_SEC * SR), dtype=np.float32)

    snrs, paths_e, paths_n = [], [], []
    for i, text in enumerate(texts):
        ids, prosody = text_to_phoneme_ids_and_prosody(
            text, pim, language="ja", language_id_map=lim
        )
        pv = [[p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0]
              for p in prosody]
        args = dict(
            lid=torch.tensor([0]), noise_scale=0.0, noise_scale_w=0.0,
            length_scale=1.0, prosody_features=torch.tensor([pv]).float(),
            speaker_embeddings=None,
        )
        with torch.no_grad():
            y_e = m_ema.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                              **args).audio.squeeze().numpy().astype(np.float32)
            y_n = m_no.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                             **args).audio.squeeze().numpy().astype(np.float32)
        noise = y_e - y_n
        snrs.append(10 * np.log10(float((y_e**2).sum()) / float((noise**2).sum())))
        pe = OUT_DIR / "ema_canon" / f"t{i:02d}.wav"
        pn = OUT_DIR / "noema_canon" / f"t{i:02d}.wav"
        sf.write(pe, np.concatenate([pad, y_e, pad]), SR)
        sf.write(pn, np.concatenate([pad, y_n, pad]), SR)
        paths_e.append(str(pe))
        paths_n.append(str(pn))

    print(
        f"\n[canonical G2P + 実 prosody] yT SNR: 平均 {np.mean(snrs):.2f} dB / "
        f"中央 {np.median(snrs):.2f} / 最小 {min(snrs):.2f} / 最大 {max(snrs):.2f}"
    )
    utmos_e = measure_utmos(paths_e)
    utmos_n = measure_utmos(paths_n)
    sc = score_files(paths_e + paths_n, domain="synthetic", mode="nr")
    return {
        "note": "b5 / M-2.5 と同じ入力条件での頑健性チェック",
        "snr_db": {
            "mean": round(float(np.mean(snrs)), 3),
            "median": round(float(np.median(snrs)), 3),
            "min": round(float(min(snrs)), 3),
            "max": round(float(max(snrs)), 3),
        },
        "utmos": paired_stats("UTMOS [canonical G2P + 実 prosody]", utmos_e, utmos_n),
        "scoreq": paired_stats(
            "SCOREQ [canonical G2P + 実 prosody]",
            [sc[p] for p in paths_e], [sc[p] for p in paths_n],
        ),
    }


def snr_sweep(m_ema: SynthesizerTrn, m_no: SynthesizerTrn, pim: dict, lim: dict,
              n_want: int) -> dict:
    """held-out を発話長で層別して SNR を見る。

    記録済みの M-2.5「SNR 12.53 dB」が再現しないので、短尺で下がるのかを確かめる。
    canonical G2P + 実 prosody（M-2.5 と同条件）で測る。
    """
    import csv  # noqa: PLC0415

    from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody  # noqa: PLC0415

    rows = [r for r in csv.reader(
        open("data/splits/corpus_heldout.tsv"), delimiter="\t") if r and r[-1]]
    pairs: list[tuple[float, float]] = []
    for r in rows:
        if len(pairs) >= n_want:
            break
        try:
            ids, pro = text_to_phoneme_ids_and_prosody(
                r[-1], pim, language="ja", language_id_map=lim)
        except Exception:  # noqa: BLE001 — G2P で落ちる行は飛ばす
            continue
        pv = [[p["a1"], p["a2"], p["a3"]] if p is not None else [0, 0, 0] for p in pro]
        a = dict(lid=torch.tensor([0]), noise_scale=0.0, noise_scale_w=0.0,
                 length_scale=1.0, prosody_features=torch.tensor([pv]).float(),
                 speaker_embeddings=None)
        with torch.no_grad():
            ye = m_ema.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                             **a).audio.squeeze().numpy()
            yn = m_no.infer(torch.tensor([ids]), torch.tensor([len(ids)]),
                            **a).audio.squeeze().numpy()
        d = ye - yn
        pairs.append((len(ye) / SR,
                      10 * np.log10(float((ye**2).sum()) / float((d**2).sum()))))

    arr = np.asarray(pairs)
    bins = []
    print(f"\n[SNR sweep] held-out {len(arr)} 発話（canonical G2P + 実 prosody）")
    for lo, hi in [(0.0, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 100.0)]:
        m = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        if not m.sum():
            continue
        bins.append({"sec_lo": lo, "sec_hi": hi, "n": int(m.sum()),
                     "mean": round(float(arr[m, 1].mean()), 3),
                     "min": round(float(arr[m, 1].min()), 3),
                     "max": round(float(arr[m, 1].max()), 3)})
        print(f"  {lo:>4.1f}–{hi:<5.1f}s n={int(m.sum()):>3}  "
              f"平均 {arr[m,1].mean():.2f}  最小 {arr[m,1].min():.2f}  "
              f"最大 {arr[m,1].max():.2f} dB")
    r = float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1])
    print(f"  全体 平均 {arr[:,1].mean():.2f} / 最小 {arr[:,1].min():.2f} / "
          f"最大 {arr[:,1].max():.2f} dB   r(秒, SNR) = {r:+.3f}")
    return {
        "n": len(arr), "by_duration": bins,
        "overall_mean": round(float(arr[:, 1].mean()), 3),
        "overall_min": round(float(arr[:, 1].min()), 3),
        "overall_max": round(float(arr[:, 1].max()), 3),
        "corr_sec_snr": round(r, 3),
        "recorded_M_2_5": 12.53,
        "reproduced": False,
        "note": (
            "docs/measurements.md M-2.5 の 12.53 dB は再現しない。"
            "発話長で層別しても最小 %.2f dB で 12.53 を下回る発話が 1 つも無い。"
            "M-2.5 は n・入力経路・SNR の定義が記録されていないので、"
            "単発の測定か別定義だった可能性が高い。**docs 側の訂正が要る。**"
            % float(arr[:, 1].min())
        ),
    }


def main() -> int:
    t_start = time.perf_counter()
    table = K.build_mora_table()
    snap = snapshot()
    ckpt = torch.load(snap + CKPT, map_location="cpu", weights_only=False)
    config = json.load(open(snap + "config.json"))
    pim = config["phoneme_id_map"]
    num_symbols = ckpt["hyper_parameters"]["num_symbols"]
    id2tok = id_to_token_map(pim)

    ema_state = ckpt["ema_generator_state"]
    print(
        f"ema_generator_state: decay={ema_state.get('decay')} "
        f"num_updates={ema_state.get('num_updates')} "
        f"shadow_params={len(ema_state['shadow_params'])}"
    )

    print("\n教師を 3 通り構築…")
    m_ema, info_ema = build_teacher(ckpt, "ema")
    m_no, info_no = build_teacher(ckpt, "noema")
    m_wrong, info_wrong = build_teacher(ckpt, "ema_wrong_order")
    for i in (info_ema, info_no, info_wrong):
        print(f"  {i['mode']:<16} applied={i['applied']:<3} skipped={i['skipped']:<3} "
              f"{i['order']}")

    order_proof = {
        "correct_vs_noema": dec_weight_diff(m_ema, m_no),
        "wrong_order_vs_noema": dec_weight_diff(m_wrong, m_no),
        "info": {"ema": info_ema, "noema": info_no, "wrong_order": info_wrong},
        "finding": (
            "逆順（remove_weight_norm -> apply_ema）は **0 個ではなく 23/53 個**が当たる。"
            "weight_norm を経由しない bias 等は融合の影響を受けないため。"
            "つまり順序を間違えると『EMA が効かない』のではなく "
            "**EMA が半分だけ当たった第三の重み**になる。sanity check を "
            "「applied > 0」で書くと通ってしまうので、applied == len(shadow) で assert すること。"
        ),
    }
    print(
        f"  decoder 重み差分: 正順 vs EMA なし = "
        f"{order_proof['correct_vs_noema']['n_differing']}"
        f"/{order_proof['correct_vs_noema']['n_tensors']} tensor 相違、"
        f"逆順 vs EMA なし = {order_proof['wrong_order_vs_noema']['n_differing']} tensor 相違"
    )
    assert order_proof["correct_vs_noema"]["n_differing"] > 0
    del m_wrong

    # --- 24 文（b5 と同一テキスト） ---
    b5 = json.loads(B5_REPORT.read_text())
    texts = [u["text"] for u in b5["utterances"]]
    print(f"\nテキスト {len(texts)} 文（reports/b5_teacher_baseline.json と同一）")

    for sub in ("ema", "noema"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)
    pad = np.zeros(int(PAD_SEC * SR), dtype=np.float32)

    rows: list[dict] = []
    zt_mismatch, dt_mismatch = [], []
    print(f"\n{'#':>3} {'秒':>6} {'mora/s':>7} {'SNR dB':>8}  テキスト")
    for i, text in enumerate(texts):
        tokens = K.text_to_intermediate(text, table)
        ids = encode_intermediate(tokens, pim, table)
        assert max(ids) < num_symbols
        prosody = torch.zeros(1, len(ids), 3)
        args = dict(
            lid=torch.tensor([0]),
            noise_scale=0.0,
            noise_scale_w=0.0,
            length_scale=1.0,
            prosody_features=prosody,
            speaker_embeddings=None,
        )
        with torch.no_grad():
            o_e = m_ema.infer(torch.tensor([ids]), torch.tensor([len(ids)]), **args)
            o_n = m_no.infer(torch.tensor([ids]), torch.tensor([len(ids)]), **args)

        zt_e = o_e.latents[0][0].numpy()
        zt_n = o_n.latents[0][0].numpy()
        dt_e = o_e.durations[0].numpy()
        dt_n = o_n.durations[0].numpy()
        if not np.array_equal(zt_e, zt_n):
            zt_mismatch.append(i)
        if not np.array_equal(dt_e, dt_n):
            dt_mismatch.append(i)

        y_e = o_e.audio.squeeze().numpy().astype(np.float32)
        y_n = o_n.audio.squeeze().numpy().astype(np.float32)
        assert y_e.shape == y_n.shape
        assert zt_e.shape[0] == 192 and zt_e.shape[1] * HOP == y_e.shape[-1]
        assert dt_e.shape[-1] == len(ids)

        noise = y_e - y_n
        snr = 10 * np.log10(float((y_e**2).sum()) / float((noise**2).sum()))

        sec = len(y_e) / SR
        mora = sum(1 for t in ids if t not in (0, 1, 2, 3, 7, 8, 9)) / 2.0
        rate = mora / sec
        assert 4.0 <= rate <= 12.0, f"発話速度 {rate:.1f} mora/s — 音素化を疑え"

        toks = [id2tok[t] for t in ids]
        row: dict = {
            "idx": i,
            "text": text,
            "sec": round(sec, 3),
            "mora_per_sec": round(rate, 2),
            "n_ids": len(ids),
            "n_frames": int(zt_e.shape[1]),
            "snr_ema_vs_noema_db": round(float(snr), 3),
            "zT_bit_identical": bool(np.array_equal(zt_e, zt_n)),
            "dT_bit_identical": bool(np.array_equal(dt_e, dt_n)),
        }
        for tag, wav in (("ema", y_e), ("noema", y_n)):
            p = OUT_DIR / tag / f"t{i:02d}.wav"
            sf.write(p, np.concatenate([pad, wav, pad]), SR)
            row[f"wav_{tag}"] = str(p)
            f_mean, f_seg, f_fr = class_sfm(wav, dt_e, toks, S_JA)
            v_mean, v_seg, v_fr = class_sfm(wav, dt_e, toks, VOWELS)
            row[f"sfm_fric_{tag}"] = None if np.isnan(f_mean) else round(f_mean, 5)
            row[f"sfm_vowel_{tag}"] = None if np.isnan(v_mean) else round(v_mean, 5)
            if tag == "ema":
                row["n_fric_segments"] = f_seg
                row["n_fric_frames"] = f_fr
                row["n_vowel_segments"] = v_seg
                row["n_vowel_frames"] = v_fr
        rows.append(row)
        print(f"{i:>3} {sec:>6.2f} {rate:>7.2f} {snr:>8.2f}  {text[:36]}")

    snrs = [r["snr_ema_vs_noema_db"] for r in rows]
    print(
        f"\nyT SNR (EMA あり基準, なしを誤差とみなす): "
        f"平均 {np.mean(snrs):.2f} dB / 中央 {np.median(snrs):.2f} / "
        f"最小 {min(snrs):.2f} / 最大 {max(snrs):.2f} dB"
    )
    print(f"zT bit 一致: {len(rows) - len(zt_mismatch)}/{len(rows)}   "
          f"dT bit 一致: {len(rows) - len(dt_mismatch)}/{len(rows)}")

    # --- 品質指標 ---
    paths_e = [r["wav_ema"] for r in rows]
    paths_n = [r["wav_noema"] for r in rows]

    print("\nUTMOS をロード中 (tarepan/SpeechMOS:v1.2.0)…")
    utmos_e = measure_utmos(paths_e)
    utmos_n = measure_utmos(paths_n)

    print("SCOREQ (synthetic / nr) …")
    from saanotts_jp.scoreq_metric import score_files  # noqa: PLC0415

    sc = score_files(paths_e + paths_n, domain="synthetic", mode="nr")
    scoreq_e = [sc[p] for p in paths_e]
    scoreq_n = [sc[p] for p in paths_n]

    for r, ue, un, se_, sn in zip(rows, utmos_e, utmos_n, scoreq_e, scoreq_n,
                                  strict=True):
        r["utmos_ema"] = round(ue, 4)
        r["utmos_noema"] = round(un, 4)
        r["scoreq_ema"] = round(se_, 4)
        r["scoreq_noema"] = round(sn, 4)

    tests = {
        "utmos": paired_stats("UTMOS", utmos_e, utmos_n),
        "scoreq": paired_stats("SCOREQ (synthetic/nr)", scoreq_e, scoreq_n),
    }

    fric_e = [r["sfm_fric_ema"] for r in rows]
    fric_n = [r["sfm_fric_noema"] for r in rows]
    vow_e = [r["sfm_vowel_ema"] for r in rows]
    vow_n = [r["sfm_vowel_noema"] for r in rows]
    tests["sfm_fricative_2_8khz"] = paired_stats("SFM 摩擦音 2–8 kHz", fric_e, fric_n)
    tests["sfm_vowel_2_8khz"] = paired_stats("SFM 母音 2–8 kHz（対照）", vow_e, vow_n)

    lim = config.get("language_id_map") or {
        c: i for i, c in enumerate(["ja", "en", "zh", "es", "fr", "pt"])
    }
    canonical = canonical_path_check(m_ema, m_no, texts, pim, lim)
    sweep = snr_sweep(m_ema, m_no, pim, lim, n_want=60)

    report = {
        "task": "D-6: 波形ラベル yT に EMA を当てるか",
        "repro": "uv run --extra eval python scripts/d6_ema_ablation.py",
        "teacher": {
            "repo": "ayousanz/piper-plus-zero-shot-tsukuyomi",
            "file": CKPT,
            "snapshot": snap.rstrip("/").split("/")[-1],
            "ema_decay": ema_state.get("decay"),
            "ema_num_updates": ema_state.get("num_updates"),
            "n_shadow_params": len(ema_state["shadow_params"]),
        },
        "input_path": (
            "kanji -> intermediate (kana_g2p) -> phoneme ids, prosody=zeros "
            "(D-014 のラベル生成経路と同一。b5 は canonical G2P + 実 prosody なので"
            "UTMOS の絶対値は b5 と直接比較しない)"
        ),
        "texts_source": "reports/b5_teacher_baseline.json の 24 文（同一テキスト）",
        "order_proof": order_proof,
        "canonical_export": {
            "file": f"{PIPER_PLUS}/src/python/piper_train/export_onnx.py",
            "lines": "510-554",
            "behavior": (
                "export_onnx は ema_generator_state があれば **無条件で** "
                "apply_ema_shadow_params(model_g.dec, ...) を呼び、その後で "
                "remove_weight_norm() する。EMA を無効化する CLI フラグは存在しない "
                "(argparse は debug/simplify/stochastic/no-fp16/unify-emb-lang/"
                "export-mode のみ)。つまり公開 ONNX = EMA 適用済みが canonical。"
            ),
        },
        "bit_identity": {
            "zT_identical_pairs": len(rows) - len(zt_mismatch),
            "dT_identical_pairs": len(rows) - len(dt_mismatch),
            "n_pairs": len(rows),
            "zT_mismatch_idx": zt_mismatch,
            "dT_mismatch_idx": dt_mismatch,
        },
        "yT_snr_db": {
            "mean": round(float(np.mean(snrs)), 3),
            "median": round(float(np.median(snrs)), 3),
            "sd": round(float(np.std(snrs, ddof=1)), 3),
            "min": round(float(min(snrs)), 3),
            "max": round(float(max(snrs)), 3),
            "note": "EMA あり を信号、あり−なし を誤差とした SNR",
        },
        "sfm": {
            "band_hz": [SFM_LO_HZ, SFM_HI_HZ],
            "n_fft": N_FFT,
            "hop": HOP,
            "fricative_set": sorted(S_JA),
            "vowel_set": sorted(VOWELS),
            "min_frames_per_segment": 2,
            "total_fricative_segments": int(sum(r["n_fric_segments"] for r in rows)),
            "total_fricative_frames": int(sum(r["n_fric_frames"] for r in rows)),
            "total_vowel_segments": int(sum(r["n_vowel_segments"] for r in rows)),
            "total_vowel_frames": int(sum(r["n_vowel_frames"] for r in rows)),
        },
        "tests": tests,
        "canonical_path_robustness": canonical,
        "snr_sweep_by_duration": sweep,
        "utterances": rows,
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "device": "cpu",
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip(),
        },
        "caveats": [
            "UTMOS / SCOREQ とも日本語では較正されていない (D-008 / D-013)。"
            "絶対値を論文の英語モデルと比較しないこと。ここでは同一文の対比較にしか使っていない。",
            "n=24。有意差が出なかった項目は「差が無い」ではなく「n=24 では検出できない」。",
            "SFM のフレーム対応は cumsum(ceil(dT)) からの概算で、"
            "STFT は center=True なので各フレームの窓が前後 512 sample はみ出す。",
            "SFM は摩擦音だけでなく母音でも同方向に上がる（広帯域の効果）ので、"
            "摩擦音特異的な改善とは言えない。摩擦音の方が絶対差が約 3.7 倍大きいだけ。",
            "SNR は「EMA あり」を信号、「あり−なし」を誤差とした定義。"
            "どちらを分子に置くかで値が変わるので、他の記録と比べるときは定義を揃えること。",
            "聴取は行っていない。UTMOS と SCOREQ が逆を向いた場合の最終判断は聴取が要る。",
            "摩擦音 SFM の母数は 24 文で 130 区間 / 316 フレームしかない"
            "（母音は 702 区間 / 1660 フレーム）。検定は発話単位 n=24 で行っているが、"
            "1 発話あたりの摩擦音フレームは平均 13 程度で、発話内平均のばらつきは大きい。",
        ],
        "recommendation": {
            "decision": "EMA を適用した yT を蒸留ターゲットにする（現状維持）",
            "primary_reason": (
                "canonical と揃える。piper-plus の export_onnx は EMA を無条件で適用し、"
                "無効化するフラグが無い。公開されている教師 = EMA 適用済みなので、"
                "EMA なしを蒸留すると『誰も配布していない教師』を真似ることになる。"
            ),
            "quality_evidence": (
                "論文の主指標 SCOREQ は 2 経路とも有意差なし "
                f"(p={tests['scoreq']['p_paired_t']} / "
                f"{canonical['scoreq']['p_paired_t']})。"
                "UTMOS は EMA なしが有意に高い "
                f"(差 {tests['utmos']['mean_diff_ema_minus_noema']} / "
                f"{canonical['utmos']['mean_diff_ema_minus_noema']}, "
                f"p={tests['utmos']['p_paired_t']} / "
                f"{canonical['utmos']['p_paired_t']}) が、"
                "差は UTMOS の発話間 sd の約 1/6 で、日本語では較正されていない指標 (D-008)。"
                "摩擦音 SFM は EMA ありが有意に高い "
                f"(+{tests['sfm_fricative_2_8khz']['mean_diff_ema_minus_noema']}, "
                f"p={tests['sfm_fricative_2_8khz']['p_paired_t']}) = より雑音的で、"
                "論文が見逃した whistly sibilant の欠陥から遠い側。"
                "ただし母音でも同方向に動くので摩擦音特異的ではない。"
            ),
            "open": (
                "UTMOS と SFM が逆を向いている。決着には聴取が要るが、"
                "SCOREQ に差が無くフットプリントも同じである以上、"
                "canonical に合わせる根拠のほうが強い。"
            ),
        },
        "elapsed_sec": round(time.perf_counter() - t_start, 1),
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n→ {REPORT}  ({report['elapsed_sec']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
