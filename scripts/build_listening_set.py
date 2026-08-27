#!/usr/bin/env python3
"""β の A/B 聴取セットを作る（Phase 5 のタイブレーク）。

計画書 §Phase 5 の設計をそのまま実装する:

* **摩擦音・無声化母音を高密度に含む 20 文**（held-out からランダムではなく、
  クラス密度で選ぶ）
* **順序と左右をランダム化**（どちらが β=0 か聴取者に分からないようにする）
* **同一刺激を 2 回반復**して内的一貫性を測る
* 回答表（CSV）と対応表（正解 JSON）を分けて出す

⚠️ **正解 JSON を先に見ないこと。** 見たら聴取が成立しない。

実行:
    uv run python scripts/build_listening_set.py --ckpt runs/v2/stage4.pt \
        --betas 0,2 --n 20 --out reports/listening_beta
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

SR = 22050
#: 密度を稼ぎたいクラス（式7 が触る音素が属する）
TARGET = ("fricative", "affricate", "devoiced")


def pick_dense(texts: str, n: int, seed: int) -> list[tuple[str, str]]:
    """S_ja が属するクラスの**密度**が高い文を選ぶ（長さで割って正規化）。"""
    import gen_teacher_labels as G
    import kana_g2p as K
    from saanotts_jp import flatness as FL
    from saanotts_jp.vocab import TOKENS, map_ids

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
    excluded = G.load_exclusions()

    cands = []
    for r in csv.reader(open(texts), delimiter="\t"):
        if not r or not r[-1] or r[0] == "source" or (len(r) >= 3 and r[1] in excluded):
            continue
        try:
            ids = map_ids(G.encode_intermediate(K.text_to_intermediate(r[-1], table), pim))
        except KeyError:
            continue
        if not (60 <= len(ids) <= 200):        # 聴取に向く長さ（約 1.5〜5 秒）
            continue
        hit = sum(1 for i in ids if FL.TOK2CLASS.get(TOKENS[int(i)]) in TARGET)
        cands.append({"uid": r[1], "text": r[-1], "n_ids": len(ids),
                      "n_target": hit, "density": hit / len(ids)})
    cands.sort(key=lambda c: -c["density"])
    top = cands[: n * 3]                        # 上位から seed でばらす（偏り防止）
    rng = np.random.default_rng(seed)
    sel = [top[i] for i in rng.choice(len(top), min(n, len(top)), replace=False)]
    return sel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--betas", default="0,2")
    ap.add_argument("--texts", default="data/splits/corpus_heldout.tsv")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--repeats", type=int, default=2, help="内的一貫性のための反復")
    ap.add_argument("--out", default="reports/listening_beta")
    ap.add_argument("--seed", type=int, default=20260827)
    args = ap.parse_args()

    betas = [float(b) for b in args.betas.split(",")]
    if len(betas) != 2:
        raise SystemExit("A/B なので β は 2 つ指定してください")

    import gen_teacher_labels as G
    import kana_g2p as K
    import synthesize_student as SS
    from saanotts_jp.vocab import map_ids

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    out = pathlib.Path(args.out)
    (out / "pairs").mkdir(parents=True, exist_ok=True)

    sel = pick_dense(args.texts, args.n, args.seed)
    print(f"{len(sel)} 文を選んだ（{'/'.join(TARGET)} の密度上位）")
    print(f"  密度 mean {np.mean([c['density'] for c in sel]):.3f} / "
          f"min {min(c['density'] for c in sel):.3f}")

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
    *models, ck = SS.load_student(args.ckpt, device)
    sigma_c = ck.get("c_stats", {}).get("sigma") if isinstance(ck.get("c_stats"), dict) else None
    gen = torch.Generator(device=device).manual_seed(args.seed)

    # β ごとに合成（同じ文・同じ seed）
    wavs: dict[float, dict[str, pathlib.Path]] = {b: {} for b in betas}
    tmp = out / "_by_beta"
    for b in betas:
        (tmp / f"beta{b:g}").mkdir(parents=True, exist_ok=True)
        for c in sel:
            ids = map_ids(G.encode_intermediate(
                K.text_to_intermediate(c["text"], table), pim))
            pcm, _ = SS.synthesize(models, ids, device, beta=b,
                                   sigma_c=sigma_c, generator=gen)
            p = tmp / f"beta{b:g}" / f"{c['uid']}.wav"
            sf.write(p, pcm.astype(np.float32), SR)
            wavs[b][c["uid"]] = p

    # --- 試行を組む: 順序ランダム / 左右ランダム / repeats 回反復 ---
    rng = np.random.default_rng(args.seed + 1)
    trials = []
    for rep in range(args.repeats):
        order = rng.permutation(len(sel))
        for k in order:
            c = sel[int(k)]
            flip = bool(rng.integers(0, 2))
            a_beta, b_beta = (betas[1], betas[0]) if flip else (betas[0], betas[1])
            tid = f"T{len(trials):03d}"
            for side, bb in (("A", a_beta), ("B", b_beta)):
                shutil.copy(wavs[bb][c["uid"]], out / "pairs" / f"{tid}_{side}.wav")
            trials.append({"trial": tid, "uid": c["uid"], "text": c["text"],
                           "repeat": rep, "A_beta": a_beta, "B_beta": b_beta,
                           "density": c["density"]})

    # 回答表（聴取者が埋める）。**正解は書かない**
    with open(out / "answer_sheet.csv", "w") as f:
        w = csv.writer(f)
        w.writerow(["trial", "好ましいほう(A/B/同じ)", "自信(1-5)", "メモ"])
        for t in trials:
            w.writerow([t["trial"], "", "", ""])

    (out / "key.json").write_text(json.dumps({
        "ckpt": args.ckpt, "betas": betas, "n_sentences": len(sel),
        "repeats": args.repeats, "n_trials": len(trials), "seed": args.seed,
        "design": "A/B 強制選択。順序ランダム / 左右ランダム / 同一刺激を "
                  f"{args.repeats} 回反復（内的一貫性の確認用）",
        "how_to_score": "各 β の選好数を数える。二項 95%CI が 0.5 を跨いだら "
                        "**β を小さいほうに倒す**（計画書 §Phase 5）。"
                        "反復間で答えが割れた試行の割合が内的一貫性",
        "trials": trials,
    }, ensure_ascii=False, indent=1))

    (out / "README.md").write_text(f"""# β の A/B 聴取（{len(trials)} 試行）

`pairs/T000_A.wav` と `pairs/T000_B.wav` を聴き比べて、
**好ましいほう**を `answer_sheet.csv` に記入してください。

- 聴きどころ: **サ行・シャ行・ツ・チ・「です」「ます」「した」の無声化**が
  自然か、シューという笛のような音（whistly）になっていないか
- どちらとも言えなければ「同じ」で構いません
- **同じ文が {args.repeats} 回出てきます**（順序と左右はランダム）。
  同じ答えになるかで一貫性を測るので、前の答えは気にせず素直に選んでください
- ⚠️ `key.json` は**先に見ないでください**（どちらが β=0 か書いてあります）

採点: `uv run python scripts/score_listening.py --dir {args.out}`
""")

    print(f"\n{len(trials)} 試行 / {len(sel)} 文 × {args.repeats} 反復")
    print(f"→ {out}/README.md を読んで {out}/answer_sheet.csv を埋めてください")
    print(f"   ⚠️ {out}/key.json は先に見ないこと")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
