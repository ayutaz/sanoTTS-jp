# Arduino / PlatformIO ライブラリ 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `v1.0.0` の C99 コアを、Arduino IDE と PlatformIO の両方から `lib_deps` 1 行で使えるライブラリとして配る。

**Architecture:** `arduino/` に手書きの C++ ラッパーとマニフェストだけを置き、`csrc/` と `esp32/main/` の実体は `scripts/build_arduino_lib.py` が「前置き + 逐語 + 後置き」の規則で `arduino/src/core/`（git 管理外）に生成する。配布はリリース資産の .zip 2 本（コード / 重み）。CMake に散っていたビルドフラグは `arduino/src/sanotts_config.h` 1 枚に集約する。

**Tech Stack:** C99（`csrc/`）/ C++11（ラッパー）/ Python 3.12 stdlib のみ（生成器）/ PlatformIO `espressif32` + `framework=arduino` / arduino-cli

**Spec:** [`docs/superpowers/specs/2026-09-13-arduino-platformio-library-design.md`](../specs/2026-09-13-arduino-platformio-library-design.md)

## Global Constraints

すべてのタスクの要件に暗黙に含まれる。**spec からの逐語**。

- **コアは 1 バイトも変えない。** `csrc/` と `esp32/main/` と `esp32/components/` を編集しない。生成器が読むだけ
- **`csrc/` の実体を git に二重化しない。** `arduino/src/core/` は `.gitignore`
- **`build.flags` に `-I` を書かない。** Arduino IDE では渡せず、PlatformIO だけ通って片方でしか動かない構成になる。引用形式 `#include "openjtalk/njd.h"` の**インクルード元相対解決**に頼る
- **`SAAN_INT8_ACT` と `SAAN_PIE` を別々に定義できないようにする**（CLAUDE.md の不変条件）
- **`SANOTTS_*`（ユーザーが触る面）と `SAAN_*`（コアの内部フラグ）を混ぜない**
- **サンプルレートは 22,050 Hz 固定**（`SAAN_SR`）。`SAAN_HOP` は 256、`SAAN_CHUNK` は 8
- **arena は 176 KB = 180,224 B**（`SAAN_ARENA_BYTES`）。`saan_stream_arena_needed()` の戻り値を使わない
- **`SAAN_MAX_IDS` は 350。** 超える入力は喋らずに拒否する
- Python は `uv run --no-project python`（生成器は stdlib だけ）
- 新しいゲートには**必ず陽性対照**を付ける（`writing-gates` skill）
- 数値を docs に書く前に `recording-measurements` skill を通す

---

## File Structure

| ファイル | 責務 |
|---|---|
| `scripts/build_arduino_lib.py` | 生成 / `--check` / `--self-test` / `--zip`。**唯一の生成規則の持ち主** |
| `arduino/src/sanotts_config.h` | フラグの既定値。**唯一の設定点** |
| `arduino/src/SanoTTS.h` / `.cpp` | 公開 API。G2P 経路判定 → 合成 → PCM |
| `arduino/src/SanoTTSSpeaker.h` | 抽象インターフェース（`esp32/main/saan_audio.h` と 1:1） |
| `arduino/src/SanoTTSSpeakerM5.h` / `.cpp` | M5Unified 実装。`__has_include` で自動的に消える |
| `arduino/src/SanoTTSSpeakerI2S.h` / `.cpp` | 汎用 I2S 実装 |
| `arduino/library.properties` / `library.json` | マニフェスト 2 種 |
| `arduino/extras/partitions/*.csv` | ビルド対象外。パーティション表 |
| `arduino/examples/*` | PcmCallback / HelloKana / HelloKanji |
| `arduino/NOTICE.txt` | 帰属ブロック（4 か所目）+ Open JTalk への改変の明記 |
| `.github/workflows/ci.yml` | `arduino` job |

---

### Task 1: 生成器と G-AR1

**Files:**
- Create: `scripts/build_arduino_lib.py`
- Modify: `.gitignore`（末尾に追記）

**Interfaces:**
- Consumes: `csrc/*.c,h` / `esp32/main/*.c,h` / `esp32/components/saanotts_core/*.c,h` を読むだけ
- Produces: `arduino/src/core/**`。後続タスクはここに実体がある前提で書く。CLI は
  `build_arduino_lib.py [--check] [--self-test] [--zip DIR] [--version V] [--blob PATH]`

- [ ] **Step 1: 生成規則を決め打ちで書く（失敗する自己テストを先に）**

`scripts/build_arduino_lib.py` に `--self-test` を先に実装する。**中身より先に、何を保証するかを固定する。**

```python
def self_test() -> int:
    """陽性対照 5 件。**落ちるべきものが落ちることを確かめる。**"""
    import tempfile, shutil
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # (1) 素の生成 → --check が通る
        generate(tmp)
        if verify(tmp) != []: fails.append("素の生成が --check を通らない")
        # (2) 生成物の本文を 1 バイト変える → 落ちる
        victim = tmp / "core" / "saanotts.c"
        b = victim.read_bytes(); victim.write_bytes(b[:-2] + b"X\n")
        if verify(tmp) == []: fails.append("本文を変えても --check が通った")
        generate(tmp)
        # (3) 前置きを消す → 落ちる
        victim = tmp / "core" / "saanotts.c"
        t = victim.read_text().split("\n", 1)[1]; victim.write_text(t)
        if verify(tmp) == []: fails.append("前置きを消しても --check が通った")
        generate(tmp)
        # (4) ファイルを消す → 落ちる
        (tmp / "core" / "fft.c").unlink()
        if verify(tmp) == []: fails.append("ファイルを消しても --check が通った")
        generate(tmp)
        # (5) 余計なファイルを足す → 落ちる（生成一覧と実体の集合が一致すること）
        (tmp / "core" / "intruder.c").write_text("int x;\n")
        if verify(tmp) == []: fails.append("余計なファイルがあっても --check が通った")
    for f in fails: print(f"  NG  {f}")
    print(f"{'NG!' if fails else 'OK '} 陽性対照 5 件")
    return 1 if fails else 0
```

