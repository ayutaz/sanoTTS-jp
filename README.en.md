# sanoTTS-jp

*[日本語](README.md) · **English***

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-try%20in%20browser-brightgreen.svg)](https://ayutaz.github.io/sanoTTS-jp/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Model license](https://img.shields.io/badge/model-not%20MIT-orange.svg)](LICENSE-MODEL.md)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/ayutaz?label=sponsor&logo=github)](https://github.com/sponsors/ayutaz)

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

### 🔊 Try it in a browser → **<https://ayutaz.github.io/sanoTTS-jp/>**

Nothing to install. **Type Japanese text with kanji and it speaks.**
⚠️ It is the **same C99 code that runs on the microcontroller**, compiled to WebAssembly
(the arena is the same 180,224 B). It is not a replacement for piper-plus's WASM build —
it is a way to touch the code that runs on hardware ([D-050](docs/decisions.md#d-050)).
Measured in Chrome at **0.008–0.019 ×RT** ([M-95](docs/measurements.md#m-95)).
It has been listened to on **both lanes** — reported fine, no dropouts
([M-96](docs/measurements.md#m-96)). ⚠️ **One listener, no control, not blind.**
⚠️ **Neither mobile nor Safari has been measured.**

This applies the distillation recipe from [arXiv:2608.21378](https://arxiv.org/abs/2608.21378)
("sanoTTS") to Japanese, distilling [piper-plus](https://github.com/ayutaz/piper-plus)
(MB-iSTFT-VITS2) into three small students: Duration, Acoustic, and an iSTFT Decoder.
**Inference is dependency-free C99** — libm only, and it never calls `malloc`.

Flash one firmware onto an M5Stack CoreS3 (a Stack-chan) and it speaks when you type
**`今日は良い天気ですね。`** over serial. Morphological analysis and accent estimation both
run on the device.

```
かな> 今日は良い天気ですね。
saanotts: 経路: 辞書
saanotts: 漢字 G2P: 33 B -> 形態素 7 個 / ids 53 個 / 25.77 ms
saanotts: init 21.60 ms / 53 ids / 106 frames / 27136 sample / 音声 1.231 s
saanotts: プリロール 4 チャンク完了（初回 pull 245.76 ms / 鳴らし始めまで 385 ms）
  ...
saanotts: 定常 xRT = 0.448（満チャンク pull の中央値 / 92.88 ms）
saanotts: アンダーラン 0 / 14 チャンク
saanotts: 出力 PCM: 27136 sample / FNV-1a 0x390bf4b2aef8f2ec
```

*Excerpted from the raw device log
[`reports/m130_cores3/device_v4_kanji.log`](reports/m130_cores3/device_v4_kanji.log), with timestamps
and caveat lines removed; `...` stands for 14 pulls.
⚠️ The **v3** log is [`reports/m90_cores3/device_m5_kanji.log`](reports/m90_cores3/device_m5_kanji.log)
— the checksum changes with the weights, so the two logs never agree.*

| | |
|---|---:|
| Model | **559 K params**, **654,032 B** as int8 (flash) |
| Runtime RAM | **157 KB** — 34% of the ESP32-S3's 512 KB SRAM |
| Speed | **xRT 0.448** steady-state, one full-chunk pull (⚠️ **0.618–0.793** over a whole utterance). ⚠️ The requirement is the **steady-state** denominator ([D-049](docs/decisions.md#d-049)) |
| Quality | **64%** of the teacher (SCOREQ ratio **0.636** for v4; v3 scored 0.644 and **the difference is not detectable**). ⚠️ **A predictor's score, not a human ear** |
| On-device G2P | **13.7 MB dictionary** with kanji, or an **877 B table** for kana only |

⚠️ **This is a proof of concept, not a product.**

## Using it

| | Where to look |
|---|---|
| **Just try it** | the [browser build](https://ayutaz.github.io/sanoTTS-jp/) above (nothing to install) |
| **Synthesize your own text / run it on hardware / run the gates** | **[Getting started](docs/getting-started.en.md)** (five ways in) |
| **Which boards work; dictionary size vs. accuracy** | **[Status](docs/support-matrix.en.md)** |
| **Weights, firmware, dictionaries** | **[Downloads](docs/downloads.en.md)** |

**The shortest path** (if you have a board):

```bash
# Flash a 16 MB ESP32-S3, dictionary included. No ESP-IDF needed.
esptool.py --chip esp32s3 -p <PORT> write_flash 0x0 esp32s3-firmware-kanji-16mb.bin
```

⚠️ **8 MB and 4 MB boards work too** (with a smaller dictionary; readings get worse).
→ [Status](docs/support-matrix.en.md)

## Why Japanese needs its own port

**Port the English recipe verbatim and it stalls at G2P.** Reading kanji needs a dictionary,
and NAIST-JDIC measures **102 MB** — far too much for the chip. The answer was not to shrink
the dictionary but to **cut the problem somewhere else**: the device accepts only
"hiragana + accent marks" and converts that with an **877 B table**. **Neither the paper nor
the official implementation has a counterpart**; this is the central design decision here.

⚠️ **That premise later collapsed.** A TTS-only dictionary format takes an entry from
130 B to **28 B**, so **438,750 entries** fit a 16 MB board. **The device now reads kanji on
its own** (the kana intermediate form remains the shared form of both routes — either way
yields bit-identical PCM).

Pitch accent (箸/橋/端) and devoiced vowels are also absent from the English version, and
neither shows up in an aggregate score, so each has a dedicated evaluation
(see [`MODEL_CARD.md`](MODEL_CARD.md)).

## How it works

```
Kanji text
   │  host side, offline (OpenJTalk)    ┊  device side, 13.7 MB dict (-DSAAN_KANJI=1)
   ▼                                     ┊  438,750 entries mmapped from flash
kana intermediate  きょ][おわよ][いて][んきです°ね   [ rise / ] nucleus / # boundary / ° devoicing
   │  device side, an 877 B table only   ┊  saan_g2p_classify() picks the route
   ▼                                     ┊  (kana / dictionary / refuse — both reach the same IDs)
phoneme IDs ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
                 33 K params      195 K            331 K
                        └─ joined by a 40-dim latent interface (the c-line)
```

**Joining the three students through an explicit latent interface is the point.** Drop it
and learn text→waveform in one net, and the paper's ablation shows the model memorizes the
training sentences and cannot read new ones.

- **The C99 inference core** (`csrc/`) — libm is the only dependency; it uses an arena
  instead of `malloc`. The streaming version is **bit-identical** to the batch one (27,136 samples)
- **On-device G2P** (`csrc/g2p.c`) — an **877 B** table, 1,549 B of code, **zero working memory**
- **Free-form input on the device** (`csrc/line.c`, 369 B) — kana or kanji text; a build
  without the dictionary **refuses kanji out loud instead of guessing**
- **The whole distillation path** — teacher label generation → four training stages →
  evaluation (SCOREQ / UTMOS / DNSMOS / kana CER / per-phoneme-class spectral flatness)

## What has been measured

All of it on one M5Stack CoreS3 (W8A8 + PIE, the default on ESP32-S3).

(Only what the table at the top does not already carry.)

| Axis | Value |
|---|---|
| **Accent** | **31/37** sign agreement with the teacher across 37 minimal pairs (⚠️ **v3 scored 37/37** — this regressed in v4. [D-057](docs/decisions.md#d-057)) |
| **Kanji G2P** | 5.51–66.30 ms for 15–84 B of input; **1,977/1,977** sentences agree with MeCab |
| **The two routes** | The same sentence written either way produces **bit-identical PCM**, on the device and on the host alike |
| **The kanji path** | QEMU synthesizes from kanji end to end (M-76); a CoreS3 reproduces the same checksum |
| **Underruns** | **Zero** across every sentence; **384 ms** to first sound |

**Speed was rebuilt until it met the RTF ≤ 0.5 requirement.** The first measurement was
0.926, and a per-step breakdown showed **MACs were only about a third** of it — activation
quantization, GELU, 102 tensor lookups per step and 489 KB/step of weight copying were the
rest. Removing those took one step from 18.38 M to **11.66 M cycles**, and **the waveform
never changed by a single bit** (identical checksums). The trail is in the timeline in
[`docs/README.md`](docs/README.md).

## Relationship to the official implementation

The official implementation, [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS), exists and
is **GPL-3.0**. This repository is MIT and was written **without reading that source**, working
from the numbers in the paper and from the piper-plus implementation. The official one covers
English, Nepali, Hindi, Vietnamese, Indonesian and Chinese — **not Japanese**.

Measurements **published in the official repository's documentation** (0.22× real time on an
ESP32-S3, among others) are used to cross-check the numbers here. **The code is not consulted**;
that boundary is enforced mechanically (rule 4 in [`CONTRIBUTING.md`](CONTRIBUTING.md)).

## Documentation

The index is [`docs/README.md`](docs/README.md). **When numbers disagree,
[`docs/measurements.md`](docs/measurements.md) wins** — every entry carries a command to
reproduce it. Decisions and corrections live in [`docs/decisions.md`](docs/decisions.md),
the model's contents and known limits in [`MODEL_CARD.md`](MODEL_CARD.md), and the
hardware procedure in [`esp32/TESTING.md`](esp32/TESTING.md).

## Contributing

Please read [`CONTRIBUTING.md`](CONTRIBUTING.md). **What helps most is telling us how it
sounds**, followed by speed measurements on a different ESP32-S3.

> 🙏 **Telling us how it sounds needs no board** — just play
> [`saanotts-jp-v4-samples.zip`](https://github.com/ayutaz/sanoTTS-jp/releases/latest).
> **"It sounds off" can carry more information than a table of n=24 numbers.**

**The known limitations, and how each was measured, are in
[`MODEL_CARD.md`](MODEL_CARD.md) §4.**

## Sponsoring

**You can support this work through [GitHub Sponsors](https://github.com/sponsors/ayutaz).**

## License

⚠️ **The code and the model are under different licenses.**

| | License |
|---|---|
| The **code and documentation** in this repository | [MIT](LICENSE) |
| The **distributed model weights** ([Releases](https://github.com/ayutaz/sanoTTS-jp/releases)) | **[`LICENSE-MODEL.md`](LICENSE-MODEL.md)** — **not** MIT |

The weights are distilled from a teacher whose material includes the Tsukuyomi-chan corpus,
whose terms

- **require attribution**, **restrict what the output may be used for**, and **propagate downstream**

so they cannot be called MIT. Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) and
[`MODEL_CARD.md`](MODEL_CARD.md) before using them.

⚠️ **The mandatory attribution was corrected on 2026-09-09** ([`docs/decisions.md`](docs/decisions.md) C-073).
**LibriTTS-R and CML-TTS (CC BY 4.0) and AISHELL-3 (Apache-2.0)** — all in the teacher's
multilingual base — **were missing** and have been added; materials that require no
attribution (MOE-Speech, CC0, public domain) moved to the **optional block (B)**.
**If you redistribute, copy block (A) in full.**
⚠️ **"No adult use" was also wrong** (C-072). What is prohibited is
**publishing intense content without zoning** — the corpus provider places no limit on
adult or violent expression when appropriate zoning is in place.

**The corpus text itself is not distributed.** Per-source provenance is in [`NOTICE.md`](NOTICE.md).
