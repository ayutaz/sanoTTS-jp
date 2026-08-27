"""S-1 (a1): 論文の blob サイズを **宣言した padding モデル**込みで再検証する。

論文本文（自分で取得: sha256 64b0d426b585e05f87867375928755a79d02ffc989374c08f1aa52c37267eab1
/ 311,287 B / `pdftotext -layout` で 353 行）から:

* §III-A「Its two blobs occupy 280,288 and 399,544 bytes.
  **Padding**, floating-point biases, and per-channel scales account for the
  difference between parameter count and binary size.」
* §V「Embeddings, normalization affines, and **the inverse-STFT support code**
  remain in floating point.」

`reports/r3_blob2_constraint.py` の `blob = P + 3B + 4C` は **padding 項を持たない**。
だから「0 件」は「解が無い」ではなく「前提が違う」を意味する。ここでは
padding と iSTFT 支援表を**上限が計算できる形で宣言してから**再探索する。

⚠️ **padding を自由変数にしない。** 自由にすると任意の構成が通り、
(a) を好きな向きに「否定」できてしまう（G8）。

実行: uv run python reports/e2_blob_padding.py
"""
import json
import sys

import numpy as np
import torch

sys.path.insert(0, "src")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration  # noqa: E402
from saanotts_jp.ptq import quantize_tensor  # noqa: E402

PAPER = {"blob1": 280288, "blob2": 399544, "total": 679832, "params": 567008}
VOCAB_EN, VOCAB_JA = 157, 57


def module_blob(m, int8=True):
    """`scripts/export_c_weights.py` と同一の規則で 1 モジュールの実バイトを積む。

    返り値: (payload_bytes, n_tensors, n_int8_tensors, params)
    """
    payload = n_t = n_i8 = params = 0
    for k, v in m.state_dict().items():
        params += v.numel()
        if int8 and v.dim() >= 2 and "emb" not in k and "pos" not in k:
            q2d, sc = quantize_tensor(v)
            payload += q2d.size * 1 + sc.size * 4      # int8 本体 + fp32 scale
            n_t += 2
            n_i8 += 1
        else:
            payload += v.numel() * 4
            n_t += 1
    return payload, n_t, n_i8, params


