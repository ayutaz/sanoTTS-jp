# sanoTTS-jp

*[日本語](README.md) · **English***

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

This applies the distillation recipe from [arXiv:2608.21378](https://arxiv.org/abs/2608.21378)
("sanoTTS") to Japanese, distilling [piper-plus](https://github.com/ayutaz/piper-plus)
(MB-iSTFT-VITS2) into three small students: Duration, Acoustic, and an iSTFT Decoder.

✅ **Two independent ESP32-S3 hardware results are available.** An M5Stack AtomS3
confirmed bit-exact inference and PCM generation; a CoreS3 ran its built-in speaker,
display, and lip sync. Their steady-state xRT values were **1.718 / 1.558**, respectively,
so neither generates in real time. The CoreS3 avoided gaps with 60% preroll
([AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results) /
[CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) /
[video](https://x.com/nnn112358/status/2095071771355725970)).

## What makes Japanese hard

Porting the English recipe as-is breaks. These three walls are specific to Japanese.

| Wall | How it was solved |
|---|---|
| **G2P doesn't fit** — reading kanji needs a dictionary. NAIST-JDIC measures **102 MB** | **The input contract was changed.** The device accepts only *kana + accent marks* and converts them with a **877 B table**. Kanji→kana happens offline on the host. ⚠️ **Re-measuring later broke this premise and the implementation went through** — a TTS-only format is 130 B → **28 B** per entry, so 438,750 entries fit a 16 MB board, and QEMU synthesizes from kanji end to end (K-7 / M-76). ⚠️ **Untested on hardware**, but a **flash-and-go 16 MB image ships in v0.2.0** |
| **Pitch accent** — 箸 / 橋 / 端 ("chopsticks" / "bridge" / "edge") share a phoneme sequence and differ only in pitch. Aggregate scores cannot see this | 15 minimal-pair groups were added to the eval set. **37/37 sign agreement** with the teacher |
| **Devoiced vowels** — the `i` and `u` in です / した are acoustically close to frication | Separability was confirmed with per-phoneme-class spectral flatness (AUC 0.847) before adding them to the noise-injection set |

**The first one mattered most.** It is not a smaller dictionary — it is a different
decomposition of the problem. Neither the paper nor the official implementation has
a counterpart.

## Status

| Axis | State |
|---|---|
| **Quality** | **64 % of the teacher** (SCOREQ ratio 0.644), above the 0.5427 ratio the paper reports for English |
| **Accent** | **37/37** sign agreement with the teacher across 37 minimal pairs |
| **Memory** | **197 KB** — 38 % of the ESP32-S3's 512 KB SRAM. Weights are 643,936 B in int8 (flash) |
| **Speed** | ⚠️ **Not met.** W8A8 + PIE steady-state xRT is **1.718** on AtomS3 (n=2, I2S disabled) and **1.558** on CoreS3 (built-in speaker + avatar). Both exceed the real-time threshold of 1.0 ([AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results/blob/main/results/atom_s3_2026-09-01.md) / [CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3/blob/main/docs/measurements.md)) |
| **Hardware audio output** | Works through M5Unified on CoreS3. With 60% preroll: **1,781 ms** to speech onset and **0** overtake gaps ([implementation](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) / [video](https://x.com/nnn112358/status/2095071771355725970)) |
| **Kanji on the device** | Synthesizes end to end under QEMU (13.7 MB dictionary / 438,750 entries). ⚠️ **Untested on hardware** |

⚠️ **Everything above is a predictor score at n = 24–200, and exactly one person has
listened, once** (the β decision, M-60 / D-038). "Teacher ratio 0.644" does not mean
"64 % as good as the teacher" — it is a ratio of scores from predictors that are **not
calibrated for Japanese**. Real human Japanese speech scores only
**SCOREQ 2.50 / UTMOS 2.30** here, so **do not compare the absolute numbers against
English papers**.

**Two independent hardware reports are now available.** The int8 kernel uses the
ESP32-S3's PIE (SIMD): `ee.vmulas.s8.accx` replaces the dot product and covers **99.4%**
of the MACs.

- **AtomS3:** two W8A8 + PIE runs reproduced steady-state xRT **1.718**. I2S was disabled,
  but the checksum and amplitude statistics of all 27,136 PCM samples exactly matched
  the QEMU baseline
  ([measurement](https://github.com/magatsux2019/sanotts-atoms3-results/blob/main/results/atom_s3_2026-09-01.md))
- **CoreS3:** with the M5Unified built-in-speaker path, m5stack-avatar, and lip sync,
  steady-state xRT was **1.558**. A 60% preroll gave **1,781 ms** to speech onset and
  **zero** overtake gaps
  ([implementation and measurements](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) /
  [video](https://x.com/nnn112358/status/2095071771355725970))

⚠️ **Correct inference and hardware playback are confirmed, but generation itself is
not real-time yet.** The CoreS3 result hides the deficit with preroll. Hardware audio is
confirmed for its M5Unified path; this repository's `saan_i2s` path and the kanji path
remain untested. Neither result reaches the **0.22× real-time** reported by the official
implementation on the same chip.

**What QEMU has verified:**

| When | What |
|---|---|
| 2026-08-30 | The shipping firmware **boots → mmaps the weights → runs G2P → synthesizes → converts to int16**. **PIE is bit-identical to the scalar path across all 27,136 samples** (with a negative control, M-62) |
| 2026-08-31 | **The device reading kanji text directly** also ran to completion (K-7 / M-76). **v0.2.0 ships a flash-and-go 16 MB image for it** |

✅ **The kana path has run inference and generated PCM on AtomS3, and played through the
built-in speaker on CoreS3.** The CoreS3 audio path is a derivative implementation using
M5Unified; generation without preroll is still not real-time. **This repository's
`saan_i2s` path and the kanji path remain untested on hardware.**

## Getting started

**Four entry points. A / B / D need neither piper-plus nor the teacher model**
(measured on a fresh clone).

| | Goal | What you need | Time |
|---|---|---|---|
| **A** | **Hear it** | `saanotts-jp-v3-samples.zip` from [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | 1 min |
| **B** | **Synthesize your own text** | + minimal setup + `saanotts-jp-v3-stage4.pt` | 10 min |
| **C** | **Make an ESP32-S3 speak** | a board (DAC optional). **ESP-IDF is not needed to just flash** | 15–30 min |
| **D** | **Run the code gates** | minimal setup only | 5 min |

### Which release holds what

**Everything is in the latest release, [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0)** (15 assets), so
`releases/latest` is enough.

| Asset | Where | What it is |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | Synthesized WAVs |
| `saanotts-jp-v3-stage4.pt` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | PyTorch weights (2,744,874 B) |
| `saanotts-jp-v3-int8.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | int8 blob for the C99 core (643,936 B) |
| `saanotts-jp-v3-fp32.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | fp32 blob, for reference and debugging |
| `golden-v3-int8.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | Reference output for `make -C csrc golden` |
| `golden-v3-fp32.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | The same, fp32 |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | **Kana** firmware (8 MB+, flash and go) |
| `esp32s3-firmware-w8a32.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | The same without optimization (**the PIE baseline**) |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | **Kanji** image (**16 MB required**, flash and go) |
| `k1-dict-438750.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | The dictionary blob alone (13,702,320 B) |

⚠️ **The weights are bit-identical across v0.1.0 / v0.1.1 / v0.2.0** — no retraining.

> **v0.2.0 originally shipped without the weights.** The moment it became
> `releases/latest`, **all five download links in this table broke** (C-052).
> `scripts/check_release_assets.py` now **reads this table** and checks in CI that every
> asset named here actually exists under that tag.

### Minimal setup (B / D) — no piper-plus, no teacher

⚠️ **Do not use `uv sync`.** `[tool.uv.sources]` in `pyproject.toml` points at an
**absolute path to piper-plus**, so without it you get
`error: Distribution not found at: file://...` (measured). Student inference needs
only **torch / numpy / soundfile**, so create a venv that bypasses the project:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

### B. Synthesize your own text

Download `saanotts-jp-v3-stage4.pt` (2.7 MB) from
[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) and pass the
**kana intermediate form**:

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v3-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav (22.05 kHz, 1.2 s) "今日は良い天気ですね。"
```

```
[ pitch rise / ] accent nucleus / # phrase boundary / ° devoicing
```

⚠️ **Turning kanji into the intermediate form needs the full setup** (OpenJTalk).
Writing kana directly does not:

```bash
uv run python scripts/to_intermediate.py "電源を入れてください。"   # full setup
#   → で[んげんおい[れてくださ]い
```

⚠️ `--text "<kanji text>"` also synthesizes, but **that path needs the private teacher
checkpoint** (it builds phoneme IDs through the teacher's `phoneme_id_map`).
`--intermediate` calls neither the teacher nor OpenJTalk and produces a
**byte-identical WAV** for the same input (M-64 / M-65).

⚠️ **The model weights are not MIT.** Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) first.

### C. Make an ESP32-S3 speak

Instructions: [`esp32/TESTING.md`](esp32/TESTING.md). After flashing, over serial:

```
かな> きょ][おわよ][いて][んきです°ね        ← works on either firmware
かな> !今日は良い天気ですね。                ← `!` only on the kanji firmware
```

**There are two firmwares.**

| | Flash this | Accepted input | Flash size |
|---|---|---|---|
| Kana | `esp32s3-firmware-w8a8-pie.bin` | The kana intermediate form only | 8 MB+ |
| **Kanji** | `esp32s3-firmware-kanji-16mb.bin` | **Prefix a line with `!`** for kanji text | **16 MB required** |

✅ **The kana path is confirmed on two hardware configurations.** AtomS3 measured
steady-state xRT **1.718** (n=2, I2S disabled). CoreS3 played through its built-in speaker
using M5Unified; steady-state xRT **1.558** was covered by 60% preroll with **zero** gaps
([implementation](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)).
⚠️ **The kanji one has never run on hardware.**

> The reason kanji used to be rejected was "the dictionary does not fit". Re-measuring broke that
> premise, and the implementation now runs: with a TTS-only dictionary format a 16 MB
> board holds 438,750 entries, and typing `!今日は良い天気ですね。` into the QEMU UART
> makes the device tokenize the sentence itself and synthesize to completion
> ([M-76](docs/measurements.md)). The audio came out **bit-identical** to the frozen
> kana intermediate (`0x78c209af06affc01`).
>
> - Matches MeCab on **1,977/1,977** sentences (unknown words included)
> - The 126-line accent rules match the Python version **2,333/2,333**
> - The NJD chain matches the host **635/635**; labels → phoneme ids **298/298**
> - Peak RAM per sentence **104,589 B**; dictionary 13,702,320 B (438,750 entries)
>
> ⚠️ **0.32% of phonemes differ from the host** (n=298, M-77) — the on-device
> dictionary is pruned, so some readings change.
> ⚠️ **Never run on real hardware; speed and audio are unmeasured.**
> **v0.2.0 ships a flash-and-go image, but only QEMU has verified it.**
> (See [`esp32/README.md`](esp32/README.md), section 漢字対応ビルド.)

### D. Run the code gates

```bash
make -C csrc line                                       # Line editing (**with a positive control**)
make -C csrc fft                                        # Inverse FFT (1,435× the naive DFT)
make -C csrc g2p PYTHON="uv run --no-project python"    # On-device G2P (2,819 vectors)
uv run --no-project python scripts/test_losses.py
uv run --no-project python scripts/test_labelpack.py
```

⚠️ **Omitting `PYTHON=...` falls back to `uv run python`, which requires piper-plus.**
(Before separating the two, "it passed without piper-plus" was observed incorrectly. C-041)

⚠️ **`make -C csrc all-test` will not pass** — the golden comparison needs `csrc/*.bin`
(exported weights). Export them from the `.pt` you downloaded with
`scripts/export_c_weights.py`.

### Full setup (kanji→kana conversion / training / label generation)

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ before uv sync
uv sync
```

⚠️ **This still does not get you the teacher checkpoint (private).** It is only needed to
regenerate labels or retrain; kanji→kana conversion works with the piper-plus source alone.

### What you still cannot do

| | Why |
|---|---|
| **Regenerate labels / retrain** | The teacher checkpoint lives in a private repository |
| **Real-time generation without preroll** | ⚠️ AtomS3 measured steady-state xRT **1.718** and CoreS3 **1.558**. CoreS3 avoids gaps with 60% preroll ([hardware implementation](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)) |
| **Say whether it sounds good** | ⚠️ **One listener, one session.** Zero for the kanji path |

## Architecture

```
Japanese text (kanji + kana)
   │  host side, offline (OpenJTalk)
   ▼
kana intermediate    きょ][おわよ][いて][んきです°ね    [ rise / ] downstep / # phrase / ° devoiced
   │  device side — a 877 B table, nothing else
   ▼
phoneme ids ──▶ Duration Dα ──▶ Acoustic Aβ ──▶ iSTFT Decoder Gγ ──▶ 22.05 kHz PCM
                 33 K params      195 K            331 K
                            └─ joined by an explicit 40-dim latent interface (the c-line)
```

**The explicit latent interface is the point.** Collapse it into a single text→waveform
network at this budget and the paper's ablation shows the model memorizes its training
sentences and cannot read held-out text.

- **C99 inference core** (`csrc/`) — libm is the only dependency. No `malloc`; it uses a
  caller-supplied arena. The streaming build is **bit-identical** to the batch build
  (27,136 samples)
- **On-device G2P** (`csrc/g2p.c`) — **877 B** table / 1,549 B code (measured on ESP32-S3) / **0 B working memory**
- **Free-form input on the device** — type one line of the kana intermediate form over
  serial and it speaks (`csrc/line.c`, 369 B). From kanji text:
  `uv run python scripts/to_intermediate.py "..."` (host side)
- **The full distillation path** — teacher label generation → 4-stage training → evaluation
  (SCOREQ / UTMOS / DNSMOS / kana CER / per-phoneme-class spectral flatness)

## Possibly useful elsewhere

- **`csrc/`** — a dependency-free C99 inference core. Readable on its own as an MCU-sized
  TTS decoder
- **`csrc/g2p.c` + `scripts/kana_g2p.py`** — the kana intermediate representation and its C
  implementation. **One answer to the G2P problem when putting Japanese TTS on an MCU**
- **`csrc/line.c`** — 369 B of UTF-8-aware line editing. If you let people type Japanese
  over an MCU serial port, you will hit both of these: **arrow keys inserting a markup
  character, and backspace splitting a UTF-8 sequence**
- **`docs/measurements.md`** — a measurement log where every entry carries a reproduction
  command: ESP32 memory budget, extrapolation from esp-dsp cycle counts, calibration of MOS
  predictors on Japanese, and more

## Relationship to the official implementation

The official implementation, [`Ampixa/sanoTTS`](https://github.com/Ampixa/sanoTTS), exists
and is **GPL-3.0**. This repository is MIT and was written **without reading its source**,
from the numbers in the paper and from the piper-plus implementation. The official project
covers English, Nepali, Hindi, Vietnamese, Indonesian, and Chinese — **not Japanese**.

Measured values **published in the official repository's documentation** (such as 0.22×
real-time on ESP32-S3) are used to cross-check the extrapolations here. **The code was not
consulted.** That boundary is frozen as D-032 in `docs/decisions.md` and enforced
mechanically by a hook.

## Documentation

The documentation is in Japanese.
**Where numbers disagree, [`docs/measurements.md`](docs/measurements.md) wins.**

| | |
|---|---|
| [`docs/README.md`](docs/README.md) | Index and current status |
| [`docs/measurements.md`](docs/measurements.md) | **Primary source for measurements**, M-1–M-81. Every entry has a reproduction command |
| [`docs/decisions.md`](docs/decisions.md) | Decisions D-001–D-045 and the **correction log C-001–C-052** |
| [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) | Facts taken from the official implementation (⚠️ all upstream-reported, none reproduced here) |
| [`docs/plan/`](docs/plan/) | Work plan and remaining tasks |
| [`docs/release-notes/`](docs/release-notes/) | What changed in each release (**corrections are kept, not deleted**) |
| [`esp32/README.md`](esp32/README.md) | ESP32-S3 build and design decisions |
| [`esp32/TESTING.md`](esp32/TESTING.md) | **How to run it on real hardware** (flashing, speaking, the 4 lines to report) |
| [`MODEL_CARD.md`](MODEL_CARD.md) | What the model is, how it was evaluated, known limits |
| [`CLAUDE.md`](CLAUDE.md) | Implementation notes; also the operating rules for AI agents |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **How to contribute.** What helps most: hardware speed, and **what it sounds like to you** |
| [`.github/workflows/README.md`](.github/workflows/README.md) | **What CI does and does not check** (⚠️ nothing about quality, speed, or audio) |

## How this repository is run

**Most of this project is written by an AI agent (Claude Code),** and the discipline for
that is written into the repository itself.

- **Never write a guess as a number.** If it was not measured, it says "not measured"
- **Never delete the correction log.** **52 entries** record errors of the form
  "something one command would have answered, inferred instead of measured." They exist so
  the same mistake is not repeated
- **Never delete a risk just because it was settled.** Deleting it makes the question resurface
- **Always report n and a CI when n is small** (a difference at n = 3 was once turned into a
  conclusion, then refuted)
- **Do not write a gate you cannot break on purpose.** Eleven defects hid behind green tests;
  they are collected in `.claude/skills/writing-gates/`

`.claude/hooks/guard_bash.py` mechanically blocks writes to the teacher repository,
`pip install`, destroying the production label pack, fetching GPL sources, and committing
corpus text (94 regression cases).

## License

⚠️ **Code and model are under different licenses.**

| | License |
|---|---|
| **Code and documentation** in this repository | [MIT](LICENSE) |
| **Distributed model weights** ([Releases](https://github.com/ayutaz/sanoTTS-jp/releases)) | **[`LICENSE-MODEL.md`](LICENSE-MODEL.md)** — **not** MIT |

The weights are distilled from a teacher trained on the Tsukuyomi-chan Corpus, whose
terms **require attribution**, **restrict what the generated audio may be used for**, and
**propagate those obligations downstream**. We therefore cannot call the weights MIT.
Read [`LICENSE-MODEL.md`](LICENSE-MODEL.md) and [`MODEL_CARD.md`](MODEL_CARD.md) before
using them.

**Corpus text is not distributed.** Primary sources per asset: [`NOTICE.md`](NOTICE.md).
