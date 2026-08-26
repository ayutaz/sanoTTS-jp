"""論文 arXiv:2608.21378 の蒸留損失（式2 / 式3 / 式5 / 式6 / 式7）。

論文が値を公開しているのは `(λ_w, λ_S, λ_A, λ_F, λ_c) = (0.1, 0.5, 0.025, 0.25, 0.5)` だけ。
**`L_c` の `λ₂, λ_n, λ_Δ, λ_s` は論文本文に無い**（"the weighted sum implemented by
the trainer" としか書かれていない）。ここでは既定値を置くが、**チューニング対象**。
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

# 論文 §II-B で公開されている重み
LAMBDA_W = 0.1     # 波形 L1
LAMBDA_S = 0.5     # マルチ解像度 STFT
LAMBDA_A = 0.025   # LSGAN
LAMBDA_F = 0.25    # feature matching
LAMBDA_C = 0.5     # joint の c アンカー（式6）

# ⚠️ 論文に値が無い。**「全部 1.0」は実測で明確に誤り**だったので、
# `data/pack_sibdense` の実 zT に対する**勾配ノルムを l1 項に揃える値**に置き換えた（C-1）。
# 依然としてチューニング対象だが、出発点は 1.0 ではない。
LAMBDA_2 = 0.40    # L2 項。⚠️ 下記 `lambda_2_from_residual()` で実行時に出すのが正しい
LAMBDA_N = 0.272   # チャネル正規化項。⚠️ σ_T 依存。`ChannelStats.lambda_n` を使うこと
LAMBDA_D = 0.86    # 一次差分項。生徒の残差は時間的に滑らかなので白色雑音仮定の 0.77 より上
LAMBDA_STAT = 0.19 # 統計項

LAMBDA_T = 0.27    # 発話長保存項（式2）。これも論文に値が無い

# 論文 §II-B: FFT {512, 1024, 2048} × hop {128, 256, 512}
STFT_RESOLUTIONS: tuple[tuple[int, int], ...] = ((512, 128), (1024, 256), (2048, 512))

HUBER_DELTA = 0.25  # 式2 の Huber_0.25


def duration_loss(
    log_d_hat: torch.Tensor,
    d_target: torch.Tensor,
    mask: torch.Tensor | None = None,
    lambda_t: float = LAMBDA_T,
    length_target: torch.Tensor | None = None,
) -> tuple[torch.Tensor, dict]:
    """式2: `L_d = Huber_0.25(l̂, log dT) + λ_T [log Σ r_i − log Σ dT_i]²`

    第 2 項は**発話長の保存**。トークンごとの誤差が打ち消し合っても、
    総尺がずれれば罰する。モーラ等時性のある日本語では効きやすいはず。

    Parameters
    ----------
    log_d_hat : [B, L]  生徒の出力（log duration）
    d_target  : [B, L]  教師の `dT`（**log ではない**）
    mask      : [B, L]  1 = 有効
    length_target : [B] 発話長保存項のターゲットを明示する場合。省略すると
        `Σ max(1, dT)`（`r` と同じ関数を教師にも掛けた自己整合な値）を使う。
        デプロイの展開規則を `ceil` にするなら `Σ ceil(dT)` を渡すこと。
    """
    log_d_t = torch.log(d_target.clamp_min(1e-5))
    if mask is None:
        mask = torch.ones_like(d_target)

    huber = F.huber_loss(log_d_hat, log_d_t, delta=HUBER_DELTA, reduction="none")
    huber = (huber * mask).sum() / mask.sum().clamp_min(1)

    # r_i = max(1, exp(l̂_i))。論文のデプロイ式と揃える
    r = torch.clamp(torch.exp(log_d_hat), min=1.0) * mask
    sum_r = r.sum(dim=1).clamp_min(1e-5)

    # ⚠️ **ターゲットを `Σ dT` にしてはいけない**（論文の字面はそうなっている）。
    # 教師の `dT` は 18.06% のトークンが 1 未満で、`r` の側だけ max(1,·) が掛かるため
    # `Σ max(1, dT) / Σ dT = 1.044`。**完璧な生徒でも length 項が 0 にならず**、
    # λ_T > 0 が発話を 4.41% 短くする定常バイアスになる（C-1 の実測）。
    # `r` と同じ関数を教師にも掛けて自己整合させる。
    if length_target is None:
        sum_d = (torch.clamp(d_target, min=1.0) * mask).sum(dim=1)
    else:
        sum_d = length_target          # 例: 教師の実フレーム数 Σceil(dT)
    sum_d = sum_d.clamp_min(1e-5)
    length_term = ((torch.log(sum_r) - torch.log(sum_d)) ** 2).mean()

    total = huber + lambda_t * length_term
    return total, {"dur/huber": huber.detach(), "dur/length": length_term.detach()}


@dataclass(frozen=True)
class ChannelStats:
    """教師パックのチャネル統計。式3 の `N_T` と式7 の `σT_k` で使う。"""

    mu: torch.Tensor     # [C]
    sigma: torch.Tensor  # [C]

    def normalize(self, x: torch.Tensor) -> torch.Tensor:
        """`N_T(u) = (u − μ_T) / σ_T`。x は [B, C, T]。"""
        return (x - self.mu[None, :, None]) / self.sigma[None, :, None].clamp_min(1e-5)

    @property
    def lambda_n(self) -> float:
        """`λ_n` の閉形式 `1/√(mean(1/σ_k²))`（σ の調和二乗平均）。

        正規化項の勾配は要素ごとに `1/σ_k` なので、`‖grad‖₂` を l1 項の `√N` に
        揃えると λ_n がこれになる。**σ に依存するのでパックごとに変わる**
        （z-line で 0.2717、c-line や別パックでは 0.37〜0.46 倍ずれた）。
        **定数で焼かずにここから取ること**（C-1）。
        """
        sig = self.sigma.clamp_min(1e-5)
        return float(1.0 / torch.sqrt(torch.mean(1.0 / sig**2)))


def latent_loss(
    c_hat: torch.Tensor,
    c_target: torch.Tensor,
    stats: ChannelStats,
    mask: torch.Tensor | None = None,
    lambda_2: float | None = None,
    lambda_n: float | None = None,
) -> tuple[torch.Tensor, dict]:
    """式3: `L_c = ‖ĉ−cT‖₁ + λ₂‖·‖₂² + λ_n‖N_T(ĉ)−N_T(cT)‖₁ + λ_Δ‖Δĉ−ΔcT‖₁ + λ_s L_stat`

    チャネル正規化項の役割は**高分散チャネルが目的関数を支配するのを防ぐ**こと。
    教師の潜在はチャネルごとに σ が 0.042〜10.14（比 241.8 倍）と大きく偏っている（A-3）。

    ⚠️ `λ₂, λ_n, λ_Δ, λ_s` は論文に値が無い。**「全部 1.0」は誤り**で、
    実 zT に対する勾配ノルムを l1 項に揃えると λ₂≈0.40（初期）/ λ_n=0.272 /
    λ_Δ=0.86 / λ_s=0.19 になる（C-1）。うち **λ_n と λ₂ は閉形式があるので
    探索不要**（λ_n は σ_T から、λ₂ は残差 RMS から実行時に出す）。
    残る探索対象は λ_Δ と λ_s。
    """
    if mask is None:
        mask = torch.ones_like(c_hat[:, :1, :])
    m = mask  # [B, 1, T]
    denom = (m.sum() * c_hat.shape[1]).clamp_min(1)

    l1 = ((c_hat - c_target).abs() * m).sum() / denom
    l2 = (((c_hat - c_target) ** 2) * m).sum() / denom
    ln = ((stats.normalize(c_hat) - stats.normalize(c_target)).abs() * m).sum() / denom

    # 一次差分（時間方向）。フレーム間の動きを合わせる
    d_hat = c_hat[..., 1:] - c_hat[..., :-1]
    d_tgt = c_target[..., 1:] - c_target[..., :-1]
    md = m[..., 1:]
    ld = ((d_hat - d_tgt).abs() * md).sum() / (md.sum() * c_hat.shape[1]).clamp_min(1)

    # L_stat: 発話内のチャネル平均・分散を σ_T で正規化して合わせる
    n = m.sum(dim=(1, 2)).clamp_min(1)[:, None]
    mu_h = (c_hat * m).sum(dim=2) / n
    mu_t = (c_target * m).sum(dim=2) / n
    var_h = ((c_hat - mu_h[..., None]) ** 2 * m).sum(dim=2) / n
    var_t = ((c_target - mu_t[..., None]) ** 2 * m).sum(dim=2) / n
    sig = stats.sigma[None, :].clamp_min(1e-5)
    l_stat = (((mu_h - mu_t) / sig).abs().mean()
              + ((var_h.sqrt() - var_t.sqrt()) / sig).abs().mean())

    # λ₂ は**固定値にしない**。勾配を l1 に揃える値は `1/(2·RMS(残差))` で、
    # 学習が進んで残差が小さくなるほど**大きく**なる（C-1 の照合で符号が確定）。
    # 実行時に残差から出すと `λ₂·‖·‖₂² = ½·RMS(残差)` になり、L2 が実質 RMS 項になる。
    # `lambda_2` を明示すればその固定値を使う（探索用）。
    if lambda_2 is None:
        lam2 = 0.5 / l2.detach().clamp_min(1e-12).sqrt()
    else:
        lam2 = torch.as_tensor(float(lambda_2), device=l2.device, dtype=l2.dtype)
    lam_n = stats.lambda_n if lambda_n is None else float(lambda_n)

    total = l1 + lam2 * l2 + lam_n * ln + LAMBDA_D * ld + LAMBDA_STAT * l_stat
    return total, {
        "lat/l1": l1.detach(), "lat/l2": l2.detach(), "lat/norm": ln.detach(),
        "lat/delta": ld.detach(), "lat/stat": l_stat.detach(),
        "lat/lambda_2": lam2.detach(),
        "lat/lambda_n": torch.as_tensor(lam_n),
    }


def multi_resolution_stft_loss(
    y_hat: torch.Tensor, y_target: torch.Tensor,
    resolutions: tuple[tuple[int, int], ...] = STFT_RESOLUTIONS,
) -> torch.Tensor:
    """式5 の `ℓ_{n,h} = ‖log(1+|S(ŷ)|) − log(1+|S(yT)|)‖₁` の平均。"""
    total = y_hat.new_zeros(())
    for n_fft, hop in resolutions:
        win = torch.hann_window(n_fft, device=y_hat.device, dtype=y_hat.dtype)
        kw = dict(n_fft=n_fft, hop_length=hop, win_length=n_fft,
                  window=win, return_complex=True, center=True)
        s_hat = torch.stft(y_hat, **kw).abs()
        s_tgt = torch.stft(y_target, **kw).abs()
        total = total + (torch.log1p(s_hat) - torch.log1p(s_tgt)).abs().mean()
    return total / len(resolutions)


def generator_loss(
    y_hat: torch.Tensor, y_target: torch.Tensor,
    disc_fake: torch.Tensor | None = None,
    feats_fake: list[torch.Tensor] | None = None,
    feats_real: list[torch.Tensor] | None = None,
) -> tuple[torch.Tensor, dict]:
    """式5: `L_G = λ_w‖ŷ−yT‖₁ + (λ_S/|R|)Σℓ + λ_A L_adv + λ_F L_FM`

    `L_adv` は LSGAN（Mao et al. 2017）。判別器は**一次差分 `Δŷ`** に対して掛ける。
    """
    l1 = (y_hat - y_target).abs().mean()
    stft = multi_resolution_stft_loss(y_hat, y_target)
    total = LAMBDA_W * l1 + LAMBDA_S * stft
    log = {"gen/l1": l1.detach(), "gen/stft": stft.detach()}

    if disc_fake is not None:
        adv = ((disc_fake - 1.0) ** 2).mean()
        total = total + LAMBDA_A * adv
        log["gen/adv"] = adv.detach()
    if feats_fake and feats_real:
        fm = sum((a - b).abs().mean() for a, b in zip(feats_fake, feats_real, strict=True))
        fm = fm / len(feats_fake)
        total = total + LAMBDA_F * fm
        log["gen/fm"] = fm.detach()
    return total, log


def discriminator_loss(
    disc_real: torch.Tensor, disc_fake: torch.Tensor
) -> tuple[torch.Tensor, dict]:
    """LSGAN の判別器損失。"""
    real = ((disc_real - 1.0) ** 2).mean()
    fake = (disc_fake**2).mean()
    return real + fake, {"disc/real": real.detach(), "disc/fake": fake.detach()}


def joint_loss(
    y_hat: torch.Tensor, y_target: torch.Tensor,
    c_hat: torch.Tensor, c_target: torch.Tensor,
    disc_fake: torch.Tensor | None = None,
    feats_fake: list[torch.Tensor] | None = None,
    feats_real: list[torch.Tensor] | None = None,
) -> tuple[torch.Tensor, dict]:
    """式6: `L_joint = L_G(ŷ, yT) + λ_c ‖Aβ(x, d̂) − cT‖₁`

    最終段で acoustic と decoder を同時に更新する。第 2 項が**インターフェースを
    固定しつつ、decoder が acoustic の誤差に適応する**のを許す。
    """
    g, log = generator_loss(y_hat, y_target, disc_fake, feats_fake, feats_real)
    anchor = (c_hat - c_target).abs().mean()
    log["joint/anchor"] = anchor.detach()
    return g + LAMBDA_C * anchor, log


# --- 式7: 推論時の摩擦音ノイズ注入 -------------------------------------------

S_JA = ("s", "sh", "ts", "ch", "z", "j", "h", "hy", "f", "I", "U")
"""日本語版のノイズ注入対象（CLAUDE.md）。

