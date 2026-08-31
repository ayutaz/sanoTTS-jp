"""Q1(b) 実測: pyopenjtalk が sys.dic を mmap するか、ヒープに read() するか。

計測器そのものが 103 MB を検出できることを、陽性対照（sys.dic を bytes に読む）で先に示す。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import subprocess
import sys

DICDIR = (_ROOT + "/.venv/lib/python3.14/site-packages/pyopenjtalk/dictionary")
SYSDIC = os.path.join(DICDIR, "sys.dic")
PID = os.getpid()


def rss_bytes():
    """現在の RSS。macOS の ps は KiB 単位で返す。"""
    out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(PID)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out) * 1024


def vmmap():
    r = subprocess.run(["/usr/bin/vmmap", "-w", str(PID)], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else f"__VMMAP_FAILED rc={r.returncode}__\n{r.stderr}"


def dic_regions(text, needle="sys.dic"):
    """vmmap 出力から needle を含む行を拾う。"""
    return [l for l in text.splitlines() if needle in l]


def banner(s):
    print("\n" + "=" * 72)
    print(s)
    print("=" * 72)


sysdic_size = os.path.getsize(SYSDIC)
print(f"sys.dic      : {SYSDIC}")
print(f"sys.dic size : {sysdic_size:,d} B")
print(f"pid          : {PID}")

banner("[0] 計測器の較正 — 陽性対照: sys.dic を丸ごと bytes に読む")
r0 = rss_bytes()
blob = open(SYSDIC, "rb").read()
r1 = rss_bytes()
print(f"RSS  読み込み前 : {r0:>13,d} B")
print(f"RSS  読み込み後 : {r1:>13,d} B")
print(f"ΔRSS            : {r1 - r0:>13,d} B   (期待: sys.dic {sysdic_size:,d} B 前後)")
ratio = (r1 - r0) / sysdic_size
print(f"ΔRSS / sys.dic  : {ratio:.3f}")
CTRL_OK = 0.9 <= ratio <= 1.3
print(f"陽性対照: {'PASS' if CTRL_OK else 'FAIL'}  "
      f"— 103 MB のヒープコピーは RSS で見える（見えないなら以下の結論は無効）")

vm0 = vmmap()
if vm0.startswith("__VMMAP_FAILED"):
    print("vmmap が使えない:", vm0.splitlines()[0])
else:
    hits = dic_regions(vm0)
    print(f"vmmap で 'sys.dic' を含む行: {len(hits)} 件（この時点では file-backed マップは無いはず）")
    for h in hits:
        print("   ", h)
    # 陰性対照: 存在しない名前は 0 件でなければならない
    neg = dic_regions(vm0, "zzz_this_file_is_never_mapped")
    print(f"陰性対照 (存在しない名前): {len(neg)} 件  {'PASS' if len(neg) == 0 else 'FAIL'}")

del blob
r2 = rss_bytes()
print(f"RSS  解放後     : {r2:>13,d} B  (Δ {r2 - r1:,d} B)")

banner("[1] pyopenjtalk を import して 1 文解析する")
r3 = rss_bytes()
import pyopenjtalk  # noqa: E402
r4 = rss_bytes()
print(f"RSS  import 前  : {r3:>13,d} B")
print(f"RSS  import 後  : {r4:>13,d} B   Δ {r4 - r3:,d} B")

njd = pyopenjtalk.run_frontend("今日は良い天気ですね。")
r5 = rss_bytes()
print(f"RSS  1 文解析後 : {r5:>13,d} B   Δ(import から) {r5 - r4:,d} B")
print(f"ΔRSS (import + 解析) 合計 : {r5 - r3:,d} B")
print(f"辞書ディレクトリ実体      : {pyopenjtalk.OPEN_JTALK_DICT_DIR!r}")
print(f"run_frontend の要素数     : {len(njd)}")

banner("[2] vmmap: sys.dic は file-backed でマップされているか")
vm1 = vmmap()
if vm1.startswith("__VMMAP_FAILED"):
    print("vmmap 失敗:", vm1[:400])
else:
    with open((_WORK + "/vmmap_after.txt"), "w") as f:
        f.write(vm1)
    for name in ("sys.dic", "matrix.bin", "char.bin", "unk.dic"):
        hits = dic_regions(vm1, name)
        print(f"--- '{name}': {len(hits)} 行")
        for h in hits:
            print("   ", h.rstrip())
    neg = dic_regions(vm1, "zzz_this_file_is_never_mapped")
    print(f"陰性対照 (存在しない名前): {len(neg)} 件  {'PASS' if len(neg) == 0 else 'FAIL'}")

banner("[3] 300 文を解析して RSS がどう動くか")
texts = []
with open((_ROOT + "/data/splits/corpus_heldout.tsv"), encoding="utf-8") as f:
    f.readline()
    for ln in f:
        texts.append(ln.rstrip("\n").split("\t")[2])
r6 = rss_bytes()
for t in texts[:300]:
    pyopenjtalk.extract_fullcontext(t)
r7 = rss_bytes()
print(f"RSS  300 文前   : {r6:>13,d} B")
print(f"RSS  300 文後   : {r7:>13,d} B   Δ {r7 - r6:,d} B")

banner("[4] getrusage の maxrss（単位確認つき）")
import resource  # noqa: E402
mx = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"ru_maxrss 生値 : {mx:,d}")
print(f"ps の現在 RSS  : {rss_bytes():,d} B")
print("→ macOS の ru_maxrss はバイト単位。ps の値と桁を突き合わせて判断すること")
