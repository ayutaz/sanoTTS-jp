/* ホスト stub の実装。**デバイスには載らない。**
 * esp32/main の .c を実機なしでコンパイル・実行するためだけのもの。 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_partition.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"

/* --- heap ---------------------------------------------------------------- */

void *heap_caps_malloc(size_t n, unsigned caps) { (void)caps; return malloc(n); }
void  heap_caps_free(void *p) { free(p); }

/* --- partition ---------------------------------------------------------- */

static const void *s_blob;
static uint32_t s_blob_size;
static esp_partition_t s_part;

void saan_stub_set_blob(const void *ptr, uint32_t size) {
    s_blob = ptr; s_blob_size = size;
}

const esp_partition_t *esp_partition_find_first(esp_partition_type_t type, int subtype,
                                                const char *label) {
    if (type != ESP_PARTITION_TYPE_DATA || subtype != 0x40 || strcmp(label, "model") != 0)
        return NULL;
    if (!s_blob) return NULL;
    s_part.type = type; s_part.subtype = subtype;
    s_part.address = 0x210000;            /* partitions.csv と同じ値にしておく */
    s_part.size = s_blob_size;
    snprintf(s_part.label, sizeof s_part.label, "%s", label);
    return &s_part;
}

esp_err_t esp_partition_mmap(const esp_partition_t *part, uint32_t offset, uint32_t size,
                             esp_partition_mmap_memory_t memory, const void **out_ptr,
                             esp_partition_mmap_handle_t *out_handle) {
    (void)part; (void)size; (void)memory;
    if (!s_blob) return ESP_FAIL;
    *out_ptr = (const uint8_t *)s_blob + offset;
    *out_handle = 1;
    return ESP_OK;
}

void esp_partition_munmap(esp_partition_mmap_handle_t h) { (void)h; }

/* --- FreeRTOS ----------------------------------------------------------- */

int xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                void *arg, unsigned prio, TaskHandle_t *out) {
    (void)name; (void)stack; (void)prio;
    if (out) *out = NULL;
    fn(arg);          /* ⚠️ その場で同期実行する */
    return pdPASS;
}
void vTaskDelete(TaskHandle_t t) { (void)t; }
void vTaskDelay(TickType_t t) { (void)t; }
unsigned uxTaskGetStackHighWaterMark(TaskHandle_t t) { (void)t; return 0; }

/* --- I2S ---------------------------------------------------------------- */

static int16_t *s_pcm;
static size_t s_pcm_n, s_pcm_cap;
static int s_enabled;

esp_err_t i2s_new_channel(const i2s_chan_config_t *cfg, i2s_chan_handle_t *tx,
                          i2s_chan_handle_t *rx) {
    (void)cfg; (void)rx;
    *tx = (i2s_chan_handle_t)0x1;
    return ESP_OK;
}
esp_err_t i2s_channel_init_std_mode(i2s_chan_handle_t h, const i2s_std_config_t *cfg) {
    (void)h; (void)cfg; return ESP_OK;
}
esp_err_t i2s_channel_enable(i2s_chan_handle_t h) { (void)h; s_enabled = 1; return ESP_OK; }
esp_err_t i2s_channel_disable(i2s_chan_handle_t h) { (void)h; s_enabled = 0; return ESP_OK; }

esp_err_t i2s_channel_write(i2s_chan_handle_t h, const void *src, size_t size,
                            size_t *written, TickType_t timeout) {
    (void)h; (void)timeout;
    if (!s_enabled) {
        fprintf(stderr, "E (stub) enable する前に i2s_channel_write が呼ばれた\n");
        return ESP_FAIL;
    }
    size_t n = size / sizeof(int16_t);
    if (s_pcm_n + n > s_pcm_cap) {
        s_pcm_cap = (s_pcm_n + n) * 2 + 4096;
        s_pcm = (int16_t *)realloc(s_pcm, s_pcm_cap * sizeof(int16_t));
        if (!s_pcm) return ESP_FAIL;
    }
    memcpy(s_pcm + s_pcm_n, src, size);
    s_pcm_n += n;
    if (written) *written = size;
    return ESP_OK;
}

const int16_t *saan_stub_i2s_pcm(size_t *n_samples) {
    if (n_samples) *n_samples = s_pcm_n;
    return s_pcm;
}
