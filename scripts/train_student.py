#!/usr/bin/env python3
"""生徒モデルの蒸留学習（Phase C 本学習）。

論文の 4 段構成:

    Stage 1  Dα        音素ID → log duration                式2
    Stage 2  Eρ + Aβ   zT → cT / (音素ID, d) → ĉ            式3
    Stage 3  Eρ + Gγ   c → 波形                             式5
    Stage 4  Aβ + Gγ   同時更新（c をアンカーで固定）        式6

**段の間で重みを引き継ぐ。** 引き継がないと各段が別のモデルを学習することになり、
損失は下がるのに成果物が出来ない（旧実装がそうだった）。

実行:
    uv run python scripts/train_student.py --run runs/v1 --stage 1 --steps 20000
    uv run python scripts/train_student.py --run runs/v1 --stage 2 --steps 20000
    ...
    uv run python scripts/train_student.py --run runs/v1 --all --steps 20000
    uv run python scripts/train_student.py --run /tmp/smoke --all --steps 12 --smoke
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, "src")
from saanotts_jp._param_reference import Acoustic, Decoder, Duration, Erho  # noqa: E402
from saanotts_jp.discriminator import (  # noqa: E402
    FirstDifferenceDiscriminator, count_parameters,
)
from saanotts_jp.labelpack import HOP, PackReader  # noqa: E402
from saanotts_jp.losses import (  # noqa: E402
    ChannelStats, discriminator_loss, duration_loss, generator_loss, joint_loss,
    latent_loss,
)
from saanotts_jp.vocab import map_ids  # noqa: E402

SEGMENT_FRAMES = 32          # 8192 sample = 0.372 s。piper-plus の train config と同じ
SEGMENT_SAMPLES = SEGMENT_FRAMES * HOP
C_DIM = 40                   # c-line の次元


# --- データ -----------------------------------------------------------------


class Batcher:
    """パックから固定長セグメント / 発話まるごとを切り出す。

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

    def segments(self, indices=None) -> tuple[torch.Tensor, torch.Tensor]:
        """(zT [B,192,F], yT [B,F*256])。decoder / joint の学習用。"""
        idx = self.rng.choice(self.usable, self.batch) if indices is None else indices
        zs, ys = [], []
        for i in idx:
            d = self.pack[int(i)]
            f = d["zT"].shape[1]
            s = int(self.rng.integers(0, f - SEGMENT_FRAMES))
            zs.append(d["zT"][:, s:s + SEGMENT_FRAMES])
            ys.append(d["yT"][s * HOP:(s + SEGMENT_FRAMES) * HOP])
        return (torch.from_numpy(np.stack(zs)).float(),
                torch.from_numpy(np.stack(ys)).float())

    def utterances(self, n: int | None = None) -> list[dict]:
        """発話まるごと。duration / acoustic の学習用（長さ展開が要るため）。

        **音素IDは教師の ID 空間から生徒の埋め込みインデックスに写す。**
        教師は 0〜173 の飛び飛びだが生徒の語彙は 57（D-016）。
        """
        out = []
        for i in self.rng.choice(self.usable, n or self.batch):
            d = dict(self.pack[int(i)])
            d["ids_teacher"] = d["ids"]
            d["ids"] = map_ids(d["ids"])
            out.append(d)
        return out


def pad_stack(arrays: list[np.ndarray], dtype=torch.float32):
    """可変長を右詰めゼロパディングして [B, L] と mask を返す。"""
    n = max(len(a) for a in arrays)
    out = np.zeros((len(arrays), n), dtype=np.float32)
    mask = np.zeros((len(arrays), n), dtype=np.float32)
    for i, a in enumerate(arrays):
        out[i, : len(a)] = a
        mask[i, : len(a)] = 1.0
    return torch.from_numpy(out).to(dtype), torch.from_numpy(mask)


# --- c-line のチャネル統計 ---------------------------------------------------


