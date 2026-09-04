"""K-2: C リーダ / Viterbi の検証ベクタを作る。

`csrc/` の作法にならい、**自己完結したバイナリ**にして C 側が読む。

出力（既定 `csrc/jdict_vectors.bin`）:

    magic "K2V1" u32 / n_cases u32 / blob_bytes u32
    <辞書 blob（K-1 の形式。matrix セクション込み）>
    ケース × n:
        text_len u32 / text(UTF-8)
        n_tokens u32
        token × n: { begin u32, end u32, entry_index u32 }
                    （begin/end は **鍵バイト**位置。UTF-8 バイトではない）

`entry_index` は blob 内の通し番号。C 側はこれと突き合わせる。

⚠️ **参照は MeCab そのもの**（`pyopenjtalk.run_mecab_detailed`）。
   未知語を含む文は除外する（K-3 で扱う）。
⚠️ 行列は **compact しない**（生の matrix.bin をそのまま載せる）。
   context id を詰め替えると、そこがバグの温床になる。サイズは K-5 で詰める。
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
from saanotts_jp.jdict import (CharProperty, ConnMatrix, DictBlob,  # noqa: E402
                                 Entry, UnkDict)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix-int8", choices=["sym", "affine"], default=None,
                    help="接続行列を 1 B に丸める（K-5 の精度影響を測る）。\n                          sym=行ごと対称 int8 / affine=行ごとアフィン uint8")
    ap.add_argument("--matrix-mode", default=None,
                    help="k9_fit_8mb.make_matrix の方式をそのまま使う"
                         "（int16 / affine / cluster:K[:u8] / lowrank:R）。"
                         "⚠️ **--matrix-int8 とは別物**: あちらは float スケールの旧形、"
                         "こちらは C リーダが再現できる整数式（M-99）。"
                         "⚠️ **定義を 2 つ持たないため make_matrix を import する**")
    ap.add_argument("--entries", type=int, default=120_000,
                    help="ベクタ用は小さめで良い（C の正しさを見るのが目的）")
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--out", default=str(HERE.parent.parent / "csrc/jdict_vectors.bin"))
    a = ap.parse_args()

    import k0_freeze_dict
    import k0_verify_dict
    if k0_verify_dict.main() != 0:
        print("NG! 辞書が D-042 の凍結物と違う")
        return 1
    dic = k0_freeze_dict.resolve_dict_dir()

    raw = load_entries(dic)
    bysurf = defaultdict(list)
    for r in raw:
        bysurf[r[0]].append(r)

    import pyopenjtalk
    freq: Counter[str] = Counter()
    with open(TRAIN, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                for ft in pyopenjtalk.run_mecab_detailed(p[2])[0]:
                    s = ft.split(",", 1)[0]
                    if s in bysurf:
                        freq[s] += 1
    ranked = [s for s, _ in freq.most_common()]
    seen = set(ranked)
    ranked += sorted((s for s in bysurf if s not in seen),
                     key=lambda s: (min(e[3] for e in bysurf[s]), len(s)))

    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= a.entries:
            break
    entries = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
               for r in sub]
    matrix = ConnMatrix.from_matrix_bin(
        (pathlib.Path(dic) / "matrix.bin").read_bytes())
    if a.matrix_mode:
        if a.matrix_int8:
            print("NG! --matrix-int8 と --matrix-mode は同時に使えない")
            return 1
        import numpy as np
        import k9_fit_8mb
        matrix, msize = k9_fit_8mb.make_matrix(a.matrix_mode)
        M0 = np.frombuffer(
            (pathlib.Path(dic) / "matrix.bin").read_bytes()[4:],
            dtype="<i2").astype(np.int64)
        M2 = np.frombuffer(matrix.data, dtype="<i2").astype(np.int64)
        nd = int((M2 != M0).sum())
        print(f"⚠️ 行列を {a.matrix_mode} にした: {nd:,d} / {M0.size:,d} 要素が変化"
              f"（{100*nd/M0.size:.2f}%）/ 最大誤差 {int(np.abs(M2-M0).max())}"
              f" / 実装時の行列サイズ {msize:,d} B")
        print("   ⚠️ **値は int16 のまま入れている**。ここで測るのは精度への影響だけ")
    elif a.matrix_int8:
        # K-5: 行ごとスケールの int8 に丸めた「値」を、**int16 のまま**入れる。
        # ⚠️ ここで測るのは**精度への影響だけ**。サイズの削減は別の話
        #    （形式を int8 にして初めて縮む）。混ぜて報告しないこと。
        import numpy as np
        M = np.frombuffer(matrix.data, dtype="<i2").reshape(
            matrix.rsize, matrix.lsize).astype(np.int32)
        if a.matrix_int8 == "sym":
            amax = np.abs(M).max(axis=1)
            scale = np.where(amax == 0, 1.0, amax / 127.0)
            q = np.rint(M / scale[:, None]).clip(-127, 127)
            M2 = np.rint(q * scale[:, None]).astype("<i2")
        else:                                    # 行ごとアフィン uint8
            lo = M.min(axis=1); hi = M.max(axis=1)
            sc = np.where(hi == lo, 1.0, (hi - lo) / 255.0)
            q = np.rint((M - lo[:, None]) / sc[:, None]).clip(0, 255)
            M2 = np.rint(q * sc[:, None] + lo[:, None]).astype("<i2")
        n_diff = int((M2 != M).sum())
        print(f"⚠️ 行列を行ごと int8 に丸めた: {n_diff:,d} / {M.size:,d} 要素が変化"
              f"（{100*n_diff/M.size:.2f}%）/ 最大誤差 "
              f"{int(np.abs(M2.astype(np.int32)-M).max())}")
        matrix = ConnMatrix(matrix.lsize, matrix.rsize, M2.tobytes())
    char_prop = CharProperty.from_char_bin((pathlib.Path(dic) / "char.bin").read_bytes())
    unkd = UnkDict.from_unk_dic((pathlib.Path(dic) / "unk.dic").read_bytes())
    blob = DictBlob.build(entries, matrix=matrix, char_prop=char_prop, unk=unkd)
    body = blob.to_bytes()
    print(f"blob {len(body):,d} B / {len(entries):,d} entries / "
          f"matrix {matrix.lsize}x{matrix.rsize}")

    # blob 内の通し番号を引く索引
    idx_of: dict[tuple, list[int]] = defaultdict(list)
    for i, e in enumerate(blob.all_entries()):
        idx_of[(e.surface, e.lc, e.rc, e.wcost, e.pron)].append(i)

    texts = []
    with open(HELDOUT, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                texts.append(p[2])
    texts = [texts[int(i * len(texts) / a.cases)] for i in range(a.cases)]

    cases = []
    skipped_unk = skipped_oov = 0
    for t in texts:
        feats, morphs = pyopenjtalk.run_mecab_detailed(t)
        if not morphs:
            skipped_unk += 1
            continue
        # ⚠️ **元テキストではなく「MeCab が実際に見た列」を使う。**
        #    OpenJTalk は MeCab の前に半角→全角正規化を掛けるので
        #    （`?`→`？` / `T`→`Ｔ` / `~`→U+301C / `-`→U+2212、94 文字の 1:1）、
        #    元テキストに surface がそのまま現れないことがある。
        #    **正規化は K-2 の対象外**（端末側の前処理として別に扱う）。
        norm = "".join(m["surface"] for m in morphs)
        toks = []
        ok = True
        pos = 0
        for m in morphs:
            f = m["features"]
            if m["is_unknown"] or len(f) < 12:
                # K-3: 未知語。unk.dic の何番かで表す（最上位ビットを立てる）
                ui = [i for i, e in enumerate(unkd.entries)
                      if e.lc == m["left_id"] and e.rc == m["right_id"]
                      and e.wcost == m["word_cost"]]
                if not ui:
                    ok = False
                    break
                cand = [0x80000000 | ui[0]]
            else:
                key = (m["surface"], m["left_id"], m["right_id"], m["word_cost"], f[9])
                cand = idx_of.get(key)
                if not cand:
                    ok = False       # 枝刈りで落ちた語
                    break
            # ⚠️ 位置は **鍵バイト**で表す（UTF-8 バイトではない）。
            #    C 側は鍵バイト列の上で解析するので、座標を揃えないと
            #    「解析は完全に正しいのに全件不一致」に見える（実際に踏んだ）。
            nb = len(blob.keys.encode(norm[:pos]))
            ne = len(blob.keys.encode(norm[:pos + len(m["surface"])]))
            toks.append((nb, ne, cand[0]))
            pos += len(m["surface"])
        if not ok:
            skipped_oov += 1
            continue
        cases.append((norm, toks))

    n_unk_cases = sum(1 for _t, tk in cases if any(i & 0x80000000 for _b, _e, i in tk))
    print(f"ケース {len(cases)}（うち未知語を含む {n_unk_cases}）"
          f" / 除外: 解析不能 {skipped_unk} / 枝刈りで欠け {skipped_oov}")
    if not cases:
        print("NG! ケースが 0 件")
        return 1

    out = bytearray(struct.pack("<4sII", b"K2V1", len(cases), len(body)))
    out += body
    for t, toks in cases:
        tb = t.encode("utf-8")
        out += struct.pack("<I", len(tb)) + tb
        out += struct.pack("<I", len(toks))
        for b, e, i in toks:
            out += struct.pack("<III", b, e, i)
    pathlib.Path(a.out).write_bytes(bytes(out))
    print(f"書き出した → {a.out} ({len(out):,d} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
