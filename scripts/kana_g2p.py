#!/usr/bin/env python3
"""かな中間表現 ⇄ 音素列の変換器。

本プロジェクトの入力仕様は **ひらがな + アクセント記号 + 無声化マーク**。
漢字かな交じり文を直接扱わないことで、ESP32 に載らない 40 MB 級の辞書を
約 1 KB の mora テーブルに置き換える（`docs/decisions.md` D-010 / D-011）。

    漢字かな交じり文  ──[ホスト側・OpenJTalk]──▶  中間表現  ──[端末側・規則のみ]──▶  音素ID列
    今日は良い天気ですね。                      きょ][おわよ][いて][んきです°ね      [1, 33, 0, ...]

本モジュールは 2 つの経路を実装する:

* `text_to_intermediate()` — **ホスト側**。OpenJTalk を使う。オフラインで一度だけ実行
* `intermediate_to_phonemes()` — **端末側**。mora テーブルと ん の異音規則だけ。
  ここが C99 に移植される部分で、必要なデータは 954 B + 18 規則

実行するとセルフテストが走る:

    /Users/s19447/Documents/piper-plus/.venv/bin/python scripts/kana_g2p.py
"""

from __future__ import annotations

import sys

PIPER_PLUS = "/Users/s19447/Documents/piper-plus"
sys.path.insert(0, f"{PIPER_PLUS}/src/python")
sys.path.insert(0, f"{PIPER_PLUS}/src/python/g2p")

# --- 中間表現の記号 ---------------------------------------------------------

ACCENT_MARKS = frozenset({"[", "]", "#"})
"""`[` 上昇 / `]` 下降核 / `#` アクセント句境界。教師の音素ID 8 / 9 / 7 に直結する。"""

BOUNDARY_MARKS = frozenset({"_", "^", "$", "?", "?!", "?.", "?~"})
"""ポーズ・BOS・EOS。EOS は疑問の種類で 4 種ある。"""

MARKS = ACCENT_MARKS | BOUNDARY_MARKS

DEVOICED_MARK = "°"
"""無声化マーク。`き°` → `k I`。

規則で推定してはいけない。無声化は品詞とアクセント型に依存するため、
「無声子音に挟まれた i/u」という規則は実測で 170 箇所を過剰に無声化した
(`docs/measurements.md` M-14)。ホスト側で確定させて入力に持たせる。
"""

DEVOICE = {"I": "i", "U": "u", "A": "a", "E": "e", "O": "o"}
REVOICE = {v: k for k, v in DEVOICE.items()}

# --- 端末側に載るデータ -----------------------------------------------------

N_ALLOPHONE = {
    # 両唇音の前 → N_m
    "m": "N_m", "b": "N_m", "p": "N_m", "my": "N_m", "by": "N_m", "py": "N_m",
    # 歯茎音の前 → N_n
    "n": "N_n", "t": "N_n", "d": "N_n", "ts": "N_n", "ch": "N_n",
    "ny": "N_n", "ty": "N_n", "dy": "N_n",
    # 軟口蓋音の前 → N_ng
    "k": "N_ng", "g": "N_ng", "ky": "N_ng", "gy": "N_ng",
}
"""`ん` の異音を後続子音から決める規則。該当しなければ `N_uvular`（語末・母音前）。"""

VOWELS = "aiueo"

PARTICLE_KANA = {"は": ["h", "a"], "へ": ["h", "e"], "を": ["o"]}
"""助詞として読まれうる仮名。どんなキャリアに入れても文脈次第で `wa`/`e` になるため、
表音としての値をここで固定する。中間表現は表音なので、助詞の「は」は `わ` と書かれる。"""


