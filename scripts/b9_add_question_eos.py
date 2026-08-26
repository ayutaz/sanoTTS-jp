#!/usr/bin/env python3
"""疑問 EOS 4 種 (`?` `?!` `?.` `?~`) を含む行をコーパスに足す（D-016）。

デプロイ語彙 57 のうち `?!` `?.` `?~` は**コーパス 23,454 行に 1 行も出ない**ので、
このまま学習すると埋め込み 3 行が未学習のまま残る。ホスト G2P は原文の約物から
これらを実際に出すので（`？！` → `?!`）、**到達可能なのに学習データが無い**状態。

**テンプレート文は使わない**（CLAUDE.md）。文型・語彙・長さを散らした実文を書く。
併せて日本語固有の多様性軸（数詞・助数詞・日付・カタカナ語・英数字混在）も混ぜる。

実行:
    uv run python scripts/b9_add_question_eos.py --dry-run
    uv run python scripts/b9_add_question_eos.py --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")
import kana_g2p as K  # noqa: E402
import gen_teacher_labels as G  # noqa: E402

SOURCE = "curated/question_eos"

# (text, 期待する EOS トークン)。**同じ骨格を使い回さない。**
SENTENCES: list[tuple[str, str]] = [
    # --- `?` 素の疑問 ---
    ("明日の会議は何時からでしたか？", "?"),
    ("この設定を初期化してもよろしいですか？", "?"),
    ("駅までは歩いて何分くらいかかりますか？", "?"),
    ("領収書の宛名はどういたしましょうか？", "?"),
    ("二〇二六年八月二十七日で間違いないですか？", "?"),
    ("バッテリーはあと何パーセント残っていますか？", "?"),
    ("その池では魚が何匹釣れましたか？", "?"),
    ("紅茶と珈琲、どちらになさいますか？", "?"),
    ("この漢字の読み方が分かる方はいらっしゃいますか？", "?"),
    ("三時のおやつ、まだ残っているかな？", "?"),
    ("君はそれを本当に自分で確かめたのか？", "?"),
    ("電源ケーブルは奥まで差し込みましたか？", "?"),
    ("十二月三十一日の営業時間を教えてもらえますか？", "?"),
    ("そちらの在庫は一個だけですか？", "?"),
    ("Wi-Fiのパスワードは変更済みでしょうか？", "?"),
    # --- `?!` 驚き ---
    ("えっ、それ全部ひとりでやったんですか？！", "?!"),
    ("嘘でしょう、もう売り切れたんですか？！", "?!"),
    ("あの荷物、まだ届いていないんですか？！", "?!"),
    ("三万円もしたの？！", "?!"),
    ("今からそこへ向かえというんですか？！", "?!"),
    ("君が犯人だったのか？！", "?!"),
    ("この雨の中を歩いて帰ったんですか？！", "?!"),
    ("百人分の弁当を明日までに用意しろと？！", "?!"),
    ("まさか鍵をかけ忘れたんじゃないでしょうね？！", "?!"),
    ("そんな昔の話を覚えているんですか？！", "?!"),
    ("十七年ぶりの再会だって？！", "?!"),
    ("それ、私の傘じゃないですか？！", "?!"),
    # --- `?.` 疑問だが下降調 ---
    ("さて、そろそろ本題に入りましょうか？。", "?."),
    ("これで全部そろったということでよろしいですね？。", "?."),
    ("では、明日の九時に集合ということで？。", "?."),
    ("なるほど、そういう事情でしたか？。", "?."),
    ("そうですか、それは残念でしたね？。", "?."),
    ("結局、誰も来なかったというわけですか？。", "?."),
    ("念のため、もう一度だけ確認させてください？。", "?."),
    ("この件はいったん保留ということで？。", "?."),
    ("まあ、そんなものでしょうか？。", "?."),
    ("五分ほどお時間よろしいでしょうか？。", "?."),
    # --- `?~` 語尾を伸ばす疑問 ---
    ("ねえ、今日の晩ご飯なににする？〜", "?~"),
    ("その話、前にも聞いた気がするんだけど？〜", "?~"),
    ("もしかして、また忘れてない？〜", "?~"),
    ("これ、けっこう美味しいと思わない？〜", "?~"),
    ("明日って確か祝日だったよね？〜", "?~"),
    ("そんなに慌てなくてもいいんじゃない？〜", "?~"),
    ("ちょっとだけ味見してもいい？〜", "?~"),
    ("二人分だと足りないかもしれないよ？〜", "?~"),
    ("さっきの店、名前なんだっけ？〜", "?~"),
    ("ほんとにそれでいいの？〜", "?~"),
]

# うち held-out に回す割合（各 EOS 種から 2 文ずつ）
HELDOUT_PER_KIND = 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default="reports/b9_question_eos.json")
    args = ap.parse_args()

    table = K.build_mora_table()
    G.ENCODE_TABLE = table
    snap = G.snapshot()
    pim = json.load(open(snap + "config.json"))["phoneme_id_map"]

    ok, bad = [], []
    for text, want in SENTENCES:
        try:
            toks = K.text_to_intermediate(text, table)
            ids = G.encode_intermediate(toks, pim)
        except KeyError as exc:
            bad.append({"text": text, "want": want, "why": f"KeyError {exc}"})
            continue
        got = toks[-1] if toks else ""
        if got != want:
            bad.append({"text": text, "want": want, "got": got,
                        "why": "期待した EOS トークンにならない"})
            continue
        ok.append({"text": text, "eos": want, "n_ids": len(ids),
                   "tokens_tail": toks[-4:]})

    from collections import Counter
    dist = Counter(r["eos"] for r in ok)
    print(f"検証 {len(ok)}/{len(SENTENCES)} 文が期待どおり")
    for k in ("?", "?!", "?.", "?~"):
        print(f"  {k:<3} {dist.get(k, 0)} 文")
    for b in bad:
        print(f"  ❌ {b['text']!r} → {b}")
    if bad:
        raise SystemExit("期待した EOS にならない文がある。文を直すこと")

    # split への割り振り。**同じ EOS が held-out に偏らないよう種別ごとに切る**
    per_kind: dict[str, list[dict]] = {}
    for r in ok:
        per_kind.setdefault(r["eos"], []).append(r)
    heldout, train = [], []
    for kind, rows in per_kind.items():
        heldout += rows[:HELDOUT_PER_KIND]
        train += rows[HELDOUT_PER_KIND:]

    rep = {
        "task": "疑問 EOS 4 種をコーパスに追加（D-016）",
        "n_sentences": len(ok), "by_eos": dict(dist),
        "to_train": len(train), "to_heldout": len(heldout),
        "source_tag": SOURCE,
        "repro": "uv run python scripts/b9_add_question_eos.py --apply",
        "note": ("`?~` は U+301C WAVE DASH でも通るように "
                 "kana_g2p.normalize_input() を足してある（U+FF5E に寄せる）"),
        "applied": bool(args.apply),
    }
    json.dump(rep, open(args.report, "w"), ensure_ascii=False, indent=2)

    if not args.apply:
        print(f"\n--apply なしなので書き込まない → train {len(train)} / "
              f"heldout {len(heldout)}")
        return 0

    for split, rows in (("train", train), ("heldout", heldout)):
        path = f"data/splits/corpus_{split}.tsv"
        existing = {r[1] for r in csv.reader(open(path), delimiter="\t")
                    if len(r) >= 3}
        added = 0
        with open(path, "a") as f:
            w = csv.writer(f, delimiter="\t")
            for i, r in enumerate(rows):
                uid = f"QEOS_{r['eos'].replace('?', 'Q').replace('!', 'E')}" \
                      f"{'D' if '.' in r['eos'] else ''}" \
                      f"{'T' if '~' in r['eos'] else ''}_{i:03d}"
                if uid in existing:
                    continue
                w.writerow([SOURCE, uid, r["text"]])
                added += 1
        print(f"{split}: {added} 行を追加 → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
