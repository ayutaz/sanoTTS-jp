# sanoTTS-jp

*[日本語](README.md) · **English***

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

This applies the distillation recipe from [arXiv:2608.21378](https://arxiv.org/abs/2608.21378)
("sanoTTS") to Japanese, distilling [piper-plus](https://github.com/ayutaz/piper-plus)
(MB-iSTFT-VITS2) into three small students: Duration, Acoustic, and an iSTFT Decoder.

⚠️ **It has never run on real hardware.** Quality and memory hit their targets;
speed did not, and there is no xtensa toolchain on this machine, so it has never
even been compiled for the target (see [Status](#status)).

## What makes Japanese hard

Porting the English recipe as-is breaks. These three walls are specific to Japanese.

| Wall | How it was solved |
|---|---|
| **G2P doesn't fit** — reading kanji needs a dictionary. NAIST-JDIC measures **102 MB** | **The input contract was changed.** The device accepts only *kana + accent marks* and converts them with a **913 B table**. Kanji→kana happens offline on the host |
| **Pitch accent** — 箸 / 橋 / 端 ("chopsticks" / "bridge" / "edge") share a phoneme sequence and differ only in pitch. Aggregate scores cannot see this | 15 minimal-pair groups were added to the eval set. **35/36 sign agreement** with the teacher |
| **Devoiced vowels** — the `i` and `u` in です / した are acoustically close to frication | Separability was confirmed with per-phoneme-class spectral flatness (AUC 0.847) before adding them to the noise-injection set |

**The first one mattered most.** It is not a smaller dictionary — it is a different
decomposition of the problem. Neither the paper nor the official implementation has
a counterpart.

## Status

| Axis | State |
|---|---|
| **Quality** | **61 % of the teacher** (SCOREQ ratio 0.611), above the 0.5427 ratio the paper reports for English |
| **Accent** | **35/36** sign agreement with the teacher across 36 minimal pairs |
| **Memory** | **197 KB** — 38 % of the ESP32-S3's 512 KB SRAM. Weights are 629 KB in int8 (flash) |
| **Speed** | ⚠️ **Not met.** Ported as fp32 it runs at **2.47× real-time** (too slow) |

⚠️ **Everything above is a predictor score at n = 24–200. Nobody has listened to the audio.**
"Teacher ratio 0.611" does not mean "61 % as good as the teacher" — it is a ratio of
scores from predictors that are **not calibrated for Japanese**. Real human Japanese
speech scores only **SCOREQ 2.50 / UTMOS 2.30** here, so **do not compare the absolute
numbers against English papers**.

**Speed is the only thing left.** It needs an int8 kernel using the ESP32-S3's PIE (SIMD).
The official implementation reports **0.22× real-time measured** on the same chip, which
supports the direction.

**The PIE kernel is written** — the dot product now uses `ee.vmulas.s8.accx` (a 16-lane
int8 multiply-accumulate), verified **bit-identical to the scalar path under QEMU**
(covering 77.1% of the MACs).

⚠️ **That still does not mean it got faster.** QEMU is not cycle-accurate, so **speed has
never been measured**. Whether it reaches 0.22× real-time can only be settled on real
ESP32-S3 hardware.

## What you can run today

Verified by actually running it in a fresh clone. No teacher model or trained weights needed.

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv sync
```

```bash
# On-device G2P — the most interesting part of this repo
make -C csrc g2p
#   → 2,819 vectors match the Python implementation exactly, table SHA-256 check, negative control

# Inverse FFT (1,435× faster than the naive DFT)
make -C csrc fft

# Loss properties and label-pack round-trip
uv run python scripts/test_losses.py
uv run python scripts/test_labelpack.py
```

### What you cannot run

| | Why |
|---|---|
| **Hear the audio** | Synthesized audio is not distributed yet (**planned for the initial release**) |
| **Trained weights** | Not distributed yet (**planned for the initial release**). `csrc/*.bin` is not part of the distribution today |
| **Label generation / training** | The teacher checkpoint lives in a private repository |
| **`make -C csrc all-test`** | Needs the weights, so it does not pass in a fresh clone |

⚠️ **In other words, this repository alone cannot reproduce the audio.** What is published
is the **method, the code, and the measurement record** — not a working model.

## Architecture

```
Japanese text (kanji + kana)
   │  host side, offline (OpenJTalk)
   ▼
kana intermediate    きょ][おわよ][いて][んきです°ね    [ rise / ] downstep / # phrase / ° devoiced
   │  device side — a 913 B table, nothing else
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
- **On-device G2P** (`csrc/g2p.c`) — 913 B table / 2.4 KB code / **0 B working memory**
- **The full distillation path** — teacher label generation → 4-stage training → evaluation
  (SCOREQ / UTMOS / DNSMOS / kana CER / per-phoneme-class spectral flatness)

## Possibly useful elsewhere

- **`csrc/`** — a dependency-free C99 inference core. Readable on its own as an MCU-sized
  TTS decoder
- **`csrc/g2p.c` + `scripts/kana_g2p.py`** — the kana intermediate representation and its C
  implementation. **One answer to the G2P problem when putting Japanese TTS on an MCU**
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
| [`docs/measurements.md`](docs/measurements.md) | **Primary source for measurements**, M-1–M-51. Every entry has a reproduction command |
| [`docs/decisions.md`](docs/decisions.md) | Decisions D-001–D-034 and the **correction log C-001–C-028** |
| [`docs/upstream-sanotts.md`](docs/upstream-sanotts.md) | Facts taken from the official implementation (⚠️ all upstream-reported, none reproduced here) |
| [`docs/plan/`](docs/plan/) | Work plan and remaining tasks |
| [`CLAUDE.md`](CLAUDE.md) | Implementation notes; also the operating rules for AI agents |

## How this repository is run

**Most of this project is written by an AI agent (Claude Code),** and the discipline for
that is written into the repository itself.

- **Never write a guess as a number.** If it was not measured, it says "not measured"
- **Never delete the correction log.** **28 entries** record errors of the form
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

Code and documentation are [MIT](LICENSE).
**Corpus text is not distributed.** Model weights and synthesized audio are **planned for
the initial release**; distributing them requires attribution to the Tsukuyomi-chan corpus
and others — see [`NOTICE.md`](NOTICE.md).
