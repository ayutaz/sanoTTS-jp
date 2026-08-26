#!/usr/bin/env python3
"""B-5 の第2段: 生成済み wav の MOS 推定値を測る。

`scripts/b5_teacher_baseline.py` が出した `reports/b5_teacher_baseline.json` を読み、
UTMOS（と、入っていれば SCOREQ）を計算して同じ JSON に書き戻す。

**日本語では較正されていない指標であることに注意**（docs/decisions.md D-008）:

* UTMOS の学習データは VoiceMOS Challenge 2022 の
  main track = BVCC（英語）/ OOD track = BC2019（中国語）で、**日本語を含まない**
* SCOREQ も同様

したがって**絶対値を論文の英語モデルと直接比較してはいけない**。
本タスクの目的は「教師が論文の教師と同水準か」を大掴みに知ることと、
生徒を測るときの**教師比の分母**を得ること。

実行:
    uv run --extra eval python scripts/b5_measure_mos.py
"""

from __future__ import annotations

import json
import pathlib
import warnings

import numpy as np
import soundfile as sf
import torch

warnings.filterwarnings("ignore")

REPORT = pathlib.Path("reports/b5_teacher_baseline.json")
UTMOS_SR = 16000

# 論文の教師（英語 Kristin / Amy）。**日本語との直接比較は不可**だが桁の目安にする
PAPER_TEACHER = {"kristin": {"scoreq": 4.68, "utmos": 4.42},
                 "amy": {"scoreq": 4.71, "utmos": 4.47}}


def load_16k(path: str) -> torch.Tensor:
    """soundfile で読んで 16 kHz にリサンプルする。

    torchaudio.load は 2.11 から torchcodec を要求するようになったので使わない。
    """
    import torchaudio

    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    tensor = torch.from_numpy(wav[:, 0]).unsqueeze(0)
    if sr != UTMOS_SR:
        tensor = torchaudio.transforms.Resample(sr, UTMOS_SR)(tensor)
    return tensor


def measure_utmos(paths: list[str]) -> list[float]:
    predictor = torch.hub.load(
        "tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True
    )
    predictor.eval()
    out = []
    for path in paths:
        with torch.no_grad():
            out.append(predictor(load_16k(path), sr=UTMOS_SR).item())
    return out


def measure_scoreq(paths: list[str]) -> list[float] | None:
    """SCOREQ（論文の主指標）。未インストールなら None を返す。

    ⚠️ **`scoreq.Scoreq` を直接呼んではいけない。** 内部が `torchaudio.load` を使い、
    torchaudio 2.13 はそこで torchcodec を要求するので `ImportError` で落ちる。
    `saanotts_jp.scoreq_metric` の soundfile shim を必ず通すこと。

    ⚠️ **`data_domain="natural"` は使わない。** 実体は NISQA TRAIN SIM（伝送劣化）
    モデルで、実測では合成音声を実人間より高く採点し（教師 3.508 > 実人間 3.375）、
    UTMOS と無相関 (r=+0.141)。TTS の劣化軸を見ていない（B-5 続き）。
    """
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, "src")
    try:
        from saanotts_jp.scoreq_metric import score_files  # noqa: PLC0415
    except ImportError:
        return None
    scored = score_files(paths, domain="synthetic", mode="nr")
    return [scored[str(p)] for p in paths]


def summarize(name: str, values: list[float], secs: list[float]) -> dict:
    arr = np.asarray(values)
    corr = float(np.corrcoef(secs, arr)[0, 1]) if len(arr) > 2 else float("nan")
    print(f"\n{name}")
    print(f"  n={len(arr)}  mean {arr.mean():.3f}  median {np.median(arr):.3f}  "
          f"sd {arr.std():.3f}  min {arr.min():.3f}  max {arr.max():.3f}")
    print(f"  発話長との相関 r = {corr:+.3f}   （短尺が不利になっていないかの確認）")
    return {"n": len(arr), "mean": round(float(arr.mean()), 4),
            "median": round(float(np.median(arr)), 4),
            "sd": round(float(arr.std()), 4),
            "min": round(float(arr.min()), 4), "max": round(float(arr.max()), 4),
            "corr_with_duration": round(corr, 4)}


def main() -> int:
    manifest = json.loads(REPORT.read_text())
    utts = manifest["utterances"]
    paths = [u["wav"] for u in utts]
    secs = [u["sec"] for u in utts]

    print(f"評価対象 {len(paths)} 文 / 平均 {np.mean(secs):.2f} 秒 "
          f"(最短 {min(secs):.2f} / 最長 {max(secs):.2f})")

    summary = {}

    print("\nUTMOS をロード中 (tarepan/SpeechMOS:v1.2.0)…")
    utmos = measure_utmos(paths)
    for u, v in zip(utts, utmos, strict=True):
        u["utmos"] = round(v, 4)
    summary["utmos"] = summarize("UTMOS", utmos, secs)

    scoreq = measure_scoreq(paths)
    if scoreq is None:
        print("\nSCOREQ: 未インストール（`uv add --optional eval scoreq`）。スキップ")
        summary["scoreq"] = None
    else:
        for u, v in zip(utts, scoreq, strict=True):
            u["scoreq"] = round(v, 4)
        summary["scoreq"] = summarize("SCOREQ", scoreq, secs)

    print("\n参考: 論文の教師（**英語。日本語との直接比較は不可**）")
    for name, ref in PAPER_TEACHER.items():
        print(f"  {name:<8} SCOREQ {ref['scoreq']}  UTMOS {ref['utmos']}")

    manifest["summary"] = summary
    manifest["note"] = (
        "UTMOS / SCOREQ は日本語では較正されていない (D-008)。"
        "絶対値を英語モデルと比較しないこと。生徒の評価では教師比で報告する。"
    )
    REPORT.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"\n→ {REPORT}")

    print("\n最低スコアの 3 文（聴取して原因を確認すること）:")
    for u in sorted(utts, key=lambda x: x["utmos"])[:3]:
        print(f"  UTMOS {u['utmos']:.2f}  {u['sec']:.1f}s  {u['text'][:38]}")
        print(f"    {u['wav']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
