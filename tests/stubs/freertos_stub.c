#include "FreeRTOS.h"

static int mutex_depth = 0;
static int mutex_takes = 0;

// A single opaque handle is enough: there is only ever one mutex.
static int the_mutex = 0;

SemaphoreHandle_t xSemaphoreCreateMutex(void)
{
    return &the_mutex;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t mutex, uint32_t ticks)
{
    (void)mutex;
    (void)ticks;

    mutex_depth++;
    mutex_takes++;

    return pdTRUE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t mutex)
{
    (void)mutex;

    // A give without a take would mean the modem's shared buffer is not
    // actually protected, so the stub makes that visible rather than harmless.
    if (mutex_depth > 0) {
        mutex_depth--;
    }

    return pdTRUE;
}

int freertos_stub_mutex_depth(void)
{
    return mutex_depth;
}

int freertos_stub_mutex_take_count(void)
{
    return mutex_takes;
}

void freertos_stub_reset(void)
{
    mutex_depth = 0;
    mutex_takes = 0;
}
