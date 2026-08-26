#!/usr/bin/env python3
"""
B-0: 枝刈り OpenJTalk (NAIST-JDIC) 辞書の実バイナリサイズを測る。

  エントリ数 {10k, 30k, 60k, 100k, 全件} x feature 削減 {L0, L1, L2}
  さらに matrix.bin の context-id 圧縮 on/off を両方測る。

すべて「実際に mecab-dict-index でビルドしてバイト数を stat する」= measured。
見積もりは一切使わない。

実行 (cwd は b0/size にすること):
  export PP=/Users/s19447/Documents/piper-plus
  $PP/.venv/bin/python measure_size.py build          # 30 セル (5 x 3 x 圧縮2) -> size/sizes.json
  $PP/.venv/bin/python measure_size.py rank-abl       # 枝刈り基準 freq/wcost/hybrid -> size/rankabl.json
  $PP/.venv/bin/python measure_size.py extra N...     # 任意のエントリ数 (L2/圧縮あり) -> size/extra.json

前提となる入力 (どちらも b0/size/ に置く):
  entries_pyoj.tsv   b0/dump_entries.py で pyopenjtalk 同梱 sys.dic から復元した全 789,024 エントリ
  train_tokfreq.tsv  b0/size/tokfreq.py で corpus_train.tsv を解析した表層形頻度

同じディレクトリの周辺スクリプト:
  tokfreq.py    コーパス -> 表層形頻度 (フル辞書で run_mecab_detailed)
  dump_vocab.py 各水準の語彙 (表層形リスト) を vocab_freq.json に出す
  eval_dict.py  1 辞書ぶんの (P3,A1,A2,A3) 列を出す。辞書ごとに別プロセス必須
  score.py      ref/hyp を突き合わせて音素列・アクセントの一致率と PER を出す
  coverage.py   語彙リストに対する token / sentence カバー率
  make_report.py 全部を集めて /Users/s19447/Desktop/saanoTTS-jp/reports/b0_size.json を書く
"""
import os, sys, json, shutil, struct, subprocess, collections, time, csv

SP   = "/private/tmp/claude-1518468357/-Users-s19447-Desktop-saanoTTS-jp/3e5773b4-3fdf-4e16-b3dd-1eecad344064/scratchpad/b0"
HERE = os.path.join(SP, "size")
PP   = "/Users/s19447/Documents/piper-plus"
# piper-plus の python G2P が実際に読む辞書 (pyopenjtalk 同梱)。
# build/share/open_jtalk/dic/sys.dic とは 101 token 違う別リビジョン。
SRC  = os.path.join(PP, ".venv/lib/python3.13/site-packages/pyopenjtalk/dictionary")
DICT_INDEX = os.path.join(SP, "mecab-dict-index")
ENTRIES    = os.path.join(HERE, "entries_pyoj.tsv")
TOKFREQ    = os.path.join(HERE, "train_tokfreq.tsv")

DEFS = ("left-id.def", "right-id.def", "pos-id.def", "rewrite.def", "char.bin")
DICRC = ("cost-factor = 800\n"
         "bos-feature = BOS/EOS,*,*,*,*,*,*,*,*,*,*\n"
         "eval-size = 8\nunk-eval-size = 4\nconfig-charset = UTF-8\n")

# ---------------------------------------------------------------- feature 削減
# feature は 11 フィールド固定:
#  0 pos 1 pos_group1 2 pos_group2 3 pos_group3 4 ctype 5 cform
#  6 orig 7 read 8 pron 9 acc 10 chain_rule
#
# ⚠️ NJDNode_load (njd/njd_node.c:438-500) は ',' で「位置」でパースする。
#    列を削除すると以降が全部ズレる。よって削減は必ず値を "*" に潰す形で行い、
#    11 列を保つ。(reports/b0_feature_fields.json の L1z が
#     "pos_group3 列削除" と書いているのは sys.dic のサイズ計算専用で、
#     実際にビルドしてはいけない)
CT_KEEP = {"特殊・マス", "特殊・ナイ", "サ変・スル"}
CF_KEEP = {"連用形", "連用タ接続", "連用ゴザイ接続", "連用テ接続",
           "連用デ接続", "連用ニ接続", "未然形"}

def _orig_keep_set():
    sys.path.insert(0, os.path.join(PP, "src/python"))
    import pyopenjtalk
    return (set(["れる", "られる", "せる", "させる", "すぎる", "ちゃう", "なる", "する", "何"])
            | set(pyopenjtalk.MULTI_READ_KANJI_LIST))

def _class3_reads():
    sys.path.insert(0, "/Users/s19447/Desktop/saanoTTS-jp/scripts/b0")
    from class3 import CLASS3_READS
    return set(CLASS3_READS)

ORIG_KEEP = None
C3 = None
ODORI = "々〃ゝヽゞヾ"

