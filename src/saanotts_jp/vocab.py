"""デプロイ語彙の凍結（B-9）。

**日本語のデプロイ語彙は 57 トークン。** 論文（英語）の 157 entries ではない。
`kana_g2p` の mora テーブル 195 エントリ + `ん` の異音規則 + 無声化 + 長音 +
記号から**原理的に出せる音素の閉包**が 57 で、コーパス 23,454 行の出現 51 を厳密に含む
（コーパスにあって閉包に無いトークンは 0）。

⚠️ **生徒は教師の音素ID をそのまま埋め込みに使えない。** 教師の ID 空間は 0〜173 の
飛び飛びで、57 トークンの最大 ID は 64、途中に 8 個の穴（20–25 / 31 / 52）がある。
学習・推論の両方で `TEACHER_TO_STUDENT` を通すこと。**重みと一緒に凍結する。**

⚠️ **6 トークンはコーパス出現が 0**（`?!` `?.` `?~` `A` `E` `O`）。
- `?!` `?.` `?~` はホスト G2P が原文の約物からそのまま出すので**到達可能**。
  コーパスに 4 種の疑問 EOS を含む行を足すこと（現行コーパスに 1 行も無い）。
- `A` `E` `O` は無声化母音。日本語の母音無声化は狭母音 `i` `u` にほぼ限られるので
  実データには出ない（C-004）。**未学習行を残さないよう `a` `e` `o` と同値初期化する。**

再現: `uv run python scripts/b9_vocab_closure.py`
"""

from __future__ import annotations

# (student_index, teacher_id, token)
VOCAB_TABLE: tuple[tuple[int, int, str], ...] = (
    ( 0,   0,          '_'),   # corpus n=1,532,868
    ( 1,   1,          '^'),   # corpus n=23,399
    ( 2,   2,          '$'),   # corpus n=23,399
    ( 3,   3,          '?'),   # corpus n=392
    ( 4,   4,         '?!'),   # corpus n=0
    ( 5,   5,         '?.'),   # corpus n=0
    ( 6,   6,         '?~'),   # corpus n=0
    ( 7,   7,          '#'),   # corpus n=3,927
    ( 8,   8,          '['),   # corpus n=141,633
    ( 9,   9,          ']'),   # corpus n=98,305
    (10,  10,          'a'),   # corpus n=180,321
    (11,  11,          'i'),   # corpus n=115,305
    (12,  12,          'u'),   # corpus n=81,816
    (13,  13,          'e'),   # corpus n=89,784
    (14,  14,          'o'),   # corpus n=157,941
    (15,  15,          'A'),   # corpus n=0
    (16,  16,          'I'),   # corpus n=16,072
    (17,  17,          'U'),   # corpus n=15,153
    (18,  18,          'E'),   # corpus n=0
    (19,  19,          'O'),   # corpus n=0
    (20,  26,        'N_m'),   # corpus n=2,327
    (21,  27,        'N_n'),   # corpus n=12,044
    (22,  28,       'N_ng'),   # corpus n=5,533
    (23,  29,   'N_uvular'),   # corpus n=10,756
    (24,  30,         'cl'),   # corpus n=14,749
    (25,  32,          'k'),   # corpus n=80,078
    (26,  33,         'ky'),   # corpus n=2,974
    (27,  34,         'kw'),   # corpus n=197
    (28,  35,          'g'),   # corpus n=25,567
    (29,  36,         'gy'),   # corpus n=948
    (30,  37,         'gw'),   # corpus n=165
    (31,  38,          't'),   # corpus n=66,402
    (32,  39,         'ty'),   # corpus n=168
    (33,  40,          'd'),   # corpus n=28,652
    (34,  41,         'dy'),   # corpus n=276
    (35,  42,          'p'),   # corpus n=4,124
    (36,  43,         'py'),   # corpus n=355
    (37,  44,          'b'),   # corpus n=13,036
    (38,  45,         'by'),   # corpus n=504
    (39,  46,         'ch'),   # corpus n=9,549
    (40,  47,         'ts'),   # corpus n=11,561
    (41,  48,          's'),   # corpus n=34,275
    (42,  49,         'sh'),   # corpus n=30,475
    (43,  50,          'z'),   # corpus n=6,843
    (44,  51,          'j'),   # corpus n=10,739
    (45,  53,          'f'),   # corpus n=4,074
    (46,  54,          'h'),   # corpus n=12,474
    (47,  55,         'hy'),   # corpus n=943
    (48,  56,          'v'),   # corpus n=526
    (49,  57,          'n'),   # corpus n=65,364
    (50,  58,         'ny'),   # corpus n=808
    (51,  59,          'm'),   # corpus n=37,750
    (52,  60,         'my'),   # corpus n=441
    (53,  61,          'r'),   # corpus n=54,362
    (54,  62,         'ry'),   # corpus n=1,605
    (55,  63,          'w'),   # corpus n=20,654
    (56,  64,          'y'),   # corpus n=12,464
)

V = len(VOCAB_TABLE)                                   # 57
TEACHER_TO_STUDENT: dict[int, int] = {t: s for s, t, _ in VOCAB_TABLE}
STUDENT_TO_TEACHER: tuple[int, ...] = tuple(t for _, t, _ in VOCAB_TABLE)
TOKENS: tuple[str, ...] = tuple(tok for _, _, tok in VOCAB_TABLE)

#: コーパス出現が 0 のトークン。埋め込み行が未学習のまま残る
UNSEEN_IN_CORPUS: tuple[str, ...] = tuple(
    tok for _, _, tok in VOCAB_TABLE if tok in ("?!", "?.", "?~", "A", "E", "O"))

#: 同値初期化する組（未学習の無声化母音 → 対応する有声母音）
TIE_INIT: dict[str, str] = {"A": "a", "E": "e", "O": "o"}

PAD, BOS, EOS = 0, 1, 2        # student index。teacher id と偶然一致する


def map_ids(teacher_ids):
    """教師の音素ID列 → 生徒の埋め込みインデックス列。

    未知の ID は**黙って落とさず例外にする**。落とすと音素が消えたまま
    それらしい音声が出て気づかない（D-009 と同じ壊れ方）。
    """
    import numpy as np

    arr = np.asarray(teacher_ids)
    flat = arr.reshape(-1)
    out = np.empty(flat.shape, dtype=np.int64)
    for i, v in enumerate(flat):
        t = int(v)
        if t not in TEACHER_TO_STUDENT:
            raise KeyError(f"教師ID {t} はデプロイ語彙 57 に無い（位置 {i}）")
        out[i] = TEACHER_TO_STUDENT[t]
    return out.reshape(arr.shape)
