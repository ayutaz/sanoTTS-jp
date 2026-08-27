"""labelpack.py の往復 bit 一致とゲート発火を実ラベル 128 文で検証する。"""
import glob, json, os, shutil, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # scripts/ を通す
from labelpack import PackWriter, PackReader, check_utt, GateError, PCM_SCALE

snap = glob.glob("~/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/")[0]
pid = json.load(open(snap + "config.json"))["phoneme_id_map"]
inv = {}
for k, v in pid.items(): inv.setdefault(v[0] if isinstance(v, list) else v, k)

IX = json.load(open("raw/index.json"))
OUT = "packtest"
if os.path.isdir(OUT): shutil.rmtree(OUT)

w = PackWriter(OUT, utts_per_shard=32)
src = []
t0 = time.perf_counter()
gated = 0
for r in IX["rows"]:
    d = np.load(f"raw/{r['seq']:06d}.npz")
    ids, pros, dT, zT, yT = d["phoneme_ids"], d["prosody"], d["dT"], d["zT"], d["yT"]
    toks = [inv.get(int(i), "?") for i in ids]
    try:
        check_utt(ids, pros, dT, zT, yT, toks)
    except GateError as e:
        gated += 1; print("  GATE:", r["seq"], e); continue
    w.add(dict(source=r["source"], id=r["id"], text=r["text"]), ids, pros, dT, zT, yT)
    src.append((ids, pros, dT, zT, yT))
w.close(dict(python=sys.version.split()[0], torch="2.13.0",
             device="cpu", ckpt_sha256="f375c749caa2a707b3fc9ee672142bdc1441bcbcdd3b523dd9efdb18b017683e",
             noise_scale=0.0, noise_scale_w=0.0, length_scale=1.0, lid=0,
             speaker_embeddings=None, corpus="data/splits/corpus_train.tsv (sample 128)"))
print(f"write {time.perf_counter()-t0:.2f}s  gated={gated}  n={len(src)}")

rd = PackReader(OUT)
assert len(rd) == len(src)
ok = True
for i, (ids, pros, dT, zT, yT) in enumerate(src):
    ri, rd_, rp = rd.tokens(i)
    ok &= np.array_equal(ri, ids) and np.array_equal(rd_, dT) and np.array_equal(rp, pros)
    ok &= np.array_equal(rd.latent(i), zT.astype(np.float16))
    ok &= np.array_equal(rd.audio(i), np.rint(np.clip(yT, -1, 1) * PCM_SCALE).astype(np.int16))
print("往復 bit 一致 (tokens/zT fp16/yT int16):", ok)

# ゲートが実際に発火するか（意図的に壊す）
cases = {}
ids, pros, dT, zT, yT = src[0]
def t(name, fn):
    try: fn(); cases[name] = "NOT FIRED"
    except GateError as e: cases[name] = str(e)[:48]
t("frames 不一致", lambda: check_utt(ids, pros, dT, zT[:, :-1], yT, None))
t("ceil(dT) 不一致", lambda: check_utt(ids, pros, dT * 0.5, zT, yT, None))
t("音素 id >= 173", lambda: check_utt(np.where(np.arange(len(ids)) == 3, 173, ids), pros, dT, zT, yT, None))
t("NaN", lambda: check_utt(ids, pros, dT, np.where(np.arange(zT.size).reshape(zT.shape) == 5, np.nan, zT), yT, None))
t("全ゼロ音声", lambda: check_utt(ids, pros, dT, zT, np.zeros_like(yT), None))
t("intersperse 無し", lambda: check_utt(np.array([1] + [10] * (len(ids) - 2) + [2], np.int32), pros, dT, zT, yT, None))
t("発話速度 2.4x", lambda: check_utt(ids, pros, dT, zT, yT, [inv.get(int(i), "?") for i in ids] * 3))
t("dT に 0", lambda: check_utt(ids, pros, np.where(np.arange(len(dT)) == 2, 0.0, dT), zT, yT, None))
for k, v in cases.items(): print(f"  [{'OK  ' if v!='NOT FIRED' else 'FAIL'}] {k:18s} -> {v}")

# ランダムアクセス速度（この形式の実測）
rng = np.random.default_rng(1); order = rng.integers(0, len(rd), 3000)
for nm, fn in [("full", lambda i: rd.latent(i).size + rd.audio(i).size + rd.tokens(i)[0].size),
               ("tokens only", lambda i: rd.tokens(i)[0].size)]:
    t0 = time.perf_counter(); s = 0
    for i in order: s += fn(int(i))
    print(f"  read {nm:12s} {(time.perf_counter()-t0)/len(order)*1e6:7.1f} us/utt")
du = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs)
print(f"pack 実サイズ {du/1e6:.2f} MB / {len(rd)} 文  (manifest+index+stats のオーバーヘッド込み)")
print("channel_stats: sigma min %.4f max %.4f n_frames %d" % (rd.sigma_T.min(), rd.sigma_T.max(), rd.n_frames))
print("shard 数:", len(glob.glob(f"{OUT}/shards/*.zt.f16")))
json.dump(dict(roundtrip_bit_exact=bool(ok), gates=cases, pack_bytes=du, n=len(rd)),
          open("packtest.json","w"), ensure_ascii=False, indent=1)