論文の英語版は `{s, sh, z, zh}`。日本語は摩擦音が多く、**無声化母音 `I` `U` が
音響的にほぼ摩擦雑音**なので加えた。`A` `E` `O` はコーパス 23,271 行で
1 度も出現しないため除外（C-004）。
"""


def inject_fricative_noise(
    c: torch.Tensor, is_fricative: torch.Tensor,
    sigma_t: torch.Tensor, beta: float,
    generator: torch.Generator | None = None,
) -> torch.Tensor:
    """式7: `z̃_{t,k} = ẑ_{t,k} + 1[x_t ∈ S] · β · σT_k · ε_{t,k}`

    acoustic student が確率的な摩擦を平滑化してしまうのを、推論時に補う。
    論文では `β = 6` を**聴取で**選んだ。集約スコア（SCOREQ）はむしろ下がる
    （4.09 → 3.92）ので、**指標で決めてはいけない**。

    Parameters
    ----------
    c            : [B, C, T]  acoustic の出力
    is_fricative : [B, T]     1 = そのフレームが `S_JA` の音素に属する
    sigma_t      : [C]        教師パックのチャネル別 σ
    """
    if beta == 0:
        return c
    noise = torch.randn(c.shape, device=c.device, dtype=c.dtype, generator=generator)
    scale = beta * sigma_t[None, :, None] * is_fricative[:, None, :]
    return c + scale * noise
