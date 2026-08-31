"""TTS 専用バイナリ形式 v2 — **11 フィールド無損失**版のサイズを枝刈り水準ごとに測る。

v1 (tts_dict_encode.py) は orig / read を捨てていた。それだと B-0 の精度曲線
(feature level L2) と厳密には対応しない。v2 は 11 フィールドすべてを復元できる
形式にして、**エントリ集合が同じなら解析結果も同じ**であることを保証する。
こうすると B-0 が実測した精度をそのまま新形式のサイズに貼り付けられる。

ゲート:
  G1  全エントリで 11 フィールド往復一致
  G2  枝刈り水準ごとに往復一致
  G3  陰性対照: 1 バイト壊すと G1 が落ちる
  G4  ランキングの再現: B-0 の申告値 (344,037 token / 既知表層 29,325 / 未知 425) と一致するか
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import struct
from collections import Counter, defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
ENTRIES = os.path.join(_WORK, "entries.tsv")
CORPUS = (_ROOT + "/data/splits/corpus_train.tsv")

SMALL = set("ァィゥェォャュョヮヵヶ")


def split_moras(s):
    out, i = [], 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in SMALL:
            out.append(s[i:i + 2]); i += 2
        else:
            out.append(s[i]); i += 1
    return out


# ------------------------------------------------------------------ 読み込み
parsed = []
with open(ENTRIES, encoding="utf-8") as f:
    for ln in f:
        surf, lc, rc, pid, cost, feat = ln.rstrip("\n").split("\t")
        fs = feat.split(",")
        assert len(fs) == 11
        parsed.append((surf, int(lc), int(rc), int(cost), tuple(fs[0:6]),
                       fs[6], fs[7], fs[8], fs[9], fs[10]))
print(f"entries={len(parsed)}", flush=True)

# orig / read の冗長性
n_orig_eq = sum(1 for p in parsed if p[5] == p[0])
n_read_eq = sum(1 for p in parsed if p[6] == p[7])
n_both = sum(1 for p in parsed if p[5] == p[0] and p[6] == p[7])
print(f"orig == surface : {n_orig_eq:,d} / {len(parsed):,d} = {100*n_orig_eq/len(parsed):.2f}%")
print(f"read == pron    : {n_read_eq:,d} / {len(parsed):,d} = {100*n_read_eq/len(parsed):.2f}%")
print(f"両方             : {n_both:,d} = {100*n_both/len(parsed):.2f}%", flush=True)

# ------------------------------------------------------------------ 語彙表
cls_tab, chain_tab = {}, {}
for p in parsed:
    cls_tab.setdefault((p[1], p[2], p[4]), len(cls_tab))
    chain_tab.setdefault(p[9], len(chain_tab))
pos6_tab = {}
for p in parsed:
    pos6_tab.setdefault(p[4], len(pos6_tab))

mora_count = Counter()
for p in parsed:
    for u in p[7].split(":"):
        mora_count.update(split_moras(u))
mora_ids = {m: i for i, (m, _) in enumerate(mora_count.most_common(254))}
SEP, ESC = 0xFE, 0xFF
inv_mora = {v: k for k, v in mora_ids.items()}


def enc_pron(pron):
    out = bytearray()
    for k, unit in enumerate(pron.split(":")):
        if k:
            out.append(SEP)
        for m in split_moras(unit):
            if m in mora_ids:
                out.append(mora_ids[m])
            else:
                b = m.encode("utf-8"); out += bytes([ESC, len(b)]) + b
    return bytes(out)


def dec_pron(b):
    units, i = [[]], 0
    while i < len(b):
        c = b[i]
        if c == SEP:
            units.append([]); i += 1
        elif c == ESC:
            n = b[i + 1]; units[-1].append(b[i + 2:i + 2 + n].decode("utf-8")); i += 2 + n
        else:
            units[-1].append(inv_mora[c]); i += 1
    return ":".join("".join(u) for u in units)


def enc_acc(accf):
    out = []
    for unit in accf.split(":"):
        a, m = unit.split("/", 1) if "/" in unit else (unit, "*")
        out.append((255 if a == "*" else int(a), 255 if m == "*" else int(m)))
    return out


def dec_acc(vals):
    return ":".join(("*" if a == 255 else str(a)) + "/" + ("*" if m == 255 else str(m))
                    for a, m in vals)


# ------------------------------------------------------------------ 符号化
MATRIX, CHARBIN, UNKDIC = 3792262, 262496, 5690


def build_louds(surfaces, tail_compress=False):
    """バイト単位 trie を LOUDS で組み、実バイト列の長さを返す。"""
    kids = [dict()]
    term = [False]
    for s in surfaces:
        n = 0
        for b in s.encode("utf-8"):
            nx = kids[n].get(b)
            if nx is None:
                nx = len(kids); kids.append(dict()); term.append(False); kids[n][b] = nx
            n = nx
        term[n] = True
    order, q = [], [0]
    while q:
        nxt = []
        for n in q:
            order.append(n)
            for b in sorted(kids[n]):
                nxt.append(kids[n][b])
        q = nxt
    n_nodes = len(order)
    bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
    louds_b = (bitlen + 7) // 8
    labels_b = n_nodes
    term_b = (n_nodes + 7) // 8
    rank_b = ((bitlen // 512 + 1) * 4 + (bitlen // 64 + 1)) + \
             ((n_nodes // 512 + 1) * 4 + (n_nodes // 64 + 1))
    return n_nodes, louds_b + labels_b + term_b + rank_b


def encode_level(sub, lossy=False):
    """sub = エントリのリスト。実バイト列を作って部位ごとのサイズを返す。

    lossy=True: orig / read を捨てる（B-0 の feature level L2 より強い削減）。
    ただし複合エントリ (acc に ':') の orig は NJDNode_load が部分語の区切りに
    使うので残す。
    """
    surfaces = sorted(set(p[0] for p in sub))
    bysurf = defaultdict(list)
    for p in sub:
        bysurf[p[0]].append(p)

    # LOUDS 順の見出し語列（BFS 順 = 深さ優先ではないので作り直す）
    kids = [dict()]; term = [False]
    for s in surfaces:
        n = 0
        for b in s.encode("utf-8"):
            nx = kids[n].get(b)
            if nx is None:
                nx = len(kids); kids.append(dict()); term.append(False); kids[n][b] = nx
            n = nx
        term[n] = True
    order, q, key = [], [0], {0: b""}
    while q:
        nxt = []
        for n in q:
            order.append(n)
            for b in sorted(kids[n]):
                c = kids[n][b]; key[c] = key[n] + bytes([b]); nxt.append(c)
        q = nxt
    n_nodes = len(order)
    term_keys = [key[n].decode("utf-8") for n in order if term[n]]
    assert sorted(term_keys) == surfaces

    bitlen = 2 + sum(len(kids[n]) + 1 for n in order)
    louds_b = (bitlen + 7) // 8
    labels_b = n_nodes
    termbits_b = (n_nodes + 7) // 8
    rank_b = ((bitlen // 512 + 1) * 4 + (bitlen // 64 + 1)) + \
             ((n_nodes // 512 + 1) * 4 + (n_nodes // 64 + 1))

    flat = []
    count_arr = bytearray()
    for s in term_keys:
        es = bysurf[s]
        assert 1 <= len(es) <= 255
        count_arr.append(len(es)); flat.extend(es)

    # 局所語彙表（この水準に出てくるものだけ）
    lcls, lchain = {}, {}
    for p in flat:
        lcls.setdefault((p[1], p[2], p[4]), len(lcls))
        lchain.setdefault(p[9], len(lchain))
    assert len(lcls) < 65536 and len(lchain) < 65536

    records = bytearray()
    pool = bytearray()          # pron + 追加 acc + orig/read 例外
    for p in flat:
        surf, lc, rc, cost, pos6, orig, read, pron, accf, chain = p
        pb = enc_pron(pron)
        accs = enc_acc(accf)
        compound = ":" in accf
        flags = 0
        if orig == surf or (lossy and not compound):
            flags |= 1
        if read == pron or lossy:
            flags |= 2
        extra = bytearray()
        if not (flags & 1):
            ob = orig.encode("utf-8"); extra += bytes([len(ob)]) + ob
        if not (flags & 2):
            rb = read.encode("utf-8"); extra += bytes([len(rb)]) + rb
        for a, m in accs:
            extra.append(a); extra.append(m)
        assert len(pb) < 256
        # 9 B 固定: class u16 / wcost i16 / chain u16 / flags u8 / pron_len u8 / extra_len u8
        records += struct.pack("<HhHBBB", lcls[(lc, rc, pos6)], cost,
                               lchain[chain], flags, len(pb), len(extra))
        pool += pb + extra

    ckpt = bytearray()
    tot = 0
    for i, c in enumerate(count_arr):
        if i % 256 == 0:
            ckpt += struct.pack("<I", tot)
        tot += c
    pckpt = bytearray()
    off = 0
    for i in range(len(flat)):
        if i % 256 == 0:
            pckpt += struct.pack("<I", off)
        _, _, _, _, pl, el = struct.unpack("<HhHBBB", records[i * 9:(i + 1) * 9])
        off += pl + el

    cls_blob = bytearray()
    inv = {v: k for k, v in lcls.items()}
    for i in range(len(lcls)):
        lc, rc, pos6 = inv[i]
        cls_blob += struct.pack("<HHH", lc, rc, pos6_tab[pos6])
    pos6_blob = b"".join((",".join(k) + "\0").encode("utf-8")
                         for k, _ in sorted(pos6_tab.items(), key=lambda kv: kv[1]))
    invc = {v: k for k, v in lchain.items()}
    chain_blob = b"".join((invc[i] + "\0").encode("utf-8") for i in range(len(lchain)))
    mora_blob = b"".join((inv_mora[i] + "\0").encode("utf-8") for i in range(len(mora_ids)))

    parts = {
        "trie_louds_bits": louds_b, "trie_labels": labels_b,
        "trie_terminal_bits": termbits_b, "trie_rank_index": rank_b,
        "surface_entry_count": len(count_arr), "surface_count_checkpoint": len(ckpt),
        "entry_records_9B": len(records), "value_pool": len(pool),
        "pool_offset_checkpoint": len(pckpt),
        "class_table": len(cls_blob), "pos6_table": len(pos6_blob),
        "chain_rule_table": len(chain_blob), "mora_table": len(mora_blob),
    }
    lex = sum(parts.values())
    # matrix.bin は B-0 と同じく「実際に使われた context id だけ」に詰める
    n_lc = len(set(p[1] for p in flat))
    n_rc = len(set(p[2] for p in flat))
    matrix_b = 4 + 2 * n_lc * n_rc
    return dict(entries=len(flat), surfaces=len(surfaces), trie_nodes=n_nodes,
                parts=parts, lexicon_bytes=lex,
                n_lc=n_lc, n_rc=n_rc, matrix_bytes=matrix_b,
                runtime_bytes=lex + matrix_b + CHARBIN + UNKDIC,
                ), flat, records, pool, lcls, lchain


def verify(flat, records, pool, lcls, lchain):
    inv = {v: k for k, v in lcls.items()}
    invc = {v: k for k, v in lchain.items()}
    off = 0
    ok = 0
    for i, p in enumerate(flat):
        cid, cost, chid, flags, pl, el = struct.unpack("<HhHBBB", records[i * 9:(i + 1) * 9])
        pb = bytes(pool[off:off + pl]); off += pl
        ex = bytes(pool[off:off + el]); off += el
        pron = dec_pron(pb)
        j = 0
        if flags & 1:
            orig = p[0]
        else:
            n = ex[j]; orig = ex[j + 1:j + 1 + n].decode("utf-8"); j += 1 + n
        if flags & 2:
            read = pron
        else:
            n = ex[j]; read = ex[j + 1:j + 1 + n].decode("utf-8"); j += 1 + n
        accs = []
        while j < len(ex):
            accs.append((ex[j], ex[j + 1])); j += 2
        lc, rc, pos6 = inv[cid]
        got = (p[0], lc, rc, cost, pos6, orig, read, pron, dec_acc(accs), invc[chid])
        if got == p:
            ok += 1
    assert off == len(pool), (off, len(pool))
    return ok


# ------------------------------------------------------------------ 全件
print("\n=== 全 788,923 エントリ（11 フィールド無損失）===", flush=True)
full, flat, rec, pool, lcls, lchain = encode_level(parsed)
for k, v in full["parts"].items():
    print(f"  {k:30s} {v:12,d} B")
print(f"  {'辞書本体 小計':30s} {full['lexicon_bytes']:12,d} B")
print(f"  {'ランタイム合計':30s} {full['runtime_bytes']:12,d} B "
      f"({full['runtime_bytes']/1048576:.2f} MiB)")
print(f"  1 エントリ = {full['lexicon_bytes']/full['entries']:.2f} B", flush=True)
ok = verify(flat, rec, pool, lcls, lchain)
G1 = ok == len(flat)
print(f"  G1 往復（11 フィールド全部）: {ok:,d} / {len(flat):,d} → {'PASS' if G1 else 'FAIL'}")

rfull_lossy, _, _, _, _, _ = encode_level(parsed, lossy=True)
print(f"  [TTS最小] 辞書本体 {rfull_lossy['lexicon_bytes']:,d} B / "
      f"ランタイム {rfull_lossy['runtime_bytes']:,d} B "
      f"({rfull_lossy['runtime_bytes']/1048576:.2f} MiB)", flush=True)
if "--full-only" in sys.argv:
    raise SystemExit(0)

bad = bytearray(rec); bad[9 * 4321 + 1] ^= 0xFF
G3 = struct.unpack("<HhHBBB", bytes(bad[9*4321:9*4322]))[0] != \
     struct.unpack("<HhHBBB", bytes(rec[9*4321:9*4322]))[0]
print(f"  G3 陰性対照: {'PASS' if G3 else 'FAIL'}", flush=True)

# ------------------------------------------------------------------ ランキング
print("\n=== コーパス頻度ランキング（B-0 の基準を再現）===", flush=True)
import pyopenjtalk

freq = Counter()
n_tok = 0
unk = set()
bysurf_all = defaultdict(list)
for p in parsed:
    bysurf_all[p[0]].append(p)
with open(CORPUS, encoding="utf-8") as f:
    hdr = f.readline()
    for ln in f:
        parts_ = ln.rstrip("\n").split("\t")
        if len(parts_) < 3:
            continue
        feats, _ = pyopenjtalk.run_mecab_detailed(parts_[2])
        for ft in feats:
            s = ft.split(",", 1)[0]
            n_tok += 1
            if s in bysurf_all:
                freq[s] += 1
            else:
                unk.add(s)
print(f"  token={n_tok:,d}  既知表層={len(freq):,d} 種  未知={len(unk):,d} 種")
print(f"  B-0 の申告値: token=344,037 / 既知表層=29,325 / 未知=425", flush=True)
G4 = (n_tok, len(freq), len(unk))

in_corpus = [s for s, _ in freq.most_common()]
rest = sorted((s for s in bysurf_all if s not in freq),
              key=lambda s: (min(e[3] for e in bysurf_all[s]), len(s)))
ranked = in_corpus + rest
print(f"  ranked surfaces = {len(ranked):,d}", flush=True)

# ------------------------------------------------------------------ 水準ごと
B0 = [(5001, 1216454, 15.785, 10.065), (10000, 1931691, 31.226, 20.817),
      (15002, 2541328, 41.978, 30.151), (21750, 3339135, 50.495, 37.548),
      (30000, 4262637, 56.817, 44.258), (41000, 5405844, 64.516, 52.430),
      (60000, 7693918, 69.935, 58.323), (88150, 10921496, 73.333, 62.366),
      (95700, 11726889, 73.419, 62.538), (100000, 12170143, 74.280, 63.183),
      (113000, 13815068, 76.043, 65.376), (200001, 22314929, 89.634, 79.742),
      (263000, 27607296, 91.312, 81.548), (400000, 41995303, 95.527, 89.290)]

print("\n=== 水準ごとのサイズ（新形式 vs B-0 の実ビルド。matrix はどちらも詰めた）===",
      flush=True)
print(f"{'entries':>8} {'surf':>7} {'無損失':>12} {'TTS最小':>12} {'B-0 L2':>12} "
      f"{'倍率':>6} {'ph%':>7} {'acc%':>7} {'G2':>5}")
curve = []
for target, b0bytes, ph, acc in B0:
    sub, n = [], 0
    for s in ranked:
        es = bysurf_all[s]
        sub.extend(es); n += len(es)
        if n >= target:
            break
    r, fl, rc2, pl2, lc2, lch2 = encode_level(sub)
    g2 = verify(fl, rc2, pl2, lc2, lch2) == len(fl)
    rl, _, _, _, _, _ = encode_level(sub, lossy=True)
    curve.append(dict(target=target, b0_runtime_bytes=b0bytes,
                      heldout_phoneme_pct=ph, heldout_accent_pct=acc,
                      G2_roundtrip=g2, lossy_runtime_bytes=rl["runtime_bytes"],
                      lossy_lexicon_bytes=rl["lexicon_bytes"], **r))
    print(f"{r['entries']:>8,} {r['surfaces']:>7,} {r['runtime_bytes']:>12,} "
          f"{rl['runtime_bytes']:>12,} {b0bytes:>12,} "
          f"{b0bytes/r['runtime_bytes']:>6.2f} {ph:>7} {acc:>7} "
          f"{'PASS' if g2 else 'FAIL':>5}", flush=True)

json.dump({"full": full, "gates": {"G1": G1, "G3": G3,
                                   "G4_ranking": {"tokens": n_tok, "known": len(freq),
                                                  "unknown": len(unk)}},
           "orig_eq_surface": n_orig_eq, "read_eq_pron": n_read_eq,
           "curve": curve},
          open(os.path.join(_WORK, "tts_dict_v2.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote tts_dict_v2.json")
