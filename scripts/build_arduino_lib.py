#!/usr/bin/env python3
"""`csrc/` と `esp32/main/` から **Arduino / PlatformIO ライブラリ**を組む。

## なぜ生成するのか

Arduino のライブラリ仕様（1.5 format）は **`src/` の下に実体が無いとコンパイルしない**
（`src/` とその全サブフォルダを再帰的にコンパイルする）。一方このリポジトリは
`esp32/components/saanotts_core/CMakeLists.txt` が明示している通り
**「csrc/ をコピーもシンボリックリンクもしない」**を方針にしている
（同じものが 2 か所にあると、片方だけ直したときに golden test がどちらを見ているか
分からなくなる）。

折衷点がこのスクリプト: **実体は生成物にして git には置かない**（`arduino/src/core/` は
`.gitignore`）。生成規則を 1 つに絞り、`--check` が**逆向きに**それを証明する。

## 生成規則（これだけ）

各出力ファイルは

    <前置き>  #include "…/sanotts_config.h"  と、必要なら #if <guard>
    <元ファイルの逐語バイト列>
    <後置き>  必要なら #endif

`--check` は出力から前置き / 後置きを**剥がして**、元ファイルと `bytes` で比べる。
さらに**ファイルの集合も比べる**（余計なものが混ざっても、欠けても落ちる）。

## なぜ前置きが要るのか

⚠️ **Arduino のライブラリは独自の `-D` を渡せない**（仕様に無い）。ESP-IDF 版で
CMake が渡していた `SAAN_INT8_ACT` / `SAAN_PIE` / `CHARSET_UTF_8` /
`LABEL_IDS_EXTERNAL_SCRATCH` / `SAAN_PORT_HEADER` は、**各翻訳単位の先頭で
`sanotts_config.h` を読む**以外に届ける手段が無い。

⚠️ **`#if SANOTTS_ENABLE_KANJI` も前置きの仕事。** Arduino は `src/` を再帰的に
コンパイルするので、**漢字を使わない構成でも Open JTalk 14 本が必ずビルドされる**。
自分で消すしかない。

⚠️ **取り込んだ Open JTalk への前置きは「改変」である。** `k4b_vendor.py --check` は
`csrc/openjtalk/` を見るので通るが、`arduino/NOTICE.txt` に明記すること。
中身が逐語であることはこのスクリプトの `--check` が毎回証明する。

## 使い方

    uv run --no-project --python 3.12 python scripts/build_arduino_lib.py
    uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --check
    uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --self-test
    uv run --no-project --python 3.12 python scripts/build_arduino_lib.py \
        --zip arduino/dist --version 1.1.0 --blob /path/to/saanotts-jp-v4-int8.bin

⚠️ **stdlib しか使わない**（`--no-project` で回るため）。
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSRC = ROOT / "csrc"
MAIN = ROOT / "esp32" / "main"
COMP = ROOT / "esp32" / "components" / "saanotts_core"
ARDUINO = ROOT / "arduino"

# --- guard 式 ----------------------------------------------------------------
#
# ⚠️ **`SANOTTS_*` はユーザーが触る面、`SAAN_*` はコアの内部フラグ。**
#    混ぜると「ユーザーが SAAN_PIE だけ立てた」形が作れてしまう（CLAUDE.md）。
G_KANJI = "SANOTTS_ENABLE_KANJI"
# ⚠️ **重みライブラリが無いときは 1 行もコンパイルしない。** `saan_model_rodata.c` は
#    `saan_model_blob.h`（重みライブラリ側）を include するので、無い環境では
#    「そんなヘッダは無い」で落ちる。代わりに `SanoTTSModelStub.c` が
#    `saan_model_open()` を「重みが無い」を返す実装で埋める。
G_RODATA = "!SANOTTS_MODEL_FROM_PARTITION && SANOTTS_HAVE_VOICE"
G_PART = "SANOTTS_MODEL_FROM_PARTITION"

# 取り込んだ Open JTalk の一時ヒープを PSRAM に向ける前置き。
# ESP-IDF 版は `set_source_files_properties(... -include oj_heap_psram.h)` でやっている。
# ⚠️ **`oj_heap_psram.c` 自身には当てない**（当てると無限再帰）。
OJ_HEAP_INCLUDE = '#include "../oj_heap_psram.h"'


class Entry(NamedTuple):
    out: str  # arduino/src/core/ からの相対パス
    src: pathlib.Path
    wrap: bool  # 前置きを付けるか（.c は True / .h と資料は False = 逐語）
    guard: str | None


def manifest() -> list[Entry]:
    """取り込む一覧。**ここが唯一の真実。** `--check` は集合もこれと比べる。"""
    e: list[Entry] = []

    # --- 常に入る .c -------------------------------------------------------
    for name in ("saanotts.c", "saanotts_stream.c", "fft.c", "saanotts_int8.c", "g2p.c"):
        e.append(Entry(name, CSRC / name, True, None))
    e.append(Entry("saan_pcm.c", MAIN / "saan_pcm.c", True, None))

    # --- 重みの置き場（排他。**どちらか片方だけが生きる**）----------------
    e.append(Entry("saan_model_rodata.c", MAIN / "saan_model_rodata.c", True, G_RODATA))
    e.append(Entry("saan_model.c", MAIN / "saan_model.c", True, G_PART))

    # --- 漢字の .c ---------------------------------------------------------
    for name in ("jdict.c", "accent.c", "njd_rules.c", "label_ids.c"):
        e.append(Entry(name, CSRC / name, True, G_KANJI))
    e.append(Entry("saan_kanji.c", MAIN / "saan_kanji.c", True, G_KANJI))
    e.append(Entry("saan_dict.c", MAIN / "saan_dict.c", True, G_KANJI))
    e.append(Entry("oj_heap_psram.c", COMP / "oj_heap_psram.c", True, G_KANJI))

    # --- ヘッダは逐語（前置きを入れない）----------------------------------
    #
    # ⚠️ ヘッダに前置きを入れるとインクルードガードの**前**にコードが来て、
    #    多重 include のたびに config を読むことになる。害は無いが差分検証が複雑になるので
    #    入れない。フラグは .c 側の前置きで既に立っている。
    for name in ("saanotts.h", "saanotts_internal.h", "saanotts_int8.h", "saanotts_stream.h",
                 "fft.h", "g2p.h", "g2p_table.h", "erf_table.h", "token_table.h", "saan_prof.h",
                 "jdict.h", "accent.h", "njd_rules.h", "label_ids.h", "dan_table.h",
                 "oj_heap_psram.h"):
        e.append(Entry(name, CSRC / name, False, None))
    for name in ("saan_pcm.h", "saan_model.h", "saan_kanji.h", "saan_dict.h"):
        e.append(Entry(name, MAIN / name, False, None))
    # T5-G2: erf 表を内部 DRAM に置くための配置属性。`saanotts_internal.h` が
    # SAAN_PORT_HEADER 経由で読む（sanotts_config.h が定義する）。
    e.append(Entry("saan_port_esp32.h", COMP / "saan_port_esp32.h", False, None))

    # --- 取り込んだ Open JTalk ---------------------------------------------
    oj = CSRC / "openjtalk"
    for p in sorted(oj.glob("*.c")):
        e.append(Entry(f"openjtalk/{p.name}", p, True, G_KANJI))
    for p in sorted(oj.glob("*.h")):
        e.append(Entry(f"openjtalk/{p.name}", p, False, None))
    # ⚠️ **ライセンスと出所は必ず一緒に運ぶ。**
    for name in ("COPYING", "PROVENANCE.md"):
        e.append(Entry(f"openjtalk/{name}", oj / name, False, None))

    return e


def _prologue(entry: Entry) -> bytes:
    """前置き。⚠️ **`--check` はこれと 1 バイト単位で照合する。**"""
    up = "../" * (entry.out.count("/") + 1)
    rel = entry.src.relative_to(ROOT).as_posix()
    lines = [
        "/* 生成物 — scripts/build_arduino_lib.py が作る。**手で編集しない。**",
        f" * 元: {rel}",
        " * 規則: この前置きと（あれば）末尾の後置きを剥がすと、元ファイルと **bit 一致**する。",
        " *       `build_arduino_lib.py --check` が毎回それを確かめる。 */",
        f'#include "{up}sanotts_config.h"',
        # ⚠️ **Arduino の既定は -Os で、ESP-IDF 版の -O2 と違う**
        #    （sdkconfig.defaults の CONFIG_COMPILER_OPTIMIZATION_PERF=y）。
        #    実測: saanotts_int8.c の PIE 命令が **-O2 で 74 / -Os で 67**（GCC 8.4 と 14.2 の両方）。
        #    ⚠️ **Arduino IDE にはライブラリ単位の最適化フラグが無い**ので pragma で入れる。
        #    実測で -Os + この pragma = -O2 と同じ 74 命令になることを確かめてある。
        #    ⚠️ これが消えると**ビルドは通り音も出るが遅くなる**。G-AR6 が命令数を見る。
        '#pragma GCC optimize("O2")',
    ]
    if entry.out.startswith("openjtalk/") and entry.out.endswith(".c"):
        # ⚠️ ESP-IDF 版の `-include oj_heap_psram.h` と同じ効果。Arduino では
        #    ファイル単位のコンパイルオプションを渡せないので前置きで当てる。
        lines.append(OJ_HEAP_INCLUDE)
    if entry.guard:
        lines.append(f"#if {entry.guard}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _epilogue(entry: Entry) -> bytes:
    if not entry.guard:
        return b""
    return f"\n#endif /* {entry.guard} */\n".encode("utf-8")


def generate(src_dir: pathlib.Path) -> int:
    """`src_dir/core/` を作り直す。戻り値は書いたファイル数。"""
    core = src_dir / "core"
    if core.exists():
        shutil.rmtree(core)
    n = 0
    for entry in manifest():
        if not entry.src.exists():
            raise SystemExit(f"NG! 取り込み元が無い: {entry.src}")
        dst = core / entry.out
        dst.parent.mkdir(parents=True, exist_ok=True)
        body = entry.src.read_bytes()
        dst.write_bytes(_prologue(entry) + body + _epilogue(entry) if entry.wrap else body)
        n += 1
    return n


def verify(src_dir: pathlib.Path) -> list[str]:
    """G-AR1。**逆向きに**証明する — 前置きと後置きを剥がして元と比べる。"""
    core = src_dir / "core"
    problems: list[str] = []
    if not core.is_dir():
        return [f"{core} が無い（先に生成すること）"]

    want: set[str] = set()
    for entry in manifest():
        want.add(entry.out)
        dst = core / entry.out
        if not dst.exists():
            problems.append(f"欠けている: core/{entry.out}")
            continue
        got = dst.read_bytes()
        body = entry.src.read_bytes()
        if not entry.wrap:
            if got != body:
                problems.append(f"逐語のはずが違う: core/{entry.out}")
            continue
        pro, epi = _prologue(entry), _epilogue(entry)
        if not got.startswith(pro):
            problems.append(f"前置きが違う（消されたか、規則が変わった）: core/{entry.out}")
            continue
        if epi and not got.endswith(epi):
            problems.append(f"後置きが違う: core/{entry.out}")
            continue
        mid = got[len(pro):len(got) - len(epi)] if epi else got[len(pro):]
        if mid != body:
            problems.append(
                f"本文が {entry.src.relative_to(ROOT)} と一致しない: core/{entry.out}")

    # ⚠️ **集合も比べる。** 一致検査だけだと、余計なファイルが混ざっても通る。
    have = {p.relative_to(core).as_posix() for p in core.rglob("*") if p.is_file()}
    for extra in sorted(have - want):
        problems.append(f"一覧に無いファイルがある: core/{extra}")
    return problems


# --- 陽性対照 ----------------------------------------------------------------
#
# ⚠️ **「OK」が空虚でないことを、落ちるべきものが落ちることで示す。**
def self_test() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        def fresh() -> None:
            generate(tmp)

        fresh()
        if verify(tmp):
            fails.append(f"(1) 素の生成が --check を通らない: {verify(tmp)[:2]}")

        # (2) 本文を 1 バイト変える
        fresh()
        v = tmp / "core" / "saanotts.c"
        v.write_bytes(v.read_bytes().replace(b"saan_", b"saaN_", 1))
        if not verify(tmp):
            fails.append("(2) 本文を 1 バイト変えても --check が通った")

        # (3) 前置きを消す
        fresh()
        v = tmp / "core" / "saanotts.c"
        v.write_bytes(v.read_bytes().split(b"\n", 5)[-1])
        if not verify(tmp):
            fails.append("(3) 前置きを消しても --check が通った")

        # (4) 後置き（#endif）を消す — 漢字ファイルが素通りで常時コンパイルされる形
        fresh()
        v = tmp / "core" / "jdict.c"
        b = v.read_bytes()
        assert b.endswith(b"*/\n"), "後置きの形が変わった"
        v.write_bytes(b[: b.rindex(b"\n#endif")])
        if not verify(tmp):
            fails.append("(4) 後置きを消しても --check が通った")

        # (5) ファイルを消す
        fresh()
        (tmp / "core" / "fft.c").unlink()
        if not verify(tmp):
            fails.append("(5) ファイルを消しても --check が通った")

        # (6) 一覧に無いファイルを足す
        fresh()
        (tmp / "core" / "intruder.c").write_text("int intruder;\n")
        if not verify(tmp):
            fails.append("(6) 一覧に無いファイルがあっても --check が通った")

        # (7) 取り込んだ Open JTalk の前置きから oj_heap_psram.h を落とす
        #     （= 一時ヒープが黙って内部 DRAM に戻る形。PSRAM 無しの板で M-98 の穴に落ちる）
        fresh()
        v = tmp / "core" / "openjtalk" / "njd.c"
        v.write_bytes(v.read_bytes().replace(OJ_HEAP_INCLUDE.encode() + b"\n", b"", 1))
        if not verify(tmp):
            fails.append("(7) Open JTalk の oj_heap_psram.h 前置きを消しても --check が通った")

    for f in fails:
        print(f"  NG  {f}")
    print(f"{'NG!' if fails else 'OK '} 陽性対照 7 件"
          f"（落ちるべきものが落ちた: {7 - len(fails)}/7）")
    return 1 if fails else 0


# --- .zip --------------------------------------------------------------------
def _zip_dir(zf: zipfile.ZipFile, top: str, base: pathlib.Path,
             skip: set[str] = frozenset()) -> int:
    n = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        if rel in skip or rel.split("/")[0] in skip or p.name == ".DS_Store":
            continue
        zf.write(p, f"{top}/{rel}")
        n += 1
    return n


def make_zips(out_dir: pathlib.Path, version: str, blob: pathlib.Path | None) -> int:
    """リリース資産 2 本。**コード（MIT）と重み（LicenseRef）を分ける。**

    ⚠️ **PlatformIO は git のサブディレクトリを指せない**（library.json は直下必須）ので、
       `lib_deps` に書けるのはこの .zip の直 URL だけ。Arduino IDE も同じ .zip を使う。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    generate(ARDUINO / "src")

    code = out_dir / f"sanoTTS-jp-arduino-{version}.zip"
    with zipfile.ZipFile(code, "w", zipfile.ZIP_DEFLATED) as zf:
        n = _zip_dir(zf, "SanoTTS-jp", ARDUINO, skip={"dist"})
        for extra in ("LICENSE", "NOTICE-openjtalk.txt"):
            p = ROOT / extra
            if p.exists():
                zf.write(p, f"SanoTTS-jp/{extra}")
                n += 1
    print(f"{code.relative_to(ROOT)}: {code.stat().st_size:,} B / {n} files")

    if blob is None:
        print("⚠️ --blob が無いので重みの .zip は作っていない")
        return 0

    voice = out_dir / f"sanoTTS-jp-voice-tsukuyomi-v4-{version}.zip"
    with tempfile.TemporaryDirectory() as td:
        stage = pathlib.Path(td) / "SanoTTS-jp-voice-tsukuyomi-v4"
        (stage / "src").mkdir(parents=True)
        # ⚠️ **blob → ヘッダの変換は既存の 1 本しかない実装を使う**（形がずれない）。
        subprocess.run([sys.executable, str(ROOT / "scripts" / "blob_to_header.py"),
                        "--blob", str(blob), "--out", str(stage / "src" / "saan_model_blob.h")],
                       check=True)
        sha = hashlib.sha256(blob.read_bytes()).hexdigest()
        (stage / "src" / "saanotts_jp_voice.h").write_text(_VOICE_HEADER.format(
            bytes_=blob.stat().st_size, sha=sha), encoding="utf-8")
        (stage / "library.properties").write_text(
            _VOICE_PROPS.format(version=version), encoding="utf-8")
        (stage / "library.json").write_text(
            _VOICE_JSON.format(version=version), encoding="utf-8")
        for name in ("LICENSE-MODEL.md", "MODEL_CARD.md"):
            shutil.copy2(ROOT / name, stage / name)
        shutil.copy2(ARDUINO / "NOTICE.txt", stage / "NOTICE.txt")
        with zipfile.ZipFile(voice, "w", zipfile.ZIP_DEFLATED) as zf:
            n = _zip_dir(zf, "SanoTTS-jp-voice-tsukuyomi-v4", stage)
    print(f"{voice.relative_to(ROOT)}: {voice.stat().st_size:,} B / {n} files")
    return 0


