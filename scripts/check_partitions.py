#!/usr/bin/env python3
"""esp32/partitions.csv を検査する（c'-4）。

ESP-IDF 同梱の gen_esp32part.py が無いので自前で見る。**構文と配置だけ**で、
実機に焼けるかは検証していない。

    uv run python scripts/check_partitions.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "esp32" / "partitions.csv"

ALIGN_APP = 0x10000   # app パーティションは 64 KB 境界
ALIGN_ANY = 0x1000    # それ以外は 4 KB 境界
MMAP_PAGE = 0x10000   # esp_partition_mmap が丸める単位


def parse_size(s: str) -> int:
    s = s.strip()
    if s.endswith(("K", "k")):
        return int(s[:-1], 0) * 1024
    if s.endswith(("M", "m")):
        return int(s[:-1], 0) * 1024 * 1024
    return int(s, 0)


def _declared(path: pathlib.Path, key: str):
    """CSV 先頭のコメントから `# <key>: <値>` を読む。無ければ None。

    ⚠️ **推定しないための仕組み。** 表が自分で名乗る。
    """
    for ln in path.read_text(encoding="utf-8").splitlines():
        t = ln.strip()
        if not t.startswith("#"):
            if t:
                break          # コメント帯は表の前だけ
            continue
        t = t.lstrip("#").strip()
        if t.lower().startswith(key + ":"):
            # ⚠️ 値の後ろの注記を拾わないよう **先頭トークンだけ**取る
            v = t.split(":", 1)[1].strip().split()[0].upper().replace("_", "").replace(",", "")
            if v.endswith("MB"):
                return int(v[:-2]) * 1024 * 1024
            if v.endswith("KB"):
                return int(v[:-2]) * 1024
            if v.endswith("B"):
                v = v[:-1]
            return int(v)
    return None


def declared_flash(path: pathlib.Path):
    return _declared(path, "flash")


def declared_app_min(path: pathlib.Path):
    return _declared(path, "app-min")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None,
                    help="検査する csv（既定は esp32/partitions.csv）")
    ap.add_argument("--flash-size", type=int, default=None,
                    help="フラッシュ容量（B）。終端がここを超えたら NG")
    ap.add_argument("--rodata", action="store_true",
                    help="重みを app の .rodata に埋める表（esp32/boards/*）。`model` 行は無くてよく、"
                         "代わりに app が 1.5 MB + int8 blob ぶん以上あることを要求する")
    args = ap.parse_args(argv)   # ⚠️ `a` は下の for で使われている。被せない
    global CSV
    if args.file:
        CSV = pathlib.Path(args.file)
        if not CSV.is_absolute():
            CSV = ROOT / args.file
    if not CSV.exists():
        print(f"NG! 無い: {CSV}")
        return 1

    rows = []
    for line in CSV.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        f = [x.strip() for x in line.split(",")]
        if len(f) < 5:
            print(f"NG! 列が足りない: {line}")
            return 1
        rows.append({
            "name": f[0], "type": f[1], "subtype": f[2],
            "offset": int(f[3], 0), "size": parse_size(f[4]),
        })

    bad = 0
    print(f"{CSV.name}: {len(rows)} パーティション")
    print(f"  {'name':10s} {'type':6s} {'subtype':9s} {'offset':>10s} {'size':>10s} {'end':>10s}")
    for r in rows:
        print(f"  {r['name']:10s} {r['type']:6s} {r['subtype']:9s} "
              f"0x{r['offset']:08x} {r['size']:10d} 0x{r['offset'] + r['size']:08x}")

    # --- 重なりと境界 ---
    ordered = sorted(rows, key=lambda r: r["offset"])
    for a, b in zip(ordered, ordered[1:]):
        if a["offset"] + a["size"] > b["offset"]:
            print(f"NG! {a['name']} と {b['name']} が重なっている")
            bad += 1
    for r in rows:
        align = ALIGN_APP if r["type"] == "app" else ALIGN_ANY
        if r["offset"] % align:
            print(f"NG! {r['name']} の offset 0x{r['offset']:x} が "
                  f"0x{align:x} の倍数でない")
            bad += 1

    by_name = {r["name"]: r for r in rows}

    # --- model パーティション ---
    if args.rodata:
        if "model" in by_name:
            print("NG! --rodata の表に `model` パーティションがある（app に埋めるので要らない。"
                  "残すと「どちらを読んでいるか」が分からなくなる）")
            bad += 1
        else:
            print("  OK  model パーティション無し（重みは app の .rodata）")
    elif "model" not in by_name:
        print("NG! `model` パーティションが無い")
        bad += 1
    else:
        m = by_name["model"]
        # ⚠️ esp_partition_mmap は 64 KB 境界に丸めてマップし、要求 offset に
        #    合わせたポインタを返す。offset が 64 KB 境界なら返却ポインタも
        #    64 KB アライン = コアが要る 4 バイト境界と、将来 PIE が要る
        #    16 バイト境界を同時に満たす。
        if m["offset"] % MMAP_PAGE:
            print(f"NG! model の offset 0x{m['offset']:x} が 64 KB 境界でない。"
                  f"esp_partition_mmap の返却ポインタが 16 バイト境界を満たさなくなる")
            bad += 1
        else:
            print(f"  OK  model の offset 0x{m['offset']:08x} は 64 KB 境界")

        # ⚠️ **fp32 blob は既定の出荷物ではない**（v0.1.1 は int8）。
        #    入らないのは想定どおりなので、**NG にしない**（注記だけ出す）。
        for name, path, fatal in [
                ("student.bin", ROOT / "csrc" / "student.bin", False),
                ("student_i8.bin", ROOT / "csrc" / "student_i8.bin", True)]:
            if not path.exists():
                continue
            n = path.stat().st_size
            ok = n <= m["size"]
            mark = "OK " if ok else ("NG!" if fatal else "－ ")
            note = "" if ok or fatal else "（fp32 は出荷物ではない。int8 を焼く）"
            print(f"  {mark} {name} {n:,} B "
                  f"{'<=' if ok else '>'} model {m['size']:,} B{note}")
            if not ok and fatal:
                bad += 1

    # --- app パーティション ---
    apps = [r for r in rows if r["type"] == "app"]
    if not apps:
        print("NG! app パーティションが無い")
        bad += 1
    else:
        # ⚠️ --rodata では app に blob（int8 v2 654,032 B）が乗る。実測の app 285,440 B +
        #    blob = 928,832 B（QEMU 構成）で、M5Unified + M5GFX を足すと 1.3 MB（報告値）。
        # ⚠️ **表ごとに違う。** 固定 1,500,000 だと、app を小さく取る構成
        #    （8 MB 板など）を通すために下限を下げると、**M5 表の防御まで外れる**。
        #    CSV に `# app-min: <bytes>` があればそれを使う。
        need = declared_app_min(CSV)
        if need is None:
            need = 1_500_000 + (654_032 if args.rodata else 0)
        for a in apps:
            ok = a["size"] >= need
            print(f"  {'OK ' if ok else 'NG!'} app '{a['name']}' {a['size']:,} B "
                  f"（{need:,} B 以上あるか{'。--rodata は blob ぶん上乗せ' if args.rodata else ''}）")
            if not ok:
                bad += 1

    # --- 辞書パーティション（漢字対応版だけ）---
    for d in [r for r in rows if r["name"] == "dict"]:
        if d["offset"] % MMAP_PAGE:
            print(f"NG! dict の offset 0x{d['offset']:x} が 64 KB 境界でない")
            bad += 1
        else:
            print(f"  OK  dict の offset 0x{d['offset']:08x} は 64 KB 境界")
        blob = ROOT / "csrc" / "k1_dict.bin"
        if blob.exists():
            n = blob.stat().st_size
            ok = n <= d["size"]
            print(f"  {'OK ' if ok else 'NG!'} k1_dict.bin {n:,} B "
                  f"{'<=' if ok else '>'} dict {d['size']:,} B")
            if not ok:
                bad += 1

    total = max(r["offset"] + r["size"] for r in rows)
    # ⚠️ **推定しない。** かつては「dict 行があれば 16 MB 前提」と決め打っていて、
    #    **どの呼び出し側も --flash-size を渡していなかった**ので、8 MB の表に dict を
    #    足した瞬間に超過検査が黙って無効になる形だった（M-100 のレビューで発覚）。
    #    いまは **CSV 自身に `# flash: 16MB` を書かせ**、無ければ NG にする。
    flash = args.flash_size
    if flash is None:
        flash = declared_flash(CSV)
    if flash is None:
        print(f"NG! {CSV.name} に `# flash: <N>MB` の宣言が無い"
              f"（推定はしない。表の先頭にコメントで書くこと）")
        bad += 1
        flash = total          # 超過検査だけは通す（別の NG が既に立っている）
    print(f"  末尾 0x{total:08x} = {total / 1048576:.2f} MB "
          f"（宣言されたフラッシュ {flash / 1048576:.0f} MB）")
    if total > flash:
        print(f"NG! {flash / 1048576:.0f} MB flash に収まらない")
        bad += 1

    print("\n" + ("NG: partitions.csv に問題がある" if bad
                  else "partitions.csv: 検査した項目はすべて OK"))
    print("⚠️ **実機に焼いていない。** gen_esp32part.py にもかけていない")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
