#!/usr/bin/env python3
"""漢字かな交じり文 → 端末に貼り付ける「かな中間表現」。

    uv run python scripts/to_intermediate.py "今日は良い天気ですね。"
    uv run python scripts/to_intermediate.py --ids "こんにちは"
    echo "電源を入れます。" | uv run python scripts/to_intermediate.py

**ホスト側専用**（OpenJTalk が要る）。端末にはこの出力の 1 行だけを渡す
（D-010 / D-011。端末側は mora テーブル 951 B + `ん` の異音規則 18 件だけを持つ）。

⚠️ **出した行が本当に元の文と同じ音になるかを、毎回その場で検査する。**
   中間表現は「かな + アクセント記号」なので**人が読めてしまい、間違っていても
   それらしく見える**。往復（中間表現 → 音素）が phonemizer の出力と一致しなければ
   NG を出して終了コード 1 で終わる。⚠️ held-out での表現可能率は 96.40% なので、
   **落ちる文は普通にある**（そのときは言い換えるか、かなで直接書く）。

⚠️ **未知語は誤読ではなく無音で消える**（B-0）。`齟齬` のような語は
   OpenJTalk が読点に置換するので、**警告も例外も出ずに語ごと消える**。
   出力のかなを目で読んで、元の文の音がそろっているか確認すること。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import kana_g2p as K  # noqa: E402
from gen_g2p_vectors import encode  # noqa: E402

# 端末が受け付ける ids の上限（esp32/main/main.c の SAAN_MAX_IDS と同じ値）。
# ⚠️ **2 か所にある定数は必ずずれる。** 食い違ったら main.c の方が正。
DEVICE_MAX_IDS = 350

SR, HOP = 22050, 256


def convert(text: str, table, phonemizer) -> tuple[str, dict, list[str]]:
    """戻り値: (中間表現, encode() の結果, 警告のリスト)。往復不一致は警告に入る。"""
    warnings: list[str] = []
    intermediate = "".join(K.text_to_intermediate(text, table))

    # --- 往復の検査（**これが主ゲート**）-----------------------------------
    reference = phonemizer.phonemize(K.normalize_input(text))
    restored = K.intermediate_to_phonemes(
        K.intermediate_to_tokens(intermediate, table), table)
    if restored != reference:
        warnings.append(
            f"往復が一致しない（**この行は元の文と同じ音にならない**）\n"
            f"      phonemizer : {' '.join(reference)}\n"
            f"      中間表現から: {' '.join(restored)}")

    info = encode(intermediate, table)
    if info["kind"] != 0:
        warnings.append(f"端末側 G2P が {info['err_byte']} バイト目で受け付けない")
    if info["n_drop_long"] or info["n_drop_devoice"]:
        warnings.append(
            f"黙って落ちる記号がある: ー {info['n_drop_long']} 個 / "
            f"° {info['n_drop_devoice']} 個（直前が平母音でないと効かない）")
    if len(info["ids"]) > DEVICE_MAX_IDS:
        warnings.append(
            f"{len(info['ids'])} ids は端末の上限 {DEVICE_MAX_IDS} を超える。"
            f"**端末は受け付けない** — 短く区切ること")
    return intermediate, info, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*", help="変換する文（省略すると標準入力を 1 行ずつ）")
    ap.add_argument("--ids", action="store_true",
                    help="生徒インデックス列も出す（端末のログと突き合わせる用）")
    args = ap.parse_args()

    from piper_plus_g2p.japanese import JapanesePhonemizer

    table = K.build_mora_table()
    phonemizer = JapanesePhonemizer()

    texts = args.text or [ln.rstrip("\n") for ln in sys.stdin if ln.strip()]
    if not texts:
        ap.error("変換する文が無い")

    bad = 0
    for text in texts:
        try:
            intermediate, info, warnings = convert(text, table, phonemizer)
        except KeyError as exc:
            # ⚠️ 表現可能率は held-out で 96.40%。**落ちる文は普通にある。**
            print(f"NG  {text}", file=sys.stderr)
            print(f"    中間表現にできない: {exc}", file=sys.stderr)
            print("    → 言い換えるか、読みを直接ひらがなで書くこと", file=sys.stderr)
            bad += 1
            continue

        n_ids = len(info["ids"])
        print(intermediate)
        print(f"    # {text}", file=sys.stderr)
        print(f"    # {len(intermediate.encode('utf-8'))} B / {n_ids} ids "
              f"/ 音素 {info['n_phonemes']} 個", file=sys.stderr)
        if args.ids:
            print(f"    # ids: {' '.join(str(i) for i in info['ids'])}", file=sys.stderr)
        for w in warnings:
            print(f"    ⚠️ {w}", file=sys.stderr)
            bad += 1

    if bad:
        print(f"\n{bad} 件の警告 / エラー。**そのまま端末に貼らないこと。**", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
