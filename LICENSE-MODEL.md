# sanoTTS-jp Model License 1.0

`SPDX-License-Identifier: LicenseRef-sanoTTS-jp-Model-1.0`

*[English below](#english)*

---

## ⚠️ このファイルが必要な理由

**リポジトリの [`LICENSE`](LICENSE)（MIT）は、配布されるモデルの重みには適用されません。**

MIT は「無制限に (without restriction)」の利用を認めます。しかしこのモデルの重みは
**つくよみちゃんコーパス**を素材に含む教師モデルからの蒸留物であり、そのコーパスの条件は

- **帰属表示を必須**とし、
- **出力の用途に禁止事項**を課し、
- **その義務が再配布を受けた側にも伝播する**

と定めています。したがって **MIT を名乗ることは、こちらが持っていない権利を
持っているかのように宣言すること**になります。そこで重みだけを別条件で配布します。

| 対象 | ライセンス |
|---|---|
| このリポジトリの**コードとドキュメント** | [MIT](LICENSE) |
| **配布されるモデルの重み**（下記「1. 適用範囲」） | **このファイル** |

---

## 1. 適用範囲

本ライセンスは、GitHub Release で配布される次の成果物（以下「本モデル」）に適用されます。

| ファイル | 内容 |
|---|---|
| `saanotts-jp-v3-stage4.pt` | PyTorch checkpoint（fp32、3 つの生徒モデル） |
| `saanotts-jp-v3-int8.bin` | C99 コア用 int8 重みブロブ（SAAN 形式。v0.2.0 の資産は v1、2026-09-02 以降のコアは v2 を読む） |
| `saanotts-jp-v3-fp32.bin` | 同 fp32 版 |
| `golden-v3-fp32.bin` / `golden-v3-int8.bin` | 移植検証用のゴールデン中間出力 |
| `samples/*.wav` | 本モデルが生成した音声サンプル |
| `esp32s3-firmware-*.bin` | **重みを含む** ESP32-S3 用の flash イメージ（v0.1.1 以降） |

本モデルから派生したもの（ファインチューン、量子化、変換、蒸留の結果を含む）、
および**本モデルが生成した音声**にも、本ライセンスの条件が及びます。

⚠️ **自分でビルドした firmware にも重みが入ります。** `esp32/` も
`esp32/boards/m5unified/`（M5Stack）も、int8 blob を**パーティションまたは `.rodata`**
としてイメージに埋め込みます。配布するなら §3 の義務に加えて、
[`NOTICE.md`](NOTICE.md) の第三者コード（Open JTalk / M5Unified / M5GFX / IPA フォント /
辞書）の表示も要ります。

## 2. 許諾

上記の条件に従う限り、**無償で**、次のことを行えます。

- 使用（**商用利用を含む**）
- 複製・改変・派生物の作成
- 再配布・サブライセンス

## 3. 条件

### 3.1 帰属表示（必須）

本モデルまたはその派生物を再配布する場合、次のブロックを**そのまま**、
配布物の `NOTICE` / `README` / クレジット表示のいずれかに含めてください。

```
This model was distilled from a piper-plus teacher model.
sanoTTS-jp — https://github.com/ayutaz/sanoTTS-jp

つくよみちゃんコーパス
  本ソフトウェアの音声合成には、フリー素材キャラクター「つくよみちゃん」
  （© 夢前黎）が無料公開している音声データを使用しています。
  https://tyc.rei-yumesaki.net/material/corpus/

MOE-Speech (litagin) — https://huggingface.co/spaces/litagin/moe-speech-license
  著作権法 30 条の 4（情報解析のための利用）に基づき学習に使用。

蒸留に使用したテキストコーパス:
  - Common Voice ja (Mozilla) — CC0-1.0
      https://github.com/common-voice/common-voice
  - ROHAN4600 (森勢将雅) — CC0-1.0
      https://github.com/mmorise/rohan4600
  - ITA コーパス — CC0-1.0
      https://github.com/mmorise/ita-corpus
  - JSUT ver1.1 (高道慎之介) — CC-BY-SA-4.0 ほか（subset 別）
      https://sites.google.com/site/shinnosuketakamichi/publication/jsut

教師実装: piper-plus (MIT) — https://github.com/ayutaz/piper-plus
```

⚠️ **1 行でも欠けると、対応する素材の条件に違反します。**
これは本プロジェクトが課した条件ではなく、**上流から伝播してきた義務**です。

### 3.2 出力の用途制限（必須・伝播する）

つくよみちゃんコーパスの条件により、**本モデルが生成した音声**は次に使えません。

| 禁止 | 内容 |
|---|---|
| ❌ 個人・団体への攻撃・批判 | |
| ❌ 政治・宗教上の主張 | |
| ❌ アダルト用途 | |
| ❌ 素材としての再配布 | 生成音声そのものを「音声素材集」として配布すること |

一次ソース: <https://tyc.rei-yumesaki.net/material/corpus/>
（**条件は提供元が更新しうるため、配布・利用の前に一次ソースを確認してください。
一次ソースと本ファイルが食い違う場合、一次ソースが優先します。**）

### 3.3 条件の伝播（必須）

本モデルまたはその派生物を第三者に配布する場合、**3.1 と 3.2 を第三者にも課して
ください**。これらの義務を外して配布することはできません。

### 3.4 やってはいけない表示

- ❌ 本モデルを **MIT / Apache-2.0 / CC0 など無制限のライセンスで再配布すること**
- ❌ つくよみちゃん（© 夢前黎）が本プロジェクトを推奨・承認していると示唆すること
- ❌ 本モデルを arXiv:2608.21378 の**著者らによる公式実装**であると示唆すること
  （本リポジトリは論文からの独立再実装です。[`NOTICE.md`](NOTICE.md) 参照）

## 4. 無保証・免責

本モデルは **現状のまま (AS IS)** 提供され、明示・黙示を問わずいかなる保証もありません。
商品性・特定目的適合性・権利非侵害の保証を含みますが、これらに限りません。
本モデルの使用または使用不能から生じたいかなる損害についても、
著作権者および提供者は責任を負いません。

⚠️ **本モデルは検証 (PoC) の成果物であり、製品品質ではありません。**
既知の制約は [`MODEL_CARD.md`](MODEL_CARD.md) を参照してください。

## 5. 既知の法的リスク（隠さずに書きます）

⚠️ 蒸留に使ったテキストのうち **JSUT ver1.1 の 6,472 行は CC-BY-SA-4.0**（継承付き）です。

「学習済みモデルは学習テキストの二次的著作物である」という立場を取られた場合、
本モデルにも CC-BY-SA の継承が及ぶ可能性があります。本プロジェクトは

- 日本の著作権法 **30 条の 4**（情報解析のための利用）により学習自体が許されること
- **コーパス本文を再配布していない**こと

から実務上この立場が通る公算は低いと判断しましたが、**リスクはゼロではありません**
（[`docs/decisions.md`](docs/decisions.md) D-035）。

⚠️ **本節を含む本ファイルの法的評価は、本プロジェクトによる一次ソースの読解であり、
弁護士による法的助言ではありません。** 重要な用途に使う場合はご自身で確認してください。

## 6. 上流の条件（要約）

| 素材 | 経路 | 条件 |
|---|---|---|
| つくよみちゃんコーパス（© 夢前黎） | 教師の学習音声 | 30 条の 4 ベースの独自ライセンス。**モデル配布は明示的に許可**。帰属表示必須・出力に禁止用途・義務が伝播 |
| MOE-Speech (litagin) | 教師 base の日本語 | `license: other`（30 条の 4） |
| Common Voice ja / ROHAN4600 / ITA | 蒸留テキスト | CC0-1.0 |
| JSUT ver1.1 | 蒸留テキスト | CC-BY-SA-4.0 ほか（**唯一の継承付き**） |
| piper-plus | 教師の実装 | MIT |

詳細と一次ソースは [`NOTICE.md`](NOTICE.md) にあります。

---

<a name="english"></a>

# English

## Why this file exists

**The repository's [`LICENSE`](LICENSE) (MIT) does NOT cover the distributed model weights.**

MIT grants use "without restriction." These weights are distilled from a teacher
trained on the **Tsukuyomi-chan Corpus**, whose terms require attribution, restrict
what the generated audio may be used for, and **propagate those obligations to
downstream recipients**. Calling the weights MIT would claim rights we do not have.

| Covered | License |
|---|---|
| Code and documentation in this repository | [MIT](LICENSE) |
| **Distributed model weights** (§1 above) | **This file** |

## Grant

Free of charge, including commercial use: use, copy, modify, create derivatives,
redistribute, and sublicense — **subject to the conditions below.**

## Conditions

1. **Attribution (mandatory).** Reproduce the attribution block in §3.1 verbatim in
   your `NOTICE`, `README`, or credits. Every line. These obligations come from
   upstream, not from this project.
2. **Output-use restrictions (mandatory, propagating).** Audio generated by this
   model may **not** be used for: attacks or criticism of individuals or
   organizations; political or religious advocacy; adult content; or redistribution
   as a voice-material library. Primary source:
   <https://tyc.rei-yumesaki.net/material/corpus/> — **check it before you
   distribute; if it conflicts with this file, the primary source wins.**
3. **Pass the conditions on.** You may not strip conditions 1 and 2 when
   redistributing.
4. **Do not** relicense these weights as MIT / Apache-2.0 / CC0, imply endorsement by
   Tsukuyomi-chan (© Rei Yumesaki), or present this as the official implementation of
   arXiv:2608.21378 (it is an independent re-implementation).

## No warranty

Provided **AS IS**, without warranty of any kind. This is a proof-of-concept
artifact, not a production-quality model. See [`MODEL_CARD.md`](MODEL_CARD.md).

## Known legal risk

6,472 of the distillation sentences come from **JSUT ver1.1 (CC-BY-SA-4.0)**. If a
trained model were held to be a derivative work of its training text, share-alike
could reach these weights. We judged this unlikely (Japanese Copyright Act Art. 30-4;
we do not redistribute the corpus text) but **the risk is not zero**. This is our own
reading of primary sources, **not legal advice**.
