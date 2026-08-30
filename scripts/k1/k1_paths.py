"""K-1 の測定スクリプトが使うパス解決。

⚠️ これらのスクリプトは元々セッションの scratchpad で書かれ、絶対パスを直書きしていた。
リポジトリへ移設する際に、この 1 箇所に集約した。

- `ROOT` : リポジトリのルート（このファイルの 2 つ上）
- `WORK` : 中間生成物の置き場。**リポジトリに入れない**（entries.tsv は 90 MB、
           trie キャッシュは 54 MB ある）。既定は `<ROOT>/.k1work`、`K1_WORK` で上書き可
- `DICT_VENV` : pyopenjtalk が実行時に実際に引く辞書（789,388 entries）
- `DICT_PP`   : piper-plus の build ツリー側（788,923 entries）。**別リビジョン**

⚠️ このマシンには sys.dic が 4 種類ある。**どれを使ったかを必ず出力に書くこと**
（docs/research/k1-kanji-katakana-ondevice.md §9-2）。
"""
from __future__ import annotations

import os
import pathlib

ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)
WORK = os.environ.get("K1_WORK", os.path.join(ROOT, ".k1work"))
os.makedirs(WORK, exist_ok=True)

PP = os.environ.get("PIPER_PLUS_ROOT", os.path.expanduser("~/Documents/piper-plus"))
DICT_PP = os.path.join(PP, "build/share/open_jtalk/dic")

HELDOUT = os.path.join(ROOT, "data/splits/corpus_heldout.tsv")
TRAIN = os.path.join(ROOT, "data/splits/corpus_train.tsv")

DICT_VENV = None
try:
    import pyopenjtalk as _pjt
    DICT_VENV = _pjt.OPEN_JTALK_DICT_DIR.decode()
except Exception:
    pass
