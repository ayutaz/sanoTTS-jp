"""K-1: D-042 の動作点で辞書バイナリを組み、受け入れ条件 G1〜G5 を通す。

    uv run python scripts/k1/k1_build_dict.py [--entries N] [--out PATH]

既定は D-042 の **370,863 entries**（N16R8 / OTA 無し）。

受け入れ条件（すべて陰性対照つき）:
  G1 全エントリで 11 フィールド往復一致
  G2 レコードを 1 バイト壊すと G1 が落ちる
  G3 common-prefix-search が総当りと一致（ヒット数を併記）
  G4 実際にヒットした経路上のラベルを壊すと G3 が落ちる
  G5 チェックポイント復元 == materialise

⚠️ **最初に G0-2（辞書の同一性）を通す。** このマシンには sys.dic が
3 リビジョン同居していて、取り違えると測定が別物になる（C-045 / D-042）。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import random
import sys
import time
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))

from dump_entries_lib import load_entries          # noqa: E402
from k1_paths import HELDOUT, TRAIN                # noqa: E402
from saanotts_jp.k1_dict import DictBlob, Entry    # noqa: E402

TARGET_ENTRIES = 370_863          # D-042
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"    {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=TARGET_ENTRIES)
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-verify-dict", action="store_true")
    a = ap.parse_args()

    # --- G0-2 を先に通す ---------------------------------------------------
    print("=== G0-2: 辞書の同一性（D-042）===")
    if a.skip_verify_dict:
        print("  ⚠️ SKIP を指定された。**SKIP を「通った」と読まないこと。**")
    else:
        import k0_verify_dict
        if k0_verify_dict.main() != 0:
            print("\nNG! 辞書が D-042 の凍結物と違う。ここで止める。")
            return 1

    import k0_freeze_dict
    dic = k0_freeze_dict.resolve_dict_dir()
    t0 = time.time()
    raw = load_entries(dic)
    print(f"\n辞書 {dic}\n  {len(raw):,d} entries を {time.time()-t0:.1f} 秒で読んだ")

    # --- ランキング（B-0 / K-0 と同じ基準）--------------------------------
    import pyopenjtalk
    bysurf: dict[str, list] = defaultdict(list)
    for r in raw:
        bysurf[r[0]].append(r)
    freq: Counter[str] = Counter()
    t0 = time.time()
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
    print(f"  ランキング {len(ranked):,d} 見出し語 ({time.time()-t0:.0f} 秒)")

    sub_rows, n = [], 0
    for s in ranked:
        sub_rows.extend(bysurf[s])
        n += len(bysurf[s])
        if n >= a.entries:
            break

    # posid は (lc, rc, pos6) から一意に決まる（K-1 §2-1）ので token から拾う
    entries = [Entry(surface=r[0], lc=r[1], rc=r[2], wcost=r[3], pos6=r[4],
                     posid=0, orig=r[5], read=r[6], pron=r[7], acc=r[8], chain=r[9])
               for r in sub_rows]
    print(f"  対象 {len(entries):,d} entries / "
          f"{len(set(e.surface for e in entries)):,d} 見出し語")

    # --- 組み立て -----------------------------------------------------------
    t0 = time.time()
    blob = DictBlob.build(entries)
    body = blob.to_bytes()
    print(f"\n=== 組み立て ({time.time()-t0:.0f} 秒) ===")
    secs = DictBlob.sections(body)
    for nm in sorted(secs, key=lambda k: secs[k][0]):
        o, l = secs[nm]
        print(f"  {nm:10s} {l:>12,d} B  @ 0x{o:08X}")
    print(f"  {'合計':10s} {len(body):>12,d} B = {len(body)/1048576:.2f} MiB")

    # --- G1 ---------------------------------------------------------------
    print("\n=== G1: 全エントリの 11 フィールド往復 ===")
    t0 = time.time()
    back = DictBlob.from_bytes(body)
    got = back.all_entries()
    exp = sorted(entries, key=lambda e: (e.surface, e.lc, e.rc, e.wcost, e.pron))
    gs = sorted(got, key=lambda e: (e.surface, e.lc, e.rc, e.wcost, e.pron))
    same = sum(1 for x, y in zip(exp, gs) if x == y)
    check("エントリ数", len(got) == len(entries), f"{len(got):,d} / {len(entries):,d}")
    check("11 フィールド一致", same == len(exp),
          f"{same:,d} / {len(exp):,d} ({time.time()-t0:.0f} 秒)")
    if same != len(exp):
        for x, y in zip(exp, gs):
            if x != y:
                print(f"    期待 {x}\n    実際 {y}")
                break

    # --- G2 陰性対照 --------------------------------------------------------
    print("\n=== G2: 陰性対照（レコードを 1 バイト壊す）===")
    ro, _re = DictBlob.record_region(body)
    broken = bytearray(body)
    broken[ro + 1] ^= 0xFF
    try:
        g2 = DictBlob.from_bytes(bytes(broken)).all_entries()
        differs = sorted(g2, key=lambda e: e.surface) != sorted(got, key=lambda e: e.surface)
    except Exception as e:  # noqa: BLE001
        differs = True
        print(f"    復元できず: {type(e).__name__}")
    check("壊すと復元結果が変わる", differs)

    # --- G3 / G4 ------------------------------------------------------------
    print("\n=== G3: common-prefix-search（held-out で総当りと照合）===")
    texts = []
    with open(HELDOUT, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                texts.append(p[2])
    random.seed(0)
    texts = [texts[int(i * len(texts) / 120)] for i in range(120)]   # 全ソースから
    kset = {back.keys.encode(s) for s in back.surfaces}
    maxlen = max(len(k) for k in kset)
    pos = hits = multi = mismatch = 0
    for t in texts:
        kb = back.keys.encode(t)
        for i in range(len(kb)):
            got_l = [n for n, _r in back.louds.common_prefix_search(kb, i)]
            exp_l = [n for n in range(1, min(maxlen, len(kb) - i) + 1)
                     if kb[i:i + n] in kset]
            pos += 1
            hits += len(got_l)
            multi += sum(1 for n in got_l if n >= 2)
            if got_l != exp_l:
                mismatch += 1
    check("総当りと一致", mismatch == 0, f"不一致 {mismatch} / 照合 {pos:,d} 位置")
    check("ゲートが空虚でない", hits > 0, f"ヒット {hits:,d} 件")
    check("多記号のヒットが十分ある", multi > hits * 0.2,
          f"2 記号以上 {multi:,d} 件 ({100*multi/max(hits,1):.1f}%)")

    print("\n=== G4: 陰性対照（ヒット経路上のラベルを壊す）===")
    # ⚠️ **「辿ったノード」では弱い。** ヒットを 1 件も生まない経路のノードを壊しても
    #    参照側も空なので一致してしまい、陰性対照が空虚になる（実際に踏んだ）。
    #    **ヒットに実際に寄与したノード**（照合成功した終端そのもの）を壊す。
    hit_nodes = []
    for t in texts[:20]:
        kb = back.keys.encode(t)
        for i in range(len(kb)):
            seen = back.louds.visited_nodes(kb, i)
            for n, _r in back.louds.common_prefix_search(kb, i):
                hit_nodes.append(seen[n - 1])       # 長さ n のヒットを作った終端
    check("ヒットを生んだノードを特定", len(hit_nodes) > 0,
          f"{len(hit_nodes):,d} 件（辿っただけのノードではない）")
    bad_trie = back.louds.with_broken_label(hit_nodes[len(hit_nodes) // 2])
    mism2 = 0
    for t in texts[:20]:
        kb = back.keys.encode(t)
        for i in range(len(kb)):
            g = [n for n, _r in bad_trie.common_prefix_search(kb, i)]
            e = [n for n in range(1, min(maxlen, len(kb) - i) + 1)
                 if kb[i:i + n] in kset]
            if g != e:
                mism2 += 1
    check("壊すと落ちる", mism2 > 0, f"不一致 {mism2} 件")

    # --- G5 -----------------------------------------------------------------
    print("\n=== G5: 値プールのオフセット復元 ===")
    mat = back.pool_offsets_materialised()
    idx = random.sample(range(len(mat)), min(20000, len(mat)))
    bad5 = sum(1 for i in idx if back.pool_offset_from_checkpoint(i) != mat[i])
    check("チェックポイント復元が一致", bad5 == 0, f"不一致 {bad5} / 標本 {len(idx):,d}")
    # ⚠️ **固定の 1 番を壊すのは弱い。** チェックポイント 1 が覆うのは
    #    エントリ 32..63 だけで、789,388 件から 20,000 件を抽くと
    #    そこに 1 件も当たらないことがある（実際に不一致 0 件で空虚になった）。
    #    **標本が実際に入っているブロック**を壊す。
    target_block = idx[0] // back.CHECKPOINT
    bck = back.with_broken_checkpoint(target_block)
    in_block = [i for i in idx if i // back.CHECKPOINT == target_block]
    check("壊すブロックに標本が入っている", len(in_block) > 0,
          f"ブロック {target_block} に {len(in_block)} 件")
    bad5b = sum(1 for i in idx if bck.pool_offset_from_checkpoint(i) != mat[i])
    check("陰性対照: ずらすと落ちる", bad5b > 0, f"不一致 {bad5b} 件")

    # --- 出力 ---------------------------------------------------------------
    if a.out:
        pathlib.Path(a.out).write_bytes(body)
        print(f"\n書き出した → {a.out} ({len(body):,d} B)")

    print()
    if FAILED:
        print(f"NG! {len(FAILED)} 件: {', '.join(FAILED)}")
        return 1
    print(f"OK  G1〜G5 すべて通過 / blob {len(body):,d} B = {len(body)/1048576:.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