- [ ] **Step 2: 自己テストを走らせて落ちることを確認**

Run: `uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --self-test`
Expected: FAIL（`generate` / `verify` が未定義 → `NameError`）

- [ ] **Step 3: 取り込み一覧と生成規則を実装**

`MANIFEST` は「出力パス → (入力パス, 漢字限定か, 追加前置き)」の表。

```python
ROOT = pathlib.Path(__file__).resolve().parent.parent
CSRC = ROOT / "csrc"
MAIN = ROOT / "esp32" / "main"
COMP = ROOT / "esp32" / "components" / "saanotts_core"

# 前置きの 1 行目。**これが無いと Arduino ではフラグが 1 つも立たない。**
def _prologue(rel_depth: int, kanji_only: bool, extra: tuple[str, ...] = ()) -> str:
    up = "../" * rel_depth
    lines = [
        "/* 生成物 — scripts/build_arduino_lib.py が作る。**手で編集しない。**",
        " * 元: {src}",
        " * 規則: この前置きと末尾の後置きを剥がすと、元ファイルと bit 一致する。",
        " *       `build_arduino_lib.py --check` が毎回それを確かめる。 */",
        f'#include "{up}sanotts_config.h"',
    ]
    lines += list(extra)
    if kanji_only:
        lines.append("#if SANOTTS_ENABLE_KANJI")
    return "\n".join(lines) + "\n"

EPILOGUE_KANJI = "\n#endif /* SANOTTS_ENABLE_KANJI */\n"

# (出力相対パス, 入力パス, 漢字限定, 追加前置き)
MANIFEST: list[tuple[str, pathlib.Path, bool, tuple[str, ...]]] = [
    # --- コア（常に入る）---
    ("saanotts.c",        CSRC / "saanotts.c",        False, ()),
    ("saanotts_stream.c", CSRC / "saanotts_stream.c", False, ()),
    ("fft.c",             CSRC / "fft.c",             False, ()),
    ("saanotts_int8.c",   CSRC / "saanotts_int8.c",   False, ()),
    ("g2p.c",             CSRC / "g2p.c",             False, ()),
    ("saan_pcm.c",        MAIN / "saan_pcm.c",        False, ()),
    # --- 漢字（#if で囲う）---
    ("jdict.c",      CSRC / "jdict.c",      True, ()),
    ("accent.c",     CSRC / "accent.c",     True, ()),
    ("njd_rules.c",  CSRC / "njd_rules.c",  True, ()),
    ("label_ids.c",  CSRC / "label_ids.c",  True, ()),
    ("saan_kanji.c", MAIN / "saan_kanji.c", True, ()),
    ("saan_dict.c",  MAIN / "saan_dict.c",  True, ()),
    ("oj_heap_psram.c", COMP / "oj_heap_psram.c", True, ()),
]
# ヘッダは加工せず**逐語でコピー**する（前置きを入れるとインクルードガードの前に
# コードが来て、多重 include のたびに config が読まれる。害は無いが差分検証が複雑になる）。
HEADERS_ALWAYS = ["saanotts.h", "saanotts_stream.h", "saanotts_internal.h",
                  "saanotts_int8.h", "fft.h", "g2p.h", "g2p_table.h",
                  "erf_table.h", "token_table.h", "saan_prof.h"]
HEADERS_KANJI  = ["jdict.h", "accent.h", "njd_rules.h", "label_ids.h", "dan_table.h",
                  "oj_heap_psram.h"]
```

⚠️ **`saan_model.c` / `saan_model_rodata.c` / `saan_model.h` も MANIFEST に入れる**
（前者 2 つは `SANOTTS_MODEL_FROM_PARTITION` で片方だけが生きるよう `#if` で囲う）。
⚠️ **Open JTalk は `csrc/openjtalk/*.c` を glob して `core/openjtalk/` に入れ、
`#if SANOTTS_ENABLE_KANJI` で囲ったうえで `#include "../oj_heap_psram.h"` を前置きする**
（`-include` が使えないため。ESP-IDF の `set_source_files_properties` と同じ効果）。
⚠️ **`.h` は逐語コピー**（`*_rule_utf_8.h` を含む）。

`generate(dst)` は `dst/core/` を作り直し、`verify(dst)` は各出力から前置き / 後置きを
剥がして入力と `==` を比べ、**集合も比べる**（余計なファイル / 欠けたファイルを落とす）。

- [ ] **Step 4: 自己テストが通ることを確認**

Run: `uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --self-test`
Expected: `OK  陽性対照 5 件`

- [ ] **Step 5: 本番生成と --check**

```bash
uv run --no-project --python 3.12 python scripts/build_arduino_lib.py
uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --check
ls arduino/src/core/ | head; ls arduino/src/core/openjtalk/ | wc -l   # 14 以上
```
Expected: `--check` が `OK`。`core/` に .c/.h が揃い、`openjtalk/` に 14 本の .c

- [ ] **Step 6: .gitignore に足してコミット**

