"""A-3-2: zT / yT の量子化が L_c と L_G にどれだけ効くかを実測する。

L_c = ||c-cT||_1 + λ2||c-cT||_2^2 + λn||N_T(c)-N_T(cT)||_1
      + λΔ||Δc-ΔcT||_1 + λs L_stat            (論文 式3)
λ 群は論文に値が無いので **項別に報告**する（合成値は λ=1 の参考値）。
"""
import json, numpy as np

RAW = "raw"
ix = json.load(open(f"{RAW}/index.json"))["rows"]
SEQ = [r["seq"] for r in ix]

# ---- チャネル統計 μ_T / σ_T を全サンプルの 1 パスで集計（Welford 不要、二次モーメント） -----
n = 0; s1 = np.zeros(192, np.float64); s2 = np.zeros(192, np.float64)
for q in SEQ:
    z = np.load(f"{RAW}/{q:06d}.npz")["zT"].astype(np.float64)
    n += z.shape[1]; s1 += z.sum(1); s2 += (z * z).sum(1)
mu = s1 / n
sd = np.sqrt(np.maximum(s2 / n - mu * mu, 0))
print(f"channel stats: n_frames={n}  sigma min {sd.min():.4f} max {sd.max():.4f} ratio {sd.max()/sd.min():.1f}x")
np.savez("channel_stats.npz", mu_T=mu.astype(np.float32), sigma_T=sd.astype(np.float32), n_frames=n)

def per_channel_int(z, bits):
    """対称 per-channel 量子化 (scale は fp32 で別途保存)。"""
    qmax = 2 ** (bits - 1) - 1
    scale = np.abs(z).max(1, keepdims=True) / qmax
    scale[scale == 0] = 1
    q = np.rint(z / scale).clip(-qmax, qmax)
    return (q * scale).astype(np.float32), scale.size * 4

CODECS = {
    "fp16":  lambda z: (z.astype(np.float16).astype(np.float32), 0),
    "bf16":  lambda z: ((z.view(np.uint32) & np.uint32(0xFFFF0000)).view(np.float32), 0),  # 切り捨て
    "int16_pc": lambda z: per_channel_int(z, 16),
    "int8_pc":  lambda z: per_channel_int(z, 8),
}
BYTES_PER_ELEM = {"fp16": 2, "bf16": 2, "int16_pc": 2, "int8_pc": 1}

def Lc_terms(c, ct, mu, sd):
    d = c - ct
    l1 = np.abs(d).mean()
    l2 = (d * d).mean()
    ln = np.abs(d / sd[:, None]).mean()
    dl = np.abs(np.diff(c, axis=1) - np.diff(ct, axis=1)).mean()
    ls = (np.abs((c.mean(1) - ct.mean(1)) / sd).mean()
          + np.abs((c.std(1) - ct.std(1)) / sd).mean())
    return dict(l1=l1, l2=l2, ln=ln, ldelta=dl, lstat=ls)

# ---- 1) 量子化のみで生じる誤差（= 生徒が到達できる下限） --------------------------------
rows = {}
for name, fn in CODECS.items():
    acc = {k: 0.0 for k in ["l1", "l2", "ln", "ldelta", "lstat"]}
    rel = []
    for q in SEQ:
        z = np.load(f"{RAW}/{q:06d}.npz")["zT"]
        zq, _ = fn(z)
        t = Lc_terms(zq.astype(np.float64), z.astype(np.float64), mu, sd)
        for k in acc: acc[k] += t[k]
        rel.append(np.sqrt(((zq - z) ** 2).mean(1)) / sd)   # per-channel RMS / sigma
    for k in acc: acc[k] /= len(SEQ)
    rel = np.concatenate(rel)
    rows[name] = dict(bytes_per_elem=BYTES_PER_ELEM[name],
                      eta_q_rms_over_sigma_mean=float(rel.mean()),
                      eta_q_rms_over_sigma_max=float(rel.max()), **{k: float(v) for k, v in acc.items()})
    print(f"{name:9s} B/elem={BYTES_PER_ELEM[name]}  eta_q(RMS/sigma) mean {rel.mean():.3e} max {rel.max():.3e}"
          f"  L1 {acc['l1']:.3e}  L2 {acc['l2']:.3e}  Ln {acc['ln']:.3e}  Ldelta {acc['ldelta']:.3e}  Lstat {acc['lstat']:.3e}")

# ---- 2) 生徒残差 η を仮定したとき、ターゲットを fp16 にすると L_c がどれだけ変わるか ----
rng = np.random.default_rng(0)
sweep = []
for eta in [0.003, 0.01, 0.03, 0.1, 0.3]:
    a32 = {k: 0.0 for k in ["l1", "l2", "ln", "ldelta", "lstat"]}
    a16 = {k: 0.0 for k in ["l1", "l2", "ln", "ldelta", "lstat"]}
    for q in SEQ[:32]:
        z = np.load(f"{RAW}/{q:06d}.npz")["zT"].astype(np.float64)
        z16 = z.astype(np.float16).astype(np.float64)
        stu = z + eta * sd[:, None] * rng.standard_normal(z.shape)
        t32 = Lc_terms(stu, z, mu, sd); t16 = Lc_terms(stu, z16, mu, sd)
        for k in a32: a32[k] += t32[k]; a16[k] += t16[k]
    m = len(SEQ[:32])
    e = {k: (a16[k] - a32[k]) / a32[k] for k in a32}
    sweep.append(dict(eta=eta, **{f"rel_delta_{k}": float(v) for k, v in e.items()}))
    print(f"eta={eta:<6} L_c 項の相対変化 (fp16 target vs fp32 target): "
          + "  ".join(f"{k} {v:+.2e}" for k, v in e.items()))

json.dump(dict(channel_sigma_min=float(sd.min()), channel_sigma_max=float(sd.max()),
               n_frames=int(n), codecs=rows, student_sweep=sweep),
          open("quant_zT.json", "w"), indent=1)
