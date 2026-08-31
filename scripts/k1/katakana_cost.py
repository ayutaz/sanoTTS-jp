"""端末でカタカナ入力を受けるコストを測る。

いまの入力仕様は「ひらがな + アクセント記号 + 無声化マーク」。
カタカナを受けるには何が要るのか、テーブルが増えるのかを実測する。

ゲート:
  G7  凍結テーブル 全キーが「カタカナ → 折り返し → ひらがな」で往復する
  G8  陰性対照: 折り返しを外すと G7 が落ちる
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import json
import os
import sys

ROOT = (_ROOT + "")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

tbl = json.load(open(os.path.join(ROOT, "csrc/g2p_table.json"), encoding="utf-8"))
keys = tbl["mora"] if isinstance(tbl, dict) and "mora" in tbl else tbl
if isinstance(keys, dict):
    mora_keys = list(keys.keys())
else:
    mora_keys = list(keys)
print(f"凍結テーブル: {type(tbl).__name__}, "
      f"トップレベルキー = {list(tbl)[:8] if isinstance(tbl, dict) else 'list'}")
print(f"mora キー数 = {len(mora_keys)}")
print(f"先頭 10 = {mora_keys[:10]}")

# --- カタカナ → ひらがな折り返し（UTF-8 バイト列で完結するか）-----------------

def fold_cp(ch):
    """コードポイント演算だけの折り返し。U+30A1..U+30F6 → U+3041..U+3096"""
    c = ord(ch)
    if 0x30A1 <= c <= 0x30F6:
        return chr(c - 0x60)
    return ch


def fold_utf8(b):
    """UTF-8 バイト列のままの折り返し。端末の C 実装に相当する形。

    ひらがな U+3041..U+3096 = E3 81 81 .. E3 82 96
    カタカナ U+30A1..U+30F6 = E3 82 A1 .. E3 83 B6
    → 3 バイト目が 0xA1..0xBF なら 2 バイト目 -1, 3 バイト目 -0x20
      3 バイト目が 0x80..0xB6 かつ 2 バイト目 0x83 なら 2 バイト目 -1, 3 バイト目 +0x20
    """
    out = bytearray()
    i = 0
    while i < len(b):
        if (i + 2 < len(b) and b[i] == 0xE3
                and ((b[i + 1] == 0x82 and 0xA1 <= b[i + 2] <= 0xBF)
                     or (b[i + 1] == 0x83 and 0x80 <= b[i + 2] <= 0xB6))):
            cp = ((b[i] & 0x0F) << 12) | ((b[i + 1] & 0x3F) << 6) | (b[i + 2] & 0x3F)
            cp -= 0x60
            out += bytes([0xE0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F)])
            i += 3
        else:
            out.append(b[i]); i += 1
    return bytes(out)


# --- G7: 全キーの往復 --------------------------------------------------------

def to_kata(s):
    return "".join(chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in s)


ng = []
for k in mora_keys:
    kata = to_kata(k)
    back_cp = "".join(fold_cp(c) for c in kata)
    back_b = fold_utf8(kata.encode("utf-8")).decode("utf-8")
    if back_cp != k or back_b != k:
        ng.append((k, kata, back_cp, back_b))
G7 = not ng
print(f"\n=== G7: 凍結テーブル {len(mora_keys)} キーの カタカナ往復 ===")
print(f"  不一致 {len(ng)} 件 → {'PASS' if G7 else 'FAIL'}")
for x in ng[:10]:
    print("   NG:", x)

# --- G8: 陰性対照 ------------------------------------------------------------
ng2 = sum(1 for k in mora_keys if to_kata(k) != k and to_kata(k) != k)
raw_ng = sum(1 for k in mora_keys if to_kata(k) != k)
G8 = raw_ng > 0
print(f"\n=== G8: 陰性対照（折り返さないと何件ずれるか）===")
print(f"  折り返し無しでずれるキー = {raw_ng} / {len(mora_keys)} → "
      f"{'PASS' if G8 else 'FAIL'}")

# --- 折り返しで足りない文字 --------------------------------------------------
extra = sorted(set("ヴヵヶヷヸヹヺヰヱ・ーヽヾ"))
print("\n=== 折り返し規則の外にあるカタカナ ===")
for ch in extra:
    f = fold_cp(ch)
    in_tbl = f in mora_keys or ch in mora_keys
    print(f"  {ch} U+{ord(ch):04X} → {f} U+{ord(f):04X}  テーブルにある: {in_tbl}")

# --- 現行の中間表現にカタカナは出るか ----------------------------------------
print("\n=== 現行の中間表現（ホスト G2P の出力）にカタカナは出るか ===")
import kana_g2p as K
table, src = K.mora_table(prefer_frozen=True)
print(f"  テーブル出所: {src} / {len(table)} エントリ / "
      f"{K.table_size_bytes(table)} B")
n = 0
kata_lines = 0
with open(os.path.join(ROOT, "data/splits/corpus_heldout.tsv"), encoding="utf-8") as f:
    f.readline()
    for ln in f:
        p = ln.rstrip("\n").split("\t")
        if len(p) < 3:
            continue
        n += 1
        if n > 300:
            break
        try:
            im = "".join(K.text_to_intermediate(p[2], table))
        except Exception:
            continue
        if any(0x30A1 <= ord(c) <= 0x30F6 for c in im):
            kata_lines += 1
print(f"  {min(n,300)} 文中、中間表現にカタカナを含む行 = {kata_lines}")

json.dump({"mora_keys": len(mora_keys), "G7_katakana_roundtrip": G7,
           "G8_negative_control": G8, "keys_needing_fold": raw_ng,
           "intermediate_lines_with_katakana": kata_lines,
           "table_bytes": K.table_size_bytes(table)},
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "katakana_cost.json"), "w"), ensure_ascii=False, indent=1)
print("\nwrote katakana_cost.json")