```bash
cat >> .gitignore <<'EOF'

# --- Arduino / PlatformIO ライブラリ（生成物。scripts/build_arduino_lib.py が作る）---
# ⚠️ **csrc/ の実体をここに二重化しない。** git に置かないのが D-e の決定。
arduino/src/core/
arduino/dist/
EOF
git add scripts/build_arduino_lib.py .gitignore
git status --short   # arduino/src/core/ が出ないことを確認
git commit -m "feat: csrc → Arduino ライブラリの生成器（G-AR1・陽性対照 5 件）"
```

---

### Task 2: 設定ヘッダ・マニフェスト・かな経路の C++ ラッパー

**Files:**
- Create: `arduino/src/sanotts_config.h`, `arduino/src/SanoTTS.h`, `arduino/src/SanoTTS.cpp`,
  `arduino/library.properties`, `arduino/library.json`,
  `arduino/examples/PcmCallback/PcmCallback.ino`

**Interfaces:**
- Consumes: Task 1 が生成した `arduino/src/core/**`
- Produces: `class SanoTTS`（`begin()` / `ready()` / `synthesize(const char*, PcmCallback)` /
  `setLengthScale(float)` / `checksum()` / `lastError()`）と
  `using PcmCallback = std::function<void(const int16_t*, size_t)>`

- [ ] **Step 1: `sanotts_config.h` を書く**

spec §5.2 をそのまま実装する。**`SANOTTS_ENABLE_KANJI` の既定は 0 にする**
（このタスクではまだ漢字経路のグルーを書いていない。Task 4 で 1 に上げる）。

```c
#ifndef SANOTTS_CONFIG_H
#define SANOTTS_CONFIG_H

/* ⚠️ **Arduino IDE はライブラリ単位の -D を渡せない**（library 1.5 仕様）。
 *    したがってフラグの既定値はここが唯一の置き場。PlatformIO は
 *    library.json の build.flags か、スケッチ側の build_flags で上書きできる。 */

#ifndef SANOTTS_ENABLE_KANJI
#define SANOTTS_ENABLE_KANJI 0      /* Task 4 で 1 にする */
#endif

/* ⚠️ **SAAN_INT8_ACT と SAAN_PIE を別々に立てられないようにする。**
 *    片方だけでは PIE 命令が 1 つも出ないのに「有効にしたつもり」になる（CLAUDE.md）。 */
#if !defined(SANOTTS_ENABLE_PIE)
#  if defined(CONFIG_IDF_TARGET_ESP32S3)
#    define SANOTTS_ENABLE_PIE 1
#  else
#    define SANOTTS_ENABLE_PIE 0
#  endif
#endif
#if SANOTTS_ENABLE_PIE && !defined(CONFIG_IDF_TARGET_ESP32S3)
#  error "SANOTTS_ENABLE_PIE=1 は ESP32-S3 専用（ee.* 命令が無い板ではアセンブラが落ちる）"
#endif
#if SANOTTS_ENABLE_PIE
#  define SAAN_INT8_ACT 1
#  define SAAN_PIE      1
#endif

#if SANOTTS_ENABLE_KANJI
#  define SAAN_KANJI                 1
#  define CHARSET_UTF_8              1
#  define LABEL_IDS_EXTERNAL_SCRATCH 1
#endif

/* T5-G2: erf 表 1,032 B を内部 DRAM に置く（flash の .rodata だと重みのストリームと
 * D-cache を争う）。⚠️ 値は変わらないので checksum は不変。 */
#ifndef SAAN_PORT_HEADER
#define SAAN_PORT_HEADER "saan_port_esp32.h"
#endif

#ifndef SANOTTS_MODEL_FROM_PARTITION
#define SANOTTS_MODEL_FROM_PARTITION 0     /* 既定 = .rodata 埋め込み */
#endif

/* 辞書の SHA-256。未定義なら saan_dict.c が起動時に「照合していない」と警告する。 */
/* #define SANOTTS_DICT_SHA256 "…" */
#ifdef SANOTTS_DICT_SHA256
#define SAAN_DICT_SHA256 SANOTTS_DICT_SHA256
#endif

#endif /* SANOTTS_CONFIG_H */
```

- [ ] **Step 2: マニフェスト 2 種を書く**

`arduino/library.properties`（⚠️ **`license` フィールドは仕様に無いので書かない**）:

```
name=SanoTTS-jp
version=1.1.0
author=ayutaz
maintainer=ayutaz
sentence=Japanese neural TTS that runs in real time on an ESP32-S3.
paragraph=567K-parameter distilled student (MB-iSTFT-VITS2 teacher). On-device kana and kanji G2P. Code is MIT; the voice weights ship separately under their own license.
category=Signal Input/Output
url=https://github.com/ayutaz/sanoTTS-jp
architectures=esp32
includes=SanoTTS.h
```

`arduino/library.json`:

```json
{
  "name": "SanoTTS-jp",
  "version": "1.1.0",
  "description": "Japanese neural TTS that runs in real time on an ESP32-S3 (567K params, on-device G2P).",
  "keywords": ["tts", "japanese", "esp32", "esp32-s3", "speech"],
  "repository": { "type": "git", "url": "https://github.com/ayutaz/sanoTTS-jp.git" },
  "license": "MIT",
  "frameworks": ["arduino", "espidf"],
  "platforms": ["espressif32"],
  "headers": ["SanoTTS.h"],
  "build": { "srcDir": "src", "libArchive": false }
}
```

⚠️ **`build.flags` に `-I` を書かない**（Global Constraints）。

- [ ] **Step 3: `SanoTTS.h` を書く**

