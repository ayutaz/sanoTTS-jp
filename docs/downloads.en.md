# Downloads

> ✅ **v1.2.0 is the current release** (2026-09-19; **30 assets**). It replaces 14 of them —
> the 10 firmware images, both Arduino .zip files, `MODEL_CARD.md` and `LICENSE-MODEL.md` —
> with a build whose static DIRAM is **49,320 B smaller than `v1.1.0`** (260,855 → 211,535 B, −18.9%), measured on hardware
> ([M-149](measurements.md#m-149)). **The audio, the weights and the dictionary did not change by
> a single byte**, and the remaining 15 assets still carry the SHA-256 they had in **v1.0.0**.
> The weights are **v4** —
> retrained on distillation text with JSUT removed, leaving CC0 / public-domain only. See
> the [release notes](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) and
> [M-119](measurements.md#m-119)–[M-134](measurements.md#m-134).
>
> ⚠️ **The v0.3.0 / v0.3.1 `NOTICE.txt` / `LICENSE-MODEL.md` / `MODEL_CARD.md` /
> `saanotts-jp-v4-samples.zip` under-attribute three materials**
> (LibriTTS-R, CML-TTS, AISHELL-3 — see [C-081](decisions.md#c-081)).
> ❌ **We decided not to replace them** ([D-061](decisions.md#d-061)).
> **Correct attribution ships from v1.0.0 onward.**


*[← README](../README.en.md)*

⚠️ **Every asset named here is checked for existence by CI** (`scripts/check_release_assets.py` — the guard added after C-052). Dead links cannot be left alone.

**Everything is in the latest release, [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0).**
⚠️ **The tag `v1.0.0` and the asset name `v4` are two different axes** — `v4` is the *model*
version, the same way v0.3.x shipped `-v3-` assets.
⚠️ **The weights changed in v1.0.0** — v0.1.0 through v0.3.1 all shipped a bit-identical
`-v3-`; v1.0.0 ships **v4**, retrained on distillation text with JSUT removed
([D-057](decisions.md#d-057) / [D-059](decisions.md#d-059)).
⚠️ **The v3 assets are still there** — you can download them from the v0.3.1 tag.

| Asset | Where | What it is |
|---|---|---|
| `saanotts-jp-v4-samples.zip` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | Synthesized WAVs |
| `saanotts-jp-v4-stage4.pt` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | PyTorch weights (2,744,874 B) |
| `saanotts-jp-v4-int8.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | int8 blob for the C99 core (**654,032 B, format v2**). ⚠️ The v1 from v0.2.0 is rejected |
| `saanotts-jp-v4-fp32.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | fp32 blob for reference and debugging |
| `golden-v4-int8.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | Reference output for `make -C csrc int8-golden` |
| `golden-v4-fp32.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | Reference output for `make -C csrc test` |
| `m5-cores3-firmware-kanji-16mb.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | **M5Stack CoreS3 / Stack-chan** (16 MB required) |
| `esp32s3-firmware-kanji-16mb.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | **Kanji input**, UART0 (16 MB required) |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | Same, **USB Serial/JTAG** (for native-USB boards) |
| `esp32s3-firmware-w8a8-pie.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | **Kana input**, UART0 (8 MB+) |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | Same, **USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | Kana input, unoptimized (**the PIE control**) |
| `k1-dict-438750.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | The dictionary blob alone (13,702,320 B) |

**For small flash** (shipped in [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0). ⚠️ **reading accuracy drops** — the 16 MB baseline above is 0.63% phoneme error):

| Asset | Where | What |
|---|---|---|
| `esp32s3-firmware-kanji-8mb.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | 8 MB **DevKit** (228,000 entries / 1.01%) |
| `m5-cores3-firmware-kanji-8mb.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | 8 MB **M5Stack boards** (213,000 / 1.09%). ⚠️ **Use this one if the board has M5Unified** |
| `esp32s3-firmware-kanji-4mb.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | **4 MB** (135,000 / 1.94%). ✅ **Ran on an ATOMS3 with no PSRAM** (M-109) |
| `esp32s3-firmware-kanji-2mb-budget.bin` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | **A 2 MB budget** (44,000 / 3.86%). ⚠️ **Flash it as a 4 MB image** |
| `k1-dict-228000-8mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | The 8 MB / DevKit dictionary alone |
| `k1-dict-213000-8mb-m5.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | The 8 MB / M5Stack dictionary alone |
| `k1-dict-135000-4mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | The 4 MB dictionary alone |
| `k1-dict-44000-2mb.bin` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | The 2 MB-budget dictionary alone |
| `SHA256SUMS.txt` | [v1.0.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.0.0) | SHA-256 of **27 of the 28 assets** (no self-referencing line). ⚠️ `v1.1.0` ships a **different** one covering 29 of its 30 assets — the file differs per tag |

⚠️ **Flash the partition table and the dictionary as a set.** A mismatch does **not** stop the
device — the readings silently degrade. When in doubt use a full flash image (write it at offset 0).

⚠️ **4 MB / 2 MB were confirmed on someone else's hardware** (M-109, including a PSRAM-less ATOMS3),
but **no checksum, steady-state xRT, or underrun count was reported**, and I have not reproduced it.

⚠️ **There is no 2 MB ESP32-S3 part** (WROOM-1 is N4 / N8 / N16). The 2 MB row only says it *fits*.

## Arduino / PlatformIO library (added in `v1.1.0`)

Two lines in `lib_deps` ([`arduino/README.md`](../arduino/README.md) / [D-065](decisions.md#d-065)).

| File | Where | What | License |
|---|---|---|---|
| `sanoTTS-jp-arduino.zip` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | The Arduino / PlatformIO library (C99 core, on-device G2P, dictionary reader, Open JTalk, C++ wrapper, 3 examples, partition tables). 308,005 B / 92 files | **MIT** |
| `sanoTTS-jp-voice-tsukuyomi-v4.zip` | [v1.2.0](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v1.2.0) | The 654,032-byte weights as an `aligned(16)` C array. 960,142 B | ⚠️ **`LicenseRef-sanoTTS-jp-Model-1.0`** (not MIT) |

⚠️ **The asset names carry no version.** `releases/latest/download/<name>` requires an exact
match ([C-097](decisions.md#c-097) — we actually hit this); the version lives inside
`library.properties` (`version=1.2.0`). To pin, use
`releases/download/v1.2.0/sanoTTS-jp-arduino.zip`.
⚠️⚠️ **The `v1.1.0` .zip is a different file** — `v1.2.0` carries the reclaimed RAM and one extra
example ([M-149](measurements.md#m-149)). **Pinning `v1.1.0` gets you the old one.**

⚠️ **`v1.2.0` changes no model or dictionary byte**, but the 10 firmware images and both .zip files
**were replaced** ([M-149](measurements.md#m-149)). The `v1.0.0` links above still work.
