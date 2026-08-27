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


def main() -> int:
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
    print(f"{CSV.relative_to(ROOT)}: {len(rows)} パーティション")
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
    if "model" not in by_name:
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

        blobs = {p.name: p for p in [ROOT / "csrc" / "student.bin",
                                     ROOT / "csrc" / "student_i8.bin"] if p.exists()}
        for name, p in sorted(blobs.items()):
            n = p.stat().st_size
            ok = n <= m["size"]
            print(f"  {'OK ' if ok else 'NG!'} {name} {n:,} B "
                  f"{'<=' if ok else '>'} model {m['size']:,} B")
            if not ok:
                bad += 1

    # --- app パーティション ---
    apps = [r for r in rows if r["type"] == "app"]
    if not apps:
        print("NG! app パーティションが無い")
        bad += 1
    else:
        for a in apps:
            ok = a["size"] >= 1_500_000
            print(f"  {'OK ' if ok else 'NG!'} app '{a['name']}' {a['size']:,} B "
                  f"（1.5 MB 以上あるか）")
            if not ok:
                bad += 1

    total = max(r["offset"] + r["size"] for r in rows)
    print(f"  末尾 0x{total:08x} = {total / 1048576:.2f} MB "
          f"（sdkconfig.defaults は 8 MB flash を宣言）")
    if total > 8 * 1024 * 1024:
        print("NG! 8 MB flash に収まらない")
        bad += 1

    print("\n" + ("NG: partitions.csv に問題がある" if bad
                  else "partitions.csv: 検査した項目はすべて OK"))
    print("⚠️ **実機に焼いていない。** gen_esp32part.py にもかけていない")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
