"""Q4: 文長を伸ばしたときのスケーリングを測るための入力を作る。

held-out の実文を連結して、目標文字数に近い実在テキストを作る（テンプレート文は使わない）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")
texts = open(os.path.join(_WORK, "heldout_text.txt"), encoding="utf-8").read().splitlines()

TARGETS = [25, 50, 100, 200, 400, 800, 1600, 2400]
REPS = 20   # 各長さで 20 本つくる（n を確保する）

out = []
meta = []
cur = 0
for t in TARGETS:
    for r in range(REPS):
        s = ""
        while len(s) < t:
            s += texts[cur % len(texts)]
            cur += 1
        s = s[:t]
        out.append(s)
        meta.append(t)

with open(os.path.join(_WORK, "sweep_text.txt"), "w", encoding="utf-8", newline="\n") as f:
    for s in out:
        assert "\n" not in s and "\t" not in s
        f.write(s + "\n")
with open(os.path.join(_WORK, "sweep_target.txt"), "w") as f:
    for m in meta:
        f.write(f"{m}\n")

print(f"{len(out)} 本 / 目標長 {TARGETS} × {REPS} 本")
print(f"実際の文字数: min={min(len(s) for s in out)} max={max(len(s) for s in out)}")
print(f"UTF-8 バイト最大: {max(len(s.encode('utf-8')) for s in out)} "
      f"(saan_probe の buff は 8192 B)")
