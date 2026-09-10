"""`rec5`（5 B レコード。M-107 §4a の設計）のホスト側ゲート。

    uv run python scripts/test_rec5.py

現行の `records` は **9 B 固定**（class u16 / wcost i16 / chain u16 / flags u8 /
pron長 u8 / extra長 u8）。`rec5` は **(class, chain, flags) を 12 bit の class2 に畳み**、
**5 B ちょうど**にする:

    b0..b1  wcost i16
    b2..b3  u16 = class2(bit 0-11) | pron長 下位 4bit(bit 12-15)
    b4      pron長 bit4(bit 0) | extra長(bit 1-6) | 予備(bit 7)
    classes = 10 B: lc u16 / rc u16 / pos6 u16 / posid u16 / chain u8 / flags u8

⚠️ **wcost は 1 bit も削れない**（実データで min −32,750 / max 21,463 = 16 bit 丸ごと）。
   削れるのは class/chain/flags の畳み込みだけ（フル辞書 789,388 entries でも 2,251 種）。

⚠️ **陽性対照は「全動作点で発火するもの」を選ぶ**（M-107 §4a）。
   class2 の幅を狭める対照は **class2 > 2,048 のときしか発火せず**、
   8 MB(1,897) / 4 MB(1,669) / 2 MB(1,348) では checksum が 1 bit も変わらない。
   **pron長 / extra長 の幅**なら実データの最大が 30 / 61 なので**どの動作点でも発火する**。
"""
from __future__ import annotations

import pathlib
import random
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from saanotts_jp.jdict import DictBlob, Entry  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK ' if cond else 'NG '} {name}" + (f"    {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def synth_entries(n: int, seed: int = 0) -> list[Entry]:
    """合成エントリ。**実データの端を踏む**ように作る。

    ⚠️ pron長 は最大 30 / extra長 は最大 61 が実データの上限（M-107 §4a）。
       境界の 1 つ内側と外側の両方を踏ませないと、幅の検査が空虚になる。
    """
    rng = random.Random(seed)
    kana = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ"
    out: list[Entry] = []
    for i in range(n):
        # pron を 1..30 モーラに振る（30 は 5 bit の上限 31 の 1 つ内側）
        npron = 1 + (i % 30)
        pron = "".join(rng.choice(kana) for _ in range(npron))
        # read を pron と変えると extra が伸びる（extra長 の端を踏ませる）
        read = pron if i % 17 == 0 else "".join(rng.choice(kana) for _ in range(npron))
        acc = "/".join([str(i % 8), str(npron)])
        out.append(Entry(
            surface=f"語{i:05d}",
            lc=i % 1200, rc=(i * 7) % 1200,
            # ⚠️ wcost の端を必ず踏む（実データは min −32,750 / max 21,463）
            wcost=(-32750 if i % 101 == 0 else (21463 if i % 103 == 0 else (i % 4000) - 2000)),
            pos6=("名詞", "一般", "*", "*", "*", "*") if i % 2 else
                 ("動詞", "自立", "*", "*", "五段・ラ行", "基本形"),
            posid=0,
            orig=f"語{i:05d}" if i % 7 else f"原{i:05d}",       # ⚠️ 7 は 1200 と互いに素
            read=read, pron=pron, acc=acc,
            chain=("*" if i % 11 else f"C{i % 13}"),           # ⚠️ 11 / 13 も互いに素
        ))
    return out


def build(entries: list[Entry], *, rec5: bool) -> DictBlob:
    b = DictBlob.build(entries, matrix=None, char_prop=None, unk=None)
    b.rec5 = rec5
    return b


