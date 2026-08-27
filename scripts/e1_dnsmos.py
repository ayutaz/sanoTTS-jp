#!/usr/bin/env python3
"""E-1: DNSMOS P.835 を 4 レーンで測る（**併記プローブ**であって合否ゲートではない）。

背景: 上流（英語・`docs/upstream-sanotts.md`）は「金属的アーティファクトは
SCOREQ で**高**得点・DNSMOS で低得点」と書いている。本プロジェクトの主指標
（SCOREQ synthetic/nr + UTMOS、D-020）は**どちらも自然さの予測器**なので、
金属的な欠陥が出ていても原理的に見えない可能性がある。それを塞ぐ追試。

============================================================================
事前登録（**測定を 1 回も走らせる前に commit した**。git log で確認できる）
============================================================================

**予測: 生徒の SIG / OVRL は、金属的アーティファクトの有無に関係なく教師より
低く出る。** 生徒は既に過平滑・こもり（1–2 kHz の帯域エネルギー欠損、SFM が教師
より平坦）が測られており、この欠陥だけで SIG は下がる。

**したがって「生徒の DNSMOS が低かった」を金属的アーティファクトの証拠として
読まない。特異性は金属様対照アーム（G7）でしか測れない。**

副次的な事前登録:

* BAK は 4 レーンとも飽和する（どれもクリーンな単一話者音声）ので、
  レーン間の差は SIG より小さい。
* DNSMOS は日本語で較正されていないので、**合否ラインを引かない**（D-013）。
* SCOREQ と食い違ったら、どちらが正しいかを決めずに食い違いをそのまま報告する。

⚠️ **この事前登録は完全な盲検ではない。** 設計時に **n=1 の疎通確認**
（`BASIC5000_0083` で教師 SIG 3.0371 / 生徒 SIG 2.3265）を既に観測している。
つまり「生徒のほうが低い」という向きは知ったうえで書いている。**盲検なのは
効果量・特異性・ゲートの結果**であって、符号ではない。n=1 の値は結果として
扱わない（C-017 の形）。

============================================================================
測るもの
============================================================================

| レーン | 実体 | n | 対応 |
|---|---|---|---|
| T_v2 | reports/eval_v2/teacher   | 24 | S_v2 と uid で**対応あり** |
| S_v2 | reports/eval_v2/student   | 24 | 〃 |
| H    | HF tsukuyomi-chan-ljspeech VOICEACTRESS100_001..024 | 24 | 対応なし |
| T_b5 | reports/b5_teacher_wav    | 24 | **橋渡し**（人間天井はこの集合と並べて測られた） |

T_b5 を足す理由: (1) 人間天井（SCOREQ 2.4983 / UTMOS 2.3047）は T_b5 と並べて
測られており、T_v2 とは別の 24 文。(2) 同一システムの独立 2 標本になるので、
**n=24 での標本間ばらつきの物差し**が取れる。

============================================================================
ゲート（すべて「落ちる壊し方」を持っている）
============================================================================

G0 名前衝突      UTMOS の torch.hub リポが同名 `speechmos` を同梱している。
                 素の import は**両方向で落ちる**（陽性対照）。ラッパの分離 import が
                 両方向で通り、素の import と同値であること
G1 決定性        別プロセス 2 回で 4 スコアが float64 で完全一致
G2 リサンプラ    wrapper == 明示 soxr_hq + ndarray == ライブラリのパス経路
G3 16 kHz 化     sr==22050 / subtype==PCM_16 / n_out == ceil(n_in*16000/sr)
G4 レーン同一性  manifest 一致 + **レーン間で SHA-256 が 1 件も重複しない**
G5 既存指標再現  同じ wav の SCOREQ/UTMOS 平均が eval.json / b5_scoreq.json と一致
G6 陽性対照      4 系統 ×3 段の劣化で SIG と OVRL が単調減少（4/4 系統）
G7 金属様対照    3 アームが原音と別物 + **DNSMOS でない**プローブで分離する
G8 交絡          |Δ(padding)| と |Δ(policy)| が |Δ(生徒−教師)| より小さい

実行:
    uv run --extra eval python scripts/e1_dnsmos.py
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from eval_metrics import (  # noqa: E402
    CALIBRATION_WARNING,
    cohens_d,
    corr_ci,
    mannwhitney,
    measure_scoreq,
    measure_utmos,
    ratio_ci,
    summarize,
)
from saanotts_jp import dnsmos_metric as DM  # noqa: E402
from saanotts_jp.flatness import band_slice, frame_sfm, stft_mag  # noqa: E402

OUT = pathlib.Path("reports/e1_dnsmos/e1.json")
SR_SRC = 22050
PAD_SEC = 0.3
HUMAN_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/datasets--ayousanz--tsukuyomi-chan-ljspeech/"
    "snapshots/*/wavs/*.wav"
)
REPRO = "uv run --extra eval python scripts/e1_dnsmos.py"

PREREGISTERED_PREDICTION = (
    "生徒の SIG / OVRL は、金属的アーティファクトの有無に関係なく教師より低く出る。"
    "生徒には既に過平滑・こもりが測られており、それだけで SIG は下がる。"
    "したがって『生徒の DNSMOS が低かった』を金属的アーティファクトの証拠として"
    "読まない。特異性は金属様対照アーム（G7）でしか測れない。"
    "副次: BAK は 4 レーンとも飽和し、レーン間の差は SIG より小さい。"
    "DNSMOS に合否ラインは引かない（D-013）。"
    "SCOREQ と食い違ったら、どちらが正しいかを決めずに食い違いをそのまま報告する。"
    "⚠️ 完全な盲検ではない: 設計時に n=1 の疎通確認（BASIC5000_0083 で "
    "教師 SIG 3.0371 / 生徒 SIG 2.3265）を観測済みで、符号は知っている。"
    "盲検なのは効果量・特異性・ゲートの結果のほう。"
)

# 期待値（既存記録。G5 はこれとの一致だけを見る再現ゲートで、品質の合格基準ではない）
EXPECTED = {
    "T_v2": {"scoreq_synthetic_nr": 1.9732, "utmos": 1.7925},
    "S_v2": {"scoreq_synthetic_nr": 1.2063, "utmos": 1.3585},
    "T_b5": {"scoreq_synthetic_nr": 2.0488, "utmos": 1.7479},
    "H":    {"scoreq_synthetic_nr": 2.4983, "utmos": 2.3047},
}
LANES = ("T_v2", "S_v2", "T_b5", "H")
SCORES = DM.SCORE_KEYS


# ==========================================================================
# レーン
# ==========================================================================
def _verify_against_wheel(pkg_dir: pathlib.Path) -> dict:
    """uv が入れた実体が `reports/r1_dnsmos/speechmos.whl` と一致するか照合する。

    ⚠️ ホイール自体の出自（同梱 ONNX が microsoft/DNS-Challenge のものか）は
    **照合していない**。ここで言えるのは「同じホイールが入っている」だけ。
    """
    import zipfile

    whl = pathlib.Path("reports/r1_dnsmos/speechmos.whl")
    if not whl.is_file():
        return {"checked": False, "reason": f"{whl} が無い"}
    z = zipfile.ZipFile(whl)
    names = [n for n in z.namelist()
             if n.startswith("speechmos/") and (n.endswith(".py") or n.endswith(".onnx"))]
    site = pkg_dir.parent
    mismatch = []
    for n in names:
        f = site / n
        if not f.is_file():
            mismatch.append([n, "missing"])
        elif hashlib.sha256(z.read(n)).hexdigest() != sha256(f):
            mismatch.append([n, "differ"])
    return {"checked": True, "wheel": str(whl),
            "wheel_sha256": hashlib.sha256(whl.read_bytes()).hexdigest(),
            "n_files_compared": len(names), "mismatch": mismatch,
            "match": not mismatch,
            "note": "ホイールの出自（同梱 ONNX が microsoft/DNS-Challenge 由来か）は"
                    "**照合していない**。言えるのは『同じホイールが入っている』だけ"}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_lanes() -> dict[str, list[str]]:
    """4 レーンのファイルを解決する。人間レーンは b5 の `resolve_human()` を再利用。"""
    from b5_scoreq_baseline import resolve_human

    ev = json.loads(pathlib.Path("reports/eval_v2/eval.json").read_text())
    uids = [u["uid"] for u in ev["utterances"]]
    t = [f"reports/eval_v2/teacher/{u}.wav" for u in uids]
    s = [f"reports/eval_v2/student/{u}.wav" for u in uids]
    for p in t + s:
        if not os.path.exists(p):
            raise SystemExit(f"eval.json の uid に対応する wav が無い: {p}")
    b5 = sorted(glob.glob("reports/b5_teacher_wav/*.wav"))
    h = resolve_human()          # mean_sec の assert を通る
    lanes = {"T_v2": t, "S_v2": s, "T_b5": b5, "H": h}
    for k, v in lanes.items():
        if len(v) != 24:
            raise SystemExit(f"{k}: n={len(v)} != 24")
    return lanes


def build_manifest(lanes) -> dict:
    man = {}
    for k, paths in lanes.items():
        rows = []
        for p in paths:
            info = sf.info(p)
            rows.append({
                "path": p, "sha256": sha256(p),
                "sr": int(info.samplerate), "subtype": info.subtype,
                "frames": int(info.frames),
                "sec": round(info.frames / info.samplerate, 6),
            })
        man[k] = rows
    return man


def gate_g3_g4(man) -> dict:
    """G3（本当に 16 kHz に落ちたか）と G4（レーンの同一性）。"""
    bad_sr = [r["path"] for k in man for r in man[k] if r["sr"] != SR_SRC]
    bad_sub = [r["path"] for k in man for r in man[k] if r["subtype"] != "PCM_16"]
    bad_len = []
    for k in man:
        for r in man[k]:
            exp = math.ceil(r["frames"] * DM.sr_target() / r["sr"])
            got = len(DM.resample_16k(
                sf.read(r["path"], dtype="float32", always_2d=True)[0][:, 0], r["sr"]))
            if got != exp:
                bad_len.append((r["path"], got, exp))
    seen: dict[str, str] = {}
    dups = []
    for k in man:
        for r in man[k]:
            if r["sha256"] in seen and seen[r["sha256"]] != k:
                dups.append((r["path"], seen[r["sha256"]], k))
            seen.setdefault(r["sha256"], k)
    g3 = not (bad_sr or bad_sub or bad_len)
    g4 = not dups
    return {
        "G3_pass": g3, "G3_bad_sr": bad_sr, "G3_bad_subtype": bad_sub,
        "G3_bad_length": bad_len,
        "G3_rule": "n_out == ceil(n_in * 16000 / sr_in)（soxr_hq の実測則。"
                   "round では 96 本中 46 本が外れる）",
        "G4_pass": g4, "G4_cross_lane_sha_dups": dups,
        "G4_n_unique_sha": len(seen), "G4_n_files": sum(len(v) for v in man.values()),
    }


# ==========================================================================
# 音声の加工（すべて float32 / 22.05 kHz のまま。int16 往復は入れない）
# ==========================================================================
def read_wav(path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    assert sr == SR_SRC, (path, sr)
    return np.ascontiguousarray(x[:, 0])


def pad(x, sec=PAD_SEC):
    n = int(sec * SR_SRC)
    return np.concatenate([np.zeros(n, np.float32), x, np.zeros(n, np.float32)])


def trim(x, sec=PAD_SEC):
    n = int(sec * SR_SRC)
    return np.ascontiguousarray(x[n:len(x) - n])


def guard_peak(y, limit=0.99):
    """[-1,1] を越えたら**全体にゲインを掛けて**収める（clip しない）。"""
    p = float(np.abs(y).max())
    if p <= limit:
        return y.astype(np.float32), 1.0
    g = limit / p
    return (y * g).astype(np.float32), float(g)


def snr_db(ref, test) -> float:
    n = min(len(ref), len(test))
    a, b = ref[:n].astype(np.float64), test[:n].astype(np.float64)
    e = a - b
    if not np.any(e):
        return float("inf")
    return float(10 * np.log10(np.sum(a ** 2) / np.sum(e ** 2)))


def add_noise(x, target_snr_db, seed):
    rng = np.random.default_rng(seed)
    p = float(np.mean(x.astype(np.float64) ** 2))
    sigma = math.sqrt(p / (10 ** (target_snr_db / 10.0)))
    return guard_peak(x + rng.normal(0, sigma, len(x)).astype(np.float32))


def requantize(x, bits):
    q = 2 ** (bits - 1) - 1
    return guard_peak(np.round(x * q) / q)


def hard_clip(x, frac):
    t = frac * float(np.abs(x).max())
    return guard_peak(np.clip(x, -t, t))


def lowpass(x, cutoff_hz):
    from scipy import signal

    b, a = signal.butter(8, cutoff_hz / (SR_SRC / 2), btype="low")
    return guard_peak(signal.filtfilt(b, a, x.astype(np.float64)).astype(np.float32))


def griffin_lim(x, n_iter, seed=0):
    """位相を捨てて Griffin-Lim で復元する（**位相破壊 = 金属様の代理**）。"""
    import librosa

    S = np.abs(librosa.stft(x, n_fft=1024, hop_length=256))
    y = librosa.griffinlim(S, n_iter=n_iter, hop_length=256, n_fft=1024,
                           init="random", random_state=seed, length=len(x))
    return guard_peak(y.astype(np.float32))


def amplitude_modulate(x, depth, f_hz=SR_SRC / 256.0):
    """フレームレート 86.13 Hz の AM（フレーム境界の周期性 = 金属様の代理）。"""
    t = np.arange(len(x), dtype=np.float64) / SR_SRC
    return guard_peak((x * (1.0 + depth * np.sin(2 * np.pi * f_hz * t))).astype(np.float32))


def frame_freeze(x, hop=256, every=2):
    """`every` フレームに 1 回、直前フレームを複製する（フレーム凍結）。"""
    y = x.copy()
    n = len(x) // hop
    for i in range(1, n):
        if i % every == 0:
            y[i * hop:(i + 1) * hop] = x[(i - 1) * hop:i * hop]
    return guard_peak(y)


# ==========================================================================
# DNSMOS でないプローブ（G7 (c) の分離検査。**循環論法を避けるため必須**）
# ==========================================================================
def probe_sfm(x, band):
    """エネルギー上位 40% フレームの帯域 SFM（音素整列なし）。"""
    mag = stft_mag(x, 1024, 256)
    bins = band_slice(1024, SR_SRC, band)
    e = mag[bins, :].sum(axis=0)
    if not np.any(e > 0):
        return float("nan")
    k = max(1, int(0.4 * mag.shape[1]))
    idx = np.argsort(e)[::-1][:k]
    v = frame_sfm(mag[:, idx], bins, 1)
    v = v[np.isfinite(v)]
    return float(np.mean(v)) if v.size else float("nan")


def probe_mod_line(x, f_hz=SR_SRC / 256.0):
    """包絡の 86.13 Hz 変調線の強さ（40–200 Hz の中央値で正規化）。

    包絡は 64 サンプル窓 / hop 16（= 1378 Hz）の RMS。**hop 256 では
    86.13 Hz が DC に折り返す**ので使えない。
    """
    win, hop = 64, 16
    n = (len(x) - win) // hop
    if n < 64:
        return float("nan")
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    env = np.sqrt(np.mean(x[idx].astype(np.float64) ** 2, axis=1))
    env -= env.mean()
    fs_env = SR_SRC / hop
    P = np.abs(np.fft.rfft(env * np.hanning(len(env)))) ** 2
    f = np.fft.rfftfreq(len(env), 1 / fs_env)
    band = (f >= 40) & (f <= 200)
    if not np.any(band):
        return float("nan")
    k = int(np.argmin(np.abs(f - f_hz)))
    med = float(np.median(P[band]))
    return float(P[k] / med) if med > 0 else float("nan")


# ==========================================================================
# 統計
# ==========================================================================
def paired_stats(a, b, n_boot=20000, seed=0) -> dict:
    """対応のある差 (a - b) の要約。比の CI も**対応 bootstrap** で出す。"""
    from scipy import stats

    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    n = len(d)
    sd = float(d.std(ddof=1))
    t_two = float(stats.t.ppf(0.975, n - 1))
    t_pow = float(stats.t.ppf(0.80, n - 1))
    se = sd / math.sqrt(n)
    tt = stats.ttest_rel(a, b)
    try:
        w = stats.wilcoxon(a, b)
        w_p = float(f"{w.pvalue:.3g}")
    except ValueError:
        w_p = float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boots = a[idx].mean(axis=1) / b[idx].mean(axis=1)
    return {
        "n": n,
        "mean_a": round(float(a.mean()), 4), "mean_b": round(float(b.mean()), 4),
        "mean_diff": round(float(d.mean()), 4),
        "sd_diff": round(sd, 4),
        "ci95_diff": [round(float(d.mean() - t_two * se), 4),
                      round(float(d.mean() + t_two * se), 4)],
        "paired_t_p": float(f"{tt.pvalue:.3g}"),
        "wilcoxon_p": w_p,
        "ratio_a_over_b": round(float(a.mean() / b.mean()), 4),
        "ratio_ci95_paired_boot": [round(float(np.percentile(boots, 2.5)), 4),
                                   round(float(np.percentile(boots, 97.5)), 4)],
        "smallest_significant_diff": round(float(t_two * se), 4),
        "mde_80pct_power": round(float((t_two + t_pow) * se), 4),
        "mde_note": "smallest_significant_diff = この sd で p<0.05 になる最小の差。"
                    "mde_80pct_power = 検出力 80% の最小差。どちらも観測 sd 依存",
    }


def unpaired_mde(a, b) -> dict:
    from scipy import stats

    a, b = np.asarray(a, float), np.asarray(b, float)
    n1, n2 = len(a), len(b)
    sp = math.sqrt(((n1 - 1) * a.var(ddof=1) + (n2 - 1) * b.var(ddof=1)) / (n1 + n2 - 2))
    se = sp * math.sqrt(1 / n1 + 1 / n2)
    df = n1 + n2 - 2
    t2 = float(stats.t.ppf(0.975, df))
    t8 = float(stats.t.ppf(0.80, df))
    return {"smallest_significant_diff": round(float(t2 * se), 4),
            "mde_80pct_power": round(float((t2 + t8) * se), 4)}


def paired_diff_ci(d, n_boot=20000, seed=0) -> list[float]:
    from scipy import stats

    d = np.asarray(d, float)
    n = len(d)
    t = float(stats.t.ppf(0.975, n - 1))
    se = d.std(ddof=1) / math.sqrt(n)
    return [round(float(d.mean() - t * se), 4), round(float(d.mean() + t * se), 4)]


# ==========================================================================
# 測定本体
# ==========================================================================
def score_lane(paths, policy, transform=None) -> list[dict]:
    out = []
    for p in paths:
        x = read_wav(p)
        if transform is not None:
            x = transform(x)
        r = DM.score_array(x, SR_SRC, policy=policy)
        r["path"] = str(p)
        out.append(r)
    return out


def child_determinism(paths_json: str) -> int:
    """`--determinism-child`: 別プロセスでスコアを出して JSON に落とす。"""
    spec = json.loads(pathlib.Path(paths_json).read_text())
    res = {}
    for p in spec["paths"]:
        r = DM.score_path(p, policy=spec["policy"])
        res[p] = {k: r[k] for k in SCORES}
    print(json.dumps(res))
    return 0


def main() -> int:
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--determinism-child", default=None)
    args = ap.parse_args()
    if args.determinism_child:
        return child_determinism(args.determinism_child)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    scratch = OUT.parent / "scratch"
    scratch.mkdir(exist_ok=True)

    # ---- 環境 -------------------------------------------------------------
    # ⚠️ **`import speechmos` を書かないこと。** UTMOS の torch.hub リポジトリが
    # 同名パッケージを同梱していて、先に読んだ方が勝つ（G0 参照）。
    import librosa
    import onnxruntime
    from importlib.metadata import version as _pkg_version

    src = DM.dnsmos_source_path()
    mdl_dir = src.parent / "dnsmos_models"
    env = {
        "speechmos_version": _pkg_version("speechmos"),
        "speechmos_path": str(src.parent),
        "speechmos_wheel_check": _verify_against_wheel(src.parent),
        "dnsmos_SR": DM.sr_target(),
        "dnsmos_INPUT_LENGTH": DM.input_length_sec(),
        "onnx_models": {p.name: p.stat().st_size for p in sorted(mdl_dir.glob("*.onnx"))},
        "onnx_models_sha256": {p.name: sha256(p) for p in sorted(mdl_dir.glob("*.onnx"))},
        "librosa": librosa.__version__,
        "onnxruntime": onnxruntime.__version__,
        "resampler": DM.RES_TYPE,
        "soxr_available": __import__("importlib.util", fromlist=["x"])
                          .find_spec("soxr") is not None,
        "python": sys.version.split()[0],
    }
    print("speechmos", env["speechmos_path"])
    print("  SR", env["dnsmos_SR"], "INPUT_LENGTH", env["dnsmos_INPUT_LENGTH"])

    # ---- レーンと manifest（G3 / G4） -------------------------------------
    lanes = resolve_lanes()
    man = build_manifest(lanes)
    gates = gate_g3_g4(man)
    print(f"G3 (16 kHz 化) {'PASS' if gates['G3_pass'] else 'FAIL'}   "
          f"G4 (レーン同一性) {'PASS' if gates['G4_pass'] else 'FAIL'} "
          f"unique sha {gates['G4_n_unique_sha']}/{gates['G4_n_files']}")

    # ---- G0 パッケージ名の衝突（UTMOS の torch.hub リポと同名） ----------
    # `tarepan/SpeechMOS:v1.2.0` は `speechmos` という名前のパッケージを同梱する。
    # 素の `import speechmos.dnsmos` を使うと **同一プロセスで UTMOS と共存できない**。
    # ここでは (a) 衝突が実在すること（陽性対照）と (b) ラッパの分離 import が
    # 両方向で通ること、(c) 分離 import と素の import が同値であることを見る。
    def _sub(code):
        return subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, cwd=os.getcwd())

    hub = ("import torch; torch.hub.load('tarepan/SpeechMOS:v1.2.0',"
           "'utmos22_strong', trust_repo=True)")
    pre = ("import sys; sys.path.insert(0,'src'); "
           "from saanotts_jp.dnsmos_metric import score_path; ")
    probe = "reports/eval_v2/teacher/BASIC5000_0083.wav"
    naive_after_utmos = _sub(f"{hub}\nimport speechmos.dnsmos as D; print('OK')")
    utmos_after_naive = _sub(f"import speechmos.dnsmos as D\n{hub}\nprint('OK')")
    wrap_then_utmos = _sub(
        f"{pre}r=score_path('{probe}')\n{hub}\nprint('OK', repr(r['ovrl_mos']))")
    utmos_then_wrap = _sub(
        f"import sys; sys.path.insert(0,'src')\n{hub}\n"
        f"from saanotts_jp.dnsmos_metric import score_path\n"
        f"r=score_path('{probe}')\nprint('OK', repr(r['ovrl_mos']))")
    naive_only = _sub(
        "import speechmos.dnsmos as D\n"
        f"print(repr(float(D.run(__import__('librosa').load('{probe}', sr=16000)[0],"
        " 16000)['ovrl_mos'])))")
    wrap_vals = [c.stdout.strip().split()[-1] for c in (wrap_then_utmos, utmos_then_wrap)
                 if c.returncode == 0 and c.stdout.strip()]
    naive_val = naive_only.stdout.strip() if naive_only.returncode == 0 else None
    g0 = {
        "collision_is_real_naive_dnsmos_after_utmos_fails":
            naive_after_utmos.returncode != 0,
        "collision_is_real_utmos_after_naive_dnsmos_fails":
            utmos_after_naive.returncode != 0,
        "wrapper_then_utmos_ok": wrap_then_utmos.returncode == 0,
        "utmos_then_wrapper_ok": utmos_then_wrap.returncode == 0,
        "wrapper_equals_naive": bool(
            naive_val is not None and len(wrap_vals) == 2
            and wrap_vals[0] == wrap_vals[1] == naive_val),
        "naive_value": naive_val, "wrapper_values": wrap_vals,
        "hub_package_path":
            os.path.expanduser("~/.cache/torch/hub/tarepan_SpeechMOS_v1.2.0/speechmos"),
        "error_naive_after_utmos": naive_after_utmos.stderr.strip().splitlines()[-1]
            if naive_after_utmos.returncode != 0 else None,
        "error_utmos_after_naive": utmos_after_naive.stderr.strip().splitlines()[-1]
            if utmos_after_naive.returncode != 0 else None,
    }
    gates["G0_pass"] = all([g0["collision_is_real_naive_dnsmos_after_utmos_fails"],
                            g0["collision_is_real_utmos_after_naive_dnsmos_fails"],
                            g0["wrapper_then_utmos_ok"], g0["utmos_then_wrapper_ok"],
                            g0["wrapper_equals_naive"]])
    gates["G0_detail"] = g0
    gates["G0_rule"] = ("(陽性対照) 素の import は両方向で落ちる / "
                        "(本題) ラッパ経由なら両方向で通り、しかも素の import と"
                        "**同一の値**（repr 一致）を返す")
    print(f"G0 (speechmos 名前衝突) {'PASS' if gates['G0_pass'] else 'FAIL'}  "
          f"衝突実在 {g0['collision_is_real_naive_dnsmos_after_utmos_fails']}/"
          f"{g0['collision_is_real_utmos_after_naive_dnsmos_fails']}  "
          f"ラッパ両方向 {g0['wrapper_then_utmos_ok']}/{g0['utmos_then_wrapper_ok']}  "
          f"素と同値 {g0['wrapper_equals_naive']}")

    # ---- G2 リサンプラの 3 経路一致 ---------------------------------------
    g2_rows = []
    for p in (lanes["T_v2"][0], lanes["H"][0]):
        w = DM.score_path(p)
        x, sr = sf.read(p, dtype="float32", always_2d=True)
        e = DM.score_array(x[:, 0], sr)
        libp = DM.score_path_via_library(p)
        dmax = max(max(abs(w[k] - e[k]) for k in SCORES),
                   max(abs(w[k] - libp[k]) for k in SCORES))
        g2_rows.append({"path": p, "max_abs_delta_3paths": float(f"{dmax:.6g}"),
                        "wrapper": {k: w[k] for k in SCORES},
                        "library_path_route": libp})
    g2_pass = all(r["max_abs_delta_3paths"] == 0.0 for r in g2_rows)
    gates["G2_pass"] = g2_pass
    gates["G2_rows"] = g2_rows
    print(f"G2 (リサンプラ 3 経路一致) {'PASS' if g2_pass else 'FAIL'}  "
          f"max|Δ| = {max(r['max_abs_delta_3paths'] for r in g2_rows):.6g}")

    # ---- 本測定: 4 レーン × 2 policy × 2 padding 状態 ----------------------
    # padding 状態:
    #   as_is        … そのまま（T/S/b5 は前後 0.3 秒の無音つき、H は無し）
    #   all_padded   … H に 0.3 秒を足して全レーンをパディングありに揃える
    #   all_unpadded … T/S/b5 から 0.3 秒を削って全レーンをパディングなしに揃える
    def tf_for(lane, state):
        if state == "as_is":
            return None
        if state == "all_padded":
            return None if lane != "H" else pad
        if state == "all_unpadded":
            return trim if lane != "H" else None
        raise ValueError(state)

    dns: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for state in ("as_is", "all_padded", "all_unpadded"):
        dns[state] = {}
        for policy in DM.POLICIES:
            dns[state][policy] = {}
            for lane in LANES:
                dns[state][policy][lane] = score_lane(
                    lanes[lane], policy, tf_for(lane, state))
            print(f"  DNSMOS {state:<13} {policy:<10} " + "  ".join(
                f"{lane} OVRL {np.mean([r['ovrl_mos'] for r in dns[state][policy][lane]]):.4f}"
                for lane in LANES))

    main_rows = dns["as_is"]["self_tile"]

    # ---- 出力の先頭 1 件を目で見る（C-018 の形） ---------------------------
    first = main_rows["T_v2"][0]
    print("\n[目視] 主系列の先頭 1 件:")
    print("  " + json.dumps(first, ensure_ascii=False))

    # ---- G1 決定性（別プロセス 2 回） -------------------------------------
    spec = {"paths": [r["path"] for lane in LANES for r in main_rows[lane]],
            "policy": "self_tile"}
    spec_p = scratch / "determinism_spec.json"
    spec_p.write_text(json.dumps(spec))
    runs = []
    for _ in range(2):
        cp = subprocess.run(
            [sys.executable, __file__, "--determinism-child", str(spec_p)],
            capture_output=True, text=True, check=True)
        runs.append(json.loads(cp.stdout.strip().splitlines()[-1]))
    inproc = {r["path"]: {k: r[k] for k in SCORES}
              for lane in LANES for r in main_rows[lane]}
    n_eq = sum(1 for p in spec["paths"]
               for k in SCORES
               if runs[0][p][k] == runs[1][p][k] == inproc[p][k])
    g1_pass = n_eq == len(spec["paths"]) * len(SCORES)
    gates["G1_pass"] = g1_pass
    gates["G1_detail"] = {
        "n_files": len(spec["paths"]), "n_scores": len(SCORES),
        "n_exact_equal": n_eq, "comparison": "float64 の == （近似ではない）",
        "runs": "別プロセス 2 回 + 親プロセスの主系列の 3 者比較"}
    print(f"\nG1 (決定性・別プロセス 2 回) {'PASS' if g1_pass else 'FAIL'}  "
          f"{n_eq}/{len(spec['paths']) * len(SCORES)} が完全一致")

    # ---- G5 既存指標の再現 -------------------------------------------------
    print("\nG5: 同じ wav で SCOREQ synthetic/nr と UTMOS を測り直す…")
    legacy: dict[str, dict[str, list[float]]] = {}
    g5_rows = []
    for lane in LANES:
        sc = measure_scoreq(lanes[lane], domain="synthetic", mode="nr")
        ut = measure_utmos(lanes[lane])
        legacy[lane] = {"scoreq_synthetic_nr": sc, "utmos": ut}
        for m, v in legacy[lane].items():
            d = abs(float(np.mean(v)) - EXPECTED[lane][m])
            g5_rows.append({"lane": lane, "metric": m,
                            "now": round(float(np.mean(v)), 6),
                            "stored": EXPECTED[lane][m],
                            "abs_delta": float(f"{d:.3g}"), "pass": d < 1e-3})
            print(f"  {lane:<5} {m:<20} now {np.mean(v):.4f}  "
                  f"stored {EXPECTED[lane][m]:.4f}  |Δ| {d:.2e} "
                  f"{'OK' if d < 1e-3 else 'NG'}")
    gates["G5_pass"] = all(r["pass"] for r in g5_rows)
    gates["G5_rows"] = g5_rows
    gates["G5_note"] = ("既存記録との一致を見る**再現ゲート**であって、"
                        "品質の合格基準ではない")

    # ---- G6 陽性対照の梯子 -------------------------------------------------
    print("\nG6: 陽性対照（T_v2 24 本に 4 系統 ×3 段）")
    ladders = {
        "white_noise_snr_db": [("20", lambda x: add_noise(x, 20, 0)),
                               ("10", lambda x: add_noise(x, 10, 0)),
                               ("0",  lambda x: add_noise(x, 0, 0))],
        "requantize_bits":    [("8", lambda x: requantize(x, 8)),
                               ("5", lambda x: requantize(x, 5)),
                               ("3", lambda x: requantize(x, 3))],
        "hard_clip_frac":     [("0.7", lambda x: hard_clip(x, 0.7)),
                               ("0.5", lambda x: hard_clip(x, 0.5)),
                               ("0.3", lambda x: hard_clip(x, 0.3))],
        "lowpass_hz":         [("6000", lambda x: lowpass(x, 6000)),
                               ("4000", lambda x: lowpass(x, 4000)),
                               ("2000", lambda x: lowpass(x, 2000))],
    }
    base = [read_wav(p) for p in lanes["T_v2"]]
    base_scores = main_rows["T_v2"]
    g6: dict[str, dict] = {}
    for fam, steps in ladders.items():
        g6[fam] = {"steps": [], "monotone_sig": None, "monotone_ovrl": None}
        prev_sig = prev_ovr = None
        prev_bak = prev_p8 = None
        ok_sig = ok_ovr = ok_bak = ok_p8 = True
        for label, fn in steps:
            vals, snrs, gains, rmsr = [], [], [], []
            for x in base:
                y, g = fn(x)
                vals.append(DM.score_array(y, SR_SRC))
                snrs.append(snr_db(x, y))
                gains.append(g)
                rmsr.append(float(np.sqrt(np.mean(y.astype(np.float64) ** 2))
                                  / np.sqrt(np.mean(x.astype(np.float64) ** 2))))
            sig = float(np.mean([v["sig_mos"] for v in vals]))
            ovr = float(np.mean([v["ovrl_mos"] for v in vals]))
            bak = float(np.mean([v["bak_mos"] for v in vals]))
            p808 = float(np.mean([v["p808_mos"] for v in vals]))
            if prev_sig is not None:
                ok_sig &= sig < prev_sig
                ok_ovr &= ovr < prev_ovr
                ok_bak &= bak < prev_bak
                ok_p8 &= p808 < prev_p8
            prev_sig, prev_ovr, prev_bak, prev_p8 = sig, ovr, bak, p808
            g6[fam]["steps"].append({
                "label": label, "sig": round(sig, 4), "bak": round(bak, 4),
                "ovrl": round(ovr, 4), "p808": round(p808, 4),
                "snr_db_mean": round(float(np.mean(snrs)), 3),
                "gain_min": round(float(np.min(gains)), 4),
                "rms_ratio_mean": round(float(np.mean(rmsr)), 4)})
        b_sig = float(np.mean([r["sig_mos"] for r in base_scores]))
        b_ovr = float(np.mean([r["ovrl_mos"] for r in base_scores]))
        b_bak = float(np.mean([r["bak_mos"] for r in base_scores]))
        b_p8 = float(np.mean([r["p808_mos"] for r in base_scores]))
        ok_sig &= g6[fam]["steps"][0]["sig"] < b_sig
        ok_ovr &= g6[fam]["steps"][0]["ovrl"] < b_ovr
        ok_bak &= g6[fam]["steps"][0]["bak"] < b_bak
        ok_p8 &= g6[fam]["steps"][0]["p808"] < b_p8
        g6[fam]["monotone_sig"] = bool(ok_sig)
        g6[fam]["monotone_ovrl"] = bool(ok_ovr)
        g6[fam]["monotone_bak"] = bool(ok_bak)
        g6[fam]["monotone_p808"] = bool(ok_p8)
        g6[fam]["source_mean"] = {"sig": round(b_sig, 4), "bak": round(b_bak, 4),
                                  "ovrl": round(b_ovr, 4), "p808": round(b_p8, 4)}
        print(f"  {fam:<20} 原音 SIG {b_sig:.3f} OVRL {b_ovr:.3f} → " + " → ".join(
            f"{s['label']}: {s['sig']:.3f}/{s['ovrl']:.3f}" for s in g6[fam]["steps"])
            + f"   単調 SIG {ok_sig} OVRL {ok_ovr}")
    g6_pass = all(v["monotone_sig"] and v["monotone_ovrl"] for v in g6.values())
    g6_breakdown = {
        "n_families": len(g6),
        "monotone_sig_families": sum(1 for v in g6.values() if v["monotone_sig"]),
        "monotone_ovrl_families": sum(1 for v in g6.values() if v["monotone_ovrl"]),
        "failed_sig": [k for k, v in g6.items() if not v["monotone_sig"]],
        "failed_ovrl": [k for k, v in g6.items() if not v["monotone_ovrl"]],
        "monotone_bak_families": sum(1 for v in g6.values() if v["monotone_bak"]),
        "monotone_p808_families": sum(1 for v in g6.values() if v["monotone_p808"]),
        "failed_bak": [k for k, v in g6.items() if not v["monotone_bak"]],
        "failed_p808": [k for k, v in g6.items() if not v["monotone_p808"]],
        "note": ("P.835 の SIG は『背景雑音を無視して音声信号の歪みだけを見る』軸なので、"
                 "加法雑音で下がらないのは設計どおりでもありうる。BAK の落ち方と"
                 "並べて読むこと。**ゲートは緩めない** — 満たさなかったと記録する"),
    }
    gates["G6_breakdown"] = g6_breakdown
    gates["G6_pass"] = g6_pass
    gates["G6_rule"] = ("原音 → 段 1 → 段 2 → 段 3 が SIG も OVRL も厳密に単調減少。"
                        "絶対値のしきい値は使わない（日本語で較正されていないため）")
    print(f"  G6 {'PASS' if g6_pass else 'FAIL'}（4 系統すべてで SIG・OVRL とも単調）")

    # ---- G7 金属様の特異性対照 --------------------------------------------
    print("\nG7: 金属様アーム（代理）と SNR 一致の白色雑音アームの対比")
    metallic = {
        "griffinlim_iter": [("8", lambda x: griffin_lim(x, 8)),
                            ("32", lambda x: griffin_lim(x, 32)),
                            ("128", lambda x: griffin_lim(x, 128))],
        "am_86hz_depth":   [("0.05", lambda x: amplitude_modulate(x, 0.05)),
                            ("0.15", lambda x: amplitude_modulate(x, 0.15)),
                            ("0.30", lambda x: amplitude_modulate(x, 0.30))],
        "frame_freeze":    [("every2", lambda x: frame_freeze(x, 256, 2))],
    }
    base_probe = {
        "sfm_2_8k": [probe_sfm(x, (2000.0, 8000.0)) for x in base],
        "sfm_8_11k": [probe_sfm(x, (8000.0, 11000.0)) for x in base],
        "mod_line_86hz": [probe_mod_line(x) for x in base],
    }
    g7: dict[str, dict] = {}
    for fam, steps in metallic.items():
        g7[fam] = {}
        for label, fn in steps:
            ys, gains = [], []
            for x in base:
                y, g = fn(x)
                ys.append(y)
                gains.append(g)
            identical = sum(1 for x, y in zip(base, ys)
                            if len(x) == len(y) and np.array_equal(x, y))
            snrs = [snr_db(x, y) for x, y in zip(base, ys)]
            sc = [DM.score_array(y, SR_SRC) for y in ys]
            # SNR を一致させた白色雑音アーム（対）
            noise = [add_noise(x, s, 1)[0] for x, s in zip(base, snrs)]
            sc_n = [DM.score_array(y, SR_SRC) for y in noise]
            probes = {
                "sfm_2_8k": [probe_sfm(y, (2000.0, 8000.0)) for y in ys],
                "sfm_8_11k": [probe_sfm(y, (8000.0, 11000.0)) for y in ys],
                "mod_line_86hz": [probe_mod_line(y) for y in ys],
            }
            probe_res = {}
            separated = False
            for pk, pv in probes.items():
                d = np.array(pv) - np.array(base_probe[pk])
                ci = paired_diff_ci(d)
                sep = bool(ci[0] > 0 or ci[1] < 0)
                separated |= sep
                probe_res[pk] = {"base_mean": round(float(np.mean(base_probe[pk])), 5),
                                 "arm_mean": round(float(np.mean(pv)), 5),
                                 "mean_diff": round(float(d.mean()), 5),
                                 "ci95_diff": ci, "separates": sep}
            delta = {k: round(float(np.mean([a[k] for a in sc])
                                   - np.mean([b[k] for b in base_scores])), 4)
                     for k in SCORES}
            delta_n = {k: round(float(np.mean([a[k] for a in sc_n])
                                     - np.mean([b[k] for b in base_scores])), 4)
                       for k in SCORES}
            g7[fam][label] = {
                "n_identical_to_source": identical,
                "snr_db_mean": round(float(np.mean(snrs)), 3),
                "gain_min": round(float(np.min(gains)), 4),
                "dnsmos_mean": {k: round(float(np.mean([a[k] for a in sc])), 4)
                                for k in SCORES},
                "delta_vs_source": delta,
                "snr_matched_noise_dnsmos_mean":
                    {k: round(float(np.mean([a[k] for a in sc_n])), 4) for k in SCORES},
                "snr_matched_noise_delta": delta_n,
                "specificity_metallic_minus_noise":
                    {k: round(delta[k] - delta_n[k], 4) for k in SCORES},
                "probes": probe_res,
                "arm_is_real": bool(identical == 0),
                "probe_separates": separated,
            }
            print(f"  {fam}/{label:<7} SNR {np.mean(snrs):6.2f} dB  "
                  f"ΔOVRL {delta['ovrl_mos']:+.3f} (雑音対 {delta_n['ovrl_mos']:+.3f}, "
                  f"特異性 {delta['ovrl_mos'] - delta_n['ovrl_mos']:+.3f})  "
                  f"別物 {identical == 0}  プローブ分離 {separated}")
    g7_pass = all(v["arm_is_real"] and v["probe_separates"]
                  for fam in g7.values() for v in fam.values())
    gates["G7_pass"] = g7_pass
    gates["G7_rule"] = ("(a) 原音と bit 一致しない (b) SNR を記録 "
                        "(c) **DNSMOS でない**プローブ（2–8k SFM / 8–11k SFM / "
                        "86.13 Hz 変調線）の少なくとも 1 つで差の 95%CI が 0 を跨がない。"
                        "向きは仮定せず観測して記録する")
    print(f"  G7 {'PASS' if g7_pass else 'FAIL'}")

    # ---- 主結果: T vs S（対応あり）/ 各レーン vs H（対応なし） -------------
    stats_out: dict[str, dict] = {"paired_T_v2_minus_S_v2": {}, "vs_human": {},
                                  "T_b5_vs_T_v2_same_system": {}}
    for k in SCORES:
        t = [r[k] for r in main_rows["T_v2"]]
        s = [r[k] for r in main_rows["S_v2"]]
        stats_out["paired_T_v2_minus_S_v2"][k] = paired_stats(s, t)  # a=生徒, b=教師
        h = [r[k] for r in main_rows["H"]]
        stats_out["vs_human"][k] = {}
        for lane in ("T_v2", "S_v2", "T_b5"):
            v = [r[k] for r in main_rows[lane]]
            stats_out["vs_human"][k][lane] = {
                "ratio_over_human": ratio_ci(v, h),
                "mannwhitney": mannwhitney(v, h),
                "cohens_d_lane_minus_human": cohens_d(v, h),
                "mde": unpaired_mde(v, h),
            }
        b5 = [r[k] for r in main_rows["T_b5"]]
        stats_out["T_b5_vs_T_v2_same_system"][k] = {
            "T_b5_mean": round(float(np.mean(b5)), 4),
            "T_v2_mean": round(float(np.mean(t)), 4),
            "diff": round(float(np.mean(b5) - np.mean(t)), 4),
            "mannwhitney": mannwhitney(b5, t),
            "note": "同一システム（教師）の独立 2 標本。n=24 の標本間ばらつきの物差し",
        }

    # ---- G8 交絡（padding / policy が生徒差より小さいか） ------------------
    g8 = {}
    for k in SCORES:
        d_student = abs(np.mean([r[k] for r in main_rows["S_v2"]])
                        - np.mean([r[k] for r in main_rows["T_v2"]]))
        d_pol = max(abs(np.mean([r[k] for r in dns["as_is"]["zero_pad"][lane]])
                        - np.mean([r[k] for r in dns["as_is"]["self_tile"][lane]]))
                    for lane in LANES)
        d_pad = max(abs(np.mean([r[k] for r in dns[st]["self_tile"][lane]])
                        - np.mean([r[k] for r in dns["as_is"]["self_tile"][lane]]))
                    for st in ("all_padded", "all_unpadded") for lane in LANES)
        g8[k] = {"abs_delta_student_minus_teacher": round(float(d_student), 4),
                 "max_abs_delta_policy": round(float(d_pol), 4),
                 "max_abs_delta_padding": round(float(d_pad), 4),
                 "pass": bool(d_pol < d_student and d_pad < d_student)}
    gates["G8_pass"] = all(v["pass"] for v in g8.values())
    gates["G8_detail"] = g8
    gates["G8_rule"] = ("|Δ(policy)| と |Δ(padding)| がいずれも |Δ(生徒−教師)| より"
                        "小さいこと。大きければ生徒差は解釈不能と報告する")
    print("\nG8 (tiling / padding の交絡)")
    for k, v in g8.items():
        print(f"  {k:<10} |Δ生徒−教師| {v['abs_delta_student_minus_teacher']:.4f}  "
              f"|Δpolicy| {v['max_abs_delta_policy']:.4f}  "
              f"|Δpadding| {v['max_abs_delta_padding']:.4f}  "
              f"{'OK' if v['pass'] else 'NG'}")

    # 窓数の交絡（レーン内 corr(n_windows, score)）
    win_corr = {}
    for lane in LANES:
        w = [r["n_windows"] for r in main_rows[lane]]
        win_corr[lane] = {"n_windows": w}
        if len(set(w)) > 1:
            for k in SCORES:
                win_corr[lane][k] = corr_ci(w, [r[k] for r in main_rows[lane]])

    # ---- 指標間の発話単位の相関（同一 24 文） -----------------------------
    cross = {}
    for lane in LANES:
        cross[lane] = {}
        for k in SCORES:
            v = [r[k] for r in main_rows[lane]]
            cross[lane][f"scoreq_vs_{k}"] = corr_ci(
                legacy[lane]["scoreq_synthetic_nr"], v)
            cross[lane][f"utmos_vs_{k}"] = corr_ci(legacy[lane]["utmos"], v)
    # 教師→生徒の変化量どうしの相関（対応あり。食い違いはここに出る）
    delta_corr = {}
    d_scoreq = np.array(legacy["S_v2"]["scoreq_synthetic_nr"]) - \
        np.array(legacy["T_v2"]["scoreq_synthetic_nr"])
    d_utmos = np.array(legacy["S_v2"]["utmos"]) - np.array(legacy["T_v2"]["utmos"])
    for k in SCORES:
        d = np.array([r[k] for r in main_rows["S_v2"]]) - \
            np.array([r[k] for r in main_rows["T_v2"]])
        delta_corr[f"dSCOREQ_vs_d{k}"] = corr_ci(d_scoreq, d)
        delta_corr[f"dUTMOS_vs_d{k}"] = corr_ci(d_utmos, d)
    # 食い違った発話（順位が最も食い違う 3 本）
    d_ovrl = np.array([r["ovrl_mos"] for r in main_rows["S_v2"]]) - \
        np.array([r["ovrl_mos"] for r in main_rows["T_v2"]])
    r_sc = np.argsort(np.argsort(d_scoreq))     # 0 = 最も生徒が落ちた発話
    r_dn = np.argsort(np.argsort(d_ovrl))
    disagree_idx = np.argsort(-np.abs(r_sc - r_dn))[:3]
    ev = json.loads(pathlib.Path("reports/eval_v2/eval.json").read_text())
    uids = [u["uid"] for u in ev["utterances"]]
    disagreement = [{
        "uid": uids[i],
        "d_scoreq_student_minus_teacher": round(float(d_scoreq[i]), 4),
        "d_ovrl_student_minus_teacher": round(float(
            main_rows["S_v2"][i]["ovrl_mos"] - main_rows["T_v2"][i]["ovrl_mos"]), 4),
        "rank_scoreq": int(r_sc[i]), "rank_dnsmos_ovrl": int(r_dn[i]),
    } for i in disagree_idx]

    # ---- M-49（別レーンの先行測定）との照合 --------------------------------
    # ⚠️ E-1 は **M-49 で一度測られている**（`scripts/e2_dnsmos.py`）。
    # あちらは `torchaudio.transforms.Resample` を使っており、こちらは soxr_hq。
    # **同じ人間 24 本で両方のリサンプラを回して、差がリサンプラで説明できるか**を見る。
    M49_HUMAN = {"ovrl_mos": 2.7866, "sig_mos": 3.1111,
                 "bak_mos": 3.7973, "p808_mos": 3.6667}

    def _load_16k_torchaudio(path):
        import torch
        import torchaudio

        wav, sr = sf.read(str(path), dtype="float32", always_2d=True)
        t = torch.from_numpy(wav[:, 0]).unsqueeze(0)
        if sr != DM.sr_target():
            t = torchaudio.transforms.Resample(sr, DM.sr_target())(t)
        return t[0].numpy().astype(np.float32)

    tor = [DM.score_array(_load_16k_torchaudio(p), DM.sr_target())
           for p in lanes["H"]]
    recon = {"m49_recorded_human": M49_HUMAN, "soxr_hq": {}, "torchaudio": {},
             "abs_delta_torchaudio_vs_m49": {}, "abs_delta_soxr_vs_m49": {}}
    for k in SCORES:
        s_ = float(np.mean([r[k] for r in main_rows["H"]]))
        t_ = float(np.mean([r[k] for r in tor]))
        recon["soxr_hq"][k] = round(s_, 4)
        recon["torchaudio"][k] = round(t_, 4)
        recon["abs_delta_torchaudio_vs_m49"][k] = float(f"{abs(t_ - M49_HUMAN[k]):.4g}")
        recon["abs_delta_soxr_vs_m49"][k] = float(f"{abs(s_ - M49_HUMAN[k]):.4g}")
    recon["torchaudio_reproduces_m49"] = all(
        v < 5e-5 for v in recon["abs_delta_torchaudio_vs_m49"].values())
    recon["note"] = (
        "M-49（scripts/e2_dnsmos.py）は torchaudio.transforms.Resample を使っている。"
        "同じ 24 本を torchaudio 経路で測ると M-49 の記録と一致する。"
        "**M-49 の人間天井は再現できた。**残る差はリサンプラだけで、"
        "OVRL では小さく p808 では 1 桁大きい。"
        "⚠️ M-49 の L0_teacher は `reports/eval_v2/teacher` とは**別の wav**"
        "（レーンを作り直しており、bit 一致は原理的に不可能・SNR 68.5 dB）なので、"
        "T_v2 と L0_teacher の差はリサンプラだけでは説明できない")
    print("\nM-49 との照合（人間 24 本）")
    for k in SCORES:
        print(f"  {k:<10} soxr_hq {recon['soxr_hq'][k]:.4f}  "
              f"torchaudio {recon['torchaudio'][k]:.4f}  "
              f"M-49 {M49_HUMAN[k]:.4f}  "
              f"|Δtor−M49| {recon['abs_delta_torchaudio_vs_m49'][k]:.4g}")
    print(f"  M-49 を再現: {recon['torchaudio_reproduces_m49']}")

    # ---- F0 交絡 ----------------------------------------------------------
    print("\nF0 交絡（2 推定器）…")
    def _ensure_pkg_resources() -> bool:
        """setuptools 84 で `pkg_resources` が消え `import pyworld` が落ちるので補う。

        返り値は「shim を入れたか」。**環境は変更しない**（このプロセス内だけ）。
        """
        import importlib.util
        import types

        if importlib.util.find_spec("pkg_resources") is not None:
            return False
        from importlib.metadata import version as _v

        m = types.ModuleType("pkg_resources")

        class _Dist:
            def __init__(self, v):
                self.version = v

        m.get_distribution = lambda n: _Dist(_v(n))
        sys.modules["pkg_resources"] = m
        return True

    pkg_resources_shimmed = _ensure_pkg_resources()

    def mean_logf0_world(x):
        import pyworld

        f0, _ = pyworld.harvest(x.astype(np.float64), SR_SRC, f0_floor=60.0,
                                f0_ceil=600.0, frame_period=5.0)
        v = f0[f0 > 0]
        return float(np.mean(np.log(v))) if v.size else float("nan")

    def mean_logf0_pyin(x):
        import librosa

        f0, voiced, _ = librosa.pyin(x.astype(np.float32), fmin=60.0, fmax=600.0,
                                     sr=SR_SRC, frame_length=1024, hop_length=256)
        v = f0[np.isfinite(f0)]
        return float(np.mean(np.log(v))) if v.size else float("nan")

    f0 = {}
    for est, fn in (("pyworld_harvest", mean_logf0_world), ("librosa_pyin", mean_logf0_pyin)):
        f0[est] = {lane: [fn(read_wav(p)) for p in lanes[lane]]
                   for lane in ("T_v2", "S_v2")}
    f0_corr = {}
    for est in f0:
        dlog = np.array(f0[est]["S_v2"]) - np.array(f0[est]["T_v2"])
        f0_corr[est] = {
            "mean_logf0_teacher": round(float(np.mean(f0[est]["T_v2"])), 4),
            "mean_logf0_student": round(float(np.mean(f0[est]["S_v2"])), 4),
            "mean_dlogf0": round(float(dlog.mean()), 4),
        }
        for k in SCORES:
            d = np.array([r[k] for r in main_rows["S_v2"]]) - \
                np.array([r[k] for r in main_rows["T_v2"]])
            f0_corr[est][f"corr_dlogf0_vs_d{k}"] = corr_ci(dlog, d)
        # レーン内（絶対値どうし）も見る
        for lane in ("T_v2", "S_v2"):
            f0_corr[est][f"corr_logf0_vs_ovrl_{lane}"] = corr_ci(
                f0[est][lane], [r["ovrl_mos"] for r in main_rows[lane]])
    sign_agree = all(
        np.sign(f0_corr["pyworld_harvest"][f"corr_dlogf0_vs_d{k}"]["pearson_r"])
        == np.sign(f0_corr["librosa_pyin"][f"corr_dlogf0_vs_d{k}"]["pearson_r"])
        for k in SCORES)
    f0_corr["two_estimators_sign_agree"] = bool(sign_agree)
    f0_corr["pkg_resources_shimmed"] = bool(pkg_resources_shimmed)
    f0_corr["pkg_resources_note"] = (
        "setuptools 84.0.0 は pkg_resources を同梱しないため、この環境では "
        "`import pyworld` が ModuleNotFoundError('pkg_resources') で落ちる。"
        "本スクリプトはプロセス内だけの最小 shim を入れて回避している"
        "（環境・uv.lock は変更していない）。⚠️ eval extra を使う他のスクリプトでも"
        "同じ理由で pyworld が落ちる")
    for est in ("pyworld_harvest", "librosa_pyin"):
        print(f"  {est:<16} r(ΔlogF0, ΔOVRL) = "
              f"{f0_corr[est]['corr_dlogf0_vs_dovrl_mos']['pearson_r']:+.3f}")
    print(f"  2 推定器の符号一致: {sign_agree}")

    # ---- 表示 --------------------------------------------------------------
    print("\n" + "=" * 86)
    print(f"{'lane':<6}" + "".join(f"{k:>12}" for k in SCORES)
          + f"{'SCOREQ':>10}{'UTMOS':>9}")
    for lane in LANES:
        print(f"{lane:<6}" + "".join(
            f"{np.mean([r[k] for r in main_rows[lane]]):>12.4f}" for k in SCORES)
            + f"{np.mean(legacy[lane]['scoreq_synthetic_nr']):>10.4f}"
              f"{np.mean(legacy[lane]['utmos']):>9.4f}")
    print("=" * 86)
    print("比（↑ 高いほど良い）")
    for k in SCORES:
        t = np.mean([r[k] for r in main_rows["T_v2"]])
        s = np.mean([r[k] for r in main_rows["S_v2"]])
        h = np.mean([r[k] for r in main_rows["H"]])
        ps = stats_out["paired_T_v2_minus_S_v2"][k]
        print(f"  {k:<10} 生徒/教師 {s / t:.4f}  生徒/人間 {s / h:.4f}  "
              f"教師/人間 {t / h:.4f}   対応差 {ps['mean_diff']:+.4f} "
              f"CI[{ps['ci95_diff'][0]:+.4f},{ps['ci95_diff'][1]:+.4f}] "
              f"p={ps['paired_t_p']} 検出限界 {ps['smallest_significant_diff']:.4f}")

    print("\n物差し（同一システムの標本間ばらつき）: "
          f"OVRL T_b5 − T_v2 = "
          f"{np.mean([r['ovrl_mos'] for r in main_rows['T_b5']]) - np.mean([r['ovrl_mos'] for r in main_rows['T_v2']]):+.4f}"
          f"   生徒 − 教師（対応あり） = "
          f"{stats_out['paired_T_v2_minus_S_v2']['ovrl_mos']['mean_diff']:+.4f}")
    print("人間天井（この日本語コーパス）: " + "  ".join(
        f"{k} {np.mean([r[k] for r in main_rows['H']]):.4f}" for k in SCORES))

    all_gates = {g: gates[f"{g}_pass"] for g in
                 ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")}
    print("\nゲート: " + "  ".join(f"{g} {'PASS' if v else 'FAIL'}"
                                    for g, v in all_gates.items()))

    out = {
        "task": "E-1: DNSMOS P.835（4 レーン）。**併記プローブであって合否ゲートではない**",
        "date": time.strftime("%Y-%m-%d"),
        "preregistered_prediction": PREREGISTERED_PREDICTION,
        "preregistration_note": ("この予測は測定を 1 回も走らせる前に "
                                 "scripts/e1_dnsmos.py の docstring として commit した。"
                                 "git log scripts/e1_dnsmos.py で確認できる"),
        "environment": env,
        "lanes": {k: [r["path"] for r in man[k]] for k in man},
        "manifest": man,
        "gates": gates,
        "gates_summary": all_gates,
        "dnsmos_per_file": {st: {pol: {lane: dns[st][pol][lane] for lane in LANES}
                                 for pol in DM.POLICIES} for st in dns},
        "dnsmos_summary_main": {
            lane: {k: summarize([r[k] for r in main_rows[lane]], k) for k in SCORES}
            for lane in LANES},
        "legacy_metrics_per_file": legacy,
        "statistics": stats_out,
        "window_confound": win_corr,
        "cross_metric_correlation": cross,
        "delta_correlation": delta_corr,
        "largest_rank_disagreement_scoreq_vs_dnsmos": disagreement,
        "positive_control_G6": g6,
        "metallic_specificity_G7": g7,
        "f0_confound": {"per_file": f0, "analysis": f0_corr},
        "reconciliation_with_M49": recon,
        "calibration_japanese": {
            "human_ceiling_this_corpus": {
                k: round(float(np.mean([r[k] for r in main_rows["H"]])), 4)
                for k in SCORES},
            "human_set": "つくよみちゃんコーパス VOICEACTRESS100_001..024（n=24）",
            "teacher_over_human_ovrl": round(
                float(np.mean([r["ovrl_mos"] for r in main_rows["T_v2"]])
                      / np.mean([r["ovrl_mos"] for r in main_rows["H"]])), 4),
            "T_b5_over_human_ovrl": round(
                float(np.mean([r["ovrl_mos"] for r in main_rows["T_b5"]])
                      / np.mean([r["ovrl_mos"] for r in main_rows["H"]])), 4),
            "note": ("**実人間の日本語スタジオ録音でも OVRL は 3 に届かない。**"
                     "しかも教師（TTS）のほうが実人間より高く出る組み合わせがある。"
                     "絶対値を英語の公表値と並べてはいけない（C-012 の再発防止）"),
        },
        "same_system_ruler": {
            "note": ("T_b5 と T_v2 はどちらも同じ教師の合成音（別の 24 文）。"
                     "この差が n=24 での**標本間ばらつきの物差し**で、"
                     "生徒差はこれと比べて読む"),
            "ovrl_diff_T_b5_minus_T_v2": round(
                float(np.mean([r["ovrl_mos"] for r in main_rows["T_b5"]])
                      - np.mean([r["ovrl_mos"] for r in main_rows["T_v2"]])), 4),
            "ovrl_paired_diff_student_minus_teacher":
                stats_out["paired_T_v2_minus_S_v2"]["ovrl_mos"]["mean_diff"],
        },
        "scale_note": ("DNSMOS は 5 点満点ではない。較正多項式の像は "
                       "SIG [1.1421, 4.0101] / BAK [1.0814, 4.3580] / "
                       "OVRL [1.0938, 3.9318]（raw ∈ [1,5] を代入して本タスクで確認）。"
                       "下限が 0 でなく約 1.1 なので比は圧縮される。"
                       "主たる読みは対応のある差 Δ にすること"),
        "p808_note": ("p808_mos は別 ONNX (model_v8.onnx, log-mel 入力) の独立予測。"
                      "4 スコアを平均しない"),
        "calibration_warning": DM.CALIBRATION_WARNING,
        "scoreq_utmos_calibration_warning": CALIBRATION_WARNING,
        "interpretation_constraints": [
            "**合否は決めない。** DNSMOS も日本語で較正されていない（D-013）",
            "生徒の DNSMOS が低いことは金属的アーティファクトの証拠にならない"
            "（事前登録どおり）。特異性は G7 の代理アームでしか測っていない",
            "G7 が測ったのは**代理に対する感度**であって、うちの生徒に実在する欠陥に"
            "対する感度ではない。Griffin-Lim / 86.13 Hz AM / フレーム凍結は文献の"
            "機序から作った代理にすぎない",
            "n=24。人間レーンは対応なし（テキストが違う）。"
            "『有意差なし』を『差が無い』と書かない",
            "G6 は満たさなかった。**指標が反応しない劣化が実在する**（下記 G6 参照）",
            "p808_mos は別 ONNX の独立予測。他の 3 スコアと平均しない",
        ],
        "not_measured": [
            "聴取（P-2 の担当）",
            "8–11 kHz の欠陥（DNSMOS の入力は 16 kHz、Nyquist 8 kHz。原理的に見えない）",
            "personalized DNSMOS / DNSMOS Pro / NISQA / distillmos",
            "int8 / C99 コアの出力音声（E-1 は fp32 経路の wav だけ）",
            "上流の申告（metallic ⇒ DNSMOS 低）の再現。うちの生徒に金属的欠陥が"
            "実在するかは未確認で、G7 は**代理**に対する感度しか測っていない",
        ],
        "repro": REPRO,
        "wall_sec": round(time.time() - t0, 1),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n→ {OUT}   ({out['wall_sec']} 秒)")
    print(f"\n⚠️ {DM.CALIBRATION_WARNING}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
