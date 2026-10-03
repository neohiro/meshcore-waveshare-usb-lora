#include "kiss_frame.h"

void kiss_parser_reset(kiss_parser_t* p)
{
    if (p == NULL)
        return;

    p->type = 0;
    p->len = 0;
    p->have_type = false;
    p->escaping = false;
    p->discard = false;
}

bool kiss_parser_feed(kiss_parser_t* p, uint8_t ch)
{
    if (p == NULL)
        return false;

    if (p->escaping) {
        p->escaping = false;

        if (ch == KISS_TFEND) {
            ch = KISS_FEND;
        } else if (ch == KISS_TFESC) {
            ch = KISS_FESC;
        } else {
            // Invalid escape sequence: abandon the rest of this frame.
            p->discard = true;
            p->len = 0;
            return false;
        }

        // A byte that arrived escaped is payload. It must not be re-examined
        // against the FEND delimiter, which is exactly the bug this guards.
        if (p->discard || !p->have_type || p->len >= KISS_MAX_FRAME_SIZE) {
            p->discard = true;
            p->len = 0;
        } else {
            p->data[p->len++] = ch;
        }

        return false;
    }

    if (ch == KISS_FESC) {
        p->escaping = true;
        return false;
    }

    if (ch == KISS_FEND) {
        // End of frame, or an empty frame used as padding.
        //
        // On success type, data and len are deliberately left intact so the
        // caller can read the frame straight after this returns true. They are
        // reset when the next frame's type byte arrives, or by
        // kiss_parser_reset.
        const bool complete = p->have_type && !p->discard;

        p->have_type = false;
        p->escaping = false;
        p->discard = false;

        return complete;
    }

    if (!p->have_type) {
        p->type = ch;
        p->have_type = true;
        p->len = 0;
        return false;
    }

    if (p->discard) {
        return false;
    }

    if (p->len >= KISS_MAX_FRAME_SIZE) {
        // Oversized: keep scanning for the terminating FEND, but never report
        // the frame.
        p->discard = true;
        p->len = 0;
        return false;
    }

    p->data[p->len++] = ch;
    return false;
}

size_t kiss_frame_max_encoded(size_t len)
{
    // FEND + type + payload (each byte may double) + FEND
    return 2 + 1 + (len * 2) + 1;
}

size_t kiss_frame_encode(uint8_t type, const uint8_t* data, size_t len,
                         uint8_t* out, size_t out_cap)
{
    if (out == NULL || len > KISS_MAX_FRAME_SIZE)
        return 0;

    if (out_cap < kiss_frame_max_encoded(len))
        return 0;

    size_t i = 0;

    out[i++] = KISS_FEND;
    out[i++] = type;

    for (size_t n = 0; n < len; n++) {
        const uint8_t b = data[n];

        if (b == KISS_FEND) {
            out[i++] = KISS_FESC;
            out[i++] = KISS_TFEND;
        } else if (b == KISS_FESC) {
            out[i++] = KISS_FESC;
            out[i++] = KISS_TFESC;
        } else {
            out[i++] = b;
        }
    }

    out[i++] = KISS_FEND;

    return i;
}
