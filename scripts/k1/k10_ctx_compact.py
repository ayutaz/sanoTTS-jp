"""M-106 §3: 文脈 ID を詰め直す（未使用の行・列を落とす）。**無損失のはず**なのを実測で確かめる。

    uv run python <this> --entries N [--no-compact] [--matrix affine|int16] --out <vec>

k9_fit_8mb.py と違い、**本物の blob をその形式で書く**ので:
  - サイズは算術でなく `len(blob)` の実測
  - **既存の C リーダがそのまま読む**（matrixa は任意寸法を受ける）
"""
import argparse, pathlib, struct, sys, json
from collections import defaultdict
import numpy as np

REPO = str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO + '/scripts/k1'); sys.path.insert(0, REPO + '/src')
from dump_entries_lib import load_entries
import k1_paths
from saanotts_jp.jdict import (CharProperty, ConnMatrix, ConnMatrixAffine,
                               DictBlob, Entry, UnkDict, UnkEntry)

D = pathlib.Path(str(k1_paths.DICT_VENV))
BASE = pathlib.Path(REPO + '/csrc/kanji_e2e_vectors.bin')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, required=True)
    ap.add_argument("--matrix", default="affine", choices=["affine", "int16"])
    ap.add_argument("--no-compact", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    raw = load_entries(str(D))
    bysurf = defaultdict(list)
    for r in raw:
        bysurf[r[0]].append(r)
    ranked = json.loads((pathlib.Path(k1_paths.WORK) / "rank_cache.json").read_text())
    single = [s for s in bysurf if len(s) == 1]
    ranked = single + [s for s in ranked if len(s) != 1]
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= a.entries:
            break
    entries = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
               for r in sub]
    unk = UnkDict.from_unk_dic((D / "unk.dic").read_bytes())
    mat = ConnMatrix.from_matrix_bin((D / "matrix.bin").read_bytes())
    M = np.frombuffer(mat.data, dtype="<i2").reshape(mat.rsize, mat.lsize)

    if not a.no_compact:
        # ⚠️ **BOS_RC = 0 / EOS_LC = 0**（csrc/jdict.c:22-23）。必ず残し、0 → 0 に写す。
        lc_used = sorted({e.lc for e in entries} | {u.lc for u in unk.entries} | {0})
        rc_used = sorted({e.rc for e in entries} | {u.rc for u in unk.entries} | {0})
        assert lc_used[0] == 0 and rc_used[0] == 0, "0 が先頭でないと BOS/EOS がずれる"
        rl = {o: i for i, o in enumerate(lc_used)}
        rr = {o: i for i, o in enumerate(rc_used)}
        entries = [e._replace(lc=rl[e.lc], rc=rr[e.rc]) for e in entries]
        unk = UnkDict([u._replace(lc=rl[u.lc], rc=rr[u.rc]) for u in unk.entries])
        M = M[np.ix_(lc_used, rc_used)]
        mat = ConnMatrix(len(rc_used), len(lc_used), M.astype("<i2").tobytes())
        print(f"  文脈 ID を詰めた: lc 1377→{len(lc_used)} / rc 1377→{len(rc_used)}")
    if a.matrix == "affine":
        mat = ConnMatrixAffine.from_int16(mat)

    blob = DictBlob.build(
        entries, matrix=mat,
        char_prop=CharProperty.from_char_bin((D / "char.bin").read_bytes()),
        unk=unk).to_bytes()
    secs = DictBlob.sections(blob)
    mn = "matrixa" if a.matrix == "affine" else "matrix"
    print(f"entries={len(entries):,d} 見出し語={len({e.surface for e in entries}):,d} "
          f"matrix={a.matrix} compact={not a.no_compact}")
    print(f"  行列セクション {secs[mn][1]:,d} B")
    print(f"BLOBSIZE {len(blob)}")

    b = BASE.read_bytes(); p = 8
    ndan, = struct.unpack_from('<I', b, p); p += 4
    for _ in range(ndan):
        l, = struct.unpack_from('<H', b, p); p += 2 + l + 1
    old, = struct.unpack_from('<I', b, p)
    pathlib.Path(a.out).write_bytes(
        b[:p] + struct.pack('<I', len(blob)) + blob + b[p + 4 + old:])
    print(f"  → {a.out}")
    return 0


raise SystemExit(main())
