"""A-3-2b: yT の int16 化が L_G（式5）にどれだけ効くか + STFT 事前計算のサイズ。"""
import json, numpy as np, torch

RAW="raw"; ix=json.load(open(f"{RAW}/index.json"))["rows"]; SEQ=[r["seq"] for r in ix]
R=[(512,128),(1024,256),(2048,512)]   # 論文 §II 式5 の R

def mrstft(a,b):
    """l_{n,h} = || log(1+|S(a)|) - log(1+|S(b)|) ||_1 の平均（|R| で割る前の項別）"""
    out={}
    ta=torch.from_numpy(a.astype(np.float32)); tb=torch.from_numpy(b.astype(np.float32))
    for nfft,hop in R:
        w=torch.hann_window(nfft)
        A=torch.stft(ta,nfft,hop,window=w,return_complex=True).abs()
        B=torch.stft(tb,nfft,hop,window=w,return_complex=True).abs()
        out[f"{nfft}_{hop}"]=float((torch.log1p(A)-torch.log1p(B)).abs().mean())
    return out

def i16(y, mode):
    if mode=="global":                      # 標準 PCM: 32767 固定
        q=np.rint(np.clip(y,-1,1)*32767.0).clip(-32768,32767)
        return (q/32767.0).astype(np.float32), 0
    peak=float(np.abs(y).max()) or 1.0      # 発話ごとピーク正規化 (scale を fp32 で同梱)
    q=np.rint(y/peak*32767.0).clip(-32768,32767)
    return (q/32767.0*peak).astype(np.float32), 4

res={}
for mode in ["global","peak"]:
    snr=[]; l1=[]; st={f"{n}_{h}":0.0 for n,h in R}; peaks=[]
    for q in SEQ:
        y=np.load(f"{RAW}/{q:06d}.npz")["yT"]
        yq,_=i16(y,mode)
        e=yq-y
        snr.append(10*np.log10((y**2).sum()/max((e**2).sum(),1e-30)))
        l1.append(np.abs(e).mean())
        peaks.append(np.abs(y).max())
        for k,v in mrstft(yq,y).items(): st[k]+=v
    for k in st: st[k]/=len(SEQ)
    res[mode]=dict(snr_db_mean=float(np.mean(snr)), snr_db_min=float(np.min(snr)),
                   l1_mean=float(np.mean(l1)), mrstft={k:float(v) for k,v in st.items()},
                   mrstft_mean=float(np.mean(list(st.values()))))
    print(f"int16/{mode:6s}  SNR mean {np.mean(snr):6.2f} dB (min {np.min(snr):.2f})  "
          f"L1 {np.mean(l1):.3e}  MRSTFT " + " ".join(f"{k}:{v:.3e}" for k,v in st.items()))
print("yT peak: mean %.4f min %.4f max %.4f" % (np.mean(peaks), np.min(peaks), np.max(peaks)))

# 参考: 生徒に想定される誤差（波形 SNR を振って MRSTFT 項の大きさを出す）
rng=np.random.default_rng(0); base={}
for snr_db in [10,20,30,40]:
    acc={f"{n}_{h}":0.0 for n,h in R}; l1a=0.0
    for q in SEQ[:32]:
        y=np.load(f"{RAW}/{q:06d}.npz")["yT"]
        sig=np.sqrt((y**2).mean()); noise=sig*10**(-snr_db/20)
        yh=(y+noise*rng.standard_normal(y.shape)).astype(np.float32)
        l1a+=np.abs(yh-y).mean()
        for k,v in mrstft(yh,y).items(): acc[k]+=v
    m=len(SEQ[:32]); base[snr_db]={k:v/m for k,v in acc.items()}; base[snr_db]["l1"]=l1a/m
    print(f"student SNR {snr_db} dB → L1 {l1a/m:.3e}  MRSTFT " + " ".join(f"{k}:{v/m:.3e}" for k,v in acc.items()))

# ---- STFT 事前計算のサイズ ---------------------------------------------------
tot_samples=sum(r["samples"] for r in ix); tot_frames=sum(r["frames"] for r in ix)
bins_per_sample=sum((n//2+1)/h for n,h in R)
print(f"\nSTFT 事前計算: {bins_per_sample:.3f} bin/sample × 3 解像度合計")
for dt,B in [("fp16",2),("fp32",4)]:
    print(f"  log1p|S| {dt}: {bins_per_sample*B:.2f} B/sample  (yT int16 は 2 B/sample → {bins_per_sample*B/2:.1f}x)")
json.dump(dict(int16=res, student_ref=base,
               stft_bins_per_sample=float(bins_per_sample),
               R=[list(x) for x in R]), open("quant_yT.json","w"), indent=1, default=float)
