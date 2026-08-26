#!/usr/bin/env python3
"""B-5 の続き: **論文の主指標 SCOREQ** で教師と実人間音声のベースラインを取る。

B-5 では SCOREQ が未導入で UTMOS だけだった（教師 1.748 / 実人間 2.305 / 比 0.758）。
`scoreq==1.0.1` を入れたので、**同じ 24 + 24 本**を SCOREQ で測り直す。

測る軸:

| 軸 | 値 | 意味 |
|---|---|---|
| `data_domain` | `synthetic` / `natural` | 学習データが違う別モデル。**両方測って比較する** |
| `mode` | `nr` / `ref` | `nr` は MOS 予測（**高いほど良い**）、`ref` は NMR との埋め込み L2 距離（**低いほど良い**） |

⚠️ **`data_domain="natural"` は「自然音声一般」ではない。**
`scoreq/scoreq.py:_init_onnx` が
`'telephone' if data_domain == 'natural' else 'synthetic'` で解決していて、
同梱 README の表によると学習セットは **NISQA TRAIN SIM**（符号化・背景雑音・
パケットロスの伝送劣化シミュレーション）。**伝送品質の推定器**であって
TTS の品質推定器ではない。数値は参考として出すが **主指標は synthetic/nr**。

⚠️ **較正**: synthetic モデルの学習セットは **VoiceMOS 22 Train Set**
（同チャレンジ main track = BVCC。英語）。UTMOS とまったく同じ較正問題を持つ。
**日本語の絶対値を論文の英語スコアと比較しない**（D-013）。

⚠️ **「24 対」は対応のあるペアではない。** 教師 24 文は `data/splits/corpus_heldout.tsv`
から選んだ文の合成、実人間 24 本は `VOICEACTRESS100_001..024` の録音で、**テキストが違う**。
したがって対応のある検定は使えず、比の CI も独立 2 標本の bootstrap で出す。

実行:
    uv run --extra eval python scripts/b5_scoreq_baseline.py
    uv run --extra eval python scripts/b5_scoreq_baseline.py --skip-ref   # ref モデル未 DL 時
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import resource
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from eval_metrics import (  # noqa: E402
    CALIBRATION_WARNING,
    cohens_d,
    corr_ci,
    mannwhitney,
    measure_scoreq,
    measure_utmos,
    pad_wav,
    ratio_ci,
    summarize,
    trim_wav,
    wav_seconds,
)

TEACHER_REPORT = pathlib.Path("reports/b5_teacher_baseline.json")
HUMAN_REPORT = pathlib.Path("reports/b5_human_control.json")
OUT = pathlib.Path("reports/b5_scoreq.json")

HUMAN_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/datasets--ayousanz--tsukuyomi-chan-ljspeech/"
    "snapshots/*/wavs/*.wav"
)
SCOREQ_CACHE = os.path.expanduser("~/.cache/scoreq/onnx-models")
PAD_SEC = 0.3  # 教師 wav に入っている前後パディング（b5_teacher_baseline.py）
SCRATCH = pathlib.Path(
    os.environ.get("SCRATCH_DIR", "/private/tmp/saanotts_b5_scoreq")
)

REPRO = (
    "uv run --extra eval python scripts/b5_scoreq_baseline.py"
    "   # 教師 wav は reports/b5_teacher_wav/（b5_teacher_baseline.py が生成）、"
    "実人間 wav は HF datasets--ayousanz--tsukuyomi-chan-ljspeech の 24 本"
)


def resolve_human() -> list[str]:
    """実人間音声 24 本を特定する。

    `reports/b5_human_control.json` は要約統計しか持っておらず、どの wav を測ったかが
    書かれていない。**`mean_sec` が完全一致することで同一集合だと確認する**。
    """
    paths = sorted(glob.glob(HUMAN_GLOB))
    if not paths:
        raise SystemExit(f"実人間 wav が見つからない: {HUMAN_GLOB}")
    secs = wav_seconds(paths)
    stored = json.loads(HUMAN_REPORT.read_text())
    assert len(paths) == stored["n"], f"n が違う: {len(paths)} != {stored['n']}"
    assert abs(np.mean(secs) - stored["mean_sec"]) < 1e-9, (
        f"mean_sec が違う: {np.mean(secs)} != {stored['mean_sec']} "
        "— b5_human_control.json とは別の wav 集合を掴んでいる"
    )
    return paths


# scoreq は data_domain='natural' を ONNX ファイル名 'telephone' に解決する
ONNX_DOMAIN = {"synthetic": "synthetic", "natural": "telephone"}


def ref_domains_available() -> list[str]:
    """ref モデルがローカルに落ちている data_domain を返す。

    Zenodo からの取得は 378 MB × 2 で、504 で落ちることがある。
    **落ちていない設定を黙って欠測にしない**ため、どれが測れたかを明示的に持ち回る。
    """
    return [d for d, f in ONNX_DOMAIN.items()
            if os.path.exists(f"{SCOREQ_CACHE}/fixed_nmr_{f}.onnx")]


def analysis_arrays(per_file, excluded, group, metric):
    """集計・相関に使う配列。**ref モードでは NMR 自身（距離 0）を落とす**。"""
    vals = per_file[group][metric]
    secs = per_file[group]["sec"]
    k = excluded.get(metric)
    if group == "human" and k is not None:
        vals = [v for i, v in enumerate(vals) if i != k]
        secs = [v for i, v in enumerate(secs) if i != k]
    return vals, secs


def main() -> int:
    t_start = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ref", action="store_true")
    args = ap.parse_args()

    teacher_manifest = json.loads(TEACHER_REPORT.read_text())
    t_utts = teacher_manifest["utterances"]
    t_paths = [u["wav"] for u in t_utts]
    t_secs = [u["sec"] for u in t_utts]  # パディング前の長さ
    t_utmos = [u["utmos"] for u in t_utts]

    h_paths = resolve_human()
    h_secs = wav_seconds(h_paths)
    stored_human = json.loads(HUMAN_REPORT.read_text())

    print(f"教師 (合成)  n={len(t_paths)}  平均 {np.mean(t_secs):.2f} 秒"
          f"（+ 前後 {PAD_SEC} 秒パディング）")
    print(f"実人間      n={len(h_paths)}  平均 {np.mean(h_secs):.2f} 秒"
          f"（パディング無し）  {pathlib.Path(h_paths[0]).parent}")

    # ---- 1. 実人間側の UTMOS を per-file で取り直す（相関に必要） -----------
    print("\nUTMOS を実人間 24 本で測り直す（b5_human_control.json は要約しか持たない）…")
    h_utmos = measure_utmos(h_paths)
    delta = abs(float(np.mean(h_utmos)) - stored_human["mean"])
    print(f"  mean {np.mean(h_utmos):.6f}  vs 保存値 {stored_human['mean']:.6f}"
          f"  |Δ| = {delta:.2e}")
    assert delta < 1e-3, "保存値と再現しない — 対象 wav か手順が違う"

    groups = {"teacher": t_paths, "human": h_paths}
    per_file: dict[str, dict[str, list[float]]] = {
        "teacher": {"sec": t_secs, "utmos": t_utmos},
        "human": {"sec": h_secs, "utmos": h_utmos},
    }

    # ---- 2. SCOREQ nr（synthetic / natural） -------------------------------
    print("\nSCOREQ nr（高いほど良い）")
    for domain in ("synthetic", "natural"):
        for g, paths in groups.items():
            vals = measure_scoreq(paths, domain=domain, mode="nr")
            per_file[g][f"scoreq_{domain}_nr"] = vals
            print(f"  {domain:<9} {g:<8} mean {np.mean(vals):.4f}")

    # ---- 3. SCOREQ ref（NMR との距離、低いほど良い） -----------------------
    # NMR は「内容が一致しないクリーン音声」なら何でも良い。話者・録音条件を揃えるため
    # 実人間の録音を固定参照にする。**参照自身は距離 0 になるので集計から除外する**。
    # 参照 1 本の当たり外れで結論が変わらないか、3 本で感度も見る。
    ref_note = None
    nmr_idx = [0, 8, 16]
    nmr_sensitivity: dict[str, dict] = {}
    excluded: dict[str, int] = {}
    ref_domains = [] if args.skip_ref else ref_domains_available()
    missing_ref = [d for d in ("synthetic", "natural") if d not in ref_domains]
    if not ref_domains:
        ref_note = ("ref モデル (fixed_nmr_*.onnx, 各 378 MB) が未取得のためスキップ。"
                    "--skip-ref を外して再実行すると自動ダウンロードされる")
        print(f"\nSCOREQ ref: スキップ（{ref_note}）")
    else:
        print("\nSCOREQ ref（NMR との埋め込み L2 距離。**低いほど良い**）")
        for domain in ref_domains:
            for j, k in enumerate(nmr_idx):
                nmr = h_paths[k]
                vals = {g: measure_scoreq(p, domain=domain, mode="ref", ref_path=nmr)
                        for g, p in groups.items()}
                h_ex = [v for i, v in enumerate(vals["human"]) if i != k]
                key = f"{domain}_nmr{k:02d}"
                nmr_sensitivity[key] = {
                    "nmr": nmr,
                    "teacher_mean": round(float(np.mean(vals["teacher"])), 4),
                    "human_mean_excl_self": round(float(np.mean(h_ex)), 4),
                    "ratio_teacher_over_human": round(
                        float(np.mean(vals["teacher"]) / np.mean(h_ex)), 4),
                    "self_distance": float(f"{vals['human'][k]:.3g}"),
                }
                print(f"  {domain:<9} NMR={pathlib.Path(nmr).stem:<20} "
                      f"教師 {np.mean(vals['teacher']):.4f}  "
                      f"実人間 {np.mean(h_ex):.4f}  "
                      f"比 {np.mean(vals['teacher']) / np.mean(h_ex):.3f}  "
                      f"自己距離 {vals['human'][k]:.2e}")
                if j == 0:  # 主系列は NMR #0
                    per_file["teacher"][f"scoreq_{domain}_ref"] = vals["teacher"]
                    per_file["human"][f"scoreq_{domain}_ref"] = vals["human"]
                    excluded[f"scoreq_{domain}_ref"] = k
        ref_note = (
            f"主系列の NMR = {h_paths[0]}（実人間 1 本目を固定）。"
            "参照自身は距離 0 になるので human 側の集計・相関から除外し n=23 とした。"
            "NMR を 3 本振った感度は scoreq_ref_nmr_sensitivity を参照。"
            + (f" 未測定の ref ドメイン: {missing_ref}"
               "（Zenodo から fixed_nmr_telephone.onnx が HTTP 504 で取得できず）"
               if missing_ref else ""))

    # ---- 4. パディングの交絡を測る -----------------------------------------
    # 教師 wav には前後 0.3 秒の無音が入っているが実人間 wav には入っていない。
    # UTMOS / SCOREQ が端をどう扱うかで比がずれる可能性があるので実測する。
    print("\nパディングの交絡を実測（教師のみ 0.3 秒パディング済みという非対称）")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    h_pad = [pad_wav(p, SCRATCH / f"hpad_{i:02d}.wav", PAD_SEC)
             for i, p in enumerate(h_paths)]
    t_trim = [trim_wav(p, SCRATCH / f"ttrim_{i:02d}.wav", PAD_SEC)
              for i, p in enumerate(t_paths)]
    padding = {
        "human_padded": {"utmos": measure_utmos(h_pad),
                         "scoreq_synthetic_nr": measure_scoreq(h_pad)},
        "teacher_trimmed": {"utmos": measure_utmos(t_trim),
                            "scoreq_synthetic_nr": measure_scoreq(t_trim)},
    }
    for k, d in padding.items():
        base = "human" if k.startswith("human") else "teacher"
        for m, v in d.items():
            b = np.mean(per_file[base][m])
            print(f"  {k:<16} {m:<20} {np.mean(v):.4f}  (元 {b:.4f}, "
                  f"Δ {np.mean(v) - b:+.4f})")

    # ---- 5. 集計 ------------------------------------------------------------
    metrics = [k for k in per_file["teacher"] if k != "sec"]
    summary: dict[str, dict] = {}
    ratios: dict[str, dict] = {}
    for m in metrics:
        t, _ = analysis_arrays(per_file, excluded, "teacher", m)
        h, _ = analysis_arrays(per_file, excluded, "human", m)
        summary[m] = {"teacher": summarize(t, m), "human": summarize(h, m)}
        ratios[m] = ratio_ci(t, h)
        ratios[m]["higher_is_better"] = not m.endswith("_ref")
        summary[m]["mannwhitney_teacher_vs_human"] = mannwhitney(t, h)
        # どちらの指標が教師と実人間をよく分離するか（|d| が大きいほど敏感）
        summary[m]["cohens_d_human_minus_teacher"] = cohens_d(h, t)

    # ---- 6. UTMOS と SCOREQ の相関 -----------------------------------------
    # pooled は「群のオフセットごと」見るので、群内相関と食い違うことがある
    # （Simpson 的な反転）。両方出さないと指標の性格を見誤る。
    corr: dict[str, dict] = {}
    for m in metrics:
        if m == "utmos":
            continue
        t_v, t_s = analysis_arrays(per_file, excluded, "teacher", m)
        h_v, h_s = analysis_arrays(per_file, excluded, "human", m)
        t_u, _ = analysis_arrays(per_file, excluded, "teacher", "utmos")
        h_u_all = per_file["human"]["utmos"]
        k = excluded.get(m)
        h_u = [v for i, v in enumerate(h_u_all) if i != k] if k is not None \
            else h_u_all
        corr[f"pooled_utmos_vs_{m}"] = corr_ci(t_u + h_u, t_v + h_v)
        corr[f"teacher_utmos_vs_{m}"] = corr_ci(t_u, t_v)
        corr[f"human_utmos_vs_{m}"] = corr_ci(h_u, h_v)
        corr[f"teacher_sec_vs_{m}"] = corr_ci(t_s, t_v)
        corr[f"human_sec_vs_{m}"] = corr_ci(h_s, h_v)
    for g in ("teacher", "human"):
        corr[f"{g}_sec_vs_utmos"] = corr_ci(per_file[g]["sec"],
                                            per_file[g]["utmos"])

    # ---- 出力 ---------------------------------------------------------------
    print("\n" + "=" * 74)
    print(f"{'metric':<24} {'教師':>9} {'実人間':>9} {'比':>7}  {'比の95%CI':>18}")
    for m in metrics:
        r = ratios[m]
        print(f"{m:<24} {summary[m]['teacher']['mean']:>9.4f} "
              f"{summary[m]['human']['mean']:>9.4f} {r['ratio']:>7.3f}"
              f"  [{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}]"
              f"  {'↑' if r['higher_is_better'] else '↓ 距離'}")
    print("=" * 74)
    print("\nUTMOS と SCOREQ の相関（pooled = 教師 + 実人間。ref は NMR 自身を除くので n=47）")
    for k, v in corr.items():
        if k.startswith("pooled_"):
            print(f"  {k:<40} r={v['pearson_r']:+.3f} "
                  f"CI[{v['pearson_ci95'][0]:+.3f},{v['pearson_ci95'][1]:+.3f}] "
                  f"ρ={v['spearman_rho']:+.3f} p={v['spearman_p']:.3g}")

    # 2 指標が「どの発話が悪いか」で一致するか（聴取トリアージ用）
    worst = {}
    for m in ("utmos", "scoreq_synthetic_nr"):
        order = np.argsort(per_file["teacher"][m])[:3]
        worst[m] = [{"idx": int(i), "value": round(per_file["teacher"][m][i], 4),
                     "sec": t_utts[i]["sec"], "text": t_utts[i]["text"],
                     "wav": t_utts[i]["wav"]} for i in order]
    print("\n教師で最低スコアの 3 文（聴取して原因を確認する）")
    for m, rows in worst.items():
        print(f"  {m}: " + ", ".join(f"t{r['idx']:02d}({r['value']:.2f})"
                                     for r in rows))

    out = {
        "task": "B-5 続き: SCOREQ ベースライン（教師 / 実人間）",
        "date": "2026-08-27",
        "sets": {
            "teacher": {"n": len(t_paths), "wavs": t_paths,
                        "source": "reports/b5_teacher_baseline.json",
                        "pad_sec": PAD_SEC},
            "human": {"n": len(h_paths), "wavs": h_paths,
                      "source": "HF datasets ayousanz/tsukuyomi-chan-ljspeech "
                                "(VOICEACTRESS100_001..024)",
                      "identified_by": "mean_sec が b5_human_control.json と完全一致 "
                                       f"({stored_human['mean_sec']})",
                      "pad_sec": 0.0},
            "paired": False,
            "paired_note": "教師と実人間はテキストが違う独立 2 標本。対応検定は使えない",
        },
        "per_file": per_file,
        "summary": summary,
        "teacher_over_human": ratios,
        "correlation": corr,
        "padding_confound": {
            "pad_sec": PAD_SEC,
            "means": {k: {m: round(float(np.mean(v)), 4) for m, v in d.items()}
                      for k, d in padding.items()},
            "ratio_both_padded": {
                m: ratio_ci(per_file["teacher"][m], padding["human_padded"][m])
                for m in ("utmos", "scoreq_synthetic_nr")},
            "ratio_both_unpadded": {
                m: ratio_ci(padding["teacher_trimmed"][m], per_file["human"][m])
                for m in ("utmos", "scoreq_synthetic_nr")},
            "note": ("教師 wav のみ前後 0.3 秒の無音が入っている非対称を実測した。"
                     "両方パディングあり／両方なしに揃えた比も出してある。"
                     "主表の比（教師=パディングあり / 実人間=なし）との差が"
                     "交絡の大きさ"),
        },
        "scoreq_ref_note": ref_note,
        "scoreq_ref_nmr_sensitivity": nmr_sensitivity,
        "scoreq_ref_excluded_index": excluded,
        "utmos_reproduced": {"human_mean_now": round(float(np.mean(h_utmos)), 6),
                             "human_mean_stored": stored_human["mean"],
                             "abs_delta": float(f"{delta:.3g}")},
        "worst_teacher_utterances": worst,
        "runtime_cost": {
            "wall_sec": round(time.time() - t_start, 1),
            "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "note": ("UTMOS + SCOREQ 4 モデルを 1 プロセスに常駐させた場合。"
                     "SCOREQ の ONNX は 1 本 378 MB で、4 設定すべて測ると 4 本載る。"
                     "生徒の評価を回すマシンのメモリ見積りに使うこと。"
                     "macOS では ru_maxrss はバイト単位"),
        },
        "pipeline_integration": {
            "new_script": "scripts/eval_metrics.py — 任意の wav 群に UTMOS と "
                          "SCOREQ 4 設定を並べて出す。統計ヘルパ (summarize / "
                          "ratio_ci / corr_ci / cohens_d / mannwhitney) も同居。"
                          "生徒の評価はこれを使う",
            "existing_script_untouched": "scripts/b5_measure_mos.py は変更していない",
            "existing_script_now_broken": (
                "⚠️ ただし scoreq を入れたことで scripts/b5_measure_mos.py:measure_scoreq "
                "は ImportError('TorchCodec is required for load_with_torchcodec') で"
                "落ちるようになった。scoreq 内部の torchaudio.load を呼ぶ前に "
                "src/saanotts_jp/scoreq_metric.py の soundfile shim を通していないため。"
                "以前は ImportError で None を返してスキップしていたので気づかない。"
                "UTMOS 計測の後・JSON 書き出しの前で落ちるので "
                "reports/b5_teacher_baseline.json は壊れないが、docs/measurements.md "
                "M-10 の再現コマンドは現状こける。"
                "修正は measure_scoreq の中身を "
                "`from saanotts_jp.scoreq_metric import score_files` 経由にする 1 箇所"),
        },
        "calibration_warning": CALIBRATION_WARNING,
        "natural_domain_warning": (
            "SCOREQ の data_domain='natural' は NISQA TRAIN SIM（符号化・背景雑音・"
            "パケットロスの伝送劣化シミュレーション）で学習したモデルで、ONNX 名も "
            "adapt_nr_telephone.onnx / fixed_nmr_telephone.onnx。伝送品質の推定器であり "
            "TTS の品質評価用ではない。主指標は synthetic/nr を使うこと"),
        "repro": REPRO,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n→ {OUT}")
    print(f"\n⚠️ {CALIBRATION_WARNING}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
