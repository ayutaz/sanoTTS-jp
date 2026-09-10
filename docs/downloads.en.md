# Downloads

> ⚠️ **v4 (weights distilled without JSUT) is fully staged — 28 assets — but not released yet.**
> Naming the files here would make
> [`../scripts/check_release_assets.py`](../scripts/check_release_assets.py) require them
> and fail CI, so **they get added when the tag is cut.** See
> [`release-notes/v1.0.0.md`](release-notes/v1.0.0.md) and
> [M-119](measurements.md#m-119)–[M-124](measurements.md#m-124).
>
> ⚠️ **The v0.3.x `NOTICE.txt` / `LICENSE-MODEL.md` / `MODEL_CARD.md` /
> `saanotts-jp-v3-samples.zip` below under-attribute three materials**
> (LibriTTS-R, CML-TTS, AISHELL-3 — see [C-081](decisions.md#c-081)).
> Corrected assets are staged; **the upload has not been performed.**


*[← README](../README.en.md)*

⚠️ **Every asset named here is checked for existence by CI** (`scripts/check_release_assets.py` — the guard added after C-052). Dead links cannot be left alone.

**Everything is in the latest release, [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1).**
⚠️ **The model weights are bit-identical across every release since v0.1.0** — no retraining.

| Asset | Where | What it is |
|---|---|---|
| `saanotts-jp-v3-samples.zip` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Synthesized WAVs |
| `saanotts-jp-v3-stage4.pt` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | PyTorch weights (2,744,874 B) |
| `saanotts-jp-v3-int8.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | int8 blob for the C99 core (**654,032 B, format v2**). ⚠️ The v1 from v0.2.0 is rejected |
| `saanotts-jp-v3-fp32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | fp32 blob for reference and debugging |
| `golden-v3-int8.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Reference output for `make -C csrc int8-golden` |
| `golden-v3-fp32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Reference output for `make -C csrc test` |
| `m5-cores3-firmware-kanji-16mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **M5Stack CoreS3 / Stack-chan** (16 MB required) |
| `esp32s3-firmware-kanji-16mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **Kanji input**, UART0 (16 MB required) |
| `esp32s3-firmware-kanji-16mb-usbjtag.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Same, **USB Serial/JTAG** (for native-USB boards) |
| `esp32s3-firmware-w8a8-pie.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **Kana input**, UART0 (8 MB+) |
| `esp32s3-firmware-w8a8-pie-usbjtag.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Same, **USB Serial/JTAG** |
| `esp32s3-firmware-w8a32.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | Kana input, unoptimized (**the PIE control**) |
| `k1-dict-438750.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | The dictionary blob alone (13,702,320 B) |

**For small flash** (shipped in [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1). ⚠️ **reading accuracy drops** — the 16 MB baseline above is 0.63% phoneme error):

| Asset | Where | What |
|---|---|---|
| `esp32s3-firmware-kanji-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB **DevKit** (228,000 entries / 1.01%) |
| `m5-cores3-firmware-kanji-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | 8 MB **M5Stack boards** (213,000 / 1.09%). ⚠️ **Use this one if the board has M5Unified** |
| `esp32s3-firmware-kanji-4mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **4 MB** (135,000 / 1.94%). ✅ **Ran on an ATOMS3 with no PSRAM** (M-109) |
| `esp32s3-firmware-kanji-2mb-budget.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | **A 2 MB budget** (44,000 / 3.86%). ⚠️ **Flash it as a 4 MB image** |
| `k1-dict-228000-8mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | The 8 MB / DevKit dictionary alone |
| `k1-dict-213000-8mb-m5.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | The 8 MB / M5Stack dictionary alone |
| `k1-dict-135000-4mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | The 4 MB dictionary alone |
| `k1-dict-44000-2mb.bin` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | The 2 MB-budget dictionary alone |
| `SHA256SUMS.txt` | [v0.3.1](https://github.com/ayutaz/sanoTTS-jp/releases/tag/v0.3.1) | SHA-256 of **all 26 assets** (no self-referencing line) |

⚠️ **Flash the partition table and the dictionary as a set.** A mismatch does **not** stop the
device — the readings silently degrade. When in doubt use a full flash image (write it at offset 0).

⚠️ **4 MB / 2 MB were confirmed on someone else's hardware** (M-109, including a PSRAM-less ATOMS3),
but **no checksum, steady-state xRT, or underrun count was reported**, and I have not reproduced it.

⚠️ **There is no 2 MB ESP32-S3 part** (WROOM-1 is N4 / N8 / N16). The 2 MB row only says it *fits*.

