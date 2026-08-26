#!/usr/bin/env python3
"""一次差分判別器の性質テスト。

**論文が指定しているのは「Δy に判別器を掛ける」の 1 点だけ**なので、
検証できるのは「論文の 1 点を実際に守っているか」と「損失に嵌るか」と
「判別器として健全か（学習すれば本物と雑音を分けられるか）」の 3 つ。
構造そのものの正しさは論文と照合できない（`discriminator.py` の A1〜A9 参照）。

実行:
    uv run python scripts/test_discriminator.py
"""

from __future__ import annotations

import sys
import time

import numpy as np
import torch

sys.path.insert(0, "src")
from saanotts_jp.discriminator import (  # noqa: E402
    LARGE, SMALL, FirstDifferenceDiscriminator, build, count_parameters,
)
from saanotts_jp.labelpack import PackReader  # noqa: E402
from saanotts_jp.losses import discriminator_loss, generator_loss  # noqa: E402

torch.manual_seed(20260827)
np.random.seed(20260827)
OK, NG = "OK ", "NG!"
fails = 0

SEG = 8192          # train_student.SEGMENT_SAMPLES と同じ 32 frames * 256
# 立ち上がりの実測: 60 step では 3 種の norm すべてで gap < 0.03 にしかならない。
D_PROBE_STEPS = 200
D_PROBE_LR = 1e-3
PACK = "data/pack_sibdense"


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    fails += not cond
    print(f"  {OK if cond else NG} {name}" + (f"  {detail}" if detail else ""))


def real_segments(n: int, offset: int = 0, seed: int = 0) -> torch.Tensor:
    """教師パックの `yT` から固定長セグメントを切り出す。**合成波形ではない。**"""
    pack = PackReader(PACK)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(pack))[offset:offset + n]
    out = []
    for i in idx:
        y = pack[int(i)]["yT"]
        s = int(rng.integers(0, len(y) - SEG))
        out.append(y[s:s + SEG])
    return torch.from_numpy(np.stack(out)).float()


def rms_matched_noise(real: torch.Tensor) -> torch.Tensor:
    """real と同じ RMS の白色雑音。**振幅だけでは分離できない fake** にする。"""
    n = torch.randn_like(real)
    return n * (real.pow(2).mean(-1, keepdim=True).sqrt()
                / n.pow(2).mean(-1, keepdim=True).sqrt())


