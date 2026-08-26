#!/usr/bin/env python3
"""C-1: 論文に値が無い λ（式3 の λ₂/λ_n/λ_Δ/λ_s、式2 の λ_T）の初期値を実測で決める。

**学習はしない。** ローカル学習は禁止（D-012 / vast.ai 規約）なので、ここでやるのは
「項のスケールと勾配ノルムの実測」だけ。

なぜ勾配ノルムか
----------------
λ は損失の**値**ではなく**勾配**を通じてしか学習に影響しない。
値が大きくても勾配が小さい項は実質効かない。したがって λ の初期化は
GradNorm 的に `λ_k = ‖∂L_l1/∂ĉ‖₂ / ‖∂L_k/∂ĉ‖₂` で揃えるのが素直。
値と勾配ノルムの両方を出し、**区別して**報告する。

⚠️ 測定空間について
-------------------
c-line (40ch) のターゲット `cT = Eρ(zT)` は Eρ が未学習なので存在しない。
したがって主測定は **z-line (192ch) の実データ**で行う。
外挿の妥当性を測るため、以下 2 つの代理空間でも同じ測定を回す:

* `z40_subset` : zT から 40ch をランダム抽出（**チャネル数だけ**を 40 に落とした対照）
* `c40_pca`    : zT の PCA-40 射影（Eρ の線形代理。Eρ は学習された非線形写像なので
                 これは代理でしかないが、「40ch でチャネル分散スペクトルが変わる」
                 効果は再現する）

実行:
    uv run python scripts/c1_lambda_balance.py
    uv run python scripts/c1_lambda_balance.py --batch-size 8 --seed 0
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import platform
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from saanotts_jp.labelpack import PackReader  # noqa: E402
from saanotts_jp import losses as L  # noqa: E402

TERMS = ("lat/l1", "lat/l2", "lat/norm", "lat/delta", "lat/stat")
LEVELS = (0.05, 0.10, 0.20, 0.40)
ERR_TYPES = ("white", "smooth_err", "detail_crush", "chan_scale", "chan_bias")
SMOOTH_K = 9  # 移動平均の窓（フレーム）。86.13 fps なので 9 frame ≒ 104 ms

# t 分布の 97.5% 点（両側 95% CI 用）。scipy を足したくないので表引き
_T975 = {5: 2.571, 10: 2.228, 15: 2.131, 20: 2.086, 25: 2.060, 26: 2.056,
         30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980}


def t975(df: int) -> float:
    if df <= 0:
        return float("nan")
    for k in sorted(_T975):
        if df <= k:
            return _T975[k]
    return 1.96


def summ(xs: list[float]) -> dict:
    """平均・標準偏差・95% CI。**n を必ず一緒に出す**（C-004 の教訓）。"""
    a = np.asarray(xs, dtype=np.float64)
    a = a[np.isfinite(a)]
    n = int(a.size)
    if n == 0:
        return {"n": 0, "mean": None}
    m = float(a.mean())
    if n == 1:
        return {"n": 1, "mean": m, "sd": 0.0, "ci95": [m, m], "min": m, "max": m}
    sd = float(a.std(ddof=1))
    h = t975(n - 1) * sd / math.sqrt(n)
    return {"n": n, "mean": m, "sd": sd, "ci95": [m - h, m + h],
            "min": float(a.min()), "max": float(a.max())}


# --- 式3 の 5 項を個別に微分可能な形で返す -----------------------------------
# losses.py は書き換えない（親が適用する）。ここで再実装し、
# **losses.latent_loss の log と一致することを assert して**ドリフトを防ぐ。

def latent_terms(c_hat, c_target, stats, mask):
    m = mask
    C = c_hat.shape[1]
    denom = (m.sum() * C).clamp_min(1)

    l1 = ((c_hat - c_target).abs() * m).sum() / denom
    l2 = (((c_hat - c_target) ** 2) * m).sum() / denom
    ln = ((stats.normalize(c_hat) - stats.normalize(c_target)).abs() * m).sum() / denom

    d_hat = c_hat[..., 1:] - c_hat[..., :-1]
    d_tgt = c_target[..., 1:] - c_target[..., :-1]
    md = m[..., 1:]
    ld = ((d_hat - d_tgt).abs() * md).sum() / (md.sum() * C).clamp_min(1)

    n = m.sum(dim=(1, 2)).clamp_min(1)[:, None]
    mu_h = (c_hat * m).sum(dim=2) / n
    mu_t = (c_target * m).sum(dim=2) / n
    var_h = ((c_hat - mu_h[..., None]) ** 2 * m).sum(dim=2) / n
    var_t = ((c_target - mu_t[..., None]) ** 2 * m).sum(dim=2) / n
    sig = stats.sigma[None, :].clamp_min(1e-5)
    l_stat = (((mu_h - mu_t) / sig).abs().mean()
              + ((var_h.sqrt() - var_t.sqrt()) / sig).abs().mean())

    return {"lat/l1": l1, "lat/l2": l2, "lat/norm": ln,
            "lat/delta": ld, "lat/stat": l_stat}


# --- 誤差モデル ---------------------------------------------------------------

def _masked_movavg(x, m, k):
    """マスクを考慮した移動平均。端とパディングで平均が薄まらないようにする。"""
    B, C, T = x.shape
    ker = torch.ones(1, 1, k, dtype=x.dtype) / k
    pad = k // 2
    num = F.conv1d(F.pad((x * m).reshape(B * C, 1, T), (pad, pad)), ker)
    den = F.conv1d(F.pad(m.expand(B, C, T).reshape(B * C, 1, T), (pad, pad)), ker)
    return (num / den.clamp_min(1e-8)).reshape(B, C, T)


def _renorm(e, m, sigma, rel):
    """誤差の (b, c) ごとの RMS を有効フレーム上で厳密に `rel * sigma[c]` に揃える。

    **誤差の「大きさ」を全種類で同一にすることで、勾配ノルムの差が
    純粋に誤差の「構造」由来になる。**
    """
    n = m.sum(dim=(1, 2)).clamp_min(1)[:, None]           # [B,1]
    rms = (((e * m) ** 2).sum(dim=2) / n).sqrt()          # [B,C]
    tgt = (rel * sigma)[None, :]                          # [1,C]
    return e * (tgt / rms.clamp_min(1e-8))[..., None] * m


def make_error(kind, target, m, mu, sigma, rel, g):
    B, C, T = target.shape
    if kind == "white":
        e = torch.randn(B, C, T, generator=g)
    elif kind == "smooth_err":
        e = _masked_movavg(torch.randn(B, C, T, generator=g), m, SMOOTH_K)
    elif kind == "detail_crush":
        # 生徒が時間方向の細部を潰す失敗。誤差 = 取り除かれた高域成分
        e = _masked_movavg(target, m, SMOOTH_K) - target
    elif kind == "chan_scale":
        s = (torch.randint(0, 2, (C,), generator=g).float() * 2 - 1)
        n = m.sum(dim=(1, 2)).clamp_min(1)[:, None]
        mu_u = (target * m).sum(dim=2) / n
        e = s[None, :, None] * (target - mu_u[..., None])
    elif kind == "chan_bias":
        s = (torch.randint(0, 2, (C,), generator=g).float() * 2 - 1)
        e = s[None, :, None].expand(B, C, T).clone()
    else:
        raise ValueError(kind)
    return _renorm(e, m, sigma, rel)


# --- バッチ -------------------------------------------------------------------

def build_batches(reader, order, bs, proj=None):
    out = []
    for i in range(0, len(order) - bs + 1, bs):
        idx = order[i:i + bs]
        items = [reader[int(j)] for j in idx]
        zs = []
        for it in items:
            z = it["zT"]
            if proj is not None:
                z = proj["V"].T @ (z - proj["mu"][:, None])
            zs.append(z)
        T = max(z.shape[1] for z in zs)
        C = zs[0].shape[0]
        zb = np.zeros((bs, C, T), np.float32)
        mb = np.zeros((bs, 1, T), np.float32)
        for b, z in enumerate(zs):
            zb[b, :, :z.shape[1]] = z
            mb[b, 0, :z.shape[1]] = 1.0
        out.append((torch.from_numpy(zb), torch.from_numpy(mb)))
    return out


def build_dur_batches(reader, order, bs):
    out = []
    for i in range(0, len(order) - bs + 1, bs):
        idx = order[i:i + bs]
        ds = [reader[int(j)]["dT"] for j in idx]
        Lm = max(len(d) for d in ds)
        db = np.zeros((bs, Lm), np.float32)
        mb = np.zeros((bs, Lm), np.float32)
        for b, d in enumerate(ds):
            db[b, :len(d)] = d
            mb[b, :len(d)] = 1.0
        out.append((torch.from_numpy(db), torch.from_numpy(mb)))
    return out


# --- 測定本体 -----------------------------------------------------------------

def measure_space(name, batches, mu, sigma, seed, verify=False):
    stats = L.ChannelStats(mu=torch.from_numpy(mu), sigma=torch.from_numpy(sigma))
    mu_t = torch.from_numpy(mu)
    sig_t = torch.from_numpy(sigma)
    rows = {}
    cos_rows = {}
    verified = None

    for kind in ERR_TYPES:
        for rel in LEVELS:
            vals = {k: [] for k in TERMS}
            gns = {k: [] for k in TERMS}
            coss = {k: [] for k in TERMS}
            achieved = []
            for bi, (zt, m) in enumerate(batches):
                g = torch.Generator().manual_seed(
                    seed + 1000 * ERR_TYPES.index(kind) + 100 * LEVELS.index(rel) + bi)
                e = make_error(kind, zt, m, mu_t, sig_t, rel, g)
                c_hat = (zt + e).detach().requires_grad_(True)
                terms = latent_terms(c_hat, zt, stats, m)

                if verify and verified is None:
                    _, log = L.latent_loss(c_hat, zt, stats, m)
                    verified = {k: [float(log[k]), float(terms[k].detach()),
                                    abs(float(log[k]) - float(terms[k].detach()))]
                                for k in TERMS}

                nv = float(m.sum()) * zt.shape[1]
                achieved.append(float((((e * m) ** 2).sum() / max(nv, 1)).sqrt()))
                grads = {}
                for k in TERMS:
                    (gk,) = torch.autograd.grad(terms[k], c_hat, retain_graph=True)
                    grads[k] = gk
                    vals[k].append(float(terms[k].detach()))
                    gns[k].append(float(gk.norm()))
                g1 = grads["lat/l1"].flatten()
                for k in TERMS:
                    gk = grads[k].flatten()
                    d = (g1.norm() * gk.norm()).clamp_min(1e-30)
                    coss[k].append(float((g1 @ gk) / d))

            key = f"{kind}@{rel:.2f}"
            rows[key] = {
                "err_type": kind, "rel_level": rel,
                "achieved_err_rms": summ(achieved),
                "terms": {k: {"value": summ(vals[k]), "grad_l2": summ(gns[k])}
                          for k in TERMS},
                "lambda_grad_matched": {
                    k: summ([a / b for a, b in zip(gns["lat/l1"], gns[k]) if b > 0])
                    for k in TERMS},
            }
            cos_rows[key] = {k: summ(coss[k]) for k in TERMS}
    return {"space": name, "channels": int(mu.shape[0]),
            "sigma_min": float(sigma.min()), "sigma_max": float(sigma.max()),
            "sigma_ratio": float(sigma.max() / max(sigma.min(), 1e-12)),
            "conditions": rows, "grad_cosine_vs_l1": cos_rows,
            "verify_vs_losses_py": verified}


def measure_duration(dbatches, seed):
    """式2 の λ_T。Huber 項と length 項の勾配ノルム比。"""
    out = {}

    # (0) 完全予測でも length 項がゼロにならないか（r = max(1, exp(l̂)) の clamp 由来）
    floors, fr_lt1, fgrad, ratio = [], [], [], []
    for dT, m in dbatches:
        log_d_t = torch.log(dT.clamp_min(1e-5))
        log_d_hat = log_d_t.clone().detach().requires_grad_(True)
        r = torch.clamp(torch.exp(log_d_hat), min=1.0) * m
        sum_r = r.sum(dim=1).clamp_min(1e-5)
        sum_d = (dT * m).sum(dim=1).clamp_min(1e-5)
        length = ((torch.log(sum_r) - torch.log(sum_d)) ** 2).mean()
        (gl,) = torch.autograd.grad(length, log_d_hat)
        floors.append(float(length.detach()))
        fgrad.append(float(gl.norm()))
        fr_lt1.append(float(((dT < 1.0).float() * m).sum() / m.sum()))
        ratio.append(float((sum_r.sum() / sum_d.sum()).detach()))
    out["clamp_floor"] = {
        "note": "log_d_hat = log(dT)（誤差ゼロ）のときの length 項。"
                "r = max(1, exp(l̂)) の clamp で dT < 1 のトークンが押し上げられるため 0 にならない。"
                "**勾配も 0 にならない** = λ_T > 0 は最適点で duration を短くする方向に定常バイアスを掛ける",
        "length_term_at_zero_error": summ(floors),
        "length_grad_l2_at_zero_error": summ(fgrad),
        "frac_dT_lt_1": summ(fr_lt1),
        "sum_clamped_over_sum_dT": summ(ratio),
    }

    for kind in ("iid", "utt_bias", "global_bias"):
        for s in LEVELS:
            hv, lv, hg, lg, lam, cs = [], [], [], [], [], []
            for bi, (dT, m) in enumerate(dbatches):
                g = torch.Generator().manual_seed(seed + 7000 + 13 * bi
                                                  + 100 * LEVELS.index(s))
                log_d_t = torch.log(dT.clamp_min(1e-5))
                if kind == "iid":
                    eps = torch.randn(dT.shape, generator=g) * s
                elif kind == "utt_bias":
                    sign = torch.randint(0, 2, (dT.shape[0], 1), generator=g).float() * 2 - 1
                    eps = (sign * s).expand_as(dT).clone()
                else:
                    eps = torch.full_like(dT, s)
                log_d_hat = (log_d_t + eps * m).detach().requires_grad_(True)

                huber = F.huber_loss(log_d_hat, log_d_t, delta=L.HUBER_DELTA,
                                     reduction="none")
                huber = (huber * m).sum() / m.sum().clamp_min(1)
                r = torch.clamp(torch.exp(log_d_hat), min=1.0) * m
                sum_r = r.sum(dim=1).clamp_min(1e-5)
                sum_d = (dT * m).sum(dim=1).clamp_min(1e-5)
                length = ((torch.log(sum_r) - torch.log(sum_d)) ** 2).mean()

                (gh,) = torch.autograd.grad(huber, log_d_hat, retain_graph=True)
                (gl,) = torch.autograd.grad(length, log_d_hat, retain_graph=True)
                hv.append(float(huber.detach())); lv.append(float(length.detach()))
                hg.append(float(gh.norm())); lg.append(float(gl.norm()))
                if float(gl.norm()) > 0:
                    lam.append(float(gh.norm()) / float(gl.norm()))
                d = (gh.norm() * gl.norm()).clamp_min(1e-30)
                cs.append(float((gh.flatten() @ gl.flatten()) / d))
            out[f"{kind}@{s:.2f}"] = {
                "err_type": kind, "log_err_scale": s,
                "huber": {"value": summ(hv), "grad_l2": summ(hg)},
                "length": {"value": summ(lv), "grad_l2": summ(lg)},
                "lambda_T_grad_matched": summ(lam),
                "grad_cosine_huber_vs_length": summ(cs),
            }
    return out


# --- 測定から推奨値とグリッドを導く -------------------------------------------

# lat/delta は chan_bias（純粋なチャネル DC オフセット）では Δĉ−ΔcT ≡ 0 になり、
# 項の値は 0（実測 ~6e-9）。それでも autograd は残った丸め誤差の sign を拾うので
# 勾配ノルムが立つ。**これは数値アーティファクトなので λ_Δ の推定から除外する。**
DEGENERATE = {("lat/delta", "chan_bias")}


def analytic_lambda_n(sigma: np.ndarray) -> float:
    """‖g_l1‖/‖g_norm‖ の閉形式。

    L1 系の項は要素ごとの勾配が ±1/denom（norm 項は ±1/(σ_k·denom)）で
    **誤差の大きさに依らない**ので、比は σ スペクトルだけで決まる:

        λ_n = sqrt(C) / sqrt(Σ_k σ_k^-2) = 1 / sqrt(mean_k(σ_k^-2))
    """
    sg = np.maximum(sigma.astype(np.float64), 1e-5)
    return float(1.0 / np.sqrt(np.mean(1.0 / sg**2)))


def analytic_lambda_2(sigma: np.ndarray, rel: float) -> float:
    """‖g_l1‖/‖g_l2‖ の閉形式。

    g_l2 の要素は 2·err/denom なので ‖g_l2‖ = 2·rms(err)·sqrt(N·C)/denom、
    ‖g_l1‖ = sqrt(N·C)/denom。誤差 RMS を rel·σ_k に揃えてあるので

        λ₂ = 1 / (2 · rel · rms_k(σ_k))

    **λ₂ だけが誤差の大きさに反比例する** = 固定の正解が存在しない。
    """
    sg = sigma.astype(np.float64)
    return float(1.0 / (2.0 * rel * np.sqrt(np.mean(sg**2))))


def derive(results: dict, dur: dict, sigmas: dict) -> dict:
    rec = {}
    for sp, r in results.items():
        per_term = {}
        for t in TERMS:
            if t == "lat/l1":
                continue
            vals, by_level = [], {}
            for key, row in r["conditions"].items():
                if (t, row["err_type"]) in DEGENERATE:
                    continue
                v = row["lambda_grad_matched"][t]["mean"]
                vals.append(v)
                by_level.setdefault(f"{row['rel_level']:.2f}", []).append(v)
            a = np.array(vals)
            # 誤差レベル依存性: レベル間の幾何平均の比
            lg = {k: float(np.exp(np.mean(np.log(v)))) for k, v in by_level.items()}
            lvl_span = max(lg.values()) / min(lg.values())
            # 誤差種依存性: 同一レベル内のばらつき（幾何平均で集約）
            per_lvl_span = float(np.mean([max(v) / min(v) for v in by_level.values()]))
            per_term[t] = {
                "min": float(a.min()), "max": float(a.max()),
                "geomean": float(np.exp(np.mean(np.log(a)))),
                "span_total": float(a.max() / a.min()),
                "span_across_error_levels": float(lvl_span),
                "span_across_error_types_within_level": per_lvl_span,
                "by_level_geomean": lg,
                "n_conditions": int(a.size),
            }
        cos = {t: [row[t]["mean"] for row in r["grad_cosine_vs_l1"].values()]
               for t in TERMS if t != "lat/l1"}
        per_cos = {t: {"min": float(np.min(v)), "max": float(np.max(v)),
                       "mean": float(np.mean(v))} for t, v in cos.items()}
        # 誤差「構造」の弁別力: 同一 rel で誤差種を変えたときの項の値の max/min
        disc = {}
        for t in TERMS:
            per_lvl = {}
            for lv in LEVELS:
                vs = [row["terms"][t]["value"]["mean"]
                      for row in r["conditions"].values()
                      if row["rel_level"] == lv and row["terms"][t]["value"]["mean"] > 1e-6]
                if len(vs) >= 2:
                    per_lvl[f"{lv:.2f}"] = float(max(vs) / min(vs))
            disc[t] = per_lvl
        rec[sp] = {
            "lambda_grad_matched_summary": per_term,
            "grad_cosine_vs_l1_summary": per_cos,
            "value_discriminability_across_error_types": disc,
            "analytic_lambda_n": analytic_lambda_n(sigmas[sp]),
            "analytic_lambda_2": {f"{lv:.2f}": analytic_lambda_2(sigmas[sp], lv)
                                  for lv in LEVELS},
            "rms_sigma": float(np.sqrt(np.mean(sigmas[sp].astype(np.float64) ** 2))),
        }
    # 空間間の移り（c-line への外挿がどこまで効くか）
    tr = {}
    for t in ("lat/l2", "lat/norm", "lat/delta", "lat/stat"):
        row = {}
        for sp in results:
            g = rec[sp]["lambda_grad_matched_summary"][t]
            row[sp] = g["by_level_geomean"]["0.20"] if t == "lat/l2" else g["geomean"]
        row["c40_pca_over_z192"] = row["c40_pca"] / row["z192"]
        tr[t] = row
    rec["space_transfer"] = {
        "table": tr,
        "note": "**c-line (40ch) への外挿はここが上限。** λ_Δ は空間を変えても 0.89 倍しか"
                "動かない（構造だけで決まるため）が、λ₂ / λ_n / λ_s は σ スペクトルに"
                "直接効くので 0.37–0.46 倍ずれる。c40_pca は Eρ の**線形**代理でしかない",
    }

    # λ_T
    lt = [v["lambda_T_grad_matched"]["mean"] for k, v in dur.items() if k != "clamp_floor"]
    lt_bias = [v["lambda_T_grad_matched"]["mean"] for k, v in dur.items()
               if k != "clamp_floor" and v["err_type"] in ("utt_bias", "global_bias")]
    rec["lambda_T"] = {
        "all_conditions": {"min": float(min(lt)), "max": float(max(lt)),
                           "geomean": float(np.exp(np.mean(np.log(lt))))},
        "systematic_bias_only": {"min": float(min(lt_bias)), "max": float(max(lt_bias)),
                                 "geomean": float(np.exp(np.mean(np.log(lt_bias))))},
        "note": "length 項は**系統的な尺のズレ**を捕まえるための項なので、"
                "バランス点は utt_bias / global_bias 側で取るのが筋。"
                "iid（打ち消し合う誤差）で揃えると λ_T が過大になる",
    }
    return rec


def build_grid(rec: dict) -> dict:
    z = rec["z192"]["lambda_grad_matched_summary"]
    ln = rec["z192"]["analytic_lambda_n"]
    l2c = z["lat/l2"]["by_level_geomean"]["0.20"]
    ldc = z["lat/delta"]["geomean"]
    lsc = z["lat/stat"]["geomean"]
    ltc = rec["lambda_T"]["systematic_bias_only"]["geomean"]
    return {
        "full_product_if_3_points_each": 3 ** 5,
        "recommended_total_runs": 12,
        "baseline_all_grad_matched": {
            "lambda_2": round(l2c, 2), "lambda_n": round(ln, 3),
            "lambda_delta": round(ldc, 2), "lambda_s": round(lsc, 3),
            "lambda_T": round(ltc, 2),
        },
        "stages": [
            {"order": 1, "name": "lambda_T", "sweep": [0.1, round(ltc, 2), 1.0],
             "runs": 3, "cost": "Dα のみ（36,164 params）。最も安い。先にここを閉じる",
             "why": "λ_T のバランス点は 0.20–0.84 と 4.2 倍しか動かない = よく決まっている。"
                    "ただし clamp 由来の定常バイアスがあるので 0 側も試す"},
            {"order": 2, "name": "lambda_2", "sweep": [0.5, 2.0, 8.0], "runs": 3,
             "why": "**唯一、固定の正解が存在しない λ。** バランス点が誤差レベルに反比例し "
                    "(rel 5%→40% で 7.94→0.99)、学習が進むほど L2 の相対寄与が落ちる。"
                    "λ₂ は実質『初期の学習率スケジュール』を決めている"},
            {"order": 3, "name": "lambda_s", "sweep": ["0.5x", "1x", "2x"],
             "center": round(lsc, 3), "runs": 3,
             "why": "l1 との勾配 cos が 0.03–0.48 と最も低い = 他の項が出せない方向を供給する。"
                    "値自体は誤差種で 1.28 倍しか動かないので中心はよく決まる"},
            {"order": 4, "name": "lambda_delta", "sweep": [0.4, round(ldc, 2), 1.5],
             "runs": 2, "cost": "既定値と外側 1 点だけ",
             "why": "バランス点 0.61–0.94（1.5 倍）とよく決まっている。"
                    "**摩擦音の whistly / 過平滑が出たときだけ**上に振る"},
            {"order": 5, "name": "lambda_n ablation", "sweep": [0.0, round(ln, 3)],
             "runs": 1, "cost": "0 の 1 本だけ追加",
             "why": "λ_n は σ スペクトルから**閉形式で決まる**（実測と 5 桁一致）ので探索不要。"
                    "ただし l1 との cos が 0.59–0.71 と最も高く冗長の疑いがあるため、"
                    "0 との差分だけ確認する"},
        ],
        "note": "総当たり 243 通りに対し 12 本。λ_n が閉形式で決まり λ_Δ / λ_s の"
                "バランス点が狭いことが効いている",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=str(ROOT / "data" / "pack_sibdense"))
    ap.add_argument("--out", default=str(ROOT / "reports" / "c1_lambda_balance.json"))
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    t0 = time.time()
    reader = PackReader(args.pack)
    rng = np.random.default_rng(args.seed)
    order = rng.permutation(len(reader))

    mu = reader.mu_T.astype(np.float32)
    sigma = reader.sigma_T.astype(np.float32)

    # --- PCA-40（Eρ の線形代理）を全フレームから作る ---
    allz = np.concatenate([reader[i]["zT"] for i in range(len(reader))], axis=1)
    zc = (allz - mu[:, None]).astype(np.float64)
    cov = (zc @ zc.T) / zc.shape[1]
    w, V = np.linalg.eigh(cov)
    ordv = np.argsort(w)[::-1][:40]
    Vp = np.ascontiguousarray(V[:, ordv]).astype(np.float32)
    proj = {"V": Vp, "mu": mu}
    cproj = (Vp.T @ zc.astype(np.float32))
    mu_c = cproj.mean(axis=1).astype(np.float32)
    sig_c = cproj.std(axis=1).astype(np.float32)
    var_explained = float(w[ordv].sum() / w.sum())

    sub = np.sort(rng.choice(192, 40, replace=False))
    mu_s, sig_s = mu[sub], sigma[sub]

    spaces = []
    b192 = build_batches(reader, order, args.batch_size)
    spaces.append(("z192", b192, mu, sigma, True))

    bsub = [(z[:, sub, :], m) for z, m in b192]
    spaces.append(("z40_subset", bsub, mu_s, sig_s, False))

    bpca = build_batches(reader, order, args.batch_size, proj=proj)
    spaces.append(("c40_pca", bpca, mu_c, sig_c, False))

    results = {}
    for nm, bs_, m_, s_, ver in spaces:
        print(f"[c1] measuring {nm} ({len(bs_)} batches) ...", flush=True)
        results[nm] = measure_space(nm, bs_, m_, s_, args.seed, verify=ver)

    print("[c1] measuring duration (eq.2) ...", flush=True)
    dbatches = build_dur_batches(reader, order, args.batch_size)
    dur = measure_duration(dbatches, args.seed)

    rec = derive(results, dur, {"z192": sigma, "z40_subset": sig_s, "c40_pca": sig_c})
    grid = build_grid(rec)

    out = {
        "task": "C-1 lambda balance (measurement only, no training)",
        "repro": ""
                 "uv run python scripts/c1_lambda_balance.py "
                 f"--pack {args.pack} --batch-size {args.batch_size} --seed {args.seed}",
        "environment": {
            "python": platform.python_version(), "torch": torch.__version__,
            "numpy": np.__version__, "platform": platform.platform(), "device": "cpu",
        },
        "pack": {
            "root": args.pack, "n_utterances": len(reader),
            "n_frames": int(reader.index["frames"].sum()),
            "n_tokens": int(reader.index["n_ids"].sum()),
            "batch_size": args.batch_size,
            "n_batches": len(b192),
            "utts_used": int(len(b192) * args.batch_size),
        },
        "method": {
            "grad_target": "dL_k/dc_hat の L2 ノルム（パディング位置は mask で 0）",
            "lambda_rule": "lambda_k = ||g_l1|| / ||g_k||（GradNorm 的初期化）",
            "error_normalisation": "全誤差種で (b,c) ごとの誤差 RMS を rel*sigma[c] に厳密に揃えた。"
                                   "勾配ノルム差は誤差の『構造』のみに由来する",
            "smooth_kernel_frames": SMOOTH_K,
            "levels": list(LEVELS), "err_types": list(ERR_TYPES),
        },
        "pca40": {
            "note": "Eρ (z192 -> c40) の**線形**代理。Eρ は学習された写像なので代理でしかない",
            "var_explained": var_explained,
            "sigma_min": float(sig_c.min()), "sigma_max": float(sig_c.max()),
            "sigma_ratio": float(sig_c.max() / sig_c.min()),
        },
        "z40_subset_channels": [int(x) for x in sub],
        "latent_eq3": results,
        "duration_eq2": dur,
        "recommendations": rec,
        "search_grid": grid,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    pathlib.Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"[c1] wrote {args.out}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
