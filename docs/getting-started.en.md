# Getting started

*[← README](../README.en.md)*

**Five ways in. A / B / D / E need neither piper-plus nor the teacher model** (measured from a fresh clone).

**Five entry points. A / B / D / E need neither piper-plus nor the teacher model**
(measured from a fresh clone).

| | What you want | What you need | Time |
|---|---|---|---|
| **A** | **Hear it** | Just `saanotts-jp-v4-samples.zip` from [Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) | 1 min |
| **B** | **Synthesize your own text** | + minimal setup + `saanotts-jp-v4-stage4.pt` | 10 min |
| **C** | **Make an ESP32-S3 speak** | A board (DAC optional). **Flashing alone needs no ESP-IDF** | 15–30 min |
| **D** | **Run the code gates** | Minimal setup only | 5 min |
| **E** | **Try it in a browser** | A browser. **Nothing to install** | 1 min |

## Minimal setup (B / D)

⚠️ **Do not run `uv sync`.** `[tool.uv.sources]` in `pyproject.toml` points at an
**absolute path** to piper-plus, so without it you stop at
`error: Distribution not found at: file://...` (measured). Student inference needs only
**torch, numpy and soundfile**, so build a venv that bypasses the project:

```bash
git clone https://github.com/ayutaz/sanoTTS-jp.git && cd sanoTTS-jp
uv venv && uv pip install "torch>=2.11" "numpy<2.5" "soundfile>=0.14"
```

## B. Synthesize your own text