def main() -> int:
    print("=== rec5（5 B レコード）のホスト側ゲート ===\n")
    entries = synth_entries(3000)

    # --- G-P1: 5 B/entry になっているか -----------------------------------
    print("=== G-P1: レコードが 5 B/entry になる ===")
    b9 = build(entries, rec5=False)
    b5 = build(entries, rec5=True)
    raw9, raw5 = b9.to_bytes(), b5.to_bytes()
    s9, s5 = DictBlob.sections(raw9), DictBlob.sections(raw5)
    check("9 B 版は `records` を持つ", "records" in s9 and "rec5" not in s9,
          f"{sorted(k for k in s9 if k.startswith('rec'))}")
    check("5 B 版は `rec5` を持つ", "rec5" in s5 and "records" not in s5,
          f"{sorted(k for k in s5 if k.startswith('rec'))}")
    if "records" in s9 and "rec5" in s5:
        n = len(entries)
        check("records は 9 B/entry", s9["records"][1] == 9 * n,
              f"{s9['records'][1]:,d} B / {n:,d} = {s9['records'][1] / n:.2f}")
        check("rec5 は 5 B/entry", s5["rec5"][1] == 5 * n,
              f"{s5['rec5'][1]:,d} B / {n:,d} = {s5['rec5'][1] / n:.2f}")

    # ⚠️ **前提が崩れたら先へ進まない。** `rec5` が無いまま G-P2 以降を走らせると
    #    **9 B 版を測って自明に通る**（matrixa_test の G-A2 と同じ理由。M-104）。
    if "rec5" not in s5:
        print("\n⚠️ **`rec5` セクションが無いので G-P2 以降は走らせない**"
              "（走らせても 9 B 版を測るだけで意味が無い）")
        print("\nNG! " + " / ".join(FAILED))
        return 1

    # --- G-P2: 全 11 フィールドが往復するか -------------------------------
    print("\n=== G-P2: 全エントリの 11 フィールドが往復する ===")
    try:
        back = DictBlob.from_bytes(raw5)
        got = back.all_entries()
    except Exception as e:                                   # noqa: BLE001
        check("from_bytes + all_entries", False, f"{type(e).__name__}: {e}")
        got = []
    if got:
        bad = [i for i, (a, b) in enumerate(zip(entries, got)) if a != b]
        check("11 フィールドが全エントリで一致", len(got) == len(entries) and not bad,
              f"{len(got):,d} 件 / 食い違い {len(bad)}"
              + (f" 例: {entries[bad[0]]} vs {got[bad[0]]}" if bad else ""))

    # --- G-P3: class2 が 12 bit に収まるか ---------------------------------
    # ⚠️ **`b5.classes` を数えてはいけない** — それはメモリ上の旧 classes（4 つ組）で、
    #    class2（6 つ組）ではない。**書き出した blob のセクションから数える。**
    print("\n=== G-P3: class2 が 12 bit（4,096）に収まる ===")
    o, _ = s5["classes"]
    n_c2 = struct.unpack("<I", raw5[o:o + 4])[0]
    n_old = len(b9.classes)
    # ⚠️ **class2 == classes なら、合成データが畳み込みを 1 度も踏んでいない**
    #    （chain / flags が cid から一意に決まってしまっている）。
    #    その状態では G-P2 が通っても「畳み込みが正しい」証拠にならない。
    check("合成データが畳み込みを踏んでいる（class2 > 旧 classes）", n_c2 > n_old,
          f"class2 {n_c2:,d} 種 / 旧 classes {n_old:,d} 種")
    check("class2 の種類が 4,096 未満", 0 < n_c2 < 4096, f"{n_c2:,d} 種")
    check("classes セクションが 10 B/エントリ",
          s5["classes"][1] >= 4 + 10 * n_c2,
          f"{s5['classes'][1]:,d} B（4 + 10 × {n_c2:,d} = {4 + 10 * n_c2:,d} + pos6 表）")

    # --- G-P4: 陽性対照（全動作点で発火するもの）--------------------------
    # ⚠️ **class2 の幅ではなく pron長 / extra長 の幅を狭める。**
    #    class2 は動作点によって 1,348〜2,097 と幅があり、11 bit にしても
    #    2,048 を超えない動作点では 1 bit も変わらない（M-107 §4a の反証）。
    print("\n=== G-P4: 陽性対照 — 幅を 1 bit 狭めると必ず壊れる ===")
    plens = [len(b5.moras.encode(e.pron)) for e in entries]
    elens = _extra_lens(raw5, len(entries))
    check("pron長 の最大が 4 bit（15）を超える", max(plens) > 15, f"max {max(plens)}")
    check("extra長 の最大が 5 bit（31）を超える", max(elens) > 31, f"max {max(elens)}")

    print("\n" + ("NG! " + " / ".join(FAILED) if FAILED else "OK  すべて通過"))
    return 1 if FAILED else 0


def _extra_lens(raw: bytes, n: int) -> list[int]:
    """rec5 の extra長 を生バイトから読む（リーダを経由しない独立な確認）。"""
    secs = DictBlob.sections(raw)
    if "rec5" not in secs:
        return [0]
    o, _ = secs["rec5"]
    out = []
    for i in range(n):
        b4 = raw[o + 5 * i + 4]
        out.append((b4 >> 1) & 0x3F)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
