"""D5: iSTFT のフレーミング規約を実測で確定する。

背景
----
生徒 decoder `Gγ` は 40ch の c[40, T] から 513 mag + 1026 phase を出し
`iSTFT(n_fft=1024, hop=256, win=1024)` で波形にする
(`src/saanotts_jp/_param_reference.py` の `Decoder.istft`、現状 `center=True`)。

教師 (piper-plus MB-iSTFT-VITS2) 側の規約:
  * `models.py:1052-1053`  `w_ceil = ceil(w); y_lengths = sum(w_ceil)`  → T フレーム
  * `mb_istft.py:301-310`  サブバンド iSTFT の出力を **`T_frames * hop_length` に切る**
  * `stft_onnx.py`         iSTFT は `conv_transpose1d` の素の OLA。
                           docstring に明記: "center=False, no trimming"
  ⇒ 教師の規約は「frame t は sample 256t から始まる (center=False)。
      T フレーム → 256T サンプル (末尾の 768 サンプルを捨てる)」

一方 `torch.istft(center=True)` は T フレームから **(T-1)*256 サンプル**しか出さない。
現状の `train_student.py:186` は足りない 256 サンプルを末尾ゼロ埋めしている。

このスクリプトが測ること
------------------------
1. `data/pack_sibdense` の 209 発話で `sum(ceil(dT)) * 256 == len(yT)` を検証
2. `torch.istft` の center=True/False × length 指定の出力サンプル数の実測表
3. 教師波形 yT の STFT→iSTFT 往復 SNR を 5 通りの規約で測る (n=209)
4. 窓の COLA / OLA 包絡を測り、center=False の端の減衰を dB で出す

実行:
  uv run python scripts/d5_istft_framing.py
"""

from __future__ import annotations

import json
import math
import pathlib
import platform
import subprocess
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from saanotts_jp.labelpack import HOP, SR, Z_CHANNELS, PackReader  # noqa: E402

PACK = ROOT / "data" / "pack_sibdense"
OUT = ROOT / "reports" / "d5_istft_framing.json"

N_FFT, HOPL, WINL = 1024, 256, 1024
REPRO = ("cd . && "
         "uv run python scripts/d5_istft_framing.py")


def snr_db(ref: torch.Tensor, est: torch.Tensor) -> float:
    """10 log10( Σref² / Σ(ref-est)² )。長さは短い方に揃えない（呼び出し側で揃える）。"""
    num = float((ref.double() ** 2).sum())
    den = float(((ref.double() - est.double()) ** 2).sum())
    if den == 0.0:
        return float("inf")
    if num == 0.0:
        return float("-inf")
    return 10.0 * math.log10(num / den)


def stat(xs: list[float]) -> dict:
    a = np.asarray([x for x in xs if math.isfinite(x)], dtype=np.float64)
    n = len(a)
    if n == 0:
        return {"n": 0}
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    return {
        "n": n,
        "mean": round(float(a.mean()), 4),
        "sd": round(sd, 4),
        "ci95_halfwidth": round(1.96 * sd / math.sqrt(n), 4) if n > 1 else None,
        "min": round(float(a.min()), 4),
        "p05": round(float(np.percentile(a, 5)), 4),
        "median": round(float(np.median(a)), 4),
        "max": round(float(a.max()), 4),
    }


# ---------------------------------------------------------------- step 1
def step1_pack_convention(pack: PackReader) -> dict:
    """`sum(ceil(dT)) * 256 == len(yT)` を 209 発話すべてで検証する。

    ⚠️ `PackReader.__getitem__` は `frames*HOP` サンプルを切り出すので
    `len(yT)` 側は index の `frames` に対して定義上一致する。
    **本当に独立な検証は (a) ceil(dT) の合計 == index の frames、
    (b) shard の実バイト数 == Σframes*HOP*2、(c) offset が連続、の 3 つ。**
    ここは 3 つとも測る。
    """
    idx = pack.index
    violations = []
    ratios = []
    frames_list = []
    for i in range(len(pack)):
        u = pack[i]
        ceil_sum = int(np.ceil(u["dT"].astype(np.float64)).sum())
        frames = int(idx[i]["frames"])
        n_y = int(len(u["yT"]))
        n_z = int(u["zT"].shape[1])
        ok = (ceil_sum == frames) and (ceil_sum * HOP == n_y) and (n_z == frames)
        frames_list.append(frames)
        ratios.append(n_y / max(ceil_sum, 1))
        if not ok:
            violations.append({
                "seq": i, "uid": None, "ceil_sum_dT": ceil_sum,
                "index_frames": frames, "len_yT": n_y, "zT_frames": n_z,
            })

    # (b) shard の実バイト数
    shard_check = []
    for sid in sorted(set(int(s) for s in idx["shard"])):
        sel = idx[idx["shard"] == sid]
        tot_f = int(sel["frames"].sum())
        yp = PACK / "shards" / f"{sid:04d}.yt.i16"
        zp = PACK / "shards" / f"{sid:04d}.zt.f16"
        exp_y, exp_z = tot_f * HOP * 2, tot_f * Z_CHANNELS * 2
        act_y, act_z = yp.stat().st_size, zp.stat().st_size
        # (c) offset の連続性
        off_ok = bool(np.array_equal(
            sel["yt_off"],
            np.concatenate([[0], np.cumsum(sel["frames"].astype(np.int64) * HOP)[:-1]])))
        shard_check.append({
            "shard": sid, "n_utt": int(len(sel)), "sum_frames": tot_f,
            "yt_bytes_expected": exp_y, "yt_bytes_actual": act_y, "yt_ok": exp_y == act_y,
            "zt_bytes_expected": exp_z, "zt_bytes_actual": act_z, "zt_ok": exp_z == act_z,
            "yt_offsets_contiguous": off_ok,
        })

    return {
        "n_utterances": len(pack),
        "rule": "sum(ceil(dT)) * 256 == len(yT)  かつ  sum(ceil(dT)) == zT frames",
        "n_violations": len(violations),
        "violations": violations,
        "samples_per_frame_unique": sorted(set(round(r, 6) for r in ratios)),
        "frames": {"min": int(min(frames_list)), "max": int(max(frames_list)),
                   "sum": int(sum(frames_list)),
                   "mean": round(float(np.mean(frames_list)), 2)},
        "shard_byte_check": shard_check,
        "all_shards_ok": all(s["yt_ok"] and s["zt_ok"] and s["yt_offsets_contiguous"]
                             for s in shard_check),
    }


