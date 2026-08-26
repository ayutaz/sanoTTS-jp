#!/usr/bin/env python3
"""labelpack の往復テストと健全性ゲートの発火テスト。

**ゲートは「落ちるべきものが落ちる」ことを確かめないと意味がない。**
B-0 の hook で「止めてはいけないものを厚く書く」教訓を得たが（C-011）、
ゲートは逆で「止めるべきものを厚く書く」。両方を検証する。

実行:
    uv run python scripts/test_labelpack.py
"""

from __future__ import annotations

import sys
import tempfile

import numpy as np

sys.path.insert(0, "src")
from saanotts_jp.labelpack import (  # noqa: E402
    HOP, SR, GateFailure, PackReader, PackWriter, Utterance, check_utt,
)


def make_utt(seq: int = 0, frames: int = 200, n_ids: int | None = None,
             rng: np.random.Generator | None = None,
             intersperse: bool = True) -> Utterance:
    """ゲートを通る正常な発話を合成する。

    **実際の教師出力と同じ構造にする**こと。canonical 経路は
    `^ t0 _ t1 _ ... tn $` の intersperse padding を入れるので（M-5 / D-005）、
    それを持たない合成データでテストしてもゲートを検証したことにならない。

    実データの比率（A-3 の 128 文実測）: frames 379.6 / n_ids 148.2 = **2.56**
    """
    rng = rng or np.random.default_rng(seq)
    if n_ids is None:
        # 実データの frames/n_ids ≒ 2.56 に合わせる。
        # n_ids = 2*tokens + 3 なので tokens = (n_ids-3)/2、
        # mora = tokens/2、発話速度 = mora / (frames*256/22050)
        n_ids = max(5, int(round(frames / 2.56)))
        if n_ids % 2 == 0:
            n_ids += 1   # ^ + (token,_)*k + token + $ は奇数長

    ids = np.zeros(n_ids, dtype=np.int32)
    ids[0], ids[-1] = 1, 2                      # ^ ... $
    if intersperse:
        # 中身は token, PAD, token, PAD, ... token（非ゼロが連続しない）
        body = np.arange(1, n_ids - 1)
        ids[body[::2]] = rng.integers(10, 65, size=len(body[::2]))
    else:
        ids[1:-1] = rng.integers(10, 65, size=n_ids - 2)

    # ceil(dT).sum() == frames をちょうど満たす dT を作る
    base = rng.random(n_ids) + 0.5
    d = np.maximum(np.floor(base / base.sum() * frames), 1.0)
    d[np.argmax(d)] += frames - d.sum()
    dT = (d - 0.4).astype(np.float32)           # ceil して d に戻る値
    assert int(np.ceil(dT).sum()) == frames

    return Utterance(
        ids=ids,
        dT=dT,
        prosody=rng.integers(-8, 8, size=(n_ids, 3)).astype(np.int16),
        zT=(rng.standard_normal((192, frames)) * 2.0).astype(np.float32),
        yT=(rng.standard_normal(frames * HOP) * 0.1).astype(np.float32),
        text=f"テスト文{seq}",
        source="synthetic",
        uid=f"t{seq:04d}",
    )


