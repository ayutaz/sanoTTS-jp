"""K-8 の前倒し: **辞書の枝刈りが音をどれだけ変えるか**を手元で測る。

⚠️ **これは実機を必要としない。** 端末が出す ids はホスト側の生徒モデルに
そのまま入れられるので、**「端末の ids から作った音」と「ホストの ids から
作った音」を直接比べられる**。実機が要るのは**速度**だけ。

D-044（動作点 438,750）は **「音素の 0.32%」だけ**で決めた。これはその根拠を
音の側から確かめるためのもの。

    uv run --extra eval python scripts/k1/k8_audio_gap.py \\
        --ids <k7_test --dump-ids の出力> --ckpt runs/v3/stage4.pt \\
        --out reports/k8_audio_gap.json [--wav-dir reports/k8_listen]

⚠️ **レポートに本文は書かない**（コーパス本文をコミットしないため）。
索引と数値だけ。`--wav-dir` を指定したときの WAV も**索引で命名**する。

測るもの:

| 指標 | 何を見るか |
|---|---|
| SCOREQ synthetic/nr | 端末の音とホストの音を**それぞれ**採点し、対応ありで比べる |
| 波形の相関 / SNR | ids が同じ文では**完全一致するはず**（陽性対照になる） |
| ids が違う文だけの部分集合 | ⚠️ **全体で薄まるので必ず分けて出す** |

⚠️ **SCOREQ は較正されていない**（D-020）。**絶対値ではなく差だけ**を見る。
⚠️ **これは聴取ではない。** 人が聴くのは K-8 の G32。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

SR = 22050


def read_ids(path: pathlib.Path):
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        p = ln.split("\t")
        if len(p) != 3:
            continue
        idx = int(p[0])
        dev = [int(x) for x in p[1].split()]
        host = [int(x) for x in p[2].split()]
        out.append((idx, dev, host))
    return out


def wav_write(path: pathlib.Path, pcm: np.ndarray) -> None:
    import soundfile as sf
    sf.write(str(path), pcm.astype(np.float32), SR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--ckpt", default=str(ROOT / "runs/v3/stage4.pt"))
    ap.add_argument("--out", default=str(ROOT / "reports/k8_audio_gap.json"))
    ap.add_argument("--wav-dir", default=None,
                    help="差が大きい文の WAV を書き出す（聴取セット）")
    ap.add_argument("--wav-top", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--skip-scoreq", action="store_true")
    a = ap.parse_args()

    import torch
    from synthesize_student import load_student, synthesize   # noqa: E402

    rows = read_ids(pathlib.Path(a.ids))
    if a.limit:
        rows = rows[:a.limit]
    dev = a.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    # ⚠️ **4 要素を返す**（4 番目は ckpt の dict）。3 要素だと思って渡すと落ちる。
    *models, _ck = load_student(a.ckpt, dev)
    print(f"{len(rows)} 文 / device={dev} / ckpt={a.ckpt}")

    same_ids, diff_ids = [], []
    recs = []
    for k, (idx, d, h) in enumerate(rows):
        pcm_d, _ = synthesize(models, np.asarray(d), dev)
        pcm_h, _ = synthesize(models, np.asarray(h), dev)
        pcm_d = np.asarray(pcm_d, dtype=np.float64).reshape(-1)
        pcm_h = np.asarray(pcm_h, dtype=np.float64).reshape(-1)
        n = min(len(pcm_d), len(pcm_h))
        # ⚠️ **長さが違う文がある**（ids が違えば frame 数も変わる）。
        #    切り詰めて相関を取るのは意味が薄いので、長さ差も記録する。
        if n > 0:
            a1, b1 = pcm_d[:n], pcm_h[:n]
            denom = float(np.sum(b1 * b1))
            snr = (10.0 * np.log10(denom / max(float(np.sum((a1 - b1) ** 2)), 1e-30))
                   if denom > 0 else float("nan"))
            r = float(np.corrcoef(a1, b1)[0, 1]) if np.std(a1) > 0 and np.std(b1) > 0 else float("nan")
        else:
            snr, r = float("nan"), float("nan")
        rec = {"index": idx, "ids_same": d == h,
               "n_dev": len(pcm_d), "n_host": len(pcm_h),
               "len_ratio": (len(pcm_d) / len(pcm_h)) if len(pcm_h) else None,
               "snr_db": snr, "pearson": r}
        recs.append(rec)
        (same_ids if d == h else diff_ids).append(rec)
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{len(rows)}")

    print(f"\nids が同じ文 {len(same_ids)} / 違う文 {len(diff_ids)}")

    # --- 陽性対照: ids が同じなら音は完全一致するはず -----------------------
    ok_same = all(r["snr_db"] > 80 or np.isnan(r["snr_db"]) for r in same_ids)
    if same_ids:
        s = [r["snr_db"] for r in same_ids if np.isfinite(r["snr_db"])]
        print(f"  {'OK ' if ok_same else 'NG '} ids が同じ文の SNR: "
              f"最小 {min(s):.1f} dB / 中央 {float(np.median(s)):.1f} dB "
              f"（**完全一致なら inf**）")
    if diff_ids:
        s = [r["snr_db"] for r in diff_ids if np.isfinite(r["snr_db"])]
        lr = [r["len_ratio"] for r in diff_ids if r["len_ratio"]]
        print(f"  ids が違う文の SNR: 中央 {float(np.median(s)):.2f} dB "
              f"/ 最小 {min(s):.2f} / 最大 {max(s):.2f}")
        print(f"  同・長さ比 (端末/ホスト): 中央 {float(np.median(lr)):.4f} "
              f"/ 最小 {min(lr):.4f} / 最大 {max(lr):.4f}")

    out = {"n": len(recs), "ckpt": a.ckpt, "sr": SR,
           "n_ids_same": len(same_ids), "n_ids_diff": len(diff_ids),
           "records": recs}

    # --- SCOREQ（対応あり）-------------------------------------------------
    if not a.skip_scoreq:
        try:
            from saanotts_jp.scoreq_metric import score_files  # noqa: E402
            have = True
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ SCOREQ を読めない（{type(e).__name__}）。skip する")
            have = False
        if have:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                td = pathlib.Path(td)
                fd, fh = [], []
                for k, (idx, d, h) in enumerate(rows):
                    pd_, _ = synthesize(models, np.asarray(d), dev)
                    ph_, _ = synthesize(models, np.asarray(h), dev)
                    p1 = td / f"dev_{idx:05d}.wav"; wav_write(p1, np.asarray(pd_).reshape(-1))
                    p2 = td / f"host_{idx:05d}.wav"; wav_write(p2, np.asarray(ph_).reshape(-1))
                    fd.append(str(p1)); fh.append(str(p2))
                # ⚠️ **`score_files` は `{path: score}` の dict を返す。**
                #    そのまま np.asarray すると dict のまま渡って落ちる。
                #    **順序を保つために元のリストで引き直す。**
                md, mh = score_files(fd), score_files(fh)
                sd = np.asarray([md[x] for x in fd], dtype=np.float64)
                sh = np.asarray([mh[x] for x in fh], dtype=np.float64)
                diff = sd - sh
                idx_diff = [i for i, r in enumerate(recs) if not r["ids_same"]]
                print(f"\n  SCOREQ 端末 {sd.mean():.4f} / ホスト {sh.mean():.4f} "
                      f"/ 差 {diff.mean():+.4f}")
                if idx_diff:
                    dd = diff[idx_diff]
                    print(f"  ids が違う {len(idx_diff)} 文だけ: 差 {dd.mean():+.4f} "
                          f"[{np.percentile(dd, 2.5):+.4f}, {np.percentile(dd, 97.5):+.4f}]")
                out["scoreq"] = {"dev_mean": float(sd.mean()),
                                 "host_mean": float(sh.mean()),
                                 "diff_mean": float(diff.mean()),
                                 "diff_mean_on_differing": float(diff[idx_diff].mean())
                                 if idx_diff else None}

    # --- 聴取セット --------------------------------------------------------
    if a.wav_dir:
        wd = pathlib.Path(a.wav_dir); wd.mkdir(parents=True, exist_ok=True)
        worst = sorted([r for r in recs if not r["ids_same"]],
                       key=lambda r: (r["snr_db"] if np.isfinite(r["snr_db"]) else -1e9))
        picked = worst[:a.wav_top]
        by_idx = {i: (d, h) for i, d, h in rows}
        for r in picked:
            d, h = by_idx[r["index"]]
            pd_, _ = synthesize(models, np.asarray(d), dev)
            ph_, _ = synthesize(models, np.asarray(h), dev)
            wav_write(wd / f"{r['index']:05d}_device.wav", np.asarray(pd_).reshape(-1))
            wav_write(wd / f"{r['index']:05d}_host.wav", np.asarray(ph_).reshape(-1))
        (wd / "README.md").write_text(
            "# 聴取セット（K-8 の G32 用）\n\n"
            f"`<索引>_device.wav` が端末の辞書（438,750 entries）で、"
            f"`<索引>_host.wav` がフル辞書。\n"
            f"**SNR が低い順に {len(picked)} 組**選んである（= 最も違う文）。\n\n"
            "⚠️ **本文は入れていない**（コーパス本文をコミットしないため）。\n"
            "索引は `--ids` に渡した TSV の 1 列目と対応する。\n\n"
            "聴くときの問い: **どちらが自然か言えるか。言えないなら差は可聴域に無い。**\n",
            encoding="utf-8")
        print(f"\n  聴取セットを {wd} に書いた（{len(picked)} 組）")
        out["wav_dir"] = str(wd)
        out["wav_indices"] = [r["index"] for r in picked]

    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"\n書き出した → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
