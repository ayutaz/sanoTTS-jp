# はじめかた

*[← README](../README.md)*

**入口は 5 つ。A / B / D / E は piper-plus も教師モデルも要らない**（新規 clone で実測）。

| | やりたいこと | 要るもの | 所要 |
|---|---|---|---|
| **A** | **音を聴く** | [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) の `saanotts-jp-v3-samples.zip` だけ | 1 分 |
| **B** | **好きな文を合成する** | + 最小セットアップ + `saanotts-jp-v3-stage4.pt` | 10 分 |
| **C** | **ESP32-S3 で喋らせる** | ボード（DAC は任意）。**焼くだけなら ESP-IDF は不要** | 15〜30 分 |
| **D** | **コードのゲートを回す** | 最小セットアップだけ | 5 分 |
| **E** | **ブラウザで試す** | ブラウザだけ。**インストール不要** | 1 分 |

## 最小セットアップ（B / D）

⚠️ **`uv sync` は使わない。** `pyproject.toml` の `[tool.uv.sources]` が
piper-plus への**絶対パス**を指しているので、持っていない人は
`error: Distribution not found at: file://...` で止まる（実測）。
生徒の推論に要るのは **torch / numpy / soundfile の 3 つだけ**なので、
プロジェクトを経由しない venv を作る:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

## B. 好きな文を合成する

[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) から
`saanotts-jp-v3-stage4.pt`（2.7 MB）を落として、**かな中間表現**を渡す。

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v3-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav（22.05 kHz / 1.2 秒）「今日は良い天気ですね。」
```

```
[ アクセント上昇 / ] 下降核 / # 句境界 / ° 無声化
```

**漢字文をそのまま渡すこともできる**（下のフルセットアップが要る。OpenJTalk で
漢字→かなを行うため）:

```bash
uv run python scripts/synthesize_student.py --ckpt saanotts-jp-v3-stage4.pt \
    --text "今日は良い天気ですね。" --out out/
