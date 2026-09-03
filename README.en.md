# sanoTTS-jp

*[日本語](README.md) · **English***

[![CI](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml/badge.svg)](https://github.com/ayutaz/sanoTTS-jp/actions/workflows/ci.yml)

**A 559 K-parameter Japanese TTS, aimed at a $3 microcontroller (ESP32-S3).**

This applies the distillation recipe from [arXiv:2608.21378](https://arxiv.org/abs/2608.21378)
("sanoTTS") to Japanese, distilling [piper-plus](https://github.com/ayutaz/piper-plus)
(MB-iSTFT-VITS2) into three small students: Duration, Acoustic, and an iSTFT Decoder.

✅ **On 2026-09-03 an M5Stack CoreS3 (a Stack-chan) spoke kanji text directly** (M-90).
A single firmware carries the 13.7 MB dictionary; you type **`今日は良い天気ですね。`** over
serial and it speaks — **no `!` prefix**, the device decides the route itself.
**A full-chunk pull runs at xRT 0.446, meeting the RTF ≤ 0.5 requirement.**
⚠️ **Nobody has listened to it.** That is the only thing left.

**Speed was rebuilt until it met the requirement.** The two independent hardware reports
(**both predate S1**) measured AtomS3 **1.718** and CoreS3 **1.558**, neither real time
([AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results) /
[CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) /
[video](https://x.com/nnn112358/status/2095071771355725970)).
A per-step breakdown showed the MACs were not the bottleneck — activation quantization,
GELU's `erff`, per-step tensor lookups and weight copies were (M-80). Removing those gave
**S1–S5a** (M-82: 0.926) → **T1–T5 plus a 64 B cache line** (M-88: 0.497, zero underruns) →
**S5b** (M-90: 0.446). **The waveform has not changed by a single bit since M-82** (identical checksums).
⚠️ **Measured over a whole utterance it is still 0.54–0.71** (the 38-frame warmup lands in the
first pull; which denominator the requirement means has not been decided).

## What makes Japanese hard

Porting the English recipe as-is breaks. These three walls are specific to Japanese.

| Wall | How it was solved |
|---|---|
| **G2P doesn't fit** — reading kanji needs a dictionary. NAIST-JDIC measures **102 MB** | **The input contract was changed.** The device accepts only *kana + accent marks* and converts them with a **877 B table**. Kanji→kana happens offline on the host. ⚠️ **Re-measuring later broke this premise and the implementation went through** — a TTS-only format is 130 B → **28 B** per entry, so 438,750 entries fit a 16 MB board, and QEMU synthesizes from kanji end to end (K-7 / M-76). ✅ **It runs on hardware too** — a CoreS3 reproduced the QEMU checksum (M-83) and **spoke through the M5 speaker** (M-90) |
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
| **Memory** | **157 KB** of arena used on hardware (176 KB = 180,224 B reserved statically) — 34 % of the ESP32-S3's 512 KB SRAM. Free internal DRAM on the board: **136,407 B** (kana build, M-89) / **132,039 B** (kanji build with the dictionary, M-90). Weights are 654,032 B in int8 (flash; blob v2 with pre-aligned rows, +10,096 B over v1's 643,936 B) |
| **Speed** | ✅ **Requirement met.** On our own CoreS3 (W8A8 + PIE, **now the default on S3** — D-048) a full-chunk pull runs at **xRT 0.446** (kanji build, all four sentences, M-90); the kana build gives 0.494 (M-89) / 0.497 (M-88). Zero underruns, **384 ms to first sound** (the M-90 shipping build; the kana build is 432–434 ms = M-88 / M-89), one step down from 18.38 M to **11.66 M cycles**. ⚠️ **Over a whole utterance it is 0.54–0.71**, still above 0.5 (the 38-frame warmup; which denominator the requirement means is undecided). The third-party numbers **predate S1**: AtomS3 **1.718** / CoreS3 **1.558** ([AtomS3](https://github.com/magatsux2019/sanotts-atoms3-results/blob/main/results/atom_s3_2026-09-01.md) / [CoreS3](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3/blob/main/docs/measurements.md)) |
| **Hardware audio output** | ✅ **[`esp32/boards/m5unified/`](esp32/boards/m5unified/README.md) speaks through the CoreS3's built-in speaker** (M-90, with the display). A third party got there first via M5Unified: 60% preroll gave **1,781 ms** to speech onset and **0** overtake gaps ([implementation](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3) / [video](https://x.com/nnn112358/status/2095071771355725970)). ⚠️ **Nobody has listened to it** |
| **Kanji on the device** | ✅ **Confirmed on a CoreS3** (M-83: checksum identical to QEMU, kanji G2P 27.85–66.30 ms). ✅ **Also loaded into the M5 speaker build** (M-90: the 13.7 MB dictionary via `esp_mmu_map`, xRT 0.446 with W8A8+PIE). **No `!` needed** — the device classifies each line three ways. ⚠️ Not listened to |

⚠️ **Everything above is a predictor score at n = 24–200, and exactly one person has
listened, once** (the β decision, M-60 / D-038). "Teacher ratio 0.644" does not mean
"64 % as good as the teacher" — it is a ratio of scores from predictors that are **not
calibrated for Japanese**. Real human Japanese speech scores only
**SCOREQ 2.50 / UTMOS 2.30** here, so **do not compare the absolute numbers against
English papers**.

**Why it used to be slow was measured.** A per-step breakdown showed **MACs at ~30 %**, with
activation quantization (software division), GELU's `erff`, 102 tensor lookups per step and
489 KB/step of weight copies taking the rest (M-80). Everything below is **measured on our own
CoreS3** (D-047; W8A8 + PIE, `esp32/boards/m5unified/`):

| When | What went in | Full-chunk xRT | One step |
|---|---|---:|---:|
| 2026-09-02 | **S1–S5a** — remove the quantization / GELU / lookup / weight-copy overhead (M-81 / D-046) | 0.926 | 18,378,513 cyc |
| 2026-09-03 | **64 B cache line** (M-84) | 0.861 | 17,125,414 |
| 2026-09-03 | **T1** (stop the tail pull early) + **T5 fix** (generated GELU; M-87 / C-055) | — | 16,638,110 |
| 2026-09-03 | **T2** (do not compute discarded outputs) + **T3** (pipelined token block) → ✅ **requirement met** (M-88) | **0.497** | 11,724,417 |
| 2026-09-03 | **T4** (packed arena; M-89) | 0.494 | 11,659,500 |
| 2026-09-03 | **S5b** (weight-stationary PIE) + dictionary + kanji path (M-90) | **0.446** | — |

**All three checksums have been identical since M-82** (W8A8+PIE `0xa69a7ebbb5ccb05f` /
W8A32 `0xe4b645c30835d42d`) — **T1–T5 and S5b changed the waveform by zero bits**.
⚠️ **Neither QEMU's instruction counts nor its stage percentages predict hardware speed** —
one change cut the instruction count and made GELU twice as slow (C-055). The trail:
[`docs/research/s1-m5-cores3-speed.md`](docs/research/s1-m5-cores3-speed.md) and
[`docs/plan/s2-fast-kanji-m5-plan.md`](docs/plan/s2-fast-kanji-m5-plan.md).

**What hardware has verified** (all on a CoreS3, D-047):

| When | What |
|---|---|
| 2026-09-02 | **The kanji path ran** (M-83; gates G28–G31). `!今日は良い天気ですね。` → 53 ids → checksum identical to QEMU. Kanji G2P **27.85–66.30 ms** |
| 2026-09-03 | **RTF ≤ 0.5 met** (M-88 / M-89, the kana build). Zero underruns, 434 ms to first sound, 136,407 B of internal DRAM free |
| 2026-09-03 | **A Stack-chan spoke kanji, katakana and hiragana** (M-90). The 13.7 MB dictionary is mapped with `esp_mmu_map`; xRT **0.446**, **384 ms to first sound**, 132,039 B of internal DRAM free. `今日は良い天気ですね。` and `きょ][おわよ][いて][んきです°ね` produce **the same PCM** |

**What QEMU verified first:**

| When | What |
|---|---|
| 2026-08-30 | The shipping firmware **boots → mmaps the weights → runs G2P → synthesizes → converts to int16**. **PIE is bit-identical to the scalar path across all 27,136 samples** (with a negative control, M-62) |
| 2026-08-31 | **The device reading kanji text directly** also ran to completion (K-7 / M-76). **v0.2.0 ships a flash-and-go 16 MB image for it** |

**Two independent third-party hardware reports exist** (⚠️ **both predate S1** — 2026-09-01/02,
before the rework above — and **neither has been reproduced here**). The int8 kernel uses the
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

⚠️ **This repository's `saan_i2s` path (DevKit direct I2S) remains untested on hardware.**
The only path that has made sound on a board is M5Unified (`esp32/boards/m5unified/`).
⚠️ The shipped v0.1.1 / v0.2.0 images predate S1 and take console input on UART0, so
native-USB-only boards (CoreS3 / AtomS3) boot but cannot be driven; build from source there.
⚠️ The **0.22× real-time** the official implementation reports on the same chip is still ahead of us.

> 🙏 **More ESP32-S3 measurements are welcome — and above all, what it sounds like to you.**
> Instructions: [`esp32/TESTING.md`](esp32/TESTING.md). **No DAC required.**

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
| `saanotts-jp-v3-int8.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | int8 blob for the C99 core (643,936 B, **format v1**). ⚠️ **The core after S4 (2026-09-02) needs v2 (654,032 B)**; a v1 blob stops at boot with `SAAN_ERR_VERSION`. The v2 asset ships with the next release; until then build it with `scripts/export_c_weights.py --int8` |
| `saanotts-jp-v3-fp32.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | fp32 blob, for reference and debugging |
| `golden-v3-int8.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | Reference output for `make -C csrc int8-golden` (drop it at `csrc/golden_i8.bin`) |
| `golden-v3-fp32.bin` | [v0.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.2.0) | For `make -C csrc golden` (= `test`); drop it at `csrc/golden.bin` |
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
かな> きょ][おわよ][いて][んきです°ね        ← the kana intermediate form
かな> 今日は良い天気ですね。                  ← on a kanji build, just type it
```

**No marker is required** on a current source build. The device looks at the line and picks
one of **three routes** (`saan_g2p_classify()`): if the frozen tokenizer consumes the line it
takes the **kana** path; if it does not and no intermediate-form mark (`[ ] # ° _ ^ $`)
is present it takes the **dictionary** path; if it does not *and* a mark is present it
**refuses to speak** — otherwise "intermediate form + `。`" would slip through and produce
plausible-sounding nonsense. ⚠️ A plain `?` is deliberately **not** treated as a mark;
until it was excluded, questions like `本当なんでしょうか?` were refused (45 of 2,333
held-out lines = 1.93%, M-90). The same rule runs host-side as `classify_route()` in
`scripts/kana_g2p.py`, and `make -C csrc kb-parity` checks they agree
(298 held-out sentences + their 298 intermediate forms = **596/596**, M-90).
⚠️ A leading `!` still exists, but only to *force* the dictionary route for testing.

**There are two firmwares.**

| | Flash this | Accepted input | Flash size |
|---|---|---|---|
| Kana | `esp32s3-firmware-w8a8-pie.bin` | The kana intermediate form only | 8 MB+ |
| **Kanji** | `esp32s3-firmware-kanji-16mb.bin` | Kanji text too (⚠️ the v0.2.0 image still needs the `!` prefix) | **16 MB required** |

✅ **Both the kana and the kanji path run on hardware** (all on a CoreS3, D-047). The kana
build gives a full-chunk **xRT 0.494** with zero underruns (M-89); the M5 build with the
dictionary gives **0.446** (M-90), and `今日は良い天気ですね。` and
`きょ][おわよ][いて][んきです°ね` come out as the same PCM (`0xa69a7ebbb5ccb05f`).
The third-party numbers **predate S1**: AtomS3 **1.718** (n=2, I2S disabled) and CoreS3
**1.558**, the latter covered by 60% preroll with **zero** gaps
([implementation](https://github.com/nnn112358/SanoTTS-jp-M5StackCoreS3)).
⚠️ **The v0.1.1 / v0.2.0 images predate S1 and read the console on UART0**, so on a
native-USB-only board they boot but cannot be driven (M-83). **The speed above and the
`!`-free classification only exist in a build from source.**
⚠️ On ESP32-S3, **W8A8 + PIE is now the default** (D-048); pass `-DSAAN_ENABLE_PIE=0` to
measure W8A32.

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
> ✅ **Confirmed on a CoreS3 on 2026-09-02** (M-83; checksum identical to QEMU, kanji G2P
> 27.85–66.30 ms), and **on 2026-09-03 it spoke through the M5 speaker** (M-90; xRT 0.446
> with W8A8+PIE, no `!` needed). ⚠️ **Still not listened to.**
> ⚠️ **The v0.2.0 image reads the console on UART0**, so a native-USB-only board cannot be
> driven; build from source. (See [`esp32/README.md`](esp32/README.md), section 漢字対応ビルド.)

### D. Run the code gates

```bash
make -C csrc line                                       # Line editing (**with a positive control**)
make -C csrc fft                                        # Inverse FFT (1,435× the naive DFT)
make -C csrc g2p PYTHON="uv run --no-project python"    # On-device G2P (2,819 vectors)
make -C csrc erf                                        # GELU erf approximation vs libm (with positive control)
make -C csrc range                                      # Range-limited kernels (S9) match the full-range ones bit for bit
uv run --no-project python scripts/test_blob_to_header.py   # blob → .rodata header (positive control: fp32 rejected)
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
| **Say whether it sounds good** | ⚠️ **One listener, one session** (the β decision, M-60). **Nobody has listened to what the board produced**, and nobody at all to the kanji path. **This is the biggest gap right now** |
| **Fit a whole utterance into real time** | ⚠️ A full-chunk pull is **0.446** and meets the requirement, but the 38-frame warmup lands in the first pull, so **the whole utterance is 0.54–0.71** (M-88–M-90). Which denominator the requirement means is undecided |
| **Get the current speed by just flashing a release** | ⚠️ The v0.2.0 firmware predates S1 and reads the console on UART0. The speed above and the `!`-free classification only exist in a build from source |
| **Feed the released int8 blob to a current core** | ⚠️ `saanotts-jp-v3-int8.bin` is still format **v1**; a core built after S4 rejects it with `SAAN_ERR_VERSION`. Build v2 with `scripts/export_c_weights.py --int8` |

## Architecture

```
Japanese text (kanji + kana)
   │  host side, offline (OpenJTalk)  ┊  device side, 13.7 MB dictionary (-DSAAN_KANJI=1)
   ▼                                   ┊  438,750 entries mapped from flash
kana intermediate    きょ][おわよ][いて][んきです°ね    [ rise / ] downstep / # phrase / ° devoiced
   │  device side — a 877 B table      ┊  saan_g2p_classify() picks the route
   ▼                                   ┊  (kana / dictionary / refuse; both reach the same ids)
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
- **Free-form input on the device** — type one line over serial and it speaks
  (`csrc/line.c`, 369 B). Either the kana intermediate form or kanji text;
  `saan_g2p_classify()` decides (a build without the dictionary **refuses and says why**
  rather than dropping the characters). To convert on the host instead:
  `uv run python scripts/to_intermediate.py "..."`
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
- **`esp32/components/saanotts_core/saan_dict.c`** — **mapping a flash partition larger than
  8 MB**. On a board built with `CONFIG_SPI_FLASH_ROM_IMPL=y`, `esp_partition_mmap` links to
  the ROM implementation, which is handed only **128 pages = 8 MB**, so it fails with
  `ESP_ERR_NO_MEM`. `esp_mmu_map` maps it (M-90)
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
| [`docs/measurements.md`](docs/measurements.md) | **Primary source for measurements**, M-1–M-90. Every entry has a reproduction command |
| [`docs/decisions.md`](docs/decisions.md) | Decisions D-001–D-048 and the **correction log C-001–C-055** |
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
- **Never delete the correction log.** **55 entries** record errors of the form
  "something one command would have answered, inferred instead of measured." They exist so
  the same mistake is not repeated
- **Never delete a risk just because it was settled.** Deleting it makes the question resurface
- **Always report n and a CI when n is small** (a difference at n = 3 was once turned into a
  conclusion, then refuted)
- **Do not write a gate you cannot break on purpose.** Twelve defects hid behind green tests;
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
