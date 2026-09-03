"""K-B: 行の**経路判定**がホストと端末で一致するかを測る。

    make -C csrc kb-parity
    # または
    uv run python scripts/k1/kb_route_parity.py

端末は 1 本のプロンプトで「かな中間表現」と「漢字かな交じり文」の両方を受ける
（`!` は要らない）。どちらに回すかを決めるのは

  - 端末: `csrc/g2p.c` の `saan_g2p_classify()`
  - ホスト: `scripts/kana_g2p.py` の `classify_route()`

の 2 つで、**片方だけ直すとユーザーには「ホストで OK と言われた行が端末で
拒否される」形で出る**。ここはその 2 つを同じ入力に当てて突き合わせる。

⚠️ **端末側は写しではなく本物を動かす。** `csrc/route_tool` は
`saan_g2p_classify()` をそのままホストで実行する棒で、判定を Python に
書き写した第 3 の実装は作らない（写し間違いごと「一致」してしまう）。

## 測るもの

| 集合 | n | 期待 |
|---|---:|---|
| held-out の**漢字かな交じり文** | 298 | ほぼ `dict`（純かなの行だけ `kana`） |
| その**中間表現** | 298 | 全部 `kana` |
| 中間表現 + `。`（**構成した拒否ケース**） | 298 | 全部 `reject` |

前の 2 つで **596/596** が本ゲート。3 つめは「3 値のうち `reject` が
1 度も出ない集合で一致 100% を出す」空虚さを潰すために足してある
（自然な held-out 文には `reject` が出ない）。

## 陰性対照

比較器そのものが働いているかを、**ホスト側の判定を 1 件わざと反転**して確かめる。
これが「不一致 1 件」と報告されなければ、596/596 は比較していない証拠。

## ⚠️ ここで見ていないもの

- **不正な UTF-8**。Python の `str` は復号済みなので対にできない
  （端末は `reject` に倒す。`make -C csrc line` の G11 が見ている）。
- **辞書経路に回った先の読み**。ここは「どちらの経路に行くか」だけ。
  読みの一致は `make -C csrc k6` / `k7`。
  ⚠️ 辞書経路の参照値は「`jt.run_frontend(text)`（**生の NJD**）+ 端末に載っている
  4 段」であって `run_frontend(..., predict_nani=False, use_sudachi_kanji_yomi=False)`
  ではない。後者は `process_odori_features` が**無条件に**入るので
  「端末に載る段だけ」と両立しない（`scripts/k1/g2p_ablate.py` の `STAGES` が正）。
- **`text2mecab`**。端末は vendored 済みだが**呼んでいない**、ホストの
  pyopenjtalk は呼ぶ。ASCII・半角を含む文で食い違いうるが、それは
  「経路」ではなく「読み」の話（K-6 の G17 は素性が一致した文しか見ないので未検出）。
"""
from __future__ import annotations

import os as _os
import subprocess
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import HELDOUT, ROOT  # noqa: E402

_sys.path.insert(0, _os.path.join(ROOT, "scripts"))
import kana_g2p as K  # noqa: E402

N_CASES = 298
"""M-77 / D-044 と同じ n。**偶数間隔で拾った held-out 文**から、
中間表現にできたものを先頭から 298 件。⚠️ 表現可能率は 96.40% なので
候補は多めに引く（`N_CANDIDATES`）。"""

N_CANDIDATES = 400
ROUTE_TOOL = _os.path.join(ROOT, "csrc", "route_tool")
DEVOICED = K.DEVOICED_MARK


