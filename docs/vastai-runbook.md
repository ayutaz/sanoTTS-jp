# vast.ai 実行手順（ラベル一括生成 → 本学習）

- 作成 2026-08-27
- 上位: [`README.md`](README.md) / [`decisions.md`](decisions.md)
- スクリプト: [`../deploy/vastai_bootstrap.sh`](../deploy/vastai_bootstrap.sh)

> ## ⚠️ 現在は使っていない（D-027）
>
> **実測したら手元の M4 Max で完結した**ので、本番のラベル生成と学習は
> ローカルでやっている（ラベル生成 CPU 40 分 / 学習 MPS 約 1.3 時間）。
> ローカルのほうが**未検証の CUDA parity ゲートを回避できる**ぶん安全でもある。
>
> この手順は次の場合に使う:
> - λ の探索を並列で回したいとき
> - 1.4 M の z-line が手元で重かったとき
> - 学習を長時間（数日）回したいとき
>
> **手順自体は有効。** ただし CUDA parity ゲート（§3）は依然として未通過。

**この手順は「ローカルで検証した内容がリモートでも同じである」ことを先に確かめてから
本番を走らせるように組んである。** 順番を飛ばさないこと。

---

## 0. なぜ手順が要るか

ローカルと vast.ai で**教師が変われば、ラベルは静かに別物になる**。壊れ方は 3 通りある。

| 何が変わると | どう壊れるか | 塞ぎ方 |
|---|---|---|
| piper-plus のコード | `infer()` の挙動が変わる。例外は出ない | コミットと主要ソースの SHA-256 をピン留め（`src/saanotts_jp/teacher_identity.py`） |
| デバイス (CPU→CUDA) | 数値が僅かにずれる。音は鳴るので気づかない | `b4_device_parity.py --device cuda` を**先に**走らせる（D-015） |
| 生成を 2 回に分ける | 前半と後半で条件が違うパックができる | **一度だけ生成し SHA-256 で固定**（D-015） |

`pyproject.toml` の piper-plus 依存は**ローカルの絶対パス**なので、そのままでは
リモートで `uv sync` が落ちる。`deploy/retarget_sources.py` が
`[tool.uv.sources]` の 2 行だけを書き換える（それ以外が変わっていないことを assert する）。

---

## 1. インスタンス

要求スペックの根拠は [`requirements.md`](requirements.md) §8.4、実測コストは M-18。

| 項目 | 目安 | 理由 |
|---|---|---|
| GPU | RTX 4090 / A5000 級 1 枚 | 生徒が 567 K なので GPU は律速でない |
| ディスク | **60 GB 以上** | ラベルパック train 4.42 GB + heldout + ckpt 927 MB + 依存 |
| RAM | 32 GB 以上 | 教師 ckpt を CPU に持ちながら生成する |
| 課金 | spot $0.13/h 程度 | 学習 1000 epoch で $0.44（M-18） |

**支配的なコストは GPU 時間ではなくディスクと試行回数。** 月 $10〜50 の桁。

## 2. 転送するもの

```
アップロード : このリポジトリ（テキスト 1.2 MB + スクリプト）。data/pack は送らない
インスタンス : ckpt 927 MB を HF から直接取得 → ラベル生成 → 学習
ダウンロード : 生徒の重み（数 MB）+ 学習ログ + reports/
```

`HF_TOKEN` を環境変数で渡すこと（教師 repo は private）。

## 3. 手順

```bash
export HF_TOKEN=...                       # 教師 repo は private
bash deploy/vastai_bootstrap.sh setup     # uv / piper-plus をピン留めで clone / 依存 / ckpt / Phase 0 検証
bash deploy/vastai_bootstrap.sh parity    # ★ ゲート。ここで止まって結果を読む
```

### ★ parity ゲートの読み方（D-015）

`reports/b4_device_parity_cuda.json` を**必ず人間が読む**。判断基準:

- **bit 一致は期待しない。** ローカルの CPU vs MPS は SNR 97〜106 dB で一致しなかった。
- 見るのは **int16 量子化後に何サンプルずれるか**。パックの `yT` は int16 固定 scale で
  それ自体が SNR 76.9 dB なので、**デバイス差がそれより十分小さければ実害が無い**。
  ローカル MPS では int16 化後に差が出たのは 0.4% のサンプルで、いずれも 1 LSB だった。
- **CUDA は未検証。** TF32 が効くと差が広がりうる。不安なら
  `TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=0` を付けて測り直して両方を manifest に残す。

通ったら:

```bash
bash deploy/vastai_bootstrap.sh labels    # 本番ラベル生成（**一度だけ**）
bash deploy/vastai_bootstrap.sh train     # 生徒の学習
```

`labels` は `data/pack` が既にあると止まる。**再生成は D-015 違反**なので、
消すなら意図して消すこと（消した理由を manifest に残す）。

## 4. 学習の走らせ方

```bash
STAGES="1 2 3 4" STEPS=20000 bash deploy/vastai_bootstrap.sh train
```

⚠️ **`STEPS` の値には論文の根拠が無い。** 論文は学習ステップ数・lr スケジュール・
バッチサイズを書いていない。まず小さく回して損失曲線を見て決めること。
`scripts/bench_train_step.py` に 1 ステップの実測がある。

**学習曲線を 512 / 5,000 / 20,946 行の 3 水準で取る。** 論文はデータ量について
2 点（512 → 1.72 / 14,343 → 2.54）しか持っておらず飽和点を書いていない。
追加コストは 1 水準あたり $1 未満（M-18）。

## 5. 持ち帰るもの

```
生徒の重み（stage ごと）      数 MB
reports/                      parity / 学習ログ / 評価
data/pack/manifest.json       SHA-256・生成環境（**パック本体は持ち帰らない**）
```

パック本体 4.42 GB は持ち帰らない。再現には manifest とテキストがあれば足りる
（同じ環境・同じデバイスなら再生成できる）。
