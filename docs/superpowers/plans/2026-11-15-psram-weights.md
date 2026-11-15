# 重みを PSRAM に写す実験 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M5 CoreS3 で重み blob（654,032 B）を起動時に PSRAM へ写して使い、残タスク #21（253 ids で定常 xRT の中央値 0.522）が縮むかを実機で測って、採るか捨てるかを決める。

**Architecture:** `esp32/main/saan_model_rodata.c` の `saan_model_open` だけを変える。`-DSAAN_MODEL_PSRAM=1` のときは `.rodata`（flash）の blob を 16 B 境界の PSRAM に写し、全バイトを比べてから `saan_weights_open` に**写しの方**を渡す。コアは触らない（重みの読み方は base ポインタが変わるだけ）。**既定は 0 で、0 のときは 1 命令も変わらない。**

**Tech Stack:** ESP-IDF v5.5（`heap_caps_aligned_alloc`）/ M5 CoreS3（PSRAM 8 MB Quad）

**Spec:** [`docs/superpowers/specs/2026-11-15-nonkiai-followups-design.md`](../specs/2026-11-15-nonkiai-followups-design.md)

**Branch:** `exp/psram-weights`（`main` から）

## Global Constraints

- **既定（`SAAN_MODEL_PSRAM` 未定義 or 0）のビルドは今と同じ**
- **PCM は bit 一致すること**（53 ids `0x390bf4b2aef8f2ec` / 303 ids `0xf1668c69d55406bf` = M-147 の実機の値）。一致しなければ速度に関係なく不採用
- **速度の判断は実機の表だけ**（C-055）。前の版と**同じ板・同じ入力・同じ日**に並べる
- 入力は M-147 と同じく**錨の 1 行 `きょ][おわよ][いて][んきです°ね` を k 回つないだもの**（k = 1 / 5 / 6 → 53 / 253 / 303 ids）。各 3 回ずつ打つ
- **採用の条件（事前に固定）**: 253 ids と 303 ids の定常 xRT の中央値が 3 回とも ≤ 0.500、アンダーランが全長で 0、PCM が bit 一致、起動の増分 ≤ 200 ms。1 つでも満たさなければ**否定の結果として記録して閉じる**（それも前進として扱う）
- 対象は `.rodata` 版（M5 系）だけ。DevKit のパーティション版（`saan_model.c`）は今回触らない
- PSRAM の無い板（AtomS3 など）でこのフラグを立てたら**ビルドで止める**

## Review Focus

1. **PSRAM の無い構成でフラグを立てる**→ 実行時に黙って flash に戻るのではなく、ビルドが `#error` で止まる → Task 1 の陽性対照
2. **PSRAM の確保に失敗する**（他が先に食った）→ 合成を始めずに理由を出して止まる → Task 1 の実装（`return false`）と起動ログ
3. **写しが壊れている**（キャッシュの問題で書いた値が読めない）→ `memcmp` で検出して止まる → Task 1 の実装
4. **辞書の mmap と PSRAM のキャッシュの取り合いで漢字 G2P が遅くなる**→ 漢字 G2P の ms も並べる → Task 2 の表
5. **静的 DIRAM が増える**→ 増えないこと（写しはヒープ）を `idf.py size` で確かめる → Task 2 の表

---

### Task 1: フラグと写し

**Files:**
- Modify: `esp32/main/saan_model_rodata.c`
- Modify: `esp32/main/CMakeLists.txt`（`SAAN_SKIP_I2S` と同じ形で `if(SAAN_MODEL_PSRAM)` → `target_compile_definitions(${COMPONENT_LIB} PRIVATE SAAN_MODEL_PSRAM=1)`）

**Interfaces:**
- Produces: `bool saan_model_open(saan_weights *w)`（シグネチャは変えない）。`SAAN_MODEL_PSRAM=1` のときの起動ログ:
  `重みを PSRAM に写した: %u B / %.2f ms / base %p / external_ram=%d`（`external_ram` は `esp_ptr_external_ram(base)`。⚠️ ESP32-S3 は flash の `.rodata` も PSRAM も同じ `0x3C000000` 台の仮想番地に貼られるので、**番地の見た目では区別できない**。1 であることを人が見る）

- [ ] **Step 1: 陽性対照を先に走らせる（ビルドが止まるべき構成）**

  PSRAM を持たない DevKit 構成で、フラグを立ててビルドする:
  Run: `cd esp32 && idf.py -B build_psram_neg -DSDKCONFIG=build_psram_neg/sdkconfig -DSAAN_MODEL_RODATA=1 -DSAAN_MODEL_PSRAM=1 build`
  Expected（実装前）: **ビルドが通ってしまう**（フラグが無視される）。これが RED。

