"""train split 全行を canonical 経路で音素化し、n_ids を出す（frames 推定用）。"""
import csv, glob, json, sys, time, warnings
import numpy as np
warnings.filterwarnings("ignore")
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody

snap = glob.glob("/Users/s19447/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/")[0]
cfg = json.load(open(snap + "config.json"))
pid = cfg["phoneme_id_map"]; lim = cfg["language_id_map"]
rows = list(csv.DictReader(open("/Users/s19447/Desktop/saanoTTS-jp/data/splits/corpus_train.tsv"), delimiter="\t"))
lim_n = int(sys.argv[1]) if len(sys.argv) > 1 else len(rows)
rows = rows[:lim_n]
out = []
t0 = time.perf_counter()
bad = 0
for i, r in enumerate(rows):
    try:
        ids, _ = text_to_phoneme_ids_and_prosody(r["text"], pid, language="ja", language_id_map=lim)
    except Exception:
        bad += 1; continue
    out.append(len(ids))
    if (i+1) % 2000 == 0:
        print(f"  {i+1}/{len(rows)} {(time.perf_counter()-t0)/(i+1)*1000:.1f} ms/行", flush=True)
a = np.array(out)
np.save("n_ids_train.npy", a)
print(f"n={len(a)} bad={bad} elapsed={time.perf_counter()-t0:.1f}s")
print("n_ids mean %.2f p50 %d p90 %d p99 %d max %d  >400: %d" % (a.mean(), np.percentile(a,50), np.percentile(a,90), np.percentile(a,99), a.max(), (a>400).sum()))
