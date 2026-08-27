#include "saan_model.h"

#include <inttypes.h>
#include <stdint.h>

#include "esp_log.h"
#include "esp_partition.h"

static const char *TAG = "saan_model";

/* partitions.csv の `model` 行と一致させること */
#define SAAN_MODEL_PART_LABEL   "model"
#define SAAN_MODEL_PART_SUBTYPE 0x40

static esp_partition_mmap_handle_t s_handle;
static bool s_mapped;

bool saan_model_open(saan_weights *w) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, SAAN_MODEL_PART_SUBTYPE, SAAN_MODEL_PART_LABEL);
    if (part == NULL) {
        ESP_LOGE(TAG, "パーティション '%s' が無い。partitions.csv を焼いたか？",
                 SAAN_MODEL_PART_LABEL);
        return false;
    }
    ESP_LOGI(TAG, "model パーティション: offset 0x%08" PRIx32 " size %" PRIu32 " B",
             (uint32_t)part->address, (uint32_t)part->size);

    const void *ptr = NULL;
    esp_err_t err = esp_partition_mmap(part, 0, part->size,
                                       ESP_PARTITION_MMAP_DATA, &ptr, &s_handle);
    if (err != ESP_OK || ptr == NULL) {
        ESP_LOGE(TAG, "esp_partition_mmap 失敗: %s", esp_err_to_name(err));
        return false;
    }
    s_mapped = true;

    /* ⚠️ **ここで落とす。** 非アラインのまま走らせると、落ちるのは
     * ずっと後の conv の中（LoadStoreAlignmentCause）で原因が分からなくなる。
     * partitions.csv の model offset を 64 KB 境界に置いていれば必ず通る。 */
    if (((uintptr_t)ptr & 15u) != 0u) {
        ESP_LOGE(TAG, "blob が 16 バイト境界に無い (ptr=%p)。"
                      "partitions.csv の model offset を 0x10000 の倍数にすること",
                 ptr);
        return false;
    }

    saan_status s = saan_weights_open(w, ptr, part->size);
    if (s != SAAN_OK) {
        ESP_LOGE(TAG, "saan_weights_open: %s "
                      "(blob を焼いていないか、別のファイルを焼いた)",
                 saan_strerror(s));
        return false;
    }
    ESP_LOGI(TAG, "重み OK: %" PRIu32 " tensors / base %p", w->n_tensors, (const void *)w->base);
    return true;
}

void saan_model_close(void) {
    if (s_mapped) {
        esp_partition_munmap(s_handle);
        s_mapped = false;
    }
}
