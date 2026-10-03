#ifndef SERIAL_STUB_H
#define SERIAL_STUB_H

/*
 * Captures the frames the modem emits instead of driving the USART, so tests
 * can assert on exactly what a host would receive.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define STUB_MAX_FRAMES 16
#define STUB_MAX_PAYLOAD 512

void serial_stub_reset(void);
int serial_stub_count(void);

// Pops the oldest captured frame. Returns false when there are none left.
bool serial_stub_next(uint8_t* type, uint8_t* payload, size_t* len);

// Copies the payload of the next frame without consuming it.
bool serial_stub_peek(uint8_t* type, const uint8_t** payload, size_t* len);

// Consumes everything currently captured, returning how many were dropped.
int serial_stub_discard(void);

// True if a captured frame of the given type is present.
bool serial_stub_has_type(uint8_t type);

/**
 * Send frames to a sink as KISS bytes instead of collecting them. Used by the
 * kiss-server mode so the real modem code can be driven over a pipe.
 * The sink receives a complete, correctly framed byte stream per frame.
 */
typedef void (*serial_stub_sink_fn)(const unsigned char* bytes, size_t len,
                                    void* ctx);

void serial_stub_set_sink(serial_stub_sink_fn fn, void* ctx);

#endif // SERIAL_STUB_H