```cpp
#ifndef SANOTTS_H
#define SANOTTS_H
#include <stddef.h>
#include <stdint.h>
#include <functional>
#include "sanotts_config.h"
#include "SanoTTSSpeaker.h"     // Task 3 で中身が入る。今は空の宣言だけ

class SanoTTS {
public:
    using PcmCallback = std::function<void(const int16_t* pcm, size_t nSamples)>;

    bool begin();
    bool ready()      const { return m_ready; }
    bool kanjiReady() const { return m_kanjiReady; }

    bool synthesize(const char* text, PcmCallback cb);
    bool synthesize(const char* text, size_t nbytes, PcmCallback cb);

    void  setLengthScale(float s) { m_sv = s; }
    float lengthScale() const { return m_sv; }

    uint64_t    checksum() const;          // saan_pcm_checksum()
    uint32_t    samples()  const;          // saan_pcm_samples()
    const char* lastError() const { return m_err; }

    static constexpr uint32_t kSampleRate = 22050;
    static constexpr int32_t  kMaxIds     = 350;

private:
    bool toIds(const char* text, size_t nbytes, int32_t* ids, int32_t cap, int32_t* nIds);
    bool m_ready = false, m_kanjiReady = false;
    float m_sv = 1.0f;
    const char* m_err = "";
};
#endif
```

- [ ] **Step 4: `SanoTTS.cpp` を書く（かな経路のみ）**

`esp32/main/main.c` の `speak_auto` / `speak_line` / `synth_once` を移植する。**移す不変条件**:

1. `saan_g2p_classify()` で 3 値判定。`SAAN_G2P_ROUTE_DICT` は漢字ビルドでなければ**喋らずに拒否**
2. arena は `static __attribute__((aligned(16))) uint8_t g_arena[180224]`
3. `saan_stream_init` 成功後に `a.used == saan_stream_arena_used(n_ids)` を検査（黙った確保失敗の二重防御）
4. `n_ids > kMaxIds` は**喋らずに拒否**（分布外の音を黙って出さない）
5. 発話ごとに `saan_pcm_reset()`
6. `g_chunk` は `static float[SAAN_CHUNK * SAAN_HOP]`（スタックに置かない。8,192 B）
7. float → int16 は **`saan_f32_to_i16()` だけを通す**（checksum の唯一の実装）

```cpp
#include "SanoTTS.h"
extern "C" {
#include "core/saanotts.h"
#include "core/saanotts_stream.h"
#include "core/g2p.h"
#include "core/saan_pcm.h"
#include "core/saan_model.h"
}

#define SANOTTS_ARENA_BYTES (176 * 1024)
static __attribute__((aligned(16))) uint8_t g_arena[SANOTTS_ARENA_BYTES];
static float   g_chunk[SAAN_CHUNK * SAAN_HOP];
static int16_t g_i16[SAAN_CHUNK * SAAN_HOP];
static int32_t g_ids[2 * 512 + 3];
static saan_weights g_w;

bool SanoTTS::begin() {
    if (m_ready) return true;
    if (!saan_model_open(&g_w)) { m_err = "重みを開けない（voice ライブラリを入れたか）"; return false; }
    m_ready = true;
    return true;
}

bool SanoTTS::synthesize(const char* text, size_t nbytes, PcmCallback cb) {
    if (!m_ready) { m_err = "begin() を呼んでいない"; return false; }
    int32_t n_ids = 0;
    if (!toIds(text, nbytes, g_ids, (int32_t)(sizeof g_ids / sizeof g_ids[0]), &n_ids)) return false;
    if (n_ids > kMaxIds) { m_err = "入力が長すぎる（350 ids 超）。短く区切ること"; return false; }

    saan_pcm_reset();
    saan_arena a; saan_arena_init(&a, g_arena, SANOTTS_ARENA_BYTES);
    saan_stream st;
    if (saan_stream_init(&st, &g_w, &a, g_ids, n_ids, m_sv) != SAAN_OK) {
        m_err = "saan_stream_init が失敗"; return false;
    }
    if (a.used != saan_stream_arena_used(n_ids)) {
        m_err = "arena の確保が黙って失敗している（pull すると再起動しうる）"; return false;
    }
    for (;;) {
        int32_t n = 0;
        if (saan_stream_pull(&st, g_chunk, &n) != SAAN_OK) { m_err = "saan_stream_pull が失敗"; return false; }
        if (n <= 0) break;
        const size_t ns = (size_t)n * SAAN_HOP;
        for (size_t i = 0; i < ns; ++i) g_i16[i] = saan_f32_to_i16(g_chunk[i]);
        if (cb) cb(g_i16, ns);
    }
    return true;
}
```

⚠️ `toIds()` は `saan_g2p_classify()` → かな経路は `saan_g2p()`、辞書経路は
`SANOTTS_ENABLE_KANJI` が 0 なら `m_err` を立てて `false`（Task 4 で埋める）。

- [ ] **Step 5: 例を書く**

`arduino/examples/PcmCallback/PcmCallback.ino`:

```cpp
#include <SanoTTS.h>
SanoTTS tts;
void setup() {
  Serial.begin(115200);
  if (!tts.begin()) { Serial.println(tts.lastError()); return; }
  tts.synthesize("きょ][おわよ][いて][んきです°ね",
                 [](const int16_t* pcm, size_t n) { Serial.printf("%u sample\n", (unsigned)n); });
  Serial.printf("checksum 0x%016llx / %u sample\n",
                (unsigned long long)tts.checksum(), (unsigned)tts.samples());
}
void loop() {}
```

