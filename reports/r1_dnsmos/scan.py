import re, sys, zlib
PAT = re.compile(rb'\((?:\\.|[^()\\])*\)', re.S)
def extract(path):
    data = open(path,'rb').read()
    out=[]
    n=0
    for m in re.finditer(rb'stream\r?\n', data):
        st=m.end(); en=data.find(b'endstream', st)
        s=data[st:en]
        try: s=zlib.decompress(s)
        except Exception: continue
        n+=1
        if b'TJ' not in s and b'Tj' not in s: continue
        for p in PAT.findall(s):
            out.append(p[1:-1].replace(rb'\(',b'(').replace(rb'\)',b')').decode('latin-1'))
        out.append(' ')
    return ''.join(out), n
txt, n = extract(sys.argv[1])
flat = re.sub(r'\s+','',txt)
print(f"[{sys.argv[1]}] decompressed_streams={n} chars={len(flat)}")
for kw in sys.argv[2:]:
    k=kw.replace(' ','')
    for m in re.finditer(re.escape(k), flat, re.I):
        print(f"  <<{kw}>> ...{flat[max(0,m.start()-180):m.end()+320]}...\n")
