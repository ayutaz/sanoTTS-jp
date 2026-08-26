"""一次差分に対する判別器（式5 の `L_adv` / `L_FM` 用）。

⚠️ **論文 arXiv:2608.21378 が指定しているのは「判別器を波形の一次差分 `Δŷ` に
掛ける」という 1 点だけ**である。層構成・チャネル数・スケール数・正規化・
損失の集約方法はいずれも本文に書かれていない。以下は**本実装の仮定**であり、
論文の再現ではない。後から差し替え・検証できるようにここに列挙する。

論文が指定していること（守っているもの）
    P1. 判別器の入力は波形の**一次差分** `Δy[t] = y[t] − y[t−1]`
    P2. `L_adv` は LSGAN (Mao et al. 2017) — 実装は `losses.py` 側
    P3. `λ_A = 0.025` / `λ_F = 0.25`（feature matching のほうが 10 倍重い）

論文に無い＝本実装の仮定（A1〜A9）
    A1. HiFi-GAN の MSD (Multi-Scale Discriminator) 相当のマルチスケール構成。
        スケール間は `AvgPool1d(4, stride=2)` で 1 段ずつ帯域を落とす。
        スケール数は既定 3（`Δy`, /2, /4）。
    A2. サブ判別器 1 本は grouped conv の 4 層 + 出力 conv 1 層。層仕様は
        `LAYER_PLAN`。HiFi-GAN の (15,41,41,41,41,5) を 4 層に詰めた縮小版。
    A3. **チャネル幅は生徒 (567,008 params) に合わせて大きく削る。** 既定は
        `base_channels=16` で判別器全体 **94,755 params = 生徒の 0.167 倍**
        （実測）。層構成は HiFi-GAN の MSD 由来だがチャネル幅を大きく削って
        いる。判別器が生徒を潰さないための選択。
        ⚠️ HiFi-GAN 本家の MSD のパラメータ数は**本プロジェクトでは未測定**
        なので、倍率の比較は書かない。
    A4. **正規化は spectral_norm**。⚠️ 3 種 (spectral/weight/none) に
        **測れた差は無い**（下の「正規化の選択」節）。理屈で選んだだけ。
    A5. 活性化は LeakyReLU(0.1)（HiFi-GAN と同じ）。
    A6. 出力は**全スケールを平坦化して 1 本のテンソルに連結**する。
        `losses.generator_loss` / `discriminator_loss` が単一テンソルを
        受け取る形なので、そこに嵌めるための措置。
        ⚠️ この連結により **LSGAN の平均は要素数の比でスケールに重み付く**
        （既定 3 スケールで概ね 4 : 2 : 1、最細スケールが約 57%）。
        スケール等重みにしたい場合は `forward_scales()` を使って
        呼び出し側で平均すること。
    A7. feature matching に返す中間特徴は**全スケールの全隠れ層の活性化後**
        （出力 conv は含まない）。既定 3 スケール × 4 層 = 12 本。
    A8. `Δy` を取ると系列長が 1 減る（8192 → 8191）。パディングで戻さない。
    A9. 一次差分は**モジュール内**で取る。呼び出し側は生波形 `[B, T]` または
        `[B, 1, T]` を渡せばよい。real / fake で差分の取り方がずれる事故を
        構造的に防ぐため。

正規化の選択: **spectral_norm**（`weight_norm` ではない）— ⚠️ 実測の裏付けは無い
    判断の根拠（測っていないものは「未測定」と書く）:
    - 入力が `Δy` = ハイパスされた波形で、実測 RMS は `yT` の 0.0533 に対して
      **0.0128（1/4.2）**。振幅が小さいぶん判別器のゲインが効きやすい。
      spectral_norm は各層の Lipschitz 定数を抑えるので、判別器が 567 K の
      生徒を一気に追い越すのを防げる（**この効果自体は未測定**）。
    - `λ_F = 0.25` は `λ_A = 0.025` の 10 倍で、**この判別器の主な仕事は
      feature matching の特徴量を供給すること**。weight_norm は特徴量の
      スケールが学習中に自由に伸びるため `L_FM` の大きさが非定常になる。
      spectral_norm は特徴量のノルムを有界に保つ。
    - HiFi-GAN の MSD は「生波形の 1 本目だけ spectral_norm、残りは
      weight_norm」だが、ここは**全スケールが差分入力**なので統一した。

    ⚠️ **実測したコスト**（`reports/c2_discriminator.json` の `norm_comparison`）:
    教師パックの実波形 vs RMS 一致白色雑音を D だけ 200 step 学習し、
    held-out 24 発話での分離幅 `D(real) − D(noise)` を **5 seed** で比較:

        spectral  0.543 ± 0.074 (n=5)
        weight    0.635 ± 0.049 (n=5)
        none      0.632 ± 0.051 (n=5)

    ⚠️ **この比較は当てにならない。独立照合で消えた。** 別の split・別の 5 seed で
    測り直すと spectral 0.646 ± 0.057 / weight 0.656 ± 0.047 で **p=0.77**。
    さらに上の表は seed が対応しているので Welch ではなく対応のある t 検定を使うべきで、
    そうすると p=0.0050 になる（検定の選び方も誤っていた）。
    **結論: 正規化の 3 種に測れた差は無い。** 「spectral が遅い」を根拠に使わないこと。

    加えて、この分離プローブ自体が**実質 Δ 振幅の検出器**だった: fake の Δ-RMS は
    real の 6.0 倍で、Δ-RMS 単独で AUC 0.922 に達する。Δ-RMS を揃えた fake では
    held-out acc が 0.500（偶然水準）、gap は +0.590 → +0.030 に落ちる。
    **プローブを Δ ドメインで RMS 一致させる版に直してから測り直すこと。**

    60 step では **3 種とも gap < 0.03** で、立ち上がりに 100〜200 step かかるのは共通
    （これは 3 種で一致しているので信用してよい）。
    ⚠️ **spectral_norm の利点（本学習での安定性）は未測定。** 既定を spectral に
    しているのは Lipschitz 制約という**理屈**からで、実測の裏付けは無い。
    本学習で `L_adv` が発散する／生徒が潰れる兆候が出たら
    `norm="weight"` との比較をやり直すこと。

    ⚠️ **副作用**: spectral_norm は forward のたびに power iteration で
    `u`/`v` バッファを更新する。同じ入力を 2 回通しても train モードでは
    bit 一致しない。決定性が要る検証では `disc.eval()` にすること。

インターフェース（`losses.py` にそのまま嵌る）:

    disc = FirstDifferenceDiscriminator()
    d_fake, f_fake = disc(y_hat)
    with torch.no_grad():
        d_real, f_real = disc(y)
    loss_g, log = generator_loss(y_hat, y, d_fake, f_fake, f_real)
    loss_d, log = discriminator_loss(disc(y)[0], disc(y_hat.detach())[0])

**判別器は学習専用でデプロイされない。** 567,008 params の勘定には入らない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.parametrizations import spectral_norm, weight_norm

# (kernel, stride, groups, channel_multiplier)
# A2: HiFi-GAN MSD の 6 conv 層を 4 層に詰めたもの。stride 4×4 = 16 分の 1 に落ちる。
LAYER_PLAN: tuple[tuple[int, int, int, int], ...] = (
    (15, 1, 1, 1),
    (41, 4, 4, 2),
    (41, 4, 16, 4),
    (5, 1, 1, 4),
)


@dataclass(frozen=True)
class DiscConfig:
    """判別器の構成。**既定値は「小」**（A3）。"""

    scales: int = 3
    base_channels: int = 16
    layer_plan: tuple[tuple[int, int, int, int], ...] = field(default=LAYER_PLAN)
    norm: str = "spectral"          # "spectral" | "weight" | "none"
    lrelu_slope: float = 0.1
    pool_kernel: int = 4            # A1: スケール間のダウンサンプル
    pool_stride: int = 2

    def name(self) -> str:
        return f"scales{self.scales}_base{self.base_channels}_{self.norm}"


SMALL = DiscConfig()                                        # 既定
LARGE = DiscConfig(scales=4, base_channels=32)              # 「大」比較用


def _norm(module: nn.Module, kind: str) -> nn.Module:
    if kind == "spectral":
        return spectral_norm(module)
    if kind == "weight":
        return weight_norm(module)
    if kind == "none":
        return module
    raise ValueError(f"norm は spectral / weight / none のいずれか: {kind!r}")


class ScaleDiscriminator(nn.Module):
    """1 スケールぶんのサブ判別器。入力は**すでに差分済み**の `[B, 1, T]`。"""

    def __init__(self, cfg: DiscConfig = SMALL) -> None:
        super().__init__()
        self.cfg = cfg
        convs, cin = [], 1
        for k, s, g, mult in cfg.layer_plan:
            cout = cfg.base_channels * mult
            if cin % g or cout % g:      # groups は両側を割り切る必要がある
                g = 1
            convs.append(_norm(
                nn.Conv1d(cin, cout, k, stride=s, padding=k // 2, groups=g), cfg.norm))
            cin = cout
        self.convs = nn.ModuleList(convs)
        self.post = _norm(nn.Conv1d(cin, 1, 3, padding=1), cfg.norm)

    def forward(self, dy: torch.Tensor) -> tuple[torch.Tensor, list[torch.Tensor]]:
        feats: list[torch.Tensor] = []
        h = dy
        for conv in self.convs:
            h = F.leaky_relu(conv(h), self.cfg.lrelu_slope)
            feats.append(h)          # A7: 活性化後を返す
        return self.post(h), feats


class FirstDifferenceDiscriminator(nn.Module):
    """論文 §II-B の "first-difference discriminator"。

    生波形 `[B, T]` または `[B, 1, T]` を受け取り、**内部で一次差分を取ってから**
    マルチスケールのサブ判別器に通す（A9）。

    Returns
    -------
    out   : [B, N]  全スケールの出力を平坦化して連結（A6）
    feats : list[Tensor]  全スケールの全隠れ層（A7）。`scales * len(layer_plan)` 本
    """

    def __init__(self, cfg: DiscConfig = SMALL) -> None:
        super().__init__()
        self.cfg = cfg
        self.subs = nn.ModuleList([ScaleDiscriminator(cfg) for _ in range(cfg.scales)])
        self.pool = nn.AvgPool1d(cfg.pool_kernel, stride=cfg.pool_stride,
                                 padding=cfg.pool_kernel // 2, count_include_pad=False)

    @staticmethod
    def first_difference(wav: torch.Tensor) -> torch.Tensor:
        """`Δy[t] = y[t] − y[t−1]`。`[B, T]` / `[B, 1, T]` → `[B, 1, T-1]`（P1 / A8）。"""
        if wav.dim() == 2:
            wav = wav.unsqueeze(1)
        elif wav.dim() != 3 or wav.shape[1] != 1:
            raise ValueError(f"波形は [B, T] か [B, 1, T]: {tuple(wav.shape)}")
        return torch.diff(wav, dim=-1)

    def forward_scales(
        self, wav: torch.Tensor
    ) -> list[tuple[torch.Tensor, list[torch.Tensor]]]:
        """スケールごとに `(out, feats)` を返す。等重みで集約したいとき用（A6）。"""
        h = self.first_difference(wav)
        out = []
        for i, sub in enumerate(self.subs):
            if i:
                h = self.pool(h)
            out.append(sub(h))
        return out

    def forward(self, wav: torch.Tensor) -> tuple[torch.Tensor, list[torch.Tensor]]:
        per_scale = self.forward_scales(wav)
        outs = torch.cat([o.flatten(1) for o, _ in per_scale], dim=1)
        feats = [f for _, fs in per_scale for f in fs]
        return outs, feats


def count_parameters(module: nn.Module) -> int:
    """spectral_norm / weight_norm の再パラメータ化を二重に数えない。"""
    return sum(p.numel() for p in module.parameters())


def build(preset: str = "small") -> FirstDifferenceDiscriminator:
    cfg = {"small": SMALL, "large": LARGE}[preset]
    return FirstDifferenceDiscriminator(cfg)
