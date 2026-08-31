"""mmap か read() かを **直接証拠**で決める。

A. vmmap でプロセスのマップに sys.dic がファイル由来で現れるか
B. 拡張モジュールの .so に mmap 系のシンボルが入っているか（nm）
C. 陰性対照: 何もマップしていない時点では出ないこと
"""
import os
import subprocess
import sys

import pyopenjtalk

DIC = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
SO = os.path.join(os.path.dirname(pyopenjtalk.__file__),
                  "openjtalk.cpython-314-darwin.so")
pid = os.getpid()


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120).stdout
    except Exception as e:
        return f"(失敗: {e})"


print("=== C. 陰性対照: 辞書をロードする前の vmmap ===")
out0 = run(["vmmap", str(pid)])
hits0 = [l for l in out0.splitlines()
         if "sys.dic" in l or "open_jtalk" in l or "dictionary" in l]
print(f"  vmmap 出力行数 = {len(out0.splitlines())}")
print(f"  sys.dic / dictionary を含む行 = {len(hits0)}")
for l in hits0[:5]:
    print("   ", l.strip()[:150])

print("\n=== 辞書をロードする ===")
_ = pyopenjtalk.run_mecab_detailed("辞書をロードさせるための文です。")
print("  ロード完了")

print("\n=== A. ロード後の vmmap ===")
out1 = run(["vmmap", str(pid)])
hits1 = [l for l in out1.splitlines()
         if "sys.dic" in l or "open_jtalk" in l or "dictionary" in l]
print(f"  vmmap 出力行数 = {len(out1.splitlines())}")
print(f"  sys.dic / dictionary を含む行 = {len(hits1)}")
for l in hits1[:12]:
    print("   ", l.strip()[:160])

if len(hits1) > len(hits0):
    print("\n  → **辞書ファイルがプロセスのマップに現れた = mmap されている**")
elif hits1:
    print("\n  → マップに現れている（ロード前から。import 時にマップ済み）= mmap")
else:
    print("\n  → マップに現れない。read() の疑い、または vmmap が使えていない")

print("\n=== B. 拡張モジュールの mmap シンボル ===")
print(f"  {SO}  exists={os.path.exists(SO)}")
nm = run(["nm", "-u", SO])
syms = [l.strip() for l in nm.splitlines()
        if "mmap" in l.lower() or "munmap" in l.lower()]
print(f"  未定義シンボルに含まれる mmap 系: {syms if syms else 'なし'}")
strs = run(["strings", "-a", SO])
mm = sorted(set(l for l in strs.splitlines()
                if "mmap" in l.lower() and len(l) < 80))
print(f"  文字列中の mmap 系（先頭 10）: {mm[:10] if mm else 'なし'}")

print("\n=== 参考: MeCab は Mmap<T> クラスで辞書を開く実装が標準 ===")
mecab_syms = sorted(set(l.strip() for l in strs.splitlines()
                        if "Mmap" in l and len(l) < 100))
print(f"  'Mmap' を含む文字列: {mecab_syms[:8] if mecab_syms else 'なし'}")
