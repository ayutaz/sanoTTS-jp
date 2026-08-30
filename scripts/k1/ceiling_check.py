"""C 実装のみ (use_vanilla=True) の天井を独立に再現する。

ceiling-lane にも頼んであるが、**判定を左右する唯一の数字**なので自分でも測る。
B-0 の申告: 音素列 95.4% / アクセント句 76.22%。

単位を必ず明示する（このプロジェクトはトークン/音素/符号化 ID の 3 単位を
取り違えて論争になった前科がある）。ここで測る単位:
  U1 音素列（pyopenjtalk.g2p 相当の音素トークン列）文単位完全一致
  U2 アクセント記号列（piper-plus canonical の [ ] # を含むトークン列）文単位完全一致
  U3 (acc, mora_size) の対の列（NJD レベル）文単位完全一致
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
import sys

import pyopenjtalk

ROOT = (_ROOT + "")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 500

texts = []
with open(f"{ROOT}/data/splits/corpus_heldout.tsv", encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            texts.append(p[2])
texts = texts[:LIMIT]
print(f"n = {len(texts)} 文", flush=True)


def njd(text, vanilla):
    return pyopenjtalk.run_frontend(text, use_vanilla=vanilla)


def u1(fs):
    """音素列。NJD feature の pron を並べる（単位: NJD ノードの pron 連結）"""
    return "".join(f["pron"] for f in fs)


def u3(fs):
    return [(f["acc"], f["mora_size"]) for f in fs]


def u_pos(fs):
    return [f["string"] for f in fs]


same_u1 = same_u3 = same_seg = 0
n = 0
diff_examples = []
for t in texts:
    try:
        a = njd(t, False)      # 既定（SudachiDict + nani 後処理あり）
        b = njd(t, True)       # C 実装のみ
    except Exception:
        continue
    n += 1
    if u1(a) == u1(b):
        same_u1 += 1
    if u3(a) == u3(b):
        same_u3 += 1
    if u_pos(a) == u_pos(b):
        same_seg += 1
    elif len(diff_examples) < 8:
        diff_examples.append((t, u_pos(a), u_pos(b)))

print(f"\n=== C 実装のみ (use_vanilla=True) vs 既定 ===")
print(f"  評価文 n = {n}")
print(f"  U0 分割（NJD ノードの string 列）一致 {same_seg}/{n} = {100*same_seg/n:.2f}%")
print(f"  U1 読み（pron 連結）一致            {same_u1}/{n} = {100*same_u1/n:.2f}%")
print(f"  U3 (acc, mora_size) 列 一致        {same_u3}/{n} = {100*same_u3/n:.2f}%")
print(f"\n  B-0 の申告: 音素列 95.4% / アクセント句 76.22%")

# --- スイッチ単体の寄与 ---
print(f"\n=== 後処理スイッチ単体の寄与（既定から 1 つずつ切る）===")
SW = ["use_sudachi_kanji_yomi", "predict_nani"]
for sw in SW:
    kw = {sw: False}
    ok1 = ok3 = m = 0
    for t in texts[:min(len(texts), 300)]:
        try:
            a = njd(t, False)
            c = pyopenjtalk.run_frontend(t, **kw)
        except Exception:
            continue
        m += 1
        if u1(a) == u1(c):
            ok1 += 1
        if u3(a) == u3(c):
            ok3 += 1
    print(f"  {sw}=False : 読み一致 {ok1}/{m} = {100*ok1/max(m,1):.2f}%  "
          f"/ acc 一致 {ok3}/{m} = {100*ok3/max(m,1):.2f}%")

print(f"\n=== 分割が変わった例 ===")
for t, a, b in diff_examples[:5]:
    print(f"  {t}")
    print(f"    既定  : {a}")
    print(f"    vanilla: {b}")

print(f"\n=== 陰性対照: 同じ設定を 2 回呼べば 100% になるか ===")
same = sum(1 for t in texts[:100] if u1(njd(t, True)) == u1(njd(t, True)))
print(f"  vanilla vs vanilla: {same}/100 = {same}%  (100 でなければ非決定的)")
