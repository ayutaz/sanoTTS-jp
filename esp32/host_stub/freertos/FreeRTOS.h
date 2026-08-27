/* ホスト stub — FreeRTOS の最小代替。**デバイスには載らない。**
 * ⚠️ タスクは作らず**その場で同期実行する**。だからホストでは
 *    スケジューリング・優先度・スタック溢れは一切検証できない。 */
#ifndef SAAN_STUB_FREERTOS_H
#define SAAN_STUB_FREERTOS_H
#include <stddef.h>
#include <stdint.h>
typedef uint32_t TickType_t;
typedef size_t StackType_t;
typedef void *TaskHandle_t;
#define portMAX_DELAY ((TickType_t)0xffffffffU)
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#define pdPASS 1
#endif