```

⚠️ **どちらの経路でも WAV はバイト単位で一致する**（M-92 で実測。held-out 300 文で
生徒インデックスも 300/300 一致）。違いは**漢字→かなに OpenJTalk が要るかどうかだけ**。

| 書きかた | 要るもの |
|---|---|
| `--intermediate "きょ][おわよ…"` | **最小セットアップだけ**（torch / numpy / soundfile） |
| `--text "今日は良い天気ですね。"` | + **フルセットアップ**（piper-plus = OpenJTalk） |

⚠️ **端末（`-DSAAN_KANJI=1`）はこの制約を受けない。** 辞書を載せた板は漢字文を
そのまま受ける。ホストで OpenJTalk が要るのは、**端末より広いフル辞書**を使うため
（端末の枝刈り辞書とは**音素の 0.63% が違う**。n=1,495。M-99 §4）。
⚠️ **n を必ず添えること。** 同じ量が n=298 では 0.32% に見える（C-059）。

⚠️ **モデルの重みは MIT ではない。** 使う前に [`LICENSE-MODEL.md`](../LICENSE-MODEL.md) を読むこと。

## C. ESP32-S3 で喋らせる

手順は [`esp32/TESTING.md`](../esp32/TESTING.md)。焼いたあとシリアルで:

```
かな> きょ][おわよ][いて][んきです°ね        ← かな中間表現
かな> 今日は良い天気ですね。                  ← 漢字版のビルドなら、そのまま打つ
```

**入力に印を付ける必要はない。** 端末が行を見て**かな / 辞書 / 拒否の 3 値**を決める
（`saan_g2p_classify()`）: 凍結テーブルのトークナイザが行末まで通れば**かな経路**、
通らず中間表現の記号（`[ ] # ° _ ^ $`）が無ければ**辞書経路**、通らないのに記号が
混じっていれば**拒否して喋らない**（「中間表現 + `。`」がそれらしい音で通るのを防ぐため）。
判定はホスト側 `scripts/kana_g2p.py` と同じ規則で、一致は `make -C csrc kb-parity` が
**596/596** で検査する。

**ファームは 3 通り。** コンソールが **UART0 の版と USB Serial/JTAG の版**があり、
CoreS3 / AtomS3 のような **native USB だけの板は `-usbjtag` の方**を焼く。

| | 焼くもの | 受け付ける入力 | flash |
|---|---|---|---|
| かな | `esp32s3-firmware-w8a8-pie.bin` / `…-usbjtag.bin` | かな中間表現のみ | 8 MB 以上 |
| **漢字** | `esp32s3-firmware-kanji-16mb.bin` / `…-usbjtag.bin` | **漢字かな交じり文**も | **16 MB 必須** |
| **M5 CoreS3** | `m5-cores3-firmware-kanji-16mb.bin` | 同上。**内蔵スピーカーで鳴る** | **16 MB 必須** |

⚠️ **v0.2.0 以前のイメージは `!` の前置が要り、入力が UART0**。必ず入れ替えること。

⚠️ **「16 MB 必須」は配布イメージの話。** **8 MB / 4 MB の板でもソースからなら漢字が動く**
（8 MB は 2026-09-05 に実機で確認 = [M-105](measurements.md#m-105) /
4 MB は QEMU まで = [M-106](measurements.md#m-106)）。
**配布はしていない**ので、自分でビルドすることになる:

（精度と確認状況は冒頭の「[対応状況](#対応状況)」と同じ。ここは**どのパーティション表を使うか**）

| | 表 | entries | **音素の誤り**（n=1,495） | 確認 |
|---|---|---:|---:|---|
| **16 MB**（配布イメージ） | `partitions_16mb.csv` | 438,750 | **0.63%** | ✅ 実機 |
| 8 MB / DevKit | `partitions_8mb_kanji.csv` | 228,000 | 1.01% | ✅ 実機 |
| 8 MB / **M5Stack 系** | `boards/m5unified/partitions_8mb.csv` | 213,000 | **1.09%** | ✅ 実機 |
| **4 MB** | `partitions_4mb_kanji.csv` | 135,000 | **1.94%** | ⚠️ **第三者の実機**（未再現） |
| **2 MB の枠** | `partitions_2mb_kanji.csv` | 44,000 | **3.86%** | ⚠️ **第三者の実機**（未再現） |

手順は [`esp32/README.md`](../esp32/README.md) の「8 MB flash の板」「4 MB / 2 MB 枠」。
⚠️ **読みが落ちる**（枝刈りを深くするため）。⚠️ **対照つきでは聴かれていない**（M-91 / M-93 / M-96 / M-109 はどれも**1 名・対照なし・盲検なし**）。
⚠️ **ESP32-S3 に 2 MB flash の品番は無い**（WROOM-1 は N4 / N8 / N16）。**4 MB が下限**で、
2 MB の行は「**枠に収まる**」ことを大きい板の上で確かめただけ。
⚠️ **4 MB / 2 MB は第三者の実機で鳴ったが、私は未再現**（M-109）。**checksum も xRT もアンダーランも報告に無い。**

**音の出口は 2 通り。**

| 板 | どう焼くか | 音の出口 |
|---|---|---|
| ESP32-S3 DevKit / AtomS3 + I2S DAC | 上のイメージを焼く | 外付け DAC（配線が要る。⚠️ `saan_i2s` は**実機未検証**） |
| **M5Stack CoreS3 / Core2 / Basic**（スタックチャンの中身） | 配布イメージ、または[ソースから](../esp32/boards/m5unified/README.md) | 内蔵スピーカー。画面に文が出て、タッチで再生 |

## D. コードのゲートを回す

```bash
make -C csrc line                                       # 端末の行編集（陽性対照つき）
make -C csrc fft                                        # 逆 FFT（naive DFT の 1,435 倍）
make -C csrc g2p PYTHON="uv run --no-project python"    # オンデバイス G2P（2,819 ベクタ）
make -C csrc erf                                        # GELU の erf 近似 vs libm（陽性対照つき）
make -C csrc range                                      # 出力範囲つきカーネルが全域版と bit 一致
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata（fp32 拒否の陽性対照）
uv run --no-project python scripts/test_corpus_license.py   # 蒸留テキストのライセンス判定（陽性対照つき）
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **`PYTHON=...` を省くと `uv run python` になり、piper-plus を要求する。**
⚠️ **`make -C csrc all-test` は通らない** — golden との突き合わせに `csrc/*.bin`
（重みの書き出し）が要る。落とした `.pt` から `scripts/export_c_weights.py` で書き出せば通る。

## E. ブラウザで試す

**<https://ayutaz.github.io/sanoTTS-jp/>** — この C99 コアをそのまま WebAssembly にしたデモ。
配っているのは [`pages.yml`](../.github/workflows/pages.yml)（`main` への push で走り、
重みと辞書は**リリース v0.3.0 からタグ固定で落として SHA-256 を照合**している）。

**インストールも設定も要らず**、入力欄に
`今日は良い天気ですね。` と打つだけで鳴る。**漢字・カタカナ・ひらがな**をそのまま受ける
（`!` のような印は要らない。経路は C 側の `saan_g2p_classify()` が決める）。

- **ESP32 と同じコードが動く。** `csrc/` の C99 と `esp32/main/saan_kanji.c` を書き換えずに
  wasm にしただけで、**arena も実機と同じ 180,224 B**（→ [D-050](decisions.md#d-050)）
- 初回は**辞書 13,702,320 B（gzip -9 で 5,476,122 B）**を落とす。⚠️ 回線が細いと待たされる
- ⚠️ **ブラウザでは 1 種類も速度を測っていない**（測ったのは node だけ。[M-94](measurements.md#m-94)）
- 音は **W8A32 / W8A8 の両方を聴いてもらい「問題なかった」/ 途切れ無し**（[M-96](measurements.md#m-96)）。⚠️ **1 名・対照なし・盲検なし**。
  ⚠️ かつてここに書いていた「`AudioContext` のリサンプルが挟まる」は、**要求どおり 22,050 Hz が返る**ことが分かった（M-95 §3）ので前提が変わった。ただし
  **鳴っている音は checksum と一致しない**
- ⚠️ **成果物は今も ESP32。** Web は入口であって、このプロジェクトの目的ではない（[D-007](decisions.md#d-007)）

手元で動かすなら（emcc が要る）。⚠️ **`index.html` と同じ階層に全部を平らに並べる**
（`.github/workflows/pages.yml` が CI でやっているのと同じ形）:

```bash
bash web/build.sh                                   # → web/dist/*.wasm と *.mjs
mkdir -p /tmp/saan-site
cp web/index.html web/main.js web/dist/*.mjs web/dist/*.wasm /tmp/saan-site/
cp csrc/student_i8.bin /tmp/saan-site/              # = リリースの saanotts-jp-v3-int8.bin
gzip -9 -c csrc/k1_dict.bin > /tmp/saan-site/k1_dict.bin.gz   # = k1-dict-438750.bin

# ⚠️ **ここまでで止めると footer の 4 本が全部 404 になる**（実測。音は鳴るので、
#    リンクを踏むまで気づかない）: NOTICE.txt / NOTICE-openjtalk.txt /
#    NOTICE-dictionary.txt / LICENSE-MODEL.md
cp LICENSE-MODEL.md /tmp/saan-site/                 # リポジトリのものでよい
#   （リリース資産 LICENSE-MODEL.md と SHA-256 が一致する。実測で確認済み）
# ⚠️ NOTICE*.txt 3 本は**リポジトリにその名前では無い**ので、リリースから落とす。
#    ⚠️ **ネットワークが要る**（CI の pages.yml も同じ 3 本を落としている）
gh release download v0.3.0 -R ayutaz/sanoTTS-jp -D /tmp/saan-site --clobber \
    -p 'NOTICE.txt' -p 'NOTICE-openjtalk.txt' -p 'NOTICE-dictionary.txt'

uv run --no-project python -m http.server -d /tmp/saan-site 8000
#   ⚠️ `python3 -m http.server` は hook が止める（D-012）
```

## フルセットアップ（漢字→かな変換 / 学習 / ラベル生成）

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ uv sync の前
uv sync
```

⚠️ **教師 checkpoint（private）はこれでも入らない。** 要るのは
**ラベル生成と学習をやり直すときだけ**で、漢字→かな変換は piper-plus のソースだけで動く。