_VOICE_HEADER = '''/* sanoTTS-jp の重み（つくよみちゃん / v4）— 小さい公開ヘッダ。
 *
 * ⚠️ **配列の実体は saan_model_blob.h が持つ。** あれを include するのは
 *    `core/saan_model_rodata.c` **だけ**（2 か所から include するとリンクが落ちる）。
 * ⚠️ **このヘッダのライセンスは MIT ではない。** LicenseRef-sanoTTS-jp-Model-1.0
 *    （同梱の LICENSE-MODEL.md）。出力の用途制限 4 項目とコピーレフトがある。
 */
#ifndef SAANOTTS_JP_VOICE_H
#define SAANOTTS_JP_VOICE_H

#include <stddef.h>
#include <stdint.h>

#define SAANOTTS_JP_VOICE_BYTES  {bytes_}u
#define SAANOTTS_JP_VOICE_SHA256 "{sha}"

#ifdef __cplusplus
extern "C" {{
#endif
extern const uint8_t g_saan_model_blob[SAANOTTS_JP_VOICE_BYTES];
#ifdef __cplusplus
}}
#endif

#endif /* SAANOTTS_JP_VOICE_H */
'''

_VOICE_PROPS = """name=SanoTTS-jp-voice-tsukuyomi-v4
version={version}
author=ayutaz
maintainer=ayutaz
sentence=Voice weights for SanoTTS-jp (Tsukuyomi-chan, v4, int8).
paragraph=NOT MIT. These weights are licensed under LicenseRef-sanoTTS-jp-Model-1.0 (see LICENSE-MODEL.md): attribution is required, four output restrictions apply, and the license is copyleft. Read LICENSE-MODEL.md before shipping a product.
category=Signal Input/Output
url=https://github.com/ayutaz/sanoTTS-jp
architectures=esp32
includes=saanotts_jp_voice.h
"""

