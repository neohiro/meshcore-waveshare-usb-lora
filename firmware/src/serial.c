#include "serial.h"
#include "pinout.h"
#include "kiss_frame.h"

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/stm32/usart.h>
#include <libopencm3/cm3/nvic.h>

#include <FreeRTOS.h>
#include <task.h>
#include <queue.h>
#include <semphr.h>

static QueueHandle_t uart_txq = NULL;
static QueueHandle_t uart_rxq = NULL;

// Held for the duration of a frame so that frames emitted by the radio task and
// by the KISS command handler never interleave on the wire.
static SemaphoreHandle_t frame_mutex = NULL;

// Scratch space for encoding a frame. Protected by frame_mutex, which the
// sender holds for the whole call, so one buffer is enough.
static uint8_t tx_frame[KISS_TX_BUFFER_SIZE];

// The reassembly parser holds a KISS_MAX_FRAME_SIZE buffer, which is far too
// large to sit on a task stack: this task is given 256 words, and the parser
// alone measured 536 bytes of that, more than half, before any callee frame.
// Only this task ever touches the parser, and it never outlives the loop, so it
// is file scope rather than a local. Moving it here leaves the task's stack to
// the code that actually recurses.
static kiss_parser_t rx_parser;

static serial_handler_t* handler = NULL;

static void serial_tx_task(void* args __attribute__((unused)))
{
    uint8_t ch;

    for (;;) {
        if (xQueueReceive(uart_txq, &ch, 500) == pdPASS) {
            // Wait until ready
            while (!usart_get_flag(USART1, USART_SR_TXE))
                taskYIELD();

            usart_send(USART1, ch);
        }
    }
}

void usart1_isr(void)
{
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    uint8_t data;

    if (usart_get_flag(USART1, USART_FLAG_RXNE) != 0) {
        data = usart_recv(USART1);

        xQueueSendFromISR(uart_rxq, &data, &xHigherPriorityTaskWoken);
    }

    portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

static void serial_rx_task(void* args __attribute__((unused)))
{
    for (;;) {
        uint8_t ch;

        if (xQueueReceive(uart_rxq, &ch, 500) != pdPASS) {
            // Idle for too long: drop any half-assembled frame rather than
            // treating a fresh burst as a continuation.
            kiss_parser_reset(&rx_parser);
            continue;
        }

        if (kiss_parser_feed(&rx_parser, ch) &&
            handler != NULL && handler->frame_received != NULL)
        {
            handler->frame_received(rx_parser.type, rx_parser.data, (size_t)rx_parser.len);
        }
    }
}

void serial_init(serial_handler_t* h)
{
    rcc_periph_clock_enable(RCC_USART1);
    gpio_set_mode(GPIOA, GPIO_MODE_OUTPUT_50_MHZ, GPIO_CNF_OUTPUT_ALTFN_PUSHPULL, GPIO_USART1_TX);
    gpio_set_mode(GPIOA, GPIO_MODE_INPUT, GPIO_CNF_INPUT_FLOAT, GPIO_USART1_RX);
    usart_set_baudrate(USART1, SERIAL_BAUD);
    usart_set_databits(USART1, SERIAL_DATA_BITS);
    usart_set_stopbits(USART1, USART_STOPBITS_1);
    usart_set_mode(USART1, USART_MODE_TX_RX);
    usart_set_parity(USART1, USART_PARITY_NONE);
    usart_set_flow_control(USART1, USART_FLOWCONTROL_NONE);

    // Enable UART RX interrupt
    usart_enable_rx_interrupt(USART1);
    nvic_set_priority(NVIC_USART1_IRQ, SERIAL_RX_IRQ_PRIORITY);
    nvic_enable_irq(NVIC_USART1_IRQ);

    usart_enable(USART1);

    uart_txq = xQueueCreate(SERIAL_TXQ_SIZE, sizeof(uint8_t));
    uart_rxq = xQueueCreate(SERIAL_RXQ_SIZE, sizeof(uint8_t));
    frame_mutex = xSemaphoreCreateMutex();

    xTaskCreate(serial_tx_task, "UART_TX", 100, NULL, configMAX_PRIORITIES - 1, NULL);
    xTaskCreate(serial_rx_task, "UART_RX", 256, NULL, configMAX_PRIORITIES - 1, NULL);

    handler = h;
}

void serial_putc(const uint8_t ch)
{
    xQueueSend(uart_txq, &ch, portMAX_DELAY);
}

void serial_send_message(uint8_t type, const uint8_t* payload, size_t payload_size)
{
    size_t n;

    if (payload_size > KISS_MAX_PAYLOAD)
        return;

    if (frame_mutex != NULL) {
        xSemaphoreTake(frame_mutex, portMAX_DELAY);
    }

    n = kiss_frame_encode(type, payload, payload_size, tx_frame, sizeof(tx_frame));

    for (size_t i = 0; i < n; i++) {
        xQueueSend(uart_txq, &tx_frame[i], portMAX_DELAY);
    }

    if (frame_mutex != NULL) {
        xSemaphoreGive(frame_mutex);
    }
}
