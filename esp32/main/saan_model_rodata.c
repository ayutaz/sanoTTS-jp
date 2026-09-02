/* saan_model.h の実装 2 — 重み blob を app の `.rodata`（flash）から読む。
 *
 * いつ使うか: **PSRAM を有効にした板**。M5Stack CoreS3 では CONFIG_SPIRAM=y にすると
 * 8 MB の PSRAM が flash の data mmap 用 vaddr を占有し、`esp_partition_mmap` が
 * ESP_ERR_NO_MEM で落ちた（第三者の実機報告。docs/research/s1-m5-cores3-speed.md §0。
 * ⚠️ 私は未再現）。ヘッダに埋めれば app の DROM として起動時にマップされるので踏まない。
 *
 * 代償: app が blob ぶん（643,936 B）大きくなり、**モデルだけの差し替えができない**
 * （app ごと再ビルド・再フラッシュ）。DevKit の既定はパーティション mmap（saan_model.c）のまま。
 *
 * 選び方: CMake の `-DSAAN_MODEL_RODATA=1`（esp32/main/CMakeLists.txt が SRCS を切り替え、
 * scripts/blob_to_header.py で ${SAAN_MODEL_BLOB} → saan_model_blob.h を生成する）。
 *
 * ⚠️ **`saan_model_blob.h` を include するのはこの翻訳単位だけ。** 配列の定義を持つので、
 *    2 箇所から include するとリンク時に重複定義で落ちる（黙って flash が 2 倍にはならない）。
 * ⚠️ **`const` を外さないこと。** const → .rodata → flash（SRAM 消費 0）。
 *    非 const → .data → DIRAM 643,936 B で即リンク不能。
 *
 * 出所: 方式は nnn112358/SanoTTS-jp-M5StackCoreS3（MIT）の main/saan_model.c と同じ。 */
#include "saan_model.h"

#include <inttypes.h>
#include <stdint.h>

#include "esp_log.h"

#include "saan_model_blob.h"

static const char *TAG = "saan_model";

bool saan_model_open(saan_weights *w) {
    ESP_LOGI(TAG, "重み: ヘッダ埋め込み (.rodata = flash) %u B / dtype %s",
             (unsigned)SAAN_MODEL_BLOB_BYTES, SAAN_MODEL_BLOB_DTYPE);
    ESP_LOGI(TAG, "  sha256 %s", SAAN_MODEL_BLOB_SHA256);

    const void *ptr = (const void *)g_saan_model_blob;

    /* ⚠️ **ここで落とす。** 非アラインのまま走らせると、落ちるのはずっと後の
     * conv の中（LoadStoreAlignmentCause）で原因が分からなくなる。
     * 生成ヘッダが `__attribute__((aligned(16)))` を付けているので必ず通るが、
     * **属性を消したときに気づけるようにチェックは残す**。 */
    if (((uintptr_t)ptr & 15u) != 0u) {
        ESP_LOGE(TAG, "blob が 16 バイト境界に無い (ptr=%p)。"
                      "scripts/blob_to_header.py の aligned(16) が消えている", ptr);
        return false;
    }

    saan_status s = saan_weights_open(w, ptr, SAAN_MODEL_BLOB_BYTES);
    if (s != SAAN_OK) {
        ESP_LOGE(TAG, "saan_weights_open: %s (ヘッダの中身が blob でない)", saan_strerror(s));
        return false;
    }
    ESP_LOGI(TAG, "重み OK: %" PRIu32 " tensors / base %p", w->n_tensors, (const void *)w->base);
    return true;
}

void saan_model_close(void) { /* .rodata なので何もしない */ }