class RunningStats:
    """`Eρ` の出力（c-line 40ch）の統計を EMA で追う。

    ⚠️ **パックの `μ_T` / `σ_T` は zT (192ch) の統計で、c-line のものではない。**
    式3 の `N_T` は c と同じ空間の統計でなければ意味が無い。しかも `Eρ` は
    学習中に動くので、統計も追従させる必要がある（旧実装は mu=0 / sigma=1 の
    ダミーを渡していて、正規化項が実質ただの L1 になっていた）。
    """

    def __init__(self, dim: int, device, decay: float = 0.99) -> None:
        self.mu = torch.zeros(dim, device=device)
        self.var = torch.ones(dim, device=device)
        self.decay = decay
        self.n = 0

    @torch.no_grad()
    def update(self, c: torch.Tensor) -> None:
        m = c.transpose(0, 1).reshape(c.shape[1], -1)
        mu, var = m.mean(dim=1), m.var(dim=1, unbiased=False)
        d = 0.0 if self.n == 0 else self.decay      # 最初の 1 回は丸ごと採る
        self.mu.mul_(d).add_(mu, alpha=1 - d)
        self.var.mul_(d).add_(var, alpha=1 - d)
        self.n += 1

    def as_channel_stats(self) -> ChannelStats:
        return ChannelStats(mu=self.mu.clone(),
                            sigma=self.var.clamp_min(1e-10).sqrt())


# --- チェックポイント ---------------------------------------------------------

MODULES = {"duration": Duration, "erho": Erho, "acoustic": Acoustic,
           "decoder": Decoder, "disc": FirstDifferenceDiscriminator}


def save_ckpt(run: pathlib.Path, stage: int, models: dict, extra: dict) -> pathlib.Path:
    run.mkdir(parents=True, exist_ok=True)
    path = run / f"stage{stage}.pt"
    torch.save({"stage": stage,
                "state": {k: v.state_dict() for k, v in models.items()},
                **extra}, path)
    return path


def load_prev(run: pathlib.Path, stage: int, want: list[str], device) -> dict:
    """前段のチェックポイントから必要なモジュールを復元する。

    **無ければ止める。** 「引き継げなかったので新規初期化した」を黙って
    やると、損失は下がるのに成果物が出来ない（旧実装の欠陥）。
    """
    out: dict = {}
    for s in range(stage - 1, 0, -1):
        p = run / f"stage{s}.pt"
        if not p.exists():
            continue
        ck = torch.load(p, map_location=device, weights_only=False)
        for name in want:
            if name in out or name not in ck["state"]:
                continue
            m = MODULES[name]().to(device)
            m.load_state_dict(ck["state"][name])
            out[name] = m
        if "c_stats" in ck and "c_stats" not in out:
            # 新しい段のものを優先する（Eρ が Stage 3 で動くので統計も変わる）
            out["c_stats"] = ck["c_stats"]
    missing = [w for w in want if w not in out]
    if missing:
        raise SystemExit(
            f"Stage {stage} は {missing} を前段から引き継ぐ必要があります。\n"
            f"{run} に stage1..{stage-1}.pt がありません。先に前段を回してください。")
    return out


# --- 学習ループ ---------------------------------------------------------------


def _log(logf, rec: dict) -> None:
    logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logf.flush()


def train_duration(ctx) -> dict:
    """Stage 1: 式2。音素IDのみから duration を予測する。"""
    dev, args = ctx["device"], ctx["args"]
    model = Duration().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.steps)
    hist = []

    def batch(b):
        utts = b.utterances()
        ids, mask = pad_stack([u["ids"] for u in utts])
        d_t, _ = pad_stack([u["dT"] for u in utts])
        return ids.long().to(dev), d_t.to(dev), mask.to(dev)

    for step in range(args.steps):
        ids, d_t, mask = batch(ctx["train"])
        loss, log = duration_loss(model(ids), d_t, mask)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        sched.step()
        hist.append(loss.item())
        if ctx["report"](step, loss.item()):
            with torch.no_grad():
                v = np.mean([duration_loss(model(i), d, m)[0].item()
                             for i, d, m in (batch(ctx["val"]) for _ in range(8))])
            _log(ctx["logf"], {"stage": 1, "step": step + 1, "train": loss.item(),
                               "val": float(v), "huber": log["dur/huber"].item(),
                               "length": log["dur/length"].item(),
                               "lr": sched.get_last_lr()[0]})
            print(f"    step {step+1:>6}  L={loss.item():.4f}  val={v:.4f}  "
                  f"huber={log['dur/huber']:.4f}  length={log['dur/length']:.5f}")
    return {"models": {"duration": model}, "hist": hist, "extra": {}}


