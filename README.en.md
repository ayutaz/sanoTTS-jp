# sanoTTS-jp

*[日本語](README.md) · **English***

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-try%20in%20browser-brightgreen.svg)](https://ayutaz.github.io/sanoTTS-jp/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Model license](https://img.shields.io/badge/model-not%20MIT-orange.svg)](LICENSE-MODEL.md)

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

### 🔊 Try it in a browser → **<https://ayutaz.github.io/sanoTTS-jp/>**

Nothing to install. **Type Japanese text with kanji and it speaks.**
⚠️ It is the **same C99 code that runs on the microcontroller**, compiled to WebAssembly
(the arena is the same 180,224 B). It is not a replacement for piper-plus's WASM build —
it is a way to touch the code that runs on hardware ([D-050](docs/decisions.md#d-050)).
⚠️ **The first load pulls a 5.5 MB dictionary** (gzip).
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
saanotts: 漢字 G2P: 33 B -> 形態素 7 個 / ids 53 個 / 25.69 ms
saanotts: init 21.56 ms / 53 ids / 106 frames / 27136 sample / 音声 1.231 s
saanotts: プリロール 4 チャンク完了（初回 pull 244.66 ms / 鳴らし始めまで 384 ms）
  ...
saanotts: 定常 xRT = 0.446（満チャンク pull の中央値 / 92.88 ms）
saanotts: アンダーラン 0 / 14 チャンク
saanotts: 出力 PCM: 27136 sample / FNV-1a 0xa69a7ebbb5ccb05f
```

*Excerpted from the raw device log
[`reports/m90_cores3/device_m5_kanji.log`](reports/m90_cores3/device_m5_kanji.log), with timestamps
and caveat lines removed; `...` stands for 14 pulls.*

| | |
|---|---:|
| Model | **559 K params**, **654,032 B** as int8 (flash) |
| Runtime RAM | **157 KB** — 34% of the ESP32-S3's 512 KB SRAM |
| Speed | **xRT 0.446** for a full-chunk pull (⚠️ 0.54–0.71 over a whole utterance) |
| Quality | **64%** of the teacher (SCOREQ ratio 0.644). ⚠️ **A predictor's score, not a human ear** |
| On-device G2P | **13.7 MB dictionary** with kanji, or an **877 B table** for kana only |

⚠️ **This is a proof of concept, not a product.** What it most lacks is human listening:
so far two sessions, each one listener, no control, not blind.

## Why Japanese needs its own port

**Port the English recipe verbatim and it stalls at G2P.** Reading kanji needs a dictionary,
and NAIST-JDIC measures **102 MB** — far too much for the chip. The answer was not to shrink
the dictionary but to **cut the problem somewhere else**: the device accepts only
"hiragana + accent marks" and converts that with an **877 B table**. **Neither the paper nor
the official implementation has a counterpart**; this is the central design decision here.

⚠️ **That premise later collapsed when it was re-measured.** A TTS-only dictionary format
takes an entry from 130 B to **28 B**, so **438,750 entries** fit a 16 MB board. The device
now reads kanji on its own, but the kana intermediate form remains **the shared intermediate
of both routes** — the same sentence written either way yields bit-identical PCM.

Pitch accent (箸/橋/端) and devoiced vowels (the `i`/`u` in です/した) are also absent from
the English version, and neither is visible in an aggregate score, so each got a dedicated
evaluation (see [`MODEL_CARD.md`](MODEL_CARD.md)).

## Getting started

**Five entry points. A / B / D / E need neither piper-plus nor the teacher model**
(measured from a fresh clone).

| | What you want | What you need | Time |
|---|---|---|---|
| **A** | **Hear it** | Just `saanotts-jp-v3-samples.zip` from [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | 1 min |
| **B** | **Synthesize your own text** | + minimal setup + `saanotts-jp-v3-stage4.pt` | 10 min |
| **C** | **Make an ESP32-S3 speak** | A board (DAC optional). **Flashing alone needs no ESP-IDF** | 15–30 min |
| **D** | **Run the code gates** | Minimal setup only | 5 min |
| **E** | **Try it in a browser** | A browser. **Nothing to install** | 1 min |

### Minimal setup (B / D)

⚠️ **Do not run `uv sync`.** `[tool.uv.sources]` in `pyproject.toml` points at an
**absolute path** to piper-plus, so without it you stop at
`error: Distribution not found at: file://...` (measured). Student inference needs only
**torch, numpy and soundfile**, so build a venv that bypasses the project:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

### B. Synthesize your own text

Download `saanotts-jp-v3-stage4.pt` (2.7 MB) from
[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) and pass the
**kana intermediate form**.

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v3-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav (22.05 kHz, 1.2 s) "今日は良い天気ですね。"
```

```
[ accent rise / ] accent nucleus / # phrase boundary / ° devoicing
```

**You can also pass kanji directly** — this needs the full setup below, because
kanji→kana goes through OpenJTalk:

```bash
uv run python scripts/synthesize_student.py --ckpt saanotts-jp-v3-stage4.pt \
    --text "今日は良い天気ですね。" --out out/
```

⚠️ **Both routes produce a byte-identical WAV** (measured in M-92; student indices also
agree 300/300 across 300 held-out sentences). The only difference is whether OpenJTalk is
needed for kanji→kana.

| How you write it | What you need |
|---|---|
| `--intermediate "きょ][おわよ…"` | **The minimal setup only** (torch / numpy / soundfile) |
| `--text "今日は良い天気ですね。"` | + the **full setup** (piper-plus, i.e. OpenJTalk) |

⚠️ **The device (`-DSAAN_KANJI=1`) has no such constraint** — a board with the dictionary
takes kanji as it comes. OpenJTalk is needed on the host because the host uses the **full**
dictionary, which differs from the device's pruned one by **0.63% of phonemes**
(n=1,495; M-96 §4). ⚠️ **Always state n** — the same quantity reads as 0.32% at n=298 (C-057).

⚠️ **The model weights are not MIT.** Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) first.

### C. Make an ESP32-S3 speak

The procedure is in [`esp32/TESTING.md`](esp32/TESTING.md). After flashing, over serial:

```
かな> きょ][おわよ][いて][んきです°ね        ← the kana intermediate form
かな> 今日は良い天気ですね。                  ← on a kanji build, just type it
```

**No prefix is needed.** The device reads the line and picks one of **three routes**
(`saan_g2p_classify()`): if the frozen tokenizer consumes the whole line it is the **kana
route**; if it does not and no intermediate-form mark (`[ ] # ° _ ^ $`) is present, the
**dictionary route**; if it does not and a mark *is* present, it **refuses and stays silent**
(so that "intermediate form plus `。`" cannot slip through sounding plausible). The rule
matches `scripts/kana_g2p.py` on the host, and `make -C csrc kb-parity` checks that at **596/596**.

**Three firmware images.** Each comes in a **UART0** and a **USB Serial/JTAG** variant; a
native-USB-only board such as a CoreS3 or AtomS3 needs the `-usbjtag` one.

| | Flash this | Accepted input | Flash size |
|---|---|---|---|
| Kana | `esp32s3-firmware-w8a8-pie.bin` / `…-usbjtag.bin` | The kana intermediate form only | 8 MB+ |
| **Kanji** | `esp32s3-firmware-kanji-16mb.bin` / `…-usbjtag.bin` | Kanji text too | **16 MB required** |
| **M5 CoreS3** | `m5-cores3-firmware-kanji-16mb.bin` | Same, through the **built-in speaker** | **16 MB required** |

⚠️ **Images before v0.3.0 need the `!` prefix and read UART0.** Replace them.

**Two ways to get sound out.**

| Board | How to flash | Audio out |
|---|---|---|
| ESP32-S3 DevKit / AtomS3 + I2S DAC | Flash the images above | External DAC, needs wiring (⚠️ `saan_i2s` is **untested on hardware**) |
| **M5Stack CoreS3 / Core2 / Basic** (what a Stack-chan contains) | The release image, or [from source](esp32/boards/m5unified/README.md) | Built-in speaker; the text shows on screen and touch replays it |

### D. Run the code gates

```bash
make -C csrc line                                       # on-device line editing (positive control)
make -C csrc fft                                        # inverse FFT (1,435× a naive DFT)
make -C csrc g2p PYTHON="uv run --no-project python"    # on-device G2P (2,819 vectors)
make -C csrc erf                                        # GELU's erf approximation vs libm (positive control)
make -C csrc range                                      # range-limited kernel is bit-identical to the full one
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata (positive control: fp32 rejected)
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **Omitting `PYTHON=...` falls back to `uv run python`, which demands piper-plus.**
⚠️ **`make -C csrc all-test` will not pass** — comparing against the golden output needs
`csrc/*.bin` (the exported weights). Export them from the downloaded `.pt` with
`scripts/export_c_weights.py` and it passes.

### E. Try it in a browser

**<https://ayutaz.github.io/sanoTTS-jp/>** hosts the same C99 core compiled to WebAssembly.
It is published by [`pages.yml`](.github/workflows/pages.yml) (runs on a push to `main`;
weights and dictionary are pulled from release **v0.3.0 at a pinned tag and checked against
their SHA-256**).

It needs no install and no setup: type `今日は良い天気ですね。` into the box
and it speaks. It takes **kanji, katakana and hiragana** directly — no marker character is
needed, because the C side decides the route (`saan_g2p_classify()`).

- **It runs the same code as the ESP32.** `csrc/` and `esp32/main/saan_kanji.c` are compiled
  to wasm unchanged, and **the arena is the same 180,224 B as on hardware**
  (→ [D-050](docs/decisions.md#d-050))
- The first load pulls the **13,702,320 B dictionary (5,476,122 B with `gzip -9`)**.
  ⚠️ Expect a wait on a slow link
- Measured in **Chrome 152** (headless): PCM is **bit-identical to node**, and a short
  utterance synthesizes in **9.6–23.0 ms** ([M-95](docs/measurements.md#m-95)).
  Someone has since listened on **both lanes** — reported fine, no dropouts
  ([M-96](docs/measurements.md#m-96)). ⚠️ **One listener, no control, not blind.**
  ⚠️ **Neither mobile nor Safari was measured**
  ([M-94](docs/measurements.md#m-94))
- Listened to on **both lanes**: fine, no dropouts ([M-96](docs/measurements.md#m-96)).
  ⚠️ **One listener, no control, not blind.** As for resampling, `AudioContext` turns out to
  honour 22,050 Hz (M-95 §3), so the old warning below no longer holds as stated:
  you hear does **not** match the checksums
- ⚠️ **The deliverable is still the ESP32.** The web page is a door, not the goal
  ([D-007](docs/decisions.md#d-007))

To run it locally (needs emcc). ⚠️ **Everything must sit flat next to `index.html`**, the same
layout `.github/workflows/pages.yml` builds in CI:

```bash
bash web/build.sh                                   # → web/dist/*.wasm and *.mjs
mkdir -p /tmp/saan-site
cp web/index.html web/main.js web/dist/*.mjs web/dist/*.wasm /tmp/saan-site/
cp csrc/student_i8.bin /tmp/saan-site/              # = saanotts-jp-v3-int8.bin from the release
gzip -9 -c csrc/k1_dict.bin > /tmp/saan-site/k1_dict.bin.gz   # = k1-dict-438750.bin

# ⚠️ **Stopping here leaves all four footer links 404** (measured; the demo still speaks,
#    so you only find out when you click): NOTICE.txt / NOTICE-openjtalk.txt /
#    NOTICE-dictionary.txt / LICENSE-MODEL.md
cp LICENSE-MODEL.md /tmp/saan-site/                 # the repo copy is fine
#   (it is SHA-256 identical to the LICENSE-MODEL.md release asset — checked)
# ⚠️ The three NOTICE*.txt files do **not** exist in the repo under those names, so pull
#    them from the release. ⚠️ **Needs network** (pages.yml pulls the same three in CI)
gh release download v0.3.0 -R ayutaz/sanoTTS-jp -D /tmp/saan-site --clobber \
    -p 'NOTICE.txt' -p 'NOTICE-openjtalk.txt' -p 'NOTICE-dictionary.txt'

uv run --no-project python -m http.server -d /tmp/saan-site 8000
#   ⚠️ `python3 -m http.server` is blocked by the hook (D-012)
```

### Full setup (kanji→kana conversion / training / label generation)

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ before uv sync
uv sync
```

⚠️ **This still does not get you the teacher checkpoint (private).** You need it only to
regenerate labels or retrain; kanji→kana conversion works from the piper-plus sources alone.

## Downloads

**Everything is in the latest release, [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0).**
⚠️ **The model weights are bit-identical across every release since v0.1.0** — no retraining.

| Asset | Where | What it is |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Synthesized WAVs |
| `saanotts-jp-v3-stage4.pt` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | PyTorch weights (2,744,874 B) |
| `saanotts-jp-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | int8 blob for the C99 core (**654,032 B, format v2**). ⚠️ The v1 from v0.2.0 is rejected |
| `saanotts-jp-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | fp32 blob for reference and debugging |
| `golden-v3-int8.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Reference output for `make -C csrc int8-golden` |
| `golden-v3-fp32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Reference output for `make -C csrc test` |
| `m5-cores3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **M5Stack CoreS3 / Stack-chan** (16 MB required) |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **Kanji input**, UART0 (16 MB required) |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Same, **USB Serial/JTAG** (for native-USB boards) |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | **Kana input**, UART0 (8 MB+) |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Same, **USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | Kana input, unoptimized (**the PIE control**) |
| `k1-dict-438750.bin` | [v0.3.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.0) | The dictionary blob alone (13,702,320 B) |

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
| **Accent** | **37/37** sign agreement with the teacher across 37 minimal pairs |
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

## What is not known

**This is the most important section here.** For all the numbers above, these are unverified.

| | |
|---|---|
| **Whether it sounds good** | ⚠️ **Human listening amounts to two sessions (M-91 / M-93), each one listener, no control, not blind.** It says no more than "not broken". Every quality number comes from a **predictor** (SCOREQ / UTMOS / DNSMOS), and none is calibrated for Japanese — **real human speech scores only SCOREQ 2.50 / UTMOS 2.30**. So **"0.644 of the teacher" is not "64 where the teacher is 100"** but a ratio between scores of an uncalibrated predictor (n=24). **Do not compare the absolute values against English papers** |
| **Real time over a whole utterance** | ⚠️ A full-chunk pull is 0.446, but the 38-frame warmup lands in the first pull, so **a whole utterance is 0.54–0.71**. Which denominator the requirement means is undecided |
| **The cost of pruning the dictionary** | ⚠️ 0.32% of phonemes differ from the host (n=298, M-77). A dropped word is not silent — it is **re-segmented and misread** (`上毛` → `上` + `毛`) |
| **I2S output on a DevKit** | ⚠️ `saan_i2s` (direct I2S) is **untested on hardware**. The only path that has made sound is M5Unified |
| **Other boards** | ⚠️ Only **one CoreS3** was measured here. Two independent third-party reports exist ([AtomS3 1.718](https://github.com/magatsux2019/sanotts-atoms3-results) / [CoreS3 1.558](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)), but **both predate the speed work** and neither has been reproduced here |
| **Distance to the official implementation** | ⚠️ It reports **0.22× real time** on the same chip; this is not there yet |

The full list, and how each was measured, is in [`MODEL_CARD.md`](MODEL_CARD.md) §4.

> 🙏 **The most valuable contribution is telling us what it sounds like.** No board needed —
> just play [`saanotts-jp-v3-samples.zip`](https://github.com/ayutaz/sanoTTS-jp/releases/latest).
> **"It sounds wrong" can carry more information than a number with n=24.**

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

⚠️ Most of this repository was **written by an AI agent (Claude Code)**. The discipline that
requires — never write a guess as a number, never delete a correction, never add a gate
without a positive control — is spelled out in `CONTRIBUTING.md` and `CLAUDE.md`.

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

**The corpus text itself is not distributed.** Per-source provenance is in [`NOTICE.md`](NOTICE.md).
