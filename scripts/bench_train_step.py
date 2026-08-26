#!/usr/bin/env python3
"""学習 1 ステップの実コストを測り、vast.ai の費用を見積もる。

⚠️ **ローカル (CPU / MPS) での実測を GPU に外挿した見積もり。** 実機での学習は未実施。
外挿倍率が最大の不確定要素なので、幅を持たせて報告する。

論文が使う損失（式5/6）のうち、コストを支配するのは decoder 側:

* multi-resolution STFT（FFT 512/1024/2048 × hop 128/256/512）
* 一次差分判別器の LSGAN + feature matching
* iSTFT

実行:
    uv run python scripts/bench_train_step.py
"""

from __future__ import annotations

import sys
import time

import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, "src")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration, Erho  # noqa: E402

SR = 22050
HOP = 256
SEGMENT = 8192  # piper-plus の train config と同じ。0.372 s
FRAMES = SEGMENT // HOP  # 32

# 論文 §II-B: FFT 512/1024/2048 × hop 128/256/512
STFT_RES = [(512, 128), (1024, 256), (2048, 512)]
# 論文の公開重み
LAMBDA = {"w": 0.1, "S": 0.5, "A": 0.025, "F": 0.25, "c": 0.5}

CORPUS_SEC = 17.1 * 3600  # 学習コーパス 20,946 文の総音声長（M-15）


class Discriminator(nn.Module):
    """一次差分に対する判別器。論文は構造を明示していないので、
    HiFi-GAN の MSD 相当を小さくしたものを置く。**構成は推測**。"""

    def __init__(self, ch=(16, 64, 128, 128)):
        super().__init__()
        layers, cin = [], 1
        for cout in ch:
            layers.append(nn.Conv1d(cin, cout, 15, stride=4, padding=7))
            cin = cout
        self.convs = nn.ModuleList(layers)
        self.post = nn.Conv1d(cin, 1, 3, padding=1)

    def forward(self, x):
        feats = []
        h = x.unsqueeze(1)
        for c in self.convs:
            h = F.leaky_relu(c(h), 0.1)
            feats.append(h)
        return self.post(h), feats


