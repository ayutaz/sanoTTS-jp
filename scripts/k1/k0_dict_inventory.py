"""K-0: このマシンにある sys.dic を全部数え上げ、SHA-256 と実体を出す。

K-1 §9-2 の通り **4 種類ある**。B-0 はそのうち piper-plus 側で測り、
教師のラベル生成経路は pyopenjtalk 同梱を引く。**どれを正とするかを凍結する**
ための材料を出す。

⚠️ このスクリプトは読むだけ。piper-plus には一切書き込まない（D-003）。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k1_paths import PP  # noqa: E402

CANDIDATE_DIRS = [
    ("piper-plus build", os.path.join(PP, "build/share/open_jtalk/dic")),
    ("piper-plus venv", os.path.join(PP, ".venv/lib/python3.13/site-packages/pyopenjtalk/dictionary")),
]
try:
    import pyopenjtalk
    CANDIDATE_DIRS.insert(0, ("本プロジェクト .venv (pyopenjtalk が実際に引く)",
                              pyopenjtalk.OPEN_JTALK_DICT_DIR.decode()))
except Exception as e:
    print(f"⚠️ pyopenjtalk を import できない: {e}")

# uv のキャッシュや他の venv も一応さらう
HOME = pathlib.Path(os.path.expanduser("~"))
for extra in [HOME / ".cache/uv", HOME / "Desktop/saanoTTS-jp/.venv"]:
    if not extra.is_dir():
        continue
    try:
        for p in extra.rglob("open_jtalk/dic/sys.dic"):
            CANDIDATE_DIRS.append(("(探索) " + str(p.parent), str(p.parent)))
        for p in extra.rglob("pyopenjtalk/dictionary/sys.dic"):
            CANDIDATE_DIRS.append(("(探索) " + str(p.parent), str(p.parent)))
    except Exception:
        pass


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def header(path):
    with open(path, "rb") as f:
        raw = f.read(72)
    (magic, version, dtype, lexsize, lsize, rsize,
     dsize, tsize, fsize, _d) = struct.unpack("<10I", raw[:40])
    charset = raw[40:72].split(b"\0")[0].decode()
    return dict(lexsize=lexsize, lsize=lsize, rsize=rsize, charset=charset)


seen = {}
rows = []
for label, d in CANDIDATE_DIRS:
    p = os.path.join(d, "sys.dic")
    if not os.path.exists(p):
        continue
    real = os.path.realpath(p)
    if real in seen:
        seen[real].append(label)
        continue
    seen[real] = [label]
    try:
        h = header(p)
    except Exception as e:
        print(f"  ヘッダ読めず {p}: {e}")
        continue
    rows.append(dict(label=label, dir=d, size=os.path.getsize(p),
                     sha=sha256(p), **h))

print(f"=== sys.dic を {len(rows)} 種類みつけた ===\n")
for r in rows:
    print(f"  {r['label']}")
    print(f"    {r['dir']}")
    print(f"    size    = {r['size']:,d} B")
    print(f"    lexsize = {r['lexsize']:,d}  lsize={r['lsize']} rsize={r['rsize']} "
          f"charset={r['charset']}")
    print(f"    sha256  = {r['sha']}")
    for alias in seen[os.path.realpath(os.path.join(r['dir'], 'sys.dic'))][1:]:
        print(f"    （同一実体）{alias}")
    print()

print("=== 付随ファイルは一致するか（B-0 §7-4 の主張の検証）===")
NAMES = ["matrix.bin", "char.bin", "unk.dic", "left-id.def", "right-id.def"]
if len(rows) >= 2:
    base = rows[0]
    for r in rows[1:]:
        print(f"  {base['label']}  vs  {r['label']}")
        for n in NAMES:
            a, b = os.path.join(base["dir"], n), os.path.join(r["dir"], n)
            if not (os.path.exists(a) and os.path.exists(b)):
                print(f"    {n:14s} (片方に無い)")
                continue
            same = sha256(a) == sha256(b)
            print(f"    {n:14s} {'一致' if same else '**不一致**'}")
        print()

print("=== 教師のラベル生成経路が引くのはどれか ===")
print("  scripts/gen_teacher_labels.py → kana_g2p → JapanesePhonemizer")
print("  → pyopenjtalk.extract_fullcontext()")
print("  OPEN_JTALK_DICT_DIR を設定している箇所:")
import subprocess
try:
    out = subprocess.run(
        ["grep", "-rn", "OPEN_JTALK_DICT_DIR",
         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
         ],
        capture_output=True, text=True, timeout=60).stdout
    hits = [l for l in out.splitlines() if "k0_dict_inventory" not in l]
    if hits:
        for l in hits[:12]:
            print("   ", l.strip()[:140])
    else:
        print("    （scripts/ 配下に無し）")
except Exception as e:
    print("    grep 失敗:", e)
