"""K-7: `_DAN_MAP`（かな → 母音）を端末に持たせるための表を生成する。

K-4 の `suppress_unnatural_auxiliary_u_long_vowel` が使う**唯一の外部資源**。
⚠️ **手で書き写さない**（C-002）。pyopenjtalk-plus から機械生成する。

    uv run python scripts/gen_dan_table.py
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "csrc" / "dan_table.h"


def main() -> int:
    from pyopenjtalk import utils as pjt_utils
    dan = dict(sorted(pjt_utils._DAN_MAP.items()))
    rows = "\n".join(f'    {{ "{k}", \'{v}\' }},' for k, v in dan.items())
    OUT.write_text(f'''/* 自動生成 — 手で編集しない。
 *
 *   uv run python scripts/gen_dan_table.py
 *
 * 出典: pyopenjtalk-plus の `utils._DAN_MAP`。**手で書き写さない**（C-002）。
 * K-4 の `suppress_u_long` が使う唯一の外部資源（かな {len(dan)} 件）。
 */
#ifndef SAAN_DAN_TABLE_H
#define SAAN_DAN_TABLE_H

#include "accent.h"

#define K7_N_DAN {len(dan)}

static const accent_dan_t k7_dan_table[K7_N_DAN] = {{
{rows}
}};

#endif /* SAAN_DAN_TABLE_H */
''', encoding="utf-8")
    print(f"{len(dan)} 件 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
