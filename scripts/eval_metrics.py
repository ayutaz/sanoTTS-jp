#!/usr/bin/env python3
"""再利用可能な音声品質メトリクス（UTMOS + SCOREQ 4 設定）と、その統計。

`scripts/b5_measure_mos.py` は**壊さずそのまま残してある**。あちらは
`reports/b5_teacher_baseline.json` を読んで UTMOS（+ SCOREQ synthetic/nr）を
書き戻す B-5 専用の手順で、こちらは**任意の wav 群に対して 4 設定の SCOREQ を
並べて出す**ための土台。生徒の評価もこちらを使う。

⚠️ **どちらの指標も日本語では較正されていない。**

* UTMOS: VoiceMOS 2022 main = BVCC（英語）/ OOD = BC2019（中国語）で学習。日本語なし
* SCOREQ `data_domain="synthetic"`: **VoiceMOS 22 Train Set**（同チャレンジ main track の
  BVCC = 英語）で学習
* SCOREQ `data_domain="natural"`: 学習セットは **NISQA TRAIN SIM**（符号化・背景雑音・
  パケットロスの伝送劣化シミュレーション）。ONNX のファイル名も `*_telephone.onnx` で、
  「自然音声一般」用ではない。**TTS の評価には向かない**
  （出典: 同梱 `scoreq-1.0.1.dist-info/METADATA` の表と `scoreq/scoreq.py:_init_onnx`）

したがって**絶対値を論文の英語スコア (4.09 / 2.54 / 4.68) と直接比較してはいけない**。
教師比・人間音声比で報告すること（D-013）。

`mode` の向きに注意:

* `mode="nr"`  … MOS 予測。**高いほど良い**（1〜5 のスケール）
* `mode="ref"` … non-matching reference との埋め込み L2 距離。**低いほど良い**

実行（単体 CLI）:
    uv run --extra eval python scripts/eval_metrics.py a.wav b.wav --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import warnings

import numpy as np
import soundfile as sf
import torch

warnings.filterwarnings("ignore")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

UTMOS_SR = 16000
UTMOS_HUB = "tarepan/SpeechMOS:v1.2.0"
UTMOS_ENTRY = "utmos22_strong"

# (data_domain, mode) → 短縮名。ref は距離なので向きが逆
SCOREQ_CONFIGS = (
    ("synthetic", "nr"),
    ("natural", "nr"),
    ("synthetic", "ref"),
    ("natural", "ref"),
)
HIGHER_IS_BETTER = {"nr": True, "ref": False}


# --------------------------------------------------------------------------
# 測定
# --------------------------------------------------------------------------
def load_16k(path: str) -> torch.Tensor:
    """soundfile で読んで 16 kHz mono にリサンプルする。

    torchaudio.load は 2.11 から torchcodec を要求するので使わない
    （`scripts/b5_measure_mos.py:load_16k` と同一実装）。
    """
    import torchaudio

    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    tensor = torch.from_numpy(wav[:, 0]).unsqueeze(0)
    if sr != UTMOS_SR:
        tensor = torchaudio.transforms.Resample(sr, UTMOS_SR)(tensor)
    return tensor


_UTMOS = None


def measure_utmos(paths) -> list[float]:
    global _UTMOS
    if _UTMOS is None:
        _UTMOS = torch.hub.load(UTMOS_HUB, UTMOS_ENTRY, trust_repo=True)
        _UTMOS.eval()
    out = []
    for path in paths:
        with torch.no_grad():
            out.append(float(_UTMOS(load_16k(str(path)), sr=UTMOS_SR).item()))
    return out


def measure_scoreq(paths, domain="synthetic", mode="nr", ref_path=None) -> list[float]:
    """`src/saanotts_jp/scoreq_metric.py` のラッパー経由で測る（torchaudio shim 込み）。"""
    from saanotts_jp.scoreq_metric import score_files

    scored = score_files([str(p) for p in paths], domain=domain, mode=mode,
                         ref_path=None if ref_path is None else str(ref_path))
    return [scored[str(p)] for p in paths]


def measure_all(paths, ref_path=None, configs=SCOREQ_CONFIGS,
                with_utmos=True) -> dict[str, list[float]]:
    """UTMOS と SCOREQ 各設定をまとめて測る。返り値は `{metric_name: [値]}`。"""
    res: dict[str, list[float]] = {}
    if with_utmos:
        res["utmos"] = measure_utmos(paths)
    for domain, mode in configs:
        if mode == "ref" and ref_path is None:
            continue
        res[f"scoreq_{domain}_{mode}"] = measure_scoreq(
            paths, domain=domain, mode=mode, ref_path=ref_path
        )
    return res


def wav_seconds(paths) -> list[float]:
    return [sf.info(str(p)).frames / sf.info(str(p)).samplerate for p in paths]


def pad_wav(src, dst, pad_sec: float) -> pathlib.Path:
    """前後に無音を足したコピーを作る（推定器の端の扱いを揃えるため）。"""
    wav, sr = sf.read(str(src), dtype="float32", always_2d=True)
    pad = np.zeros((int(pad_sec * sr), wav.shape[1]), dtype=np.float32)
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dst, np.concatenate([pad, wav, pad]), sr)
    return dst


def trim_wav(src, dst, trim_sec: float) -> pathlib.Path:
    """前後 `trim_sec` を削ったコピーを作る。"""
    wav, sr = sf.read(str(src), dtype="float32", always_2d=True)
    n = int(trim_sec * sr)
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dst, wav[n : len(wav) - n], sr)
    return dst


# --------------------------------------------------------------------------
# 統計（n が小さいので点推定だけを書かない。C-004 / B-6 の反省）
# --------------------------------------------------------------------------
def _t_crit(df: int) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.975, df))


def summarize(values, name: str = "") -> dict:
    """平均の 95% CI（t 分布）まで含めた要約。**n を必ず持たせる**。"""
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    sd = float(arr.std(ddof=1)) if n > 1 else float("nan")
    half = _t_crit(n - 1) * sd / math.sqrt(n) if n > 1 else float("nan")
    return {
        "name": name,
        "n": n,
        "mean": round(float(arr.mean()), 4),
        "sd": round(sd, 4),
        "median": round(float(np.median(arr)), 4),
        "min": round(float(arr.min()), 4),
        "max": round(float(arr.max()), 4),
        "ci95_mean": [round(float(arr.mean() - half), 4),
                      round(float(arr.mean() + half), 4)],
    }


def ratio_ci(num, den, n_boot: int = 20000, seed: int = 0) -> dict:
    """2 群の平均の比とその bootstrap 95% CI（独立 2 標本、非対応）。"""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(num, float), np.asarray(den, float)
    boots = np.array([
        rng.choice(a, len(a), replace=True).mean()
        / rng.choice(b, len(b), replace=True).mean()
        for _ in range(n_boot)
    ])
    return {
        "ratio": round(float(a.mean() / b.mean()), 4),
        "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                 round(float(np.percentile(boots, 97.5)), 4)],
        "n_num": len(a), "n_den": len(b), "n_boot": n_boot, "seed": seed,
    }


def corr_ci(x, y, seed: int = 0, n_boot: int = 20000) -> dict:
    """Pearson（Fisher z の CI）と Spearman（bootstrap CI）を両方返す。"""
    from scipy import stats

    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    r, p_r = stats.pearsonr(x, y)
    rho, p_rho = stats.spearmanr(x, y)
    z = np.arctanh(np.clip(r, -0.999999, 0.999999))
    se = 1.0 / math.sqrt(n - 3)
    lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    rhos = np.array([stats.spearmanr(x[i], y[i]).statistic for i in idx])
    rhos = rhos[np.isfinite(rhos)]
    return {
        "n": n,
        "pearson_r": round(float(r), 4),
        "pearson_p": float(f"{p_r:.3g}"),
        "pearson_ci95": [round(float(lo), 4), round(float(hi), 4)],
        "spearman_rho": round(float(rho), 4),
        "spearman_p": float(f"{p_rho:.3g}"),
        "spearman_ci95": [round(float(np.percentile(rhos, 2.5)), 4),
                          round(float(np.percentile(rhos, 97.5)), 4)],
    }


def cohens_d(a, b, n_boot: int = 20000, seed: int = 0) -> dict:
    """2 群の分離度（|d| が大きいほど指標として敏感）。bootstrap CI 付き。"""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)

    def _d(x, y):
        s = math.sqrt((x.var(ddof=1) + y.var(ddof=1)) / 2)
        return (x.mean() - y.mean()) / s if s > 0 else float("nan")

    boots = np.array([
        _d(rng.choice(a, len(a), replace=True), rng.choice(b, len(b), replace=True))
        for _ in range(n_boot)
    ])
    return {"d": round(float(_d(a, b)), 4),
            "ci95": [round(float(np.percentile(boots, 2.5)), 4),
                     round(float(np.percentile(boots, 97.5)), 4)]}


def mannwhitney(a, b) -> dict:
    from scipy import stats

    u, p = stats.mannwhitneyu(np.asarray(a, float), np.asarray(b, float),
                              alternative="two-sided")
    n1, n2 = len(a), len(b)
    return {"U": float(u), "p": float(f"{p:.3g}"),
            "auc_a_gt_b": round(float(u) / (n1 * n2), 4), "n1": n1, "n2": n2}


CALIBRATION_WARNING = (
    "UTMOS / SCOREQ はいずれも日本語では較正されていない。"
    "UTMOS の学習データは VoiceMOS 2022 main=BVCC(英語) / OOD=BC2019(中国語)。"
    "SCOREQ は同梱 README (scoreq-1.0.1.dist-info/METADATA) の表によると "
    "synthetic = VoiceMOS 22 Train Set (= 同チャレンジ main track の BVCC。英語)、"
    "natural = NISQA TRAIN SIM (符号化・雑音・パケットロスの伝送劣化シミュレーション)。"
    "どちらの学習セットにも日本語 TTS は入っていない。"
    "日本語の絶対値を論文の英語スコア (教師 4.68 / embedded 2.54) と"
    "直接比較してはいけない。教師比・人間音声比で報告すること (D-013)。"
    "【出典の区別】学習セット名は同梱 README の記載（本タスクで確認済み）、"
    "「BVCC が英語」は VoiceMOS 2022 の公表内容であって本タスクの実測ではない。"
    "実測で言えるのは「実人間の日本語スタジオ録音でも SCOREQ synthetic/nr は "
    "2.498 (n=24) しか出ない」という圧縮の事実のほう。"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--ref", default=None, help="SCOREQ ref モードの NMR wav")
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-utmos", action="store_true")
    args = ap.parse_args()

    res = measure_all(args.wavs, ref_path=args.ref, with_utmos=not args.no_utmos)
    summ = {k: summarize(v, k) for k, v in res.items()}
    for k, s in summ.items():
        arrow = "↑" if not k.endswith("_ref") else "↓"
        print(f"{k:<24} {arrow} n={s['n']}  mean {s['mean']:.4f}  "
              f"95%CI [{s['ci95_mean'][0]:.4f}, {s['ci95_mean'][1]:.4f}]  "
              f"sd {s['sd']:.4f}")
    print(f"\n⚠️ {CALIBRATION_WARNING}")
    if args.json:
        out = {"files": [str(p) for p in args.wavs], "per_file": res,
               "summary": summ, "calibration_warning": CALIBRATION_WARNING,
               "repro": "uv run --extra eval python scripts/eval_metrics.py "
                        + " ".join(args.wavs)}
        pathlib.Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=1))
        print(f"→ {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
