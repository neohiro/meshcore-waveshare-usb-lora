#include "test_kiss_frame.h"
#include "test_util.h"

#include "kiss_frame.h"

#include <stdint.h>

static uint8_t type_out;
static size_t len_out;
static const uint8_t* data_out;

/*
 * Feeds a byte stream through the parser and captures the first completed
 * frame. Returns true if a frame was produced.
 */
static bool feed_all(kiss_parser_t* p, const uint8_t* buf, size_t n, size_t* consumed)
{
    kiss_parser_reset(p);
    data_out = NULL;
    len_out = 0;

    for (size_t i = 0; i < n; i++) {
        if (kiss_parser_feed(p, buf[i])) {
            type_out = p->type;
            len_out = p->len;
            data_out = p->data;
            if (consumed != NULL) {
                *consumed = i + 1;
            }
            return true;
        }
    }

    return false;
}

static void test_encode_basic(void)
{
    SUITE("encode/basic");

    uint8_t out[32];

    // A frame with no payload is FEND, type, FEND.
    const size_t n = kiss_frame_encode(0x06, NULL, 0, out, sizeof(out));
    const uint8_t expect[] = { 0xC0, 0x06, 0xC0 };
    CHECK_EQ_INT(n, 3);
    CHECK_EQ_MEM(out, expect, 3);

    const uint8_t payload[] = { 0x11 };
    const size_t n2 = kiss_frame_encode(0x06, payload, 1, out, sizeof(out));
    const uint8_t expect2[] = { 0xC0, 0x06, 0x11, 0xC0 };
    CHECK_EQ_INT(n2, 4);
    CHECK_EQ_MEM(out, expect2, 4);
}

static void test_encode_escaping(void)
{
    SUITE("encode/escaping");

    uint8_t out[32];

    // 0xC0 and 0xDB must be escaped, other bytes pass through untouched.
    const uint8_t payload[] = { 0xC0, 0xDB, 0x00, 0xDB, 0xC0 };
    const uint8_t expect[] = {
        0xC0, 0x06,
        0xDB, 0xDC,   // escaped FEND
        0xDB, 0xDD,   // escaped FESC
        0x00,
        0xDB, 0xDD,
        0xDB, 0xDC,
        0xC0
    };
    const size_t n = kiss_frame_encode(0x06, payload, sizeof(payload), out, sizeof(out));
    CHECK_EQ_INT(n, sizeof(expect));
    CHECK_EQ_MEM(out, expect, sizeof(expect));
}

static void test_encode_bounds(void)
{
    SUITE("encode/bounds");

    uint8_t out[8];

    // Too small an output buffer must be refused rather than overrun.
    CHECK_EQ_INT(kiss_frame_encode(0x00, (const uint8_t*)"\x01\x02\x03\x04", 4, out, 4), 0);
    CHECK_EQ_INT(kiss_frame_encode(0x00, NULL, 0, out, 2), 0);
    CHECK_EQ_INT(kiss_frame_encode(0x00, (const uint8_t*)"\x01", 1, NULL, 16), 0);

    // A payload beyond the receive limit is refused outright.
    static uint8_t big[KISS_MAX_FRAME_SIZE + 1];
    CHECK_EQ_INT(kiss_frame_encode(0x00, big, sizeof(big), out, sizeof(out)), 0);

    // The advertised worst case really is enough for a maximal payload.
    static uint8_t maxout[KISS_MAX_FRAME_SIZE * 2 + 4];
    static uint8_t filler[KISS_MAX_FRAME_SIZE];
    for (size_t i = 0; i < sizeof(filler); i++) {
        filler[i] = (uint8_t)i; // wraps through both delimiters
    }
    const size_t worst = kiss_frame_encode(0x00, filler, sizeof(filler),
                                           maxout, kiss_frame_max_encoded(sizeof(filler)));
    // Only a handful of bytes in this pattern need escaping, so the result is
    // shorter than the bound; what matters is that the bound is sufficient.
    CHECK(worst > 0);
    CHECK(worst <= kiss_frame_max_encoded(sizeof(filler)));
    CHECK(kiss_frame_max_encoded(sizeof(filler)) <= sizeof(maxout));
}

