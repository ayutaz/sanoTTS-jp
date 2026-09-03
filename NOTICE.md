# サードパーティとデータの扱い

MIT ライセンス（[`LICENSE`](LICENSE)）が適用されるのは**このリポジトリのコードと
ドキュメント**です。以下はそれぞれの提供元の条件に従います。

> ⚠️ **配布されるモデルの重みは MIT ではありません。**
> 専用の [`LICENSE-MODEL.md`](LICENSE-MODEL.md) が適用されます
> （帰属表示が必須・出力に用途制限・義務が下流に伝播）。
> モデルの内容と既知の制約は [`MODEL_CARD.md`](MODEL_CARD.md)。

## このリポジトリに**含まれていない**もの

再配布の条件が確認できていない、または重いため、**リポジトリには入れていません**。
再現するには各自で取得してください（手順は [`docs/README.md`](docs/README.md)）。

| 対象 | 理由 |
|---|---|
| **コーパス本文**（`data/splits/corpus_*.tsv`） | JSUT は subset 別 CC-BY-SA。原本の再配布は各提供元の条件に従う必要がある |
| **教師モデルの重み** | `ayousanz/piper-plus-zero-shot-tsukuyomi`（HF private） |
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
| [Common Voice ja](https://commonvoice.mozilla.org/) | 11,060 | **CC0-1.0（一次ソースで確認済み）** | ✅ |
| 自作（疑問 EOS） | 47 | MIT（このリポジトリ） | ✅ |

### Common Voice は CC0 で確定（2026-08-28 に再調査）

**以前この欄に「文自体を CC0 とする明示が確認できなかった」と書いていたが誤りだった。**
一次ソースは [common-voice リポジトリの README](https://github.com/common-voice/common-voice):

> The majority of our sentence text in `/server/data` comes directly from user
> submissions ... or they are scraped from Wikipedia ... and are released under a
> **CC0 public domain Creative Commons license**.

README が挙げる唯一の例外は `europarl-VERSION-LANG.txt`（Europarl Corpus 由来）だが、
**`server/data/ja/` に europarl ファイルは存在しない**（実測。ファイルは
`sentence-collector.txt` / `yumie-text-1.txt` / `singleword-benchmark.txt` の 3 つのみ）。

本リポジトリが取り込んだ内訳:

| ファイル | 文数 | 根拠 |
|---|---:|---|
| `sentence-collector.txt` | 8,522 | 投稿者が CC0 提供に同意する Contribution Terms |
| `yumie-text-1.txt` | 1,421 | ⚠️ [PR #3968](https://github.com/common-voice/common-voice/pull/3968) に**個別の CC0 waiver 記載なし**。CV の一括規約でのみ担保 |
| `singleword-benchmark.txt` | 11 | 同上 |

⚠️ `yumie-text-1` の 1,421 行だけは個別の waiver を確認できていない
（探した範囲: PR 本文 / リポジトリ内 `yumie` 全文検索 0 件）。
落としても CC0 分は **13,092 行**残る。

**CC0 のみで再構成した場合**: Common Voice 9,954 + ROHAN 4,140 + ITA 380 + 自作 39
= **14,513 行**（train）で、論文の 14,343 行を上回る。**JSUT を外しても行数は足りる。**
ただし JSUT の `countersuffix26`（助数詞）/ `loanword128`（カタカナ語）/
`onomatopee300`（オノマトペ）は日本語固有の多様性軸なので、**CC0 での補充が要る**。

## 教師モデル

- **piper-plus** ([ayutaz/piper-plus](https://github.com/ayutaz/piper-plus)) — MIT
- 教師 checkpoint は 6 言語 base を **つくよみちゃんコーパス**で
  ファインチューンしたもの。base の日本語は **MOE-Speech**

### つくよみちゃんコーパス — 学習済みモデルの配布は明示的に許可されている

⚠️ **`CC-BY-4.0` と記録していたが誤り**（2026-08-28 に
[一次ソース](https://tyc.rei-yumesaki.net/material/corpus/)で確認）。
著作権法 30 条の 4 に基づく**独自ライセンス**である。要点:

- ✅ **学習済み音声合成モデルをソフトウェアとして公開配布してよい**
- ✅ 商用利用可（有料ソフト・広告つきも明記で可）
- ❌ コーパス単体の再配布は禁止（本リポジトリは行っていない）
- **帰属表示が必須**（下記）。再配布を受けた側にも義務が伝播する
- 出力音声に禁止用途あり（個人攻撃 / 政治・宗教の主張 / アダルト /
  素材としての再配布）

**必須の帰属表示** — 生徒モデルを配布する成果物には次を含めること:

> 本ソフトウェアの音声合成には、フリー素材キャラクター「つくよみちゃん」
> （© 夢前黎）が無料公開している音声データを使用しています。
> https://tyc.rei-yumesaki.net/material/corpus/

### MOE-Speech

⚠️ **`CC-BY-SA-4.0` と記録していたが誤り。** 実際は
[litagin/moe-speech](https://huggingface.co/spaces/litagin/moe-speech-license) 由来で、
HF 上の表示は `license: other`、**著作権法 30 条の 4（情報解析のための利用）に基づく**。
本プロジェクトはこの条件を受け入れて学習に使用している（2026-08-28 ユーザー判断）。

⚠️ piper-plus 側の `data-sources.yml` は現在も `CC-BY-SA-4.0 / verified: false`
と記載しており、**実態とずれている**（piper-plus 側の課題。本リポジトリからは変更しない）。

### 生徒モデルの重み

**初期リリースでは つくよみちゃん由来のまま配布する**（下記の帰属表示を付す）。
将来 MOE-Speech ベースなど別の教師に差し替える方針
（候補は [`docs/decisions.md`](docs/decisions.md) D-035 参照）。

**配布時に適用されるライセンスは [`LICENSE-MODEL.md`](LICENSE-MODEL.md)**（D-039）。
MIT を名乗ると、下記の伝播する義務を外して配れることになってしまうため。

⚠️ **v0.1.1 から配布している ESP32-S3 の flash イメージ（`esp32s3-firmware-*.bin`）にも
重みが入っている。** 再配布するなら下記の帰属表示を同梱し、出力の用途制限も伝播する。

---

## ⚠️ 生徒モデルを配布するときに必ず同梱する帰属表示

**この節をそのまま配布物の `NOTICE` / README にコピーすること。**
1 つでも欠けると、対応する素材の条件に違反する。

```
This model was distilled from a piper-plus teacher model.

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
  - JSUT ver1.1 (高道慎之介) — CC-BY-SA-4.0 etc.
      https://sites.google.com/site/shinnosuketakamichi/publication/jsut
```

**出力音声に付く制限**（つくよみちゃんコーパスの条件。配布を受けた側にも伝播する）:
個人攻撃・批判 / 政治・宗教の主張 / アダルト / 素材としての再配布 には使えない。

⚠️ **JSUT だけが継承（copyleft）付き**である。「モデルは学習テキストの二次的著作物」
という立場を取られた場合、モデルも CC-BY-SA になり **MIT 配布と衝突する**。
日本では著作権法 30 条の 4 により学習自体が許され、本文も再配布していないため
実務上この立場が通る可能性は低いと判断したが、**リスクはゼロではない**
（2026-08-28 ユーザー判断。D-035）。
第三者素材の義務を完全にゼロにしたい場合は、JSUT だけでなく
**つくよみちゃん（＝教師）も外す必要がある**。

## ⚠️ リポジトリに**含まれている**第三者コード

| 対象 | ライセンス | 場所 |
|---|---|---|
| **Open JTalk**（NJD / JPCommon 系 34 ファイル） | **修正 BSD**（Copyright (c) 2008-2016 Nagoya Institute of Technology / HTS Working Group） | [`csrc/openjtalk/`](csrc/openjtalk/) |
| **nnn112358 の M5Stack 移植から取り込んだ 3 ファイル** | **MIT**（Copyright (c) 2026 nnn112358） | [`esp32/boards/m5unified/main/saan_audio_m5.cpp`](esp32/boards/m5unified/main/saan_audio_m5.cpp) / [`saan_ui_m5.cpp`](esp32/boards/m5unified/main/saan_ui_m5.cpp) / [`scripts/blob_to_header.py`](scripts/blob_to_header.py) |

**Open JTalk** は端末で漢字を扱う経路（K-7）で使う。かな入力だけの既定ビルドには入らない
（`idf.py -DSAAN_KANJI=1` を付けたときだけコンパイル対象になる）。
**nnn112358 の 3 ファイル**は M5Stack 向けビルド（`esp32/boards/m5unified/`）と
重みを `.rodata` に埋めるビルドでだけ使う。

- ライセンス全文は [`csrc/openjtalk/COPYING`](csrc/openjtalk/COPYING)
- 出所・バージョン・改変の一覧は [`csrc/openjtalk/PROVENANCE.md`](csrc/openjtalk/PROVENANCE.md)
- 取得元は **pyopenjtalk-plus** の sdist 同梱（`lib/open_jtalk/src`）
- 全ファイル連結の SHA-256: `572fc2b7341530ff56d9c415fdb7df41886ad9ed57e6975579cb3a4b644a5f43`
- **改変は 1 件だけ**（`jpcommon_label.c` の `MAXBUFLEN` 1024 → 256。K-5）。
  `scripts/k1/k4b_vendor.py --check` が「上流 + 改変表」と突き合わせるので、
  **表に無い改変は落ちる**
- ⚠️ **ESP32 ビルドは一時ヒープを PSRAM に向けるが、取り込んだ C は 1 バイトも変えていない。**
  `cc -include csrc/oj_heap_psram.h` で `calloc` / `strdup` / `free` を
  コンパイル時に差し替えるだけで、実装（`esp32/components/saanotts_core/oj_heap_psram.c`）は
  **本プロジェクトが書いたもの（MIT）**。上流のファイル一覧・SHA-256 は上のまま変わらない

⚠️ **修正 BSD は MIT と同居できる**（コピーレフトではない）。
⚠️ **GPL-3.0 の `Ampixa/sanoTTS` とは別物**（D-032 の凍結対象ではない）。

nnn112358 の 3 ファイルについて:

- 取り込み元は [nnn112358/SanoTTS-jp-M5StackCoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)
  （第三者による本リポジトリの M5Stack 移植）。**各ファイルの冒頭に出所と MIT を書いてある。**
- 本リポジトリの API（`saan_audio.h` / `saan_ui.h` / `saan_pcm.h`）に合わせて書き換えており、
  逐語コピーではない（float → int16 と checksum は `saan_pcm.c` を呼ぶようにした）
- **MIT 同士なので、このリポジトリの `LICENSE` と衝突しない。** 著作権表示は残すこと

## ⚠️ ビルド時に取得する第三者コンポーネント（リポジトリには入っていない）

`esp32/boards/m5unified/` を **M5Stack 実機向けにビルドするときだけ**、
ESP-IDF Component Registry から自動で取得される（`main/idf_component.yml`）。
**リポジトリには含まれない**（`.gitignore` が `managed_components/` を除外している）。

| 対象 | バージョン | ライセンス |
|---|---|---|
| **M5Unified** | 0.2.21 | **MIT**（Copyright (c) 2021 M5Stack） |
| **M5GFX** | 0.2.28 | **MIT**（同上）。⚠️ `src/lgfx/` は **LovyanGFX 由来で FreeBSD ライセンス**、同梱の日本語フォントは **IPA フォントライセンス v1.0**（`src/lgfx/Fonts/IPA/`） |

⚠️ **これらを含む firmware イメージを配布する場合は、上の表示も同梱すること。**
画面に文字を出すビルドは **IPA 由来のフォントデータをバイナリに含む**。
⚠️ 本リポジトリが **v0.1.1 / v0.2.0 で配布した `esp32s3-firmware-*.bin` には
M5Unified / M5GFX は入っていない**（DevKit 構成でビルドしたもの）。
⚠️ 確認した組み合わせは `dependencies.lock`（このファイルもリポジトリには入れていない）。

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
