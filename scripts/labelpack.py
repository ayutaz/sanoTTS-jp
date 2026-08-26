#!/usr/bin/env python3
"""蒸留ラベルパックの書き込み / 読み出し / 健全性ゲート（A-3 で確定した形式）。

レイアウト（`pack/` 以下）:

    manifest.json          生成環境・フラグ・shard ごとの SHA-256・コーパス由来
    index.npy              構造化配列。学習時に読むのはこれ（1 発話 1 行）
    index.jsonl            同内容 + text。人間が読む / grep する用
    tokens.npz             phoneme_ids / dT / prosody を連結 + offsets（全体で 38 MB）
    channel_stats.npz      mu_T[192] sigma_T[192] n_frames min_T[192] max_T[192]
    shards/{sid:04d}.zt.f16   zT fp16 を (192,T) C-order で連結
    shards/{sid:04d}.yt.i16   yT int16 を連結（scale は 32767 固定）

設計の根拠は `reports/a3_pack_design.json`。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import numpy as np

SR = 22050
HOP = 256
Z_CH = 192
NUM_SYMBOLS = 173          # ckpt の num_symbols。185 entry あるが有効 id は 0..172
PCM_SCALE = 32767.0
UTTS_PER_SHARD = 128       # 部分再生成で書き直す量が 1 shard = 約 39 MB

INDEX_DTYPE = np.dtype([
    ("seq", "<i4"), ("shard", "<i4"),
    ("n_ids", "<i4"), ("frames", "<i4"),
    ("zt_off", "<i8"),        # shard 内の要素オフセット (fp16 単位)
    ("yt_off", "<i8"),        # shard 内の要素オフセット (int16 単位)
    ("tok_off", "<i8"),       # tokens.npz 内の要素オフセット
    ("crc32", "<u4"),         # 1 発話ぶんのバイト列の CRC32（行単位の破損検出）
])


# --------------------------------------------------------------------------- gates
class GateError(Exception):
    pass


VOWEL_TOKENS = set("aiueoAIUEO")


def mora_count(tokens: list[str]) -> int:
    return sum(1 for t in tokens if t in VOWEL_TOKENS or t.startswith("N") or t == "cl")


def check_utt(ids, prosody, dT, zT, yT, tokens=None) -> None:
    """保存前ゲート。1 つでも落ちたらその発話を採用しない（理由を index.jsonl に残す）。

    ここに並ぶのは全部「例外を出さずにデータを壊す」経路への対策。
    """
    ids = np.asarray(ids)
    # --- 形と整合 ---
    if zT.shape[0] != Z_CH:
        raise GateError(f"zT のチャネルが {zT.shape[0]} (192 でない)")
    if zT.shape[1] * HOP != yT.shape[0]:
        raise GateError(f"hop 不一致: frames {zT.shape[1]}*256 != samples {yT.shape[0]}")
    if not (len(ids) == len(dT) == len(prosody)):
        raise GateError(f"長さ不一致 ids {len(ids)} dT {len(dT)} prosody {len(prosody)}")
    ceil_sum = int(np.ceil(dT).sum())
    if ceil_sum != zT.shape[1]:
        raise GateError(f"ceil(dT).sum()={ceil_sum} != frames {zT.shape[1]}")
    # --- 音素 ID ---
    if ids.min() < 0 or ids.max() >= NUM_SYMBOLS:
        raise GateError(f"音素 id が範囲外: max {ids.max()} (num_symbols={NUM_SYMBOLS})")
    if ids[0] != 1 or ids[-1] != 2:
        raise GateError(f"BOS/EOS が無い: {ids[0]} .. {ids[-1]}")
    nz = np.flatnonzero(ids != 0)
    if nz.size < 2 or not (np.diff(nz) >= 2).all():
        # canonical 経路は必ず `_`(id 0) を intersperse する。これが無い =
        # 自前で音素→ID 変換した = 2.4 倍速ラベル（C-007）。
        raise GateError("intersperse PAD が無い（自前音素化の疑い）")
    # --- 数値 ---
    for name, arr in (("zT", zT), ("yT", yT), ("dT", dT)):
        if not np.isfinite(arr).all():
            raise GateError(f"{name} に NaN/Inf")
    if float(np.abs(yT).max()) < 1e-4:
        raise GateError("yT が無音（全ゼロ相当）")
    if float(np.abs(yT).max()) >= 1.0:
        raise GateError(f"yT がクリップ: peak {np.abs(yT).max():.4f}")
    if float(dT.min()) <= 0:
        raise GateError(f"dT に非正値: min {dT.min()}")
    # --- 発話速度（音素化ミスは他の全チェックを通過してしまう） ---
    if tokens is not None:
        rate = mora_count(tokens) / (yT.shape[0] / SR)
        # 実測（train 128 文）: mean 7.19 / min 5.04 / max 9.64。
        # skill の 6–10 は実データの 5.5% を誤って弾くので 4–12 にする。
        if not (4.0 <= rate <= 12.0):
            raise GateError(f"発話速度 {rate:.2f} mora/s が範囲外")


# --------------------------------------------------------------------------- writer
@dataclass
class PackWriter:
    out: str
    utts_per_shard: int = UTTS_PER_SHARD
    _rows: list = field(default_factory=list)
    _meta: list = field(default_factory=list)
    _tok: dict = field(default_factory=lambda: {"phoneme_ids": [], "dT": [], "prosody": []})
    _zf: object = None
    _yf: object = None
    _sid: int = -1
    _zoff: int = 0
    _yoff: int = 0
    _toff: int = 0
    _n: int = 0
    _s1: np.ndarray = None
    _s2: np.ndarray = None
    _mn: np.ndarray = None
    _mx: np.ndarray = None
    _nfr: int = 0

    def __post_init__(self):
        os.makedirs(f"{self.out}/shards", exist_ok=True)
        self._s1 = np.zeros(Z_CH, np.float64)
        self._s2 = np.zeros(Z_CH, np.float64)
        self._mn = np.full(Z_CH, np.inf)
        self._mx = np.full(Z_CH, -np.inf)

    def _roll(self):
        sid = self._n // self.utts_per_shard
        if sid != self._sid:
            if self._zf:
                self._zf.close(); self._yf.close()
            self._sid, self._zoff, self._yoff = sid, 0, 0
            self._zf = open(f"{self.out}/shards/{sid:04d}.zt.f16", "wb")
            self._yf = open(f"{self.out}/shards/{sid:04d}.yt.i16", "wb")

    def add(self, meta: dict, ids, prosody, dT, zT, yT):
        """zT/yT は教師が返した fp32 のまま渡す。量子化はここで一度だけ行う。"""
        self._roll()
        z16 = np.ascontiguousarray(zT, np.float32).astype(np.float16)          # (192, T)
        y16 = np.rint(np.clip(yT, -1.0, 1.0) * PCM_SCALE).astype(np.int16)
        zb, yb = z16.tobytes(), y16.tobytes()
        self._zf.write(zb); self._yf.write(yb)

        # チャネル統計は fp32 の原値から取る（量子化前）
        z64 = np.asarray(zT, np.float64)
        self._s1 += z64.sum(1); self._s2 += (z64 * z64).sum(1)
        self._mn = np.minimum(self._mn, z64.min(1)); self._mx = np.maximum(self._mx, z64.max(1))
        self._nfr += z64.shape[1]

        ids = np.asarray(ids, np.int32)
        self._tok["phoneme_ids"].append(ids)
        self._tok["dT"].append(np.asarray(dT, np.float32))
        self._tok["prosody"].append(np.asarray(prosody, np.int16).reshape(-1, 3))

        import zlib
        crc = zlib.crc32(zb); crc = zlib.crc32(yb, crc)
        crc = zlib.crc32(ids.tobytes(), crc)
        self._rows.append((self._n, self._sid, len(ids), z16.shape[1],
                           self._zoff, self._yoff, self._toff, crc & 0xFFFFFFFF))
        self._meta.append(dict(seq=self._n, shard=self._sid, frames=int(z16.shape[1]),
                               n_ids=int(len(ids)), seconds=y16.size / SR, **meta))
        self._zoff += z16.size; self._yoff += y16.size; self._toff += len(ids)
        self._n += 1

    def close(self, manifest: dict):
        if self._zf:
            self._zf.close(); self._yf.close()
        np.save(f"{self.out}/index.npy", np.array(self._rows, INDEX_DTYPE))
        with open(f"{self.out}/index.jsonl", "w") as f:
            for m in self._meta:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        np.savez(f"{self.out}/tokens.npz",
                 phoneme_ids=np.concatenate(self._tok["phoneme_ids"]),
                 dT=np.concatenate(self._tok["dT"]),
                 prosody=np.concatenate(self._tok["prosody"]),
                 offsets=np.cumsum([0] + [len(a) for a in self._tok["phoneme_ids"]]).astype(np.int64))
        mu = self._s1 / self._nfr
        sd = np.sqrt(np.maximum(self._s2 / self._nfr - mu * mu, 0))
        np.savez(f"{self.out}/channel_stats.npz",
                 mu_T=mu.astype(np.float32), sigma_T=sd.astype(np.float32),
                 min_T=self._mn.astype(np.float32), max_T=self._mx.astype(np.float32),
                 n_frames=np.int64(self._nfr))
        sums = {}
        for root, _, fs in os.walk(self.out):
            for fn in sorted(fs):
                if fn == "manifest.json":
                    continue
                p = os.path.join(root, fn)
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for blk in iter(lambda: fh.read(1 << 22), b""):
                        h.update(blk)
                sums[os.path.relpath(p, self.out)] = h.hexdigest()
        with open(f"{self.out}/SHA256SUMS", "w") as f:
            for k in sorted(sums):
                f.write(f"{sums[k]}  {k}\n")
        manifest = dict(manifest)
        manifest.update(n_utts=self._n, n_frames=int(self._nfr),
                        utts_per_shard=self.utts_per_shard,
                        pcm_scale=PCM_SCALE, hop=HOP, sample_rate=SR,
                        z_channels=Z_CH, zt_dtype="float16", yt_dtype="int16",
                        sha256=sums)
        json.dump(manifest, open(f"{self.out}/manifest.json", "w"),
                  ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- reader
class PackReader:
    """学習用。memmap なので worker 間で page cache を共有する。"""

    def __init__(self, out: str):
        self.out = out
        self.manifest = json.load(open(f"{out}/manifest.json"))
        self.index = np.load(f"{out}/index.npy")
        t = np.load(f"{out}/tokens.npz")
        self.ids_all, self.dT_all = t["phoneme_ids"], t["dT"]
        self.pros_all, self.tok_off = t["prosody"], t["offsets"]
        cs = np.load(f"{out}/channel_stats.npz")
        self.mu_T, self.sigma_T, self.n_frames = cs["mu_T"], cs["sigma_T"], int(cs["n_frames"])
        self._z, self._y = {}, {}

    def _mm(self, cache, sid, suffix, dtype):
        if sid not in cache:
            cache[sid] = np.memmap(f"{self.out}/shards/{sid:04d}.{suffix}", dtype=dtype, mode="r")
        return cache[sid]

    def tokens(self, i: int):
        """Phase 2 (Duration Dα) はこれだけで足りる。shard に触らない。"""
        e = self.index[i]
        a, b = int(e["tok_off"]), int(e["tok_off"]) + int(e["n_ids"])
        return self.ids_all[a:b], self.dT_all[a:b], self.pros_all[a:b]

    def latent(self, i: int) -> np.ndarray:
        e = self.index[i]; T = int(e["frames"]); o = int(e["zt_off"])
        mm = self._mm(self._z, int(e["shard"]), "zt.f16", np.float16)
        return np.array(mm[o:o + Z_CH * T]).reshape(Z_CH, T)   # copy: memmap view を外に出さない

    def audio(self, i: int) -> np.ndarray:
        e = self.index[i]; n = int(e["frames"]) * HOP; o = int(e["yt_off"])
        mm = self._mm(self._y, int(e["shard"]), "yt.i16", np.int16)
        return np.array(mm[o:o + n])

    def audio_float(self, i: int) -> np.ndarray:
        return self.audio(i).astype(np.float32) / PCM_SCALE

    def __len__(self):
        return len(self.index)
