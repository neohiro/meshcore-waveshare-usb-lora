#ifndef FREERTOS_STUB_H
#define FREERTOS_STUB_H

/*
 * Just enough of FreeRTOS for src/kiss.c to link natively. The modem's command
 * handler only uses a mutex around its shared scratch buffer, and the host is
 * single threaded so the "mutex" is a counter that must never drop below zero.
 */

#include <stdint.h>
#include <stddef.h>

#define portMAX_DELAY ((uint32_t)0xFFFFFFFFUL)

typedef int BaseType_t;
typedef unsigned int TickType_t;
typedef void* TaskHandle_t;
typedef void* SemaphoreHandle_t;

#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define pdFAIL 0
#define portMAX_PRIORITIES 5

SemaphoreHandle_t xSemaphoreCreateMutex(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, uint32_t ticks);
BaseType_t xSemaphoreGive(SemaphoreHandle_t mutex);

// Exposed so a test can assert the modem takes and releases its mutex.
int freertos_stub_mutex_depth(void);
int freertos_stub_mutex_take_count(void);
void freertos_stub_reset(void);

#endif // FREERTOS_STUB_H
