"""M-108 §6: 438,750 と 538,000 の**文ごとの一致**を突き合わせ、実際の分割表で McNemar を出す。

    uv run python scripts/k1/k13_rec5_mcnemar.py

⚠️ **「1258 → 1279 で +21 文」だけでは足りない。** 改善と悪化の内訳が分からないと、
   「21 文良くなった」のか「41 文良くなって 20 文壊れた」のか区別できない。
   **対応のある 2 値なので McNemar**（同じ 1,495 文を 2 つの辞書で解析している）。

⚠️ **分母が同じことを先に確かめる**（M-107 の事故）。突き合わせるのは
   両方のダンプに現れた文だけで、件数を必ず出力する。

⚠️ **make を通してから測る**（古いバイナリの事故。M-106 §10）。
"""
import json, pathlib, struct, subprocess, sys
from collections import defaultdict
from math import comb
REPO = "/Users/s19447/Desktop/saanoTTS-jp"
sys.path.insert(0, REPO + '/scripts/k1'); sys.path.insert(0, REPO + '/src')
from dump_entries_lib import load_entries
import k1_paths
from saanotts_jp.jdict import CharProperty, ConnMatrix, DictBlob, Entry, UnkDict
D = pathlib.Path(str(k1_paths.DICT_VENV)); BASE = pathlib.Path(REPO + '/csrc/kanji_e2e_vectors.bin')
raw = load_entries(str(D)); bysurf = defaultdict(list)
for r in raw: bysurf[r[0]].append(r)
ranked = json.loads((pathlib.Path(k1_paths.WORK)/"rank_cache.json").read_text())
ranked = [s for s in bysurf if len(s)==1] + [s for s in ranked if len(s)!=1]
unk = UnkDict.from_unk_dic((D/"unk.dic").read_bytes())
mat = ConnMatrix.from_matrix_bin((D/"matrix.bin").read_bytes())
char = CharProperty.from_char_bin((D/"char.bin").read_bytes())
b = BASE.read_bytes(); p = 8
ndan, = struct.unpack_from('<I', b, p); p += 4
for _ in range(ndan):
    l, = struct.unpack_from('<H', b, p); p += 2 + l + 1
old, = struct.unpack_from('<I', b, p)
HEAD, TAIL = b[:p], b[p+4+old:]

def run(N, tag):
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= N: break
    es = [Entry(r[0],r[1],r[2],r[3],r[4],0,r[5],r[6],r[7],r[8],r[9]) for r in sub]
    db = DictBlob.build(es, matrix=mat, char_prop=char, unk=unk); db.rec5 = True
    blob = db.to_bytes()
    pathlib.Path("/tmp/mc.bin").write_bytes(HEAD + struct.pack('<I', len(blob)) + blob + TAIL)
    subprocess.run(["make", "-s", "-C", REPO + "/csrc", "label_ids_test"], check=True)
    subprocess.run([REPO+"/csrc/label_ids_test","/tmp/mc.bin","--dump-ids",f"/tmp/ids_{tag}.tsv"],
                   capture_output=True)
    d = {}
    for ln in pathlib.Path(f"/tmp/ids_{tag}.tsv").read_text(errors="replace").splitlines():
        f = ln.split("\t")
        if len(f) >= 3: d[f[0]] = (f[1] == f[2])
    return d

A = run(438750, "a"); B = run(538000, "b")
keys = sorted(set(A) & set(B))
imp = sum(1 for k in keys if not A[k] and B[k])
wor = sum(1 for k in keys if A[k] and not B[k])
print(f"突き合わせた文 {len(keys)}（438,750 のダンプ {len(A)} / 538,000 のダンプ {len(B)}）")
print(f"  438,750 で一致 {sum(A[k] for k in keys)} / 538,000 で一致 {sum(B[k] for k in keys)}")
print(f"\n**分割表**: 改善 {imp} 文 / 悪化 {wor} 文 / 変化なし {len(keys)-imp-wor}")
m = imp + wor
if m:
    lo = min(imp, wor)
    pv = min(1.0, sum(comb(m,i) for i in range(lo+1)) / (2**m) * 2)
    print(f"  McNemar 正確検定（両側）p = {pv:.3e}")
