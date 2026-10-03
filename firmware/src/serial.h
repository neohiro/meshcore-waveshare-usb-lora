#ifndef SERIAL_H__
#define SERIAL_H__

#include "kiss_frame.h"

#include <stdint.h>
#include <stddef.h>

#define SERIAL_BAUD         115200
#define SERIAL_DATA_BITS    8
#define SERIAL_RX_IRQ_PRIORITY 0xC0 // Must >= max SysCall priority (0xB0 in FreeRTOSConfig.h)

// Large enough for one maximal KISS frame plus worst-case escaping.
#define SERIAL_TXQ_SIZE     1024
#define SERIAL_RXQ_SIZE     512

typedef struct {
    void (*frame_received)(uint8_t type, const uint8_t* payload, size_t payload_size);
} serial_handler_t;

void serial_init(serial_handler_t* handler);

// Sends one complete KISS frame. The frame is written atomically with respect
// to other tasks, so frames never interleave on the wire.
void serial_send_message(uint8_t type, const uint8_t* payload, size_t payload_size);

void serial_putc(const uint8_t ch);

#endif // SERIAL_H__