_VOICE_JSON = """{{
  "name": "SanoTTS-jp-voice-tsukuyomi-v4",
  "version": "{version}",
  "description": "Voice weights for SanoTTS-jp (Tsukuyomi-chan, v4, int8). NOT MIT \\u2014 see LICENSE-MODEL.md.",
  "keywords": ["tts", "japanese", "esp32", "weights"],
  "repository": {{ "type": "git", "url": "https://github.com/ayutaz/sanoTTS-jp.git" }},
  "license": "LicenseRef-sanoTTS-jp-Model-1.0",
  "frameworks": ["arduino", "espidf"],
  "platforms": ["espressif32"],
  "headers": ["saanotts_jp_voice.h"],
  "build": {{ "srcDir": "src", "libArchive": false }}
}}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="G-AR1: 生成物が csrc/esp32 の逐語か（生成はしない）")
    ap.add_argument("--self-test", action="store_true", help="陽性対照 7 件")
    ap.add_argument("--zip", type=pathlib.Path, metavar="DIR", help="リリース .zip を組む")
    ap.add_argument("--version", default="0.0.0", help="--zip のときの版")
    ap.add_argument("--blob", type=pathlib.Path, help="--zip のときの重み blob（int8）")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    if a.check:
        problems = verify(ARDUINO / "src")
        for p in problems:
            print(f"  NG  {p}")
        entries = len(manifest())
        print(f"{'NG!' if problems else 'OK '} G-AR1 生成物 {entries} 件が"
              f" csrc/ と esp32/ の逐語（前置き + 本文 + 後置き）")
        return 1 if problems else 0

    if a.zip:
        return make_zips(a.zip, a.version, a.blob)

    n = generate(ARDUINO / "src")
    print(f"OK  arduino/src/core/ に {n} 件を生成した"
          f"（⚠️ git 管理外。`--check` で csrc との一致を確かめる）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
