#!/usr/bin/env python3
"""A-2 追試: 経路 (b) でも A1/A2/A3 が音素ID列から決まるかを確認する。

A-2 本体 (`a2_prosody.py`) は canonical 経路 (a) `text_to_phoneme_ids_and_prosody`
で測ったが、A-1 / M-19 で「かな無し行は経路 (a) が北京語に誤ルーティングする」ことが
分かっている。中間表現が実際に通るのは経路 (b) = `JapanesePhonemizer` なので、
**経路 (b) でも prosody が ID 列の決定的な関数か**を別に確認する。

実行:
    uv run python scripts/a2_prosody_routeb.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import a2_prosody as A  # noqa: E402

from piper_plus_g2p.encode.encoder import PiperEncoder  # noqa: E402
from piper_plus_g2p.japanese import JapanesePhonemizer  # noqa: E402

OUT = pathlib.Path("/Users/s19447/Desktop/saanoTTS-jp/reports/a2_prosody_routeb.json")


def main() -> int:
    cfg = A.load_config()
    R = A.token_roles(cfg)
    ph = JapanesePhonemizer()
    enc = PiperEncoder(cfg["phoneme_id_map"])
    rows = A.load_cache()

    stats = {}
    for split in ("heldout", "embedded", "train"):
        sub = [r for r in rows if r["split"] == split]
        if split == "train":
            sub = sub[:5000]  # 時間の都合で先頭 5,000 行
        exact = tok_ok = tok_all = 0
        bad = []
        for r in sub:
            tokens, pros = ph.phonemize_with_prosody(r["text"])
            ids, pr = enc.encode_with_prosody(tokens, pros)
            pv = tuple((p.a1, p.a2, p.a3) if p else (0, 0, 0) for p in pr)
            dec = A.decode_prosody(list(ids), R)
            if dec == pv:
                exact += 1
            elif len(bad) < 5:
                bad.append(r["text"][:40])
            tok_ok += sum(1 for a, b in zip(dec, pv) if a == b)
            tok_all += len(pv)
        stats[split] = {
            "n": len(sub),
            "sentence_exact_pct": round(exact / len(sub) * 100, 3),
            "token_exact_pct": round(tok_ok / tok_all * 100, 4),
            "mismatch_examples": bad,
        }
        print(f"  {split:9s} n={len(sub):5d}  文 {stats[split]['sentence_exact_pct']:7.3f}%  "
              f"token {stats[split]['token_exact_pct']:8.4f}%")
    OUT.write_text(json.dumps(
        {"route": "JapanesePhonemizer + PiperEncoder.encode_with_prosody",
         "question": "A1/A2/A3 は音素ID列だけから決まるか",
         "decoder": "a2_prosody.decode_prosody()",
         "results": stats}, ensure_ascii=False, indent=1))
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