def load_texts() -> list[str]:
    texts: list[str] = []
    with open(HELDOUT, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        # C-018: ヘッダ行を発話として数えない
        assert header[:3] == ["source", "id", "text"], header
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[2].strip():
                texts.append(p[2])
    return texts


def device_routes(lines: list[str]) -> list[tuple[str, int]]:
    """`csrc/route_tool`（= 端末の `saan_g2p_classify`）に流す。"""
    if not _os.path.exists(ROUTE_TOOL):
        raise SystemExit(f"NG  {ROUTE_TOOL} が無い。先に `make -C csrc route_tool`")
    payload = bytearray()
    for s in lines:
        b = s.encode("utf-8")
        payload += f"{len(b)}\n".encode("ascii") + b
    out = subprocess.run([ROUTE_TOOL], input=bytes(payload),
                         stdout=subprocess.PIPE, check=True).stdout.decode("ascii")
    rows = [r for r in out.split("\n") if r]
    if len(rows) != len(lines):
        raise SystemExit(f"NG  route_tool が {len(rows)} 行しか返さない（{len(lines)} 行必要）")
    res = []
    for r in rows:
        name, eb = r.split(" ")
        res.append((name, int(eb)))
    return res


def compare(name: str, lines: list[str], host: list[tuple[str, int]],
            dev: list[tuple[str, int]]) -> int:
    """不一致件数を返す。⚠️ **経路だけでなく err_byte も比べる**
    （位置が違えば「どの文字を消せばよいか」の案内が食い違う）。"""
    bad = 0
    for i, (ln, h, d) in enumerate(zip(lines, host, dev)):
        if h != d:
            bad += 1
            if bad <= 5:
                print(f"    不一致 [{name} #{i}] {ln[:40]!r}")
                print(f"      ホスト {h}  /  端末 {d}")
    print(f"  {'OK ' if bad == 0 else 'NG!'} {name}: {len(lines) - bad} / {len(lines)} 一致")
    return bad


def measure_cost(all_texts: list[str], table) -> dict:
    """**規則の代償を測る。** 「拒否」は打ち間違いを捕まえる代わりに、
    ごく普通の文も落としうる。落ちる量と中身を数字で出しておかないと
    「安全側に倒した」で済ませてしまう。

    ⚠️ **末尾の半角 `?` だけがマークの行**を別に数える。`?` は中間表現では
    疑問 EOS だが、日本語の平文でも普通に使われる。端末の漢字経路は
    `csrc/label_ids.c` の `question_type()` が半角 `?` を疑問 EOS として
    扱うので、**辞書経路に回せば読める行**が拒否されていることになる。
    """
    n = {"kana": 0, "dict": 0, "reject": 0}
    q_only = 0
    for t in all_texts:
        r, _ = K.classify_route(t, table)
        n[r] += 1
        if (r == "reject"
                and {c for c in t if c in K.MARK_CHARS} == {"?"}
                and t.rstrip().endswith("?")):
            q_only += 1
    return {"n": len(all_texts), "kana": n["kana"], "dict": n["dict"],
            "reject": n["reject"], "reject_q_only": q_only}


def main() -> int:
    table, src = K.mora_table(prefer_frozen=True)
    print(f"mora テーブル: {src} / {len(table)} 件")

    all_texts = load_texts()
    print(f"held-out 全 {len(all_texts)} 行")
    cand = []
    seen = set()
    for i in range(N_CANDIDATES):
        t = all_texts[int(i * len(all_texts) / N_CANDIDATES)]
        if t not in seen:
            seen.add(t)
            cand.append(t)

    kanji: list[str] = []
    inter: list[str] = []
    n_fail = 0
    for t in cand:
        if len(kanji) >= N_CASES:
            break
        try:
            m = "".join(K.text_to_intermediate(t, table))
        except KeyError:
            n_fail += 1          # 表現可能率 96.40%（held-out）。落ちる文は普通にある
            continue
        kanji.append(t)
        inter.append(m)
    if len(kanji) != N_CASES:
        raise SystemExit(f"NG  ケースが {len(kanji)} 件しか作れなかった（{N_CASES} 件必要）。"
                         f"N_CANDIDATES を増やすこと")
    print(f"ケース {len(kanji)} 文（候補 {len(cand)} / 中間表現にできなかった {n_fail}）")

    # 構成した拒否ケース（自然な held-out には reject が出ないので空虚さ対策）
    reject = [m + "。" for m in inter]
    # ⚠️ **`°` だけがマークの行**も要る。`[ ] #` を含む行しか見ないと、マーク集合から
    #    `°` を落とす壊し方（片側だけ）を検出できない — 実際に一度取り逃がした。
    #    アクセント記号を全部消して `。` を足すと「かな + `°` + 読めない文字」になる。
    dev_only = [
        "".join(c for c in m if c == DEVOICED or c not in K.MARK_CHARS) + "。"
        for m in inter if DEVOICED in m
    ]
    if len(dev_only) < 20:
        raise SystemExit(f"NG  `°` だけがマークの行が {len(dev_only)} 件しか作れない")

    host_k = [K.classify_route(t, table) for t in kanji]
    host_i = [K.classify_route(t, table) for t in inter]
    host_r = [K.classify_route(t, table) for t in reject]
    host_d = [K.classify_route(t, table) for t in dev_only]
    dev_k = device_routes(kanji)
    dev_i = device_routes(inter)
    dev_r = device_routes(reject)
    dev_d = device_routes(dev_only)

    print("\n-- 経路の分布（ホスト側）--")
    for label, rs in (("漢字かな交じり文", host_k), ("中間表現", host_i),
                      ("中間表現 + 。", host_r),
                      (f"かな + ° + 。（n={len(dev_only)}）", host_d)):
        cnt = {}
        for r, _ in rs:
            cnt[r] = cnt.get(r, 0) + 1
        print(f"  {label:<20} {cnt}")

    print("\n-- ホスト判定 vs 端末判定 --")
    bad = 0
    bad += compare("漢字かな交じり文", kanji, host_k, dev_k)
    bad += compare("中間表現", inter, host_i, dev_i)
    total = len(kanji) + len(inter)
    print(f"  {'OK ' if bad == 0 else 'NG!'} **本ゲート: {total - bad} / {total}**")
    bad_r = compare("中間表現 + 。（構成した拒否）", reject, host_r, dev_r)
    bad_r += compare("かな + ° + 。（マークが `°` だけ）", dev_only, host_d, dev_d)

    # --- 空虚さの検査 ------------------------------------------------------
    print("\n-- ゲートが空虚でないこと --")
    routes = {r for r, _ in host_k + host_i + host_r + host_d}
    print(f"  {'OK ' if routes == {'kana', 'dict', 'reject'} else 'NG!'} "
          f"3 値すべてが出る: {sorted(routes)}")
    ok_cover = routes == {"kana", "dict", "reject"}
    n_dict = sum(1 for r, _ in host_k if r == "dict")
    cnt_k: dict[str, int] = {}
    for r, _ in host_k:
        cnt_k[r] = cnt_k.get(r, 0) + 1
    print(f"  {'OK ' if n_dict > 0 else 'NG!'} 漢字文のうち {n_dict} 件が辞書経路"
          f"（内訳 {cnt_k}）")
    n_kana_i = sum(1 for r, _ in host_i if r == "kana")
    print(f"  {'OK ' if n_kana_i == len(host_i) else 'NG!'} "
          f"中間表現は {n_kana_i} / {len(host_i)} 件がかな経路")
    n_rej = sum(1 for r, _ in host_r if r == "reject")
    print(f"  {'OK ' if n_rej == len(host_r) else 'NG!'} "
          f"中間表現 + `。` は {n_rej} / {len(host_r)} 件が拒否"
          f"（**黙って辞書経路に回っていない**）")
    n_rej_d = sum(1 for r, _ in host_d if r == "reject")
    print(f"  {'OK ' if n_rej_d == len(host_d) else 'NG!'} "
          f"かな + `°` + `。` は {n_rej_d} / {len(host_d)} 件が拒否"
          f"（**マークが `°` だけの行でも拒否になる**）")

    # 陰性対照: ホストの判定を 1 件だけ反転して、比較器が気づくか
    print("\n-- 陰性対照（比較器が本当に比べているか）--")
    tampered = list(host_i)
    tampered[0] = ("dict", 0) if tampered[0][0] != "dict" else ("kana", -1)
    n_bad_ctrl = sum(1 for h, d in zip(tampered, dev_i) if h != d)
    print(f"  {'OK ' if n_bad_ctrl == 1 else 'NG!'} "
          f"ホスト側を 1 件反転すると不一致 {n_bad_ctrl} 件（1 件のはず）")

    print("\n-- 規則の代償（**ゲートではない。測定**）--")
    cost = measure_cost(all_texts, table)
    print(f"  held-out {cost['n']} 行の経路: かな {cost['kana']} / 辞書 {cost['dict']} "
          f"/ 拒否 {cost['reject']}（{100 * cost['reject'] / cost['n']:.2f}%）")
    print(f"  うち **マークが末尾の半角 `?` だけ** の行: {cost['reject_q_only']}")

    ok = (bad == 0 and bad_r == 0 and ok_cover and n_dict > 0
          and n_kana_i == len(host_i) and n_rej == len(host_r)
          and n_rej_d == len(host_d) and n_bad_ctrl == 1)
    print("\n" + ("OK  ホストと端末の経路判定は一致する" if ok else "NG! 一致しない"))
    if cost["reject_q_only"]:
        print(f"⚠️ **規則の代償が測れた**: held-out の {cost['reject']} 行が拒否になり、"
              f"うち {cost['reject_q_only']} 行は末尾が半角 `?` のごく普通の疑問文。"
              f"端末の漢字経路は `csrc/label_ids.c` の question_type() が"
              f"半角 `?` を疑問 EOS として扱うので、**辞書経路に回せば読める**。"
              f"`?` をマーク集合から外すかは**判断が要る**（外すと「かな + `?` の"
              f"中間表現の打ち間違い」が辞書経路に回る）。")
    print("⚠️ 見ていないもの: 不正な UTF-8（str にできない）/ 辞書経路に回った先の読み"
          "（`make -C csrc k6` / `k7`）/ text2mecab の有無")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
