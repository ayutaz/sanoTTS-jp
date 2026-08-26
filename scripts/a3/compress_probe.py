"""yT を FLAC、zT を zstd/deflate にしたときのサイズと復号コスト。"""
import io, json, os, time, zlib
import numpy as np, soundfile as sf

IX=json.load(open("raw/index.json"))["rows"]
Y=[];Z=[]
for r in IX:
    d=np.load(f"raw/{r['seq']:06d}.npz")
    Y.append(np.rint(np.clip(d["yT"],-1,1)*32767).astype(np.int16))
    Z.append(d["zT"].astype(np.float16))
raw_y=sum(a.nbytes for a in Y); raw_z=sum(a.nbytes for a in Z)
print(f"素: yT int16 {raw_y/1e6:.2f} MB  zT fp16 {raw_z/1e6:.2f} MB")

# --- yT: FLAC ---
bufs=[]; t0=time.perf_counter()
for a in Y:
    b=io.BytesIO(); sf.write(b,a,22050,format="FLAC",subtype="PCM_16"); bufs.append(b.getvalue())
enc=time.perf_counter()-t0
flac=sum(len(b) for b in bufs)
rng=np.random.default_rng(0); order=rng.integers(0,len(bufs),1000)
t0=time.perf_counter()
for i in order: sf.read(io.BytesIO(bufs[int(i)]),dtype="int16")
dec=(time.perf_counter()-t0)/len(order)*1e6
print(f"yT FLAC   {flac/1e6:7.2f} MB ({100*flac/raw_y:.1f}% of int16)  encode {enc:.2f}s  decode {dec:.1f} us/utt")

# --- zT: deflate / zstd ---
t0=time.perf_counter(); dz=[zlib.compress(a.tobytes(),6) for a in Z]; e1=time.perf_counter()-t0
print(f"zT deflate6 {sum(len(b) for b in dz)/1e6:7.2f} MB ({100*sum(len(b) for b in dz)/raw_z:.1f}%)  encode {e1:.2f}s")
t0=time.perf_counter()
for i in order: zlib.decompress(dz[int(i)])
print(f"            decode {(time.perf_counter()-t0)/len(order)*1e6:.1f} us/utt")
try:
    import zstandard as zstd
    for lv in (3,10):
        c=zstd.ZstdCompressor(level=lv); t0=time.perf_counter(); cz=[c.compress(a.tobytes()) for a in Z]; e=time.perf_counter()-t0
        d=zstd.ZstdDecompressor(); t0=time.perf_counter()
        for i in order: d.decompress(cz[int(i)])
        print(f"zT zstd-{lv:<2d}  {sum(len(b) for b in cz)/1e6:7.2f} MB ({100*sum(len(b) for b in cz)/raw_z:.1f}%)  encode {e:.2f}s  decode {(time.perf_counter()-t0)/len(order)*1e6:.1f} us/utt")
        cy=[c.compress(a.tobytes()) for a in Y]
        print(f"yT zstd-{lv:<2d}  {sum(len(b) for b in cy)/1e6:7.2f} MB ({100*sum(len(b) for b in cy)/raw_y:.1f}%)")
except ImportError:
    print("zstandard 無し")
