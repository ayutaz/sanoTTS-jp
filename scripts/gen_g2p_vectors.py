#!/usr/bin/env python3
"""端末側 G2P (C99 移植) のテストベクタを生成する。

**Python の `scripts/kana_g2p.py` を唯一の正とする。** C 側の実装はここが吐いた
`(中間表現, 期待 ids)` と**完全一致**しなければならない（相関や一致率ではなく bit 一致）。

    uv run python scripts/gen_g2p_vectors.py --closed --out csrc/g2p_vectors.bin
    uv run python scripts/gen_g2p_vectors.py --corpus --out csrc/g2p_vectors_corpus.bin \
        --manifest csrc/g2p_vectors_corpus.json

2 つのベクタ集合がある:

* `--closed` — **mora テーブルだけから機械生成**する自己完結セット。
  コーパス本文を 1 文字も含まない。境界条件・エラー経路もここに入る
* `--corpus` — コーパス全行をホスト G2P に通した中間表現。網羅性のため

⚠️ **コーパス本文（漢字かな交じり文）はベクタに入れない。** 入るのは
`text_to_intermediate()` が出した**かな中間表現と期待 ids だけ**で、uid も原文も
書かない。出力先 `csrc/*.bin` は `.gitignore` 済みなのでコミットもされない
（`csrc/ids_heldout.json` が Common Voice の本文を追跡してしまっている前例がある）。

⚠️ **不正 UTF-8 のベクタだけは Python に対応物が無い**（`str` は既にデコード済み）。
期待値は「C 側の設計判断」であって参照実装との一致ではない。`csrc/g2p.h` に規約を書き、
ここでは**その規約どおりの期待値を手で置いている**。

生成器自身のゲート（`writing-gates`）:

* 表引きの**鏡実装**（ドロップ件数を数えるために書いた第 2 の実装）が
  `kana_g2p` の出力と 1 件でも食い違ったら **AssertionError で止まる**。
  鏡がドリフトしたまま「期待値」を吐くのが一番危ない
* mora テーブルが 195 エントリ / `ん` 異音が 21 件 / 語彙閉包が 57 であることを assert
* `a..o`(10..14) と `A..O`(15..19) が **+5** で対応することを assert
  （C 側は算術で無声化するので、この並びが崩れると黙って壊れる）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import kana_g2p as K                                    # noqa: E402
from saanotts_jp.vocab import TOKENS, VOCAB_TABLE       # noqa: E402

# --- ベクタ形式 -------------------------------------------------------------
# little-endian 固定。ホスト (arm64/x86_64) と ESP32-S3 はどちらも LE。
#
#   ヘッダ 48 B: "G2PV" | u32 version | u32 n_vectors | u32 reserved | u8 sha256[32]
#   レコード 40 B + 可変長:
#       u32 kind | i32 err_byte | u32 text_len | u32 n_ids | u32 n_phonemes
#       u32 n_pad | u32 n_drop_long | u32 n_drop_devoice | u32 name_len | u32 reserved
#       name[name_len] | text[text_len] | **4 バイト境界までパディング** | i32 ids[n_ids]
#
# ⚠️ ids の直前でパディングする。name/text は可変長なので、詰めると ids が 4 バイト
#    境界に載らず、C 側の `const int32_t *` が**アラインメント違反**になる。
MAGIC = b"G2PV"
VERSION = 1

KIND_OK = 0           # 変換が成功し、ids が完全一致すること
KIND_ERR_UNKNOWN = 1  # 中間表現に無い文字。err_byte も一致すること
KIND_ERR_UTF8 = 2     # 不正な UTF-8。err_byte も一致すること

# --- 参照（kana_g2p）の薄いラッパ ------------------------------------------

STUDENT_INDEX: dict[str, int] = {tok: i for i, tok in enumerate(TOKENS)}
PAD_ID = STUDENT_INDEX["_"]
BOS_ID = STUDENT_INDEX["^"]
EOS_ID = STUDENT_INDEX["$"]


class UnknownChar(Exception):
    """中間表現として解釈できない文字。`byte_off` は UTF-8 でのバイト位置。"""

    def __init__(self, char_index: int, byte_off: int, ch: str) -> None:
        super().__init__(f"解釈できない文字: {ch!r} (char {char_index} / byte {byte_off})")
        self.char_index = char_index
        self.byte_off = byte_off


def tokenize(text: str, table: dict[str, list[str]]) -> list[str]:
    """`K.intermediate_to_tokens` の鏡。**失敗位置をバイトで返す**ためだけに書いた。

    ⚠️ 鏡は必ずドリフトする。呼び出し側 (`encode`) で参照と突き合わせて assert すること。
    """
    tokens: list[str] = []
    i = 0
    max_mora = max(len(k) for k in table)
    multi = sorted((m for m in K.MARKS if len(m) > 1), key=len, reverse=True)
    while i < len(text):
        mark = next((m for m in multi if text.startswith(m, i)), None)
        if mark is None and text[i] in K.MARKS:
            mark = text[i]
        if mark is not None:
            tokens.append(mark)
            i += len(mark)
            continue
        for length in range(min(max_mora, len(text) - i), 0, -1):
            if text[i : i + length] in table:
                mora = text[i : i + length]
                i += length
                if i < len(text) and text[i] == K.DEVOICED_MARK:
                    mora += K.DEVOICED_MARK
                    i += 1
                tokens.append(mora)
                break
        else:
            raise UnknownChar(i, len(text[:i].encode("utf-8")), text[i])
    return tokens


def convert_with_counts(
    seq: list[str], table: dict[str, list[str]]
) -> tuple[list[str], int, int]:
    """`K.intermediate_to_phonemes` の鏡 + **黙って落ちた件数**。

    Python は `ー` と `°` を例外なしに捨てる経路が 2 本あり、C が同じように黙って
    捨てても**テストは緑のまま**になる。件数を数えて期待値に載せるのが唯一の検出手段。

    * `n_drop_long`    — 直前に平母音が無くて何も出力しなかった `ー`
    * `n_drop_devoice` — `°` が付いていたのに無声化母音を生まなかったトークン
                         （`っ°` `ん°` `ー°`）
    """
    out: list[str] = []
    n_drop_long = 0
    n_drop_devoice = 0
    for i, item in enumerate(seq):
        if item in K.MARKS:
            out.append(item)
            continue
        devoiced = item.endswith(K.DEVOICED_MARK)
        mora = item[:-1] if devoiced else item

        if mora == "ー":
            hit = False
            for prev in reversed(out):
                if prev in K.VOWELS:
                    out.append(prev)
                    hit = True
                    break
            n_drop_long += not hit
            n_drop_devoice += devoiced        # `ー°` の `°` は届かずに消える
            continue

        if mora == "ん":
            following = None
            for j in range(i + 1, len(seq)):
                if seq[j] in K.MARKS:
                    continue
                nxt = table.get(seq[j].rstrip(K.DEVOICED_MARK))
                following = nxt[0] if nxt else None
                break
            out.append(K.N_ALLOPHONE.get(following, "N_uvular"))
            n_drop_devoice += devoiced        # `ん°` の `°` も届かない
            continue

        phonemes = list(table.get(mora, []))
        if not phonemes:
            raise KeyError(f"mora テーブルに無い: {mora!r}")
        if devoiced:
            if phonemes[-1] in K.REVOICE:
                phonemes[-1] = K.REVOICE[phonemes[-1]]
            else:
                n_drop_devoice += 1           # `っ°` は黙って無視される
        out.extend(phonemes)
    return out, n_drop_long, n_drop_devoice


def intersperse(phonemes: list[str]) -> list[int]:
    """音素列 → 生徒インデックス列。`gen_teacher_labels.encode_intermediate` と同一規則。

    先頭 `^` + PAD、各音素の後ろに PAD（**その音素自身が PAD なら挟まない**）、末尾 `$`。
    ここを外すと発話が約 2.4 倍速になるが**例外は出ない**（C-007）。
    """
    ids = [BOS_ID, PAD_ID]
    for p in phonemes:
        i = STUDENT_INDEX[p]
        ids.append(i)
        if i != PAD_ID:
            ids.append(PAD_ID)
    ids.append(EOS_ID)
    return ids


def encode(text: str, table: dict[str, list[str]]) -> dict:
    """中間表現 → ベクタ 1 件。参照と鏡の両方を走らせて突き合わせる。"""
    try:
        tokens = tokenize(text, table)
    except UnknownChar as exc:
        # 参照も同じ位置で落ちること（鏡がずれていないことの確認）
        try:
            K.intermediate_to_tokens(text, table)
        except KeyError as ref_exc:
            assert f"位置 {exc.char_index}" in str(ref_exc), (
                f"鏡と参照で失敗位置が違う: 鏡={exc.char_index} 参照={ref_exc}")
        else:
            raise AssertionError(f"鏡だけが失敗した: {text!r}") from exc
        return {
            "kind": KIND_ERR_UNKNOWN,
            "err_byte": exc.byte_off,
            "text": text.encode("utf-8"),
            "ids": [],
            "n_phonemes": 0,
            "n_pad": 0,
            "n_drop_long": 0,
            "n_drop_devoice": 0,
        }

    assert tokens == K.intermediate_to_tokens(text, table), \
        f"鏡のトークン化が参照とずれた: {text!r}"
    phonemes, n_long, n_dev = convert_with_counts(tokens, table)
    assert phonemes == K.intermediate_to_phonemes(tokens, table), \
        f"鏡の音素変換が参照とずれた: {text!r}"

    ids = intersperse(phonemes)
    n_pad = sum(1 for p in phonemes if p == "_")
    # 長さの不変量（C-019）。**単位を 2 通り書いて両方 assert する** —
    # `n_phonemes` が「PAD 込み」なのか「PAD 抜き」なのかで符号が逆になる。
    assert len(ids) == 2 * len(phonemes) + 3 - n_pad
    assert len(ids) == 2 * (len(phonemes) - n_pad) + 3 + n_pad
    return {
        "kind": KIND_OK,
        "err_byte": -1,
        "text": text.encode("utf-8"),
        "ids": ids,
        "n_phonemes": len(phonemes),
        "n_pad": n_pad,
        "n_drop_long": n_long,
        "n_drop_devoice": n_dev,
    }


# --- テーブルのハッシュ -----------------------------------------------------


def table_sha256(table: dict[str, list[str]],
                 allophone: dict[str, str] | None = None) -> str:
    """mora テーブル + `ん` 異音規則 + 語彙の正準シリアライズの SHA-256。

    C 側の `saan_g2p_table_sha256[]` と突き合わせて、**違うテーブルで作ったベクタと
    突き合わせる事故**を塞ぐ。ベクタが古いのか実装が古いのかを区別できる。

    ⚠️ **`allophone` を明示的に渡せるようにしてある。** 既定は `K.N_ALLOPHONE` だが、
    それは**この翻訳単位が掴んでいる `kana_g2p`** のグローバルであって、呼び出し側が
    見ているものとは限らない。`scripts/kana_g2p.py` を**スクリプトとして実行**すると
    そちらは `__main__`、こちらの `import kana_g2p` は**別のモジュール実体**になり、
    `build_mora_table()` が足す 3 件（kw / gw / v）が入っていない 18 件の方を読む。
    実際に踏んだ（ハッシュ検証が落ちて気づいた）。**自分のを渡すこと。**
    """
    if allophone is None:
        allophone = K.N_ALLOPHONE
    lines = [f"{k}\t{' '.join(table[k])}" for k in sorted(table)]
    lines.append("--allophone--")
    lines += [f"{k}\t{allophone[k]}" for k in sorted(allophone)]
    lines.append("--vocab--")
    lines += [f"{i}\t{tok}" for i, tok in enumerate(TOKENS)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --- ベクタ集合 -------------------------------------------------------------


def sanity_checks(table: dict[str, list[str]]) -> None:
    """テーブルそのものの assert。C 側の設計がここに依存している。"""
    assert len(table) == 195, f"mora テーブルが 195 でない: {len(table)}"
    assert len(K.N_ALLOPHONE) == 21, f"ん 異音規則が 21 件でない: {len(K.N_ALLOPHONE)}"
    assert max(len(v) for v in table.values()) <= 2, "1 モーラ 2 音素を超えた"
    assert max(len(k) for k in table) <= 2, "キーが 2 文字を超えた"
    cps = {ord(c) for k in table for c in k}
    assert min(cps) == 0x3041 and max(cps) == 0x30FC, \
        f"キーのコードポイント範囲が変わった: {hex(min(cps))}..{hex(max(cps))}"
    closure: set[str] = set(K.MARKS) | {"N_uvular"} | set(K.REVOICE.values())
    for v in table.values():
        closure |= set(v)
    closure |= set(K.N_ALLOPHONE.values())
    assert closure == set(TOKENS), \
        f"語彙閉包が 57 と一致しない: 余り {closure - set(TOKENS)} / 不足 {set(TOKENS) - closure}"
    # C は無声化を `id + 5` の算術でやる。並びが崩れたら黙って別の音素になる
    for voiced, devoiced in (("a", "A"), ("i", "I"), ("u", "U"), ("e", "E"), ("o", "O")):
        assert STUDENT_INDEX[devoiced] - STUDENT_INDEX[voiced] == 5, \
            f"{voiced}->{devoiced} が +5 でない。vocab.py の並びが変わった"
    assert [t for _, _, t in VOCAB_TABLE] == list(TOKENS)


BOUNDARY_CASES: list[tuple[str, str]] = [
    # (名前, 中間表現)  ⚠️ すべて合成。コーパス本文由来ではない
    ("empty", ""),
    ("mark_only_open", "["),
    ("mark_only_all", "[]#^$_"),
    ("mark_run_10", "[[[[[[[[[["),
    ("pad_only", "_"),
    ("pad_run", "___"),
    ("demo_sentence", "きょ][おわよ][いて][んきです°ね"),
    ("q_plain", "ほ[んとおで]す°か?"),
    ("q_bang_then_q", "?!?"),
    ("q_then_bang", "??!"),
    ("q_dot", "?."),
    ("q_tilde", "?~"),
    ("q_all", "?!?.?~?"),
    # ⚠️ `ー` の沈黙脱落（例外が出ない 5 系統）
    ("long_alone", "ー"),
    ("long_after_mark", "[ー"),
    ("long_after_cl", "っー"),
    ("long_after_n", "んー"),
    ("long_after_devoiced", "き°ー"),
    ("long_normal", "かー"),
    ("long_double", "かーー"),
    ("long_across_mark", "か[ー"),
    ("long_across_marks", "か[]#ー"),
    ("long_across_consonant", "かす°ー"),
    ("long_with_devoice_mark", "ー°"),
    # ⚠️ `°` の沈黙無視
    ("devoice_on_cl", "っ°"),
    ("devoice_on_n", "ん°"),
    ("devoice_all_vowels", "か°き°く°け°こ°"),
    ("devoice_single_phoneme", "を°あ°"),
    ("devoice_two_char_mora", "きょ°し°ふ°"),
    # `ん` の異音
    ("n_final", "ん"),
    ("n_run", "んんんんん"),
    ("n_before_m", "あんま"),
    ("n_before_k", "あんか"),
    ("n_before_t", "あんた"),
    ("n_before_s", "あんさ"),
    ("n_before_vowel", "あんあ"),
    ("n_before_kw", "あんくぁ"),
    ("n_before_v", "あんゔぁ"),
    ("n_across_marks", "あん[]#か"),
    ("n_across_marks_10", "あん[[[[[[[[[[ま"),
    ("n_marks_then_end", "あん[]#"),
    ("n_before_devoiced", "あんき°"),
    ("n_before_cl", "あんっか"),
    # 最長一致（短一致だと**例外が出ずに**別の音素になる）
    ("longest_toxu", "とぅ"),
    ("longest_fuxu", "ふぅ"),
    ("longest_tsuxu", "つぅ"),
    ("longest_backoff", "きあ"),
    ("longest_kiyo", "きよ"),
    ("longest_kyo", "きょ"),
    ("small_u_standalone", "あぅ"),
    # 規則生成が実測値を上書きした 2 件（直感で手書きすると必ず外す）
    ("foreign_tyi", "てぃ"),
    ("foreign_dyi", "でぃ"),
    ("particle_ha", "は"),
    ("particle_he", "へ"),
    ("particle_wo", "を"),
    # PAD 規則（intersperse を `_` の後にも入れると長さが変わる）
    ("pad_rule_mixed", "え_そ][おなの?!"),
    ("pad_rule_edges", "_あ_"),
    ("pad_rule_pair", "あ__い"),
    # 長め（バッファ上限の確認用）。**合成**の繰り返しで本文ではない
    ("long_utterance", "あいうえおかきくけこさしすせそたちつてとなにぬねの" * 6),
]

ERROR_CASES_UNKNOWN: list[tuple[str, str]] = [
    ("unknown_kanji", "漢"),
    ("unknown_katakana", "ア"),
    ("unknown_ascii", "X"),
    ("unknown_space", " "),
    ("unknown_kuten", "。"),
    ("unknown_wi", "ゐ"),
    ("unknown_small_ya", "ゃ"),
    ("unknown_small_a_after", "あぁ"),
    ("unknown_bang_alone", "!"),
    ("unknown_tilde_alone", "~"),
    ("unknown_dot_alone", "."),
    ("degree_leading", "°"),
    ("degree_double", "か°°"),
    ("degree_after_mark", "[°"),
    ("unknown_after_valid", "あいう漢"),
    ("unknown_wave_dash", "〜"),        # U+301C。ホスト側で U+FF5E に寄せる対象
]

#: 不正 UTF-8。⚠️ **Python に対応物が無い。C 側の規約（csrc/g2p.h）に対する期待値**で、
#: 参照実装との一致ではない。`err_byte` は不正シーケンスの**先頭バイト**。
INVALID_UTF8_CASES: list[tuple[str, bytes, int]] = [
    ("utf8_truncated_head", b"\xe3\x81", 0),
    ("utf8_truncated_tail", b"\xe3\x81\x82\xe3\x81", 3),
    ("utf8_stray_continuation", b"\x81\x82", 0),
    ("utf8_continuation_after_valid", b"\xe3\x81\x82\x80", 3),
    ("utf8_overlong_2byte", b"\xc0\xaf", 0),
    ("utf8_overlong_3byte", b"\xe0\x80\xaf", 0),
    ("utf8_surrogate", b"\xed\xa0\x80", 0),
    ("utf8_ff", b"\xff", 0),
    ("utf8_ff_mid", b"\xe3\x81\x82\xff\xe3\x81\x82", 3),
    ("utf8_5byte_lead", b"\xf8\x88\x80\x80\x80", 0),
    ("utf8_lead_then_lead", b"\xe3\xe3\x81\x82", 0),
]

#: **妥当な UTF-8 だが中間表現に無い**もの。ERR_UTF8 ではなく ERR_UNKNOWN。
#: この 2 つを取り違えると「不正入力は全部 UTF8 エラー」でテストが満点を取れてしまう。
VALID_BUT_UNKNOWN_BYTES: list[tuple[str, bytes, int]] = [
    ("valid4_emoji", b"\xf0\x9f\x98\x80", 0),                 # U+1F600
    ("valid4_after_kana", b"\xe3\x81\x82\xf0\x9f\x98\x80", 3),
    ("embedded_nul", b"\xe3\x81\x82\x00\xe3\x81\x82", 3),      # strlen ではなく nbytes
]


def closed_vectors(table: dict[str, list[str]]) -> list[tuple[str, dict]]:
    """mora テーブルだけから機械生成する自己完結セット。**本文を一切含まない。**"""
    out: list[tuple[str, dict]] = []
    morae = sorted(table)
    marks = sorted(K.MARKS)

    for m in morae:
        out.append((f"bare:{m}", encode(m, table)))
        out.append((f"devoice:{m}", encode(m + K.DEVOICED_MARK, table)))
        out.append((f"long:{m}", encode(m + "ー", table)))
        out.append((f"nasal:{m}", encode("ん" + m, table)))
    for m in morae:
        for mk in marks:
            out.append((f"mark:{m}{mk}", encode(m + mk, table)))

    for name, text in BOUNDARY_CASES:
        out.append((f"edge:{name}", encode(text, table)))
    for name, text in ERROR_CASES_UNKNOWN:
        v = encode(text, table)
        assert v["kind"] == KIND_ERR_UNKNOWN, f"{name} は失敗するはずだった: {text!r}"
        out.append((f"err:{name}", v))

    for name, raw, off in INVALID_UTF8_CASES:
        out.append((f"utf8:{name}", {
            "kind": KIND_ERR_UTF8, "err_byte": off, "text": raw, "ids": [],
            "n_phonemes": 0, "n_pad": 0, "n_drop_long": 0, "n_drop_devoice": 0}))
    for name, raw, off in VALID_BUT_UNKNOWN_BYTES:
        out.append((f"utf8ok:{name}", {
            "kind": KIND_ERR_UNKNOWN, "err_byte": off, "text": raw, "ids": [],
            "n_phonemes": 0, "n_pad": 0, "n_drop_long": 0, "n_drop_devoice": 0}))
    return out


CORPUS_FILES = ("corpus_train.tsv", "corpus_heldout.tsv", "corpus_embedded.tsv")


def corpus_vectors(table: dict[str, list[str]], splits_dir: str,
                   limit: int = 0) -> tuple[list[tuple[str, dict]], dict]:
    """コーパス全行 → 中間表現 → ベクタ。**原文と uid はベクタに書かない。**

    ⚠️ TSV の 1 行目はヘッダ (`source/id/text`)。**発話として通してはいけない**
    （13 個のゲートを持つラベル生成器がヘッダ行を素通ししたのが C-018）。
    """
    out: list[tuple[str, dict]] = []
    n_rows = n_host_fail = 0
    for fn in CORPUS_FILES:
        path = os.path.join(splits_dir, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            header = f.readline().rstrip("\n").split("\t")
            assert header[:3] == ["source", "id", "text"], \
                f"{fn} のヘッダが想定と違う: {header[:3]}"
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3 or not parts[2]:
                    continue
                n_rows += 1
                if limit and len(out) >= limit:
                    break
                try:
                    mid = "".join(K.text_to_intermediate(parts[2], table))
                except KeyError:
                    n_host_fail += 1      # ホスト側の `fy` 逆引き失敗。端末の問題ではない
                    continue
                v = encode(mid, table)
                assert v["kind"] == KIND_OK, f"{fn} の行が端末側で失敗した: {mid!r}"
                # 名前に uid も原文も入れない（連番のみ）
                out.append((f"corpus:{len(out)}", v))
    stats = {"rows_scanned": n_rows, "host_g2p_failures": n_host_fail,
             "vectors": len(out)}
    return out, stats


# --- 書き出し ---------------------------------------------------------------


def write_vectors(path: str, vectors: list[tuple[str, dict]], sha_hex: str) -> int:
    buf = bytearray()
    buf += MAGIC
    buf += struct.pack("<III", VERSION, len(vectors), 0)
    buf += bytes.fromhex(sha_hex)
    assert len(buf) == 48
    for name, v in vectors:
        nb = name.encode("utf-8")
        buf += struct.pack("<IiIIIIIIII", v["kind"], v["err_byte"], len(v["text"]),
                           len(v["ids"]), v["n_phonemes"], v["n_pad"],
                           v["n_drop_long"], v["n_drop_devoice"], len(nb), 0)
        buf += nb
        buf += v["text"]
        while len(buf) % 4:          # ids を 4 バイト境界に載せる
            buf += b"\x00"
        for i in v["ids"]:
            buf += struct.pack("<i", i)
        assert len(buf) % 4 == 0
    with open(path, "wb") as f:
        f.write(buf)
    return len(buf)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--closed", action="store_true", help="自己完結セット（既定）")
    ap.add_argument("--corpus", action="store_true", help="コーパス全行を通す")
    ap.add_argument("--limit", type=int, default=0, help="コーパスの上限（デバッグ用）")
    ap.add_argument("--splits", default="data/splits")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", default="")
    args = ap.parse_args()
    if not args.closed and not args.corpus:
        args.closed = True

    # ⚠️ **piper-plus が無くても走れるようにする。** OpenJTalk が入っていない環境では
    #    凍結テーブル（csrc/g2p_table.json / sha256 検証つき）に落ちる。
    #    どちらを使ったかは必ず出す（黙って切り替えると追えない）。
    table, which = K.mora_table()
    print(f"mora テーブル: {which}（{len(table)} 件）")
    sanity_checks(table)
    sha = table_sha256(table)

    vectors: list[tuple[str, dict]] = []
    stats: dict = {}
    if args.closed:
        vectors += closed_vectors(table)
    if args.corpus:
        cv, stats = corpus_vectors(table, args.splits, args.limit)
        vectors += cv

    nbytes = write_vectors(args.out, vectors, sha)
    n_ok = sum(1 for _, v in vectors if v["kind"] == KIND_OK)
    n_ids = sum(len(v["ids"]) for _, v in vectors)
    n_long = sum(v["n_drop_long"] for _, v in vectors)
    n_dev = sum(v["n_drop_devoice"] for _, v in vectors)
    max_ids = max((len(v["ids"]) for _, v in vectors), default=0)

    print(f"mora テーブル {len(table)} エントリ / ん 異音 {len(K.N_ALLOPHONE)} 件 "
          f"/ 語彙 {len(TOKENS)}")
    print(f"sha256(table) = {sha}")
    print(f"{args.out}: {len(vectors)} ベクタ ({n_ok} 成功 / "
          f"{len(vectors) - n_ok} エラー期待) / ids 合計 {n_ids} / 最長 {max_ids} / "
          f"{nbytes:,} B")
    print(f"黙って落ちる件数の合計: ー {n_long} / ° {n_dev}")
    if stats:
        cover = stats["vectors"] / stats["rows_scanned"] if stats["rows_scanned"] else 0
        print(f"コーパス: {stats['rows_scanned']} 行走査 / "
              f"ホスト G2P 失敗 {stats['host_g2p_failures']} 行 "
              f"→ ベクタ {stats['vectors']} 行 ({cover:.2%})")

    if args.manifest:
        # ⚠️ **原文も uid も入れない。** 件数とハッシュだけ
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump({
                "table_sha256": sha,
                "table_entries": len(table),
                "n_allophone_rules": len(K.N_ALLOPHONE),
                "vocab_size": len(TOKENS),
                "n_vectors": len(vectors),
                "n_ok": n_ok,
                "n_error_expected": len(vectors) - n_ok,
                "total_ids": n_ids,
                "max_ids": max_ids,
                "dropped_long": n_long,
                "dropped_devoice": n_dev,
                "file_bytes": nbytes,
                "file_sha256": hashlib.sha256(open(args.out, "rb").read()).hexdigest(),
                **stats,
            }, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
