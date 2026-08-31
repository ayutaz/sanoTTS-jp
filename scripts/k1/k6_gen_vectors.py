"""K-6: 端末の全段をホストと突き合わせるベクタを作る。

端末が走らせる経路（K-2 の Viterbi → K-4b の NJD → K-4 の 4 段 → ラベル）を、
**ホストの同じ経路**と比べる。入力は**漢字かな交じり文そのもの**。

⚠️ **ホスト側の基準を 2 本用意する。**

| 基準 | 何と比べているか |
|---|---|
| `full` | `run_frontend(t)` = ホスト既定（後処理 7 段） |
| `dev`  | `run_frontend(t, predict_nani=False, use_sudachi_kanji_yomi=False)` |

`dev` は**端末に載る段だけ**を通したもの。ONNX の「何」推定と Sudachi は
端末に載らないので（M-70 / C-049）、`full` との差は**移植の誤りではない**。
2 本持つことで「うちの port が違う」と「そもそも端末には無理」を切り分ける。

⚠️ **辞書は枝刈りしたものを使う。** ホストはフル辞書（789,388 entries）なので、
枝刈りで落ちた語の分は必ず食い違う。それが D-043 で許容した 0.60% の正体。

出力（既定 `csrc/k6_vectors.bin`）:

    magic "K6V2" u32 / n_cases u32
    _DAN_MAP: n u32 →（かな 1 文字 UTF-8 / 母音 1 B）× n
    blob_len u32 / <辞書 blob>
    ケース × n:
        text（u16 長 + UTF-8）
        feat: n u32 +（u16 長 + UTF-8）× n   ← ホストの MeCab feature 列
        full: n u32 +（u16 長 + UTF-8）× n
        dev : n u32 +（u16 長 + UTF-8）× n
        ids_dev : n u32 + i32 × n   ← dev ラベルからホストが作る生徒インデックス
        ids_full: n u32 + i32 × n   ← ホスト既定（フル辞書）の生徒インデックス

⚠️ **feature 列も持つ。** これが無いと「食い違い 18%」が
辞書の枝刈りのせいなのか素性復元の誤りなのか切り分けられない。
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))

from dump_entries_lib import load_entries                       # noqa: E402
from k1_paths import HELDOUT, TRAIN                             # noqa: E402
from saanotts_jp.k1_dict import (CharProperty, ConnMatrix, DictBlob,  # noqa: E402
                                 Entry, UnkDict)


def _s(x: str) -> bytes:
    b = x.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=370_863)   # D-042
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--out", default=str(HERE.parent.parent / "csrc/k6_vectors.bin"))
    ap.add_argument("--skip-verify-dict", action="store_true")
    ap.add_argument("--matrix-int8", choices=["sym", "affine"], default=None,
                    help="接続行列を 1 B に丸める（判断 D の材料。M-72）。"
                         "⚠️ **形式は int16 のまま値だけ丸める** — "
                         "精度への影響だけを測るため。サイズの削減は別の話")
    ap.add_argument("--keep-single-char", action="store_true",
                    help="1 文字の見出し語を**必ず残す**（未知語フォールバックの土台。"
                         "M-73: 落ちる語の 97.9%% は単漢字で文字を覆える）")
    a = ap.parse_args()

    if not a.skip_verify_dict:
        import k0_verify_dict
        if k0_verify_dict.main() != 0:
            print("\nNG! 辞書が D-042 の凍結物と違う。ここで止める。")
            return 1

    import pyopenjtalk
    import k1_paths
    dic = str(k1_paths.DICT_VENV)

    raw = load_entries(dic)
    bysurf = defaultdict(list)
    for r in raw:
        bysurf[r[0]].append(r)

    # 順位: TRAIN の頻度 → 残りを単語コスト順（k1_fit_point.py と同じ）
    freq: Counter[str] = Counter()
    with open(TRAIN, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                for ft in pyopenjtalk.run_mecab(p[2]):
                    s = ft.split(",", 1)[0]
                    if s in bysurf:
                        freq[s] += 1
    ranked = [s for s, _ in freq.most_common()]
    seen = set(ranked)
    ranked += sorted((s for s in bysurf if s not in seen),
                     key=lambda s: (min(e[3] for e in bysurf[s]), len(s)))
    if a.keep_single_char:
        # ⚠️ **順位の先頭に持ってくるのではなく、別枠で確保する。**
        #    頻度順を壊すと他の測定と比べられなくなる。
        single = [s for s in bysurf if len(s) == 1]
        n_single = sum(len(bysurf[s]) for s in single)
        rest = [s for s in ranked if len(s) != 1]
        ranked = single + rest
        print(f"1 文字の見出し語を確保: {len(single):,d} 語 / {n_single:,d} entries")

    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= a.entries:
            break
    entries = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
               for r in sub]
    D = pathlib.Path(dic)
    matrix = ConnMatrix.from_matrix_bin((D / "matrix.bin").read_bytes())
    if a.matrix_int8:
        import numpy as np
        M = np.frombuffer(matrix.data, dtype="<i2").reshape(
            matrix.rsize, matrix.lsize).astype(np.int32)
        if a.matrix_int8 == "sym":
            amax = np.abs(M).max(axis=1)
            sc = np.where(amax == 0, 1.0, amax / 127.0)
            q = np.rint(M / sc[:, None]).clip(-127, 127)
            M2 = np.rint(q * sc[:, None]).astype("<i2")
        else:
            lo = M.min(axis=1); hi = M.max(axis=1)
            sc = np.where(hi == lo, 1.0, (hi - lo) / 255.0)
            q = np.rint((M - lo[:, None]) / sc[:, None]).clip(0, 255)
            M2 = np.rint(q * sc[:, None] + lo[:, None]).astype("<i2")
        print(f"⚠️ 行列を {a.matrix_int8} で 1 B に丸めた: "
              f"最大誤差 {int(np.abs(M2.astype(np.int32) - M).max())}")
        matrix = ConnMatrix(matrix.lsize, matrix.rsize, M2.tobytes())
    blob = DictBlob.build(
        entries,
        matrix=matrix,
        char_prop=CharProperty.from_char_bin((D / "char.bin").read_bytes()),
        unk=UnkDict.from_unk_dic((D / "unk.dic").read_bytes()),
    ).to_bytes()
    print(f"blob {len(blob):,d} B / {len(entries):,d} entries")

    texts = []
    with open(HELDOUT, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                texts.append(p[2])
    if a.cases < len(texts):
        texts = [texts[int(i * len(texts) / a.cases)] for i in range(a.cases)]

    import piper_plus_g2p.japanese as J   # キャッシュを毎回落とすため
    sys.path.insert(0, str(HERE.parent))
    import kana_g2p as K                                        # noqa: E402
    from gen_g2p_vectors import intersperse                     # noqa: E402
    from piper_plus_g2p.japanese import JapanesePhonemizer      # noqa: E402
    ph = JapanesePhonemizer()
    orig_efc = J.pyopenjtalk.extract_fullcontext

    def ids_from_labels(text, labels):
        """⚠️ **canonical な `phonemize()` をそのまま使う。**
        ラベル → 音素の規則を生成器側で書き直すと、C の移植と
        「同じ間違い」を突き合わせることになって検査にならない。"""
        J.pyopenjtalk.extract_fullcontext = lambda *_a, **_k: list(labels)
        try:
            J._phonemize_core_cached.cache_clear()
            return intersperse(ph.phonemize(K.normalize_input(text)))
        finally:
            J.pyopenjtalk.extract_fullcontext = orig_efc
            J._phonemize_core_cached.cache_clear()

    cases = []
    n_diff = 0
    for t in texts:
        try:
            feats = pyopenjtalk.run_mecab(t)
            J._phonemize_core_cached.cache_clear()
            full = pyopenjtalk.make_label(pyopenjtalk.run_frontend(t))
            J._phonemize_core_cached.cache_clear()
            dev = pyopenjtalk.make_label(pyopenjtalk.run_frontend(
                t, predict_nani=False, use_sudachi_kanji_yomi=False))
        except Exception:
            continue
        if not full:
            continue
        if full != dev:
            n_diff += 1
        try:
            ids_dev = ids_from_labels(t, dev)
            J._phonemize_core_cached.cache_clear()
            ids_full = intersperse(ph.phonemize(K.normalize_input(t)))
        except Exception:
            continue
        cases.append((t, feats, full, dev, ids_dev, ids_full))

    print(f"ケース {len(cases)}")
    print(f"⚠️ ホスト既定 vs 端末に載る段だけ: {n_diff} / {len(cases)} 文が食い違う"
          f"（{100*n_diff/max(1,len(cases)):.2f}%）= **端末が原理的に届かない分**")

    from pyopenjtalk import utils as pjt_utils
    dan = pjt_utils._DAN_MAP
    out = bytearray(struct.pack("<4sI", b"K6V2", len(cases)))
    out += struct.pack("<I", len(dan))
    for k, v in sorted(dan.items()):
        out += _s(k) + v.encode("ascii")[:1]
    out += struct.pack("<I", len(blob)) + blob
    print(f"_DAN_MAP {len(dan)} 件")
    for t, feats, full, dev, ids_dev, ids_full in cases:
        out += _s(t)
        for group in (feats, full, dev):
            out += struct.pack("<I", len(group))
            for x in group:
                out += _s(x)
        for arr in (ids_dev, ids_full):
            out += struct.pack("<I", len(arr))
            out += struct.pack(f"<{len(arr)}i", *arr)
    pathlib.Path(a.out).write_bytes(bytes(out))
    print(f"書き出した → {a.out} ({len(out):,d} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