# ---------------------------------------------------------------- step 2
def step2_length_table() -> dict:
    """torch.istft / torch.stft の出力長を実測する。推論しない。"""
    win = torch.hann_window(WINL)
    rows = []
    for T in (10, 37, 100):
        # 実際にありうるスペクトル（乱数の複素数だと NOLA 判定以外は同じ）
        S = torch.fft.rfft(torch.randn(T, N_FFT) * win, dim=-1).transpose(0, 1)  # [513,T]
        for center in (True, False):
            for lname, length in (("None", None),
                                  ("256*T", 256 * T),
                                  ("256*(T-1)", 256 * (T - 1)),
                                  ("256*T+768", 256 * T + 768)):
                row = {"T": T, "center": center, "length_arg": lname}
                try:
                    y = torch.istft(S, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                                    window=win, center=center, length=length)
                    row["n_samples"] = int(y.shape[-1])
                    row["n_samples_div_256"] = round(int(y.shape[-1]) / 256, 4)
                except Exception as e:  # noqa: BLE001
                    row["error"] = f"{type(e).__name__}: {e}"[:400]
                rows.append(row)

    # torch.stft 側: 長さ 256T の信号を何フレームにするか
    stft_rows = []
    for T in (10, 37, 100):
        y = torch.randn(256 * T)
        for center in (True, False):
            S = torch.stft(y, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                           window=win, center=center, return_complex=True)
            stft_rows.append({"T_target": T, "signal_samples": 256 * T,
                              "center": center, "stft_frames": int(S.shape[-1]),
                              "frames_minus_T": int(S.shape[-1]) - T})
    # length= に自然長より長い値を渡したとき、余りが「ゼロ埋め」かどうかを実測する。
    # ここを誤解すると train_student.py の F.pad と同じ無音バグを踏む。
    T = 37
    y0 = torch.randn(256 * T)
    S0 = torch.stft(y0, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                    window=win, center=True, return_complex=True)[..., :T]  # T frames
    nat = torch.istft(S0, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                      window=win, center=True)
    pad = torch.istft(S0, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                      window=win, center=True, length=256 * T)
    zero_pad_probe = {
        "T": T,
        "natural_len": int(nat.numel()),
        "len_with_length_256T": int(pad.numel()),
        "extra_samples": int(pad.numel() - nat.numel()),
        "extra_is_all_zero": bool(torch.all(pad[nat.numel():] == 0)),
        "prefix_identical": bool(torch.equal(pad[: nat.numel()], nat)),
        "extra_rms_over_signal_rms": round(
            float(pad[nat.numel():].pow(2).mean().sqrt() / pad.pow(2).mean().sqrt()), 4),
        "note": ("**ゼロ埋めではない。** center=True の内部バッファは n_fft + hop*(T-1) = "
                 "256T+768 サンプルあり、length=256T は start=n_fft//2 から 256T 分を"
                 "「切り出す」だけ。余分な 256 サンプルは最後のフレームの窓の裾を"
                 "不完全な包絡で割った値で、無音でも正しい信号でもない。"
                 " ただし length が内部バッファ長を超えると本当にゼロ埋めされ、"
                 " PyTorch が UserWarning を出す（本スクリプトの 256*T+768 行）。"),
    }

    # 参考: どこからが本当のゼロ埋めか
    over = torch.istft(S0, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                       window=win, center=True, length=256 * T + 768)
    zero_pad_probe["length_256T_plus_768_len"] = int(over.numel())
    zero_pad_probe["length_256T_plus_768_trailing_zeros"] = int(
        (over.flip(0) == 0).cumprod(0).sum())

    return {
        "istft_output_lengths": rows,
        "stft_frame_counts": stft_rows,
        "length_arg_zero_pads": zero_pad_probe,
        "formulas_confirmed": {
            "istft center=True,  length=None": "hop*(T-1)",
            "istft center=False, length=None": "n_fft + hop*(T-1)",
            "stft  center=True  of L samples": "floor(L/hop)+1",
            "stft  center=False of L samples": "floor((L-n_fft)/hop)+1",
        },
    }


# ---------------------------------------------------------------- OLA helpers
def teacher_frames(y: torch.Tensor, T: int, win: torch.Tensor) -> torch.Tensor:
    """教師規約のフレーミング: frame t = y[256t : 256t+1024]。末尾はゼロ埋め。

    [513, T] complex を返す。
    """
    ypad = F.pad(y, (0, N_FFT))
    S = torch.stft(ypad, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                   window=win, center=False, return_complex=True)
    return S[..., :T]


