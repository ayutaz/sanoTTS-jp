"""saanoTTS (arXiv:2608.21378) Table I のパラメータ数を再現する層構成。
   torch: /Users/s19447/Documents/piper-plus/.venv/bin/python で検証済み。
   D 36,164 / A 199,536 / G 331,308 / 合計 567,008 / E_rho 14,952 すべて delta 0。"""
import torch, torch.nn as nn, torch.nn.functional as F
N = lambda m: sum(p.numel() for p in m.parameters())
# 論文（英語）の deployed acoustic vocabulary は 157 entries。
# **日本語は 57。** `kana_g2p` の mora テーブル 195 エントリと `ん` の異音規則から
# 原理的に出せる音素の閉包が 57 で、コーパス 23,454 行の出現 51 を厳密に含む（B-9）。
# 157 → 57 で Dα −3,200 / Aβ −4,800 / 合計 567,008 → **559,008**。
# ⚠️ **MMAC は 1 も減らない**（埋め込みは表引きで 0 MAC）。浮くのは flash 8 KB だけ。
try:                                        # パッケージとして import されたとき
    from .vocab import V as VOCAB           # = 57。B-9 で凍結
except ImportError:                         # このファイルを直接実行したとき
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from saanotts_jp.vocab import V as VOCAB
VOCAB_PAPER_EN = 157   # 論文 Table I の再現用（下の __main__ が両方で検算する）

# ---------- E_rho : 学習専用 z(192ch) -> c(40ch)   14,952  (一意解) ----------
class Erho(nn.Module):
    def __init__(s):
        super().__init__(); s.l1 = nn.Conv1d(192, 64, 1); s.l2 = nn.Conv1d(64, 40, 1)
    def forward(s, z): return s.l2(torch.tanh(s.l1(z)))

