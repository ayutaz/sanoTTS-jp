"""M-106 §2 / §10 の動作点を **実測サイズ**で決め直す（matrixc を本物の形式で書く）。

    uv run python scripts/k1/k11_fit_2mb.py [--budget N] [--grid "N:K,N:K,..."]

k9_fit_8mb.py と違い **算術ではなく len(blob)**。文脈 ID の詰め直しも入れる。
"""
import json, pathlib, struct, subprocess, sys, re
from collections import defaultdict
import numpy as np
import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--budget", type=int, default=983_040,
                 help="dict パーティションの枠。既定は 2 MB flash 相当 983,040。"
                      "4 MB / DevKit は 3014656 / 8 MB は 7143424")
_ap.add_argument("--grid", default="44000:256,48000:256,52000:256,60000:256",
                 help='"entries:K" をカンマ区切りで')
_a = _ap.parse_args()
BUDGET = _a.budget
GRID = [tuple(int(x) for x in t.split(":")) for t in _a.grid.split(",")]

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO + '/scripts/k1'); sys.path.insert(0, REPO + '/src')
from dump_entries_lib import load_entries
import k1_paths
from saanotts_jp.jdict import (CharProperty, ConnMatrix, ConnMatrixCluster,
                               DictBlob, Entry, UnkDict)
D = pathlib.Path(str(k1_paths.DICT_VENV))
BASE = pathlib.Path(REPO + '/csrc/kanji_e2e_vectors.bin')
# ⚠️ **char レンジ表は実際に blob へ入れる**（M-106 §5 で C リーダを書いた）。
#    かつてここは 370 B の算術だったが、**実測は 832 B**（カテゴリ名 352 B + 値表を含む）。

# ⚠️ **必ず make を通してから測る。** `csrc/label_ids_test` を直接叩くと、
#    jdict.c を変えた後でも古いバイナリが走り、**別の精度が出る**（今日 1 回踏んだ。
#    そのときは jdict_open が拒否したので気づけたが、**通ってしまう変更なら気づけない**）。
subprocess.run(["make", "-s", "-C", REPO + "/csrc", "label_ids_test"], check=True)

raw = load_entries(str(D))
bysurf = defaultdict(list)
for r in raw: bysurf[r[0]].append(r)
ranked = json.loads((pathlib.Path(k1_paths.WORK) / "rank_cache.json").read_text())
ranked = [s for s in bysurf if len(s) == 1] + [s for s in ranked if len(s) != 1]
unk0 = UnkDict.from_unk_dic((D / "unk.dic").read_bytes())
mat0 = ConnMatrix.from_matrix_bin((D / "matrix.bin").read_bytes())
M0 = np.frombuffer(mat0.data, dtype="<i2").reshape(mat0.rsize, mat0.lsize)
char = CharProperty.from_char_bin((D / "char.bin").read_bytes())
b = BASE.read_bytes(); p = 8
ndan, = struct.unpack_from('<I', b, p); p += 4
for _ in range(ndan):
    l, = struct.unpack_from('<H', b, p); p += 2 + l + 1
old, = struct.unpack_from('<I', b, p)
HEAD, TAIL = b[:p], b[p + 4 + old:]

print("entries\t見出し\t|ctx|\tK\tmatrixc\tcharr\t**blob 実測**\t枠\t文一致\t音素%")
for N, K in GRID:
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= N: break
    es = [Entry(r[0],r[1],r[2],r[3],r[4],0,r[5],r[6],r[7],r[8],r[9]) for r in sub]
    lcu = sorted({e.lc for e in es} | {u.lc for u in unk0.entries} | {0})
    rcu = sorted({e.rc for e in es} | {u.rc for u in unk0.entries} | {0})
    rl = {o:i for i,o in enumerate(lcu)}; rr = {o:i for i,o in enumerate(rcu)}
    es = [e._replace(lc=rl[e.lc], rc=rr[e.rc]) for e in es]
    unk = UnkDict([u._replace(lc=rl[u.lc], rc=rr[u.rc]) for u in unk0.entries])
    sub_m = ConnMatrix(len(rcu), len(lcu), M0[np.ix_(lcu,rcu)].astype("<i2").tobytes())
    clu = ConnMatrixCluster.from_int16(sub_m, K)
    db_ = DictBlob.build(es, matrix=clu, char_prop=char, unk=unk)
    db_.char_range = True                      # `charr`（レンジ表）で書く
    blob = db_.to_bytes()
    s2 = DictBlob.sections(blob)
    pathlib.Path("/tmp/k11.bin").write_bytes(HEAD + struct.pack('<I', len(blob)) + blob + TAIL)
    out = subprocess.run([REPO+"/csrc/label_ids_test","/tmp/k11.bin"],
                         capture_output=True).stdout.decode("utf-8","replace")
    m1 = re.search(r"ホスト既定と一致 (\d+) / (\d+)", out)
    m2 = re.search(r"音素だけの列\s+編集距離 (\d+) / ホスト長 (\d+) = \*\*([\d.]+)%", out)
    fits = f"余り {BUDGET-len(blob):,d}" if len(blob) <= BUDGET else f"+{len(blob)-BUDGET:,d}"
    print(f"{len(es)}\t{len({e.surface for e in es})}\t{len(lcu)}\t{K}\t{s2['matrixc'][1]}\t"
          f"{s2['charr'][1]}\t{len(blob)}\t{fits}\t{m1.group(1) if m1 else '?'}\t{m2.group(3) if m2 else '?'}",
          flush=True)
