"""R-3: csrc/student.bin と student_i8.bin のテンソルを列挙し、
モジュール別のバイト内訳を出す。論文 blob1/blob2 (280,288 / 399,544 B) と突き合わせる。"""
import struct, sys, collections, pathlib
NAME_LEN = 64
DT = {0: "f32", 1: "i8", 2: "scale"}

def read(path):
    b = pathlib.Path(path).read_bytes()
    assert b[:4] == b"SAAN"
    ver, n, hb = struct.unpack("<III", b[4:16])
    ent = NAME_LEN + 4 + 4 + 16 + 8 + 8
    out = []
    for i in range(n):
        o = 16 + i * ent
        nm = b[o:o+NAME_LEN].split(b"\0")[0].decode()
        dt, nd = struct.unpack("<II", b[o+NAME_LEN:o+NAME_LEN+8])
        d4 = struct.unpack("<4I", b[o+NAME_LEN+8:o+NAME_LEN+24])
        off, nb = struct.unpack("<QQ", b[o+NAME_LEN+24:o+NAME_LEN+40])
        out.append((nm, DT[dt], d4[:nd], nb))
    return out, len(b), hb

def mod(nm):
    return nm.split(".")[0]

for path in ("csrc/student.bin", "csrc/student_i8.bin"):
    ts, total, hb = read(path)
    print(f"\n===== {path}  total {total:,} B  header {hb:,} B  n_tensors {len(ts)} =====")
    g = collections.OrderedDict()
    for nm, dt, dims, nb in ts:
        k = mod(nm)
        g.setdefault(k, collections.Counter())
        g[k][dt] += nb
        g[k]["_total"] += nb
        g[k]["_n"] += 1
    for k, c in g.items():
        parts = " ".join(f"{d}={c[d]:,}" for d in ("f32","i8","scale") if c[d])
        print(f"  {k:12s} {c['_total']:9,d} B   ({c['_n']} tensors)  {parts}")
    print(f"  {'SUM(tensor)':12s} {sum(c['_total'] for c in g.values()):9,d} B")
    # 論文の blob 分割: blob1 = duration+acoustic, blob2 = decoder
    b1 = sum(g[k]["_total"] for k in g if k in ("duration","acoustic"))
    b2 = sum(g[k]["_total"] for k in g if k == "decoder")
    other = sum(g[k]["_total"] for k in g if k not in ("duration","acoustic","decoder"))
    print(f"  blob1(duration+acoustic) {b1:,} B   論文 280,288 B  差 {b1-280288:+,} ({100*(b1-280288)/280288:+.1f}%)")
    print(f"  blob2(decoder)           {b2:,} B   論文 399,544 B  差 {b2-399544:+,} ({100*(b2-399544)/399544:+.1f}%)")
    print(f"  その他(非モジュール)     {other:,} B  ->", [k for k in g if k not in ("duration","acoustic","decoder")])