def ola(S: torch.Tensor, win: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """conv_transpose1d 相当の素の OLA（教師 `OnnxISTFT` と同じ）。

    戻り値 (rec, env)。rec は窓を掛けて足しただけ、env は Σ w² の包絡。
    長さは (T-1)*hop + n_fft。
    """
    T = S.shape[-1]
    x = torch.fft.irfft(S, n=N_FFT, dim=0) * win[:, None]        # [1024, T]
    out_len = (T - 1) * HOPL + N_FFT
    rec = F.fold(x.unsqueeze(0), output_size=(1, out_len),
                 kernel_size=(1, N_FFT), stride=(1, HOPL)).reshape(-1)
    env = F.fold((win ** 2)[:, None].expand(N_FFT, T).unsqueeze(0).contiguous(),
                 output_size=(1, out_len), kernel_size=(1, N_FFT),
                 stride=(1, HOPL)).reshape(-1)
    return rec, env


# ---------------------------------------------------------------- step 3
def step3_roundtrip(pack: PackReader, win: torch.Tensor, limit: int | None = None) -> dict:
    n = len(pack) if limit is None else min(limit, len(pack))
    variants = {
        "a_center_true_len": [],       # stft(center=True)->T+1 frames, istft(center=True, length=256T)
        "b_center_false": [],          # stft(center=False)->T-3 frames, istft(center=False)
        "c_center_true_natural": [],   # stft(center=True)->T+1 frames, istft(center=True) 素
        "d1_teacher_T_env": [],        # 教師規約 T frames + 真の包絡で正規化 + 256T に切る
        "d2_teacher_T_const": [],      # 教師規約 T frames + 定数 1.5 で正規化 + 256T に切る
        "e_current_code_path": [],     # 現状の train_student.py: center=True T frames + 末尾 256 ゼロ埋め
        "f_center_true_T_frames_length": [],  # center=True T frames + length=256T を torch に任せる
    }
    v_tail = {k: [] for k in variants}        # 先頭 768 サンプルを除いた SNR
    v_frames = {k: [] for k in variants}
    v_len_ok = {k: 0 for k in variants}
    errors: dict[str, str] = {}
    const_div = float((win ** 2).sum()) * HOPL / N_FFT   # 教師 stft_onnx.py の wss 式
    true_ss = 3.0 * N_FFT / (8.0 * HOPL)                 # 4x overlap hann の定常包絡

    for i in range(n):
        u = pack[i]
        y = torch.from_numpy(u["yT"]).float()
        T = int(u["zT"].shape[1])
        L = int(y.numel())
        assert L == T * HOPL, (L, T)

        # --- (a)/(c) center=True
        Sa = torch.stft(y, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                        window=win, center=True, return_complex=True)
        v_frames["a_center_true_len"].append(int(Sa.shape[-1]))
        v_frames["c_center_true_natural"].append(int(Sa.shape[-1]))
        ya = torch.istft(Sa, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                         window=win, center=True, length=L)
        v_len_ok["a_center_true_len"] += int(ya.numel() == L)
        variants["a_center_true_len"].append(snr_db(y, ya[:L]))
        v_tail["a_center_true_len"].append(snr_db(y[768:], ya[:L][768:]))

        yc = torch.istft(Sa, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                         window=win, center=True)
        v_len_ok["c_center_true_natural"] += int(yc.numel() == L)
        m = min(L, int(yc.numel()))
        variants["c_center_true_natural"].append(snr_db(y[:m], yc[:m]))
        v_tail["c_center_true_natural"].append(snr_db(y[768:m], yc[768:m]))

        # --- (b) center=False
        try:
            Sb = torch.stft(y, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                            window=win, center=False, return_complex=True)
            v_frames["b_center_false"].append(int(Sb.shape[-1]))
            yb = torch.istft(Sb, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                             window=win, center=False)
            v_len_ok["b_center_false"] += int(yb.numel() == L)
            m = min(L, int(yb.numel()))
            variants["b_center_false"].append(snr_db(y[:m], yb[:m]))
            v_tail["b_center_false"].append(snr_db(y[768:m], yb[768:m]))
        except Exception as e:  # noqa: BLE001
            errors.setdefault("b_center_false", f"{type(e).__name__}: {e}"[:400])

        # --- (e) 現状のコード経路。decoder が center=True の規約で T フレーム出し、
        #         足りない 256 サンプルを F.pad で末尾ゼロ埋めしている (train_student.py:186)
        Se = Sa[..., :T]
        ye = torch.istft(Se, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                         window=win, center=True)
        ye = F.pad(ye, (0, max(0, L - int(ye.numel()))))[:L]
        v_frames["e_current_code_path"].append(int(Se.shape[-1]))
        v_len_ok["e_current_code_path"] += int(ye.numel() == L)
        variants["e_current_code_path"].append(snr_db(y, ye))
        v_tail["e_current_code_path"].append(snr_db(y[768:], ye[768:]))

        # --- (f) center=True の T フレームに length=256T を渡して torch に埋めさせる
        yf = torch.istft(Se, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                         window=win, center=True, length=L)
        v_frames["f_center_true_T_frames_length"].append(int(Se.shape[-1]))
        v_len_ok["f_center_true_T_frames_length"] += int(yf.numel() == L)
        variants["f_center_true_T_frames_length"].append(snr_db(y, yf))
        v_tail["f_center_true_T_frames_length"].append(snr_db(y[768:], yf[768:]))

        # --- (d) 教師規約: T フレーム、frame t は sample 256t から
        Sd = teacher_frames(y, T, win)
        v_frames["d1_teacher_T_env"].append(int(Sd.shape[-1]))
        v_frames["d2_teacher_T_const"].append(int(Sd.shape[-1]))
        rec, env = ola(Sd, win)
        yd1 = (rec / env.clamp_min(1e-11))[:L]
        yd2 = (rec / const_div)[:L]
        v_len_ok["d1_teacher_T_env"] += int(yd1.numel() == L)
        v_len_ok["d2_teacher_T_const"] += int(yd2.numel() == L)
        variants["d1_teacher_T_env"].append(snr_db(y, yd1))
        v_tail["d1_teacher_T_env"].append(snr_db(y[768:], yd1[768:]))
        variants["d2_teacher_T_const"].append(snr_db(y, yd2))
        v_tail["d2_teacher_T_const"].append(snr_db(y[768:], yd2[768:]))

    desc = {
        "a_center_true_len": "stft(center=True) → T+1 frames → istft(center=True, length=256T)",
        "b_center_false": "stft(center=False) → T-3 frames → istft(center=False)",
        "c_center_true_natural": "stft(center=True) → T+1 frames → istft(center=True) 素（length 無し）",
        "d1_teacher_T_env": "教師規約 T frames (frame t = y[256t:256t+1024]) + 真の Σw² 包絡で除算 + 先頭 256T に切る",
        "d2_teacher_T_const": f"同上だが教師 stft_onnx.py の定数 wss={const_div:.4f} で除算",
        "e_current_code_path": ("現状 train_student.py:186 の経路。center=True 規約の T frames を "
                                "istft(center=True) して 256(T-1) サンプルを得、F.pad で末尾に 256 "
                                "サンプルのゼロを足す。**完璧な decoder でもこの SNR が上限**"),
        "f_center_true_T_frames_length": ("center=True の T frames に length=256T を渡し torch に"
                                          " 長さを合わせさせる。ゼロ埋めではなく最後のフレームの裾が"
                                          " 不完全な包絡で出てくる"),
    }
    out = {"n_utterances_measured": n,
           "CAVEAT": (
               "**往復 SNR はフレーミング規約の優劣を判定しない。** 解析窓 → 完全 IFFT → 合成窓 "
               "を通すと各フレームはそのサンプルにちょうど w²·y を寄与するので、包絡 Σw² で割れば "
               "env>0 の所はフレームの部分集合でも厳密に復元する。実測でも (a)/(c)/(f) が "
               "139.009 dB で完全に並ぶ。この測定が示すのは (i) 出力サンプル数が 256T になるか、"
               "(ii) torch.istft がそもそも動くか、(iii) 包絡が 0 に近い所で fp32 が壊れるか、の 3 点だけ。"
               " 規約の選択は step6（包絡の最小値）とフレーム数の要求で決める。"),
           "const_divisor_from_teacher_formula": round(const_div, 6),
           "true_steady_state_envelope_3N_over_8H": round(true_ss, 6),
           "note_on_teacher_constant": (
               "教師の wss = Σw²·hop/n_fft は 3·hop/8、正しい定常包絡は 3·n_fft/(8·hop)。"
               " 両者が一致するのは hop² == n_fft のときだけで、教師の設定 (n_fft=16, hop=4)"
               " はちょうどそれに当たる偶然。生徒の 1024/256 では一致しない"
               f" ({round(const_div,4)} vs {round(true_ss,4)}) ので定数をそのまま流用してはいけない。"),
           "variants": {}}
    for k in variants:
        if not variants[k]:
            out["variants"][k] = {"description": desc[k], "error": errors.get(k, "no data")}
            continue
        fr = v_frames[k]
        out["variants"][k] = {
            "description": desc[k],
            "stft_frames_vs_T": sorted(set(int(f) for f in fr))[:3] if fr else None,
            "n_output_length_equals_256T": v_len_ok[k],
            "snr_db_full": stat(variants[k]),
            "snr_db_excluding_first_768_samples": stat(v_tail[k]),
        }
    if errors:
        out["errors"] = errors
    return out


# ---------------------------------------------------------------- step 4
def proposed_istft(mag: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """`_param_reference.py` の `Decoder.istft` の置き換え案（採用候補）。

    教師 (`mb_istft.py:308-310` / `stft_onnx.py`) と同じ規約:
      frame t は sample 256t から始まる (center=False)。**T フレーム → ちょうど 256T サンプル。**
    torch.istft(center=False) は hann(1024)/hop256 で必ず NOLA チェックに落ちるので使えない。
    """
    B, _, T = mag.shape
    S = torch.complex(mag * cos, mag * sin)                      # [B, 513, T]
    x = torch.fft.irfft(S, n=N_FFT, dim=1)                       # [B, 1024, T]
    w = torch.hann_window(WINL, device=mag.device, dtype=mag.dtype)
    out_len = (T - 1) * HOPL + N_FFT
    y = F.fold((x * w[None, :, None]), output_size=(1, out_len),
               kernel_size=(1, N_FFT), stride=(1, HOPL)).reshape(B, out_len)
    env = F.fold((w ** 2)[None, :, None].expand(1, N_FFT, T).contiguous(),
                 output_size=(1, out_len), kernel_size=(1, N_FFT),
                 stride=(1, HOPL)).reshape(1, out_len)
    return (y / env.clamp_min(1e-8))[:, : T * HOPL]


def recommended_istft(mag: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """**採用案**。`_param_reference.py` の `Decoder.istft` の 1 行修正。

    `center=True` のまま `length = T * 256` を渡すだけ。
    torch.istft の内部バッファは n_fft + hop*(T-1) = 256T+768 サンプルあり、
    `length` は start=n_fft//2 から 256T 分を **切り出す**（ゼロ埋めではない。
    ゼロ埋めになるのは length > 256T+768 のときで、そのとき PyTorch が warn する）。
    ⇒ **T フレーム入力でちょうど 256T サンプル出力。**
    """
    S = torch.complex(mag * cos, mag * sin)
    T = mag.shape[-1]
    return torch.istft(S, n_fft=N_FFT, hop_length=HOPL, win_length=WINL,
                       window=torch.hann_window(WINL, device=S.device),
                       center=True, length=T * HOPL)


def step4_proposed(pack: PackReader, win: torch.Tensor) -> dict:
    """採用案と代替案が (i) 長さ、(ii) 微分可能性、(iii) 速度、(iv) 警告なし を満たすか。"""
    shapes = []
    for T in (10, 37, 100):
        mag = torch.rand(2, 513, T)
        y = proposed_istft(mag, torch.cos(torch.rand(2, 513, T)), torch.sin(torch.rand(2, 513, T)))
        shapes.append({"T": T, "batch": 2, "out_samples": int(y.shape[-1]),
                       "equals_256T": int(y.shape[-1]) == 256 * T})

    # 微分可能性
    mag = torch.rand(1, 513, 64, requires_grad=True)
    c = torch.rand(1, 513, 64, requires_grad=True)
    sn = torch.rand(1, 513, 64, requires_grad=True)
    proposed_istft(mag, c, sn).pow(2).mean().backward()
    grad_ok = all(g is not None and bool(torch.isfinite(g).all()) for g in
                  (mag.grad, c.grad, sn.grad))
    grad_norm = float(mag.grad.norm())

    # d1 と bit 単位で一致するか（教師波形 1 本で照合）
    u = pack[1]
    y = torch.from_numpy(u["yT"]).float()
    T = int(u["zT"].shape[1])
    S = teacher_frames(y, T, win)
    rec, env = ola(S, win)
    ref = (rec / env.clamp_min(1e-11))[: T * HOPL]
    got = proposed_istft(S.abs().unsqueeze(0),
                         torch.cos(S.angle()).unsqueeze(0),
                         torch.sin(S.angle()).unsqueeze(0))[0]
    match_snr = snr_db(ref, got)

    # 速度（学習で毎 step 走るので測る）。batch 8 x 128 frames = セグメント相当
    mag = torch.rand(8, 513, 128)
    cs, sn = torch.cos(torch.rand(8, 513, 128)), torch.sin(torch.rand(8, 513, 128))
    for _ in range(3):
        proposed_istft(mag, cs, sn)
    t0 = time.perf_counter()
    for _ in range(20):
        proposed_istft(mag, cs, sn)
    prop_ms = (time.perf_counter() - t0) / 20 * 1000
    S8 = torch.complex(mag * cs, mag * sn)
    for _ in range(3):
        torch.istft(S8, N_FFT, HOPL, WINL, window=win, center=True)
    t0 = time.perf_counter()
    for _ in range(20):
        torch.istft(S8, N_FFT, HOPL, WINL, window=win, center=True)
    cur_ms = (time.perf_counter() - t0) / 20 * 1000

    # ---- 採用案 (recommended_istft) の検証 ----
    import warnings
    rec_shapes, rec_warn = [], []
    for T in (10, 37, 100, 779):
        mag = torch.rand(2, 513, T)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            y = recommended_istft(mag, torch.cos(torch.rand(2, 513, T)),
                                  torch.sin(torch.rand(2, 513, T)))
        rec_shapes.append({"T": T, "batch": 2, "out_samples": int(y.shape[-1]),
                           "equals_256T": int(y.shape[-1]) == 256 * T,
                           "n_warnings": len(caught),
                           "warnings": [str(c.message)[:120] for c in caught]})
        rec_warn.append(len(caught))

    mag = torch.rand(1, 513, 64, requires_grad=True)
    c2 = torch.rand(1, 513, 64, requires_grad=True)
    s2 = torch.rand(1, 513, 64, requires_grad=True)
    recommended_istft(mag, c2, s2).pow(2).mean().backward()
    rec_grad_ok = all(g is not None and bool(torch.isfinite(g).all())
                      for g in (mag.grad, c2.grad, s2.grad))

    # 実物の Decoder に通して端から端まで動くか
    from saanotts_jp._param_reference import Acoustic, Decoder, Erho  # noqa: PLC0415
    dec = Decoder()
    real = []
    for T in (10, 37, 100):
        cvec = torch.randn(1, 40, T)
        m_, co, si = dec(cvec)
        y_old = Decoder.istft(m_, co, si)
        y_new = recommended_istft(m_, co, si)
        real.append({"T": T, "c_shape": list(cvec.shape),
                     "mag_frames": int(m_.shape[-1]),
                     "current_istft_samples": int(y_old.shape[-1]),
                     "current_shortfall_vs_256T": 256 * T - int(y_old.shape[-1]),
                     "recommended_istft_samples": int(y_new.shape[-1]),
                     "recommended_equals_256T": int(y_new.shape[-1]) == 256 * T})

    mag8 = torch.rand(8, 513, 128)
    cs8, sn8 = torch.cos(torch.rand(8, 513, 128)), torch.sin(torch.rand(8, 513, 128))
    for _ in range(3):
        recommended_istft(mag8, cs8, sn8)
    t0 = time.perf_counter()
    for _ in range(20):
        recommended_istft(mag8, cs8, sn8)
    rec_ms = (time.perf_counter() - t0) / 20 * 1000

    return {
        "recommended_istft": {
            "what": "Decoder.istft に length=T*256 を足すだけ（center=True のまま）",
            "output_shapes": rec_shapes,
            "all_equal_256T": all(r["equals_256T"] for r in rec_shapes),
            "zero_pad_warning_count": sum(rec_warn),
            "differentiable": rec_grad_ok,
            "through_real_Decoder": real,
            "speed_cpu_ms_per_call_batch8x128frames": round(rec_ms, 3),
        },
        "alternative_teacher_center_false_ola": {
        "what": "教師 stft_onnx.py と同じ素の OLA を F.fold で書いた版（採用しないが実測は残す）",
        "output_shapes": shapes,
        "all_equal_256T": all(s["equals_256T"] for s in shapes),
        "differentiable": grad_ok,
        "grad_norm_mag": round(grad_norm, 6),
        "snr_vs_variant_d1_db": (None if math.isinf(match_snr) else round(match_snr, 2)),
        "snr_vs_variant_d1_is_inf": math.isinf(match_snr),
        "speed_cpu_ms_per_call_batch8x128frames": {
            "proposed_fold_ola": round(prop_ms, 3),
            "current_torch_istft_center_true": round(cur_ms, 3),
            "ratio": round(prop_ms / cur_ms, 2),
        },
        },
    }


# ---------------------------------------------------------------- step 5
def step5_cola(win: torch.Tensor) -> dict:
    """COLA / OLA 包絡。center=False の端で実際にどれだけ減衰するかを測る。"""
    T = 64
    env = F.fold((win ** 2)[:, None].expand(N_FFT, T).unsqueeze(0).contiguous(),
                 output_size=(1, (T - 1) * HOPL + N_FFT),
                 kernel_size=(1, N_FFT), stride=(1, HOPL)).reshape(-1)
    steady = float(env[N_FFT:-N_FFT].mean())
    head = env[:N_FFT].numpy()
    trimmed_head = head[:768]     # 256T に切ったとき先頭に残る不完全域
    with np.errstate(divide="ignore"):
        head_db = 20.0 * np.log10(np.maximum(trimmed_head, 1e-30) / steady)
    marks = [0, 1, 63, 127, 255, 256, 383, 511, 512, 639, 767, 768, 1023]
    return {
        "window": "torch.hann_window(1024)  (periodic=True)",
        "hop": HOPL, "overlap_factor": N_FFT // HOPL,
        "steady_state_envelope_sum_w2": round(steady, 6),
        "analytic_3N_over_8H": round(3.0 * N_FFT / (8.0 * HOPL), 6),
        "cola_satisfied_in_steady_state": bool(
            float((env[N_FFT:-N_FFT] - steady).abs().max()) < 1e-4),
        "max_abs_deviation_in_steady_region": float(
            (env[N_FFT:-N_FFT] - steady).abs().max()),
        "incomplete_head_samples": 768,
        "incomplete_head_ms": round(768 / SR * 1000, 2),
        "envelope_at_sample": {str(m): round(float(env[m]), 8) for m in marks},
        "attenuation_db_at_sample": {
            str(m): (None if m >= 768 else round(float(head_db[m]), 3)) for m in marks},
        "worst_attenuation_db_in_head": round(float(head_db.min()), 3),
        "mean_attenuation_db_over_head_768": round(float(head_db[1:].mean()), 3),
        "n_head_samples_attenuated_more_than": {
            "0.1dB": int((head_db < -0.1).sum()),
            "1dB": int((head_db < -1.0).sum()),
            "6dB": int((head_db < -6.0).sum()),
            "20dB": int((head_db < -20.0).sum()),
            "40dB": int((head_db < -40.0).sum()),
        },
        "head_ms_attenuated_more_than_1dB": round(
            float((head_db < -1.0).sum()) / SR * 1000, 2),
        "sample0_envelope_is_exactly_zero": bool(float(env[0]) == 0.0),
        "note": ("periodic hann は w[0]=0 なので sample 0 の包絡はちょうど 0。"
                 " center=False では sample 0 の情報が解析段で消える（復元不能な 1 サンプル）。"
                 " 末尾は 256T に切るので 4 重オーバーラップが揃っており減衰しない。"),
    }


# ---------------------------------------------------------------- step 8
def step8_frame_alignment(win: torch.Tensor, T: int = 32, t_probe: int = 12) -> dict:
    """スペクトルの 1 フレームだけ非ゼロにして、出力のどこに落ちるかを実測する。

    教師では z フレーム t が音声 [256t, 256t+256) をちょうど担当する（block upsample）。
    生徒の c フレーム t が出力のどこに乗るかを測って、ズレを sample 単位で出す。
    """
    # 全 bin を 1 + 位相 0 にすると irfft が n=0 のデルタになり w[0]=0 で消えるので、
    # ランダム位相の広帯域バーストで測る（フレーム 1 個ぶんの窓に局在する）。
    g = torch.Generator().manual_seed(0)
    phase = torch.rand(513, generator=g) * 2 * math.pi
    amp = torch.rand(513, generator=g)

    def probe(fn) -> dict:
        mag = torch.zeros(1, 513, T)
        cs = torch.zeros(1, 513, T)
        sn = torch.zeros(1, 513, T)
        mag[0, :, t_probe] = amp
        cs[0, :, t_probe] = torch.cos(phase)
        sn[0, :, t_probe] = torch.sin(phase)
        y = fn(mag, cs, sn)[0].abs()
        thr = float(y.max()) * 1e-3
        nz = torch.nonzero(y > thr).flatten()
        idx = torch.arange(y.numel(), dtype=torch.float64)
        e = y.double() ** 2
        com = float((idx * e).sum() / e.sum())
        return {"support_first_rel1e-3": int(nz.min()), "support_last_rel1e-3": int(nz.max()),
                "support_len": int(nz.max() - nz.min() + 1),
                "energy_center_of_mass": round(com, 1),
                "peak_at": int(y.argmax())}

    teacher_block = {"owns_samples": [256 * t_probe, 256 * t_probe + 255],
                     "block_center": 256 * t_probe + 127.5}
    rec = probe(recommended_istft)
    alt = probe(lambda m, c, s: proposed_istft(m, c, s))
    return {
        "probe_frame_index": t_probe, "T": T,
        "teacher_z_frame_maps_to": teacher_block,
        "recommended_center_true": dict(
            rec, offset_of_com_vs_teacher_block_center=round(
                rec["energy_center_of_mass"] - teacher_block["block_center"], 1)),
        "alternative_center_false_fold": dict(
            alt, offset_of_com_vs_teacher_block_center=round(
                alt["energy_center_of_mass"] - teacher_block["block_center"], 1)),
        "note": ("どちらも定数オフセットで、decoder の受容野（depthwise k=7 × 5 段 = 31 フレーム "
                 "≒ 7936 サンプル）に比べれば十分小さいので学習で吸収できる。"
                 " ただし center=True のほうがズレが小さい。"),
    }


# ---------------------------------------------------------------- step 7
def step7_edge_energy(pack: PackReader) -> dict:
    """端の区間が実際どれだけエネルギーを持つか（=規約の弱点がどれだけ効くか）。"""
    head768, head256, tail256, tail512 = [], [], [], []
    peak_head, peak_tail = [], []
    for i in range(len(pack)):
        y = pack[i]["yT"].astype(np.float64)
        tot = float((y ** 2).sum()) + 1e-30
        head768.append(float((y[:768] ** 2).sum()) / tot)
        head256.append(float((y[:256] ** 2).sum()) / tot)
        tail256.append(float((y[-256:] ** 2).sum()) / tot)
        tail512.append(float((y[-512:] ** 2).sum()) / tot)
        peak_head.append(float(np.abs(y[:768]).max()) / (float(np.abs(y).max()) + 1e-30))
        peak_tail.append(float(np.abs(y[-256:]).max()) / (float(np.abs(y).max()) + 1e-30))
    def pct(xs):
        a = np.asarray(xs)
        return {"mean_pct": round(float(a.mean()) * 100, 6),
                "median_pct": round(float(np.median(a)) * 100, 6),
                "max_pct": round(float(a.max()) * 100, 6)}
    return {
        "n": len(pack),
        "energy_fraction_first_768_samples": pct(head768),
        "energy_fraction_first_256_samples": pct(head256),
        "energy_fraction_last_512_samples": pct(tail512),
        "energy_fraction_last_256_samples": pct(tail256),
        "peak_ratio_first_768_vs_utterance": {
            "mean": round(float(np.mean(peak_head)), 5),
            "median": round(float(np.median(peak_head)), 5),
            "max": round(float(np.max(peak_head)), 5)},
        "peak_ratio_last_256_vs_utterance": {
            "mean": round(float(np.mean(peak_tail)), 5),
            "median": round(float(np.median(peak_tail)), 5),
            "max": round(float(np.max(peak_tail)), 5)},
        "note": ("教師は決定的推論なので発話の前後がほぼ無音になる。"
                 " 端の包絡が悪い規約でも、そこに乗っている信号自体が小さければ実害は小さい。"),
    }


# ---------------------------------------------------------------- step 6
def step6_envelope_per_convention(win: torch.Tensor, T: int = 64) -> dict:
    """**ここが本当の判別材料。**

    往復 SNR は規約を区別しない: 解析窓を掛けて完全な IFFT を通し合成窓を掛けると
    各フレームはそのサンプルに `w²·y` をちょうど寄与するので、
    包絡 `Σw²` で割れば **どんなフレーム部分集合でも env>0 の所は厳密に復元する**。
    実際 (a)/(c)/(f) は 139 dB で並ぶ。

    生徒では decoder が任意のスペクトルを出すので、効くのは
    **返す区間での包絡の最小値** = `1/env` の増幅率（もしくは定数で割る場合の減衰）。
    """
    def env_of(n_frames: int, offsets_start: int) -> torch.Tensor:
        out_len = (n_frames - 1) * HOPL + N_FFT
        e = F.fold((win ** 2)[:, None].expand(N_FFT, n_frames).unsqueeze(0).contiguous(),
                   output_size=(1, out_len), kernel_size=(1, N_FFT),
                   stride=(1, HOPL)).reshape(-1)
        return e[offsets_start:offsets_start + T * HOPL]

    steady = 1.5
    convs = {
        "center_true_T_plus_1_frames": (T + 1, N_FFT // 2),
        "center_true_T_frames_length_256T": (T, N_FFT // 2),
        "teacher_center_false_T_frames": (T, 0),
    }
    out = {}
    for name, (nf, st) in convs.items():
        e = env_of(nf, st)
        assert int(e.numel()) == T * HOPL, (name, int(e.numel()))
        amp = steady / e.clamp_min(1e-30)
        out[name] = {
            "frames_required": nf,
            "returned_samples": int(e.numel()),
            "env_min": round(float(e.min()), 10),
            "env_max": round(float(e.max()), 6),
            "env_min_position": int(e.argmin()),
            "worst_gain_vs_steady": (None if float(e.min()) == 0.0
                                     else round(float(amp.max()), 2)),
            "worst_gain_is_unbounded": bool(float(e.min()) == 0.0),
            "n_samples_gain_over_2x": int((amp > 2).sum()),
            "n_samples_gain_over_10x": int((amp > 10).sum()),
            "n_samples_env_below_1pct_of_steady": int((e < 0.015).sum()),
        }
    out["_interpretation"] = (
        "center_true_T_plus_1: 包絡の最小 1.25（先頭と末尾で -1.58 dB のみ）。数値的に安全だが "
        "decoder が T+1 フレーム出す必要がある。 "
        "center_true_T_frames_length_256T: 同じく最小 1.25 だが末尾 256 サンプルが "
        "フレーム T-1 の窓の裾だけで作られる（復元は厳密でも decoder の出力誤差がそこに集中する）。 "
        "teacher_center_false: T フレームで 256T サンプルが出る唯一の規約だが、"
        "先頭 640 サンプルで包絡が 1.5 → 0 に落ち、sample 0 では厳密に 0 になる。")
    return out


def main() -> None:
    t0 = time.time()
    torch.manual_seed(0)
    win = torch.hann_window(WINL)
    pack = PackReader(PACK)

    s1 = step1_pack_convention(pack)
    s2 = step2_length_table()
    s3 = step3_roundtrip(pack, win)
    s4 = step4_proposed(pack, win)
    s5 = step5_cola(win)
    s6 = step6_envelope_per_convention(win)
    s7 = step7_edge_energy(pack)
    s8 = step8_frame_alignment(win)

    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = None

    decision = {
        "verdict": (
            "**center=True のまま `length = T*256` を渡す。**"
            " Decoder は今までどおり T フレームぶんの mag/cos/sin を出せばよく、"
            " 出力はちょうど 256T サンプルになる。教師の規約（center=False の素の OLA を"
            " T*hop に切る）をそのまま真似るのは採らない。"),
        "criterion": "生徒が T フレームの c から教師と同じ 256*T サンプルを出せること",
        "why_not_teacher_center_false": [
            "torch.istft(center=False) は hann(1024)/hop=256 で **必ず** RuntimeError "
            "('window overlap add min: 1')。length を何に指定しても回避できない（step2、T=10/37/100 で全滅）。"
            " 使うなら F.fold / conv_transpose1d で自前 OLA を書くことになる",
            "自前 OLA にしても先頭 640 サンプルで包絡 Σw² が 1.5 → 0 に落ちる。"
            " 真の包絡で割ると decoder の出力誤差が最大 1103 倍に増幅され（sample 63 で env=0.00136）、"
            " sample 0 では env がちょうど 0 で復元不能。219 サンプルが 10 倍超の増幅域に入る",
            "定数で割る（教師 stft_onnx.py の方式）と先頭が最大 60 dB 減衰する。"
            " しかも教師の定数式 wss = Σw²·hop/n_fft = 3·hop/8 は正しい定常包絡 3·n_fft/(8·hop) と"
            " hop² == n_fft のときしか一致しない。教師 (n_fft=16, hop=4) はちょうどその条件を満たす偶然で、"
            " 生徒の 1024/256 では 96 vs 1.5 と 64 倍ずれる（往復 SNR 0.14 dB で実測、variant d2）",
            "教師の z フレーム t は音声 [256t, 256t+256) を担当する。center=False だと"
            " c フレーム t のエネルギー重心が +390.5 サンプル (17.7 ms) ずれる。"
            " center=True なら -121.5 サンプル (5.5 ms) で済む（step8 実測）",
        ],
        "why_not_T_plus_1_frames": (
            "center=True で T+1 フレーム出せば包絡の最小は 1.25（最悪 1.20 倍）ともっと素直だが、"
            " c[40,T] から T+1 フレームを出すには c を 1 フレーム水増しする必要がある。"
            " 採用案の包絡最小 0.2531（最悪 5.93 倍、2 倍超は 128 サンプルだけ）は"
            " その 128 サンプルが発話末尾＝実質無音（末尾 256 サンプルのエネルギー比 平均 0.0%、"
            " 最大 1e-6%、ピーク比 平均 0.011%）なので実害が無い"),
        "diff_param_reference_py": {
            "file": "src/saanotts_jp/_param_reference.py",
            "symbol": "Decoder.istft (86-90 行)",
            "before": (
                "    @staticmethod\n"
                "    def istft(mag, cos, sin):\n"
                "        S = torch.complex(mag * cos, mag * sin)\n"
                "        return torch.istft(S, n_fft=1024, hop_length=256, win_length=1024,\n"
                "                           window=torch.hann_window(1024, device=S.device), center=True)\n"),
            "after": (
                "    @staticmethod\n"
                "    def istft(mag, cos, sin):\n"
                '        """T フレーム → ちょうど 256*T サンプル。教師の y_lengths 規約に一致させる。\n'
                "\n"
                "        `length` を省くと torch.istft は 256*(T-1) しか返さない（1 フレーム不足）。\n"
                "        length=T*256 は内部バッファ n_fft + hop*(T-1) = 256T+768 から\n"
                "        start=n_fft//2 で 256T 分を **切り出す** だけでゼロ埋めではない\n"
                "        （ゼロ埋めになるのは length > 256T+768 のときで PyTorch が warn する）。D5 実測。\n"
                '        """\n'
                "        S = torch.complex(mag * cos, mag * sin)\n"
                "        T = mag.shape[-1]\n"
                "        return torch.istft(S, n_fft=1024, hop_length=256, win_length=1024,\n"
                "                           window=torch.hann_window(1024, device=S.device),\n"
                "                           center=True, length=T * 256)\n"),
            "verified": "step4.recommended_istft — T=10/37/100/779 で out==256T、警告 0、勾配 finite、"
                        "実物の Decoder に通しても 256T",
        },
        "diff_train_student_py": {
            "file": "scripts/train_student.py",
            "lines": [186, 229],
            "before": "        y_hat = F.pad(y_hat, (0, max(0, y.shape[-1] - y_hat.shape[-1])))[:, : y.shape[-1]]",
            "after": "        assert y_hat.shape[-1] == y.shape[-1], (y_hat.shape, y.shape)",
            "why": ("現状は足りない 256 サンプルを末尾ゼロで埋めている。**完璧な decoder でも "
                    "SNR の上限が 95.03 ± 0.55 dB（n=209, min 79.71）に張り付く**（variant e）。"
                    " 生徒が到達しうる SNR より遥かに高いので学習は壊れないが、"
                    " 最後の 1 フレームぶんの勾配が常に「無音を出せ」になるのは正しくない"),
        },
        "device_note": ("ESP32 の C99 実装では center=True の半窓パディングを自前で持つ必要がある: "
                        "出力サンプル n は frame floor(n/256)-1 .. floor(n/256)+2 の寄与を受けるので "
                        "2 フレーム先読み（+23 ms）が要る。center=False なら先読み 0 で済むので、"
                        "**デバイス側だけは center=False + 先頭 768 サンプルの包絡補正テーブル "
                        "(768 × 4 B = 3 KB) に切り替える選択肢がある。ただしそれは学習と規約が変わるので"
                        "別タスクとして測ること（未測定）。**"),
        "residual_risk": (
            "採用案でも c フレーム t のエネルギー重心は教師のブロック中心から -121.5 サンプル "
            "(5.5 ms) ずれる。decoder の受容野は depthwise k=7 × 5 段 = 31 フレーム ≒ 7936 サンプル "
            "なので学習で吸収できるはずだが、**吸収されることは未検証**（本学習の後に "
            "教師 yT と生徒 ŷ の相互相関ピーク位置で確かめること）。"),
    }

    report = {
        "task": "D5 iSTFT のフレーミング規約を確定する",
        "decision": decision,
        "date": "2026-08-27",
        "repro": REPRO,
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "device": "cpu",
            "git_commit": commit,
        },
        "pack": {"path": str(PACK), "manifest_n_utterances": pack.manifest["n_utterances"]},
        "teacher_convention_source": {
            "models.py:1052-1053": "w_ceil = ceil(w); y_lengths = clamp_min(sum(w_ceil),1)",
            "mb_istft.py:308-310": (
                "expected_sub_T = T_frames * self.hop_length; "
                "subbands_signal = subbands_signal[..., :expected_sub_T]  ← 明示的に切る"),
            "stft_onnx.py:8": "\"The inverse basis absorbs the Hann window and OLA "
                              "normalisation so that no post-processing is needed "
                              "(center=False, no trimming).\"",
            "stft_onnx.py:60-62": "F.conv_transpose1d(combined, inverse_basis, stride=hop) "
                                  "→ 出力長 (T-1)*hop + n_fft、その後 T*hop に切る",
        },
        "step1_pack_frame_sample_relation": s1,
        "step2_torch_istft_length_table": s2,
        "step3_roundtrip_snr": s3,
        "step4_proposed_implementation": s4,
        "step5_cola": s5,
        "step6_envelope_per_convention": s6,
        "step7_edge_energy": s7,
        "step8_frame_alignment": s8,
        "elapsed_sec": round(time.time() - t0, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("step1_pack_frame_sample_relation",
                               "step3_roundtrip_snr", "step5_cola")},
                     ensure_ascii=False, indent=1)[:6000])
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
