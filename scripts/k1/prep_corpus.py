"""held-out コーパスから本文列 (3 列目) を取り出す。ヘッダ行は落とす (C-018)。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")
SRC = (_ROOT + "/data/splits/corpus_heldout.tsv")

rows = []
with open(SRC, encoding="utf-8") as f:
    header = f.readline().rstrip("\n").split("\t")
    assert header == ["source", "id", "text"], f"想定外のヘッダ: {header}"
    for ln in f:
        ln = ln.rstrip("\n")
        if not ln:
            continue
        parts = ln.split("\t")
        assert len(parts) == 3, f"列数が 3 でない: {parts[:2]}"
        rows.append(parts)

# ゲート: ヘッダ行が本文として紛れ込んでいないこと（C-018 の再発防止）
assert not any(r[2] == "text" for r in rows), "本文に 'text' が混入している"
# ゲート: 改行・タブが本文に残っていないこと（1 行 1 文の前提）
assert not any("\t" in r[2] for r in rows)

with open(os.path.join(_WORK, "heldout_text.txt"), "w", encoding="utf-8", newline="\n") as f:
    for r in rows:
        f.write(r[2] + "\n")
with open(os.path.join(_WORK, "heldout_ids.txt"), "w", encoding="utf-8", newline="\n") as f:
    for r in rows:
        f.write(r[1] + "\n")

n = len(rows)
chars = [len(r[2]) for r in rows]
print(f"n={n} 文")
print(f"先頭 3 件: {[r[2] for r in rows[:3]]}")
print(f"文字数 min={min(chars)} mean={sum(chars)/n:.2f} max={max(chars)}")
