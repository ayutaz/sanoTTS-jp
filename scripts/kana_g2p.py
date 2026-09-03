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
  ここが C99 に移植される部分。必要なデータは **mora テーブル 1,786 B + `ん` 異音規則 21 件**

コーパス 23,457 行での実測（A-1 / Phase B-a）:

    表現可能 99.77%   往復一致 100.00%

表現できない 0.23%（55 行）は **`fy` を含む行**で、これは中間表現の問題ではなく
**教師の音素表に `fy` が無い**（`phoneme_id_map` に存在しない）。ラベル生成から除外する。

実行するとセルフテストが走る:

    ~/Documents/piper-plus/.venv/bin/python scripts/kana_g2p.py
"""

from __future__ import annotations

import pathlib
import sys

import os

#: piper-plus の checkout。**環境変数で差し替えられる**（他人の環境でも動くように）。
#: 既定は開発者のローカルパスだが、clone した人は `PIPER_PLUS_ROOT` を設定する。
PIPER_PLUS = os.environ.get("PIPER_PLUS_ROOT",
                            os.path.expanduser("~/Documents/piper-plus"))
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


class IntermediateError(KeyError):
    """中間表現として解釈できない文字。**位置を機械的に取れるようにするためだけ**の型。

    ⚠️ `KeyError` の派生なので、既存の `except KeyError` はそのまま通る
    （`str()` も従来と同じ文言）。端末側 `saan_g2p()` の `err_byte` と
    突き合わせるのに文字位置が要るので、`index` を属性で持たせてある。
    """

    def __init__(self, char: str, index: int) -> None:
        super().__init__(f"解釈できない文字: {char!r} (位置 {index})")
        self.char = char
        self.index = index

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

# 子音 → その子音を表すかな（拗音は「かな + 小書き」で綴る）
_CONSONANT_KANA = {
    "ts": "つ", "v": "ゔ", "f": "ふ", "sh": "し", "j": "じ", "ch": "ち",
    "ky": "き", "gy": "ぎ", "ny": "に", "hy": "ひ", "by": "び", "py": "ぴ",
    "my": "み", "ry": "り", "ty": "て", "dy": "で", "kw": "く", "gw": "ぐ",
    "s": "す", "z": "ず", "t": "と", "d": "ど", "y": "い", "w": "う",
}
# 母音 → 小書きかな
_SMALL_VOWEL = {"a": "ぁ", "i": "ぃ", "u": "ぅ", "e": "ぇ", "o": "ぉ"}
# 拗音の子音は「小書きのヤ行」で綴るのが標準（きゃ きゅ きょ）。
# 非標準の母音（きぇ 等）は小書き母音で綴る。
_YOUON_VOWEL = {"a": "ゃ", "u": "ゅ", "o": "ょ"}
_YOUON = {"ky", "gy", "ny", "hy", "by", "py", "my", "ry", "ty", "dy", "sh", "j", "ch"}


def _build_foreign_mora() -> dict[str, list[str]]:
    """外来語・非標準の (子音, 母音) 組み合わせを**規則で**生成する。

    キャリア法（`build_mora_table`）では導出できない。`まつぁま` のような文字列は
    実在語にならず OpenJTalk が別の読みに割るため、音素を実測で取れない。

    個別に列挙するとコーパスを走査するたびに漏れが出る（実際に 2 回漏らした）ので、
    **子音 × 母音の直積を張る**。表音としての値なので機械的に決まる。
    """
    table: dict[str, list[str]] = {}
    for cons, kana in _CONSONANT_KANA.items():
        for vowel, small in _SMALL_VOWEL.items():
            if cons in _YOUON and vowel in _YOUON_VOWEL:
                mora = kana + _YOUON_VOWEL[vowel]      # きゃ きゅ きょ
            else:
                mora = kana + small                     # きぇ つぁ ゔぃ くゎ
            table.setdefault(mora, [cons, vowel])
    table["いぇ"] = ["y", "e"]
    return table


FOREIGN_MORA = _build_foreign_mora()
"""規則生成した拡張モーラ。`piper_plus_g2p` の音素表に無い組み合わせは
`build_mora_table` 側で ID に落ちないだけなので、多めに張っても害はない。"""


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
    table.update(FOREIGN_MORA)
    # `ん` の異音は後続子音から決まるので、外来音の子音も規則に足す
    N_ALLOPHONE.setdefault("kw", "N_ng")
    N_ALLOPHONE.setdefault("gw", "N_ng")
    N_ALLOPHONE.setdefault("v", "N_m")   # 唇歯音。両唇音に寄せる
    return table


FROZEN_TABLE = pathlib.Path(__file__).resolve().parent.parent / "csrc" / "g2p_table.json"
"""`gen_g2p_tables.py` が `csrc/g2p_table.h` と一緒に書く凍結テーブル。"""


def load_frozen_mora_table() -> dict[str, list[str]]:
    """凍結した mora テーブルを読む。**piper-plus (OpenJTalk) が要らない。**

    `build_mora_table()` は phonemizer を呼ぶので、piper-plus を持っていない人は
    1 文字も変換できない。リリースの重みだけで合成できるようにするための経路
    （C-041）。⚠️ **漢字→かな**はこれでは出来ない（`text_to_intermediate` は
    OpenJTalk が要る）。**かな中間表現→音素**だけ。

    ⚠️ **読んだ表の SHA-256 を必ず検証する。** 手で編集された表や、
    生成器と食い違った表をそのまま使うと、**音は出るのに端末と違う列**になる。
    ハッシュは `gen_g2p_vectors.table_sha256()` と同じ関数で計算する。

    ⚠️ **副作用がある。** `build_mora_table()` と同じく `N_ALLOPHONE` に
    外来音の 3 件（kw / gw / v）を反映する。ここを揃えないと `ん` の異音が
    静かに変わる。
    """
    import json

    if not FROZEN_TABLE.exists():
        raise SystemExit(
            f"凍結テーブルが無い: {FROZEN_TABLE}\n"
            "  uv run python scripts/gen_g2p_tables.py で生成する"
            "（piper-plus が要る）")
    blob = json.loads(FROZEN_TABLE.read_text(encoding="utf-8"))
    table = {k: list(v) for k, v in blob["mora"].items()}
    N_ALLOPHONE.update(blob["n_allophone"])

    from gen_g2p_vectors import table_sha256   # ⚠️ 循環を避けるため関数内で import

    # ⚠️ **異音規則は自分のものを渡す。** このファイルをスクリプトとして実行すると
    #    こちらは `__main__`、`gen_g2p_vectors` の `import kana_g2p` は**別実体**になり、
    #    向こうの `N_ALLOPHONE` は build 前の 18 件のまま。渡さないと必ず落ちる。
    got = table_sha256(table, allophone=N_ALLOPHONE)
    if got != blob["sha256"]:
        raise SystemExit(
            f"凍結テーブルの SHA-256 が合わない\n"
            f"  記録: {blob['sha256']}\n"
            f"  実際: {got}\n"
            "  **手で編集したか、生成器と食い違っている。**"
            " uv run python scripts/gen_g2p_tables.py を打ち直すこと")
    return table


def mora_table(prefer_frozen: bool = False) -> tuple[dict[str, list[str]], str]:
    """使える方の mora テーブルを返す。戻り値は (表, どちらを使ったか)。

    ⚠️ **どちらを使ったかを必ず呼び出し側に返す。** 黙って切り替えると、
    凍結テーブルが古いときに「なぜか端末と音が違う」になって追えなくなる。
    """
    if prefer_frozen:
        return load_frozen_mora_table(), "frozen"
    try:
        return build_mora_table(), "live"
    except ImportError:
        return load_frozen_mora_table(), "frozen"


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
            raise IntermediateError(text[i], i)
    return tokens


# --- 行の経路判定（K-B / 端末の csrc/g2p.c と同じ 3 値）----------------------

# ⚠️ **`?` 系は入れない**（`?` `?!` `?.` `?~` は先頭が `?`）。EOS のマークであると同時に
#    普通の日本語の疑問文にも出る約物で、入れると `本当なんでしょうか?` が拒否になる
#    （held-out 2,333 行で 45 行 = 1.93%、うち 42 行が普通の疑問文）。
#    csrc/g2p.c の has_mark() と同じ集合でなければならない（kb_route_parity.py が突き合わせる）。
MARK_CHARS = frozenset(m[0] for m in MARKS if not m[0].startswith("?")) | {DEVOICED_MARK}
"""「行にマークがあるか」を見るための 1 文字集合。

