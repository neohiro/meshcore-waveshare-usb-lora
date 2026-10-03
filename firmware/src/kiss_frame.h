#ifndef KISS_FRAME_H__
#define KISS_FRAME_H__

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * KISS framing (KA9Q / K3MC), as used by MeshCore's own KISS modem:
 *
 *   FEND <type> <escaped payload...> FEND
 *
 * 0xC0 (FEND) delimits a frame, and 0xDB (FESC) escapes a literal 0xC0 or
 * 0xDB inside the payload. Both delimiters are therefore always escaped in
 * payload data.
 *
 * Kept free of any RTOS or hardware dependency so it can be exercised by the
 * host tests in tests/ without a target board.
 */

#define KISS_FEND  0xC0
#define KISS_FESC  0xDB
#define KISS_TFEND 0xDC
#define KISS_TFESC 0xDD

// Largest payload the modem ever sends, set by the KISS specification: the
// length is a single byte on the wire.
#define KISS_MAX_PAYLOAD 255

// Largest unescaped payload accepted on receive.
//
// This deliberately equals KISS_MAX_PAYLOAD. It used to be 512, which meant
// the reassembler would happily rebuild a 512-byte frame that every consumer
// -- kiss.c and serial_send_message() -- then rejected for exceeding
// KISS_MAX_PAYLOAD: work done to throw the frame away, and two limits that
// disagreed. Accepting exactly what can be sent costs half the buffer, which
// matters because this buffer is the firmware's largest single allocation.
#define KISS_MAX_FRAME_SIZE 256

// Worst case on the wire for that payload: FEND + type + escaped payload + FEND.
// A compile-time constant so it can size a buffer.
#define KISS_TX_BUFFER_SIZE (2 + 1 + (KISS_MAX_PAYLOAD * 2) + 1)

typedef struct {
    uint8_t type;
    uint16_t len;
    uint8_t data[KISS_MAX_FRAME_SIZE];

    bool have_type;
    bool escaping;
    bool discard;
} kiss_parser_t;

// Discards any partial frame and returns to the "waiting for FEND" state.
// A parser must be passed through this once before its first feed.
void kiss_parser_reset(kiss_parser_t* p);

/*
 * Consumes one received byte. Returns true when a complete frame has just been
 * parsed, in which case p->type, p->data and p->len describe it.
 *
 * The frame stays readable until the next frame starts or the parser is reset,
 * so the caller must consume it before feeding any further bytes. A false
 * return means either "nothing yet" or "that frame was malformed and was
 * dropped"; the two are not distinguished because a receiver has no reason to
 * act on either.
 */
bool kiss_parser_feed(kiss_parser_t* p, uint8_t ch);

// Worst-case number of bytes kiss_frame_encode can emit for a payload of len.
size_t kiss_frame_max_encoded(size_t len);

/*
 * Encodes one frame into out. Returns the number of bytes written, or 0 if the
 * payload is too large or out_cap is too small. A return of 0 still encodes a
 * legal frame, meaning "type byte with no payload" (4 bytes on the wire).
 */
size_t kiss_frame_encode(uint8_t type, const uint8_t* data, size_t len,
                         uint8_t* out, size_t out_cap);

#endif // KISS_FRAME_H__