def train_acoustic(ctx) -> dict:
    """Stage 2: 式3。`Eρ` で zT を 40ch の cT に落とし、`Aβ` にそれを追わせる。

    `Eρ` は**学習専用**（14,952 params）。デプロイ時は acoustic が c を直接出すので
    559,008 の勘定に入らない。

    ⚠️ **`Eρ` は式3 だけでは決まらない。** `c` の意味を決めるのは decoder（Stage 3）で、
    Stage 2 で `Eρ` を自由に動かすと `c` が定数に潰れる自明解がある。ここでは
    **`Eρ` を凍結して `Aβ` だけを学習する**。`Eρ` の初期値はランダムだが、
    Stage 3 で decoder と一緒に学習され、Stage 4 で `Aβ` が追い直す。
    自明解に落ちていないことは `c_rank` / `c_std` で監視する。
    """
    dev, args = ctx["device"], ctx["args"]
    erho = Erho().to(dev)
    for p in erho.parameters():          # ← 凍結（理由は docstring）
        p.requires_grad_(False)
    acoustic = Acoustic().to(dev)
    opt = torch.optim.AdamW(acoustic.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=max(1, args.steps // max(1, args.accum)))
    stats = RunningStats(C_DIM, dev)
    hist = []
    accum = max(1, args.accum)

    def one(b, train: bool):
        u = b.utterances(1)[0]
        ids = torch.from_numpy(u["ids"]).long()[None].to(dev)
        d = torch.from_numpy(np.ceil(u["dT"]).astype(np.int64))[None].to(dev)
        z = torch.from_numpy(u["zT"])[None].to(dev)
        with torch.no_grad():
            c_t = erho(z)
        if train:
            stats.update(c_t)
        c_h = acoustic(ids, d)
        n = min(c_t.shape[-1], c_h.shape[-1])
        return latent_loss(c_h[..., :n], c_t[..., :n], stats.as_channel_stats()) + (c_t,)

    opt.zero_grad(set_to_none=True)
    for step in range(args.steps):
        loss, log, c_t = one(ctx["train"], True)
        (loss / accum).backward()
        if (step + 1) % accum == 0:
            torch.nn.utils.clip_grad_norm_(acoustic.parameters(), 5.0)
            opt.step()
            opt.zero_grad(set_to_none=True)
            sched.step()          # accum の単位で進める（step ごとだと最後まで届かない）
        hist.append(loss.item())
        if ctx["report"](step, loss.item()):
            with torch.no_grad():
                v = np.mean([one(ctx["val"], False)[0].item() for _ in range(8)])
                # 自明解（c が定数に潰れる）の監視。MPS に svdvals が無いので CPU で取る
                m = c_t[0].t().float().cpu()          # [T, 40]
                sv = torch.linalg.svdvals(m - m.mean(0, keepdim=True))
                rank = float((sv > sv[0] * 1e-3).sum()) if float(sv[0]) > 0 else 0.0
            _log(ctx["logf"], {
                "stage": 2, "step": step + 1, "train": loss.item(), "val": float(v),
                "l1": log["lat/l1"].item(), "norm": log["lat/norm"].item(),
                "delta": log["lat/delta"].item(), "stat": log["lat/stat"].item(),
                "lambda_2": log["lat/lambda_2"].item(),
                "lambda_n": float(log["lat/lambda_n"]),
                "c_std": float(c_t.std()), "c_rank": rank,
                "lr": sched.get_last_lr()[0]})
            print(f"    step {step+1:>6}  L={loss.item():.4f}  val={v:.4f}  "
                  f"l1={log['lat/l1']:.4f}  c_std={float(c_t.std()):.3f}  "
                  f"c_rank={rank:.0f}/40")
    return {"models": {"erho": erho, "acoustic": acoustic}, "hist": hist,
            "extra": {"c_stats": {"mu": stats.mu.cpu(), "sigma":
                                  stats.var.clamp_min(1e-10).sqrt().cpu()}}}


def _gan_step(ctx, gen_params, disc, opt_g, opt_d, y_hat, y, c_pair=None):
    """式5 / 式6 の 1 ステップ。生成器 → 判別器の順で更新する。"""
    d_fake, f_fake = disc(y_hat)
    with torch.no_grad():
        _, f_real = disc(y)
    if c_pair is None:
        loss, log = generator_loss(y_hat, y, d_fake, f_fake, f_real)
    else:
        loss, log = joint_loss(y_hat, y, c_pair[0], c_pair[1], d_fake, f_fake, f_real)
    opt_g.zero_grad(set_to_none=True)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(gen_params, 5.0)
    opt_g.step()

    d_real, _ = disc(y)
    d_f, _ = disc(y_hat.detach())
    loss_d, _ = discriminator_loss(d_real, d_f)
    opt_d.zero_grad(set_to_none=True)
    loss_d.backward()
    opt_d.step()
    return loss, log, loss_d


def train_decoder(ctx) -> dict:
    """Stage 3: 式5。`Eρ` と decoder を同時に学習し、**c の意味をここで決める**。"""
    dev, args = ctx["device"], ctx["args"]
    prev = load_prev(ctx["run"], 3, ["erho"], dev)
    erho = prev["erho"]
    for p in erho.parameters():
        p.requires_grad_(True)           # Stage 2 で凍結したので戻す
    decoder = Decoder().to(dev)
    disc = FirstDifferenceDiscriminator().to(dev)
    params = list(erho.parameters()) + list(decoder.parameters())
    opt_g = torch.optim.AdamW(params, lr=args.lr)
    opt_d = torch.optim.AdamW(disc.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt_g, T_max=args.steps)
    hist = []

    def forward(b):
        z, y = b.segments()
        z, y = z.to(dev), y.to(dev)
        c = erho(z)
        mag, cos, sin = decoder(c)
        y_hat = Decoder.istft(mag, cos, sin)
        # D5: istft が length=T*256 を渡すので長さは一致する。
        # 以前の `F.pad` はゼロ埋めで勾配経路を切り、SNR 上限を 33.6 dB に固定していた
        assert y_hat.shape[-1] == y.shape[-1], (y_hat.shape, y.shape)
        return y_hat, y

    for step in range(args.steps):
        y_hat, y = forward(ctx["train"])
        loss, log, loss_d = _gan_step(ctx, params, disc, opt_g, opt_d, y_hat, y)
        sched.step()
        hist.append(loss.item())
        if ctx["report"](step, loss.item()):
            with torch.no_grad():
                yh, yy = forward(ctx["val"])
                v = generator_loss(yh, yy)[0].item()
                snr = 10 * torch.log10(yy.pow(2).mean()
                                       / (yy - yh).pow(2).mean().clamp_min(1e-20))
            _log(ctx["logf"], {"stage": 3, "step": step + 1, "train": loss.item(),
                               "val": v, "stft": log["gen/stft"].item(),
                               "adv": float(log.get("gen/adv", 0)),
                               "disc": loss_d.item(), "val_snr_db": float(snr),
                               "lr": sched.get_last_lr()[0]})
            print(f"    step {step+1:>6}  L_G={loss.item():.4f}  val={v:.4f}  "
                  f"stft={log['gen/stft']:.4f}  L_D={loss_d.item():.4f}  "
                  f"SNR={float(snr):.1f}dB")
    # c-line の σ は式7 の摩擦音ノイズ注入で要る。**後段の ckpt に引き継ぐ**
    return {"models": {"erho": erho, "decoder": decoder, "disc": disc},
            "hist": hist,
            "extra": {"c_stats": prev["c_stats"]} if "c_stats" in prev else {}}


def train_joint(ctx) -> dict:
    """Stage 4: 式6。`Aβ` と decoder を同時更新し、c をアンカーで固定する。

    **predicted-code mixing**（論文 §II-B: decoder は教師の contract と生徒の
    予測した contract の両方を受け取る）。旧実装はガウスノイズで代理していたが、
    ここでは**実際に `Aβ` の出力**を使う。
    """
    dev, args = ctx["device"], ctx["args"]
    # duration も引き継ぐ。**Stage 4 の ckpt がデプロイ対象 3 つの完成品**になる
    prev = load_prev(ctx["run"], 4,
                     ["duration", "erho", "acoustic", "decoder", "disc"], dev)
    duration, erho, acoustic = prev["duration"], prev["erho"], prev["acoustic"]
    decoder, disc = prev["decoder"], prev["disc"]
    for p in erho.parameters():
        p.requires_grad_(False)          # contract は Stage 3 で確定済み
    params = list(acoustic.parameters()) + list(decoder.parameters())
    opt_g = torch.optim.AdamW(params, lr=args.lr)
    opt_d = torch.optim.AdamW(disc.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt_g, T_max=args.steps)
    hist = []

    def forward(b):
        """発話 1 本から教師 contract と生徒 contract を作り、同じ区間を切る。"""
        u = b.utterances(1)[0]
        ids = torch.from_numpy(u["ids"]).long()[None].to(dev)
        d = torch.from_numpy(np.ceil(u["dT"]).astype(np.int64))[None].to(dev)
        z = torch.from_numpy(u["zT"])[None].to(dev)
        y = torch.from_numpy(u["yT"])[None].to(dev)
        with torch.no_grad():
            c_t = erho(z)
        c_h = acoustic(ids, d)
        n = min(c_t.shape[-1], c_h.shape[-1], y.shape[-1] // HOP)
        if n <= SEGMENT_FRAMES:
            return None
        s = int(b.rng.integers(0, n - SEGMENT_FRAMES))
        sl = slice(s, s + SEGMENT_FRAMES)
        c_t, c_h = c_t[..., sl], c_h[..., sl]
        y = y[:, s * HOP:(s + SEGMENT_FRAMES) * HOP]
        # predicted-code mixing: 教師 contract と生徒 contract を混ぜる
        mix = float(torch.rand(1))
        mag, cos, sin = decoder(mix * c_h + (1 - mix) * c_t)
        y_hat = Decoder.istft(mag, cos, sin)
        assert y_hat.shape[-1] == y.shape[-1], (y_hat.shape, y.shape)
        return y_hat, y, c_h, c_t

    step = 0
    while step < args.steps:
        got = forward(ctx["train"])
        if got is None:
            continue
        y_hat, y, c_h, c_t = got
        loss, log, loss_d = _gan_step(ctx, params, disc, opt_g, opt_d, y_hat, y,
                                      c_pair=(c_h, c_t))
        sched.step()
        hist.append(loss.item())
        if ctx["report"](step, loss.item()):
            with torch.no_grad():
                vs = [forward(ctx["val"]) for _ in range(8)]
                vs = [g for g in vs if g is not None]
                v = float(np.mean([joint_loss(a, b, c, d)[0].item()
                                   for a, b, c, d in vs])) if vs else float("nan")
            _log(ctx["logf"], {"stage": 4, "step": step + 1, "train": loss.item(),
                               "val": v, "anchor": log["joint/anchor"].item(),
                               "stft": log["gen/stft"].item(),
                               "disc": loss_d.item(), "lr": sched.get_last_lr()[0]})
            print(f"    step {step+1:>6}  L={loss.item():.4f}  val={v:.4f}  "
                  f"anchor={log['joint/anchor']:.4f}  L_D={loss_d.item():.4f}")
        step += 1
    return {"models": {"duration": duration, "erho": erho, "acoustic": acoustic,
                       "decoder": decoder, "disc": disc},
            "hist": hist,
            "extra": {"c_stats": prev["c_stats"]} if "c_stats" in prev else {}}


STAGES = {1: train_duration, 2: train_acoustic, 3: train_decoder, 4: train_joint}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/pack")
    ap.add_argument("--val-pack", default="data/pack_heldout")
    ap.add_argument("--run", required=True, help="チェックポイントとログの置き場")
    ap.add_argument("--stage", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--all", action="store_true", help="Stage 1→4 を順に回す")
    ap.add_argument("--smoke", action="store_true", help="判定を出して終わる")
    ap.add_argument("--steps", type=int, default=20000)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=8, help="Stage 2 の勾配累積")
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=20260827)
    args = ap.parse_args()
    if not args.all and args.stage is None:
        raise SystemExit("--stage か --all のどちらかが要ります")

    device = args.device or (
        "mps" if torch.backends.mps.is_available()
        else "cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(args.seed)

    pack = PackReader(args.pack)
    val_pack = PackReader(args.val_pack) if pathlib.Path(args.val_pack).is_dir() else pack
    run = pathlib.Path(args.run)
    run.mkdir(parents=True, exist_ok=True)
    print(f"train {len(pack)} 発話 / val {len(val_pack)} 発話 / device={device}")
    print(f"  zT の μ_T mean {pack.mu_T.mean():+.4f}  σ_T mean {pack.sigma_T.mean():.4f}")
    if val_pack is pack:
        print(f"  ⚠️ {args.val_pack} が無いので **train で検証している**（値は楽観的）")

    logf = open(run / "log.jsonl", "a")
    stages = [1, 2, 3, 4] if args.all else [args.stage]
    results = []
    for st in stages:
        every = max(1, args.steps // (5 if args.smoke else 40))
        ctx = {
            "device": device, "args": args, "run": run, "logf": logf,
            "train": Batcher(pack, batch=args.batch, seed=args.seed + st),
            "val": Batcher(val_pack, batch=args.batch, seed=args.seed + 100 + st),
            "report": lambda s, _l, e=every: (s + 1) % e == 0,
        }
        print(f"\n--- Stage {st}: {STAGES[st].__doc__.splitlines()[0]}")
        t0 = time.perf_counter()
        r = STAGES[st](ctx)
        el = time.perf_counter() - t0
        h = r["hist"]
        drop = (h[0] - h[-1]) / abs(h[0]) * 100 if h[0] else 0.0
        path = save_ckpt(run, st, r["models"], {
            "args": vars(args), "elapsed_sec": round(el, 1),
            "first": h[0], "last": h[-1], **r["extra"]})
        deployed = {k: count_parameters(m) for k, m in r["models"].items()
                    if k in ("duration", "acoustic", "decoder")}
        n_par = sum(deployed.values())
        print(f"    {args.steps} step / {el:.1f}s ({el/args.steps*1000:.0f} ms/step)  "
              f"L {h[0]:.4f} → {h[-1]:.4f} ({drop:+.1f}%)")
        note = "（この段の分だけ）"
        if len(deployed) == 3:
            assert n_par == 559008, f"デプロイ対象が 559,008 でない: {n_par}"
            note = "= 完成品（D-016 の 559,008 と一致）"
        print(f"    → {path}  デプロイ対象 {n_par:,} params {note}")
        results.append({"stage": st, "first": h[0], "last": h[-1],
                        "drop_pct": round(drop, 2),
                        "ms_per_step": round(el / args.steps * 1000)})
        del r
    logf.close()

    (run / "summary.json").write_text(json.dumps(
        {"device": device, "steps": args.steps, "pack": args.pack,
         "val_pack": args.val_pack, "results": results},
        ensure_ascii=False, indent=1))

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
        print("⚠️ 「回ること」の確認であって品質の確認ではない")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
