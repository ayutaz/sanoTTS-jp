---
name: teacher-inference
description: Use when running the piper-plus teacher model in sanoTTS-jp — generating distillation labels (dT/zT/yT), synthesizing reference audio, or comparing teacher output. Covers the six silent failure modes that produce wrong labels without raising an exception.
---

# 教師モデルを正しく呼ぶ

## 原則

**教師の呼び出しは 6 箇所で黙って壊れる。** どれも例外を出さず、
出力は「それらしい音声」になるので聴くまで気づかない。
ラベルを 20,946 文分作り直すのは 1 時間かかる。呼ぶ前にこの表を確認する。

## 6 つの沈黙する失敗

| # | 間違い | 結果 | 正しく |
|---|---|---|---|
| 1 | `sys.path` を通さず import | v1.13.0 相当の **stale な `piper_train`** を掴む | `uv run` を使う。スクリプトは `__file__` を assert |
| 2 | EMA を適用しない | `yT` の SNR が **14.5 dB** しかない（`zT`/`dT` は bit 一致） | `apply_ema_shadow_params()` を **`remove_weight_norm()` の前に**。戻り値を `applied == len(shadow) and skipped == 0` で assert する |
| 3 | `speaker_embeddings=<npy>` を渡す | **bit 完全に無視される**（`spk_proj` が無い） | `None` を渡す |
| 4 | `lid` を省略 | 総フレーム数が変わり `z` が別物 | `lid=torch.tensor([0])`（ja） |
| 5 | prosody の条件を**混ぜる** | `prosody_proj(0)=bias` が concat され**None / ゼロ / 実値で総フレームが 3 通り変わる** | 本プロジェクトは**ゼロで統一**（D-014）。デバイスが A1/A2/A3 を供給できないため。UTMOS に有意差なし (p=0.72) |
| 6 | 自前で音素→ID 変換 | intersperse padding が抜け **2.4 倍速** | `text_to_phoneme_ids_and_prosody` に `language_id_map` を渡す |

## 最小の正しい呼び出し

```python
from piper_train.export_onnx import apply_ema_shadow_params
from piper_train.vits.commons import normalize_checkpoint_state_dict
from piper_train.vits.models import SynthesizerTrn
from piper_train.infer_onnx import text_to_phoneme_ids_and_prosody

ckpt = torch.load(snap + "epoch=499-step=22000.ckpt", map_location="cpu", weights_only=False)
model = SynthesizerTrn(**kwargs_from(ckpt["hyper_parameters"]))

sd, stats = normalize_checkpoint_state_dict(
    {k[len("model_g."):]: v for k, v in ckpt["state_dict"].items() if k.startswith("model_g.")},
    model.state_dict())
result = model.load_state_dict(sd, strict=False)
assert not result.missing_keys and not result.unexpected_keys   # 0 / 0 になるはず
model.eval()

shadow = ckpt["ema_generator_state"]["shadow_params"]
applied, skipped = apply_ema_shadow_params(model.dec, shadow)   # ← 順序が命
# ⚠️ **「applied > 0」では通ってしまう。** remove_weight_norm() の後に呼ぶと
# 53 個中 23 個だけ当たり、EMA が半分載った「第三の重み」になる（D6 の実測）
assert applied == len(shadow) and skipped == 0, (applied, len(shadow), skipped)
model.dec.remove_weight_norm()

ids, prosody = text_to_phoneme_ids_and_prosody(
    text, phoneme_id_map, language="ja", language_id_map=lim)   # lim を必ず渡す
pv = [[p["a1"], p["a2"], p["a3"]] if p else [0, 0, 0] for p in prosody]

with torch.no_grad():
    out = model.infer(
        torch.tensor([ids]), torch.tensor([len(ids)]),
        lid=torch.tensor([0]),          # ja。焼き込まれていないので必須
        noise_scale=0.0,                # 論文 §II: 決定的にする
        noise_scale_w=0.0,
        length_scale=1.0,
        prosody_features=torch.tensor([pv]).float(),
        speaker_embeddings=None)        # 何を渡しても無視される

yT, zT, dT = out.audio, out.latents[0], out.durations
```

実装例: `scripts/phase0_verify_teacher.py`

## 呼んだあとの健全性チェック

**ラベルを保存する前に必ず assert する。** どれか 1 つでも落ちたら生成を止める。

```python
assert zT.shape[1] == 192                        # 論文の教師潜在
assert zT.shape[-1] * 256 == yT.shape[-1]        # hop 256
assert dT.shape[-1] == len(ids)
assert max(ids) < 173                            # num_symbols。185 entry あるが有効は 0..172
assert 6.0 <= mora_count / (yT.shape[-1] / 22050) <= 10.0   # 自然な発話速度
```

**発話速度チェックは特に効く。** 音素化を間違えると 17.7 mora/s になるが、
音声としては再生できてしまうので他のチェックは全部通る。

## ラベルパックに一緒に保存するもの

- **チャネルごとの `μ_T`, `σ_T`** — 損失 `L_c` の正規化項と、推論時の摩擦音ノイズ注入 `σT_k` の両方で要る
- 生成環境（Python / torch / CUDA のバージョン）と SHA-256
  — GPU 推論が CPU と bit 一致するかは**未検証**（`measurements.md` M-15）

## ラベル一括生成の前に塞ぐ 2 つの欠陥

どちらも例外を出さずにデータを壊す。詳細は計画書 §2 B-1 / B-2。

1. **G2P の言語誤ルーティング** — `MultilingualPhonemizer` はかなを文全体で判定するため、
   かなを 1 文字も含まない行が**丸ごと中国語音素**になる。コーパスの **5.36%** が該当
2. **`prosody_features` の無警告ズレ** — `PiperEncoder._convert_prosody` が
   長さを強制的に揃えるため `strict=True` が発火しない

## 環境

```bash
uv run python scripts/...
```

piper-plus は**読み取り専用**（`docs/decisions.md` D-003）。checkout / commit / 編集をしない。
`v1.13.0` への checkout は**不要**（v2.0 HEAD で missing 0 / unexpected 0）。