Download `saanotts-jp-v4-stage4.pt` (2.7 MB) from
[Releases](https://github.com/ayutaz/sanoTTS-jp/releases/latest) and pass the
**kana intermediate form**.

```bash
uv run --no-project python scripts/synthesize_student.py \
    --ckpt saanotts-jp-v4-stage4.pt \
    --intermediate "きょ][おわよ][いて][んきです°ね" --out out/
#   → out/cli_000.wav (22.05 kHz, 1.2 s) "今日は良い天気ですね。"
```

```
[ accent rise / ] accent nucleus / # phrase boundary / ° devoicing
```

**You can also pass kanji directly** — this needs the full setup below, because
kanji→kana goes through OpenJTalk:

```bash
uv run python scripts/synthesize_student.py --ckpt saanotts-jp-v4-stage4.pt \
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
(n=1,495; M-99 §4). ⚠️ **Always state n** — the same quantity reads as 0.32% at n=298 (C-059).

⚠️ **The model weights are not MIT.** Read [`LICENSE-MODEL.md`](../LICENSE-MODEL.md) first.

## C. Make an ESP32-S3 speak

The procedure is in [`esp32/TESTING.md`](../esp32/TESTING.md). After flashing, over serial:

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

⚠️ **"16 MB required" applies to the released images.** Kanji also runs on 8 MB and 4 MB
boards if you **build from source** (8 MB verified on hardware 2026-09-05,
[M-105](measurements.md#m-105); 4 MB and 2 MB **on third-party hardware**, [M-109](measurements.md#m-109) — ⚠️ **not reproduced here**).
No small-flash image is published, so you build it yourself:

| | Partition table | entries | **Phoneme error** (n=1,495) | Verified |
|---|---|---:|---:|---|
| **16 MB** (released) | `partitions_16mb.csv` | 438,750 | **0.63%** | ✅ hardware |
| 8 MB / DevKit | `partitions_8mb_kanji.csv` | 228,000 | 1.01% | ✅ hardware |
| 8 MB / **M5Stack** | `boards/m5unified/partitions_8mb.csv` | 213,000 | **1.09%** | ✅ hardware |
| **4 MB** | `partitions_4mb_kanji.csv` | 135,000 | **1.94%** | ⚠️ **third-party hardware** (not reproduced here) |
| **2 MB budget** | `partitions_2mb_kanji.csv` | 44,000 | **3.86%** | ⚠️ **third-party hardware** (not reproduced here) |

Steps: the "8 MB flash の板" and "4 MB / 2 MB 枠" sections of [`esp32/README.md`](../esp32/README.md).
⚠️ Readings get worse (deeper pruning). ⚠️ **Nobody has listened with a control** (M-91 / M-93 / M-96 / M-109 are all one listener, no control, not blinded).
⚠️ **No ESP32-S3 part has 2 MB of flash** (WROOM-1 comes as N4 / N8 / N16), so **4 MB is the floor**.
The 2 MB row only says the data *fits that budget*, measured on a larger board.
⚠️ **4 MB and 2 MB ran on third-party hardware** (M-109: an ATOMS3 with **no PSRAM**, sound heard). ⚠️ **Not reproduced here, and no checksum / xRT / underrun was reported.**

**Two ways to get sound out.**

| Board | How to flash | Audio out |
|---|---|---|
| ESP32-S3 DevKit / AtomS3 + I2S DAC | Flash the images above | External DAC, needs wiring (⚠️ `saan_i2s` is **untested on hardware**) |
| **M5Stack CoreS3 / Core2 / Basic** (what a Stack-chan contains) | The release image, or [from source](../esp32/boards/m5unified/README.md) | Built-in speaker; the text shows on screen and touch replays it |

## D. Run the code gates

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

## E. Try it in a browser

**<https://ayutaz.github.io/sanoTTS-jp/>** hosts the same C99 core compiled to WebAssembly.
It is published by [`pages.yml`](../.github/workflows/pages.yml) (runs on a push to `main`;
weights and dictionary are pulled from release **`v1.0.0` at a pinned tag and checked against
their SHA-256**).

It needs no install and no setup: type `今日は良い天気ですね。` into the box
and it speaks. It takes **kanji, katakana and hiragana** directly — no marker character is
needed, because the C side decides the route (`saan_g2p_classify()`).

- **It runs the same code as the ESP32.** `csrc/` and `esp32/main/saan_kanji.c` are compiled
  to wasm unchanged, and **the arena is the same 180,224 B as on hardware**
  (→ [D-050](decisions.md#d-050))
- The first load pulls the **13,702,320 B dictionary (5,476,122 B with `gzip -9`)**.
  ⚠️ Expect a wait on a slow link
- Measured in **Chrome 152** (headless): PCM is **bit-identical to node**, and a short
  utterance synthesizes in **9.6–23.0 ms** ([M-95](measurements.md#m-95)).
  Someone has since listened on **both lanes** — reported fine, no dropouts
  ([M-96](measurements.md#m-96)). ⚠️ **One listener, no control, not blind.**
  ⚠️ **Neither mobile nor Safari was measured**
  ([M-94](measurements.md#m-94))
- Listened to on **both lanes**: fine, no dropouts ([M-96](measurements.md#m-96)).
  ⚠️ **One listener, no control, not blind.** As for resampling, `AudioContext` turns out to
  honour 22,050 Hz (M-95 §3), so the old warning below no longer holds as stated:
  you hear does **not** match the checksums
- ⚠️ **The deliverable is still the ESP32.** The web page is a door, not the goal
  ([D-007](decisions.md#d-007))

To run it locally (needs emcc). ⚠️ **Everything must sit flat next to `index.html`**, the same
layout `.github/workflows/pages.yml` builds in CI:

```bash
bash web/build.sh                                   # → web/dist/*.wasm and *.mjs
mkdir -p /tmp/saan-site
cp web/index.html web/main.js web/dist/*.mjs web/dist/*.wasm /tmp/saan-site/
cp csrc/student_i8.bin /tmp/saan-site/              # = saanotts-jp-v4-int8.bin from the release
gzip -9 -c csrc/k1_dict.bin > /tmp/saan-site/k1_dict.bin.gz   # = k1-dict-438750.bin

# ⚠️ **Stopping here leaves all four footer links 404** (measured; the demo still speaks,
#    so you only find out when you click): NOTICE.txt / NOTICE-openjtalk.txt /
#    NOTICE-dictionary.txt / LICENSE-MODEL.md
cp LICENSE-MODEL.md /tmp/saan-site/                 # the repo copy is fine
#   (it is SHA-256 identical to the LICENSE-MODEL.md release asset — checked)
# ⚠️ The three NOTICE*.txt files do **not** exist in the repo under those names, so pull
#    them from the release. ⚠️ **Needs network** (pages.yml pulls the same three in CI)
#    ⚠️ **The tag is `v1.0.0`.** The NOTICE files in `v0.3.0` / `v0.3.1` are **still missing
#       attributions** (LibriTTS-R / CML-TTS / AISHELL-3 and the full Apache text; D-061
#       decided not to fix them).
gh release download v1.0.0 -R ayutaz/sanoTTS-jp -D /tmp/saan-site --clobber \
    -p 'NOTICE.txt' -p 'NOTICE-openjtalk.txt' -p 'NOTICE-dictionary.txt'

uv run --no-project python -m http.server -d /tmp/saan-site 8000
#   ⚠️ `python3 -m http.server` is blocked by the hook (D-012)
```

## Full setup (kanji→kana conversion / training / label generation)

```bash
git clone https://github.com/ayutaz/piper-plus.git ~/piper-plus       # MIT
cd sanoTTS-jp
python3 deploy/retarget_sources.py --root ~/piper-plus                # ⚠️ before uv sync
uv sync
```

⚠️ **This still does not get you the teacher checkpoint (private).** You need it only to
regenerate labels or retrain; kanji→kana conversion works from the piper-plus sources alone.

