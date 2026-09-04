"""M-97: 8 MB 枠の動作点を振る（エントリ数 × 接続行列の方式）。

    uv run python scripts/k1/k9_fit_8mb.py --entries N [--matrix MODE] \
        [--keep-single-char] --out <vec>
    ./csrc/label_ids_test <vec>          # 音素の編集距離が出る

既存ベクタ `csrc/kanji_e2e_vectors.bin` の **blob だけ差し替える**。
ホスト側の基準（feats/full/dev/ids）は枝刈り辞書に依存しないので再計算不要。
無ければ先に作る:

    uv run python scripts/k1/k6_gen_vectors.py --cases 300 --entries 438750 \
        --out csrc/kanji_e2e_vectors.bin

⚠️ **陽性対照を先に通すこと。** `--entries 438750 --matrix int16` で
   blob 13,702,320 B / 音素 0.32% / 254 文（M-77）が再現しなければ、
   差し替え経路が壊れている。`--entries 370863` なら 0.55% / 245 文。

⚠️ **行列の変種は値だけ丸めて int16 のまま入れる**（k6_gen_vectors.py の
   --matrix-int8 と同じ流儀）。**精度への影響だけを測る**もので、
   サイズは形式から算術する（REALSIZE / FULLSIZE 行）。**C リーダはまだ無い。**

MODE:
   int16                  現行
   affine                 行ごとアフィン uint8
   cluster:K[:u8]         行・列を K クラスタに畳む（+ 代表行列を uint8 に）
   lowrank:R              低ランク近似（⚠️ 実行時は辺ごとに R 長の内積が要る）
"""
import argparse, json, pathlib, struct, sys, time
from collections import Counter, defaultdict
import numpy as np

ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, ROOT + '/scripts/k1'); sys.path.insert(0, ROOT + '/src')
from dump_entries_lib import load_entries
from k1_paths import TRAIN, WORK
import k1_paths
from saanotts_jp.jdict import CharProperty, ConnMatrix, DictBlob, Entry, UnkDict

SP = pathlib.Path(__file__).resolve().parent
BASE = pathlib.Path(ROOT + '/csrc/kanji_e2e_vectors.bin')
RANK_CACHE = pathlib.Path(WORK) / "rank_cache.json"
D = pathlib.Path(str(k1_paths.DICT_VENV))


def load_ranking():
    raw = load_entries(str(D))
    bysurf = defaultdict(list)
    for r in raw:
        bysurf[r[0]].append(r)
    if RANK_CACHE.exists():
        ranked = json.loads(RANK_CACHE.read_text())
        print(f"ランキングをキャッシュから読んだ ({len(ranked):,d} 見出し語)")
        return bysurf, ranked
    import pyopenjtalk
    freq = Counter()
    t0 = time.time()
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
    RANK_CACHE.write_text(json.dumps(ranked))
    print(f"ランキング {len(ranked):,d} 見出し語 ({time.time()-t0:.0f} 秒) → キャッシュ")
    return bysurf, ranked


def affine_u8_int(M):
    """行ごとアフィン uint8。**整数演算だけ**で量子化と逆量子化を行う。

    ⚠️ **C リーダが再現できる形でなければ、ここで測った精度は移らない。**
    float でスケールを持つと、ホスト（float64）と C（float / 別の丸め）で
    値が食い違い、Viterbi の判断が変わりうる。だから **int32 で閉じる**:

        span = hi - lo                                  （行ごと。実測 1,188..17,342）
        q    = ((v - lo)*255*2 + span) // (span*2)      量子化（round-half-up）
        v'   = lo + (q*span*2 + 255) // (255*2)         逆量子化（round-half-up）

    どちらも中間値は最大 255*17,342*2 = **8,844,420** で int32 に収まる。

    ⚠️ **かつてここに「0..18656 / 9,514,560」と書いていたのは誤り**（C-060）。
       18,656 は**全体のレンジ**（4839 −(−13817)）であって**行ごとの span ではない**。
       再現: `M.max(axis=1) - M.min(axis=1)` の max は **17,342**、min は 1,188、
       **span==0 の行は 0 / 1377**（実データではゼロ除算の枝を一度も踏まない）。
    C 側は `lo` を int16、`span` を uint16 で持てばよく、浮動小数は 1 つも要らない。

    ⚠️ **`span == 0` の行（全要素が同じ）は q=0 / v'=lo。** ゼロ除算を避ける。
    """
    M = np.asarray(M, dtype=np.int64)
    lo = M.min(axis=1); hi = M.max(axis=1)
    span = (hi - lo)
    sp = np.where(span == 0, 1, span)[:, None]
    q = ((M - lo[:, None]) * 510 + sp) // (2 * sp)
    q = np.clip(q, 0, 255)
    deq = lo[:, None] + (q * span[:, None] * 2 + 255) // 510
    deq = np.where(span[:, None] == 0, lo[:, None], deq)
    return deq.astype(np.int64), int(np.abs(deq - M).max())