def build_mora_table() -> dict[str, list[str]]:
    """かな → 音素列のテーブルを piper-plus の phonemizer から導出する。

    ハードコードせず実測で作るのは、C++ 側 (`openjtalk_phonemize.cpp`) と
    PUA マップがずれていた前例があるため（`docs/decisions.md` C-002）。

    キャリアの選定には 2 つの罠があり、どちらも実測で踏んだ:

    * 母音キャリア `あ_あ` → `へ` `は` が **助詞と解釈**され `へ → ['e']` になる
    * 無声子音キャリア `か_か` → 挟まれた母音が **無声化**し `し → ['sh','I']` になる

    有声子音の `ま_ま` を使い、それでも助詞読みが残る `は` `へ` `を` だけ
    `PARTICLE_KANA` で上書きする。
    """
    from piper_plus_g2p.japanese import JapanesePhonemizer

    phonemizer = JapanesePhonemizer()
    seeds = (
        "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめも"
        "やゆよらりるれろわをんがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ"
        "きゃきゅきょしゃしゅしょちゃちゅちょにゃにゅにょひゃひゅひょみゃみゅみょ"
        "りゃりゅりょぎゃぎゅぎょじゃじゅじょびゃびゅびょぴゃぴゅぴょ"
        "ふぁふぃふぇふぉうぃうぇてぃでぃとぅどぅゔっー"
    )
    morae: list[str] = []
    i = 0
    while i < len(seeds):
        if i + 1 < len(seeds) and seeds[i + 1] in "ゃゅょぁぃぇぉ":
            morae.append(seeds[i : i + 2])
            i += 2
        else:
            morae.append(seeds[i])
            i += 1

    table: dict[str, list[str]] = {}
    for mora in morae:
        phonemes = [p for p in phonemizer.phonemize("ま" + mora + "ま") if p not in MARKS]
        if len(phonemes) >= 5 and phonemes[:2] == ["m", "a"] and phonemes[-2:] == ["m", "a"]:
            # テーブルは常に**有声の基底形**を持つ。無声化は `°` マークが担う。
            # キャリアが実在語を作ると文脈で無声化する（`ま+す+ま` → 「ます」で
            # `す → ['s','U']`）ので、ここで必ず戻す。
            table[mora] = [DEVOICE.get(p, p) for p in phonemes[2:-2]]
    table.update(PARTICLE_KANA)
    return table


def table_size_bytes(table: dict[str, list[str]]) -> int:
    """端末に載せる mora テーブルの素バイト数（キー + 値 + 区切り）。"""
    return sum(
        len(k.encode()) + sum(len(p.encode()) + 1 for p in v) for k, v in table.items()
    )


# --- 端末側の変換（C99 に移植する部分）--------------------------------------


def intermediate_to_phonemes(
    seq: list[str], table: dict[str, list[str]]
) -> list[str]:
    """中間表現 → 音素列。**mora テーブルと `ん` の規則しか使わない。**

    Parameters
    ----------
    seq
        1 要素 = 1 モーラまたは 1 記号。`intermediate_to_tokens()` で作る。
    """
    out: list[str] = []
    for i, item in enumerate(seq):
        if item in MARKS:
            out.append(item)
            continue

        devoiced = item.endswith(DEVOICED_MARK)
        mora = item[:-1] if devoiced else item

        if mora == "ー":  # 長音は直前の母音を繰り返す
            for prev in reversed(out):
                if prev in VOWELS:
                    out.append(prev)
                    break
            continue

        if mora == "ん":  # 後続子音で異音が決まる
            following = None
            for j in range(i + 1, len(seq)):
                if seq[j] in MARKS:
                    continue
                nxt = table.get(seq[j].rstrip(DEVOICED_MARK))
                following = nxt[0] if nxt else None
                break
            out.append(N_ALLOPHONE.get(following, "N_uvular"))
            continue

        phonemes = list(table.get(mora, []))
        if not phonemes:
            raise KeyError(f"mora テーブルに無い: {mora!r}")
        if devoiced and phonemes[-1] in REVOICE:
            phonemes[-1] = REVOICE[phonemes[-1]]
        out.extend(phonemes)
    return out


def intermediate_to_tokens(text: str, table: dict[str, list[str]]) -> list[str]:
    """中間表現の文字列を、モーラ／記号のリストに分割する。"""
    tokens: list[str] = []
    i = 0
    max_mora = max(len(k) for k in table)
    # 疑問 EOS (`?!` `?.` `?~`) は 2 文字あるので長いものから照合する
    multi_char_marks = sorted((m for m in MARKS if len(m) > 1), key=len, reverse=True)

    while i < len(text):
        mark = next((m for m in multi_char_marks if text.startswith(m, i)), None)
        if mark is None and text[i] in MARKS:
            mark = text[i]
        if mark is not None:
            tokens.append(mark)
            i += len(mark)
            continue

        for length in range(min(max_mora, len(text) - i), 0, -1):
            if text[i : i + length] in table:
                mora = text[i : i + length]
                i += length
                if i < len(text) and text[i] == DEVOICED_MARK:
                    mora += DEVOICED_MARK
                    i += 1
                tokens.append(mora)
                break
        else:
            raise KeyError(f"解釈できない文字: {text[i]!r} (位置 {i})")
    return tokens