static void test_parse_basic(void)
{
    SUITE("parse/basic");

    kiss_parser_t p;
    const uint8_t frame[] = { 0xC0, 0x06, 0x11, 0x22, 0xC0 };

    CHECK(feed_all(&p, frame, sizeof(frame), NULL));
    CHECK_EQ_INT(type_out, 0x06);
    CHECK_EQ_INT(len_out, 2);
    const uint8_t want[] = { 0x11, 0x22 };
    CHECK_EQ_MEM(data_out, want, 2);

    // A type with an empty payload is still a frame.
    const uint8_t empty[] = { 0xC0, 0x06, 0xC0 };
    CHECK(feed_all(&p, empty, sizeof(empty), NULL));
    CHECK_EQ_INT(type_out, 0x06);
    CHECK_EQ_INT(len_out, 0);
}

static void test_parse_escaped_delimiter_is_payload(void)
{
    SUITE("parse/escaped-delimiter");

    kiss_parser_t p;

    // This is the case a naive parser gets wrong: after unescaping 0xDB 0xDC
    // the resulting 0xC0 is payload, not the end of the frame.
    const uint8_t frame[] = {
        0xC0, 0x00,
        0xDB, 0xDC,   // payload 0xC0
        0x11,
        0xDB, 0xDD,   // payload 0xDB
        0xC0
    };

    CHECK(feed_all(&p, frame, sizeof(frame), NULL));
    CHECK_EQ_INT(type_out, 0x00);
    CHECK_EQ_INT(len_out, 3);
    const uint8_t want[] = { 0xC0, 0x11, 0xDB };
    CHECK_EQ_MEM(data_out, want, 3);
}

static void test_parse_padding_and_back_to_back(void)
{
    SUITE("parse/padding");

    kiss_parser_t p;

    // A doubled FEND is padding, and two frames may arrive in one read.
    const uint8_t two[] = { 0xC0, 0xC0, 0x00, 0xAA, 0xC0, 0x06, 0x11, 0xC0 };

    CHECK(kiss_parser_feed(&p, two[0]) == false);
    CHECK(kiss_parser_feed(&p, two[1]) == false);
    CHECK(kiss_parser_feed(&p, two[2]) == false);
    CHECK(kiss_parser_feed(&p, two[3]) == false);
    CHECK(kiss_parser_feed(&p, two[4]) == true);
    CHECK_EQ_INT(p.type, 0x00);
    CHECK_EQ_INT(p.len, 1);
    const uint8_t want_aa[] = { 0xAA };
    CHECK_EQ_MEM(p.data, want_aa, 1);

    CHECK(kiss_parser_feed(&p, two[5]) == false);
    CHECK(kiss_parser_feed(&p, two[6]) == false);
    CHECK(kiss_parser_feed(&p, two[7]) == true);
    CHECK_EQ_INT(p.type, 0x06);
    CHECK_EQ_INT(p.len, 1);
    const uint8_t want_11[] = { 0x11 };
    CHECK_EQ_MEM(p.data, want_11, 1);
}

static void test_parse_malformed(void)
{
    SUITE("parse/malformed");

    kiss_parser_t p;

    // Invalid escape: the frame must be dropped, not delivered truncated.
    const uint8_t bad_escape[] = { 0xC0, 0x06, 0x11, 0xDB, 0x99, 0x22, 0xC0 };
    CHECK(feed_all(&p, bad_escape, sizeof(bad_escape), NULL) == false);

// It must recover for the next frame rather than staying poisoned.
    const uint8_t good[] = { 0xC0, 0x06, 0x33, 0xC0 };
    const uint8_t want_33a[] = { 0x33 };

    CHECK(feed_all(&p, good, sizeof(good), NULL));
    CHECK_EQ_INT(type_out, 0x06);
    CHECK_EQ_MEM(data_out, want_33a, 1);

    // An oversized payload is dropped but the parser resynchronises.
    static uint8_t oversized[KISS_MAX_FRAME_SIZE + 32];
    oversized[0] = 0xC0;
    oversized[1] = 0x06;
    for (size_t i = 0; i < KISS_MAX_FRAME_SIZE + 20; i++) {
        oversized[2 + i] = 0x41;
    }
    oversized[2 + KISS_MAX_FRAME_SIZE + 20] = 0xC0;
    CHECK(feed_all(&p, oversized, KISS_MAX_FRAME_SIZE + 23, NULL) == false);

    const uint8_t want_33b[] = { 0x33 };

    CHECK(feed_all(&p, good, sizeof(good), NULL));
    CHECK_EQ_INT(type_out, 0x06);
    CHECK_EQ_MEM(data_out, want_33b, 1);
}

