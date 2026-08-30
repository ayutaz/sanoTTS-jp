"""Q1(b) 続き: 辞書に帰属する RSS と、文を処理し続けたときの伸びを分解する。

vmmap の領域を「file-backed(辞書)」「MALLOC」「その他」に分けて前後差をとる。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import re
import subprocess

DICDIR = (_ROOT + "/.venv/lib/python3.14/site-packages/pyopenjtalk/dictionary")
PID = os.getpid()
SP = (_WORK + "")


def rss_bytes():
    out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(PID)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out) * 1024


def vmmap_text():
    return subprocess.run(["/usr/bin/vmmap", "-w", str(PID)],
                          capture_output=True, text=True).stdout


SZ = re.compile(r"^(\d+(?:\.\d+)?)([KMG]?)$")


def parse_sz(tok):
    m = SZ.match(tok)
    if not m:
        return None
    v = float(m.group(1))
    return int(v * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)])


ROW = re.compile(r"^(.{1,30}?)\s{2,}([0-9a-f]+)-([0-9a-f]+)\s+\[\s*([^\]]+)\]\s+(\S+)\s+(\S+)(?:\s+(.*))?$")


def regions(text):
    """(kind, start, vsize, resident, dirty, swap, path) を返す。"""
    out = []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        nums = m.group(4).split()
        if len(nums) < 4:
            continue
        vs, res, dirty, swap = (parse_sz(x) for x in nums[:4])
        if None in (vs, res, dirty, swap):
            continue
        out.append((m.group(1).strip(), int(m.group(2), 16), vs, res, dirty, swap,
                    (m.group(7) or "").strip()))
    return out


def summarize(text, tag):
    rs = regions(text)
    dic_res = sum(r[3] for r in rs if DICDIR in r[6])
    dic_vs = sum(r[2] for r in rs if DICDIR in r[6])
    malloc_res = sum(r[3] for r in rs if r[0].startswith("MALLOC"))
    malloc_dirty = sum(r[4] for r in rs if r[0].startswith("MALLOC"))
    other_res = sum(r[3] for r in rs) - dic_res - malloc_res
    print(f"[{tag}] 領域 {len(rs)} 件 / ps RSS {rss_bytes():,d} B")
    print(f"    辞書 file-backed : vsize {dic_vs:>12,d} B  resident {dic_res:>12,d} B")
    print(f"    MALLOC           : resident {malloc_res:>12,d} B  dirty {malloc_dirty:>12,d} B")
    print(f"    その他           : resident {other_res:>12,d} B")
    return dict(dic_res=dic_res, dic_vs=dic_vs, malloc_res=malloc_res,
                malloc_dirty=malloc_dirty, other_res=other_res, rss=rss_bytes())


def dic_lines(text):
    return [l.rstrip() for l in text.splitlines() if DICDIR in l]


print("=== A. import 直後（まだ 1 文も解析していない） ===")
import pyopenjtalk  # noqa: E402
a = summarize(vmmap_text(), "import 後")
print("    辞書ファイルの行:")
for l in dic_lines(vmmap_text()):
    print("      ", l)

print("\n=== B. 1 文だけ解析 ===")
pyopenjtalk.run_frontend("今日は良い天気ですね。")
b = summarize(vmmap_text(), "1 文後")
for l in dic_lines(vmmap_text()):
    print("      ", l)

texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"), encoding="utf-8") as f:
    f.readline()
    for ln in f:
        texts.append(ln.rstrip("\n").split("\t")[2])
print(f"\nheld-out {len(texts)} 文")

for n in (100, 500, 2333):
    for t in texts[:n]:
        pyopenjtalk.extract_fullcontext(t)
    print(f"\n=== C. 先頭 {n} 文を解析した直後 ===")
    c = summarize(vmmap_text(), f"{n} 文後")
    for l in dic_lines(vmmap_text()):
        print("      ", l)

print("\n=== D. gc を回してから ===")
import gc  # noqa: E402
gc.collect()
d = summarize(vmmap_text(), "gc 後")

open(os.path.join(_WORK, "vmmap_final.txt"), "w").write(vmmap_text())
print("\nvmmap 全文を vmmap_final.txt に保存した")