def test_roundtrip() -> int:
    print("=== 往復テスト ===")
    n = 300
    with tempfile.TemporaryDirectory() as tmp:
        w = PackWriter(tmp, utts_per_shard=128)
        originals = []
        for i in range(n):
            u = make_utt(i, frames=150 + (i % 90))
            originals.append(u)
            w.add(u)
        manifest = w.close({"note": "test"})
        print(f"  書き込み {manifest['n_utterances']} 発話 / "
              f"{manifest['n_shards']} shard / {manifest['n_frames']:,} frames")
        if manifest["pack_gate_problems"]:
            for p in manifest["pack_gate_problems"]:
                print(f"  ⚠️ パックゲート: {p}")

        r = PackReader(tmp)
        assert len(r) == n, f"件数不一致 {len(r)} != {n}"

        bad = 0
        z_err, y_err = [], []
        for i in (0, 1, 127, 128, 129, 255, 299):
            got, exp = r[i], originals[i]
            if not np.array_equal(got["ids"], exp.ids):
                print(f"  NG idx={i}: ids 不一致"); bad += 1
            if not np.allclose(got["dT"], exp.dT, atol=1e-6):
                print(f"  NG idx={i}: dT 不一致"); bad += 1
            if not np.array_equal(got["prosody"], exp.prosody):
                print(f"  NG idx={i}: prosody 不一致"); bad += 1
            if got["zT"].shape != exp.zT.shape:
                print(f"  NG idx={i}: zT 形状 {got['zT'].shape} != {exp.zT.shape}"); bad += 1
                continue
            z_err.append(float(np.abs(got["zT"] - exp.zT).max()))
            y_err.append(float(np.abs(got["yT"] - exp.yT).max()))

        print(f"  ids / dT / prosody: {'一致' if bad == 0 else str(bad) + ' 件 NG'}")
        print(f"  zT fp16 の最大誤差 : {max(z_err):.5f}  (|zT| ~ 2.0 に対して)")
        print(f"  yT int16 の最大誤差: {max(y_err):.2e}  (1/32767 = {1/32767:.2e})")
        # fp16 の相対精度は 2^-11 ≒ 4.9e-4。|z| max ~8 なら誤差 4e-3 程度が上限
        assert max(z_err) < 0.02, "fp16 の誤差が想定より大きい"
        assert max(y_err) < 2 / 32767, "int16 の誤差が想定より大きい"

        # チャネル統計が保存され、まともな値か
        assert r.mu_T.shape == (192,) and r.sigma_T.shape == (192,)
        assert np.isfinite(r.mu_T).all() and (r.sigma_T > 0).all()
        print(f"  channel_stats: mu_T {r.mu_T.mean():+.4f} / "
              f"sigma_T {r.sigma_T.mean():.4f}（式3 と式7 で必須）")
    return bad


def test_gates() -> int:
    print("\n=== ゲートの発火テスト（落ちるべきものが落ちるか）===")
    cases = []

    u = make_utt(); cases.append(("G1 zT が 192ch でない",
                                  Utterance(**{**u.__dict__, "zT": u.zT[:64]})))
    u = make_utt(); cases.append(("G2 hop 不一致",
                                  Utterance(**{**u.__dict__, "yT": u.yT[:-256]})))
    u = make_utt(); d = u.dT.copy(); d[0] += 5
    cases.append(("G3 ceil(dT) 不一致", Utterance(**{**u.__dict__, "dT": d})))
    u = make_utt(); cases.append(("G4 長さ不一致",
                                  Utterance(**{**u.__dict__, "dT": u.dT[:-1]})))
    u = make_utt(); i = u.ids.copy(); i[1] = 200
    cases.append(("G5 音素ID が 173 以上", Utterance(**{**u.__dict__, "ids": i})))
    u = make_utt(); i = u.ids.copy(); i[0] = 5
    cases.append(("G6 BOS が無い", Utterance(**{**u.__dict__, "ids": i})))
    u = make_utt(); z = u.zT.copy(); z[0, 0] = np.nan
    cases.append(("G8 NaN を含む", Utterance(**{**u.__dict__, "zT": z})))
    u = make_utt(); cases.append(("G9 無音",
                                  Utterance(**{**u.__dict__, "yT": u.yT * 0})))
    u = make_utt(); cases.append(("G10 クリップ",
                                  Utterance(**{**u.__dict__, "yT": u.yT * 100})))
    # G7: intersperse padding が無い = 自前で音素→ID を組んだ証拠。
    # **これを見逃すと発話が 2.4 倍速になる**（M-5）。他のゲートは全部通ってしまう
    cases.append(("G7 intersperse 欠落（自前音素化）",
                  make_utt(frames=200, intersperse=False)))
    # G12: 発話速度そのものが範囲外
    cases.append(("G12 発話速度が遅すぎ", make_utt(frames=400, n_ids=21)))
    cases.append(("G12 発話速度が速すぎ", make_utt(frames=60, n_ids=201)))

    failures = 0
    for name, utt in cases:
        try:
            check_utt(utt)
            print(f"  NG! [通過] {name}  ← 止まるべきなのに通った")
            failures += 1
        except GateFailure as exc:
            print(f"  OK  [{str(exc).split(':')[0]:>3}] {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  NG! [{type(exc).__name__}] {name}: {exc}")
            failures += 1

    # 正常な発話は通ること（誤検知チェック）
    for frames in (100, 200, 400, 800):
        try:
            check_utt(make_utt(frames=frames))
        except GateFailure as exc:
            print(f"  NG! 正常な発話 (frames={frames}) が落ちた: {exc}")
            failures += 1
    print(f"  OK  正常な発話 4 通りは通過（誤検知なし）")
    return failures


def main() -> int:
    bad = test_roundtrip() + test_gates()
    print()
    print("すべて期待通り" if bad == 0 else f"{bad} 件 NG")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