static void test_parse_reset(void)
{
    SUITE("parse/reset");

    kiss_parser_t p;

    // The parser must be initialised before use; feeding an uninitialised one
    // would test whatever the stack happened to hold.
    kiss_parser_reset(&p);

    const uint8_t partial[] = { 0xC0, 0x06, 0x11, 0x22 };

    // Half a frame, then reset: the stale type must not be reported.
    for (size_t i = 0; i < sizeof(partial); i++) {
        CHECK(kiss_parser_feed(&p, partial[i]) == false);
    }

    kiss_parser_reset(&p);

    const uint8_t next[] = { 0xC0, 0x06, 0xC0 };
    CHECK(feed_all(&p, next, sizeof(next), NULL));
    CHECK_EQ_INT(type_out, 0x06);
    CHECK_EQ_INT(len_out, 0);
}

static void test_completed_frame_survives_until_next(void)
{
    SUITE("parse/completed-readable");

    kiss_parser_t p;

    kiss_parser_reset(&p);

    const uint8_t frame[] = { 0xC0, 0x06, 0xAA, 0xBB, 0xC0 };
    for (size_t i = 0; i < sizeof(frame) - 1; i++) {
        CHECK(kiss_parser_feed(&p, frame[i]) == false);
    }

    CHECK(kiss_parser_feed(&p, frame[sizeof(frame) - 1]) == true);

    // The payload must still be readable on return. Clearing it before
    // reporting completion would make every received frame look empty, and the
    // caller has no other chance to copy it.
    CHECK_EQ_INT(p.type, 0x06);
    CHECK_EQ_INT(p.len, 2);

    const uint8_t want[] = { 0xAA, 0xBB };
    CHECK_EQ_MEM(p.data, want, 2);
}

static uint32_t lcg = 12345;

static uint32_t lcg_next(void)
{
    lcg = (lcg * 1103515245u) + 12345u;
    return (lcg >> 8) & 0xFFFFFF;
}

static void test_roundtrip_fuzz(void)
{
    SUITE("roundtrip/fuzz");

    // Encoding then decoding must be an identity for arbitrary payloads,
    // including ones dense in the delimiter and escape bytes.
    static uint8_t payload[KISS_MAX_PAYLOAD];
    uint8_t frame[KISS_TX_BUFFER_SIZE];

    for (int iter = 0; iter < 20000; iter++) {
        // Bounded by KISS_MAX_PAYLOAD so the worst-case escaped frame still
        // fits the buffer, which is what the modem actually emits.
        const size_t len = (size_t)(lcg_next() % (KISS_MAX_PAYLOAD + 1));

        for (size_t i = 0; i < len; i++) {
            const uint32_t r = lcg_next() % 10;
            payload[i] = (r < 3) ? 0xC0 : (r < 6) ? 0xDB : (uint8_t)lcg_next();
        }

        const uint8_t type = (uint8_t)(lcg_next() & 0x0F);
        const size_t wire = kiss_frame_encode(type, payload, len, frame, sizeof(frame));

        CHECK(wire > 0);
        CHECK(wire <= kiss_frame_max_encoded(len));

        // Decode byte by byte, the worst case for a streaming parser.
        kiss_parser_t p;
        kiss_parser_reset(&p);
        bool got = false;

        for (size_t i = 0; i < wire && !got; i++) {
            got = kiss_parser_feed(&p, frame[i]);
        }

        if (!got) {
            tu_checks++;
            tu_failures++;
            printf("  FAIL [%s] fuzz iter %d: no frame decoded (len=%zu)\n",
                   tu_suite, iter, len);
            continue;
        }

        tu_checks++;

        if (p.type != type || p.len != len || (len && memcmp(p.data, payload, len) != 0)) {
            tu_failures++;
            printf("  FAIL [%s] fuzz iter %d: mismatch len=%zu got_len=%u\n",
                   tu_suite, iter, len, (unsigned)p.len);
        }
    }
}

int test_kiss_frame(void)
{
    RUN(test_encode_basic);
    RUN(test_encode_escaping);
    RUN(test_encode_bounds);
    RUN(test_parse_basic);
    RUN(test_parse_escaped_delimiter_is_payload);
    RUN(test_parse_padding_and_back_to_back);
    RUN(test_parse_malformed);
    RUN(test_parse_reset);
    RUN(test_completed_frame_survives_until_next);
    RUN(test_roundtrip_fuzz);

    return tu_report("kiss_frame");
}