def L0(f):
    return f

def L1(f):
    """ctype 3値 / cform 7値 / pos_group2 は助数詞のみ / pos_group3 は '*' / read は class3 の 49値のみ。
    reports/b0_feature_fields.json で 12,211 行 bit 完全一致が実測されている keep-set
    (ただし pos_group3 は列削除ではなく '*' 化)。"""
    g = list(f)
    if g[2] != "助数詞": g[2] = "*"
    g[3] = "*"
    if g[4] not in CT_KEEP: g[4] = "*"
    if g[5] not in CF_KEEP: g[5] = "*"
    if g[7] not in C3: g[7] = "*"
    return g

def L2(f):
    """L1 + orig の縮約。複合エントリ (acc に ':') は orig が表層分割キーなので必須。
    機能語 + MULTI_READ_KANJI_LIST(69字) + 踊り字 + 単漢字 も残す。"""
    g = L1(f)
    if ":" in f[9]:
        return g
    o = f[6]
    if o in ORIG_KEEP: return g
    if any(c in o for c in ODORI): return g
    if len(o) == 1 and 0x4E00 <= ord(o) <= 0x9FFF: return g
    g[6] = "*"
    return g

LEVELS = {"L0": L0, "L1": L1, "L2": L2}

# ---------------------------------------------------------------- 読み込み
def load_entries():
    bysurf = collections.defaultdict(list)
    n = 0
    with open(ENTRIES, encoding="utf-8") as fh:
        for ln in fh:
            s, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t")
            bysurf[s].append((int(lc), int(rc), int(cost), feat.split(",")))
            n += 1
    return bysurf, n

def load_freq():
    freq = collections.Counter()
    with open(TOKFREQ, encoding="utf-8") as fh:
        for ln in fh:
            c, s = ln.rstrip("\n").split("\t")
            freq[s] = int(c)
    return freq

# ---------------------------------------------------------------- 枝刈り基準
def rank_surfaces(bysurf, freq, mode="freq"):
    """枝刈りの基準。返り値は表層形の並び (前ほど残す)。

    mode="freq"  : corpus_train.tsv の出現頻度 降順
                   → コーパスに出ない語は min(wcost) 昇順で補充  ← 本測定の既定
    mode="wcost" : min(wcost) 昇順のみ (コーパスを一切使わない)
    mode="hybrid": コーパス頻度 f と wcost を score = log2(1+f)*1000 - wcost で合成
    """
    if mode == "freq":
        in_corpus = [s for s, _ in freq.most_common() if s in bysurf]
        rest = sorted((s for s in bysurf if s not in freq),
                      key=lambda s: (min(e[2] for e in bysurf[s]), len(s)))
        return in_corpus + rest
    if mode == "wcost":
        return sorted(bysurf, key=lambda s: (min(e[2] for e in bysurf[s]), len(s)))
    if mode == "hybrid":
        import math
        return sorted(bysurf,
                      key=lambda s: -(math.log2(1 + freq.get(s, 0)) * 1000
                                      - min(e[2] for e in bysurf[s])))
    raise ValueError(mode)

def take_entries(ranked, bysurf, n_entries):
    """エントリ数 n_entries に達するまで表層形を丸ごと採る。
    同じ表層形のエントリは曖昧性解消のため全部残す (途中で切らない)。"""
    out, tot = [], 0
    for s in ranked:
        k = len(bysurf[s])
        if tot >= n_entries:
            break
        out.append(s); tot += k
    return out, tot

# ---------------------------------------------------------------- matrix 圧縮
def compact_matrix(rows, dic_dir):
    """使われている lcAttr/rcAttr だけの部分行列に切り出して matrix.bin を書き直し、
    CSV 行と unk.dic の token も同じ写像で書き換える。
    connector.h:39-46 が matrix[rcAttr + lsize*lcAttr] を引く。
    BOS/EOS は zero-init Node (tokenizer.cpp:80-87) なので旧 id 0 -> 新 id 0 を保証する。
    出力が不変であることは build_dict.md §5.2 で実測済み。"""
    import numpy as np
    mb = open(os.path.join(SRC, "matrix.bin"), "rb").read()
    lsize, rsize = struct.unpack("<HH", mb[:4])
    M = np.frombuffer(mb[4:], dtype="<i2").reshape(rsize, lsize)

    ub = bytearray(open(os.path.join(SRC, "unk.dic"), "rb").read())
    h = struct.unpack("<10I", bytes(ub[:40])); ulex, uds = h[3], h[6]
    utok = 72 + uds
    unk_l, unk_r = set(), set()
    for i in range(ulex):
        lc, rc = struct.unpack("<HH", bytes(ub[utok + i * 16: utok + i * 16 + 4]))
        unk_l.add(lc); unk_r.add(rc)

    used_l = {0} | unk_l | {r[1] for r in rows}
    used_r = {0} | unk_r | {r[2] for r in rows}
    L, R = sorted(used_l), sorted(used_r)
    lmap = {o: n for n, o in enumerate(L)}
    rmap = {o: n for n, o in enumerate(R)}
    assert lmap[0] == 0 and rmap[0] == 0

    NM = M[np.ix_(L, R)].astype("<i2")
    out = struct.pack("<HH", len(R), len(L)) + NM.tobytes()
    open(os.path.join(dic_dir, "matrix.bin"), "wb").write(out)

    for i in range(ulex):
        o = utok + i * 16
        lc, rc = struct.unpack("<HH", bytes(ub[o:o + 4]))
        ub[o:o + 4] = struct.pack("<HH", lmap[lc], rmap[rc])
    open(os.path.join(dic_dir, "unk.dic"), "wb").write(bytes(ub))
    return lmap, rmap, len(L), len(R), len(out), len(mb)

