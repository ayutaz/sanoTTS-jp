# A-3: ラベルパック形式の決定（測定スクリプト）

`reports/a3_pack_design.json` の全数値を出したスクリプト。実行順:

```bash
S=scripts/a3
# 1) 教師から本物のラベルを 128 文ぶん取る（fp32 の生 npz。約 84 MB / 19 秒）
uv run python $S/gen_labels_sample.py --n 128 --out <work>/raw
cd <work>
# 2) train split 全行の n_ids（コーパス規模への外挿に使う。8 秒）
uv run --project /Users/s19447/Desktop/saanoTTS-jp python $S/phonemize_all.py
# 3) zT の量子化が L_c に与える影響
uv run --project ... python $S/quant_probe.py        # → quant_zT.json
# 4) yT の int16 化 + STFT 事前計算のサイズ
uv run --project ... python $S/quant_y.py            # → quant_yT.json
# 5) 格納形式 7 種のサイズ / 書き込み / ランダムアクセス
uv run --project ... --with h5py python $S/fmt_bench.py   # → fmt_bench.json
# 6) コーパス規模への外挿・SHA-256 コスト・shard 粒度
uv run --project ... python $S/scale_probe.py        # → scale.json
# 7) FLAC / zstd
uv run --project ... --with zstandard python $S/compress_probe.py
# 8) scripts/labelpack.py の往復 bit 一致とゲート発火
uv run --project ... python $S/test_labelpack.py     # → packtest.json
```

`h5py` / `zstandard` は比較のためだけに使うので `uv add` せず `--with` で入れる。
