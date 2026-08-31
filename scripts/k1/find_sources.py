"""OpenJTalk / mecab の C ソースを探す。探索範囲を明示的に記録する。"""
import os, sys, time

ROOTS = [
    os.path.expanduser("~/Desktop/saanoTTS-jp"),
    os.path.expanduser("~/Documents/piper-plus"),
    os.path.expanduser("~/.cache/uv"),
    os.path.expanduser("~/.cache/pip"),
    os.path.expanduser("~/Library/Caches/uv"),
    os.path.expanduser("~/Library/Caches/pip"),
    os.path.expanduser("~/open_jtalk"),
    os.path.expanduser("~/src"),
    "/opt/homebrew",
    "/usr/local",
]

TARGETS = {"mecab.h", "dictionary.cpp", "mmap.h", "darts.h", "tokenizer.cpp",
           "viterbi.cpp", "njd.h", "jpcommon.h", "mecab.cpp", "connector.cpp",
           "njd_set_pronunciation.c", "text2mecab.c"}

found = {}
scanned_roots = []
t0 = time.time()
for root in ROOTS:
    if not os.path.isdir(root):
        scanned_roots.append((root, "MISSING"))
        continue
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # 巨大な無関係ディレクトリを刈る
        dirnames[:] = [d for d in dirnames if d not in
                       (".git", "node_modules", "__pycache__", ".mypy_cache")]
        n += 1
        for f in filenames:
            if f in TARGETS:
                found.setdefault(f, []).append(os.path.join(dirpath, f))
        if time.time() - t0 > 240:
            scanned_roots.append((root, f"TIMEOUT after {n} dirs"))
            break
    else:
        scanned_roots.append((root, f"OK {n} dirs"))

print("=== 探索した root ===")
for r, s in scanned_roots:
    print(f"{s:30s} {r}")
print()
print("=== 見つかったファイル ===")
for f in sorted(TARGETS):
    paths = found.get(f, [])
    print(f"--- {f}: {len(paths)} 件")
    for p in paths[:12]:
        try:
            sz = os.path.getsize(p)
        except OSError:
            sz = -1
        print(f"      {sz:>10d} B  {p}")
