/* ホスト stub — ESP-IDF の esp_err.h の最小代替。**デバイスには載らない。** */
#ifndef SAAN_STUB_ESP_ERR_H
#define SAAN_STUB_ESP_ERR_H
typedef int esp_err_t;
#define ESP_OK   0
#define ESP_FAIL -1
static inline const char *esp_err_to_name(esp_err_t e) { return e == ESP_OK ? "ESP_OK" : "ESP_FAIL"; }
#endif
