#include "saan_dict.h"

#include <inttypes.h>
#include <stdint.h>

#include "esp_log.h"
#include "esp_partition.h"

static const char *TAG = "saan_dict";

/* partitions_16mb.csv の `dict` 行と一致させること */
#define SAAN_DICT_PART_LABEL   "dict"
#define SAAN_DICT_PART_SUBTYPE 0x41

static esp_partition_mmap_handle_t s_handle;
static bool s_mapped;

bool saan_dict_open(k1_dict_t *d) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, SAAN_DICT_PART_SUBTYPE, SAAN_DICT_PART_LABEL);
    if (part == NULL) {
        ESP_LOGE(TAG, "パーティション '%s' が無い。**16 MB 版の表を焼いたか？** "
                      "（esp32/partitions_16mb.csv）", SAAN_DICT_PART_LABEL);
        return false;
    }
    ESP_LOGI(TAG, "dict パーティション: offset 0x%08" PRIx32 " size %" PRIu32 " B",
             (uint32_t)part->address, (uint32_t)part->size);

    const void *ptr = NULL;
    esp_err_t err = esp_partition_mmap(part, 0, part->size,
                                       ESP_PARTITION_MMAP_DATA, &ptr, &s_handle);
    if (err != ESP_OK || ptr == NULL) {
        /* ⚠️ **ここは MMU の窓が足りないと落ちる。** S3 の vaddr は 32 MB を
         *    flash と PSRAM で分け合う。model 768 KB + dict 13.5 MB を同時に
         *    貼るので、PSRAM を大きく使う構成では足りなくなる（K-0 / D-042）。 */
        ESP_LOGE(TAG, "esp_partition_mmap 失敗: %s"
                      "（MMU の窓が足りない可能性。model と dict を同時に貼っている）",
                 esp_err_to_name(err));
        return false;
    }
    s_mapped = true;

    if (((uintptr_t)ptr & 15u) != 0u) {
        ESP_LOGE(TAG, "辞書が 16 バイト境界に無い (ptr=%p)", ptr);
        return false;
    }
    int r = k1_open(d, (const uint8_t *)ptr, part->size);
    if (r != 0) {
        ESP_LOGE(TAG, "k1_open: %d（辞書 blob を焼いていないか、別物を焼いた）", r);
        return false;
    }
    ESP_LOGI(TAG, "辞書 OK: 見出し語 %" PRIu32 " / エントリ %" PRIu32
                  " / 行列 %ux%u",
             d->n_surfaces, d->n_entries, (unsigned)d->lsize, (unsigned)d->rsize);
    return true;
}

void saan_dict_close(void) {
    if (s_mapped) {
        esp_partition_munmap(s_handle);
        s_mapped = false;
    }
}