- [ ] **Step 6: PlatformIO でビルドできることを確認**

⚠️ **PlatformIO はこの環境に無い。** 入れる（`espressif32` の toolchain で約 1〜2 GB 落ちる）:

```bash
uv venv /tmp/pio-venv --python 3.12 && /tmp/pio-venv/bin/pip -q install platformio
mkdir -p /tmp/pio-kana/src && cd /tmp/pio-kana
cp /Users/s19447/Desktop/saanoTTS-jp/arduino/examples/PcmCallback/PcmCallback.ino src/main.cpp
cat > platformio.ini <<'EOF'
[env:esp32s3]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
lib_deps = symlink:///Users/s19447/Desktop/saanoTTS-jp/arduino
build_flags = -DSANOTTS_NO_VOICE=1
EOF
/tmp/pio-venv/bin/pio run
```

Expected: リンクまで通る。⚠️ 重みが無いので `-DSANOTTS_NO_VOICE=1` のとき
`saan_model_open()` は「重みが無い」を返すスタブにする（`SanoTTS.cpp` に `#if` で置く）。

- [ ] **Step 7: コミット**

```bash
git add arduino/library.properties arduino/library.json arduino/src arduino/examples
git status --short   # arduino/src/core/ が出ないことを確認
git commit -m "feat: Arduino ライブラリの設定ヘッダ・マニフェスト・かな経路"
```

---

### Task 3: スピーカー（M5Unified / 汎用 I2S）と `say()`

**Files:**
- Create: `arduino/src/SanoTTSSpeaker.h`, `arduino/src/SanoTTSSpeakerM5.h` / `.cpp`,
  `arduino/src/SanoTTSSpeakerI2S.h` / `.cpp`, `arduino/examples/HelloKana/HelloKana.ino`
- Modify: `arduino/src/SanoTTS.h`, `arduino/src/SanoTTS.cpp`

**Interfaces:**
- Consumes: Task 2 の `SanoTTS`
- Produces: `class SanoTTSSpeaker`（`begin(uint32_t)` / `beginUtterance(size_t)` /
  `prerollPush(const int16_t*, size_t)` / `start()` / `write(const int16_t*, size_t)` /
  `stop()` / `prerollSamples() const`）、`SanoTTS::setSpeaker(SanoTTSSpeaker*)`、`SanoTTS::say(const char*)`

- [ ] **Step 1: 抽象インターフェースを書く**

`esp32/main/saan_audio.h` と **1:1 に対応させる**（実績のある設計をそのまま使う）。

```cpp
class SanoTTSSpeaker {
public:
    virtual ~SanoTTSSpeaker() {}
    virtual bool   begin(uint32_t sampleRate) = 0;                 // saan_audio_setup
    virtual size_t prerollSamples() const { return 8192; }          // SAAN_AUDIO_PREROLL_SAMPLES
    virtual bool   beginUtterance(size_t nSamples) = 0;             // saan_audio_begin_utterance
    virtual bool   prerollPush(const int16_t* pcm, size_t n) = 0;   // saan_audio_preroll_push
    virtual bool   start() = 0;                                     // saan_audio_start
    virtual bool   write(const int16_t* pcm, size_t n) = 0;         // saan_audio_write_f32
    virtual void   stop() = 0;                                      // saan_audio_stop
};
```

⚠️ **プリロールを省かない。** 初回 pull は定常の約 6 倍かかる（受容野 38 フレームの warmup）。
省くと必ずアンダーランする。

- [ ] **Step 2: M5Unified 実装を書く**

`esp32/boards/m5unified/main/saan_audio_m5.cpp` を移植する。**移す不変条件**:

- ⚠️ **`M5.Speaker.playRaw` はデータをコピーしない。** 渡したポインタを Speaker が読む間、
  バッファを生かしておく必要がある → **リングを 3 枚以上**持ち、ヒープから一度だけ取って解放しない
- `speaker_config_t.sample_rate` と `playRaw` の第 3 引数を**どちらも 22050** にする（リサンプル無し）
- ヘッダ全体を `#if __has_include(<M5Unified.h>)` で囲い、無い環境では**中身が消える**

- [ ] **Step 3: 汎用 I2S 実装を書く**

`esp32/main/saan_i2s.c` を移植する。GPIO は既定 BCLK=5 / WS=6 / DOUT=7 とし、
**コンストラクタで差し替えられる**ようにする。`driver/i2s_std.h` を使う。

- [ ] **Step 4: `say()` を足す**

```cpp
bool SanoTTS::say(const char* text) {
    if (!m_spk) { m_err = "setSpeaker() を呼んでいない"; return false; }
    // 1. ids を作る（synthesize と同じ経路）
    // 2. spk->beginUtterance(prerollSamples())
    // 3. プリロールが埋まるまで pull → prerollPush
    // 4. spk->start()
    // 5. 残りを pull → spk->write
    // 6. spk->stop()
}
```

⚠️ **`synthesize()` と ids を作る経路を共有する**（2 か所に書くと片方だけ判定がずれる）。

- [ ] **Step 5: 例を書く**

`arduino/examples/HelloKana/HelloKana.ino` は spec §D-b のプレビューそのまま。

- [ ] **Step 6: ビルド確認（M5 あり / 無し の 2 構成）**

```bash
cd /tmp/pio-kana
/tmp/pio-venv/bin/pio run                                 # M5Unified 無し → M5 実装が消える
printf '\nlib_deps = \n    symlink:///Users/s19447/Desktop/saanoTTS-jp/arduino\n    m5stack/M5Unified\n' >> platformio.ini
/tmp/pio-venv/bin/pio run                                 # M5Unified あり
```
Expected: どちらもリンクまで通る。**片方だけ通る状態でコミットしない。**