# ---------------------------------------------------------------- ビルド 1 セル
def sysdic_sections(path):
    with open(path, "rb") as fh:
        hdr = fh.read(40)
    (_, ver, _, lexsize, lsize, rsize, dsize, tsize, fsize, _) = struct.unpack("<10I", hdr)
    return dict(lexsize=lexsize, lsize=lsize, rsize=rsize,
                darts_bytes=dsize, token_bytes=tsize, feature_bytes=fsize)

def build_cell(bysurf, ranked, n_entries, level, compact, outroot, keep_dir=True):
    t0 = time.time()
    tag = f"{'full' if n_entries is None else n_entries}_{level}_{'C' if compact else 'N'}"
    d = os.path.join(outroot, "in_" + tag)
    o = os.path.join(outroot, "out_" + tag)
    for p in (d, o):
        shutil.rmtree(p, ignore_errors=True); os.makedirs(p)
    for fn in DEFS:
        shutil.copyfile(os.path.join(SRC, fn), os.path.join(d, fn))
    open(os.path.join(d, "dicrc"), "w").write(DICRC)

    if n_entries is None:
        surfs, tot = list(bysurf.keys()), sum(len(v) for v in bysurf.values())
    else:
        surfs, tot = take_entries(ranked, bysurf, n_entries)

    fn = LEVELS[level]
    rows = []
    for s in surfs:
        for lc, rc, cost, feat in bysurf[s]:
            rows.append([s, lc, rc, cost, ",".join(fn(feat))])
    assert len(rows) == tot, (len(rows), tot)

    if compact:
        lmap, rmap, nl, nr, mbytes, mbytes0 = compact_matrix(rows, d)
        for r in rows:
            r[1] = lmap[r[1]]; r[2] = rmap[r[2]]
    else:
        shutil.copyfile(os.path.join(SRC, "matrix.bin"), os.path.join(d, "matrix.bin"))
        shutil.copyfile(os.path.join(SRC, "unk.dic"),    os.path.join(d, "unk.dic"))
        nl = len({r[1] for r in rows} | {0}); nr = len({r[2] for r in rows} | {0})
        mbytes = mbytes0 = os.path.getsize(os.path.join(SRC, "matrix.bin"))

    csvp = os.path.join(d, "dic.csv")
    with open(csvp, "w", encoding="utf-8", newline="\n") as fh:
        for s, lc, rc, cost, feat in rows:
            fh.write(f"{s},{lc},{rc},{cost},{feat}\n")
    csv_bytes = os.path.getsize(csvp)

    # ⚠️ open_jtalk 版 mecab は CHECK_DIE の exit() が潰されている
    #    (dictionary_compiler.cpp:100-107)。stderr 行数 0 を必ず確認する。
    pr = subprocess.run([DICT_INDEX, "-d", d, "-o", o, "-f", "utf-8", "-t", "utf-8", "-s", "-q"],
                        capture_output=True, text=True)
    err = [l for l in pr.stderr.splitlines() if l.strip()]
    assert pr.returncode == 0 and not err, (tag, pr.returncode, err[:5])

    for fn2 in ("matrix.bin", "char.bin", "unk.dic"):
        shutil.copyfile(os.path.join(d, fn2), os.path.join(o, fn2))
    sysdic = os.path.join(o, "sys.dic")
    sec = sysdic_sections(sysdic)
    sz = {k: os.path.getsize(os.path.join(o, k))
          for k in ("sys.dic", "matrix.bin", "char.bin", "unk.dic")}
    os.remove(csvp)
    if not keep_dir:
        shutil.rmtree(o, ignore_errors=True)
    shutil.rmtree(d, ignore_errors=True)

    return dict(tag=tag, level=level, compact=compact,
                target_entries=n_entries, surfaces=len(surfs), entries=tot,
                csv_bytes=csv_bytes,
                distinct_lcAttr=nl, distinct_rcAttr=nr,
                sys_dic_bytes=sz["sys.dic"],
                darts_bytes=sec["darts_bytes"], token_bytes=sec["token_bytes"],
                feature_bytes=sec["feature_bytes"], lexsize=sec["lexsize"],
                matrix_bin_bytes=sz["matrix.bin"], char_bin_bytes=sz["char.bin"],
                unk_dic_bytes=sz["unk.dic"],
                total_runtime_bytes=sum(sz.values()),
                dicdir=o if keep_dir else None,
                build_seconds=round(time.time() - t0, 1))

