# Downloads

*[← README](../README.en.md)*

⚠️ **Every asset named here is checked for existence by CI** (`scripts/check_release_assets.py` — the guard added after C-052). Dead links cannot be left alone.

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