def mrstft(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """log(1+|S|) の L1。論文 式5 の ell_{n,h}。"""
    total = a.new_zeros(())
    for n_fft, hop in STFT_RES:
        win = torch.hann_window(n_fft, device=a.device)
        sa = torch.stft(a, n_fft, hop, window=win, return_complex=True).abs()
        sb = torch.stft(b, n_fft, hop, window=win, return_complex=True).abs()
        total = total + (torch.log1p(sa) - torch.log1p(sb)).abs().mean()
    return total / len(STFT_RES)


def bench(device: str, batch: int, iters: int = 12) -> dict:
    dev = torch.device(device)
    acoustic, decoder = Acoustic().to(dev), Decoder().to(dev)
    disc = Discriminator().to(dev)
    opt_g = torch.optim.AdamW(
        list(acoustic.parameters()) + list(decoder.parameters()), lr=2e-4
    )
    opt_d = torch.optim.AdamW(disc.parameters(), lr=2e-4)

    c_t = torch.randn(batch, 40, FRAMES, device=dev)  # 教師の cT
    y_t = torch.randn(batch, SEGMENT, device=dev) * 0.1  # 教師の yT

    def step():
        # --- generator ---
        mag, cos, sin = decoder(c_t)
        y_hat = Decoder.istft(mag, cos, sin)
        y_hat = F.pad(y_hat, (0, max(0, SEGMENT - y_hat.shape[-1])))[:, :SEGMENT]

        d_fake, f_fake = disc(torch.diff(y_hat, dim=-1))
        with torch.no_grad():
            _, f_real = disc(torch.diff(y_t, dim=-1))
        l_adv = ((d_fake - 1) ** 2).mean()
        l_fm = sum((a - b).abs().mean() for a, b in zip(f_fake, f_real, strict=True))
        loss_g = (
            LAMBDA["w"] * (y_hat - y_t).abs().mean()
            + LAMBDA["S"] * mrstft(y_hat, y_t)
            + LAMBDA["A"] * l_adv
            + LAMBDA["F"] * l_fm
        )
        opt_g.zero_grad(set_to_none=True)
        loss_g.backward()
        opt_g.step()

        # --- discriminator ---
        d_real, _ = disc(torch.diff(y_t, dim=-1))
        d_f, _ = disc(torch.diff(y_hat.detach(), dim=-1))
        loss_d = ((d_real - 1) ** 2).mean() + (d_f**2).mean()
        opt_d.zero_grad(set_to_none=True)
        loss_d.backward()
        opt_d.step()

    for _ in range(3):  # ウォームアップ
        step()
    if device == "mps":
        torch.mps.synchronize()

    t0 = time.perf_counter()
    for _ in range(iters):
        step()
    if device == "mps":
        torch.mps.synchronize()
    elapsed = time.perf_counter() - t0

    per_step = elapsed / iters
    audio_per_step = batch * SEGMENT / SR
    return {
        "device": device,
        "batch": batch,
        "sec_per_step": per_step,
        "audio_sec_per_step": audio_per_step,
        "audio_sec_per_wall_sec": audio_per_step / per_step,
    }


def main() -> int:
    print("学習 1 ステップのベンチマーク（decoder + 判別器、論文 式5/6 相当）")
    print(f"  segment {SEGMENT} sample = {SEGMENT/SR:.3f} s / {FRAMES} frames")
    print(f"  MR-STFT {STFT_RES}")
    print(f"  判別器のパラメータ数: {sum(p.numel() for p in Discriminator().parameters()):,}"
          "  ⚠️ 論文は構造を明示していないので推測")

    devices = ["cpu"] + (["mps"] if torch.backends.mps.is_available() else [])
    rows = []
    print(f"\n{'device':>7}{'batch':>7}{'s/step':>10}{'音声s/step':>12}{'音声s/実時間s':>14}")
    for dev in devices:
        for batch in (8, 16):
            r = bench(dev, batch)
            rows.append(r)
            print(f"{r['device']:>7}{r['batch']:>7}{r['sec_per_step']:>10.4f}"
                  f"{r['audio_sec_per_step']:>12.2f}{r['audio_sec_per_wall_sec']:>14.2f}")

    best = max(rows, key=lambda r: r["audio_sec_per_wall_sec"])
    print(f"\nローカル最速: {best['device']} batch={best['batch']} → "
          f"{best['audio_sec_per_wall_sec']:.1f} 音声秒/実時間秒")

    # --- 学習量の想定 ---
    print("\n" + "=" * 66)
    print("学習量の想定（⚠️ 論文は step 数を明示していないので仮定）")
    print("=" * 66)
    print(f"  学習コーパスの総音声長: {CORPUS_SEC/3600:.1f} 時間")
    for epochs in (100, 300, 1000):
        total_audio = CORPUS_SEC * epochs
        print(f"\n  {epochs} epoch = 音声 {total_audio/3600:>7.0f} 時間分を通す")
        for name, speedup in [("ローカル最速", 1.0), ("RTX 4090 (×10 と仮定)", 10.0),
                              ("RTX 4090 (×30 と仮定)", 30.0)]:
            rate = best["audio_sec_per_wall_sec"] * speedup
            hours = total_audio / rate / 3600
            print(f"    {name:<24} {hours:>8.1f} 時間", end="")
            if speedup > 1:
                for price, label in [(0.29, "on-demand $0.29/h"), (0.13, "spot $0.13/h")]:
                    print(f"   {label} → ${hours*price:>7.2f}", end="")
            print()

    print("""
⚠️ この見積もりの弱点:
  1. **GPU の外挿倍率が推測**（×10 / ×30 で幅を出した）。実機で 1 回測れば確定する
  2. **必要 epoch 数が不明。** 論文は step 数を書いていない。
     14,343 行で SCOREQ 2.54 という結果だけがある
  3. **判別器の構造が推測。** 論文は「一次差分に対する判別器」としか書いていない
  4. Duration / Acoustic 単体の学習コストは decoder に比べ小さいので含めていない
  5. ハイパーパラメータ探索（λ 群が論文に無い）の試行回数を含んでいない""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
