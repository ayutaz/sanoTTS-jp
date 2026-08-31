"""【最後の関門】OpenJTalk の mecab は辞書を mmap するか、RAM に read() するか。

read() なら 103 MB がヒープに載り、フラッシュの議論は全部無意味になる。
ESP32-S3 は SRAM 512 KB / PSRAM 8 MB なので、どちらでも 103 MB は載らないが、
**mmap ならフラッシュから直接読めるので載る**。ここが判定を決める。

3 通りで測る（1 つの手段に頼らない）:
  A. RSS の増分（resource.getrusage）
  B. プロセスのメモリマップに sys.dic がファイル由来で現れるか（psutil）
  C. マップ済み領域の合計サイズ
"""
import os
import resource
import sys


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS は bytes、Linux は KB
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


print(f"platform = {sys.platform}")
base = rss_mb()
print(f"import 前 maxRSS = {base:8.2f} MB")

import pyopenjtalk  # noqa: E402
after_import = rss_mb()
print(f"import 後 maxRSS = {after_import:8.2f} MB  (+{after_import-base:.2f})")

DIC = pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()
sysdic = os.path.join(DIC, "sys.dic")
sysdic_mb = os.path.getsize(sysdic) / (1024 * 1024)
print(f"sys.dic = {sysdic_mb:.2f} MB  ({sysdic})")

# 辞書を実際にロードさせる
_ = pyopenjtalk.run_mecab_detailed("これは辞書をロードさせるための文です。")
after_load = rss_mb()
print(f"辞書ロード後 maxRSS = {after_load:8.2f} MB  "
      f"(+{after_load-after_import:.2f} from import)")

# 全体を舐める（mmap なら触ったページだけ RSS に載る）
n = 0
for i in range(0, 2000):
    pass
after_use = rss_mb()

print(f"\n=== A. RSS 増分による判定 ===")
delta = after_load - base
print(f"  合計増分 = {delta:.2f} MB / sys.dic = {sysdic_mb:.2f} MB")
if delta > sysdic_mb * 0.8:
    print("  → 辞書サイズに匹敵する増分。**read() でヒープに載せている疑い**")
else:
    print(f"  → 辞書サイズの {100*delta/sysdic_mb:.1f}% しか増えていない。"
          f"**mmap の可能性が高い**")
print("  ⚠️ RSS は間接証拠。mmap でも触ったページは RSS に載るので、"
      "これだけでは決められない。")

print(f"\n=== B. メモリマップに sys.dic が現れるか（直接証拠）===")
try:
    import psutil
    p = psutil.Process()
    maps = p.memory_maps(grouped=False)
    hits = [m for m in maps if "sys.dic" in m.path or "open_jtalk" in m.path
            or "dictionary" in m.path]
    print(f"  マップ総数 = {len(maps)}")
    if hits:
        for m in hits:
            print(f"  **ファイル由来のマップを検出**: {m.path}")
            print(f"     rss={getattr(m,'rss',0)/1048576:.2f} MB "
                  f"size={getattr(m,'size',0)/1048576:.2f} MB")
        print("  → **mmap されている（確定的証拠）**")
    else:
        print("  sys.dic のファイルマップは見つからなかった")
        print("  → read() でヒープに読んでいるか、psutil が拾えていない")
except ImportError:
    print("  psutil が無い。C にフォールバック")

print(f"\n=== C. vmmap による確認（macOS）===")
print(f"  pid = {os.getpid()}")
print(f"  手動確認: vmmap {os.getpid()} | grep -i 'sys.dic\\|open_jtalk'")

print(f"\n=== D. 陰性対照: 同じサイズのファイルを read() したら RSS はどう動くか ===")
before = rss_mb()
with open(sysdic, "rb") as f:
    blob = f.read()
after = rss_mb()
print(f"  明示的に read() したときの増分 = {after-before:.2f} MB "
      f"(sys.dic {sysdic_mb:.2f} MB)")
print(f"  → read() すれば RSS はこれだけ動く。上の A の増分と比べること。")
del blob
