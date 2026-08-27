#!/usr/bin/env python3
"""CER（文字誤り率）を測る。**日本語では WER ではなく CER**（分かち書きが問題になる）。

`eval_student.py` が出した教師 / 生徒の wav を Whisper で書き起こし、
元テキストとの CER を出す。**教師比で報告する**（Whisper 自体の誤りが乗るため、
生徒の絶対 CER には意味が薄い）。

⚠️ **正規化を先に決める。** 日本語の書き起こしは表記ゆれが大きい
（漢数字/算用数字、送り仮名、句読点）。ここでは NFKC + 記号除去 +
**カタカナ→ひらがな**まで寄せる。数字の読み下しはしない（誤りを過小評価しうるため、
`--strict` で無効化できる）。

実行:
    uv run --extra eval python scripts/measure_cer.py --eval-dir reports/eval_v2
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata
import warnings

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, "src")

#: 除去する記号（句読点・約物）。読みに影響しない
_PUNCT = re.compile(r"[、。，．・「」『』（）()【】〜～\-—…!！?？:：;；\"'\s]")


def normalize(text: str, strict: bool = False) -> str:
    """表記ベースの正規化。**何を寄せたかを report に必ず書く。**

    ⚠️ **これだけでは日本語 TTS の評価にならない。** Whisper は同じ音を
    漢字でもひらがなでも書き起こすので、参照が漢字だと**正しく読めていても
    CER が跳ね上がる**（実測: 教師の「和歌山県」→「わけわけん」で CER 1.286）。
    主指標は下の `to_kana()` を通した**かな CER** にする。
    """
    t = unicodedata.normalize("NFKC", text)
    t = _PUNCT.sub("", t)
    if strict:
        return t
    # カタカナ → ひらがな（Whisper はカタカナ語の表記が揺れる）
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)


_KANA_TABLE = None


def to_kana(text: str) -> str | None:
    """テキスト → 読み（かな中間表現からアクセント記号を落としたもの）。

    **参照と仮説の両方を同じ経路に通す。** 表記の違い（漢字 / ひらがな /
    カタカナ）が消え、**読みの誤りだけが残る**。これが日本語 TTS の CER の
    測り方として正しい。

    G2P が失敗したら `None`（Whisper が記号列や外国語を出したとき）。
    """
    global _KANA_TABLE
    import kana_g2p as K  # noqa: PLC0415

    if _KANA_TABLE is None:
        _KANA_TABLE = K.build_mora_table()
    try:
        toks = K.text_to_intermediate(text, _KANA_TABLE)
    except Exception:                              # noqa: BLE001
        return None
    marks = {"[", "]", "#"}
    return "".join(t.replace("°", "") for t in toks if t not in marks)


def cer(ref: str, hyp: str) -> float:
    """レーベンシュタイン距離 / 参照長。jiwer を使わず自前（文字単位で明示するため）。"""
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", required=True, help="eval_student.py の出力")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--strict", action="store_true", help="カタカナ→ひらがなをしない")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = pathlib.Path(args.eval_dir)
    ev = json.load(open(root / "eval.json"))
    from faster_whisper import WhisperModel

    print(f"Whisper {args.model} を読み込み中…")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    def transcribe(path: str) -> str:
        segs, _ = model.transcribe(path, language="ja", beam_size=5,
                                   condition_on_previous_text=False)
        return "".join(s.text for s in segs)

    sys.path.insert(0, "scripts")
    rows, n_kana_fail = [], 0
    for u in ev["utterances"]:
        ref_s = normalize(u["text"], args.strict)
        ref_k = to_kana(u["text"])
        out = {"uid": u["uid"], "ref": u["text"], "ref_kana": ref_k}
        for who in ("teacher", "student"):
            wav = root / who / f"{u['uid']}.wav"
            hyp_raw = transcribe(str(wav))
            out[f"{who}_hyp"] = hyp_raw.strip()
            out[f"{who}_cer_surface"] = cer(ref_s, normalize(hyp_raw, args.strict))
            hyp_k = to_kana(hyp_raw.strip())
            out[f"{who}_hyp_kana"] = hyp_k
            if ref_k is None or hyp_k is None:
                n_kana_fail += 1
                out[f"{who}_cer"] = None
            else:
                out[f"{who}_cer"] = cer(ref_k, hyp_k)
        rows.append(out)
        tk = out["teacher_cer"]; sk = out["student_cer"]
        print(f"  {u['uid']:<26} かな: 教師 "
              f"{'--' if tk is None else f'{tk:.3f}'} / 生徒 "
              f"{'--' if sk is None else f'{sk:.3f}'}"
              f"   （表記: {out['teacher_cer_surface']:.3f} / "
              f"{out['student_cer_surface']:.3f}）")

    ok = [r for r in rows if r["teacher_cer"] is not None and r["student_cer"] is not None]
    t = np.array([r["teacher_cer"] for r in ok])
    s = np.array([r["student_cer"] for r in ok])
    t_sf = np.array([r["teacher_cer_surface"] for r in rows])
    s_sf = np.array([r["student_cer_surface"] for r in rows])

    def stats(a):
        return {"n": int(a.size), "mean": float(a.mean()),
                "median": float(np.median(a)), "sd": float(a.std(ddof=1)),
                "max": float(a.max())}

    # 対応のある検定（同じ文の対なので paired）。⚠️ Welch を使わない（C-2 で踏んだ）
    from scipy import stats as sps  # noqa: PLC0415

    diff = s - t
    tt = sps.ttest_rel(s, t)
    try:
        wx = sps.wilcoxon(s, t)
        wx_p = float(wx.pvalue)
    except ValueError:
        wx_p = None
    boot = np.random.default_rng(0).choice(
        diff, size=(20000, diff.size), replace=True).mean(axis=1)

    rep = {
        "eval_dir": args.eval_dir, "model": args.model, "n": len(rows),
        "paired_test": {
            "mean_diff_student_minus_teacher": float(diff.mean()),
            "ci95_bootstrap": [float(np.percentile(boot, 2.5)),
                               float(np.percentile(boot, 97.5))],
            "paired_t_p": float(tt.pvalue), "wilcoxon_p": wx_p,
            "note": "対応のある検定。同じ文の対なので Welch は使わない",
        },
        "primary": "かな CER（参照・仮説の両方を OpenJTalk で読みに落として比較）",
        "normalization_surface": ("NFKC + 記号除去"
                                  + ("" if args.strict else " + カタカナ→ひらがな")),
        "n_kana_g2p_failed": n_kana_fail,
        "teacher_cer": stats(t), "student_cer": stats(s),
        "delta_student_minus_teacher": float(s.mean() - t.mean()),
        "teacher_cer_surface": stats(t_sf), "student_cer_surface": stats(s_sf),
        "warnings": [
            "**主指標はかな CER。** 表記 CER は Whisper が同じ音を漢字でもひらがなでも "
            "書き起こすため、正しく読めていても跳ね上がる（実測で教師 1.286 の例あり）",
            "**Whisper 自体の誤りが両方に乗る**ので、生徒の絶対値ではなく教師との差で読む",
            f"n={len(rows)}。少数の失敗発話が平均を動かす（median も併記した）",
            "かな化に失敗した発話は集計から除外している（Whisper が記号列を出した場合）",
        ],
        "repro": f"uv run --extra eval python scripts/measure_cer.py --eval-dir {args.eval_dir}",
        "utterances": rows,
    }
    out = args.out or str(root / "cer.json")
    pathlib.Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=1))

    print(f"\n=== かな CER（主指標 / n={t.size}）===")
    print(f"  教師  mean {t.mean():.4f}  median {np.median(t):.4f}  max {t.max():.4f}")
    print(f"  生徒  mean {s.mean():.4f}  median {np.median(s):.4f}  max {s.max():.4f}")
    pt = rep["paired_test"]
    print(f"  差    {pt['mean_diff_student_minus_teacher']:+.4f} "
          f"CI95 [{pt['ci95_bootstrap'][0]:+.4f}, {pt['ci95_bootstrap'][1]:+.4f}]  "
          f"paired-t p={pt['paired_t_p']:.4f}"
          + (f" / Wilcoxon p={pt['wilcoxon_p']:.4f}" if pt['wilcoxon_p'] else ""))
    print(f"\n=== 表記 CER（参考 / n={len(rows)}）===")
    print(f"  教師  mean {t_sf.mean():.4f}  median {np.median(t_sf):.4f}  max {t_sf.max():.4f}")
    print(f"  生徒  mean {s_sf.mean():.4f}  median {np.median(s_sf):.4f}  max {s_sf.max():.4f}")
    print("  ⚠️ 表記 CER は Whisper の表記ゆれを拾う。判断には使わない")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
