#!/usr/bin/env python3
"""損失関数の性質テスト。

**数値が出ることと正しいことは別。** 損失は「最小値がどこか」「何に反応するか」
という性質で検証する。

実行:
    uv run python scripts/test_losses.py
"""

from __future__ import annotations

import sys

import torch

sys.path.insert(0, "src")
from saanotts_jp.losses import (  # noqa: E402
    ChannelStats, S_JA, discriminator_loss, duration_loss, generator_loss,
    inject_fricative_noise, joint_loss, latent_loss, multi_resolution_stft_loss,
)

torch.manual_seed(20260827)
OK, NG = "OK ", "NG!"
fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    fails += not cond
    print(f"  {OK if cond else NG} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("=== 式2 duration ===")
    d = torch.rand(4, 20) * 8 + 1
    perfect, log = duration_loss(torch.log(d), d)
    check("完全一致で最小", perfect.item() < 1e-6, f"L={perfect.item():.2e}")
    worse, _ = duration_loss(torch.log(d) + 0.5, d)
    check("ずれると増える", worse.item() > perfect.item(), f"{worse.item():.4f}")

    # 発話長保存項の効き: トークン誤差が打ち消し合っても総尺がずれれば罰する
    shifted = torch.log(d.clone())
    shifted[:, :10] += 0.4
    shifted[:, 10:] -= 0.4          # Huber は対称に増えるが、総尺は log 上で相殺しない
    _, lg = duration_loss(shifted, d)
    check("発話長保存項が反応する", lg["dur/length"].item() > 0,
          f"length={lg['dur/length'].item():.5f}")

    print("\n=== 式3 latent ===")
    C, T = 40, 60
    c = torch.randn(3, C, T)
    stats = ChannelStats(mu=torch.zeros(C), sigma=torch.rand(C) * 3 + 0.1)
    perfect, _ = latent_loss(c, c, stats)
    check("完全一致で 0", perfect.item() < 1e-6, f"L={perfect.item():.2e}")
    off, _ = latent_loss(c + 0.3, c, stats)
    check("ずれると増える", off.item() > 0.1, f"{off.item():.4f}")

    # チャネル正規化項の役割: σ の小さいチャネルの誤差を相対的に重く見る
    lo = int(stats.sigma.argmin())
    hi = int(stats.sigma.argmax())
    a = c.clone(); a[:, lo] += 0.5
    b = c.clone(); b[:, hi] += 0.5
    _, la = latent_loss(a, c, stats)
    _, lb = latent_loss(b, c, stats)
    check("低σチャネルの誤差を重く見る", la["lat/norm"] > lb["lat/norm"],
          f"σ={stats.sigma[lo]:.2f}→{la['lat/norm']:.3f} vs σ={stats.sigma[hi]:.2f}→{lb['lat/norm']:.3f}")

    # 一次差分項: 定数オフセットには反応しない（形が同じなら Δ は同じ）
    _, lc = latent_loss(c + 1.0, c, stats)
    check("Δ 項は定数オフセットに反応しない", lc["lat/delta"].item() < 1e-6,
          f"delta={lc['lat/delta'].item():.2e}")

    print("\n=== 式5 generator ===")
    y = torch.randn(3, 8192) * 0.1
    perfect, _ = generator_loss(y, y)
    check("完全一致で 0", perfect.item() < 1e-6, f"L={perfect.item():.2e}")
    check("MR-STFT が 3 解像度を平均",
          multi_resolution_stft_loss(y, y).item() < 1e-6)
    noisy, _ = generator_loss(y + torch.randn_like(y) * 0.05, y)
    check("雑音を足すと増える", noisy.item() > 0.01, f"{noisy.item():.4f}")

    # LSGAN: 判別器が本物と判定 (=1) すると adv が 0
    _, lg = generator_loss(y, y, disc_fake=torch.ones(3, 1, 10))
    check("LSGAN: D(fake)=1 で adv=0", lg["gen/adv"].item() < 1e-6)
    _, lg = generator_loss(y, y, disc_fake=torch.zeros(3, 1, 10))
    check("LSGAN: D(fake)=0 で adv=1", abs(lg["gen/adv"].item() - 1.0) < 1e-6)

    dl, _ = discriminator_loss(torch.ones(3, 1, 10), torch.zeros(3, 1, 10))
    check("判別器: 完璧な判別で 0", dl.item() < 1e-6)

    print("\n=== 式6 joint ===")
    total, lg = joint_loss(y, y, c, c)
    check("完全一致で 0", total.item() < 1e-6, f"L={total.item():.2e}")
    total2, lg2 = joint_loss(y, y, c + 0.2, c)
    check("c のずれが anchor に出る", lg2["joint/anchor"].item() > 0.1,
          f"anchor={lg2['joint/anchor'].item():.4f}")

    print("\n=== 式7 摩擦音ノイズ注入 ===")
    ch = torch.zeros(2, C, T)
    fric = torch.zeros(2, T); fric[:, 10:20] = 1.0
    sig = torch.ones(C) * 2.0
    out = inject_fricative_noise(ch, fric, sig, beta=6.0)
    inside = out[:, :, 10:20].abs().mean().item()
    outside = torch.cat([out[:, :, :10], out[:, :, 20:]], dim=2).abs().mean().item()
    check("摩擦音の区間にだけ注入", outside < 1e-9 and inside > 1.0,
          f"内 {inside:.3f} / 外 {outside:.2e}")
    check("β=0 なら無変化",
          torch.equal(inject_fricative_noise(ch, fric, sig, beta=0.0), ch))
    # σ に比例することを確認（式7 は σT_k 倍）
    o1 = inject_fricative_noise(ch, fric, torch.ones(C), beta=6.0,
                                generator=torch.Generator().manual_seed(1))
    o2 = inject_fricative_noise(ch, fric, torch.ones(C) * 2, beta=6.0,
                                generator=torch.Generator().manual_seed(1))
    check("σ に比例する", torch.allclose(o2[:, :, 10:20], o1[:, :, 10:20] * 2, atol=1e-5))

    print(f"\nS_ja = {S_JA}")
    check("無声化母音は I / U のみ（A/E/O は除外、C-004）",
          "I" in S_JA and "U" in S_JA and "A" not in S_JA and "O" not in S_JA)

    print()
    print("すべて期待通り" if fails == 0 else f"{fails} 件 NG")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
