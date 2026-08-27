import struct, sys, os, numpy as np
SRC="~/Documents/piper-plus/build/share/open_jtalk/dic"
csv_in, dic_dir = sys.argv[1], sys.argv[2]   # csv to rewrite in place, dir to write matrix.bin/unk.dic

# --- old matrix
mb = open(f"{SRC}/matrix.bin","rb").read()
lsize, rsize = struct.unpack("<HH", mb[:4])
M = np.frombuffer(mb[4:], dtype="<i2").reshape(rsize, lsize)   # M[lcAttr][rcAttr]
assert M.size == lsize*rsize

# --- unk.dic tokens (parse header like sys.dic)
ub = bytearray(open(f"{SRC}/unk.dic","rb").read())
h = struct.unpack("<10I", bytes(ub[:40])); ulex, uds, uts = h[3], h[6], h[7]
utok_off = 72 + uds
unk_lc, unk_rc = set(), set()
for i in range(ulex):
    lc, rc = struct.unpack("<HH", bytes(ub[utok_off+i*16: utok_off+i*16+4]))
    unk_lc.add(lc); unk_rc.add(rc)

# --- used ids from csv
used_l, used_r = {0}, {0}
rows=[]
for ln in open(csv_in, encoding="utf-8"):
    p = ln.rstrip("\n").split(",", 4)
    l, r = int(p[1]), int(p[2]); used_l.add(l); used_r.add(r); rows.append((p, l, r))
used_l |= unk_lc; used_r |= unk_rc

L = sorted(used_l)   # lcAttr values (axis with extent rsize)
R = sorted(used_r)   # rcAttr values (axis with extent lsize)
lmap = {o:n for n,o in enumerate(L)}
rmap = {o:n for n,o in enumerate(R)}
assert lmap[0]==0 and rmap[0]==0

NM = M[np.ix_(L, R)].astype("<i2")
out = struct.pack("<HH", len(R), len(L)) + NM.tobytes()
open(f"{dic_dir}/matrix.bin","wb").write(out)

# rewrite csv
with open(csv_in+".tmp","w",encoding="utf-8",newline="\n") as f:
    for p,l,r in rows:
        p[1]=str(lmap[l]); p[2]=str(rmap[r]); f.write(",".join(p)+"\n")
os.replace(csv_in+".tmp", csv_in)

# rewrite unk.dic tokens
for i in range(ulex):
    o=utok_off+i*16
    lc, rc = struct.unpack("<HH", bytes(ub[o:o+4]))
    ub[o:o+4] = struct.pack("<HH", lmap[lc], rmap[rc])
open(f"{dic_dir}/unk.dic","wb").write(bytes(ub))
print(f"{os.path.basename(dic_dir)}: lcAttr {rsize}->{len(L)}  rcAttr {lsize}->{len(R)}  matrix.bin {len(mb):,} -> {len(out):,}")