⚠️ **手書きしない。** `MARKS` の先頭 1 文字（`?!` `?.` `?~` は `?` に潰れる）と
`°` から導く。端末側 `csrc/g2p.c` の `has_mark()` が `kSaanG2pMarks[].b0` から
同じ集合を導いており、**表を更新すれば両方が同時に動く**。
"""


def classify_route(text: str, table: dict[str, list[str]]) -> tuple[str, int]:
    """行を 3 値に分ける。**端末の `saan_g2p_classify()` と同じ規則。**

    戻り値は `(経路, err_byte)`。経路は `"kana"` / `"dict"` / `"reject"`。
    `err_byte` は**トークン化が止まった UTF-8 バイト位置**（かな経路では -1）。

    規則:
      kana    `intermediate_to_tokens()` が行末まで通る          → 端末は `saan_g2p()`
      dict    通らず、行に**マークが 1 つも無い**                → 端末は漢字経路
      reject  通らず、行に**マークがある**                      → 端末は拒否

    ⚠️ **判定を別に書き下さない。** 「ひらがなならかな経路」は凍結テーブルとずれる
    （`ぁぃぇぉゃゅょゎゐゑゕゖ` は単独ではモーラになれず、`_ ^ $` はマーク側）。
    判定は**トークナイザが通るかそのもの**にしてある。

    ⚠️ **拒否を残すのが要点。** 「中間表現 + `。`」を黙って辞書経路に回すと、
    `[` `]` `#` が読み上げられたり落とされたりして**それらしい音が出てしまう**。

    ⚠️ **不正な UTF-8 はここでは扱えない**（Python の `str` は復号済み）。
    端末側は `SAAN_G2P_ERR_UTF8` を `reject` に倒す。**一致検査は `str` に
    できる入力だけで行う**（`scripts/k1/kb_route_parity.py`）。
    """
    try:
        intermediate_to_tokens(text, table)
    except IntermediateError as exc:
        err_byte = len(text[: exc.index].encode("utf-8"))
    else:
        return ("kana", -1)
    if any(ch in MARK_CHARS for ch in text):
        return ("reject", err_byte)
    return ("dict", err_byte)


# --- ホスト側の変換（オフラインで一度だけ）----------------------------------


def normalize_mid_mora_marks(phonemes: list[str]) -> list[str]:
    """子音と母音の間に入り込んだアクセント記号をモーラ境界へ寄せる。

    OpenJTalk は稀に `... t # a ...` のようにモーラの内部で句境界を切る。
    中間表現はモーラ単位なので、このままでは表現できない（A-1 で全走査した結果、
    `n #` `g #` `w #` など 20 種以上・計 1,000 回超が該当した）。

    子音の直後の記号を、その子音の**前**へ移す。モーラを割らない位置に寄せるだけで、
    記号そのものは消さない。

    ⚠️ **これが出力を「良くする」のか「変える」だけなのかは未測定**（Phase A §5）。
    機構としては OpenJTalk 側のフレージングの粗さを吸収しているように見えるが、
    教師音声への影響は確認していない。
    """
    vowels = set("aiueoAIUEO")
    out: list[str] = []
    i = 0
    while i < len(phonemes):
        p = phonemes[i]
        # 子音 + 記号 + 母音 の並びなら、記号を子音の前へ
        if (
            p not in MARKS
            and p not in vowels
            and i + 2 < len(phonemes)
            and phonemes[i + 1] in ACCENT_MARKS
            and phonemes[i + 2] in vowels
        ):
            out.append(phonemes[i + 1])
            out.append(p)
            i += 2
            continue
        out.append(p)
        i += 1
    return out


def phonemes_to_intermediate(
    phonemes: list[str], table: dict[str, list[str]], normalize: bool = True
) -> list[str]:
    """音素列 → 中間表現。ホスト側で使う逆変換。

    `normalize=True` でモーラ内部のアクセント記号を境界へ寄せる（A-1 の決定）。
    """
    if normalize:
        phonemes = normalize_mid_mora_marks(phonemes)
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


#: 入力の正規化表。**同じに見えて別のコードポイントである記号**を教師が扱える側に寄せる。
#: 揃えないと**例外も警告も出ずに約物が丸ごと落ちる**（未知語が無音で消えるのと同じ壊れ方）。
INPUT_NORMALIZE: dict[str, str] = {
    "\u301c": "\uff5e",   # 〜 WAVE DASH → ～ FULLWIDTH TILDE
    "\u2212": "\uff0d",   # − MINUS SIGN → － FULLWIDTH HYPHEN-MINUS
    "\u00a0": " ",        # NO-BREAK SPACE
}


def normalize_input(text: str) -> str:
    """ホスト G2P に渡す前の記号正規化。

    実測: `まじで？〜`（U+301C WAVE DASH）は疑問 EOS `?~` にならず、
    **約物が黙って消える**。`？～`（U+FF5E）だと `?~` になる。
    日本語では U+301C のほうが普通に使われるので、ここで寄せる。
    """
    for a, b in INPUT_NORMALIZE.items():
        text = text.replace(a, b)
    return text


def text_to_intermediate(text: str, table: dict[str, list[str]]) -> list[str]:
    """漢字かな交じり文 → 中間表現。**ホスト側専用**（OpenJTalk を使う）。"""
    from piper_plus_g2p.japanese import JapanesePhonemizer

    return phonemes_to_intermediate(
        JapanesePhonemizer().phonemize(normalize_input(text)), table)


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

    # --- 凍結テーブルのドリフト検査 -----------------------------------------
    # ⚠️ **ここでしか捕まえられない。** `load_frozen_mora_table()` の SHA-256 は
    #    「JSON が自己整合か」しか見ない。**phonemizer が変わって live 側だけ動いた**
    #    ケースは、両方が揃っているこの環境で突き合わせるしかない。
    frozen = load_frozen_mora_table()
    if frozen != table:
        diff = sorted(set(frozen) ^ set(table)) or \
            [k for k in table if frozen.get(k) != table[k]]
        print(f"NG  csrc/g2p_table.json が build_mora_table() と食い違う: {diff[:10]}")
        print("    uv run python scripts/gen_g2p_tables.py を打ち直すこと")
        failures += 1
    else:
        print(f"凍結テーブル {FROZEN_TABLE.name}: {len(frozen)} 件が live と完全一致")

    if failures:
        print(f"{failures} 件 NG")
        return 1
    print(f"{len(cases)}/{len(cases)} 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
