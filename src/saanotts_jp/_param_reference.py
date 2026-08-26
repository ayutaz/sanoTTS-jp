"""saanoTTS (arXiv:2608.21378) Table I のパラメータ数を再現する層構成。
   torch: /Users/s19447/Documents/piper-plus/.venv/bin/python で検証済み。
   D 36,164 / A 199,536 / G 331,308 / 合計 567,008 / E_rho 14,952 すべて delta 0。"""
import torch, torch.nn as nn, torch.nn.functional as F
N = lambda m: sum(p.numel() for p in m.parameters())
VOCAB = 157   # 論文: deployed acoustic vocabulary has 157 entries

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
        S = torch.complex(mag * cos, mag * sin)
        return torch.istft(S, n_fft=1024, hop_length=256, win_length=1024,
                           window=torch.hann_window(1024, device=S.device), center=True)

if __name__ == "__main__":
    tg = {"E_rho": 14952, "D_alpha": 36164, "A_beta": 199536, "G_gamma": 331308}
    ms = {"E_rho": Erho(), "D_alpha": Duration(), "A_beta": Acoustic(), "G_gamma": Decoder()}
    tot = 0
    for k, m in ms.items():
        n = N(m); print("%-9s %8d  target %8d  delta %+d" % (k, n, tg[k], n - tg[k]))
        if k != "E_rho": tot += n
    print("deployed total %d  target 567008  match=%s" % (tot, tot == 567008))
    x = torch.randint(0, VOCAB, (1, 4)); d = torch.tensor([[3, 4, 2, 5]])
    c = ms["A_beta"](x, d); print("c:", tuple(c.shape))
    m_, co, si = ms["G_gamma"](c); y = Decoder.istft(m_, co, si)
    print("pcm:", tuple(y.shape), "= %.4f s @22.05kHz" % (y.shape[-1] / 22050))
