# sanoTTS-jp

*[日本語](README.md) · **English***

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

This applies the distillation recipe from [arXiv:2608.21378](https://arxiv.org/abs/2608.21378)
("sanoTTS") to Japanese, distilling [piper-plus](https://github.com/ayutaz/piper-plus)
(MB-iSTFT-VITS2) into three small students: Duration, Acoustic, and an iSTFT Decoder.

⚠️ **It has never run on real hardware.** Quality and memory hit their targets;
**speed has never been measured** — there is no ESP32-S3 board on hand
(see [Status](#status)). The toolchain (ESP-IDF v5.5) and QEMU are installed, and the
build plus bit-exactness verification do pass.

## What makes Japanese hard

Porting the English recipe as-is breaks. These three walls are specific to Japanese.

| Wall | How it was solved |
|---|---|
| **G2P doesn't fit** — reading kanji needs a dictionary. NAIST-JDIC measures **102 MB** | **The input contract was changed.** The device accepts only *kana + accent marks* and converts them with a **877 B table**. Kanji→kana happens offline on the host |
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
| **Memory** | **197 KB** — 38 % of the ESP32-S3's 512 KB SRAM. Weights are 629 KB in int8 (flash) |
| **Speed** | ⚠️ **Not met.** Ported as fp32 it runs at **2.47× real-time** (too slow) |

⚠️ **Everything above is a predictor score at n = 24–200. Nobody has listened to the audio.**
"Teacher ratio 0.644" does not mean "64 % as good as the teacher" — it is a ratio of
scores from predictors that are **not calibrated for Japanese**. Real human Japanese
speech scores only **SCOREQ 2.50 / UTMOS 2.30** here, so **do not compare the absolute
numbers against English papers**.

**Measuring speed on real hardware is the only thing left.** The int8 kernel using the
ESP32-S3's PIE (SIMD) **is written and verified bit-identical under QEMU** (99.4% of MACs).
The official implementation reports **0.22× real-time measured** on the same chip, which
supports the direction.

**The PIE kernel is written** — the dot product now uses `ee.vmulas.s8.accx` (a 16-lane
int8 multiply-accumulate), verified **bit-identical to the scalar path under QEMU**
(covering **99.4%** of the MACs).

⚠️ **That still does not mean it got faster.** QEMU is not cycle-accurate, so **speed has
never been measured**. Whether it reaches 0.22× real-time can only be settled on real
ESP32-S3 hardware.

## Getting started

**Four entry points. A / B / D need neither piper-plus nor the teacher model**
(measured on a fresh clone).

| | Goal | What you need | Time |
|---|---|---|---|
| **A** | **Hear it** | `saanotts-jp-v3-samples.zip` from [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | 1 min |
| **B** | **Synthesize your own text** | + minimal setup + `saanotts-jp-v3-stage4.pt` | 10 min |
| **C** | **Make an ESP32-S3 speak** | + ESP-IDF v5.5 + a board (DAC optional) | 15–30 min |
| **D** | **Run the code gates** | minimal setup only | 5 min |

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
かな> きょ][おわよ][いて][んきです°ね
```

⚠️ **The device does not accept kanji** (the dictionary does not fit).
⚠️ **Nobody has measured the speed yet.**

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
| **Measure real-hardware speed** | ⚠️ **No ESP32-S3 board here.** See [`esp32/TESTING.md`](esp32/TESTING.md) |

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
| [`docs/measurements.md`](docs/measurements.md) | **Primary source for measurements**, M-1–M-67. Every entry has a reproduction command |
| [`docs/decisions.md`](docs/decisions.md) | Decisions D-001–D-041 and the **correction log C-001–C-045** |
| [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) | Facts taken from the official implementation (⚠️ all upstream-reported, none reproduced here) |
| [`docs/plan/`](docs/plan/) | Work plan and remaining tasks |
| [`docs/release-notes/`](docs/release-notes/) | What changed in each release (**corrections are kept, not deleted**) |
| [`esp32/README.md`](esp32/README.md) | ESP32-S3 build and design decisions |
| [`esp32/TESTING.md`](esp32/TESTING.md) | **How to run it on real hardware** (flashing, speaking, the 4 lines to report) |
| [`MODEL_CARD.md`](MODEL_CARD.md) | What the model is, how it was evaluated, known limits |
| [`CLAUDE.md`](CLAUDE.md) | Implementation notes; also the operating rules for AI agents |

## How this repository is run

**Most of this project is written by an AI agent (Claude Code),** and the discipline for
that is written into the repository itself.

- **Never write a guess as a number.** If it was not measured, it says "not measured"
- **Never delete the correction log.** **45 entries** record errors of the form
  "something one command would have answered, inferred instead of measured." They exist so
  the same mistake is not repeated
- **Never delete a risk just because it was settled.** Deleting it makes the question resurface
- **Always report n and a CI when n is small** (a difference at n = 3 was once turned into a
  conclusion, then refuted)
- **Do not write a gate you cannot break on purpose.** Six defects hid behind green tests;
  they are collected in `.claude/skills/writing-gates/`

`.claude/hooks/guard_bash.py` mechanically blocks writes to the teacher repository,
`pip install`, destroying the production label pack, fetching GPL sources, and committing
corpus text (83 regression cases).

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
