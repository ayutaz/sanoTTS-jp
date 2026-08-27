/* ホスト stub — ESP_LOGx を stderr に出すだけ。**デバイスには載らない。** */
#ifndef SAAN_STUB_ESP_LOG_H
#define SAAN_STUB_ESP_LOG_H
#include <stdio.h>
#define ESP_LOGI(tag, fmt, ...) fprintf(stderr, "I (%s) " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) fprintf(stderr, "W (%s) " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGE(tag, fmt, ...) fprintf(stderr, "E (%s) " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) ((void)0)
#endif