# ---------- D_alpha : 音素ID -> log duration   36,164 ----------
class DurBlock(nn.Module):                      # kernel-5 residual block
    def __init__(s, w=32, k=5):
        super().__init__()
        s.c1 = nn.Conv1d(w, w, k, padding=k//2); s.c2 = nn.Conv1d(w, w, k, padding=k//2)
        s.norm = nn.LayerNorm(w); s.gamma = nn.Parameter(torch.ones(1))   # LayerScale
    def forward(s, x):
        h = s.c2(F.relu(s.c1(x)))
        h = s.norm(h.transpose(1, 2)).transpose(1, 2)
        return x + s.gamma * h

class Duration(nn.Module):
    def __init__(s, V=VOCAB, w=32):
        super().__init__()
        s.emb = nn.Embedding(V, w)
        s.blocks = nn.ModuleList([DurBlock(w) for _ in range(3)])
        s.proj = nn.Conv1d(w, 1, 1)
    def forward(s, x):
        h = s.emb(x).transpose(1, 2)
        for b in s.blocks: h = b(h)
        return s.proj(h).squeeze(1)             # log d

# ---------- A_beta : (音素ID, d) -> c[40,T]   199,536 ----------
class AcBlock(nn.Module):                       # token block / frame block 共通
    def __init__(s, w=48, k=5):
        super().__init__()
        s.c1 = nn.Conv1d(w, w, k, padding=k//2); s.c2 = nn.Conv1d(w, w, k, padding=k//2)
        s.norm = nn.LayerNorm(w)
    def forward(s, x):
        h = s.c2(F.relu(s.c1(x)))
        return x + s.norm(h.transpose(1, 2)).transpose(1, 2)

class Acoustic(nn.Module):
    def __init__(s, V=VOCAB, w=48, P=88, cdim=40):
        super().__init__()
        s.emb = nn.Embedding(V, w)
        s.token = nn.ModuleList([AcBlock(w) for _ in range(3)])   # 音素レート
        s.pos = nn.Embedding(P, w)                                # 長さ展開後の音素内位置
        s.frame = nn.ModuleList([AcBlock(w) for _ in range(5)])   # フレームレート 86.13 fps
        s.out = nn.Conv1d(w, cdim, 1, bias=False)
    def forward(s, x, d):                       # d: [B,L] int frame counts
        h = s.emb(x).transpose(1, 2)
        for b in s.token: h = b(h)              # <- token block はここまで
        h = torch.repeat_interleave(h, d[0].long(), dim=2)        # length regulator (0 params)
        # device を h から取る。torch.arange を既定 (CPU) で作ると MPS/CUDA で
        # "Placeholder storage has not been allocated" になる
        idx = torch.cat([torch.arange(int(n), device=h.device) for n in d[0]]
                        ).clamp(max=s.pos.num_embeddings-1)
        h = h + s.pos(idx).t().unsqueeze(0)
        for b in s.frame: h = b(h)              # <- frame block はここから
        return s.out(h)

# ---------- G_gamma : c[40,T] -> 513 mag + 1026 phase -> iSTFT   331,308 ----------
class Decoder(nn.Module):
    def __init__(s, W=76, E=304, K=7, R=12, CIN=40, r=48):
        super().__init__()
        s.inp = nn.Conv1d(CIN, W, 3, padding=1)
        s.dw  = nn.ModuleList([nn.Conv1d(W, W, K, padding=K//2, groups=W, bias=False) for _ in range(5)])
        s.pw1 = nn.ModuleList([nn.Conv1d(W, E, 1) for _ in range(5)])
        s.pw2 = nn.ModuleList([nn.Conv1d(E, W, 1) for _ in range(5)])
        s.cdown = nn.ModuleList([nn.Conv1d(CIN, R, 1) for _ in range(5)])   # rank-12 conditioning
        s.cup   = nn.ModuleList([nn.Conv1d(R, W, 1) for _ in range(5)])
        s.gamma = nn.ParameterList([nn.Parameter(torch.ones(1)) for _ in range(5)])
        s.hdown = nn.Conv1d(W, r, 1); s.hout = nn.Conv1d(r, 1539, 1)        # 分解出力ヘッド
    def forward(s, c):
        h = s.inp(c)
        for i in range(5):
            g = s.cup[i](s.cdown[i](c))                                     # rank-12 FiLM gain
            h = h + s.gamma[i] * s.pw2[i](F.gelu(s.pw1[i](s.dw[i](h) + g)))
        o = s.hout(F.gelu(s.hdown(h)))
        mag, cos, sin = o[:, :513], o[:, 513:1026], o[:, 1026:]
        return mag, cos, sin
    @staticmethod
    def istft(mag, cos, sin):
        """T フレーム → **ちょうど 256*T サンプル**。教師の `y_lengths` 規約に合わせる。

        教師は `y_lengths = sum(ceil(w))` フレームから hop 256 でアップサンプルするので
        `len(yT) == 256 * T`（`data/pack_sibdense` 209 発話すべてで成立、D5）。
        一方 `torch.istft` は `length` を省くと **256*(T-1)** しか返さない（1 フレーム不足）。
        `center=False` は hann(1024)/hop=256 では `window overlap add min: 1` で
        必ず RuntimeError になるので使えない。

        `length=T*256` は内部バッファ `n_fft + hop*(T-1)` から `start=n_fft//2` で
        256T 分を**切り出す**だけでゼロ埋めではない。往復 SNR 139.0 ± 0.02 dB (n=209)。
        """
        S = torch.complex(mag * cos, mag * sin)
        T = mag.shape[-1]
        return torch.istft(S, n_fft=1024, hop_length=256, win_length=1024,
                           window=torch.hann_window(1024, device=S.device),
                           center=True, length=T * 256)

if __name__ == "__main__":
    # (1) 論文 Table I（英語・VOCAB=157）の再現。層構成の逆算が正しいことの検算
    tg = {"E_rho": 14952, "D_alpha": 36164, "A_beta": 199536, "G_gamma": 331308}
    en = {"E_rho": Erho(), "D_alpha": Duration(V=VOCAB_PAPER_EN),
          "A_beta": Acoustic(V=VOCAB_PAPER_EN), "G_gamma": Decoder()}
    tot = 0
    for k, m in en.items():
        n = N(m); print("%-9s %8d  target %8d  delta %+d" % (k, n, tg[k], n - tg[k]))
        if k != "E_rho": tot += n
    print("paper(EN, V=157) deployed total %d  target 567008  match=%s"
          % (tot, tot == 567008))

    # (2) 日本語の実語彙 V=57 での実数
    ms = {"E_rho": Erho(), "D_alpha": Duration(), "A_beta": Acoustic(),
          "G_gamma": Decoder()}
    tot_ja = sum(N(m) for k, m in ms.items() if k != "E_rho")
    print("ja   (V=%d)   deployed total %d  (paper 567008 との差 %+d)"
          % (VOCAB, tot_ja, tot_ja - 567008))
    assert tot_ja == 559008, tot_ja
    x = torch.randint(0, VOCAB, (1, 4)); d = torch.tensor([[3, 4, 2, 5]])
    c = ms["A_beta"](x, d); print("c:", tuple(c.shape))
    m_, co, si = ms["G_gamma"](c); y = Decoder.istft(m_, co, si)
    print("pcm:", tuple(y.shape), "= %.4f s @22.05kHz" % (y.shape[-1] / 22050))