- [ ] **Step 7: コミット**

```bash
git add arduino/src arduino/examples
git commit -m "feat: スピーカー差し込み（M5Unified / 汎用 I2S）と say()"
```

---

### Task 4: 漢字経路

**Files:**
- Modify: `arduino/src/sanotts_config.h`（`SANOTTS_ENABLE_KANJI` を 1 に）、`arduino/src/SanoTTS.cpp`
- Create: `arduino/extras/partitions/sanotts_16mb.csv`, `sanotts_8mb.csv`, `sanotts_4mb.csv`,
  `arduino/examples/HelloKanji/HelloKanji.ino`

**Interfaces:**
- Consumes: Task 1 が生成した `core/saan_kanji.c` / `core/saan_dict.c`
- Produces: `SanoTTS::kanjiReady()` が辞書の可否を返す

- [ ] **Step 1: パーティション表を置く**

`esp32/partitions_16mb.csv` / `partitions_8mb_kanji.csv` / `partitions_4mb_kanji.csv` を
`arduino/extras/partitions/` に**逐語コピー**する。⚠️ `extras/` はビルド対象外なので
Arduino が誤ってコンパイルすることはない。

- [ ] **Step 2: `toIds()` の辞書経路を埋める**

`esp32/main/main.c` の `speak_kanji()` を移植する。**移す不変条件**:

- `saan_kanji_to_ids(&g_dict, text, nbytes, g_arena, SANOTTS_ARENA_BYTES, ids, cap, &n_ids, &n_tok)`
- ⚠️ **Viterbi は合成用の arena を借りる。** 別に確保しない
- `_Static_assert` 相当の配列 typedef で `SANOTTS_ARENA_BYTES >= SAAN_KANJI_WORKBYTES` を検査する
- `begin()` で `saan_kanji_init()` と `saan_dict_open(&g_dict)` を呼び、
  **失敗しても `m_ready` は立てたまま `m_kanjiReady = false`**（D-063 = かな専用で続く）

- [ ] **Step 3: 例を書く**

```cpp
#include <SanoTTS.h>
#include <SanoTTSSpeakerM5.h>
SanoTTS tts; SanoTTSSpeakerM5 spk;
void setup() {
  M5.begin();
  tts.setSpeaker(&spk);
  if (!tts.begin()) { Serial.println(tts.lastError()); return; }
  if (!tts.kanjiReady()) Serial.println("辞書が無いのでかな専用。dict パーティションを焼くこと");
  tts.say("今日は良い天気ですね。");
}
void loop() {}
```

- [ ] **Step 4: ビルド確認（漢字オン）**

```bash
cd /tmp/pio-kana && sed -i '' 's/-DSANOTTS_NO_VOICE=1/-DSANOTTS_NO_VOICE=1 -DSANOTTS_ENABLE_KANJI=1/' platformio.ini
/tmp/pio-venv/bin/pio run
```
Expected: リンクまで通る。⚠️ **app が 1.4 MB を超えるので `board_build.partitions` が要る**

- [ ] **Step 5: 非 S3 でも通ることを確認（W8A32 に自動で落ちるか）**

```bash
sed -i '' 's/board = esp32-s3-devkitc-1/board = esp32dev/' platformio.ini
/tmp/pio-venv/bin/pio run
```
Expected: 通る。⚠️ **`ee.*` がアセンブラに出ないこと**（`SANOTTS_ENABLE_PIE` が 0 になっている）

- [ ] **Step 6: コミット**

```bash
git add arduino/src arduino/extras arduino/examples
git commit -m "feat: 漢字経路（辞書が無ければかな専用で続く = D-063）"
```

---

### Task 5: .zip 生成（コード + 重み）

**Files:**
- Modify: `scripts/build_arduino_lib.py`（`--zip` / `--blob` / `--version`）

**Interfaces:**
- Consumes: `scripts/blob_to_header.py`（既存。`g_saan_model_blob` / `SAAN_MODEL_BLOB_BYTES` /
  `SAAN_MODEL_BLOB_SHA256` / `SAAN_MODEL_BLOB_DTYPE` を出す）
- Produces: `arduino/dist/sanoTTS-jp-arduino-<ver>.zip` と
  `arduino/dist/sanoTTS-jp-voice-tsukuyomi-v4-<ver>.zip`

- [ ] **Step 1: コード .zip を組む**

zip の中は `SanoTTS-jp/` 1 階層（Arduino IDE の .zip インストールの要件）。
入れるもの: `library.properties` `library.json` `src/**`（`core/` を含む）`examples/**`
`extras/**` `README.md` `README.en.md` `NOTICE.txt` `LICENSE`。

- [ ] **Step 2: 重み .zip を組む**

```
SanoTTS-jp-voice-tsukuyomi-v4/
  library.properties           name=SanoTTS-jp-voice-tsukuyomi-v4 / architectures=esp32
  library.json                 "license": "LicenseRef-sanoTTS-jp-Model-1.0"
  src/saanotts_jp_voice.h      小さい公開ヘッダ（extern 宣言 + バイト数 + SHA-256）
  src/saan_model_blob.h        scripts/blob_to_header.py の出力（約 3.3 MB）
  src/saanotts_jp_voice.c      saan_model_blob.h を **1 回だけ** include して配列を定義する
  LICENSE-MODEL.md / NOTICE.txt / MODEL_CARD.md
```

