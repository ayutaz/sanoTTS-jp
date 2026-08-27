#ifndef SAAN_STUB_FREERTOS_TASK_H
#define SAAN_STUB_FREERTOS_TASK_H
#include "freertos/FreeRTOS.h"
typedef void (*TaskFunction_t)(void *);
/* ⚠️ その場で同期実行する。vTaskDelete(NULL) は単に return させたいので
 *    longjmp は使わず、呼び出し側（tts_task）が return するのに任せる */
int  xTaskCreate(TaskFunction_t fn, const char *name, uint32_t stack,
                 void *arg, unsigned prio, TaskHandle_t *out);
void vTaskDelete(TaskHandle_t t);
void vTaskDelay(TickType_t ticks);
unsigned uxTaskGetStackHighWaterMark(TaskHandle_t t);
#endif
