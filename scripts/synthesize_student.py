#!/usr/bin/env python3
"""学習した生徒で音声を合成する（推論経路）。

デバイス上で走るのと同じ順序:

    テキスト ──[kana_g2p]──▶ 音素ID ──[map_ids]──▶ 生徒の埋め込み index
      ──▶ Dα ──▶ d̂ = clip_[1,80](round(s_v · r)) ──▶ Aβ ──▶ ĉ
      ──▶ [式7 摩擦音ノイズ注入] ──▶ Gγ ──▶ iSTFT ──▶ 22.05 kHz PCM

**教師は一切呼ばない。** ここで教師を呼ぶと評価が意味を失う。

実行:
    uv run python scripts/synthesize_student.py --ckpt runs/v1/stage4.pt \
        --texts data/splits/corpus_heldout.tsv --limit 24 --out reports/student_wav
    uv run python scripts/synthesize_student.py --ckpt runs/v1/stage4.pt \
        --text "今日は良い天気ですね。" --out /tmp/one

⚠️ **`--text` / `--texts`（漢字混じり文）は教師 ckpt を要求する。**
音素 ID を教師の `phoneme_id_map` 経由で組むため。教師は private リポジトリにあるので、
**リリースの重みだけを持っている人はこの経路を使えない**。

その場合は `--intermediate` で**かな中間表現を直接**渡す。**端末が受け取るのと同じ形**で、
教師 ckpt を 1 バイトも読まない:

    uv run python scripts/synthesize_student.py --ckpt saanotts-jp-v3-stage4.pt \
        --intermediate "きょ][おわよ][いて][んきです°ね" --out /tmp/one

⚠️ **どちらの経路も同じ ids を作る**（生徒インデックスまで一致することを確認済み）。
漢字文から中間表現を作るのは `scripts/to_intermediate.py`。
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration  # noqa: E402
from saanotts_jp.losses import S_JA, inject_fricative_noise  # noqa: E402
from saanotts_jp.vocab import TOKENS, map_ids  # noqa: E402

SR = 22050
HOP = 256
S_V = 1.2187          # D-019。⚠️ 完璧な生徒の仮定で解いた値。学習後に解き直す
CLIP_LO, CLIP_HI = 1, 80

#: 式7 のノイズ注入対象を**生徒の埋め込み index** に落としたもの
FRICATIVE_IDX = frozenset(i for i, t in enumerate(TOKENS) if t in S_JA)


def load_student(path: str, device) -> tuple[Duration, Acoustic, Decoder, dict]:
    ck = torch.load(path, map_location=device, weights_only=False)
    need = ("duration", "acoustic", "decoder")
    missing = [n for n in need if n not in ck["state"]]
    if missing:
        raise SystemExit(
            f"{path} に {missing} がありません（stage={ck.get('stage')}）。\n"
            "デプロイ対象 3 つが揃うのは Stage 4 の ckpt だけです。")
    out = []
    for name, cls in zip(need, (Duration, Acoustic, Decoder), strict=True):
        m = cls().to(device).eval()
        m.load_state_dict(ck["state"][name])
        out.append(m)
    return (*out, ck)


@torch.no_grad()
def synthesize(models, ids_student: np.ndarray, device, s_v: float = S_V,
               beta: float = 0.0, sigma_c: torch.Tensor | None = None,
               generator: torch.Generator | None = None) -> tuple[np.ndarray, dict]:
    """生徒の埋め込み index 列 → PCM。"""
    duration, acoustic, decoder = models
    x = torch.from_numpy(np.asarray(ids_student)).long()[None].to(device)

    log_d = duration(x)                                   # 式2 の出力
    r = torch.exp(log_d)
    d_hat = torch.clamp(torch.round(s_v * r), CLIP_LO, CLIP_HI).long()

    c = acoustic(x, d_hat)                                # [1, 40, T]

    if beta > 0:
        if sigma_c is None:
            raise SystemExit("式7 のノイズ注入には c-line の σ が要ります（--ckpt の c_stats）")
        # フレームごとに「その音素が S_ja か」を作る。長さ展開と同じ規則で並べる
        flags = torch.tensor([1.0 if int(i) in FRICATIVE_IDX else 0.0
                              for i in ids_student], device=device)
        is_fric = torch.repeat_interleave(flags, d_hat[0])[None]   # [1, T]
        c = inject_fricative_noise(c, is_fric, sigma_c.to(device), beta,
                                   generator=generator)

    mag, cos, sin = decoder(c)
    pcm = Decoder.istft(mag, cos, sin)[0].cpu().numpy()
    frames = int(d_hat.sum())
    assert len(pcm) == frames * HOP, (len(pcm), frames * HOP)
    return pcm, {"n_ids": len(ids_student), "frames": frames,
                 "sec": frames * HOP / SR,
                 "d_hat_min": int(d_hat.min()), "d_hat_max": int(d_hat.max()),
                 "clipped_lo": int((torch.round(s_v * r) < CLIP_LO).sum()),
                 "clipped_hi": int((torch.round(s_v * r) > CLIP_HI).sum())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True, help="runs/<name>/stage4.pt")
    ap.add_argument("--texts", help="TSV (source, id, text)")
    ap.add_argument("--text", help="1 文だけ合成する（漢字可。⚠️ 教師 ckpt が要る）")
    ap.add_argument("--intermediate",
                    help="かな中間表現を直接渡す（例: きょ][おわよ...）。"
                         "**教師 ckpt が要らない唯一の経路**")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--s-v", type=float, default=S_V)
    ap.add_argument("--beta", type=float, default=0.0,
                    help="式7 のノイズ注入強度。**聴取で決める**（指標では決めない）")
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if not (args.texts or args.text or args.intermediate):
        raise SystemExit("--texts / --text / --intermediate のどれかが要ります")

    device = args.device or (
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available() else "cpu")
    *models, ck = load_student(args.ckpt, device)

    sigma_c = None
    if "c_stats" in ck:
        sigma_c = ck["c_stats"]["sigma"]
    elif args.beta > 0:
        raise SystemExit("ckpt に c_stats がありません。Stage 2 を回し直してください")

    import kana_g2p as K
    # ⚠️ **どちらのテーブルを使ったかを必ずログに出す。** 黙って切り替えると
    #    「なぜか端末と音が違う」を追えなくなる。
    #    `--intermediate` は**常に凍結テーブル**を使う（OpenJTalk を呼ばない = 誰でも走る）。
    #    漢字経路はどのみち OpenJTalk が要るので live を使う。
    table, which = K.mora_table(prefer_frozen=bool(args.intermediate))
    print(f"mora テーブル: {which}（{len(table)} 件）"
          + ("  ← csrc/g2p_table.json / sha256 検証済み" if which == "frozen" else ""))

    rows: list[tuple[str, str]] = []
    if args.intermediate:
        # --- 中間表現を直接受ける経路（**教師 ckpt を読まない**）-----------
        # ⚠️ 生徒インデックスは `gen_g2p_vectors.encode()` で作る。これは
        #    `csrc/g2p.c` の期待値を作っているのと**同じ関数**なので、
        #    端末が同じ 1 行から作る ids と一致することが `make -C csrc g2p` で
        #    毎回検査されている。教師の phoneme_id_map を経由する下の経路とも
        #    一致する（`scripts/gen_demo_ids.py` の錨がその突き合わせ）。
        from gen_g2p_vectors import encode as encode_student

        def to_student_ids(text: str) -> list[int]:
            info = encode_student(text, table)
            if info["kind"] != 0:
                raise KeyError(f"{info['err_byte']} バイト目が中間表現として読めない")
            return info["ids"]

        rows.append(("cli_000", args.intermediate))
    else:
        # --- 漢字混じり文の経路（⚠️ **教師 ckpt が要る**）-------------------
        import gen_teacher_labels as G
        G.ENCODE_TABLE = table
        try:
            pim = json.load(open(G.snapshot() + "config.json"))["phoneme_id_map"]
        except SystemExit:
            raise SystemExit(
                "教師 ckpt が HF キャッシュに無い（private リポジトリ）。\n"
                "  リリースの重みだけを持っている場合は、かな中間表現を直接渡すこと:\n"
                '    --intermediate "きょ][おわよ][いて][んきです°ね"\n'
                "  漢字文からの変換は scripts/to_intermediate.py（OpenJTalk を使う）"
            ) from None

        def to_student_ids(text: str) -> list[int]:
            return map_ids(G.encode_intermediate(K.text_to_intermediate(text, table), pim))

        if args.text:
            rows.append(("cli_000", args.text))
        else:
            excluded = G.load_exclusions()
            for r in csv.reader(open(args.texts), delimiter="\t"):
                if not r or not r[-1] or r[0] == "source":
                    continue
                if len(r) >= 3 and r[1] in excluded:
                    continue      # B-10: 教師の学習テキストは評価に使わない
                rows.append((r[1] if len(r) >= 3 else f"utt_{len(rows):04d}", r[-1]))
                if args.limit and len(rows) >= args.limit:
                    break

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    # generator は c と同じ device でないと torch.randn が受け取らない
    gen = torch.Generator(device=device).manual_seed(args.seed)
    index, skipped = [], []
    for uid, text in rows:
        try:
            ids_s = to_student_ids(text)
        except KeyError as exc:
            skipped.append({"uid": uid, "text": text, "why": str(exc)})
            continue
        pcm, meta = synthesize(models, ids_s, device, s_v=args.s_v,
                               beta=args.beta, sigma_c=sigma_c, generator=gen)
        path = outdir / f"{uid}.wav"
        sf.write(path, pcm.astype(np.float32), SR)
        index.append({"uid": uid, "text": text, "wav": str(path),
                      "peak": float(np.abs(pcm).max()), **meta})

    total_hi = sum(r["clipped_hi"] for r in index)
    total_lo = sum(r["clipped_lo"] for r in index)
    json.dump({
        "ckpt": args.ckpt, "stage": ck.get("stage"), "device": device,
        "s_v": args.s_v, "beta": args.beta, "n": len(index), "n_skipped": len(skipped),
        "total_sec": round(sum(r["sec"] for r in index), 2),
        "clip_lo_hits": total_lo, "clip_hi_hits": total_hi,
        "skipped": skipped[:50], "utterances": index,
        "repro": f"uv run python scripts/synthesize_student.py --ckpt {args.ckpt} --out {args.out}",
    }, open(outdir / "index.json", "w"), ensure_ascii=False, indent=1)

    print(f"{len(index)} 文 / {sum(r['sec'] for r in index):.1f} 秒 → {outdir}")
    print(f"  clip 下限 {total_lo} 回 / 上限 {total_hi} 回"
          + ("  ⚠️ 上限 80 に当たっている（教師では 0 回）" if total_hi else ""))
    if index:
        peaks = np.array([r["peak"] for r in index])
        print(f"  peak mean {peaks.mean():.4f} / max {peaks.max():.4f}"
              + ("  ⚠️ クリップしている" if peaks.max() >= 1.0 else ""))
    if skipped:
        print(f"  棄却 {len(skipped)} 件（{skipped[0]['why'][:40]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
