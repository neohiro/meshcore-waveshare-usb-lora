#include <FreeRTOS.h>
#include <task.h>

#include <string.h>

#include "init.h"
#include "pinout.h"
#include "kiss.h"

static void error_blink()
{
    taskDISABLE_INTERRUPTS();

    gpio_set(LED_RXD_PORT, LED_RXD_PIN);
    gpio_set(LED_TXD_PORT, LED_TXD_PIN);

    for (;;) {
        for (size_t i = 0; i < 1500000; i++) {
            __asm__("nop");
        }

        gpio_toggle(LED_RXD_PORT, LED_RXD_PIN);
        gpio_toggle(LED_TXD_PORT, LED_TXD_PIN);
    }
}

void vApplicationStackOverflowHook(TaskHandle_t xTask, char* pcTaskName)
{
    (void)xTask;
    (void)pcTaskName;

    error_blink();
}

void vAssertCalled(const char *file, int line) {
    (void)file;
    (void)line;

    error_blink();
}

static serial_handler_t serial_handler = {
    .frame_received = kiss_frame_received,
};

static void lora_packet_received(int8_t rssi_dbm, int8_t snr_quarter_db, const uint8_t* payload, size_t payload_size)
{
    kiss_packet_received(rssi_dbm, snr_quarter_db, payload, payload_size);
}

static void lora_packet_transmitted(uint32_t time_on_air)
{
    kiss_packet_transmitted(time_on_air);
}

static radio_handler_t radio_handler = {
    .packet_received = lora_packet_received,
    .packet_transmitted = lora_packet_transmitted,
};

/**
 * Main entry point.
 */
int main(void)
{
    global_init(&serial_handler, &radio_handler);

    kiss_init();

    vTaskStartScheduler();

    for(;;) {
        __asm__("nop");
    }

    return 0;
}
