/* ホスト stub — `model` パーティションの代わりに blob ファイルを読む。
 * **デバイスには載らない。**
 *
 * 実機の esp_partition_mmap は 64 KB 境界に丸めてマップするので、返却ポインタは
 * 少なくとも 64 KB アライン。stub でもそれを真似て 64 KB 境界に置く
 * （main.c のアライン assert を素通りさせない = 本物と同じ条件で検査する）。 */
#ifndef SAAN_STUB_ESP_PARTITION_H
#define SAAN_STUB_ESP_PARTITION_H

#include <stdint.h>
#include "esp_err.h"

typedef enum { ESP_PARTITION_TYPE_APP = 0, ESP_PARTITION_TYPE_DATA = 1 } esp_partition_type_t;
typedef enum { ESP_PARTITION_MMAP_DATA = 1, ESP_PARTITION_MMAP_INST = 0 } esp_partition_mmap_memory_t;
typedef uint32_t esp_partition_mmap_handle_t;

typedef struct {
    esp_partition_type_t type;
    int subtype;
    uint32_t address;
    uint32_t size;
    char label[17];
} esp_partition_t;

/* host_main.c が blob を読み込んでからこれを呼ぶ */
void saan_stub_set_blob(const void *ptr, uint32_t size);

const esp_partition_t *esp_partition_find_first(esp_partition_type_t type, int subtype,
                                                const char *label);
esp_err_t esp_partition_mmap(const esp_partition_t *part, uint32_t offset, uint32_t size,
                             esp_partition_mmap_memory_t memory, const void **out_ptr,
                             esp_partition_mmap_handle_t *out_handle);
void esp_partition_munmap(esp_partition_mmap_handle_t handle);

#endif