# --- ホスト側の変換（オフラインで一度だけ）----------------------------------


def phonemes_to_intermediate(
    phonemes: list[str], table: dict[str, list[str]]
) -> list[str]:
    """音素列 → 中間表現。ホスト側で使う逆変換。"""
    reverse: dict[tuple[str, ...], str] = {}
    for mora, ph in table.items():
        reverse.setdefault(tuple(ph), mora)
    for variant in ("N_m", "N_n", "N_ng", "N_uvular", "N"):
        reverse.setdefault((variant,), "ん")
    for mora, ph in list(table.items()):
        for devoiced, voiced in DEVOICE.items():
            if ph and ph[-1] == voiced:
                reverse.setdefault(tuple(ph[:-1] + [devoiced]), mora + DEVOICED_MARK)

    max_len = max(len(k) for k in reverse)
    out: list[str] = []
    i = 0
    while i < len(phonemes):
        if phonemes[i] in MARKS:
            out.append(phonemes[i])
            i += 1
            continue
        for length in range(min(max_len, len(phonemes) - i), 0, -1):
            key = tuple(phonemes[i : i + length])
            if key in reverse:
                out.append(reverse[key])
                i += length
                break
        else:
            raise KeyError(f"逆引きできない音素: {phonemes[i]!r}")
    return out


def text_to_intermediate(text: str, table: dict[str, list[str]]) -> list[str]:
    """漢字かな交じり文 → 中間表現。**ホスト側専用**（OpenJTalk を使う）。"""
    from piper_plus_g2p.japanese import JapanesePhonemizer

    return phonemes_to_intermediate(JapanesePhonemizer().phonemize(text), table)


# --- セルフテスト -----------------------------------------------------------


def main() -> int:
    from piper_plus_g2p.japanese import JapanesePhonemizer

    table = build_mora_table()
    phonemizer = JapanesePhonemizer()
    print(f"mora テーブル: {len(table)} エントリ / {table_size_bytes(table)} B")
    print(f"ん の異音規則: {len(N_ALLOPHONE)} 件\n")

    cases = [
        ("今日は良い天気ですね。", "基本"),
        ("電源を入れてください。", "通知文"),
        ("橋を渡る。", "アクセント: 尾高"),
        ("箸を持つ。", "アクセント: 頭高"),
        ("端を持つ。", "アクセント: 平板"),
        ("コンピューターを再起動します。", "カタカナ語"),
        ("エラーが発生しました。", "カタカナ語"),
        ("バッテリー残量は十五パーセントです。", "カタカナ + 数詞"),
        ("シャットダウンしています。", "カタカナ語 + 促音"),
        ("午後三時十五分です。", "時刻"),
    ]

    failures = 0
    print(f"{'原文':<24}{'中間表現':<30}{'判定'}")
    for text, note in cases:
        reference = phonemizer.phonemize(text)
        try:
            intermediate = text_to_intermediate(text, table)
            restored = intermediate_to_phonemes(intermediate, table)
        except KeyError as exc:
            print(f"{text[:22]:<24}{'—':<30}NG ({exc})")
            failures += 1
            continue
        joined = "".join(intermediate)
        # 中間表現の文字列を再パースしても同じになるか（往復の完全性）
        reparsed = intermediate_to_phonemes(intermediate_to_tokens(joined, table), table)
        ok = restored == reference and reparsed == reference
        failures += not ok
        print(f"{text[:22]:<24}{joined[:28]:<30}{'OK' if ok else 'NG'}  {note}")

    print()
    if failures:
        print(f"{failures} 件 NG")
        return 1
    print(f"{len(cases)}/{len(cases)} 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