def make_matrix(mode):
    """(ConnMatrix, 実装したときのバイト数) を返す。"""
    raw = (D / "matrix.bin").read_bytes()
    L = int.from_bytes(raw[0:2], "little"); R = int.from_bytes(raw[2:4], "little")
    M = np.frombuffer(raw[4:], dtype="<i2").reshape(R, L).astype(np.float64)
    if mode == "int16":
        return ConnMatrix(L, R, raw[4:]), len(raw)
    if mode in ("affine", "affine_f64"):
        if mode == "affine":
            # **実装する形**（整数で閉じる）
            M2 = affine_u8_int(M)[0].astype("<i2")
            size = R * L + R * 2 + R * 2 + 8      # uint8 本体 + 行ごと lo(i16) + span(u16)
        else:
            # ⚠️ **比較用の旧形**（float スケール）。C では再現できない
            lo = M.min(axis=1); hi = M.max(axis=1)
            sc = np.where(hi == lo, 1.0, (hi - lo) / 255.0)
            q = np.rint((M - lo[:, None]) / sc[:, None]).clip(0, 255)
            M2 = np.rint(q * sc[:, None] + lo[:, None]).astype("<i2")
            size = R * L + R * 2 + R * 4 + 8
        return ConnMatrix(L, R, M2.tobytes()), size
    if mode.startswith("cluster"):
        parts = mode.split(":")
        k = int(parts[1])
        q8 = len(parts) > 2 and parts[2] == "u8"
        from sklearn.cluster import KMeans
        kmr = KMeans(n_clusters=k, n_init=3, random_state=0).fit(M)
        Mr = kmr.cluster_centers_[kmr.labels_]
        kmc = KMeans(n_clusters=k, n_init=3, random_state=0).fit(Mr.T)
        # ⚠️ kmc.cluster_centers_.T は (1377, k_col)。**行クラスタごとの代表行**を
        #    取り出さないと (k_row, k_col) にならない（一度ここを間違えた）。
        CC = kmc.cluster_centers_.T
        rep = np.zeros(k, dtype=int)
        for i, aa in enumerate(kmr.labels_):
            rep[aa] = i
        # ⚠️ **代表値を先に整数へ丸める。** k-means の重心は float なので、
        #    ここで丸めておかないとホストと C で別の値になる。
        C = np.rint(CC[rep]).astype(np.int64)            # (k_row, k_col) の代表値
        if q8:
            # ⚠️ **代表行列を行ごとアフィン uint8 に落とす**（クラスタと直交する削減）。
            #    量子化は affine と同じ整数式（C リーダが再現できる形）。
            C = affine_u8_int(C)[0]
            size = k * k + k * 2 + k * 2 + L * 2 + R * 2 + 8
        else:
            size = k * k * 2 + L * 2 + R * 2 + 8
        M2 = np.rint(C[kmr.labels_][:, kmc.labels_]).astype("<i2")
        return ConnMatrix(L, R, M2.tobytes()), size
    if mode.startswith("lowrank"):
        r = int(mode.split(":")[1])
        U, S, Vt = np.linalg.svd(M, full_matrices=False)
        M2 = np.rint((U[:, :r] * S[:r]) @ Vt[:r]).clip(-32768, 32767).astype("<i2")
        size = r * (L + R) * 2 + 8
        return ConnMatrix(L, R, M2.tobytes()), size
    raise SystemExit(f"unknown matrix mode {mode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, required=True)
    ap.add_argument("--matrix", default="int16")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-single-char", action="store_true")
    a = ap.parse_args()

    bysurf, ranked = load_ranking()
    if a.keep_single_char:
        # M-73: 落ちる語の 97.9% は単漢字で文字を覆える。**別枠で確保する**
        single = [s for s in bysurf if len(s) == 1]
        rest = [s for s in ranked if len(s) != 1]
        print(f"1 文字の見出し語を確保: {len(single):,d} 語 / "
              f"{sum(len(bysurf[s]) for s in single):,d} entries")
        ranked = single + rest
    sub, n = [], 0
    for s in ranked:
        sub.extend(bysurf[s]); n += len(bysurf[s])
        if n >= a.entries:
            break
    entries = [Entry(r[0], r[1], r[2], r[3], r[4], 0, r[5], r[6], r[7], r[8], r[9])
               for r in sub]
    matrix, msize = make_matrix(a.matrix)
    blob = DictBlob.build(
        entries, matrix=matrix,
        char_prop=CharProperty.from_char_bin((D / "char.bin").read_bytes()),
        unk=UnkDict.from_unk_dic((D / "unk.dic").read_bytes())).to_bytes()

    # 実装したときの実サイズ = blob − int16 行列 + 変種の行列
    real = len(blob) - (3_792_262) + msize
    print(f"entries={len(entries):,d} 見出し語={len(set(e.surface for e in entries)):,d}  "
          f"matrix={a.matrix} ({msize:,d} B)")
    print(f"  測定用 blob {len(blob):,d} B / **実装時のイメージ {real:,d} B**")

    # --- 全部入りのサイズモデル（char レンジ表 + レコード dedup）--------------
    # ⚠️ **どちらも無損失**なので音素の誤りは上の測定値のまま変わらない。
    secs = DictBlob.sections(blob)
    ro, rl = secs["records"]
    nrec = rl // 9
    d = len(set(blob[ro + i * 9: ro + (i + 1) * 9] for i in range(nrec)))
    rec_new = int(nrec * 2.25) + d * 9            # 18 bit ID + 表
    char_new = 374                                 # 106 run のレンジ表（実測）
    co, cl = secs["char"]
    full = real - rl + rec_new - cl + char_new
    print(f"  distinct レコード {d:,d} / {nrec:,d} → records {rl:,d} → {rec_new:,d}")
    print(f"FULLSIZE {full}")

    b = BASE.read_bytes()
    p = 8
    ndan, = struct.unpack_from('<I', b, p); p += 4
    for _ in range(ndan):
        l, = struct.unpack_from('<H', b, p); p += 2 + l + 1
    old_len, = struct.unpack_from('<I', b, p)
    out = b[:p] + struct.pack('<I', len(blob)) + blob + b[p + 4 + old_len:]
    pathlib.Path(a.out).write_bytes(out)
    print(f"  → {a.out}")
    print(f"REALSIZE {real}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
