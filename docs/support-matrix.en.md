# Status

*[← README](../README.en.md)*

**What works / which boards were verified / dictionary size vs. reading accuracy.**
Numbers come from [`measurements.md`](measurements.md) (Japanese; every entry has a repro command).

⚠️ **"✅ hardware" and "⚠️ third-party hardware" are kept apart.** The latter was **not reproduced here**.

⚠️ **The shipping weights are v4** (JSUT removed; **`v1.0.0`, 2026-09-12**), verified on the
16 MB M5 CoreS3 ([M-124](measurements.md#m-124) / [M-130](measurements.md#m-130): xRT **0.448**,
**0** underruns, **bit-identical PCM whether the sentence is written in kanji or in kana**).
⚠️ **Only that one image was flashed with v4**; the 8 MB / 4 MB / 2 MB images were only
**checked by content** (no boards). ⚠️ **Nobody has heard v4 for even a second.**
⚠️ **The current release is `v1.2.0`** (2026-09-19; [M-149](measurements.md#m-149)) — 20,480 B less
static DIRAM, **bit-identical audio**, and the weights and dictionary unchanged since `v1.0.0`.
⚠️ **The board rows and the dictionary table below were measured with v3** — the dictionary and
the C code are identical across v3 and v4, so reading accuracy is unchanged.

## What works

| | State | Evidence |
|---|---|---|
| **Kana intermediate form → audio** | ✅ **on hardware** (M5 CoreS3) | [M-90](measurements.md#m-90) |
| **Kanji text → audio** (morphological analysis + accent, all on device) | ✅ **on hardware** | [M-90](measurements.md#m-90) |
| **Real-time budget** (xRT ≤ 0.5) | ⚠️ **It depends on sentence length** — [M-147](measurements.md#m-147) is the first hardware run past 224 ids. **53–203 ids meet it (0.473–0.498)**, but the median reaches **0.522 at 253 ids and 0.523 at 303 ids**. ⚠️ **v1.1.0 behaves the same** (0.526 at 303 ids), so this is **not** a MEM-7/MEM-8 regression. ⚠️ The **mean stays at 0.487–0.499 and never crosses 0.5**, and there are **zero underruns at every length**: full-chunk pulls are **bimodal at 43.8 ms / 48.6 ms**, and the share of the slow mode rises with length (33%→53%), so **only the median jumps** once it passes 50% (per-chunk work grows just 2.5%). Time to first sound **369 ms ≤ 0.8 s** | [M-147](measurements.md#m-147) / [D-049](decisions.md#d-049) / [C-054](decisions.md#c-054) |
| **Memory** (fits 512 KB SRAM) | ✅ **211,535 B static DIRAM**, measured **on hardware** (kanji build) — 61.9% of the pool, **181,119 B** free at boot, largest block **131,072 B**. The per-utterance peak runs **113,072–115,056 B** and matches `arena_peak(n)` **exactly at all six lengths**. ⚠️ v1.1.0 was 232,015 B | [M-147](measurements.md#m-147) |
| **Browser** (the same C99 core as wasm) | ✅ bit-identical PCM to the device | [M-95](measurements.md#m-95) |
| **Pitch accent** | ⚠️ the shipping **v4 scores 31/37** (v3 scored 37/37). Changing only the seed already costs 3 pairs, so this **cannot be read as damage from dropping JSUT** ([D-057](decisions.md#d-057)) | [M-118](measurements.md#m-118) / [M-117](measurements.md#m-117) |
| **Arduino / PlatformIO library** (call it from your own sketch) | ⚠️ **PCM is bit-identical to the ESP-IDF build** (QEMU, two configurations); builds under both PlatformIO and arduino-cli. ❌ **Never run on real hardware** — xRT and underruns are **unmeasured** | [M-137](measurements.md#m-137) / [D-065](decisions.md#d-065) |
| ⚠️ **Controlled listening test (G32)** | ⚠️ **partial.** **18 of the 24** held-out sentences were heard with **the teacher as an immediate control** — verdict "no problem" ([M-135](measurements.md#m-135)). ❌ **Not blinded, one listener; accent and pruning-induced misreadings still unheard** | [M-135](measurements.md#m-135) |
| ⚠️ **Actual sample-rate error** | ❌ **unmeasured** (the ESP32-S3 has no APLL) | — |

## Boards

| Board | Chip | Flash | PSRAM | State |
|---|---|---|---|---|
| **M5Stack CoreS3** | ESP32-S3 | 16 MB | 8 MB Quad | ✅ **verified on hardware** ([M-90](measurements.md#m-90) / [M-105](measurements.md#m-105)). **The shipping config** |
| **M5Stack ATOMS3 + Voice Base** | ESP32-S3 | 8 MB | **none** | ✅ **4 MB / 2 MB dictionaries ran on third-party hardware** ([M-109](measurements.md#m-109)). ⚠️ **not reproduced here** |
| ESP32-S3 DevKit / StampS3 etc. | ESP32-S3 | 8 MB+ | any | ✅ **flash a released image** (16 MB for kanji, 8 MB for kana) |
| ATOMS3R | ESP32-S3 | 8 MB | 8 MB Octal | ⚠️ **builds only** (never flashed) |
| M5Stack Core2 | **plain ESP32** | 16 MB | 8 MB | ⚠️ **builds only.** A **different chip** (Xtensa LX6, **no PIE**, different MMU window) — **never measured** |
| M5Stamp-C5 | **ESP32-C5** (RISC-V) | 4 MB | none | ❌ **expected not to work.** **No FPU** (`rv32imac`) means 106 soft-float call sites, ⚠️ **the RAM objection is gone** — [M-142](measurements.md#m-142) cut the requirement to **342,308 B**, which fits the 393,216 B of SRAM, so only the FPU reason remains ([M-106](measurements.md#m-106) §1). ⚠️ **It compiles — that is not the same as running** |
| ESP32-P4 | RISC-V | — | — | Not attempted. **Has both an FPU and PIE (`xesppie`)**, so it looks promising. ⚠️ **Its PIE instruction set differs from Xtensa's** — the kernel would be rewritten |
| ARM Cortex-M / RP2040 etc. | — | — | — | ❌ **not ported.** ⚠️ The C99 core **cross-compiles** (Xtensa / rv32imac / rv32imafc, 5/5) but there is **no bare-metal libc or HAL** |

⚠️ **Floor** ([M-106](measurements.md#m-106) §1): **~1 MB of flash (kana) / 4 MB (kanji)**, **hardware floating point**, **200 MHz+**. RAM for the kanji path totals **342,308 B** (260,896 static + 81,412 for Open JTalk's scratch heap). ⚠️ **The 260,896 B is arithmetic** — M-106's 289,568 B minus the 28,672 B the arena shrank; that configuration was not re-measured ([M-142](measurements.md#m-142) is the M5 CoreS3 build). ⚠️ **The kana-only RAM figure has not been measured.**
⚠️ **No ESP32-S3 part has 2 MB of flash** (WROOM-1 is N4 / N8 / N16), so **4 MB is the floor**.

## Dictionary size vs. reading accuracy

**Same model, same weights** — only the dictionary differs. Readings change; audio quality does not.

| Flash | Dictionary | entries | **Phonemes differing from host** (n=1,495) | Verified |
|---|---:|---:|---:|---|
| **16 MB** (released image) | 13,702,320 B | 438,750 | **0.63%** | ✅ hardware |
| 8 MB / DevKit | 7,123,088 B | 228,000 | 1.01% | ✅ hardware |
| 8 MB / M5Stack | 6,797,056 B | 213,000 | 1.09% | ✅ hardware |
| **4 MB** | 3,006,656 B | 135,000 | **1.94%** | ⚠️ third-party hardware |
| **2 MB budget** | 977,456 B | 44,000 | **3.86%** | ⚠️ third-party hardware |
| (built, not shipped) **16 MB + `rec5`** | 13,766,400 B | **538,000** | **0.60%** | ⚠️ **never flashed** ([M-108](measurements.md#m-108)) |

⚠️ "Phoneme error" is **how often the device differs from host OpenJTalk**, not how good it sounds.
**Nobody has listened with a control.**