- [ ] **Step 2: 実装する**

  - `#if SAAN_MODEL_PSRAM` の中で `#include "esp_heap_caps.h"` と `#include "sdkconfig.h"`
  - `#if !CONFIG_SPIRAM` → `#error "SAAN_MODEL_PSRAM=1 には PSRAM（CONFIG_SPIRAM=y）が要る"`
  - `heap_caps_aligned_alloc(16, SAAN_MODEL_BLOB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)`。NULL なら `ESP_LOGE` して `return false`
  - `memcpy` → `memcmp`。不一致なら `ESP_LOGE` して `return false`
  - 写しの 16 B 境界の検査は今の検査をそのまま写しのポインタに当てる
  - `esp_timer_get_time()` で写しにかかった時間を測ってログに出す
  - `saan_model_close` は写しを `heap_caps_free` する

- [ ] **Step 3: 陽性対照が止まり、本番の構成が通ることを確かめる**

  Run: Step 1 と同じコマンド
  Expected: `#error` のメッセージでビルドが失敗する。
  Run: `cd esp32/boards/m5unified && idf.py -B build_m5k_psram -DSDKCONFIG=build_m5k_psram/sdkconfig -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.cores3" -DSAAN_KANJI=1 -DSAAN_MODEL_PSRAM=1 -DSAAN_DICT_BLOB=$PWD/../../../csrc/k1_dict.bin build`
  Expected: 通る。
  Run: `bash scripts/check_esp32_template.sh`
  Expected: 12 節すべて OK（既定の構成が変わっていない）。

- [ ] **Step 4: 既定のビルドが変わっていないことを確かめる**

  フラグ無しの M5 漢字構成を、このブランチと `main` の両方でビルドし、`saan_model_rodata.c.obj` を `xtensa-esp32s3-elf-objdump -d` で比べる。
  Expected: 差が 0 行。

- [ ] **Step 5: Commit**

  ```bash
  git add esp32/main/saan_model_rodata.c esp32/main/CMakeLists.txt
  git commit -m "exp(esp32): -DSAAN_MODEL_PSRAM=1 で重みを PSRAM に写す（PSRAM 無しはビルドで止める・既定は不変）"
  ```

---

### Task 2: 実機で並べて決める

**Files:**
- Create: `reports/m153_psram/`（実機ログ。`reports/m147_device/` と同じく追跡する）
- Modify: `docs/measurements.md`（M-153）/ `docs/decisions.md`（D-072 = 採る / 捨てる）
- Modify: `CLAUDE.md`（残タスク #21 の行）

- [ ] **Step 1: 前の版を測る（人: CoreS3 に `main` のファームを焼く）**

  シリアルに k = 1 / 5 / 6 の行を 3 回ずつ打ち、ログを `reports/m153_psram/before.log` に保存する。

- [ ] **Step 2: このブランチを測る（人: Task 1 Step 3 のファームを焼く）**

  同じ行を同じ回数打ち、`reports/m153_psram/after.log` に保存する。起動ログの「重みを PSRAM に写した」の行も残す。

- [ ] **Step 3: 表にする**

  両方のログから、入力ごとに「定常 xRT（中央値 / 平均）」「アンダーラン」「出力 PCM の checksum」「init ms」「漢字 G2P ms（起動セルフテストの漢字行があれば）」を抜き、`idf.py size` の静的 DIRAM と起動の所要時間を足して 1 枚の表にする。**ログの行をそのまま引く**（`scripts/check_doc_claims.py` の G-D1 が引用元のログに数値が在るか見る）。

- [ ] **Step 4: 事前に固定した条件で決めて記録する**

  採用の条件（Global Constraints）を満たせば D-072 に「採る」と書き、M5 構成の既定にするかは別に決める（既定を変えるなら firmware の再リリースになる）。満たさなければ D-072 に「捨てる」と書き、**なぜ効かなかったか**を `-DSAAN_PROFILE=1` の STEP / HEAD / DEC の行で 1 つだけ確かめて添える（深追いしない）。

- [ ] **Step 5: ゲートを回して PR を作る**

  Run: `uv run --no-project python scripts/check_doc_claims.py && uv run python scripts/check_doc_counters.py && uv run python scripts/check_doc_links.py && uv run --no-project python scripts/build_measurements_index.py --check`
  Expected: すべて OK。

  ```bash
  git add reports/m153_psram docs/measurements.md docs/decisions.md CLAUDE.md
  git commit -m "docs: 重みを PSRAM に写して #21 を実機で測った（M-153 / D-072）"
  git push -u origin exp/psram-weights
  gh pr create --base main --title "exp: 重みを PSRAM に写すと定常 xRT は縮むか（M-153 / D-072）"
  ```
