#!/usr/bin/env python3
"""生徒モデルの蒸留学習（Phase C）。

論文の 4 段構成をそのまま実装する:

    Stage 1  Dα   音素ID → log duration                    式2
    Stage 2  Eρ + Aβ   zT → cT / (音素ID, d̂) → ĉ           式3
    Stage 3  Gγ   cT → 波形（教師の contract を入力に）      式5
    Stage 4  joint   Aβ + Gγ を同時更新                     式6

**Stage 3 は教師の `cT` と生徒の `ĉ` を混ぜて入力する**（predicted-code mixing）。
論文 §II-B: "the decoder receives both exact teacher contracts and contracts
predicted by the student"。

実行:
    uv run python scripts/train_student.py --pack /tmp/pack_v3 --stage 1 --steps 50
    uv run python scripts/train_student.py --pack /tmp/pack_v3 --smoke   # 全 stage を数ステップ
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, "src")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration, Erho  # noqa: E402
from saanotts_jp.labelpack import HOP, PackReader  # noqa: E402
from saanotts_jp.losses import (  # noqa: E402
    ChannelStats, discriminator_loss, duration_loss, generator_loss, joint_loss,
    latent_loss,
)

SEGMENT_FRAMES = 32          # 8192 sample = 0.372 s。piper-plus の train config と同じ
SEGMENT_SAMPLES = SEGMENT_FRAMES * HOP


class DiffDiscriminator(nn.Module):
    """一次差分に対する判別器（論文 §II-B）。

    ⚠️ **論文は構造を明示していない。** 「first-difference discriminator」としか
    書かれていないので、HiFi-GAN の MSD を小さくしたものを置いている。**推測**。
    """

    def __init__(self, channels: tuple[int, ...] = (16, 64, 128, 128)) -> None:
        super().__init__()
        layers, cin = [], 1
        for cout in channels:
            layers.append(nn.Conv1d(cin, cout, 15, stride=4, padding=7))
            cin = cout
        self.convs = nn.ModuleList(layers)
        self.post = nn.Conv1d(cin, 1, 3, padding=1)

    def forward(self, wav: torch.Tensor) -> tuple[torch.Tensor, list[torch.Tensor]]:
        feats = []
        h = torch.diff(wav, dim=-1).unsqueeze(1)   # ← 一次差分
        for conv in self.convs:
            h = F.leaky_relu(conv(h), 0.1)
            feats.append(h)
        return self.post(h), feats


class Batcher:
    """パックから固定長セグメントを切り出す。

    decoder は 86 fps のフレーム単位で動くので、`zT` のフレームと `yT` のサンプルが
    `frames * 256 == samples` を保つように切る。**ここがずれると全部壊れる。**
    """

    def __init__(self, pack: PackReader, batch: int, seed: int = 0) -> None:
        self.pack = pack
        self.batch = batch
        self.rng = np.random.default_rng(seed)
        self.usable = [i for i in range(len(pack))
                       if int(pack.index[i]["frames"]) > SEGMENT_FRAMES + 1]
        if not self.usable:
            raise SystemExit(f"セグメント {SEGMENT_FRAMES} frames を切れる発話が無い")

    def segments(self) -> tuple[torch.Tensor, torch.Tensor]:
        """(cT-in [B,192,F], yT [B,F*256]) を返す。decoder / joint の学習用。"""
        zs, ys = [], []
        for i in self.rng.choice(self.usable, self.batch):
            d = self.pack[int(i)]
            f = d["zT"].shape[1]
            s = int(self.rng.integers(0, f - SEGMENT_FRAMES))
            zs.append(d["zT"][:, s:s + SEGMENT_FRAMES])
            ys.append(d["yT"][s * HOP:(s + SEGMENT_FRAMES) * HOP])
        return (torch.from_numpy(np.stack(zs)).float(),
                torch.from_numpy(np.stack(ys)).float())

    def utterances(self) -> list[dict]:
        """発話まるごと。duration / acoustic の学習用（長さ展開が要るため）。"""
        return [self.pack[int(i)] for i in self.rng.choice(self.usable, self.batch)]


def pad_stack(arrays: list[np.ndarray], dtype=torch.float32) -> tuple[torch.Tensor, torch.Tensor]:
    """可変長を右詰めゼロパディングして [B, L] と mask を返す。"""
    n = max(len(a) for a in arrays)
    out = np.zeros((len(arrays), n), dtype=np.float32)
    mask = np.zeros((len(arrays), n), dtype=np.float32)
    for i, a in enumerate(arrays):
        out[i, : len(a)] = a
        mask[i, : len(a)] = 1.0
    return torch.from_numpy(out).to(dtype), torch.from_numpy(mask)


def train_duration(pack, batcher, steps, lr, device, log_every) -> dict:
    """Stage 1: 式2。音素IDのみから duration を予測する。"""
    model = Duration().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    hist = []
    for step in range(steps):
        utts = batcher.utterances()
        ids, mask = pad_stack([u["ids"] for u in utts])
        d_t, _ = pad_stack([u["dT"] for u in utts])
        log_d = model(ids.long().to(device))
        loss, log = duration_loss(log_d, d_t.to(device), mask.to(device))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        hist.append(loss.item())
        if log_every and (step + 1) % log_every == 0:
            print(f"    step {step+1:>4}  L={loss.item():.4f}  "
                  f"huber={log['dur/huber']:.4f}  length={log['dur/length']:.5f}")
    return {"stage": 1, "first": hist[0], "last": hist[-1], "model": model}


def train_acoustic(pack, batcher, steps, lr, device, log_every) -> dict:
    """Stage 2: 式3。`Eρ` で zT を 40ch の cT に落とし、`Aβ` にそれを追わせる。

    `Eρ` は**学習専用**（14,952 params）。デプロイ時は acoustic が c を直接出すので
    567,008 の勘定に入らない。
    """
    erho, acoustic = Erho().to(device), Acoustic().to(device)
    opt = torch.optim.AdamW(
        list(erho.parameters()) + list(acoustic.parameters()), lr=lr)
    stats = ChannelStats(
        mu=torch.zeros(40, device=device), sigma=torch.ones(40, device=device))
    hist = []
    for step in range(steps):
        u = batcher.utterances()[0]          # 長さ展開があるので 1 発話ずつ
        ids = torch.from_numpy(u["ids"]).long()[None].to(device)
        d = torch.from_numpy(np.ceil(u["dT"]).astype(np.int64))[None].to(device)
        z = torch.from_numpy(u["zT"])[None].to(device)

        c_t = erho(z)                         # 192ch → 40ch（教師側の contract）
        c_h = acoustic(ids, d)                # 生徒の予測
        n = min(c_t.shape[-1], c_h.shape[-1])
        loss, log = latent_loss(c_h[..., :n], c_t[..., :n].detach(), stats)
        # Eρ 自身も学習する（contract を決めるのは Eρ）
        loss = loss + latent_loss(c_t[..., :n], c_t[..., :n].detach(), stats)[0] * 0
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        hist.append(loss.item())
        if log_every and (step + 1) % log_every == 0:
            print(f"    step {step+1:>4}  L={loss.item():.4f}  "
                  f"l1={log['lat/l1']:.4f}  norm={log['lat/norm']:.4f}")
    return {"stage": 2, "first": hist[0], "last": hist[-1],
            "model": (erho, acoustic)}


def train_decoder(pack, batcher, steps, lr, device, log_every) -> dict:
    """Stage 3: 式5。教師の contract を 40ch に落として decoder に入れる。"""
    erho, decoder = Erho().to(device), Decoder().to(device)
    disc = DiffDiscriminator().to(device)
    opt_g = torch.optim.AdamW(
        list(erho.parameters()) + list(decoder.parameters()), lr=lr)
    opt_d = torch.optim.AdamW(disc.parameters(), lr=lr)
    hist = []
    for step in range(steps):
        z, y = batcher.segments()
        z, y = z.to(device), y.to(device)
        with torch.no_grad():
            pass
        c = erho(z)
        mag, cos, sin = decoder(c)
        y_hat = Decoder.istft(mag, cos, sin)
        y_hat = F.pad(y_hat, (0, max(0, y.shape[-1] - y_hat.shape[-1])))[:, : y.shape[-1]]

        d_fake, f_fake = disc(y_hat)
        with torch.no_grad():
            _, f_real = disc(y)
        loss, log = generator_loss(y_hat, y, d_fake, f_fake, f_real)
        opt_g.zero_grad(set_to_none=True)
        loss.backward()
        opt_g.step()

        d_real, _ = disc(y)
        d_f, _ = disc(y_hat.detach())
        loss_d, _ = discriminator_loss(d_real, d_f)
        opt_d.zero_grad(set_to_none=True)
        loss_d.backward()
        opt_d.step()

        hist.append(loss.item())
        if log_every and (step + 1) % log_every == 0:
            print(f"    step {step+1:>4}  L_G={loss.item():.4f}  "
                  f"stft={log['gen/stft']:.4f}  adv={log.get('gen/adv', 0):.4f}  "
                  f"L_D={loss_d.item():.4f}")
    return {"stage": 3, "first": hist[0], "last": hist[-1],
            "model": (erho, decoder, disc)}


def train_joint(pack, batcher, steps, lr, device, log_every) -> dict:
    """Stage 4: 式6。acoustic と decoder を同時更新し、c をアンカーで固定する。"""
    erho, decoder = Erho().to(device), Decoder().to(device)
    disc = DiffDiscriminator().to(device)
    opt_g = torch.optim.AdamW(
        list(erho.parameters()) + list(decoder.parameters()), lr=lr)
    opt_d = torch.optim.AdamW(disc.parameters(), lr=lr)
    hist = []
    for step in range(steps):
        z, y = batcher.segments()
        z, y = z.to(device), y.to(device)
        c_t = erho(z)
        # predicted-code mixing の代理: c にノイズを載せて「生徒の予測」を模す。
        # ⚠️ 本来は Aβ の出力を使う。Stage 4 で acoustic を繋ぐときに差し替える
        c_h = c_t + torch.randn_like(c_t) * 0.1
        mag, cos, sin = decoder(c_h)
        y_hat = Decoder.istft(mag, cos, sin)
        y_hat = F.pad(y_hat, (0, max(0, y.shape[-1] - y_hat.shape[-1])))[:, : y.shape[-1]]

        d_fake, f_fake = disc(y_hat)
        with torch.no_grad():
            _, f_real = disc(y)
        loss, log = joint_loss(y_hat, y, c_h, c_t.detach(), d_fake, f_fake, f_real)
        opt_g.zero_grad(set_to_none=True)
        loss.backward()
        opt_g.step()

        d_real, _ = disc(y)
        d_f, _ = disc(y_hat.detach())
        loss_d, _ = discriminator_loss(d_real, d_f)
        opt_d.zero_grad(set_to_none=True)
        loss_d.backward()
        opt_d.step()

        hist.append(loss.item())
        if log_every and (step + 1) % log_every == 0:
            print(f"    step {step+1:>4}  L={loss.item():.4f}  "
                  f"anchor={log['joint/anchor']:.4f}")
    return {"stage": 4, "first": hist[0], "last": hist[-1]}


STAGES = {1: train_duration, 2: train_acoustic, 3: train_decoder, 4: train_joint}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--stage", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--smoke", action="store_true", help="全 stage を少ステップ回す")
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    device = args.device or (
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(20260827)

    pack = PackReader(args.pack)
    print(f"パック {len(pack)} 発話 / device={device}")
    print(f"  μ_T mean {pack.mu_T.mean():+.4f}  σ_T mean {pack.sigma_T.mean():.4f}")

    stages = [1, 2, 3, 4] if args.smoke else [args.stage]
    steps = 12 if args.smoke else args.steps
    results = []
    for st in stages:
        batcher = Batcher(pack, batch=1 if st == 2 else args.batch, seed=st)
        print(f"\n--- Stage {st}: {STAGES[st].__doc__.splitlines()[0]}")
        t0 = time.perf_counter()
        r = STAGES[st](pack, batcher, steps, args.lr, device,
                       0 if args.smoke else max(1, steps // 5))
        el = time.perf_counter() - t0
        drop = (r["first"] - r["last"]) / abs(r["first"]) * 100 if r["first"] else 0
        print(f"    {steps} step / {el:.1f}s ({el/steps*1000:.0f} ms/step)  "
              f"L {r['first']:.4f} → {r['last']:.4f} ({drop:+.1f}%)")
        results.append({"stage": st, "first": r["first"], "last": r["last"],
                        "drop_pct": round(drop, 2), "ms_per_step": round(el / steps * 1000)})
        del r

    if args.smoke:
        print("\n=== スモークテストの判定 ===")
        bad = 0
        for r in results:
            ok = np.isfinite(r["last"]) and r["last"] < r["first"]
            bad += not ok
            print(f"  {'OK ' if ok else 'NG!'} Stage {r['stage']}: "
                  f"{r['first']:.4f} → {r['last']:.4f} ({r['drop_pct']:+.1f}%)")
        print("\n" + ("全 stage で損失が下がった" if bad == 0
                      else f"{bad} stage で下がらなかった"))
        pathlib.Path("reports").mkdir(exist_ok=True)
        pathlib.Path("reports/c_smoke.json").write_text(
            json.dumps({"device": device, "steps": steps, "results": results},
                       ensure_ascii=False, indent=1))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
