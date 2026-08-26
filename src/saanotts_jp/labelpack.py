"""教師ラベルパックの読み書き。

形式は A-3 の実測比較で決めた（`reports/a3_pack_design.json`）:

* **shard 化した生バイナリ + memmap + 構造化 index**
  npz（読み 173 µs/utt）/ HDF5（114–125 µs/utt）/ npz_compressed（792 µs/utt）は
  読み速度と部分再生成のしやすさで不採用
* `zT` は **fp16**（実測 |zT| max 52.18、fp16 の上限 65504 に対し余裕あり）
* `yT` は **int16 / 固定 scale 32767**（SNR mean 76.86 dB / min 74.13 dB）
* 波形を持つ。**STFT の事前計算はしない**（fp16 でも 12.0 B/sample で int16 の 6 倍）

```
pack/
  manifest.json          生成環境・フラグ・コーパス由来・全ファイルの SHA-256
  index.npy              構造化配列。学習時に読むのはこれ
  index.jsonl            同内容 + text / source / id（人間が grep する用）
  tokens.npz             phoneme_ids / dT / prosody を連結 + offsets
  channel_stats.npz      mu_T[192] sigma_T[192] min_T[192] max_T[192] n_frames
  shards/{sid:04d}.zt.f16
  shards/{sid:04d}.yt.i16
  SHA256SUMS
```

`mu_T` / `sigma_T` は**必ず一緒に保存する**。式3 のチャネル正規化項 `N_T` と、
式7 の摩擦音ノイズ注入 `σT_k` の両方で要る。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import zlib
from dataclasses import dataclass

import numpy as np

SR = 22050
HOP = 256
Z_CHANNELS = 192
NUM_SYMBOLS = 173
YT_SCALE = 32767
UTTS_PER_SHARD = 128

INDEX_DTYPE = np.dtype([
    ("seq", np.int32),
    ("shard", np.int32),
    ("n_ids", np.int32),
    ("frames", np.int32),
    ("zt_off", np.int64),   # shard 内のフレーム offset
    ("yt_off", np.int64),   # shard 内のサンプル offset
    ("tok_off", np.int64),  # tokens.npz 内の offset
    ("crc32", np.uint32),
])


class GateFailure(Exception):
    """健全性ゲートに落ちた発話。**採用せず index.jsonl に理由を残す。**"""


def _pad_interspersed(ids: np.ndarray) -> bool:
    """canonical 経路の `^ t0 _ t1 _ ... tn $` 構造になっているか。

    BOS/EOS を除いた中身で、非ゼロが 2 つ連続していないことを確かめる。
    """
    body = ids[1:-1]
    if body.size < 3:
        return True
    nz = body != 0
    return not bool(np.any(nz[:-1] & nz[1:]))


@dataclass(frozen=True)
class Utterance:
    """1 発話ぶんの教師ラベル。"""

    ids: np.ndarray       # int32 [L]  音素ID列
    dT: np.ndarray        # float32 [L]  duration（ceil 前）
    prosody: np.ndarray   # int16 [L, 3]  A1/A2/A3
    zT: np.ndarray        # float32 [192, T]
    yT: np.ndarray        # float32 [T*256]
    text: str
    source: str
    uid: str


def check_utt(u: Utterance) -> None:
    """保存の直前に走らせる 13 個のゲート（A-3 decision_5）。

    **どれも例外を出さずにデータを壊す種類の失敗を捕まえる。**
    1 つでも落ちたら `GateFailure` を投げ、その発話は採用しない。
    """
    frames = u.zT.shape[1]
    ceil_sum = int(np.ceil(u.dT).sum())
    peak = float(np.abs(u.yT).max()) if u.yT.size else 0.0

    gates = [
        ("G1", u.zT.shape[0] == Z_CHANNELS, f"zT が {u.zT.shape[0]}ch（192 でない）"),
        ("G2", frames * HOP == len(u.yT), f"hop 不一致: {frames}*{HOP} != {len(u.yT)}"),
        ("G3", ceil_sum == frames, f"ceil(dT).sum()={ceil_sum} != frames={frames}"),
        ("G4", len(u.ids) == len(u.dT) == len(u.prosody),
         f"長さ不一致: ids={len(u.ids)} dT={len(u.dT)} prosody={len(u.prosody)}"),
        ("G5", u.ids.size > 0 and 0 <= int(u.ids.min()) and int(u.ids.max()) < NUM_SYMBOLS,
         f"音素ID が範囲外: max={int(u.ids.max()) if u.ids.size else 'empty'}"),
        ("G6", u.ids.size >= 2 and int(u.ids[0]) == 1 and int(u.ids[-1]) == 2,
         "BOS(^)=1 / EOS($)=2 で挟まれていない"),
        # G7: canonical 経路は必ずトークン間に PAD(id 0) を挟む（intersperse）。
        # これが無いのは自前で音素→ID を組んだ証拠で、発話が 2.4 倍速になる（M-5）。
        ("G7", _pad_interspersed(u.ids),
         "非ゼロ id が PAD(0) で隔てられていない（intersperse 欠落 = 自前音素化の疑い）"),
        ("G8", np.isfinite(u.zT).all() and np.isfinite(u.yT).all() and np.isfinite(u.dT).all(),
         "NaN / Inf を含む"),
        ("G9", peak >= 1e-4, f"音声が無音（peak={peak:.2e}）"),
        ("G10", peak < 1.0, f"音声がクリップ（peak={peak:.4f}）"),
        ("G11", u.dT.size > 0 and float(u.dT.min()) > 0, "duration に 0 以下がある"),
    ]
    for gate_id, ok, msg in gates:
        if not ok:
            raise GateFailure(f"{gate_id}: {msg}")

    # G12: 発話速度。**音素化を間違えると 2.4 倍速になるが音声としては再生できる**
    # ので、他のゲートを全部通ってしまう（M-5）。ここでしか捕まらない。
    seconds = len(u.yT) / SR
    mora = int(np.sum(~np.isin(u.ids, [0, 1, 2, 3, 7, 8, 9]))) / 2.0
    rate = mora / seconds if seconds > 0 else 0.0
    if not (4.0 <= rate <= 12.0):
        raise GateFailure(f"G12: 発話速度 {rate:.2f} mora/s が範囲外（音素化を疑え）")


def check_pack(index: np.ndarray, rates: list[float]) -> list[str]:
    """パック全体のゲート（G13）。個別発話では見えない系統的なズレを捕まえる。"""
    problems = []
    mean_rate = float(np.mean(rates)) if rates else 0.0
    if not (6.5 <= mean_rate <= 8.5):
        problems.append(f"G13: 平均発話速度 {mean_rate:.2f} mora/s が 6.5–8.5 の外")
    # 範囲は本プロジェクトの構成（中間表現 + prosody=zeros）での実測に合わせてある。
    # canonical + 実 prosody だと 2.56 になるが、zeros では発話が約 5% 短く 2.31（M-20）。
    ratio = float(np.mean(index["frames"] / np.maximum(index["n_ids"], 1)))
    if not (2.15 <= ratio <= 2.55):
        problems.append(f"G13: frames/n_ids の平均 {ratio:.3f} が 2.15–2.55 の外")
    return problems


class PackWriter:
    """shard を順に書き出す。**offset は書いた実バイト数から決める**（推測しない）。"""

    def __init__(self, root: str | pathlib.Path, utts_per_shard: int = UTTS_PER_SHARD):
        self.root = pathlib.Path(root)
        (self.root / "shards").mkdir(parents=True, exist_ok=True)
        self.utts_per_shard = utts_per_shard
        self._rows: list[tuple] = []
        self._meta: list[dict] = []
        self._ids: list[np.ndarray] = []
        self._dts: list[np.ndarray] = []
        self._pros: list[np.ndarray] = []
        self._rates: list[float] = []
        self._zt_sum = np.zeros(Z_CHANNELS, dtype=np.float64)
        self._zt_sqsum = np.zeros(Z_CHANNELS, dtype=np.float64)
        self._zt_min = np.full(Z_CHANNELS, np.inf)
        self._zt_max = np.full(Z_CHANNELS, -np.inf)
        self._n_frames = 0
        self._shard = -1
        self._zt_f = None
        self._yt_f = None
        self._zt_off = 0
        self._yt_off = 0
        self._tok_off = 0
        self._n = 0

    def _roll(self) -> None:
        if self._zt_f:
            self._zt_f.close()
            self._yt_f.close()
        self._shard += 1
        sid = f"{self._shard:04d}"
        self._zt_f = open(self.root / "shards" / f"{sid}.zt.f16", "wb")
        self._yt_f = open(self.root / "shards" / f"{sid}.yt.i16", "wb")
        self._zt_off = 0
        self._yt_off = 0

    def add(self, u: Utterance) -> None:
        check_utt(u)
        if self._n % self.utts_per_shard == 0:
            self._roll()

        zt = u.zT.astype(np.float16)
        yt = np.clip(np.round(u.yT * YT_SCALE), -YT_SCALE, YT_SCALE).astype(np.int16)
        self._zt_f.write(zt.tobytes(order="C"))
        self._yt_f.write(yt.tobytes())

        # チャネル統計は fp32 のまま積む（fp16 に落とす前の値で取る）
        self._zt_sum += u.zT.sum(axis=1)
        self._zt_sqsum += (u.zT.astype(np.float64) ** 2).sum(axis=1)
        self._zt_min = np.minimum(self._zt_min, u.zT.min(axis=1))
        self._zt_max = np.maximum(self._zt_max, u.zT.max(axis=1))
        self._n_frames += u.zT.shape[1]

        crc = zlib.crc32(zt.tobytes()) ^ zlib.crc32(yt.tobytes())
        self._rows.append((self._n, self._shard, len(u.ids), u.zT.shape[1],
                           self._zt_off, self._yt_off, self._tok_off, crc))
        self._meta.append({"seq": self._n, "uid": u.uid, "source": u.source,
                           "text": u.text, "n_ids": len(u.ids), "frames": u.zT.shape[1]})
        self._ids.append(u.ids.astype(np.int32))
        self._dts.append(u.dT.astype(np.float32))
        self._pros.append(u.prosody.astype(np.int16))

        seconds = len(u.yT) / SR
        mora = int(np.sum(~np.isin(u.ids, [0, 1, 2, 3, 7, 8, 9]))) / 2.0
        self._rates.append(mora / seconds if seconds else 0.0)

        self._zt_off += u.zT.shape[1]
        self._yt_off += len(u.yT)
        self._tok_off += len(u.ids)
        self._n += 1

    def close(self, manifest: dict) -> dict:
        if self._zt_f:
            self._zt_f.close()
            self._yt_f.close()

        index = np.array(self._rows, dtype=INDEX_DTYPE)
        np.save(self.root / "index.npy", index)
        with open(self.root / "index.jsonl", "w") as f:
            for m in self._meta:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

        offs = np.cumsum([0] + [len(x) for x in self._ids]).astype(np.int64)
        np.savez(self.root / "tokens.npz",
                 ids=np.concatenate(self._ids) if self._ids else np.zeros(0, np.int32),
                 dT=np.concatenate(self._dts) if self._dts else np.zeros(0, np.float32),
                 prosody=(np.concatenate(self._pros) if self._pros
                          else np.zeros((0, 3), np.int16)),
                 offsets=offs)

        n = max(self._n_frames, 1)
        mu = self._zt_sum / n
        var = np.maximum(self._zt_sqsum / n - mu**2, 0.0)
        np.savez(self.root / "channel_stats.npz",
                 mu_T=mu.astype(np.float32), sigma_T=np.sqrt(var).astype(np.float32),
                 min_T=self._zt_min.astype(np.float32),
                 max_T=self._zt_max.astype(np.float32),
                 n_frames=np.int64(self._n_frames))

        problems = check_pack(index, self._rates)
        manifest = dict(manifest)
        manifest.update({
            "pack_version": 1, "n_utterances": self._n, "n_shards": self._shard + 1,
            "n_frames": int(self._n_frames), "utts_per_shard": self.utts_per_shard,
            "yt_scale": YT_SCALE, "zt_dtype": "float16", "yt_dtype": "int16",
            "pack_gate_problems": problems,
        })
        (self.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1))

        sums = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.name not in ("manifest.json", "SHA256SUMS"):
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                sums.append(f"{h}  {p.relative_to(self.root)}")
        (self.root / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        return manifest


class PackReader:
    """memmap で読む。**学習時に触るのは index.npy と shard だけ。**"""

    def __init__(self, root: str | pathlib.Path):
        self.root = pathlib.Path(root)
        self.index = np.load(self.root / "index.npy")
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        tok = np.load(self.root / "tokens.npz")
        self.ids, self.dT, self.prosody, self.offsets = (
            tok["ids"], tok["dT"], tok["prosody"], tok["offsets"])
        stats = np.load(self.root / "channel_stats.npz")
        self.mu_T, self.sigma_T = stats["mu_T"], stats["sigma_T"]
        self._zt: dict[int, np.memmap] = {}
        self._yt: dict[int, np.memmap] = {}

    def __len__(self) -> int:
        return len(self.index)

    def _shard_zt(self, sid: int) -> np.memmap:
        if sid not in self._zt:
            self._zt[sid] = np.memmap(
                self.root / "shards" / f"{sid:04d}.zt.f16", dtype=np.float16, mode="r")
        return self._zt[sid]

    def _shard_yt(self, sid: int) -> np.memmap:
        if sid not in self._yt:
            self._yt[sid] = np.memmap(
                self.root / "shards" / f"{sid:04d}.yt.i16", dtype=np.int16, mode="r")
        return self._yt[sid]

    def __getitem__(self, i: int) -> dict:
        r = self.index[i]
        z = self._shard_zt(int(r["shard"]))
        y = self._shard_yt(int(r["shard"]))
        frames, n_ids = int(r["frames"]), int(r["n_ids"])
        zs = int(r["zt_off"]) * Z_CHANNELS
        zt = np.asarray(z[zs:zs + Z_CHANNELS * frames]).reshape(Z_CHANNELS, frames)
        yt = np.asarray(y[int(r["yt_off"]):int(r["yt_off"]) + frames * HOP])
        t0 = int(r["tok_off"])
        return {
            "zT": zt.astype(np.float32),
            "yT": yt.astype(np.float32) / YT_SCALE,
            "ids": self.ids[t0:t0 + n_ids],
            "dT": self.dT[t0:t0 + n_ids],
            "prosody": self.prosody[t0:t0 + n_ids],
        }
