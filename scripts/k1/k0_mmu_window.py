"""K-0: ESP32-S3 のデータ mmap 窓に辞書が入るかを、ESP-IDF のソースから確定する。

K-1 調査の残った唯一のブロッカー候補。B-0 §5 は「512 エントリ × 64 KB = 32 MB を
flash と PSRAM で共有」と書いているが、**その数字の出どころを誰も確認していない**。
ESP-IDF は手元に入っているので、推測ではなくヘッダの定数を読む。

⚠️ **これはソース読解であって実機実測ではない。** 実機で確かめるまでは
「ヘッダにこう書いてある」までしか言えない。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

IDF = pathlib.Path(os.environ.get("IDF_PATH", os.path.expanduser("~/esp/esp-idf")))
if not IDF.is_dir():
    sys.exit(f"ESP-IDF が見つからない: {IDF}")

print(f"IDF_PATH = {IDF}")
gv = IDF / "tools/cmake/version.cmake"
if gv.exists():
    txt = gv.read_text(encoding="utf-8", errors="replace")
    ver = {k: v for k, v in re.findall(r"set\(IDF_VERSION_(\w+)\s+(\d+)\)", txt)}
    print("IDF version =", ".".join(ver.get(k, "?") for k in ("MAJOR", "MINOR", "PATCH")))

# --- 探す定数 --------------------------------------------------------------
WANT = [
    "SOC_MMU_PAGE_SIZE",
    "SOC_MMU_LINEAR_ADDRESS_REGION_NUM",
    "SOC_MMU_PERIPH_NUM",
    "SOC_MMU_ENTRY_NUM",
    "SOC_MMU_DBUS_VADDR_BASE",
    "SOC_MMU_IBUS_VADDR_BASE",
    "SOC_DRAM0_CACHE_ADDRESS_LOW",
    "SOC_DRAM0_CACHE_ADDRESS_HIGH",
    "SOC_IRAM0_CACHE_ADDRESS_LOW",
    "SOC_IRAM0_CACHE_ADDRESS_HIGH",
    "SOC_MMU_VALID",
    "SOC_MMU_INVALID",
    "SOC_EXTRAM_DATA_LOW",
    "SOC_EXTRAM_DATA_HIGH",
    "SOC_EXTRAM_DATA_SIZE_MB",
    "SPI_FLASH_MMU_PAGE_SIZE",
]

S3 = IDF / "components/soc/esp32s3"
roots = [S3, IDF / "components/hal/esp32s3", IDF / "components/esp_mm",
         IDF / "components/spi_flash"]

found: dict[str, list[tuple[str, int, str]]] = {w: [] for w in WANT}
scanned = 0
for root in roots:
    if not root.is_dir():
        continue
    for f in root.rglob("*.h"):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        scanned += 1
        for i, ln in enumerate(lines, 1):
            m = re.match(r"\s*#define\s+(\w+)\s+(.+?)\s*(?://.*)?$", ln)
            if m and m.group(1) in found:
                found[m.group(1)].append((str(f.relative_to(IDF)), i, m.group(2).strip()))

print(f"走査したヘッダ = {scanned:,d}\n")
print("=== ESP32-S3 の MMU 定数（ESP-IDF のヘッダから）===")
for w in WANT:
    hits = found[w]
    if not hits:
        print(f"  {w:38s} (見つからない)")
        continue
    for path, line, val in hits[:2]:
        print(f"  {w:38s} = {val:<28s} {path}:{line}")

# --- 算術 ------------------------------------------------------------------
def num(name, default=None):
    for _p, _l, v in found.get(name, []):
        v = v.strip()
        try:
            return int(v, 0)
        except ValueError:
            m = re.match(r"\(?\s*(0x[0-9a-fA-F]+|\d+)\s*\)?$", v)
            if m:
                return int(m.group(1), 0)
    return default


page = num("SOC_MMU_PAGE_SIZE")
entries = num("SOC_MMU_ENTRY_NUM")
dlow, dhigh = num("SOC_DRAM0_CACHE_ADDRESS_LOW"), num("SOC_DRAM0_CACHE_ADDRESS_HIGH")
elow, ehigh = num("SOC_EXTRAM_DATA_LOW"), num("SOC_EXTRAM_DATA_HIGH")

print("\n=== 算術（上の定数から。実機実測ではない）===")
if page and entries:
    total = page * entries
    print(f"  MMU テーブル総容量 = {entries} entries x {page:,d} B = "
          f"{total:,d} B = {total/1048576:.0f} MB")
    print("  ⚠️ この総量は **命令(IBUS) と データ(DBUS) と PSRAM で共有**する")
if dlow and dhigh:
    print(f"  DRAM0 cache vaddr = 0x{dlow:08X}..0x{dhigh:08X} "
          f"= {(dhigh-dlow):,d} B = {(dhigh-dlow)/1048576:.0f} MB")
if elow and ehigh:
    print(f"  EXTRAM data vaddr = 0x{elow:08X}..0x{ehigh:08X} "
          f"= {(ehigh-elow):,d} B = {(ehigh-elow)/1048576:.0f} MB")

# --- 辞書が入るか ----------------------------------------------------------
CASES = [
    ("フル辞書 素の形式", 25_880_002),
    ("フル辞書 最適化後（算術）", 16_128_887),
    ("400,000 entries (95.53%/89.29%)", 14_467_408),
    ("263,000 entries (91.31%/81.55%)", 10_275_693),
]
if dlow and dhigh:
    win = dhigh - dlow
    print(f"\n=== 辞書イメージ vs DRAM0 cache 窓 {win/1048576:.0f} MB ===")
    for name, size in CASES:
        print(f"  {name:36s} {size:>12,d} B  "
              f"{'収まる' if size <= win else '⚠️ 超える'}")
    print("\n  ⚠️ ただし窓は **PSRAM と共有**する。PSRAM を N MB マップすると")
    print("     flash 側に使えるのは (窓 − N) になる。実機で確認すること。")

# --- CONFIG_MMU_PAGE_SIZE の既定と、PSRAM が窓を食う量 --------------------
print("\n=== CONFIG_MMU_PAGE_SIZE の既定（Kconfig）===")
for kc in (IDF / "components/soc/Kconfig", IDF / "components/esp_mm/Kconfig",
           IDF / "components/spi_flash/Kconfig", IDF / "components/esp_system/Kconfig"):
    if not kc.exists():
        continue
    t = kc.read_text(encoding="utf-8", errors="replace")
    if "MMU_PAGE_SIZE" in t:
        blk = t[t.index("MMU_PAGE_SIZE") - 200: t.index("MMU_PAGE_SIZE") + 900]
        for ln in blk.splitlines():
            if re.search(r"MMU_PAGE_(SIZE|64KB|32KB|16KB|8KB)|default|config ", ln):
                print("   ", kc.relative_to(IDF), "|", ln.strip()[:88])
        break

print("\n=== PSRAM は DBUS の窓を共有するか ===")
same = (elow == dlow and ehigh == dhigh)
print(f"  SOC_EXTRAM_DATA_LOW/HIGH == SOC_DRAM0_CACHE_ADDRESS_LOW/HIGH : {same}")
print("  → " + ("**共有する。** PSRAM をマップした分だけ flash 用の vaddr が減る"
                if same else "別窓。PSRAM は flash を圧迫しない"))

MiB = 1024 * 1024
WIN = (dhigh - dlow) if (dlow and dhigh) else 32 * MiB
print(f"\n=== ボード別の flash データ用 vaddr 予算（算術）===")
print(f"{'ボード':22s} {'PSRAM':>8s} {'物理 flash':>11s} {'窓−PSRAM':>12s} {'実効上限':>12s}")
BOARDS = [
    ("ESP32-S3-WROOM-1-N16R8", 8 * MiB, 16 * MiB),
    ("ESP32-S3-WROOM-1-N8R8", 8 * MiB, 8 * MiB),
    ("ESP32-S3-WROOM-2-N32R8V", 8 * MiB, 32 * MiB),
    ("ESP32-S3-WROOM-2-N32R16V", 16 * MiB, 32 * MiB),
    ("(PSRAM 無効)", 0, 32 * MiB),
]
budgets = {}
for name, psram, flash in BOARDS:
    vaddr = WIN - psram
    eff = min(vaddr, flash)
    budgets[name] = eff
    print(f"{name:22s} {psram//MiB:>6d} MB {flash//MiB:>9d} MB "
          f"{vaddr:>12,d} {eff:>12,d}")

print(f"\n=== 辞書が入るか（実効上限に対して。app と model の枠は別途引く）===")
print(f"{'辞書':36s} " + " ".join(f"{n.split('-')[-1]:>10s}" for n, _, _ in BOARDS))
for dname, size in CASES:
    row = []
    for name, _, _ in BOARDS:
        row.append("  収まる  " if size <= budgets[name] else "  ⚠️超過 ")
    print(f"{dname:36s} " + " ".join(f"{c:>10s}" for c in row))

print("""
⚠️ **これは vaddr 窓の上限だけ。** 実際にはここから app パーティション・
   model パーティション・OTA 枠を引く（reports/b0_flash_budget.json）。
⚠️ **ソース読解であって実機実測ではない。** ESP-IDF 5.5.0 のヘッダに
   そう書いてある、までしか言えない。
⚠️ PSRAM は既定で全量マップされる想定で引いた。マップ量を減らす設定が
   あるかは未確認。""")