def main() -> int:
    print("=== インターフェース適合（losses.py にそのまま嵌るか）===")
    disc = build("small").eval()
    y = real_segments(4, seed=1)
    y_hat = y + torch.randn_like(y) * 0.02
    d_fake, f_fake = disc(y_hat)
    d_real, f_real = disc(y)
    check("forward が (Tensor, list[Tensor]) を返す",
          torch.is_tensor(d_fake) and isinstance(f_fake, list) and torch.is_tensor(f_fake[0]),
          f"out={tuple(d_fake.shape)} feats={len(f_fake)}")
    check("feats の本数が real / fake で一致（zip strict=True）", len(f_fake) == len(f_real))
    check("feats の各要素の形が real / fake で一致",
          all(a.shape == b.shape for a, b in zip(f_fake, f_real)))
    lg, log = generator_loss(y_hat, y, d_fake, f_fake, f_real)
    check("generator_loss が adv と fm を出す",
          "gen/adv" in log and "gen/fm" in log and torch.isfinite(lg),
          f"adv={log['gen/adv']:.4f} fm={log['gen/fm']:.4f}")
    ld, logd = discriminator_loss(d_real, d_fake)
    check("discriminator_loss が有限", bool(torch.isfinite(ld)),
          f"real={logd['disc/real']:.4f} fake={logd['disc/fake']:.4f}")
    check("入力は [B,T] でも [B,1,T] でも同じ",
          torch.allclose(disc(y)[0], disc(y.unsqueeze(1))[0], atol=1e-6))

    print("\n=== 論文の唯一の指定: 一次差分を取っているか ===")
    # Δy[t] = y[t] - y[t-1] は DC 成分を消す。定数オフセットで出力が変わらなければ
    # 差分を取っている証拠になる。⚠️ spectral_norm は forward ごとに power iteration
    # でバッファを更新するので、決定性を得るために eval() が必須。
    disc.eval()
    with torch.no_grad():
        base_out, base_f = disc(y)
        dc_dev = 0.0
        for off in (0.5, -3.0):
            o, f = disc(y + off)
            dev = float((o - base_out).abs().max())
            dc_dev = max(dc_dev, dev)
            check(f"定数オフセット {off:+.1f} で出力が不変",
                  torch.allclose(o, base_out, atol=1e-4), f"max|Δ|={dev:.2e}")
            check(f"定数オフセット {off:+.1f} で中間特徴も不変",
                  all(torch.allclose(a, b, atol=1e-4) for a, b in zip(f, base_f)))
        # 逆に符号反転（DC ではない変化）には反応すること = 単に潰しているのではない
        sign_dev = float((disc(-y)[0] - base_out).abs().max())
    check("符号反転には反応する（定数以外を素通ししていない）",
          sign_dev > 1000 * max(dc_dev, 1e-12),
          f"符号反転 {sign_dev:.2e} / DC {dc_dev:.2e} = {sign_dev / max(dc_dev, 1e-12):.0f}x")
    dy = FirstDifferenceDiscriminator.first_difference(y)
    check("差分で長さが 1 減る（A8: 埋め戻さない）", dy.shape[-1] == SEG - 1,
          f"{SEG} -> {dy.shape[-1]}")
    check("first_difference が定義どおり",
          torch.allclose(dy[0, 0], y[0, 1:] - y[0, :-1], atol=0))

    print("\n=== feature matching が同一入力で 0 ===")
    with torch.no_grad():
        d_same, f_same = disc(y)
    _, log_same = generator_loss(y, y, d_same, f_same, [f.clone() for f in f_same])
    check("同一波形で fm = 0", log_same["gen/fm"].item() < 1e-7,
          f"fm={log_same['gen/fm'].item():.2e}")
    check("同一波形で l1 / stft も 0",
          log_same["gen/l1"].item() < 1e-9 and log_same["gen/stft"].item() < 1e-6)

    print("\n=== 判別器単体の健全性（D だけ更新して分離が進むか）===")
    # 生徒は一切更新しない。fake は RMS を real に合わせた白色雑音（振幅では分離できない）。
    # 学習していない初期状態では分離していなくてよい。**held-out 発話で測る。**
    torch.manual_seed(7)
    d2 = build("small")
    opt = torch.optim.AdamW(d2.parameters(), lr=D_PROBE_LR)
    tr_real = real_segments(24, offset=0, seed=11)
    ev_real = real_segments(24, offset=100, seed=12)      # 学習に使っていない発話
    ev_fake = rms_matched_noise(ev_real)

    def scores(model: torch.nn.Module) -> tuple[np.ndarray, np.ndarray]:
        """発話ごとの D 出力平均。[n], [n]"""
        model.eval()
        with torch.no_grad():
            r = model(ev_real)[0].mean(dim=1).numpy()
            f = model(ev_fake)[0].mean(dim=1).numpy()
        model.train()
        return r, f

    def wilson(k: int, n: int) -> tuple[float, float]:
        """正答率の Wilson 95% 信頼区間（n=48 と小さいので必ず併記する）。"""
        z, ph = 1.96, k / n
        den = 1 + z * z / n
        c = (ph + z * z / (2 * n)) / den
        h = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / den
        return max(0.0, c - h), min(1.0, c + h)

    def report(tag: str, r: np.ndarray, f: np.ndarray) -> float:
        n = len(r)
        k = int((r > 0.5).sum() + (f <= 0.5).sum())
        lo, hi = wilson(k, 2 * n)
        print(f"     {tag:>9}  D(real)={r.mean():+.3f}±{r.std():.3f}  "
              f"D(noise)={f.mean():+.3f}±{f.std():.3f}  gap={r.mean() - f.mean():+.3f}  "
              f"acc={k / (2 * n):.3f} [{lo:.2f},{hi:.2f}] (n={2 * n})")
        return float(r.mean() - f.mean())

    r0, f0 = scores(d2)
    gap0 = report("init", r0, f0)
    for _ in range(D_PROBE_STEPS):
        b = tr_real[torch.randint(0, len(tr_real), (8,))]
        loss, _ = discriminator_loss(d2(b)[0], d2(rms_matched_noise(b))[0])
        opt.zero_grad(); loss.backward(); opt.step()
    r1, f1 = scores(d2)
    gap1 = report(f"{D_PROBE_STEPS} step", r1, f1)
    check("held-out で real と noise の分離が進む", gap1 > gap0 + 0.30,
          f"gap {gap0:+.3f} -> {gap1:+.3f}")
    check("しきい値 0.5 が real / noise を分ける", r1.mean() > 0.5 > f1.mean(),
          f"real {r1.mean():+.3f} > 0.5 > noise {f1.mean():+.3f}")
    check("real 側が LSGAN のターゲット 1 に近づく",
          abs(r1.mean() - 1) < abs(r0.mean() - 1),
          f"|D(real)-1| {abs(r0.mean() - 1):.3f} -> {abs(r1.mean() - 1):.3f}")
    # ⚠️ これは判別器単体の健全性テストであって学習ではない。生徒は 1 度も更新していない。

    print("\n=== パラメータ数（判別器はデプロイされない = 567,008 の外）===")
    n_small, n_large = count_parameters(build("small")), count_parameters(build("large"))
    print(f"     small {n_small:,}  ({SMALL.name()})")
    print(f"     large {n_large:,}  ({LARGE.name()})")
    check("既定 (small) が 50,000〜150,000 に収まる", 50_000 <= n_small <= 150_000,
          f"{n_small:,}")
    check("既定 (small) が生徒 567,008 より小さい", n_small < 567_008,
          f"{n_small:,} / 567,008 = {n_small / 567_008:.2f}x")
    check("large は small より大きいが 1 M 未満", n_small < n_large < 1_000_000,
          f"{n_large:,}")
    check("scales / base_channels でスケールする",
          count_parameters(build("small").subs[0]) * SMALL.scales == n_small)

    print("\n=== スケールごとの寄与（A6: 連結による重み付け）===")
    per = build("small").eval().forward_scales(y)
    lens = [o[0].numel() // len(y) for o in per]
    tot = sum(lens)
    print("     " + "  ".join(f"scale{i}={n} ({n / tot:.1%})" for i, n in enumerate(lens)))
    check("最細スケールが支配的だが 70% 未満", lens[0] / tot < 0.70, f"{lens[0] / tot:.1%}")

    print()
    print("すべて期待通り" if fails == 0 else f"{fails} 件 NG")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
