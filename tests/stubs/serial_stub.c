#include "serial.h"
#include "serial_stub.h"
#include "kiss_frame.h"

#include <stdio.h>
#include <string.h>

typedef struct {
    uint8_t type;
    size_t len;
    uint8_t payload[STUB_MAX_PAYLOAD];
} stub_frame_t;

static stub_frame_t frames[STUB_MAX_FRAMES];
static int frame_count = 0;

static serial_stub_sink_fn sink_fn = NULL;
static void* sink_ctx = NULL;

void serial_stub_set_sink(serial_stub_sink_fn fn, void* ctx)
{
    sink_fn = fn;
    sink_ctx = ctx;
}

void serial_stub_reset(void)
{
    memset(frames, 0, sizeof(frames));
    frame_count = 0;
}

int serial_stub_count(void)
{
    return frame_count;
}

void serial_send_message(uint8_t type, const uint8_t* payload, size_t payload_size)
{
    if (sink_fn != NULL) {
        // Emit real KISS bytes on the wire, escaping included.
        unsigned char out[KISS_TX_BUFFER_SIZE];
        const size_t n = kiss_frame_encode(type, payload, payload_size,
                                           out, sizeof(out));

        if (n > 0) {
            sink_fn(out, n, sink_ctx);
        }
        return;
    }

    if (frame_count >= STUB_MAX_FRAMES) {
        return;
    }

    if (payload_size > STUB_MAX_PAYLOAD) {
        return;
    }

    stub_frame_t* f = &frames[frame_count++];
    f->type = type;
    f->len = payload_size;

    if (payload != NULL && payload_size > 0) {
        memcpy(f->payload, payload, payload_size);
    }
}

bool serial_stub_next(uint8_t* type, uint8_t* payload, size_t* len)
{
    if (frame_count == 0) {
        return false;
    }

    stub_frame_t* f = &frames[0];

    if (type != NULL) {
        *type = f->type;
    }
    if (payload != NULL && f->len > 0) {
        memcpy(payload, f->payload, f->len);
    }
    if (len != NULL) {
        *len = f->len;
    }

    for (int i = 1; i < frame_count; i++) {
        frames[i - 1] = frames[i];
    }

    frame_count--;
    return true;
}

bool serial_stub_peek(uint8_t* type, const uint8_t** payload, size_t* len)
{
    if (frame_count == 0) {
        return false;
    }

    if (type != NULL) {
        *type = frames[0].type;
    }
    if (payload != NULL) {
        *payload = frames[0].payload;
    }
    if (len != NULL) {
        *len = frames[0].len;
    }

    return true;
}

int serial_stub_discard(void)
{
    const int dropped = frame_count;
    frame_count = 0;
    return dropped;
}

bool serial_stub_has_type(uint8_t type)
{
    for (int i = 0; i < frame_count; i++) {
        if (frames[i].type == type) {
            return true;
        }
    }

    return false;
}
