"""A-3-1: 格納形式の実測比較（サイズ / 書き込み / ランダムアクセス）。

共通のペイロード: zT fp16 (192,T) / yT int16 (T*256) / dT fp32 (N) /
                  phoneme_ids int32 (N) / prosody int16 (N,3)
"""
import json, os, shutil, time, subprocess, io
import numpy as np

RAW="raw"; IX=json.load(open(f"{RAW}/index.json"))["rows"]; SEQ=[r["seq"] for r in IX]
OUT="fmt"; os.makedirs(OUT, exist_ok=True)

def payload(q):
    z=np.load(f"{RAW}/{q:06d}.npz")
    return dict(zT=z["zT"].astype(np.float16),
                yT=np.rint(np.clip(z["yT"],-1,1)*32767.0).astype(np.int16),
                dT=z["dT"].astype(np.float32),
                phoneme_ids=z["phoneme_ids"].astype(np.int32),
                prosody=z["prosody"].astype(np.int16))
PAY=[payload(q) for q in SEQ]
LOGICAL=sum(sum(v.nbytes for v in p.values()) for p in PAY)
print(f"論理ペイロード合計 {LOGICAL/1e6:.2f} MB / {len(PAY)} 文")

def du(path):
    """実ブロック使用量 (KB) と論理サイズ (B) とファイル数。"""
    kb=int(subprocess.run(["du","-sk",path],capture_output=True,text=True).stdout.split()[0])
    n=0; b=0
    for root,_,fs in os.walk(path):
        for f in fs:
            n+=1; b+=os.path.getsize(os.path.join(root,f))
    if os.path.isfile(path): n=1; b=os.path.getsize(path)
    return kb*1024, b, n

def fresh(d):
    p=f"{OUT}/{d}"
    if os.path.isdir(p): shutil.rmtree(p)
    elif os.path.exists(p): os.remove(p)
    return p

RES={}
def record(name, path, wsec, reader, partial_reader=None):
    blocks, logical, nfiles = du(path)
    rng=np.random.default_rng(1)
    order=rng.integers(0,len(PAY),3000)
    t0=time.perf_counter()
    acc=0
    for i in order: acc+=reader(int(i))
    rt=(time.perf_counter()-t0)/len(order)*1e6
    pt=None
    if partial_reader:
        t0=time.perf_counter()
        for i in order: acc+=partial_reader(int(i))
        pt=(time.perf_counter()-t0)/len(order)*1e6
    RES[name]=dict(bytes_on_disk=blocks, bytes_logical=logical, n_files=nfiles,
                   write_s=wsec, read_us_per_utt=rt, partial_read_us_per_utt=pt,
                   overhead_pct=100*(logical-LOGICAL)/LOGICAL)
    print(f"{name:14s} disk {blocks/1e6:7.2f} MB  logical {logical/1e6:7.2f} MB "
          f"(+{100*(logical-LOGICAL)/LOGICAL:5.2f}%)  files {nfiles:6d}  "
          f"write {wsec:6.2f}s  read {rt:8.1f} us/utt" + (f"  dT-only {pt:7.1f} us" if pt else ""))

# ---------- 1) npz 無圧縮 / 圧縮 ----------
for tag,fn in [("npz_store",np.savez),("npz_deflate",np.savez_compressed)]:
    p=fresh(tag); os.makedirs(p)
    t0=time.perf_counter()
    for k,pl in enumerate(PAY): fn(f"{p}/{k:06d}.npz", **pl)
    w=time.perf_counter()-t0
    def rd(i,p=p):
        d=np.load(f"{p}/{i:06d}.npz")
        return d["zT"].size+d["yT"].size+d["dT"].size+d["phoneme_ids"].size
    def rp(i,p=p):
        d=np.load(f"{p}/{i:06d}.npz"); return d["dT"].size+d["phoneme_ids"].size
    record(tag,p,w,rd,rp)

# ---------- 2) safetensors ----------
from safetensors.numpy import save_file, load_file
try:
    from safetensors import safe_open
except Exception:
    safe_open=None
