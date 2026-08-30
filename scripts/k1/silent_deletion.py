"""「未知語は無音で消える」を、CLAUDE.md / b0_alternatives.json の実例で再現する。

CLAUDE.md は 齟齬 / 蜃気楼 / 氷点下 を挙げ、
reports/b0_alternatives.json は 饕餮 / 蹉跌 / 忸怩たる思い を挙げている。
**フル辞書で本当に消えるのか**を確かめる。消えないなら、その例は
枝刈り辞書での観測であって「フル辞書でも起きる」の根拠にはならない。

⚠️ 再現しないことは、それ自体が最重要の指摘になる（verifying-reports）。
"""
import pyopenjtalk

CLAUDE_MD = ["齟齬", "蜃気楼", "氷点下"]
B0_JSON = ["饕餮", "蹉跌", "忸怩たる思い"]
EXTRA = ["躑躅", "薔薇", "顳顬", "髑髏", "彁", "妛", "椦", "𡈽",
         "ＰＩＮ", "𠮟責", "𩸽", "鱲子"]

print(f"{'語':10s} {'未知語?':>8} {'音素':40s} {'消えたか'}")
print("-" * 78)


def probe(w):
    t = w + "が問題です。"
    _, ms = pyopenjtalk.run_mecab_detailed(t)
    unk = [m["surface"] for m in ms if m["is_unknown"]]
    ph = pyopenjtalk.g2p(t)
    base = pyopenjtalk.g2p("が問題です。")
    # 語が音素に何も足していない = 無音で消えた
    vanished = ph.strip() == base.strip()
    return unk, ph, vanished


for group, name in ((CLAUDE_MD, "CLAUDE.md の例"), (B0_JSON, "b0_alternatives.json の例"),
                    (EXTRA, "追加プローブ（外字・幽霊漢字・全角）")):
    print(f"\n--- {name} ---")
    for w in group:
        try:
            unk, ph, van = probe(w)
        except Exception as e:
            print(f"{w:10s} 例外: {e}")
            continue
        print(f"{w:10s} {str(bool(unk)):>8} {ph[:40]:40s} "
              f"{'**消えた**' if van else 'のこった'}")

print("\n=== 陰性対照: 確実に存在する語は『のこった』になるか ===")
for w in ["電源", "天気", "音声"]:
    unk, ph, van = probe(w)
    print(f"  {w}: 消えた={van} (False であるべき)")

print("\n=== 陽性対照: 辞書に無いと分かっている綴りを作る ===")
for w in ["ズガピョコリン", "ヷヸヹ"]:
    unk, ph, van = probe(w)
    print(f"  {w}: 未知語={unk} 消えた={van} 音素={ph[:50]}")
