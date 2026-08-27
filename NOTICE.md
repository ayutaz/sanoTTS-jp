# サードパーティとデータの扱い

MIT ライセンス（[`LICENSE`](LICENSE)）が適用されるのは**このリポジトリのコードと
ドキュメント**です。以下はそれぞれの提供元の条件に従います。

## このリポジトリに**含まれていない**もの

再配布の条件が確認できていない、または重いため、**リポジトリには入れていません**。
再現するには各自で取得してください（手順は [`docs/README.md`](docs/README.md)）。

| 対象 | 理由 |
|---|---|
| **コーパス本文**（`data/splits/corpus_*.tsv`） | JSUT は subset 別 CC-BY-SA、Common Voice は**文自体を CC0 とする明示が確認できなかった**（repo は MPL-2.0、`server/data/ja/` に個別 LICENSE 無しを実測） |
| **教師モデルの重み** | `ayousanz/piper-plus-zero-shot-tsukuyomi`（HF private） |
| **生徒モデルの重み** | 教師コーパスのライセンス継承の議論が未決（下記） |
| **教師ラベルパック**（5.5 GB） | 上記から生成される派生物。サイズも大きい |

⚠️ `reports/*.json` と `data/splits/corpus_stats.json` に含まれていた
コーパス本文は [`scripts/sanitize_reports.py`](scripts/sanitize_reports.py) で
除去済みです（**履歴の全リビジョンからも除去**しました）。
`uid` と統計は残してあるので、コーパスを取得すれば対応が取れます。

⚠️ **ドキュメントには技術的説明のための短い引用が数件残っています。**
例: CER の測り方を説明する箇所で、Whisper が漢字の地名をひらがなで書き起こす
現象を示すために 1 文（6 文字の地名）を引用しています。
**コーパスの再配布ではなく、実測の記録としての引用**です。

## コーパスのライセンス（本プロジェクトが調査した範囲）

| ソース | 行数 | ライセンス | 再配布 |
|---|---:|---|---|
| [ROHAN4600](https://github.com/mmorise/rohan4600) | 4,600 | CC0 / PD | ✅ |
| [ITA コーパス](https://github.com/mmorise/ita-corpus) | 422 | PD | ✅ |
| [JSUT ver1.1](https://sites.google.com/site/shinnosuketakamichi/publication/jsut) | 7,189 | subset 別 CC-BY-SA（`precedent130` は PD） | ⚠️ 継承が要る |
| [Common Voice ja](https://commonvoice.mozilla.org/) | 11,060 | CC0（**要検証**） | ⚠️ 根拠が弱い |
| 自作（疑問 EOS） | 47 | MIT（このリポジトリ） | ✅ |

**⚠️ Common Voice のライセンス根拠が最も弱いのに最大シェアです。**
文そのものを CC0 とする明示を見つけられませんでした（参照 URL 3 本とも 404 を実測）。
配布可能なサブセットだけで再構成する場合は ROHAN + ITA + JSUT `precedent130` + 自作 =
**約 5,200 行**になります（論文の 14,343 行には届きません）。

## 教師モデル

- **piper-plus** ([ayutaz/piper-plus](https://github.com/ayutaz/piper-plus)) — MIT
- 教師 checkpoint は **つくよみちゃんコーパス**（CC-BY-4.0）と
  **MOE-Speech**（CC-BY-SA-4.0）で学習されています

⚠️ **蒸留物へのライセンス継承は未決の論点です。** CC-BY-SA の継承が蒸留モデルに
及ぶかは議論があるため、**本リポジトリでは生徒モデルの重みを配布していません**
（[`docs/decisions.md`](docs/decisions.md) D-006）。

## 主な依存（すべて寛容型ライセンス）

| パッケージ | ライセンス |
|---|---|
| PyTorch | Apache-2.0 ほか |
| NumPy | BSD-3-Clause ほか |
| librosa | ISC |
| soundfile | BSD-3-Clause |
| SCOREQ | MIT |
| faster-whisper | MIT |
| piper-train | MIT |

## 論文

再実装の対象は **arXiv:2608.21378** "sanoTTS: The Smallest Real-Time Neural TTS on a
General-Purpose Microcontroller" です。

## 公式実装との関係

公式実装 [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS) は **GPL-3.0** で公開されています。

**本リポジトリ（MIT）は公式実装のソースコードを一切参照していません。**
論文本文の数値と piper-plus の実装から独立に書いた clean-room 再実装であり、
著者らの実装ではありません。

公式リポジトリの**公開ドキュメント（README / docs）に記載された実測値と
ハイパーパラメータ**は、本リポジトリの外挿値の検証に使っています。
これらは事実であって著作権の対象ではありません。**ソースコードは参照していません。**

公式実装は英語・ネパール語・ヒンディー語・ベトナム語・インドネシア語・中国語に対応しており、
日本語は含まれていません。