p=fresh("safetensors"); os.makedirs(p)
t0=time.perf_counter()
for k,pl in enumerate(PAY): save_file({kk:np.ascontiguousarray(vv) for kk,vv in pl.items()}, f"{p}/{k:06d}.safetensors")
w=time.perf_counter()-t0
def rd_st(i,p=p):
    d=load_file(f"{p}/{i:06d}.safetensors")
    return d["zT"].size+d["yT"].size+d["dT"].size+d["phoneme_ids"].size
def rp_st(i,p=p):
    with safe_open(f"{p}/{i:06d}.safetensors", framework="np") as f:
        return f.get_tensor("dT").size+f.get_tensor("phoneme_ids").size
record("safetensors",p,w,rd_st,rp_st if safe_open else None)

# ---------- 3) 生バイナリ shard + index (memmap) ----------
p=fresh("shard_bin"); os.makedirs(p)
t0=time.perf_counter()
fh={k:open(f"{p}/{k}.bin","wb") for k in ["zT","yT","dT","phoneme_ids","prosody"]}
off={k:0 for k in fh}; idx=[]
for k,pl in enumerate(PAY):
    e={}
    for kk,vv in pl.items():
        b=np.ascontiguousarray(vv).tobytes(); fh[kk].write(b)
        e[kk]=(off[kk], vv.size); off[kk]+=len(b)
    idx.append(dict(seq=k, T=int(pl["zT"].shape[1]), N=int(pl["dT"].shape[0]),
                    **{f"{kk}_off":int(v[0]) for kk,v in e.items()}))
for f in fh.values(): f.close()
dt=np.dtype([("seq","<i4"),("T","<i4"),("N","<i4")]+[(f"{k}_off","<i8") for k in fh])
arr=np.zeros(len(idx),dt)
for i,e in enumerate(idx):
    arr[i]=(e["seq"],e["T"],e["N"],*[e[f"{k}_off"] for k in fh])
np.save(f"{p}/index.npy",arr)
w=time.perf_counter()-t0
MM={k:np.memmap(f"{p}/{k}.bin",dtype=d,mode="r") for k,d in
    [("zT",np.float16),("yT",np.int16),("dT",np.float32),("phoneme_ids",np.int32),("prosody",np.int16)]}
