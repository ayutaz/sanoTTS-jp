import re, sys, zlib
PAT = re.compile(rb'\((?:\\.|[^()\\])*\)', re.S)
def extract(path):
    data = open(path,'rb').read()
    out=[]
    for m in re.finditer(rb'stream\r?\n(.*?)endstream', data, re.S):
        s=m.group(1)
        try: s=zlib.decompress(s)
        except Exception: continue
        if b'TJ' not in s and b'Tj' not in s: continue
        for p in PAT.findall(s):
            p=p[1:-1].replace(rb'\(',b'(').replace(rb'\)',b')')
            out.append(p.decode('latin-1'))
        out.append(' ')
    return ''.join(out)
if __name__=='__main__':
    print(re.sub(r'\s+',' ',extract(sys.argv[1])))
