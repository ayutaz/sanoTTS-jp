#!/usr/bin/env python3
"""esp32/main/demo_ids.h を csrc/golden.bin から生成する（c'-4）。

雛形は**中間表現の文字列を `saan_g2p()` に通して**音を出す。ここが吐く
`kSaanDemoIds` は**その答え合わせの錨**で、実機で G2P の出力と突き合わせる。

⚠️ **中間表現もここで作る。** 手で書くと、C の G2P が間違っているのか
中間表現が間違っているのか区別できなくなる。`kana_g2p.text_to_intermediate()`
（ホスト側・OpenJTalk）で作り、**それを符号化すると golden.bin の in.ids に
なることを assert する**。

golden.bin の `in.ids` は **すでに生徒インデックス**（0..56）なので、
`TEACHER_TO_STUDENT` を通す必要はない。ここでそれを assert する。

    uv run python scripts/gen_demo_ids.py
"""
from __future__ import annotations

import json
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

sys.path.insert(0, str(ROOT / "scripts"))

import gen_g2p_vectors as GV  # noqa: E402
import kana_g2p as K  # noqa: E402
from saanotts_jp.vocab import VOCAB_TABLE  # noqa: E402

HDR_ENT = 64 + 4 + 4 + 16 + 8 + 8


def read_saan(path: pathlib.Path) -> dict[str, tuple[int, tuple[int, ...], bytes]]:
    b = path.read_bytes()
    if b[:4] != b"SAAN":
        raise SystemExit(f"{path}: SAAN ヘッダでない")
    (version, n) = struct.unpack_from("<II", b, 4)
    if version != 1:
        raise SystemExit(f"{path}: version {version} は未対応")
    out = {}
    for i in range(n):
        e = 16 + i * HDR_ENT
        name = b[e : e + 64].split(b"\0", 1)[0].decode()
        dtype = struct.unpack_from("<I", b, e + 64)[0]
        dims = struct.unpack_from("<4I", b, e + 64 + 8)
        off, nb = struct.unpack_from("<QQ", b, e + 64 + 8 + 16)
        out[name] = (dtype, dims, b[off : off + nb])
    return out


def main() -> int:
    golden = ROOT / "csrc" / "golden.bin"
    export = json.loads((ROOT / "csrc" / "export.json").read_text())
    t = read_saan(golden)
    if "in.ids" not in t:
        raise SystemExit("golden.bin に in.ids が無い")
    _dtype, _dims, payload = t["in.ids"]
    ids = [int(v) for v in struct.unpack(f"<{len(payload) // 4}f", payload)]

    vocab = {idx: tok for idx, _teacher, tok in VOCAB_TABLE}
    n_vocab = len(VOCAB_TABLE)

    # --- 生徒インデックスであることの検査（教師IDだと 173 まで出る） ---
    bad = [v for v in ids if not (0 <= v < n_vocab)]
    if bad:
        raise SystemExit(f"語彙外の id がある（教師IDを渡していないか）: {bad[:10]}")

    text = export.get("golden", {}).get("text", "")
    toks = " ".join(vocab[v] for v in ids)

    # --- 中間表現（端末が実際に受け取る入力）------------------------------
    # ⚠️ **これが in.ids に一致しなければ、雛形は golden と別の音を出す。**
    #    手で書いた文字列を置くと、C の G2P のバグと見分けがつかなくなる。
    table = K.build_mora_table()
    GV.sanity_checks(table)
    intermediate = "".join(K.text_to_intermediate(text, table))
    enc = GV.encode(intermediate, table)
    if enc["kind"] != GV.KIND_OK:
        raise SystemExit(f"中間表現が端末側で符号化できない: {intermediate!r}")
    if enc["ids"] != ids:
        raise SystemExit(
            "中間表現の符号化が golden.bin の in.ids と一致しない\n"
            f"  中間表現: {intermediate}\n"
            f"  符号化   : {enc['ids'][:20]} ... ({len(enc['ids'])} 個)\n"
            f"  in.ids   : {ids[:20]} ... ({len(ids)} 個)")
    mid_bytes = intermediate.encode("utf-8")
    if '"' in intermediate or "\\" in intermediate:
        raise SystemExit(f"中間表現に C 文字列で escape が要る文字がある: {intermediate!r}")

    lines = [
        "/* 自動生成 — 手で編集しない。",
        " *",
        " *   uv run python scripts/gen_demo_ids.py",
        " *",
        f" * 出典: csrc/golden.bin の in.ids（{golden.name} sha256 は csrc/export.json）",
        f" * 原文: {text}",
        " *",
        " * ⚠️ これは**生徒インデックス**（0..%d）であって教師の音素ID ではない。" % (n_vocab - 1),
        " *    生徒は教師の ID 空間を直接使えない（D-016 / src/saanotts_jp/vocab.py）。",
        " *",
        " *",
        " * SAAN_DEMO_INTERMEDIATE が**端末が実際に受け取る入力**で、",
        " * csrc/g2p.c の saan_g2p() に通すと kSaanDemoIds になる。",
        " * ⚠️ kSaanDemoIds は**答え合わせの錨**であって入力ではない。",
        " *    esp32/main/main.c は G2P の出力の方を合成に使う。",
        " */",
        "#ifndef SAAN_DEMO_IDS_H",
        "#define SAAN_DEMO_IDS_H",
        "",
        "#include <stdint.h>",
        "",
        f'#define SAAN_DEMO_TEXT "{text}"',
        "",
        "/* 端末側 G2P の入力。ひらがな + `[` 上昇 / `]` 下降核 / `#` 句境界 / `°` 無声化 */",
        f'#define SAAN_DEMO_INTERMEDIATE "{intermediate}"',
        f"#define SAAN_DEMO_INTERMEDIATE_BYTES {len(mid_bytes)}",
        f"#define SAAN_DEMO_N_IDS {len(ids)}",
        "",
        "/* 音素列: " + toks,
        " */",
        f"static const int32_t kSaanDemoIds[SAAN_DEMO_N_IDS] = {{",
    ]
    for i in range(0, len(ids), 12):
        lines.append("    " + ", ".join(f"{v:2d}" for v in ids[i : i + 12]) + ",")
    lines += ["};", "", "#endif /* SAAN_DEMO_IDS_H */", ""]

    out = ROOT / "esp32" / "main" / "demo_ids.h"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"{out}: {len(ids)} ids / 値域 {min(ids)}..{max(ids)} / 語彙 {n_vocab}")
    print(f"  原文        : {text}")
    print(f"  中間表現    : {intermediate} ({len(mid_bytes)} B)")
    print(f"  音素        : {toks}")
    print("  OK  中間表現を符号化すると golden.bin の in.ids と完全一致した")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
