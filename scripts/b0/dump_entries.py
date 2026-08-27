import struct, sys, numpy as np, time
DIC = sys.argv[2] if len(sys.argv)>2 else "~/Documents/piper-plus/build/share/open_jtalk/dic/sys.dic"
OUT = sys.argv[1]
b = open(DIC, "rb").read()
magic,version,dtype,lexsize,lsize,rsize,dsize,tsize,fsize,dummy = struct.unpack("<10I", b[:40])
charset = b[40:72].split(b"\0")[0].decode()
print(f"lexsize={lexsize} dsize={dsize} tsize={tsize} fsize={fsize} charset={charset}", flush=True)
off = 72
darts = b[off:off+dsize]; off += dsize
tok   = b[off:off+tsize]; off += tsize
feat  = b[off:off+fsize]
assert off + fsize == len(b), (off+fsize, len(b))

u = np.frombuffer(darts, dtype=np.uint32)
base  = u[0::2].view(np.int32).astype(np.int64)
check = u[1::2].astype(np.int64)
N = base.size
print(f"darts units={N}", flush=True)

# node tables
parent = [np.array([-1], dtype=np.int64)]
charsA = [np.array([0],  dtype=np.int64)]
pos_l  = np.array([0], dtype=np.int64)
id_l   = np.array([0], dtype=np.int64)
next_id = 1
term_nodeid, term_val = [], []
t0=time.time(); depth=0
while pos_l.size:
    B = base[pos_l]
    # terminal: p = B
    ok = (B >= 0) & (B < N)
    p = np.where(ok, B, 0)
    hit = ok & (check[p] == (B & 0xFFFFFFFF)) & (base[p] < 0)
    if hit.any():
        term_nodeid.append(id_l[hit]); term_val.append(-base[p[hit]] - 1)
    # children
    new_pos, new_par, new_ch = [], [], []
    CH = 65536
    for s in range(0, pos_l.size, CH):
        Bs = B[s:s+CH]; Is = id_l[s:s+CH]
        P = Bs[:,None] + np.arange(1,257,dtype=np.int64)[None,:]
        v = (P >= 0) & (P < N)
        Pc = np.where(v, P, 0)
        m = v & (check[Pc] == (Bs[:,None] & 0xFFFFFFFF))
        r,c = np.nonzero(m)
        if r.size:
            new_pos.append(P[r,c]); new_par.append(Is[r]); new_ch.append(c)
    if not new_pos:
        break
    np_pos = np.concatenate(new_pos); np_par = np.concatenate(new_par)
    np_ch  = np.concatenate(new_ch).astype(np.int64)
    n = np_pos.size
    ids = np.arange(next_id, next_id+n, dtype=np.int64); next_id += n
    parent.append(np_par); charsA.append(np_ch)
    pos_l, id_l = np_pos, ids
    depth += 1
    print(f"  depth={depth} nodes={n} total={next_id} t={time.time()-t0:.1f}s", flush=True)

PAR = np.concatenate(parent); CHR = np.concatenate(charsA)
TN = np.concatenate(term_nodeid); TV = np.concatenate(term_val)
print(f"terminal nodes={TN.size} total_nodes={PAR.size} t={time.time()-t0:.1f}s", flush=True)

# reconstruct keys
maxd = 0
keys = []
for nid in TN:
    out = bytearray()
    x = int(nid)
    while x > 0:
        out.append(int(CHR[x])); x = int(PAR[x])
    out.reverse()
    keys.append(bytes(out))
print(f"keys reconstructed t={time.time()-t0:.1f}s", flush=True)

nfeat = 0
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    total = 0
    for k, val in zip(keys, TV):
        val = int(val)
        size = val & 0xff
        idx  = val >> 8
        surf = k.decode("utf-8", "replace")
        for j in range(size):
            i = idx + j
            lc, rc, pid, cost, fo, comp = struct.unpack("<HHHhII", tok[i*16:(i+1)*16])
            e = feat.index(b"\0", fo)
            s = feat[fo:e].decode(charset, "replace")
            f.write(f"{surf}\t{lc}\t{rc}\t{pid}\t{cost}\t{s}\n")
            total += 1
print(f"wrote {total} rows (lexsize={lexsize}) t={time.time()-t0:.1f}s", flush=True)
