"""R-3: 教師 MBiSTFTGenerator (model_g.dec.*) のパラメータ数を ckpt から直接数える。
読み取り専用。piper-plus は触らない。"""
import glob, json, re, torch, collections

snap = glob.glob("/Users/s19447/.cache/huggingface/hub/models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/")[0]
ck = torch.load(snap + "epoch=499-step=22000.ckpt", map_location="cpu", weights_only=False)
sd = ck["state_dict"]
hp = ck["hyper_parameters"]
print("hyper_parameters (decoder 関連):")
for k in ("resblock","resblock_kernel_sizes","resblock_dilation_sizes","upsample_rates",
          "upsample_initial_channel","upsample_kernel_sizes","inter_channels","gin_channels",
          "filter_length","hop_length","segment_size","sample_rate"):
    if k in hp: print(f"  {k} = {hp[k]}")
print("  ※ mb_istft 関連 (n_fft/hop/subbands) の hp キー:",
      [k for k in hp if "subband" in k or "istft" in k.lower() or k in ("gen_istft_n_fft","gen_istft_hop_size")])

dec = {k[len("model_g.dec."):]: v for k, v in sd.items() if k.startswith("model_g.dec.")}
print(f"\ndec テンソル数 = {len(dec)}")

# weight_norm パラメータは weight_g/weight_v で 2 本持つが、実効パラメータ数は
# 融合後の weight と同じ shape。両方数えると二重計上になるので、
# (a) 生の state_dict 合計 と (b) 融合後の実効数 を両方出す。
raw = sum(v.numel() for v in dec.values())

groups = collections.OrderedDict()
def bucket(name):
    if name.startswith("conv_pre"): return "conv_pre"
    if name.startswith("ups."): return f"ups.{name.split('.')[1]}"
    if name.startswith("resblocks."): return f"resblocks.{name.split('.')[1]}"
    if name.startswith("subband_conv_post"): return "subband_conv_post"
    if name.startswith("cond_layers."): return f"cond_layers.{name.split('.')[1]}"
    if name.startswith("cond"): return "cond (input FiLM)"
    return "other:" + name
for k, v in dec.items():
    groups.setdefault(bucket(k), []).append((k, tuple(v.shape), v.numel()))

print("\n=== 生の state_dict（weight_g/weight_v を両方数えた値）===")
tot = 0
for g, items in groups.items():
    n = sum(i[2] for i in items)
    tot += n
    print(f"{g:24s} {n:9,d}   ({len(items)} tensors)")
print(f"{'RAW TOTAL':24s} {tot:9,d}")

# 融合後の実効パラメータ数: weight_g を除外し weight_v を weight として数える
eff = 0
detail = collections.OrderedDict()
for k, v in dec.items():
    if k.endswith("weight_g"):
        continue
    eff += v.numel()
    detail.setdefault(bucket(k), 0)
    detail[bucket(k)] += v.numel()
print("\n=== 融合後（weight_norm 解除後）の実効パラメータ数 ===")
for g, n in detail.items():
    print(f"{g:24s} {n:9,d}")
print(f"{'EFFECTIVE TOTAL':24s} {eff:9,d}")

print("\n=== 層ごとの shape（全テンソル）===")
for g, items in groups.items():
    print(f"[{g}]")
    for k, s, n in items:
        print(f"    {k:44s} {str(s):22s} {n:9,d}")
