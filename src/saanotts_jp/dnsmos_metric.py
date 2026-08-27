"""DNSMOS P.835（`speechmos` 同梱の ONNX）のラッパー。

`src/saanotts_jp/scoreq_metric.py` に倣う。**リサンプルはここでしか行わない。**

⚠️ **DNSMOS は日本語で較正されていない。** 学習データは雑音抑圧器の出力
（英語中心の DNS Challenge クリップ）。SCOREQ / UTMOS とまったく同じ較正問題を
持つので、**絶対値を英語の公表値と並べない**（D-013 / C-012）。

⚠️ **「5 点満点」ではない。** 較正多項式（`get_polyfit_val`）は raw ∈ [1,5] を
SIG 1.1421→4.0101 / BAK 1.0814→4.3580 / OVRL 1.0938→3.9318 に写す。
下限が 0 でなく約 1.1 なので、**比は圧縮される**。主たる読みは対応のある差 Δ にする。

塞いである罠（すべて本タスクで実測して確認した）:

1. **`speechmos.dnsmos.run(x, sr)` は `sr != 16000` で ValueError を投げる。**
   ただし **ファイルパスを渡した場合だけ** 内部で `librosa.load(path, sr=16000)` が
   走ってリサンプルされる。ndarray 経路はリサンプルしない。
   → ここで明示的に `soxr_hq` でリサンプルして ndarray を渡す。
2. **リサンプルを忘れても例外は出ない。** 22.05 kHz の配列を `sr=16000` と偽って
   渡すと**スコアが上がる方向**にずれる。`n_out` を返して呼び出し側が検査できるようにする。
3. **ndarray は [-1, 1] 必須。** ライブラリは範囲外で例外を投げるが、
   ここでは**黙って clip しない**（信号を変えたことを隠さない）。
4. **パス経路の戻り値には `filename` (str) が混じる**ので `{k: float(v)}` の
   集計が落ちる。返り値のキーを固定する。
5. **list 経路（ThreadPoolExecutor + pandas + tqdm）は使わない。** 1 本ずつ回して
   順序と決定性を保つ。
6. **`INPUT_LENGTH` / `SR` はライブラリから import して assert する**
   （9.01 / 16000 をハードコードしない）。

`p808_mos` は**別モデル**（`model_v8.onnx`、log-mel 入力）の独立予測。
前処理感度が 1 桁大きいので **4 スコアの平均を取らない**。

使い方:
    from saanotts_jp.dnsmos_metric import score_path, score_array
    score_path("a.wav")                      # -> dict
    score_array(x, sr_in=22050, policy="zero_pad")
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

# --- 凍結定数 --------------------------------------------------------------
RES_TYPE = "soxr_hq"        # G2 で 3 経路一致を検査している。変えると値が動く
POLICY_DEFAULT = "self_tile"
POLICIES = ("self_tile", "zero_pad")
SCORE_KEYS = ("sig_mos", "bak_mos", "ovrl_mos", "p808_mos")

_D = None


def _lib():
    """`speechmos.dnsmos` を取得し、前提定数を assert する。"""
    global _D
    if _D is None:
        import speechmos.dnsmos as D

        assert D.SR == 16000, f"SR が変わった: {D.SR}"
        assert D.INPUT_LENGTH == 9.01, f"INPUT_LENGTH が変わった: {D.INPUT_LENGTH}"
        _D = D
    return _D


def sr_target() -> int:
    return int(_lib().SR)


def input_length_sec() -> float:
    return float(_lib().INPUT_LENGTH)


def resample_16k(x: np.ndarray, sr_in: int) -> np.ndarray:
    """mono float32 を 16 kHz に落とす。**リサンプルはこの関数だけが行う。**"""
    import librosa

    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 2:
        x = x[:, 0]
    if x.ndim != 1:
        raise ValueError(f"1 次元にならない: shape={x.shape}")
    sr = sr_target()
    if sr_in == sr:
        return np.ascontiguousarray(x, dtype=np.float32)
    y = librosa.resample(x, orig_sr=sr_in, target_sr=sr, res_type=RES_TYPE)
    return np.ascontiguousarray(y, dtype=np.float32)


def _apply_policy(y: np.ndarray, policy: str) -> tuple[np.ndarray, int]:
    """tiling / padding 方針を適用する。返り値は `(音声, 自己連結回数)`。

    * `self_tile` … 何もしない。**ライブラリ側の既定**（`while len < len_samples:
      audio = np.append(audio, audio)`）がそのまま働く。
    * `zero_pad`  … 9.01 秒に足りない分を**末尾ゼロ埋め**して自己連結を封じる。
      9.01 秒以上の音声は切らない（人間レーンに 2 本ある）。
    """
    if policy not in POLICIES:
        raise ValueError(f"未知の policy: {policy}")
    need = int(input_length_sec() * sr_target())
    if policy == "zero_pad":
        if len(y) < need:
            y = np.concatenate([y, np.zeros(need - len(y), dtype=np.float32)])
        return np.ascontiguousarray(y), 0
    n_doub = 0
    n = len(y)
    while n < need:          # ライブラリが実際にやる回数を先に数えるだけ
        n *= 2
        n_doub += 1
    return np.ascontiguousarray(y), n_doub


def _n_windows(n_after_policy: int, n_doublings: int) -> int:
    """ライブラリが実際に採点する窓の数（`num_hops` のうち長さが足りる分）。"""
    D = _lib()
    fs, il = int(D.SR), float(D.INPUT_LENGTH)
    n = n_after_policy * (2 ** n_doublings)
    num_hops = int(np.floor(n / fs) - il) + 1
    need = int(il * fs)
    cnt = 0
    for idx in range(max(num_hops, 0)):
        seg = min(int((idx + il) * fs), n) - int(idx * fs)
        if seg >= need:
            cnt += 1
    return cnt


def score_array(x: np.ndarray, sr_in: int, policy: str = POLICY_DEFAULT) -> dict:
    """1 本ぶんを採点する。`x` は mono/stereo の float 波形、`sr_in` はその sr。"""
    D = _lib()
    n_in = int(np.asarray(x).shape[0])
    y = resample_16k(x, sr_in)
    n_out = int(len(y))
    peak = float(np.abs(y).max()) if n_out else 0.0
    if peak > 1.0:
        raise ValueError(
            f"リサンプル後の振幅が [-1,1] を越えた (peak={peak:.6f})。"
            "黙って clip すると信号が変わるので停止する。"
            "呼び出し側で明示的にゲインを掛けること")
    y2, n_doub = _apply_policy(y, policy)
    res = D.run(y2, sr_target())
    out = {k: float(res[k]) for k in SCORE_KEYS}
    out.update(
        n_windows=_n_windows(len(y2), n_doub),
        n_doublings=int(n_doub),
        sec=round(n_in / sr_in, 6),
        peak=round(peak, 6),
        sr_in=int(sr_in),
        n_in=n_in,
        n_out=n_out,
        resampler=RES_TYPE,
        policy=policy,
    )
    return out


def score_path(path, policy: str = POLICY_DEFAULT) -> dict:
    """wav を読んで採点する。`sf.read` → 明示リサンプル → ndarray 経路。"""
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    out = score_array(x[:, 0], sr, policy=policy)
    out["path"] = str(path)
    return out


def score_path_via_library(path) -> dict:
    """**G2 の照合専用**: ライブラリのパス経路（内部 `librosa.load`）で採点する。

    本番の集計には使わない（戻り値に `filename` が混じる）。
    """
    res = _lib().run(str(path), sr_target())
    return {k: float(res[k]) for k in SCORE_KEYS}


CALIBRATION_WARNING = (
    "DNSMOS は日本語で較正されていない。P.835 の SIG/BAK/OVRL は雑音抑圧器の出力で"
    "学習された予測器で、TTS 用でもない。したがって絶対値を英語の公表値や論文の"
    "SCOREQ/UTMOS と並べてはいけない（D-013 / C-012）。"
    "また 5 点満点ではなく、較正多項式の像は SIG [1.1421, 4.0101] / "
    "BAK [1.0814, 4.3580] / OVRL [1.0938, 3.9318]（raw ∈ [1,5] を代入して確認）。"
    "p808_mos は別 ONNX の独立予測なので 4 スコアを平均しない。"
)
