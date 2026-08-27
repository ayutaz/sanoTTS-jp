"""R-3: 教師 MBiSTFTGenerator と生徒 Gγ を同じ物差し（params / MAC per audio-second）で並べ、
「教師を細くしたコピー」の必要幅と計算量を解く。読み取り専用。"""
SR, HOP = 22050.0, 256.0
FPS0 = SR / HOP                      # 86.1328  conv_pre 入力のフレームレート

def teacher_dec(U=256, subbands=4, n_fft=16, hop=4, kers=(3,5,7), zin=192,
                up=(4,4), upk=(16,16)):
    """MBiSTFTGenerator の params と MAC/audio-second。
    hp 実測: resblock=2, kernels (3,5,7), dilations ((1,2),(2,6),(3,12)),
             upsample_rates (4,4), upsample_kernel_sizes (16,16), U=256。"""
    P = {}; M = {}
    fps = FPS0
    P["conv_pre"] = zin*U*7 + U;            M["conv_pre"] = zin*U*7 * fps
    ch = U
    for i, (u, k) in enumerate(zip(up, upk)):
        co = U // (2**(i+1))
        P[f"ups.{i}"] = ch*co*k + co;       M[f"ups.{i}"] = ch*co*k * fps
        fps *= u
        w = 2*sum(co*co*kk for kk in kers)          # ResBlock2 = 2 conv / block
        b = 2*len(kers)*co
        P[f"resblocks.stage{i}"] = w + b;   M[f"resblocks.stage{i}"] = w * fps
        ch = co
    out = subbands*(n_fft+2)
    P["subband_conv_post"] = ch*out*7 + out; M["subband_conv_post"] = ch*out*7 * fps
    # 話者/言語 FiLM。うちのラベル生成では g = emb_lang(0) の**定数**なので実行時は畳める
    P["cond (FiLM, 512->2U)"] = 512*U*2 + U*2
    M["cond (FiLM)"] = 0.0
    for i in range(len(up)):
        co = U // (2**(i+1))
        P[f"cond_layers.{i}"] = 512*co*2 + co*2
        M[f"cond_layers.{i}"] = 0.0
    # PQMF 合成（buffer。学習パラメータではない）: 63 タップ × 4 サブバンド / 出力サンプル
    M["PQMF synthesis (非パラメータ)"] = 4*63 * SR
    M["iSTFT 16pt ×4 subband (非パラメータ)"] = 0.0   # 実 iFFT。conv では数えない
    return P, M

def student_dec(W=76, E=304, K=7, R=12, CIN=40, r=48, OUT=1539, nblk=5):
    P = {}; M = {}
    P["inp Conv1d(40,W,k3)"] = CIN*W*3 + W;  M["inp"] = CIN*W*3 * FPS0
    P["dw ×5"]   = nblk*(W*K);               M["dw ×5"]   = nblk*(W*K) * FPS0
    P["pw1 ×5"]  = nblk*(W*E + E);           M["pw1 ×5"]  = nblk*(W*E) * FPS0
    P["pw2 ×5"]  = nblk*(E*W + W);           M["pw2 ×5"]  = nblk*(E*W) * FPS0
    P["cdown ×5"]= nblk*(CIN*R + R);         M["cdown ×5"]= nblk*(CIN*R) * FPS0
    P["cup ×5"]  = nblk*(R*W + W);           M["cup ×5"]  = nblk*(R*W) * FPS0
    P["gamma ×5"]= nblk;                     M["gamma"]   = 0.0
    P["hdown"]   = W*r + r;                  M["hdown"]   = W*r * FPS0
    P["hout"]    = r*OUT + OUT;              M["hout"]    = r*OUT * FPS0
    return P, M

def show(name, P, M):
    print(f"\n=== {name} ===")
    print(f"{'層':34s} {'params':>10s} {'MMAC/audio-s':>14s}")
    for k in P:
        mm = M.get(k, 0.0)/1e6
        print(f"{k:34s} {P[k]:10,d} {mm:14.3f}")
    extra = {k: v for k, v in M.items() if k not in P}
    for k, v in extra.items():
        print(f"{k:34s} {'—':>10s} {v/1e6:14.3f}")
    print(f"{'合計':34s} {sum(P.values()):10,d} {sum(M.values())/1e6:14.3f}")
    return sum(P.values()), sum(M.values())/1e6

tP, tM = teacher_dec()
pt, mt = show("教師 MBiSTFTGenerator (U=256, ckpt 実測 hp)", tP, tM)
sP, sM = student_dec()
ps, ms = show("生徒 Gγ (_param_reference.Decoder)", sP, sM)
print(f"\n教師 / 生徒 : params {pt/ps:.2f}×   MMAC/s {mt/ms:.2f}×")

print("\n=== 「教師を細くしたコピー」を 331,308 params に合わせるとどうなるか ===")
print(f"{'U':>5s} {'params':>10s} {'MMAC/s':>10s}  {'note'}")
best = None
for U in range(4, 300, 4):
    P, M = teacher_dec(U=U)
    p = sum(P.values()); m = sum(M.values())/1e6
    if best is None or abs(p-331308) < abs(best[1]-331308):
        best = (U, p, m)
    if U % 16 == 0 or abs(p-331308) < 40000:
        print(f"{U:5d} {p:10,d} {m:10.3f}")
print(f"→ 331,308 に最も近い U = {best[0]} (params {best[1]:,}, {best[2]:.3f} MMAC/s)")
print(f"   生徒 Gγ の {ms:.3f} MMAC/s に対し {best[2]/ms:.2f}×")
print(f"   論文の総計 45 MMAC/s に対し decoder 単独で {best[2]/45*100:.0f}%")

print("\n（参考）FiLM を落とした細い教師コピー（cond/cond_layers 無し）")
for U in (96, 100, 104, 128):
    P, M = teacher_dec(U=U)
    p = sum(v for k, v in P.items() if not k.startswith("cond"))
    m = sum(M.values())/1e6
    print(f"  U={U:4d}  params(FiLM 除く) {p:9,d}  {m:8.3f} MMAC/s")

print("\n=== 逆向き: 生徒 Gγ と同じ計算量 28.191 MMAC/s に教師コピーを合わせると ===")
target = 28.191
rows = []
for U in range(4, 130, 2):
    P, M = teacher_dec(U=U)
    p_all = sum(P.values())
    p_nofilm = sum(v for k, v in P.items() if not k.startswith("cond"))
    m = sum(M.values())/1e6
    rows.append((abs(m-target), U, p_all, p_nofilm, m))
rows.sort()
for a, U, pa, pn, m in rows[:3]:
    print(f"  U={U:3d}  {m:7.3f} MMAC/s  params(FiLM込) {pa:9,d}  params(FiLM除) {pn:9,d}")
print("  ※ PQMF 合成の 5.557 MMAC/s を含む。conv だけなら U はもう少し大きく取れる")