def main():
    out = {"paper": PAPER, "paper_pdf_sha256":
           "64b0d426b585e05f87867375928755a79d02ffc989374c08f1aa52c37267eab1"}

    for tag, V in (("ja_V57", VOCAB_JA), ("en_V157", VOCAB_EN)):
        mods = {"duration": Duration(V=V), "acoustic": Acoustic(V=V),
                "decoder": Decoder()}
        r = {}
        for name, m in mods.items():
            p, nt, ni8, par = module_blob(m)
            r[name] = {"payload_bytes": p, "n_tensors": nt, "n_int8_tensors": ni8,
                       "params": par}
        r["blob1_dur_plus_ac"] = (r["duration"]["payload_bytes"]
                                  + r["acoustic"]["payload_bytes"])
        r["blob2_decoder"] = r["decoder"]["payload_bytes"]
        r["total_payload"] = r["blob1_dur_plus_ac"] + r["blob2_decoder"]
        r["total_params"] = sum(r[k]["params"] for k in ("duration", "acoustic", "decoder"))
        out[tag] = r

    en = out["en_V157"]
    assert en["total_params"] == PAPER["params"], en["total_params"]

    # ---- 宣言する padding モデル（上限が計算できる形に限る）----
    dec_tensors = en["decoder"]["n_tensors"]
    b1_tensors = (en["duration"]["n_tensors"] + en["acoustic"]["n_tensors"])
    pad_models = {}
    for A in (4, 16, 64):
        pad_models[f"align_{A}B"] = {
            "blob1_max": b1_tensors * (A - 1), "blob2_max": dec_tensors * (A - 1)}

    # ---- 宣言する iSTFT 支援表（fp32）----
    istft = {
        "hann_1024_fp32": 1024 * 4,
        "rfft_twiddle_512c_fp32": 512 * 2 * 4,
        "full_fft_twiddle_1024c_fp32": 1024 * 2 * 4,
        "bitrev_1024_u32": 1024 * 4,
    }
    istft_min = istft["hann_1024_fp32"] + istft["rfft_twiddle_512c_fp32"]
    istft_max = (istft["hann_1024_fp32"] + istft["full_fft_twiddle_1024c_fp32"]
                 + istft["bitrev_1024_u32"])
    out["declared_padding_models"] = pad_models
    out["declared_istft_support"] = {**istft, "min": istft_min, "max": istft_max}

    # ---- 到達可能な上限 ----
    A = 64                                   # 最も緩い宣言
    b2_max = en["blob2_decoder"] + pad_models["align_64B"]["blob2_max"] + istft_max
    b1_min = en["blob1_dur_plus_ac"]         # padding は増やすことしかできない
    out["reachability"] = {
        "blob2_ours": en["blob2_decoder"],
        "blob2_max_with_align64_and_istft": b2_max,
        "blob2_paper": PAPER["blob2"],
        "blob2_still_short_by": PAPER["blob2"] - b2_max,
        "blob2_reachable": b2_max >= PAPER["blob2"],
        "blob1_ours_min": b1_min,
        "blob1_paper": PAPER["blob1"],
        "blob1_excess_ours_minus_paper": b1_min - PAPER["blob1"],
        "blob1_reachable": b1_min <= PAPER["blob1"],
        "note": "padding は**増やすことしかできない**ので、うちの blob1 が論文より"
                "大きいことは padding では説明できない",
    }

    # ---- 合計での到達可能性（blob の中身は論文に書かれていないので、合計が本命）----
    avail = (pad_models["align_64B"]["blob1_max"] + pad_models["align_64B"]["blob2_max"]
             + istft_max)
    need = PAPER["total"] - en["total_payload"]
    per_align = {}
    for A in (4, 16, 64):
        av = (pad_models[f"align_{A}B"]["blob1_max"]
              + pad_models[f"align_{A}B"]["blob2_max"] + istft_max)
        per_align[f"align_{A}B"] = {"available": av,
                                    "reachable": bool(av >= PAPER["total"] - en["total_payload"])}
    out["total_reachability_by_alignment"] = per_align
    out["total_reachability"] = {
        "our_total_payload": en["total_payload"],
        "paper_total": PAPER["total"],
        "need": need,
        "available_align64_plus_istft_max": avail,
        "reachable": bool(need <= avail),
        "slack": avail - need,
        "note": "⚠️ 論文は blob の中身（どのモジュールがどちらに入るか）を書いていない。"
                "したがって**合計**が到達可能かどうかが (a1) の本命の判定になる",
    }

    # ---- G8 陽性対照: padding=0 / iSTFT なしなら、うちの構成はうちの実測値に一致する ----
    pos = {
        "target": en["blob2_decoder"],
        "recomputed": module_blob(Decoder())[0],
        "ok": module_blob(Decoder())[0] == en["blob2_decoder"],
        "note": "同じ規則で 2 回計算して一致するか（探索器が対象を測っていることの確認）",
    }
    # 陰性対照: 層を 1 つ落とすと必ず値が変わる
    d = Decoder()
    d.dw = torch.nn.ModuleList(list(d.dw)[:-1])
    pos["negative_control_drop_one_dw"] = module_blob(d)[0]
    pos["negative_control_differs"] = module_blob(d)[0] != en["blob2_decoder"]
    out["G8_positive_control"] = pos

    # ---- ブロブ分割を変えれば合うか（分割仮説）----
    # 論文は blob の中身を明示していない。うちは blob1=D+A / blob2=G と仮定している
    en_mods = {"duration": en["duration"]["payload_bytes"],
               "acoustic": en["acoustic"]["payload_bytes"],
               "decoder": en["decoder"]["payload_bytes"]}
    out["split_hypothesis"] = {
        "our_assumption": "blob1 = Dα + Aβ / blob2 = Gγ",
        "per_module_bytes": en_mods,
        "paper_blob1_minus_our_blob1": PAPER["blob1"] - en["blob1_dur_plus_ac"],
        "paper_blob2_minus_our_blob2": PAPER["blob2"] - en["blob2_decoder"],
        "paper_total_minus_our_total": PAPER["total"] - en["total_payload"],
    }

    print(json.dumps(out, ensure_ascii=False, indent=1))
    with open("reports/e2_blob_padding.json", "w") as f:
        json.dump({**out, "repro": "uv run python reports/e2_blob_padding.py"}, f,
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
