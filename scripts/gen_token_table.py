"""K-7: 音素名 → 生徒インデックス の表を生成する。

⚠️ **手で書き写さない**（C-002 の入口）。`src/saanotts_jp/vocab.py` の
`TOKENS` から機械生成し、SHA-256 で照合する。

    uv run python scripts/gen_token_table.py
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from saanotts_jp.vocab import TOKENS  # noqa: E402

OUT = ROOT / "csrc" / "token_table.h"


def main() -> int:
    sha = hashlib.sha256("\n".join(TOKENS).encode("utf-8")).hexdigest()
    rows = "\n".join(f'    {{ "{t}", {i} }},' for i, t in enumerate(TOKENS))
    sha_bytes = ", ".join(f"0x{sha[i:i+2]}" for i in range(0, 64, 2))
    OUT.write_text(f'''/* 自動生成 — 手で編集しない。
 *
 *   uv run python scripts/gen_token_table.py
 *
 * 出典: src/saanotts_jp/vocab.py の TOKENS。**手で書き写さない**（C-002）。
 * ⚠️ 語彙が変わったら再生成すること。`make -C csrc label-ids` が SHA-256 で検出する。
 */
#ifndef SAAN_TOKEN_TABLE_H
#define SAAN_TOKEN_TABLE_H

#include <stdint.h>

#define LABEL_IDS_N_TOKENS {len(TOKENS)}

typedef struct {{ const char *name; int32_t id; }} saan_token_t;

static const saan_token_t kSaanTokens[LABEL_IDS_N_TOKENS] = {{
{rows}
}};

/* "\\n" で連結した TOKENS の SHA-256。ホスト側とずれたら再生成し忘れ。 */
static const uint8_t kSaanTokensSha256[32] = {{
    {sha_bytes}
}};

#endif /* SAAN_TOKEN_TABLE_H */
''', encoding="utf-8")
    print(f"{len(TOKENS)} トークン / SHA-256 {sha}")
    print(f"書き出した → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
