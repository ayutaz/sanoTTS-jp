"""辞書 blob（`csrc/k1_dict.bin`）の同一性と自己整合を検査する。

    uv run python scripts/check_dict_blob.py <blob> [--manifest PATH]
    uv run python scripts/check_dict_blob.py <blob> --emit-manifest PATH

**なぜ要るか。** 端末側 G2P の辞書 blob には SHA-256 も manifest も無かった。
別物を焼いても例外は出ず、**「動くが読みが違う」**だけになる（欠陥 5）。
`scripts/k1/k0_verify_dict.py` が入力辞書（sys.dic）に対してやっていることを、
**出力の blob** に対してやる。

検査は 2 層ある。**層ごとに見えるものが違う**:

| 層 | 見るもの | 見えないもの |
|---|---|---|
| **自己整合**（blob 単独） | 構造の辻褄（セクション表・長さ・チェックポイント） | **値の中身**。`pool` の 1 バイトを反転しても素通りする（対照 1 で実演する） |
| **manifest 照合** | SHA-256 / 長さ / version / セクション表 / entries | manifest 自身が正しいこと。**両方を作り直せば一致する** |

⚠️ **manifest が無ければ非ゼロで終了する。** 「manifest が無いから検査を飛ばした」を
「通った」と読ませないため（`k0_verify_dict.py` の SKIP と同じ扱い）。

⚠️ **これは「この blob が D-042 の辞書から作られた」証明ではない。**
blob は入力辞書の同一性を内部に持たないので、manifest に記録した
`scripts/k1/dict_manifest.json` の SHA-256 と、**今そこにあるファイル**を
照合するところまでしかできない。

陽性対照は 5 つ内蔵してあり、**本番の判定より先に走る**（`writing-gates`）。
1 つでも落ちなければ検査自体を失敗にする。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from saanotts_jp.jdict import CharProperty, DictBlob  # noqa: E402

SCHEMA = "saanotts-jp/dict-blob-manifest/1"
DICT_MANIFEST = ROOT / "scripts/k1/dict_manifest.json"
HEADER_FMT = "<4sHH"
TABLE_ENT = 16


# --------------------------------------------------------------------------
# 読み取り
# --------------------------------------------------------------------------
def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_header(raw: bytes) -> tuple:
    """(magic, version, n_sections)。**ここでは弾かない**（自己整合側で報告する）。"""
    if len(raw) < 8:
        raise ValueError(f"8 B に満たない: {len(raw)} B")
    magic, ver, n = struct.unpack(HEADER_FMT, raw[:8])
    return magic, ver, n


def raw_sections(raw: bytes) -> dict:
    """`DictBlob.sections` と同じ表を、magic/version を弾かずに読む。"""
    _m, _v, n = parse_header(raw)
    out = {}
    need = 8 + TABLE_ENT * n
    if need > len(raw):
        raise ValueError(f"セクション表がファイルからはみ出す: {need} > {len(raw)}")
    for i in range(n):
        name, off, ln = struct.unpack("<8sII", raw[8 + TABLE_ENT * i:
                                                   8 + TABLE_ENT * (i + 1)])
        out[name.rstrip(b"\0").decode("ascii", "replace")] = (off, ln)
    return out


def describe(raw: bytes) -> dict:
    """blob から読み取れる内容の要約。**推測しない**（読めない項目は入れない）。"""
    magic, ver, n = parse_header(raw)
    secs = raw_sections(raw)
    d = {
        "format": {"magic": magic.decode("ascii", "replace"),
                   "version": ver, "n_sections": n},
        "sections": {k: {"offset": o, "length": l}
                     for k, (o, l) in sorted(secs.items(), key=lambda kv: kv[1][0])},
        "content": {},
    }
    if "counts" in secs:
        o, l = secs["counts"]
        counts = raw[o:o + l]
        d["content"]["surfaces"] = len(counts)
        d["content"]["entries"] = sum(counts)
    if "matrix" in secs:
        o, l = secs["matrix"]
        if l >= 4:
            ls, rs = struct.unpack("<HH", raw[o:o + 4])
            d["content"]["matrix"] = {"lsize": ls, "rsize": rs}
    if "louds" in secs:
        o, l = secs["louds"]
        if l >= 20:
            bitlen, nodes, _s, _b, _e = struct.unpack("<IIIII", raw[o:o + 20])
            d["content"]["trie_nodes"] = nodes
            d["content"]["trie_bitlen"] = bitlen
    return d


# --------------------------------------------------------------------------
# 層 1: 自己整合（blob 単独）
# --------------------------------------------------------------------------
def self_check(raw: bytes) -> list[str]:
    """blob だけで分かる矛盾を列挙する。空なら矛盾なし。"""
    bad: list[str] = []
    try:
        magic, ver, n = parse_header(raw)
    except Exception as e:                                    # noqa: BLE001
        return [f"ヘッダが読めない: {e}"]
    if magic != b"K1D1":
        bad.append(f"magic が違う: {magic!r}")
    if ver != DictBlob.VERSION:
        bad.append(f"version が違う: {ver} != {DictBlob.VERSION}"
                   f"（このリポジトリのコアは拒む）")
    if not (0 < n <= 64):
        bad.append(f"セクション数が異常: {n}")
        return bad
    try:
        secs = raw_sections(raw)
    except Exception as e:                                    # noqa: BLE001
        return bad + [f"セクション表が読めない: {e}"]
    if len(secs) != n:
        bad.append(f"セクション名が重複している: {len(secs)} 種 / 宣言 {n}")

    # --- 表そのもの ---
    head_end = 8 + TABLE_ENT * n
    known = set(DictBlob._SEC_NAMES)
    spans = []
    for name, (off, ln) in secs.items():
        if name not in known:
            bad.append(f"{name}: 知らないセクション名")
        if off % 16:
            bad.append(f"{name}: offset {off} が 16 B 境界でない")
        if off < head_end:
            bad.append(f"{name}: offset {off} がヘッダ（{head_end} B）に食い込む")
        if off > len(raw) or ln > len(raw) or off + ln > len(raw):
            bad.append(f"{name}: [{off}, {off + ln}) がファイル {len(raw)} B の外")
        else:
            spans.append((off, off + ln, name))
    spans.sort()
    for (a0, a1, an), (b0, _b1, bn) in zip(spans, spans[1:]):
        if a1 > b0:
            bad.append(f"{an} と {bn} が重なる: {a1} > {b0}")
        if b0 - a1 >= 16:
            bad.append(f"{an} と {bn} の間に {b0 - a1} B の隙間（詰めは 16 B 未満のはず）")
    if spans and len(raw) - spans[-1][1] >= 16:
        bad.append(f"末尾に {len(raw) - spans[-1][1]} B の余りがある")

    def sec(name: str) -> bytes | None:
        if name not in secs:
            return None
        o, l = secs[name]
        if o + l > len(raw):
            return None
        return raw[o:o + l]

    for req in ("keytab", "moratab", "louds", "counts", "classes",
                "chains", "records", "pool", "surfck", "poolck", "termck"):
        if req not in secs:
            bad.append(f"必須セクション {req} が無い")
    # ⚠️ **ここで早期 return しない。** 範囲外のセクションが 1 つあるだけで
    #    以降の検査を全部飛ばすと、**別の壊れ方が見えなくなる**（対照で踏んだ:
    #    末尾を切った blob では records / matrix の壊し方が 1 件も増えなかった）。
    #    範囲外のセクションは `sec()` が None を返すので、個別に飛ばせば足りる。

    # --- louds: 宣言ヘッダから長さが決まる ---
    nodes = None
    lo = sec("louds")
    if lo is not None and len(lo) >= 20:
        bitlen, nodes, n_sup, n_blk, n_sel = struct.unpack("<IIIII", lo[:20])
        want = (20 + (bitlen + 7) // 8 + nodes + (nodes + 7) // 8
                + n_sup + n_blk + n_sel)
        if want != len(lo):
            bad.append(f"louds: 宣言から計算した {want} B != セクション長 {len(lo)} B")
            nodes = None
        elif nodes * 8 < bitlen - 16 or nodes > bitlen:
            bad.append(f"louds: ノード数 {nodes} とビット長 {bitlen} が釣り合わない")

    counts = sec("counts")
    n_surf = len(counts) if counts is not None else None
    n_entry = sum(counts) if counts is not None else None

    # --- 終端ビットの数 == 見出し語数（層をまたぐ照合）---
    if lo is not None and nodes is not None and counts is not None:
        toff = 20 + (struct.unpack("<I", lo[:4])[0] + 7) // 8 + nodes
        term = lo[toff:toff + (nodes + 7) // 8]
        n_term = sum(bin(b).count("1") for b in term)
        if n_term != n_surf:
            bad.append(f"trie の終端 {n_term:,d} != counts の見出し語数 {n_surf:,d}")

    # --- records / pool / チェックポイント ---
    rec = sec("records")
    pool = sec("pool")
    if rec is not None:
        if len(rec) % DictBlob.RECORD_SIZE:
            bad.append(f"records: 長さ {len(rec)} が "
                       f"{DictBlob.RECORD_SIZE} の倍数でない")
        elif n_entry is not None and len(rec) // DictBlob.RECORD_SIZE != n_entry:
            bad.append(f"records: {len(rec) // DictBlob.RECORD_SIZE:,d} 件 != "
                       f"counts の合計 {n_entry:,d} 件")
        elif pool is not None:
            ck = sec("poolck")
            n = len(rec) // DictBlob.RECORD_SIZE
            tot = 0
            miss = 0
            for i in range(n):
                if ck is not None and i % DictBlob.CHECKPOINT == 0:
                    j = 4 * (i // DictBlob.CHECKPOINT)
                    if j + 4 <= len(ck) and \
                            struct.unpack("<I", ck[j:j + 4])[0] != tot:
                        miss += 1
                b = rec[DictBlob.RECORD_SIZE * i:DictBlob.RECORD_SIZE * (i + 1)]
                tot += b[7] + b[8]              # pron 長 + extra 長
            if tot != len(pool):
                bad.append(f"pool: レコード長の合計 {tot:,d} != セクション長 "
                           f"{len(pool):,d}")
            if miss:
                bad.append(f"poolck: {miss:,d} 個のチェックポイントが "
                           f"レコード長の累計と合わない")

    for name, unit, count, label in (("poolck", 4, n_entry, "entries"),
                                     ("surfck", 4, n_surf, "見出し語"),
                                     ("termck", 4, nodes, "trie ノード")):
        s = sec(name)
        step = DictBlob.CHECKPOINT if name != "termck" else DictBlob.TERM_CHECKPOINT
        if s is not None and count is not None:
            want = unit * ((count + step - 1) // step)
            if len(s) != want:
                bad.append(f"{name}: 長さ {len(s):,d} != {label} {count:,d} から "
                           f"計算した {want:,d}")

    # --- 任意セクション ---
    mat = sec("matrix")
    if mat is not None:
        if len(mat) < 4:
            bad.append("matrix: 4 B 未満")
        else:
            ls, rs = struct.unpack("<HH", mat[:4])
            want = 4 + 2 * ls * rs
            if want != len(mat):
                bad.append(f"matrix: lsize {ls} × rsize {rs} から計算した {want:,d} B "
                           f"!= セクション長 {len(mat):,d} B")
    ch = sec("char")
    if ch is not None:
        if len(ch) < 4:
            bad.append("char: 4 B 未満")
        else:
            ncat = struct.unpack("<I", ch[:4])[0]
            want = 4 + 32 * ncat + 4 * CharProperty.N_CODEPOINTS
            if want != len(ch):
                bad.append(f"char: カテゴリ {ncat} 件から計算した {want:,d} B != "
                           f"セクション長 {len(ch):,d} B")
    unk = sec("unk")
    if unk is not None:
        if len(unk) < 4:
            bad.append("unk: 4 B 未満")
        else:
            try:
                n_unk = struct.unpack("<I", unk[:4])[0]
                o = 4
                for _ in range(n_unk):
                    o += 6
                    o = unk.index(b"\0", o) + 1
                    o = unk.index(b"\0", o) + 1
                if o != len(unk):
                    bad.append(f"unk: {n_unk} 件を読むと {o} B で、"
                               f"セクション長 {len(unk)} B と合わない")
            except ValueError:
                bad.append(f"unk: {n_unk} 件を読み切る前に終端 NUL が尽きた")

    return bad


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def manifest_path_for(blob_path: str | os.PathLike) -> pathlib.Path:
    return pathlib.Path(str(blob_path) + ".manifest.json")


def source_dict_info(path: pathlib.Path = DICT_MANIFEST) -> dict | None:
    """入力辞書の同一性（`scripts/k1/dict_manifest.json`）を要約する。"""
    if not path.exists():
        return None
    raw = path.read_bytes()
    man = json.loads(raw.decode("utf-8"))
    return {
        "manifest_path": os.path.relpath(path, ROOT),
        "manifest_sha256": sha256_bytes(raw),
        "decision": man.get("decision"),
        "lexsize": man.get("sys_dic_header", {}).get("lexsize"),
        "sys_dic_sha256": man.get("files", {}).get("sys.dic", {}).get("sha256"),
    }


def build_manifest(raw: bytes, *, blob_name: str, provenance: str,
                   build: dict | None = None) -> dict:
    """blob から manifest を組む。**ここが唯一の定義**（ビルド側も呼ぶ）。"""
    d = describe(raw)
    man = {
        "_comment": "辞書 blob の同一性。scripts/check_dict_blob.py が照合する。"
                    "⚠️ manifest と blob は同時に作られるので、"
                    "両方を作り直せば一致する。「D-042 の辞書から作られた」証明ではない。",
        "schema": SCHEMA,
        "generator": "scripts/k1/k1_build_dict.py",
        "provenance": provenance,
        "created_utc": datetime.datetime.now(datetime.timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blob": {"name": blob_name, "bytes": len(raw), "sha256": sha256_bytes(raw)},
        "format": d["format"],
        "content": d["content"],
        "sections": d["sections"],
    }
    src = source_dict_info()
    if src is not None:
        # ⚠️ **`asserted` は「この blob をその辞書から作った」と言えるかどうか。**
        #    `--emit-manifest` は既存 blob に**今そこにある** dict_manifest.json を
        #    貼るだけなので、貼った事実を「作られた証拠」と読ませない。
        src["asserted"] = (provenance == "build")
        man["source_dict"] = src
    if build:
        man["build"] = build
    return man


def write_manifest(man: dict, path: str | os.PathLike) -> None:
    pathlib.Path(path).write_text(
        json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def manifest_check(raw: bytes, man: dict) -> list[str]:
    """manifest と blob の食い違いを列挙する。空なら一致。"""
    bad: list[str] = []
    if man.get("schema") != SCHEMA:
        bad.append(f"schema が違う: {man.get('schema')!r} != {SCHEMA!r}")
    for key in ("blob", "format", "content", "sections"):
        if key not in man:
            bad.append(f"manifest に {key} が無い")
    if bad:
        return bad

    b = man["blob"]
    if b.get("bytes") != len(raw):
        bad.append(f"長さ {b.get('bytes')} != 実物 {len(raw)}")
    h = sha256_bytes(raw)
    if b.get("sha256") != h:
        bad.append(f"sha256 {str(b.get('sha256'))[:24]}… != 実物 {h[:24]}…")
    # ⚠️ **ここで例外を出さない。** 別のファイルを焼いた／転送が途切れた、という
    #    いちばん起きそうな壊れ方で traceback になると、報告として使えない。
    try:
        got = describe(raw)
    except Exception as e:                                    # noqa: BLE001
        return bad + [f"blob として読めないので構造を照合できない: "
                      f"{type(e).__name__}: {e}"]
    for k, v in man["format"].items():
        if got["format"].get(k) != v:
            bad.append(f"format.{k}: {v!r} != 実物 {got['format'].get(k)!r}")
    for k, v in man["content"].items():
        if got["content"].get(k) != v:
            bad.append(f"content.{k}: {v!r} != 実物 {got['content'].get(k)!r}")
    for k in got["content"]:
        if k not in man["content"]:
            bad.append(f"content.{k} が manifest に無い（実物 "
                       f"{got['content'][k]!r}）")
    ms, gs = man["sections"], got["sections"]
    for name in sorted(set(ms) | set(gs)):
        if name not in ms:
            bad.append(f"セクション {name} が manifest に無い")
        elif name not in gs:
            bad.append(f"セクション {name} が blob に無い")
        elif (ms[name].get("offset"), ms[name].get("length")) != \
                (gs[name]["offset"], gs[name]["length"]):
            bad.append(f"セクション {name}: manifest "
                       f"({ms[name].get('offset')}, {ms[name].get('length')}) != "
                       f"実物 ({gs[name]['offset']}, {gs[name]['length']})")
    return bad


# --------------------------------------------------------------------------
# 陽性対照 — **本番の判定より先に走らせて、落ちるのを見る**
# --------------------------------------------------------------------------
def _set_table_field(buf: bytearray, name: str, field: str, value: int) -> bool:
    _m, _v, n = parse_header(bytes(buf))
    for i in range(n):
        nm = buf[8 + TABLE_ENT * i: 16 + TABLE_ENT * i].rstrip(b"\0")
        if nm.decode("ascii", "replace") == name:
            at = 16 + TABLE_ENT * i + (0 if field == "offset" else 4)
            buf[at:at + 4] = struct.pack("<I", value)
            return True
    return False


def _mutations(raw: bytes) -> tuple[list, list]:
    """(名前, 壊した bytes, 自己整合は落ちるべきか, 説明) の一覧と、作れなかった対照。

    ⚠️ **作れなかった対照は黙って消さない。** 任意セクション（matrix / char / unk）が
    無い blob では対照が減る。減ったことを言わないと「対照 4 件が全部通った」が
    「5 件目は存在しない」を隠す。
    """
    secs = raw_sections(raw)
    out: list = []
    skipped: list[str] = []

    # 1. 値の 1 バイト反転 — **自己整合では見えない**。sha256 でしか捕まらない
    target = "pool" if "pool" in secs else max(secs, key=lambda k: secs[k][1])
    o, l = secs[target]
    b = bytearray(raw)
    b[o + l // 2] ^= 0xFF
    out.append((f"{target} の中央 1 バイトを反転", bytes(b), False,
                "値の中身。自己整合は素通り（層の限界）／sha256 が捕まえる"))

    # 2. セクションの宣言長を 1 ずらす
    if "records" in secs:
        b = bytearray(raw)
        assert _set_table_field(b, "records", "length", secs["records"][1] + 1)
        out.append(("records の宣言長 +1", bytes(b), True,
                    "9 の倍数でなくなる / counts の合計と合わなくなる"))

    # 3. セクションの offset をファイルの外へ
    last = max(secs, key=lambda k: secs[k][0])
    b = bytearray(raw)
    assert _set_table_field(b, last, "offset", len(raw) + 16)
    out.append((f"{last} の offset をファイル外へ", bytes(b), True, "範囲検査"))

    # 4. 接続行列の lsize を +1
    if "matrix" in secs:
        o, _l = secs["matrix"]
        b = bytearray(raw)
        ls = struct.unpack("<H", raw[o:o + 2])[0]
        b[o:o + 2] = struct.pack("<H", ls + 1)
        out.append(("matrix の lsize を +1", bytes(b), True,
                    "4 + 2·lsize·rsize がセクション長と合わなくなる"))
    else:
        skipped.append("matrix の lsize を +1（この blob に matrix セクションが無い）")
    if "records" not in secs:
        skipped.append("records の宣言長 +1（この blob に records セクションが無い）")

    # 5. version を上げる（S4 で v1 → v2 に上がった。実際に起きた壊れ方）
    b = bytearray(raw)
    b[4:6] = struct.pack("<H", DictBlob.VERSION + 1)
    out.append((f"version を {DictBlob.VERSION + 1} に", bytes(b), True,
                "コアが SAAN_ERR_VERSION で拒む blob を、検査も拒むか"))
    return out, skipped


def run_controls(raw: bytes, man: dict) -> bool:
    """陽性対照。**1 つでも落ちなければ検査自体を失敗にする。**

    ⚠️ **判定は「元の blob からの差分」で見る。**
    「壊したら N 件落ちた」を絶対数で見ると、**元の blob が既に壊れている場合**に
    対照が意味を失う（実際に踏んだ: 末尾を切った blob を渡したら、
    値の 1 バイト反転が「自己整合 2 件」になり、対照が誤って FAIL した）。
    元が何件落ちていても、**その壊し方で新しく増えた指摘があるか**を見る。
    """
    base_s = set(self_check(raw))
    base_m = set(manifest_check(raw, man))
    print("=== 陽性対照（壊したコピーで、検査が落ちるのを先に見る）===")
    try:
        muts, skipped = _mutations(raw)
    except Exception as e:                                    # noqa: BLE001
        # blob として読めないものは壊しようがない（別ファイルを焼いた等）。
        print(f"  ⚠️ **SKIP** — この blob は構造を読めないので対照を作れない "
              f"（{type(e).__name__}: {e}）。")
        print("     SKIP を「通った」と読まないこと。"
              "**下の自己整合が落ちることで判定は NG になる。**")
        if not base_s:
            print("  → **FAIL** — 読めないのに自己整合が 0 件。検査が効いていない。")
            return False
        print()
        return True
    if base_s or base_m:
        print(f"  ⚠️ 元の blob が既に落ちている（自己整合 {len(base_s)} 件 / "
              f"manifest {len(base_m)} 件）。対照は**増えた指摘**で判定する。")
    tmp = tempfile.mkdtemp(prefix="dictblob_ctl_")
    ok = True
    try:
        for label, bad_raw, self_should_fail, why in muts:
            p = os.path.join(tmp, "broken.bin")
            with open(p, "wb") as f:
                f.write(bad_raw)
            got = open(p, "rb").read()          # 実際にファイル経由で読み直す
            s = set(self_check(got)) - base_s
            m = set(manifest_check(got, man)) - base_m
            good = (len(s) > 0) == self_should_fail and len(m) > 0
            ok &= good
            print(f"  {'OK ' if good else 'NG '} {label}")
            print(f"        自己整合 +{len(s):<2d} 件"
                  f"（期待 {'≥1' if self_should_fail else '0'}）/ "
                  f"manifest 照合 +{len(m):<2d} 件（期待 ≥1）  — {why}")
            if s:
                print(f"        例: {sorted(s)[0]}")
            if not good:
                print("        ⚠️ **この壊し方を検査が捕まえていない。**")
            os.unlink(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for sk in skipped:
        print(f"  --  {sk}")
    print(f"  → {'PASS' if ok else '**FAIL（検査が効いていない）**'}"
          f"（{len(muts)} 件実行 / {len(skipped)} 件は作れず）\n")
    return ok


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("blob")
    ap.add_argument("--manifest", default=None,
                    help="既定は <blob>.manifest.json")
    ap.add_argument("--emit-manifest", default=None, metavar="PATH",
                    help="既存 blob から manifest を組んで書き出す（照合はしない）。"
                         "⚠️ **その blob が正しい証明にはならない**")
    ap.add_argument("--no-controls", action="store_true",
                    help="陽性対照を飛ばす。⚠️ **SKIP を「通った」と読まないこと**")
    a = ap.parse_args(argv)

    bp = pathlib.Path(a.blob)
    if not bp.exists():
        print(f"NG: blob が無い: {bp}")
        return 1
    raw = bp.read_bytes()
    print(f"blob      : {bp} ({len(raw):,d} B)")
    print(f"sha256    : {sha256_bytes(raw)}")

    # --- --emit-manifest（既存 blob から組む小さな経路）---
    if a.emit_manifest:
        bad = self_check(raw)
        print("\n=== 自己整合（emit の前に通す）===")
        for b in bad:
            print("   ", b)
        if bad:
            print(f"\nNG! 自己整合が {len(bad)} 件落ちた。この blob の manifest は書かない。")
            return 1
        print("  矛盾なし")
        man = build_manifest(raw, blob_name=bp.name,
                             provenance="emitted-from-existing-blob")
        write_manifest(man, a.emit_manifest)
        print(f"\n書き出した → {a.emit_manifest}")
        print("⚠️ provenance = emitted-from-existing-blob。**この manifest は"
              "「今この blob がこうである」しか言わない。**")
        print("   D-044 の動作点で作り直したものかは、"
              "scripts/k1/k1_build_dict.py --out を通した manifest でしか言えない。")
        return 0

    mp = pathlib.Path(a.manifest) if a.manifest else manifest_path_for(bp)
    print(f"manifest  : {mp}")
    if not mp.exists():
        print(f"\nNG! manifest が無い: {mp}")
        print("    ⚠️ **manifest が無いことを「通った」と読まないこと。**")
        print("    作り方:")
        print("      uv run python scripts/k1/k1_build_dict.py --out "
              f"{bp}            # ビルドと同時に書かれる")
        print(f"      uv run python scripts/check_dict_blob.py {bp} "
              f"--emit-manifest {manifest_path_for(bp)}   # 既存 blob から組む")
        return 1
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        print(f"\nNG! manifest が読めない: {type(e).__name__}: {e}")
        return 1
    print(f"            provenance={man.get('provenance')!r} "
          f"created={man.get('created_utc')!r}")
    if man.get("blob", {}).get("name") != bp.name:
        print(f"            ⚠️ manifest が記録した名前は "
              f"{man.get('blob', {}).get('name')!r}（名前は照合しない）")
    print()

    fails: list[str] = []

    if a.no_controls:
        print("=== 陽性対照 ===\n  ⚠️ **SKIP を指定された。SKIP を「通った」と読まないこと。**\n")
    elif not run_controls(raw, man):
        fails.append("陽性対照")

    print("=== 自己整合（blob 単独）===")
    bad = self_check(raw)
    for b in bad:
        print("   ", b)
    if bad:
        fails.append("自己整合")
    else:
        d = describe(raw)
        c = d["content"]
        print(f"  矛盾なし  version={d['format']['version']} / "
              f"セクション {d['format']['n_sections']} 個 / "
              f"{c.get('entries', -1):,d} entries / "
              f"{c.get('surfaces', -1):,d} 見出し語")

    print("\n=== manifest 照合 ===")
    bad = manifest_check(raw, man)
    for b in bad:
        print("   ", b)
    if bad:
        fails.append("manifest 照合")
    else:
        print("  sha256 / 長さ / version / entries / セクション表 すべて一致")

    print("\n=== 入力辞書 manifest の同一性 ===")
    src = man.get("source_dict")
    if not src:
        print("  ⚠️ **SKIP** — この manifest は入力辞書を記録していない。")
        print("     SKIP を「通った」と読まないこと。")
    else:
        cur = source_dict_info(ROOT / src.get("manifest_path",
                                              "scripts/k1/dict_manifest.json"))
        if cur is None:
            print(f"  ⚠️ **SKIP** — {src.get('manifest_path')} が今この木に無い。")
        elif cur["manifest_sha256"] != src.get("manifest_sha256"):
            print(f"  記録 {str(src.get('manifest_sha256'))[:24]}…")
            print(f"  現在 {cur['manifest_sha256'][:24]}…")
            print("  **不一致** — blob を作ったときの入力辞書 manifest と、"
                  "今そこにあるものが違う。")
            fails.append("入力辞書 manifest")
        else:
            print(f"  一致（{src.get('decision')} / lexsize "
                  f"{src.get('lexsize'):,d} / sys.dic "
                  f"{str(src.get('sys_dic_sha256'))[:16]}…）")
            if not src.get("asserted"):
                print("  ⚠️ **ただし asserted=false。** この manifest は既存 blob から"
                      "起こしたもので、")
                print("     記録した辞書は「manifest を書いた時点でそこにあったもの」に"
                      "すぎない。")
                print("     **この blob がその辞書から作られた証拠ではない。**")

    print("\n=== この検査が見ていないもの ===")
    print("  - **値の中身**（読み・アクセントが正しいか）。構造の辻褄しか見ない")
    print("  - **manifest 自身の正しさ**。blob と一緒に作られるので、"
          "両方を作り直せば一致する")
    print("  - 入力辞書から実際に作られたこと。記録した "
          "dict_manifest.json の同一性までしか言えない")
    print("  - 端末（`csrc/jdict.c`）がこの blob を読めること。"
          "それは make -C csrc jdict の仕事")

    print()
    if fails:
        print(f"NG! 落ちた: {', '.join(fails)}")
        return 1
    print("OK  blob は manifest と一致し、自己整合も取れている")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
