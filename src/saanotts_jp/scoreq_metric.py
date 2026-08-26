"""SCOREQ（論文 arXiv:2608.21378 の主指標）のラッパー。

`scoreq==1.0.1` は PyPI にある（onnxruntime 経路なので fairseq は不要）。
**「pip パッケージが見つからない」は誤りだった**（C-016）。

⚠️ 2 つの落とし穴:

1. **torchaudio 2.13 の `load()` は torchcodec を要求する。** scoreq 内部が
   `torchaudio.load` を呼ぶので、そのままでは `ImportError` で落ちる。
   ここで **soundfile 経由の実装に差し替える**（依存を増やさない）。
2. **`data_domain="synthetic"` のモデルは VoiceMOS 2022 Train Set = BVCC（英語）で
   学習されている。** UTMOS とまったく同じ較正問題を持つので、
   **日本語の絶対値を英語モデルと比較してはいけない**（D-013）。教師比で報告する。

使い方:
    from saanotts_jp.scoreq_metric import score_files
    score_files(["a.wav", "b.wav"], domain="synthetic")   # -> {path: mos}
"""

from __future__ import annotations

import warnings

import numpy as np
import soundfile as sf
import torch


def _install_torchaudio_shim() -> None:
    """`torchaudio.load` を soundfile 実装に差し替える（torchcodec を要求しない）。"""
    import torchaudio

    def _load(uri, frame_offset=0, num_frames=-1, normalize=True,
              channels_first=True, **_):
        data, sr = sf.read(str(uri), dtype="float32", always_2d=True,
                           start=frame_offset,
                           frames=-1 if num_frames in (-1, None) else num_frames)
        t = torch.from_numpy(np.ascontiguousarray(data))
        return (t.T if channels_first else t), sr

    torchaudio.load = _load


_MODELS: dict[tuple[str, str], object] = {}


def get_model(domain: str = "synthetic", mode: str = "nr"):
    """SCOREQ モデルを取得（プロセス内でキャッシュ）。

    domain: "synthetic"（合成音声、VoiceMOS22 学習）/ "natural"（NISQA 学習）
    mode:   "nr"（参照なし）/ "ref"（non-matching reference）
    """
    key = (domain, mode)
    if key not in _MODELS:
        _install_torchaudio_shim()
        import scoreq

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _MODELS[key] = scoreq.Scoreq(data_domain=domain, mode=mode)
    return _MODELS[key]


def score_files(paths, domain: str = "synthetic", mode: str = "nr",
                ref_path: str | None = None) -> dict[str, float]:
    """wav ファイル群を採点する。返り値は `{path: score}`。

    `mode="nr"` は MOS 予測（高いほど良い）。`mode="ref"` はクリーン音声との
    ユークリッド距離（**低いほど良い**）なので、集計の向きを間違えないこと。
    """
    m = get_model(domain, mode)
    out: dict[str, float] = {}
    for p in paths:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out[str(p)] = float(m.predict(test_path=str(p), ref_path=ref_path))
    return out