⚠️ **`saan_model_blob.h` を 2 か所から include しない**（配列の定義を持つのでリンクが落ちる）。

- [ ] **Step 3: 走らせて大きさを見る**

```bash
GH_TOKEN="$(gh auth token)" gh release download v1.0.0 -p saanotts-jp-v4-int8.bin -D /tmp/w
uv run --no-project --python 3.12 python scripts/build_arduino_lib.py \
    --zip arduino/dist --version 1.1.0 --blob /tmp/w/saanotts-jp-v4-int8.bin
ls -l arduino/dist/
```
Expected: 2 本できる。**大きさを控える**（M-137 に書く）

- [ ] **Step 4: .zip から PlatformIO でビルドできることを確認**

```bash
mkdir -p /tmp/pio-zip/src && cd /tmp/pio-zip
cp /Users/s19447/Desktop/saanoTTS-jp/arduino/examples/PcmCallback/PcmCallback.ino src/main.cpp
cat > platformio.ini <<'EOF'
[env:esp32s3]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
board_build.partitions = huge_app.csv
lib_deps =
    file:///Users/s19447/Desktop/saanoTTS-jp/arduino/dist/sanoTTS-jp-arduino-1.1.0.zip
    file:///Users/s19447/Desktop/saanoTTS-jp/arduino/dist/sanoTTS-jp-voice-tsukuyomi-v4-1.1.0.zip
EOF
/tmp/pio-venv/bin/pio run
```
Expected: 重み込みでリンクが通る。**app の大きさを控える**（M-137）

- [ ] **Step 5: コミット**

```bash
git add scripts/build_arduino_lib.py
git commit -m "feat: リリース .zip 2 本（コード MIT / 重み LicenseRef）を組む"
```

---

### Task 6: CI job とゲートの登録

**Files:**
- Modify: `.github/workflows/ci.yml`, `.github/workflows/README.md`,
  `scripts/check_ci_coverage.py`, `scripts/check_attribution.py`
- Create: `arduino/NOTICE.txt`

**Interfaces:**
- Consumes: Task 1〜5
- Produces: CI の `arduino` job

- [ ] **Step 1: `arduino/NOTICE.txt` を書く**

`NOTICE.md` の **(A) 帰属ブロックを一字一句コピー**し、さらに
**Open JTalk への改変**（前置き N 行 + 後置き 1 行）を明記する。

- [ ] **Step 2: `check_attribution.py` を 4 か所に拡張**

`COPY_HTML = "web/index.html"` の隣に `COPY_TXT = "arduino/NOTICE.txt"` を足し、
最後の `print(... 3 か所 ...)` を 4 に直す。**陽性対照を 1 件足す**
（`arduino/NOTICE.txt` の写しから 1 行消すと落ちる）。

- [ ] **Step 3: `check_ci_coverage.py` の `SCRIPT_GLOBS` に足す**

```python
SCRIPT_GLOBS = (..., "scripts/sanitize_reports.py",
                # ⚠️ `build_*` は test_/check_ に当たらない。足さないと
                #    「CI に無い」ことすら誰も気づかない（sanitize_reports.py と同じ穴）。
                "scripts/build_arduino_lib.py")
```

- [ ] **Step 4: `ci.yml` に `arduino` job を足す**

```yaml
  arduino:
    name: arduino（PlatformIO / arduino-cli でビルドが通るか）
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
      - name: G-AR1 生成物が csrc の逐語か（陽性対照 5 件）
        run: |
          uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --self-test
          uv run --no-project --python 3.12 python scripts/build_arduino_lib.py
          uv run --no-project --python 3.12 python scripts/build_arduino_lib.py --check
      - name: 重みを落とす（タグ固定）
        run: gh release download v1.0.0 -p saanotts-jp-v4-int8.bin -D /tmp/w
        env: { GH_TOKEN: "${{ github.token }}" }
      - name: .zip を組む
        run: uv run --no-project --python 3.12 python scripts/build_arduino_lib.py
             --zip arduino/dist --version ci --blob /tmp/w/saanotts-jp-v4-int8.bin
      - name: PlatformIO を入れる
        run: pipx install platformio
      - name: G-AR2 3 構成でビルド（かな / 漢字 / 非 S3）
        run: bash scripts/ci_arduino_build.sh
      - name: G-AR3 arduino-cli で .zip からビルド
        run: bash scripts/ci_arduino_cli_build.sh
```

⚠️ **`scripts/ci_arduino_build.sh` と `ci_arduino_cli_build.sh` も書く**
（Task 2〜4 で手で打った手順をそのままスクリプトにする。⚠️ **ドキュメントに書いた手順と
実際に回すものを別にしない**）。

- [ ] **Step 5: `.github/workflows/README.md` を 6 job → 7 job に直す**

- [ ] **Step 6: ゲートを全部回す**

```bash
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py
uv run --no-project --python 3.12 python scripts/check_ci_coverage.py --self-test
uv run --no-project --python 3.12 python scripts/check_attribution.py --self-test
uv run --no-project --python 3.12 python scripts/check_attribution.py
```
Expected: 全部 OK

- [ ] **Step 7: コミット**

```bash
git add .github scripts arduino/NOTICE.txt
git commit -m "ci: arduino job（G-AR1/2/3）と帰属ブロック 4 か所目"
```

---

### Task 7: ドキュメント

**Files:**
- Create: `arduino/README.md`, `arduino/README.en.md`
- Modify: `README.md`, `README.en.md`, `docs/decisions.md`, `docs/measurements.md`, `CLAUDE.md`

- [ ] **Step 1: `arduino/README.md` を書く**