IDXA=np.load(f"{p}/index.npy")
ITEM={"zT":2,"yT":2,"dT":4,"phoneme_ids":4,"prosody":2}
def rd_sb(i):
    e=IDXA[i]; T=int(e["T"]); N=int(e["N"])
    z=np.array(MM["zT"][e["zT_off"]//2:e["zT_off"]//2+192*T]).reshape(192,T)
    y=np.array(MM["yT"][e["yT_off"]//2:e["yT_off"]//2+T*256])
    d=np.array(MM["dT"][e["dT_off"]//4:e["dT_off"]//4+N])
    x=np.array(MM["phoneme_ids"][e["phoneme_ids_off"]//4:e["phoneme_ids_off"]//4+N])
    return z.size+y.size+d.size+x.size
def rp_sb(i):
    e=IDXA[i]; N=int(e["N"])
    d=np.array(MM["dT"][e["dT_off"]//4:e["dT_off"]//4+N])
    x=np.array(MM["phoneme_ids"][e["phoneme_ids_off"]//4:e["phoneme_ids_off"]//4+N])
    return d.size+x.size
record("shard_bin",p,w,rd_sb,rp_sb)

# ---------- 4) HDF5: 発話ごとの group ----------
import h5py
p=fresh("hdf5_group.h5")
t0=time.perf_counter()
with h5py.File(p,"w") as f:
    for k,pl in enumerate(PAY):
        g=f.create_group(f"{k:06d}")
        for kk,vv in pl.items(): g.create_dataset(kk,data=vv)
w=time.perf_counter()-t0
H=h5py.File(p,"r")
def rd_h(i):
    g=H[f"{i:06d}"]
    return g["zT"][:].size+g["yT"][:].size+g["dT"][:].size+g["phoneme_ids"][:].size
def rp_h(i):
    g=H[f"{i:06d}"]; return g["dT"][:].size+g["phoneme_ids"][:].size
record("hdf5_group",p,w,rd_h,rp_h)
H.close()

# ---------- 5) HDF5: ragged（1 field 1 dataset + offset） ----------
p=fresh("hdf5_ragged.h5")
t0=time.perf_counter()
with h5py.File(p,"w") as f:
    for kk,dt_ in [("zT","f2"),("yT","i2"),("dT","f4"),("phoneme_ids","i4"),("prosody","i2")]:
        cat=np.concatenate([np.ascontiguousarray(pl[kk]).ravel() for pl in PAY])
        f.create_dataset(kk,data=cat,dtype=dt_)
    o={kk:np.cumsum([0]+[pl[kk].size for pl in PAY]) for kk in PAY[0]}
    for kk,v in o.items(): f.create_dataset(f"off_{kk}",data=v.astype(np.int64))
    f.create_dataset("T",data=np.array([pl["zT"].shape[1] for pl in PAY],np.int32))
    f.create_dataset("N",data=np.array([pl["dT"].shape[0] for pl in PAY],np.int32))
w=time.perf_counter()-t0
H2=h5py.File(p,"r"); OFF={kk:H2[f"off_{kk}"][:] for kk in PAY[0]}; TT=H2["T"][:]; NN=H2["N"][:]
def rd_h2(i):
    z=H2["zT"][OFF["zT"][i]:OFF["zT"][i+1]]; y=H2["yT"][OFF["yT"][i]:OFF["yT"][i+1]]
    d=H2["dT"][OFF["dT"][i]:OFF["dT"][i+1]]; x=H2["phoneme_ids"][OFF["phoneme_ids"][i]:OFF["phoneme_ids"][i+1]]
    return z.size+y.size+d.size+x.size
def rp_h2(i):
    d=H2["dT"][OFF["dT"][i]:OFF["dT"][i+1]]; x=H2["phoneme_ids"][OFF["phoneme_ids"][i]:OFF["phoneme_ids"][i+1]]
    return d.size+x.size
record("hdf5_ragged",p,w,rd_h2,rp_h2)
H2.close()

# ---------- 6) shard_bin(zT/dT/ids) + 個別 wav（P1-5 案） ----------
import soundfile as sf
p=fresh("wav_sidecar"); os.makedirs(p)
t0=time.perf_counter()
fh={k:open(f"{p}/{k}.bin","wb") for k in ["zT","dT","phoneme_ids","prosody"]}
off={k:0 for k in fh}; idx=[]
for k,pl in enumerate(PAY):
    e={}
    for kk in fh:
        b=np.ascontiguousarray(pl[kk]).tobytes(); fh[kk].write(b); e[kk]=off[kk]; off[kk]+=len(b)
    sf.write(f"{p}/{k:06d}.wav", pl["yT"], 22050, subtype="PCM_16")
    idx.append((k,int(pl["zT"].shape[1]),int(pl["dT"].shape[0]),*[e[kk] for kk in fh]))
for f in fh.values(): f.close()
dt2=np.dtype([("seq","<i4"),("T","<i4"),("N","<i4")]+[(f"{k}_off","<i8") for k in fh])
np.save(f"{p}/index.npy",np.array(idx,dt2))
w=time.perf_counter()-t0
MM2={k:np.memmap(f"{p}/{k}.bin",dtype=d,mode="r") for k,d in
     [("zT",np.float16),("dT",np.float32),("phoneme_ids",np.int32),("prosody",np.int16)]}
IX2=np.load(f"{p}/index.npy")
def rd_ws(i):
    e=IX2[i]; T=int(e["T"]); N=int(e["N"])
    z=np.array(MM2["zT"][e["zT_off"]//2:e["zT_off"]//2+192*T]).reshape(192,T)
    y,_=sf.read(f"{p}/{i:06d}.wav",dtype="int16")
    d=np.array(MM2["dT"][e["dT_off"]//4:e["dT_off"]//4+N])
    x=np.array(MM2["phoneme_ids"][e["phoneme_ids_off"]//4:e["phoneme_ids_off"]//4+N])
    return z.size+y.size+d.size+x.size
record("wav_sidecar",p,w,rd_ws,None)

json.dump(dict(n_utt=len(PAY), logical_payload_bytes=int(LOGICAL), formats=RES),
          open("fmt_bench.json","w"), indent=1, default=float)