# ---------------------------------------------------------------- main
def main_build():
    global ORIG_KEEP, C3
    ORIG_KEEP = _orig_keep_set(); C3 = _class3_reads()
    bysurf, n = load_entries()
    freq = load_freq()
    ranked = rank_surfaces(bysurf, freq, "freq")
    print(f"entries={n:,} surfaces={len(bysurf):,} corpus-surfaces-in-dict="
          f"{sum(1 for s in freq if s in bysurf):,}/{len(freq):,}", flush=True)
    outroot = os.path.join(HERE, "builds"); os.makedirs(outroot, exist_ok=True)
    res = []
    for N in (10000, 30000, 60000, 100000, None):
        for lv in ("L0", "L1", "L2"):
            for compact in (True, False):
                r = build_cell(bysurf, ranked, N, lv, compact, outroot,
                               keep_dir=compact)
                res.append(r)
                print(f"{r['tag']:<16} surf={r['surfaces']:>7,} ent={r['entries']:>7,} "
                      f"sys={r['sys_dic_bytes']:>11,} mat={r['matrix_bin_bytes']:>9,} "
                      f"TOTAL={r['total_runtime_bytes']:>11,} ({r['total_runtime_bytes']/2**20:6.2f} MiB) "
                      f"{r['build_seconds']}s", flush=True)
    json.dump(res, open(os.path.join(HERE, "sizes.json"), "w"), indent=1, ensure_ascii=False)
    # 枝刈り基準ごとの語彙だけ書き出す (カバー率評価用)
    for mode in ("freq", "wcost", "hybrid"):
        rk = rank_surfaces(bysurf, freq, mode)
        vocab = {}
        for N in (10000, 30000, 60000, 100000):
            s, t = take_entries(rk, bysurf, N)
            vocab[str(N)] = s
        json.dump(vocab, open(os.path.join(HERE, f"vocab_{mode}.json"), "w"), ensure_ascii=False)
    print("wrote", os.path.join(HERE, "sizes.json"))

def main_rank_abl():
    """枝刈り基準 freq / wcost / hybrid を 30k エントリで実ビルド比較。"""
    global ORIG_KEEP, C3
    ORIG_KEEP = _orig_keep_set(); C3 = _class3_reads()
    bysurf, n = load_entries(); freq = load_freq()
    outroot = os.path.join(HERE, "rankabl"); os.makedirs(outroot, exist_ok=True)
    res = []
    for mode in ("freq", "wcost", "hybrid"):
        rk = rank_surfaces(bysurf, freq, mode)
        r = build_cell(bysurf, rk, 30000, "L1", True, outroot, keep_dir=True)
        r["rank_mode"] = mode
        os.rename(r["dicdir"], r["dicdir"] + "_" + mode)
        r["dicdir"] += "_" + mode
        res.append(r); print(mode, r["entries"], r["total_runtime_bytes"], flush=True)
    json.dump(res, open(os.path.join(HERE, "rankabl.json"), "w"), indent=1, ensure_ascii=False)


def main_extra():
    """曲線の外挿検証と ESP32 予算ちょうどの点を実ビルドで押さえる。"""
    global ORIG_KEEP, C3
    ORIG_KEEP = _orig_keep_set(); C3 = _class3_reads()
    bysurf, n = load_entries(); freq = load_freq()
    ranked = rank_surfaces(bysurf, freq, "freq")
    outroot = os.path.join(HERE, "extra"); os.makedirs(outroot, exist_ok=True)
    res = []
    for N in [int(x) for x in sys.argv[2:]]:
        r = build_cell(bysurf, ranked, N, "L2", True, outroot, keep_dir=True)
        res.append(r)
        print(f"{r['tag']:<16} surf={r['surfaces']:>7,} ent={r['entries']:>7,} "
              f"sys={r['sys_dic_bytes']:>11,} TOTAL={r['total_runtime_bytes']:>11,} "
              f"({r['total_runtime_bytes']/2**20:6.2f} MiB)", flush=True)
    p = os.path.join(HERE, "extra.json")
    old = json.load(open(p)) if os.path.exists(p) else []
    json.dump(old + res, open(p, "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__":
    {"build": main_build, "rank-abl": main_rank_abl, "extra": main_extra}[sys.argv[1]]()
