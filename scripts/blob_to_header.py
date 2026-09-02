#!/usr/bin/env python3
"""重み blob を C のヘッダ（`const` 配列）に変換する。

出所: nnn112358/SanoTTS-jp-M5StackCoreS3（MIT, Copyright (c) 2026 nnn112358）の
scripts/blob_to_header.py を取り込んだもの。本リポジトリでは
esp32/main/CMakeLists.txt が `-DSAAN_MODEL_RODATA=1` のときビルド時に呼ぶ
（生成物はビルドディレクトリ。ソースには置かない）。回帰は scripts/test_blob_to_header.py。

    uv run --no-project python scripts/blob_to_header.py \
        --blob csrc/student_i8.bin --out build/saan_model_blob.h

⚠️ **int8 blob のみ受け付ける。** fp32 を渡すと落ちる（--allow-fp32 で解除）。

⚠️ **`const` を外さないこと。** ESP32-S3 では
   `const` → `.rodata` → **flash**（XIP、SRAM 消費 0）
   非 `const` → `.data` → **DIRAM**（654,032 B。残り 18,662 B しかないので即リンクエラー）
   実測で確認済み（`xtensa-esp32s3-elf-size -A`）。

⚠️ **16 バイト境界を明示する。** コアは payload を `const float*` に直接
   キャストする。Xtensa は非アラインの 4 バイトロードで LoadStoreAlignmentCause。
   素の `uint8_t[]` はアライメント 1 なので、属性が無いと**リンク結果次第で
   たまたま揃い、無関係な変更で突然落ちる**。

⚠️ **このヘッダは配列の定義を持つ。** 2 つ以上の翻訳単位から include すると
   リンク時に重複定義で落ちる（= 黙って flash が 2 倍になることはない）。
   include してよいのは `saan_model.c` だけ。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import struct
import sys

PER_LINE = 16


def blob_dtype(data: bytes) -> str:
    """SAAN v1 のヘッダを覗いて、int8 blob か fp32 blob かを見る。

    int8 blob だけが `<name>.scale` テンソルを持つ（fp32 blob は 0 個）。
    main.c の W8A8 ガードと同じ判定基準。
    """
    try:
        if data[:4] != b"SAAN":
            return "unknown"
        n_tensors = struct.unpack_from("<I", data, 8)[0]
        return "int8" if b".scale" in data[: 64 + n_tensors * 128] else "fp32"
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--symbol", default="g_saan_model_blob")
    # ⚠️ **本プロジェクトの実装は int8 のみ。** fp32 blob を渡しても
    #    W8A8/PIE は 1 命令も効かず（saan_conv1d_w が W.f32 で早期 return）、
    #    flash も 3.5 倍食う。**焼いてから気づくのを防ぐためビルド時に落とす。**
    ap.add_argument("--allow-fp32", action="store_true",
                    help="fp32 blob を敢えて通す（既定は拒否）")
    args = ap.parse_args()

    if not args.blob.exists():
        print(f"NG! blob が無い: {args.blob}", file=sys.stderr)
        return 1

    data = args.blob.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    dtype = blob_dtype(data)

    if dtype != "int8" and not args.allow_fp32:
        print(f"NG! blob の dtype が {dtype} — **この実装は int8 のみ**。\n"
              f"    {args.blob}\n"
              f"    csrc/student_i8.bin か、リリースの saanotts-jp-v3-int8.bin を"
              f"指すこと。\n"
              f"    どうしても通すなら --allow-fp32（推奨しない）",
              file=sys.stderr)
        return 1

    out = []
    out.append("/* 自動生成 — 編集しない (scripts/blob_to_header.py) */")
    out.append(f"/*   元 blob : {args.blob.name}")
    out.append(f" *   サイズ  : {len(data):,} B")
    out.append(f" *   SHA-256 : {sha}")
    out.append(f" *   dtype   : {dtype}")
    out.append(" *")
    out.append(" * ⚠️ `const` = .rodata = flash。SRAM は消費しない。")
    out.append(" * ⚠️ include してよいのは saan_model.c だけ（配列の定義を持つため）。")
    out.append(" */")
    out.append("#ifndef SAAN_MODEL_BLOB_H")
    out.append("#define SAAN_MODEL_BLOB_H")
    out.append("")
    out.append("#include <stdint.h>")
    out.append("")
    out.append(f"#define SAAN_MODEL_BLOB_BYTES {len(data)}u")
    out.append(f'#define SAAN_MODEL_BLOB_SHA256 "{sha}"')
    out.append(f'#define SAAN_MODEL_BLOB_DTYPE "{dtype}"')
    out.append("")
    out.append(f"const uint8_t {args.symbol}[SAAN_MODEL_BLOB_BYTES]")
    out.append("    __attribute__((aligned(16))) = {")

    body = []
    for i in range(0, len(data), PER_LINE):
        body.append("".join(f"0x{c:02x}," for c in data[i : i + PER_LINE]))
    out.append("\n".join(body))

    out.append("};")
    out.append("")
    out.append(f"_Static_assert(sizeof({args.symbol}) == SAAN_MODEL_BLOB_BYTES,")
    out.append('               "blob のサイズが宣言と食い違っている");')
    out.append("")
    out.append("#endif /* SAAN_MODEL_BLOB_H */")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out) + "\n")

    print(f"{args.out}: {len(data):,} B / dtype {dtype} / sha256 {sha[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
