"""`scripts/b_durations_all.py` が出した全行 duration の読み込み。

B-4（長さフィルタ）/ B-7（`s_v` 較正）/ B-8（`_` PAD）が共通で使う。

⚠️ **`dT` は生の float `w`。フレーム数は `ceil(dT)`**（`models.py` の
`w_ceil = torch.ceil(w)` と同じ）。`round` ではない。ここを取り違えると
教師と生徒のフレーム勘定が全部ずれる。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np

SR = 22050
HOP = 256


@dataclass
class Durations:
    ids: np.ndarray          # 連結された音素ID (int16)
    dT: np.ndarray           # 連結された生 duration (float32)
    offsets: np.ndarray      # [n+1]
    index: list[dict]        # split / uid / text / n_tokens / n_ids / frames
    meta: dict

    def __len__(self) -> int:
        return len(self.index)

    def utt(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        a, b = self.offsets[i], self.offsets[i + 1]
        return self.ids[a:b], self.dT[a:b]

    @property
    def id2tok(self) -> dict[int, str]:
        return {int(k): v for k, v in self.meta["id_to_phoneme"].items()}


DEFAULT_EXCLUSIONS = "data/splits/exclusions_teacher_ft.txt"


def load_exclusions(path: str = DEFAULT_EXCLUSIONS) -> set[str]:
    """B-10 の除外 uid（教師の FT テキストと重複する行）。"""
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    return {l.split("\t")[0] for l in p.read_text().splitlines()
            if l.strip() and not l.startswith("#")}


def load(root: str | pathlib.Path = "reports/durations",
         exclude: bool = True) -> Durations:
    """`exclude=True` で B-10 の汚染行を落とす（既定）。"""
    root = pathlib.Path(root)
    z = np.load(root / "durations.npz")
    index = [json.loads(l) for l in open(root / "index.jsonl")]
    meta = json.load(open(root / "meta.json"))
    ids, dT, offsets = z["ids"], z["dT"], z["offsets"]
    assert len(offsets) == len(index) + 1, (len(offsets), len(index))
    assert offsets[-1] == len(ids) == len(dT)
    for i, r in enumerate(index):
        assert r["off"] == offsets[i]

    dropped = 0
    if exclude:
        bad = load_exclusions()
        keep = [i for i, r in enumerate(index) if r["uid"] not in bad]
        dropped = len(index) - len(keep)
        if dropped:
            new_ids, new_d, new_index, off = [], [], [], 0
            for i in keep:
                a, b = offsets[i], offsets[i + 1]
                new_ids.append(ids[a:b]); new_d.append(dT[a:b])
                r = dict(index[i]); r["off"] = off; new_index.append(r)
                off += b - a
            ids = np.concatenate(new_ids); dT = np.concatenate(new_d)
            offsets = np.array([r["off"] for r in new_index] + [off], dtype=np.int64)
            index = new_index
    meta = dict(meta)
    meta["n_excluded_contaminated"] = dropped
    return Durations(ids=ids, dT=dT, offsets=offsets, index=index, meta=meta)
