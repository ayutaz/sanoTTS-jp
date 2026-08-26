"""A-3: コーパス規模への外挿・SHA-256 コスト・shard 粒度。"""
import hashlib, json, os, time
import numpy as np

n_ids=np.load("n_ids_train.npy").astype(np.float64)
ix=json.load(open("raw/index.json"))["rows"]
fr=np.array([r["frames"] for r in ix],float); nid=np.array([r["n_ids"] for r in ix],float)
a,b=np.polyfit(nid,fr,1)
F=float(np.maximum(a*n_ids+b,1).sum()); N=float(n_ids.sum()); U=len(n_ids)
S=F*256
comp=dict(zT_fp16=F*192*2, yT_int16=S*2, dT_fp32=N*4, ids_int32=N*4, prosody_int16=N*3*2)
tot=sum(comp.values())
print(f"train {U} 文 / est frames {F:,.0f} / est samples {S:,.0f} / {S/22050/3600:.2f} h")
for k,v in comp.items(): print(f"  {k:12s} {v/1e9:7.3f} GB  ({100*v/tot:5.1f}%)")
print(f"  {'合計':12s} {tot/1e9:7.3f} GB   平均 {tot/U/1024:.1f} KiB/文")
print(f"  参考 fp32/fp32     {(F*192*4+S*4+N*4+N*4+N*6)/1e9:7.3f} GB")

# 形式ごとの 1 epoch 読み出しコスト外挿（warm cache 実測 us/utt から）
fb=json.load(open("fmt_bench.json"))
print("\n1 epoch (20,946 文) の読み出し時間 [warm cache / 1 worker]:")
for k,v in fb["formats"].items():
    print(f"  {k:14s} {v['read_us_per_utt']*U/1e6:6.2f} s   disk {v['bytes_on_disk']/fb['logical_payload_bytes']*tot/1e9:6.3f} GB (外挿)")

# SHA-256 スループット
buf=os.urandom(64*1024*1024)
t0=time.perf_counter(); h=hashlib.sha256(); h.update(buf); el=time.perf_counter()-t0
bw=len(buf)/el/1e9
print(f"\nSHA-256: {bw:.2f} GB/s → パック {tot/1e9:.2f} GB 全体で {tot/1e9/bw:.1f} s")

# shard 粒度: 1 文再生成のために書き直すバイト数
print("\nshard 粒度 (1 文の部分再生成で書き直す量):")
for per in [1,128,256,512,1024,20946]:
    nsh=int(np.ceil(U/per)); sz=tot/nsh
    print(f"  {per:6d} 文/shard → shard {nsh:5d} 個 / 1 個 {sz/1e6:8.1f} MB / 書き直し {sz/1e6:8.1f} MB")
json.dump(dict(utts=U, est_frames=F, est_samples=S, hours=S/22050/3600,
               components=comp, total_bytes=tot, sha256_gbps=bw), open("scale.json","w"), indent=1, default=float)