最低限これを含める: `platformio.ini` の丸ごとコピー / Arduino IDE の .zip 手順 /
**辞書の焼き方（esptool の 1 行）** / パーティション表の選び方 /
⚠️ **漢字は PlatformIO 推奨**（Arduino IDE の `partitions.csv` は IDE 2.x で効かない報告がある）/
⚠️ **実機で鳴らしていない**こと。

- [ ] **Step 2: D-065 を `docs/decisions.md` に書く**

spec の §2 と §3 を要約する。⚠️ **番号は `check_doc_counters.py` が見る**。

- [ ] **Step 3: M-137 を `docs/measurements.md` に書く**

**Task 5 Step 3 / Step 4 で控えた実測値だけ**を書く（.zip の大きさ / app の大きさ /
PlatformIO と arduino-cli のバージョン）。⚠️ `recording-measurements` skill を通す。
⚠️ **推測値を書かない。** 速度と実機は「測っていない」と明記する。

- [ ] **Step 4: 索引を再生成して全ゲートを回す**

```bash
uv run --no-project --python 3.12 python scripts/build_measurements_index.py
uv run --no-project --python 3.12 python scripts/check_doc_counters.py
uv run --no-project --python 3.12 python scripts/check_doc_links.py
uv run --no-project --python 3.12 python scripts/check_doc_commands.py
uv run --no-project --python 3.12 python scripts/check_doc_claims.py
uv run --no-project --python 3.12 python scripts/build_measurements_index.py --check
```
Expected: 全部 OK

- [ ] **Step 5: コミット**

```bash
git add arduino/README.md arduino/README.en.md README.md README.en.md docs CLAUDE.md
git commit -m "docs: Arduino / PlatformIO の導線（D-065 / M-137）"
```

---

### Task 8: G-AR4 — QEMU で checksum が一致するか（手元のみ）

**Files:**
- Create: `scripts/check_arduino_qemu.sh`（手順を固定する。CI では回らない）

⚠️ **これが唯一「同じ音が出る」を証明するゲート。** 他は全部「ビルドが通る」しか言っていない。

- [ ] **Step 1: Arduino ビルドで checksum を出せるようにする**

`PcmCallback.ino` が最後に `tts.checksum()` を Serial に出す（Task 2 Step 5 で済み）。

- [ ] **Step 2: QEMU で走らせる**

```bash
cd /tmp/pio-zip && /tmp/pio-venv/bin/pio run
export PATH="$HOME/.espressif/tools/qemu-xtensa/esp_develop_9.0.0_20240606/qemu/bin:$PATH"
python -m esptool --chip esp32s3 merge_bin --fill-flash-size 8MB -o /tmp/ard.bin \
    0x0 .pio/build/esp32s3/bootloader.bin 0x8000 .pio/build/esp32s3/partitions.bin \
    0x10000 .pio/build/esp32s3/firmware.bin
qemu-system-xtensa -nographic -machine esp32s3 -m 8M -drive file=/tmp/ard.bin,if=mtd,format=raw
```

- [ ] **Step 3: 基準と突き合わせる**

Expected: **W8A8+PIE の基準 `0xa69a7ebbb5ccb05f`** と一致（かな 1 文・同じ ids）。
⚠️ **一致しなければマージしない。** 差が出たら `sanotts_config.h` のフラグが
ESP-IDF 版と食い違っている。

- [ ] **Step 4: 陽性対照**

`-DSANOTTS_ENABLE_PIE=0` で走らせ、**W8A32 の基準 `0xe4b645c30835d42d`** が出ること。
⚠️ 2 つとも一致して初めて「フラグが効いている」と言える。

- [ ] **Step 5: 結果を M-137 に追記してコミット**

```bash
git add scripts/check_arduino_qemu.sh docs/measurements.md
git commit -m "test: G-AR4 — Arduino ビルドの PCM が ESP-IDF ビルドと bit 一致（QEMU）"
```

---

## Self-Review

**1. Spec coverage**

| spec | タスク |
|---|---|
| §4.1 .zip 2 本 | Task 5 |
| §4.2 リポジトリの形 | Task 1〜4 |
| §4.3 既存ファイルの変更 | Task 6（CI / ゲート）/ Task 7（docs） |
| §5.1 生成器 | Task 1 |
| §5.2 設定ヘッダ | Task 2 Step 1 |
| §5.3 公開 API | Task 2（synthesize）/ Task 3（say / speaker） |
| §5.4 重み | Task 2 Step 4 + Task 5 Step 2 |
| §5.5 辞書 | Task 4 |
| §6 G-AR1〜G-AR5 | Task 1 / 6 / 8 |
| §7 リスク | Task 7 Step 1・3（README と M-137 に明記） |
| §8 受け入れ条件 | Task 6 Step 6 / Task 7 Step 4 / Task 8 Step 3 |

**2. Placeholder scan**

Task 3 Step 2・3 は「移植する」と書いて全コードを載せていない。移植元
（`saan_audio_m5.cpp` / `saan_i2s.c`）を**パス付きで名指しし、移す不変条件を列挙**してあるので
実行可能と判断する。Task 4 Step 2 も同じ形。

**3. Type consistency**

`PcmCallback` は Task 2 で `std::function<void(const int16_t*, size_t)>` として定義し、
Task 3・5 でも同じ。`SanoTTSSpeaker` の 6 メソッドは Task 3 Step 1 で定義し Step 4 で使う。
`g_arena` / `SANOTTS_ARENA_BYTES` は Task 2 で定義し Task 4 で再利用する。
