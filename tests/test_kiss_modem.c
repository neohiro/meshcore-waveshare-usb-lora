#include "test_kiss_modem.h"
#include "test_util.h"

#include "FreeRTOS.h"
#include "serial_stub.h"
#include "radio_stub.h"

#include "kiss.h"
#include "kiss_frame.h"
#include "radio.h"
#include "version.h"

#include <string.h>

// Hardware sub-commands, mirrored here so the test does not simply restate the
// modem's own header. If a value drifts in kiss.h these would stop matching,
// which is exactly the sort of mistake worth catching.
#define REQ_GET_RANDOM 0x02
#define REQ_SET_RADIO 0x09
#define REQ_SET_TX_POWER 0x0A
#define REQ_GET_RADIO 0x0B
#define REQ_GET_TX_POWER 0x0C
#define REQ_GET_CURRENT_RSSI 0x0D
#define REQ_IS_CHANNEL_BUSY 0x0E
#define REQ_GET_AIRTIME 0x0F
#define REQ_GET_NOISE_FLOOR 0x10
#define REQ_GET_VERSION 0x11
#define REQ_GET_STATS 0x12
#define REQ_GET_BATTERY 0x13
#define REQ_GET_DEVICE_NAME 0x16
#define REQ_PING 0x17
#define REQ_REBOOT 0x18
#define REQ_SET_SIGNAL_REPORT 0x19
#define REQ_GET_SIGNAL_REPORT 0x1A
#define REQ_ENCRYPT_DATA 0x05

#define RSP_OK 0xF0
#define RSP_ERROR 0xF1
#define RSP_TX_DONE 0xF8
#define RSP_RX_META 0xF9

#define ERR_INVALID_LENGTH 0x01
#define ERR_INVALID_PARAM 0x02
#define ERR_NO_CALLBACK 0x03

#define CMD_DATA 0x00
#define CMD_TXDELAY 0x01
#define CMD_PERSISTENCE 0x02
#define CMD_SLOTTIME 0x03
#define CMD_FULLDUPLEX 0x05
#define CMD_SETHARDWARE 0x06
#define CMD_RETURN 0xFF

static uint8_t rx_buf[STUB_MAX_PAYLOAD];

// Little-endian field writers, so the tests do not hand-compute byte arrays.
// Spelling 62500 as {0xA4,0xF4} instead of {0x24,0xF4} is exactly the kind of
// mistake that makes a test blame correct firmware.
static size_t put_u32(uint8_t* p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)((v >> 16) & 0xFF);
    p[3] = (uint8_t)((v >> 24) & 0xFF);
    return 4;
}

static size_t build_set_radio(uint8_t* out, uint32_t freq, uint32_t bw,
                              uint8_t sf, uint8_t cr)
{
    size_t n = put_u32(out, freq);
    n += put_u32(out + n, bw);
    out[n++] = sf;
    out[n++] = cr;
    return n;
}

// Sends a hardware request and pops the single reply the modem owes it.
//
// A SetHardware frame carries the sub-command as the first payload byte; 0x06
// is the KISS type byte that selects SetHardware, not part of the payload.
static bool hardware(uint8_t subcmd, const uint8_t* payload, size_t len,
                     uint8_t* type_out, uint8_t* body, size_t* body_len)
{
    uint8_t frame[STUB_MAX_PAYLOAD];

    frame[0] = subcmd;
    if (payload != NULL && len > 0) {
        memcpy(&frame[1], payload, len);
    }

    serial_stub_reset();
    kiss_frame_received(CMD_SETHARDWARE, frame, len + 1);

    if (!serial_stub_next(type_out, rx_buf, body_len)) {
        return false;
    }

    if (body != NULL) {
        memcpy(body, rx_buf, *body_len);
    }

    return true;
}

static void expect_ok(const char* what, uint8_t subcmd, const uint8_t* payload, size_t len)
{
    uint8_t type;
    size_t body_len;

    if (!hardware(subcmd, payload, len, &type, rx_buf, &body_len) || body_len < 1) {
        tu_checks++;
        tu_failures++;
        printf("  FAIL [%s] %s: no reply frame\n", tu_suite, what);
        return;
    }

    tu_checks++;

    if (rx_buf[0] == RSP_ERROR) {
        tu_failures++;
        printf("  FAIL [%s] %s: modem returned error %u\n", tu_suite, what,
               (unsigned)(body_len > 1 ? rx_buf[1] : 0));
    } else if (rx_buf[0] != RSP_OK) {
        tu_failures++;
        printf("  FAIL [%s] %s: expected OK(0x%02X), got 0x%02X\n",
               tu_suite, what, RSP_OK, rx_buf[0]);
    }
}

static void expect_error(const char* what, uint8_t subcmd, const uint8_t* payload,
                         size_t len, uint8_t code)
{
    uint8_t type;
    size_t body_len;

    if (!hardware(subcmd, payload, len, &type, rx_buf, &body_len) || body_len < 2) {
        tu_checks++;
        tu_failures++;
        printf("  FAIL [%s] %s: expected error %u, got no reply\n",
               tu_suite, what, (unsigned)code);
        return;
    }

    CHECK_EQ_INT(rx_buf[0], RSP_ERROR);
    CHECK_EQ_INT(rx_buf[1], code);
}

static void reset_all(void)
{
    serial_stub_reset();
    radio_stub_reset();
    freertos_stub_reset();
    kiss_init();
}

// -- framing level -----------------------------------------------------------

static void test_set_radio_accepts_and_applies(void)
{
    SUITE("set-radio/valid");
    reset_all();

    uint8_t req[10];
    const size_t n = build_set_radio(req, 869618000, 62500, 8, 8);

    expect_ok("set-radio", REQ_SET_RADIO, req, n);
    CHECK_EQ_INT(radio_stub_frequency(), 869618000);

    radio_lora_params_t p;
    radio_get_lora_params(&p);
    CHECK_EQ_INT(p.spreading_factor, 8);
    CHECK_EQ_INT(p.coding_rate, 4); // the wire's 8 maps to the SX126x enumerant 4
}

static void test_set_radio_rejects_bad_parameters(void)
{
    SUITE("set-radio/validation");
    reset_all();

    uint8_t req[10];
    size_t n = build_set_radio(req, 869618000, 62500, 8, 8);

    // Too short to contain the fields.
    expect_error("set-radio/short", REQ_SET_RADIO, req, 6, ERR_INVALID_LENGTH);

    // 100 kHz is outside the SX1262's range.
    n = build_set_radio(req, 100000, 62500, 8, 8);
    expect_error("set-radio/freq-low", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    n = build_set_radio(req, 2000000000, 62500, 8, 8);
    expect_error("set-radio/freq-high", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    // SF outside 5..12.
    n = build_set_radio(req, 869618000, 62500, 4, 8);
    expect_error("set-radio/sf-low", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    n = build_set_radio(req, 869618000, 62500, 13, 8);
    expect_error("set-radio/sf-high", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    // Coding rate denominator outside 5..8.
    n = build_set_radio(req, 869618000, 62500, 8, 4);
    expect_error("set-radio/cr-low", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    n = build_set_radio(req, 869618000, 62500, 8, 9);
    expect_error("set-radio/cr-high", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    // Bandwidth the SX126x cannot produce.
    n = build_set_radio(req, 869618000, 12345, 8, 8);
    expect_error("set-radio/bw", REQ_SET_RADIO, req, n, ERR_INVALID_PARAM);

    // A rejected configuration must not have been applied.
    CHECK_EQ_INT(radio_stub_frequency(), 869618000);
    radio_lora_params_t p;
    radio_get_lora_params(&p);
    CHECK_EQ_INT(p.spreading_factor, 8);
}

static void test_get_radio_round_trips(void)
{
    SUITE("set-radio/roundtrip");
    reset_all();

    // 869.525 MHz / 125 kHz / SF11 / CR 4/7, deliberately different from the
    // boot defaults so a stale value cannot pass by accident.
    uint8_t req[10];
    const size_t n = build_set_radio(req, 869525000, 125000, 11, 7);

    expect_ok("set-radio", REQ_SET_RADIO, req, n);

    uint8_t type;
    size_t body_len;
    CHECK(hardware(REQ_GET_RADIO, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x8B); // RESP_RADIO
    CHECK_EQ_INT(body_len, 11);

    const uint32_t freq = (uint32_t)rx_buf[1] | ((uint32_t)rx_buf[2] << 8) |
                          ((uint32_t)rx_buf[3] << 16) | ((uint32_t)rx_buf[4] << 24);
    const uint32_t bw = (uint32_t)rx_buf[5] | ((uint32_t)rx_buf[6] << 8) |
                        ((uint32_t)rx_buf[7] << 16) | ((uint32_t)rx_buf[8] << 24);

    CHECK_EQ_INT(freq, 869525000);
    CHECK_EQ_INT(bw, 125000);
    CHECK_EQ_INT(rx_buf[9], 11);  // SF
    CHECK_EQ_INT(rx_buf[10], 7);  // CR denominator restored for the wire
}

static void test_tx_power(void)
{
    SUITE("tx-power");
    reset_all();

    const uint8_t seventeen[] = { 17 };
    expect_ok("set-tx-power/17", REQ_SET_TX_POWER, seventeen, 1);
    CHECK_EQ_INT(radio_stub_tx_power(), 17);

    const uint8_t negative[] = { (uint8_t)-5 };
    expect_ok("set-tx-power/-5", REQ_SET_TX_POWER, negative, 1);
    CHECK_EQ_INT(radio_stub_tx_power(), -5);

    const uint8_t too_high[] = { 30 };
    expect_error("set-tx-power/30", REQ_SET_TX_POWER, too_high, 1, ERR_INVALID_PARAM);

    const uint8_t too_low[] = { (uint8_t)-40 };
    expect_error("set-tx-power/-40", REQ_SET_TX_POWER, too_low, 1, ERR_INVALID_PARAM);

    expect_error("set-tx-power/empty", REQ_SET_TX_POWER, NULL, 0, ERR_INVALID_LENGTH);

    const uint8_t max[] = { 22 };
    expect_ok("set-tx-power/22", REQ_SET_TX_POWER, max, 1);
    CHECK_EQ_INT(radio_stub_tx_power(), 22);

    uint8_t type;
    size_t body_len;
    CHECK(hardware(REQ_GET_TX_POWER, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x8C);
    CHECK_EQ_INT(body_len, 2);
    CHECK_EQ_INT((int8_t)rx_buf[1], 22);
}

static void test_simple_queries(void)
{
    SUITE("queries");
    reset_all();

    uint8_t type;
    size_t body_len;

    CHECK(hardware(REQ_PING, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x97); // PONG
    CHECK_EQ_INT(body_len, 1);

    CHECK(hardware(REQ_GET_VERSION, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x91); // VERSION
    CHECK_EQ_INT(body_len, 3);
    CHECK_EQ_INT(rx_buf[1], FIRMWARE_VERSION);
    CHECK_EQ_INT(rx_buf[2], 0); // reserved

    CHECK(hardware(REQ_GET_DEVICE_NAME, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x96);
    CHECK_EQ_INT(body_len, 1 + strlen(KISS_DEVICE_NAME));
    CHECK_EQ_MEM(&rx_buf[1], KISS_DEVICE_NAME, strlen(KISS_DEVICE_NAME));

    CHECK(hardware(REQ_GET_STATS, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x92);
    CHECK_EQ_INT(body_len, 13);
    // rx, tx, errors little endian
    CHECK_EQ_INT((uint32_t)rx_buf[1] | ((uint32_t)rx_buf[2] << 8) |
                 ((uint32_t)rx_buf[3] << 16) | ((uint32_t)rx_buf[4] << 24), 7);
    CHECK_EQ_INT((uint32_t)rx_buf[5] | ((uint32_t)rx_buf[6] << 8) |
                 ((uint32_t)rx_buf[7] << 16) | ((uint32_t)rx_buf[8] << 24), 3);

    radio_stub_set_rssi(-95);
    CHECK(hardware(REQ_GET_CURRENT_RSSI, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x8D);
    CHECK_EQ_INT(body_len, 2);
    CHECK_EQ_INT((int8_t)rx_buf[1], -95);

    CHECK(hardware(REQ_GET_NOISE_FLOOR, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x90);
    CHECK_EQ_INT(body_len, 3);

    radio_stub_set_channel_busy(true);
    CHECK(hardware(REQ_IS_CHANNEL_BUSY, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x8E);
    CHECK_EQ_INT(rx_buf[1], 1);

    radio_stub_set_channel_busy(false);
    CHECK(hardware(REQ_IS_CHANNEL_BUSY, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[1], 0);

    radio_stub_set_airtime(1234);
    for (int n = 1; n <= 255; n += 254) {
        const uint8_t len[] = { (uint8_t)n };
        CHECK(hardware(REQ_GET_AIRTIME, len, 1, &type, rx_buf, &body_len));
        CHECK_EQ_INT(rx_buf[0], 0x8F);
        CHECK_EQ_INT(body_len, 5);
        CHECK_EQ_INT((uint32_t)rx_buf[1] | ((uint32_t)rx_buf[2] << 8) |
                     ((uint32_t)rx_buf[3] << 16) | ((uint32_t)rx_buf[4] << 24), 1234);
    }

    const uint8_t zero_len[] = { 0 };
    expect_error("get-airtime/0", REQ_GET_AIRTIME, zero_len, 1, ERR_INVALID_PARAM);
}

static void test_random(void)
{
    SUITE("random");
    reset_all();

    uint8_t type;
    size_t body_len;

    // The request is a single length byte, so a one-byte payload asks for that
    // many random bytes back.
    for (int n = 1; n <= 64; n += 21) {
        const uint8_t want_bytes[] = { (uint8_t)n };
        CHECK(hardware(REQ_GET_RANDOM, want_bytes, 1, &type, rx_buf, &body_len));
        CHECK_EQ_INT(rx_buf[0], 0x82);
        CHECK_EQ_INT(body_len, (size_t)n + 1);
    }

    // Asking for more than the 64-byte cap is a parameter error, not a length
    // error, and no bytes come back.
    const uint8_t too_long[] = { 65 };
    expect_error("get-random/65", REQ_GET_RANDOM, too_long, 1, ERR_INVALID_PARAM);

    const uint8_t zero[] = { 0 };
    expect_error("get-random/0", REQ_GET_RANDOM, zero, 1, ERR_INVALID_PARAM);

    expect_error("get-random/empty", REQ_GET_RANDOM, NULL, 0, ERR_INVALID_LENGTH);

    // The stream must be stable across calls, since it is derived from the
    // device's unique id.
    const uint8_t eight[] = { 8 };
    CHECK(hardware(REQ_GET_RANDOM, eight, 1, &type, rx_buf, &body_len));
    uint8_t first[8];
    memcpy(first, &rx_buf[1], 8);

    CHECK(hardware(REQ_GET_RANDOM, eight, 1, &type, rx_buf, &body_len));
    CHECK_EQ_MEM(&rx_buf[1], first, 8);
}

static void test_unavailable_features(void)
{
    SUITE("no-callback");
    reset_all();

    // A USB dongle has no battery, no temperature sensor, no crypto, and the
    // modem has no identity of its own. All must say so rather than invent data.
    expect_error("battery", REQ_GET_BATTERY, NULL, 0, ERR_NO_CALLBACK);
    expect_error("encrypt", REQ_ENCRYPT_DATA, (const uint8_t*)"\x01\x02", 2, ERR_NO_CALLBACK);
    expect_error("unknown", 0x7E, NULL, 0, ERR_NO_CALLBACK);

    const uint8_t no_subcmd[1] = { CMD_SETHARDWARE };
    serial_stub_reset();
    kiss_frame_received(CMD_SETHARDWARE, no_subcmd, 1);
    // 0x06 as a sub-command is simply unknown, so "no callback" is the honest
    // answer rather than a length complaint.
    uint8_t type;
    size_t body_len;
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], RSP_ERROR);
    CHECK_EQ_INT(rx_buf[1], ERR_NO_CALLBACK);

    // A SetHardware frame with no payload at all cannot name a sub-command.
    serial_stub_reset();
    kiss_frame_received(CMD_SETHARDWARE, NULL, 0);
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], RSP_ERROR);
    CHECK_EQ_INT(rx_buf[1], ERR_INVALID_LENGTH);
}

static void test_signal_report(void)
{
    SUITE("signal-report");
    reset_all();

    uint8_t type;
    size_t body_len;

    const uint8_t on[] = { 0x01 };
    CHECK(hardware(REQ_SET_SIGNAL_REPORT, on, 1, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x99); // HW_RESP(GET_SIGNAL_REPORT), not the doc's 0x9A
    CHECK_EQ_INT(rx_buf[1], 1);

    // With signal reporting on, a received packet must be followed by RxMeta.
    const uint8_t packet[] = { 0xDE, 0xAD, 0xBE, 0xEF, 0xC0, 0xDB };
    serial_stub_reset();
    kiss_packet_received(-84, 27, packet, sizeof(packet));

    uint8_t t1, t2;
    size_t l1, l2;
    CHECK(serial_stub_next(&t1, rx_buf, &l1));
    CHECK_EQ_INT(t1, CMD_DATA);
    CHECK_EQ_INT(l1, sizeof(packet));
    CHECK_EQ_MEM(rx_buf, packet, sizeof(packet));

    CHECK(serial_stub_next(&t2, rx_buf, &l2));
    CHECK_EQ_INT(t2, CMD_SETHARDWARE);
    CHECK_EQ_INT(l2, 3);
    CHECK_EQ_INT(rx_buf[0], RSP_RX_META);
    // SNR goes out in quarter-dB steps exactly as the SX1262 reports it.
    CHECK_EQ_INT((int8_t)rx_buf[1], 27);
    CHECK_EQ_INT((int8_t)rx_buf[2], -84);

    // Turning it off must suppress RxMeta but keep the data frame.
    CHECK(hardware(REQ_SET_SIGNAL_REPORT, (const uint8_t*)"\x00", 1, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[1], 0);

    serial_stub_reset();
    kiss_packet_received(-80, 12, packet, sizeof(packet));
    CHECK(serial_stub_next(&t1, rx_buf, &l1));
    CHECK_EQ_INT(t1, CMD_DATA);
    CHECK_EQ_INT(serial_stub_count(), 0);
}

static void test_tx_done(void)
{
    SUITE("tx-done");
    reset_all();

    uint8_t type;
    size_t body_len;

    serial_stub_reset();
    kiss_packet_transmitted(150);
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(type, CMD_SETHARDWARE);
    CHECK_EQ_INT(rx_buf[0], RSP_TX_DONE);
    CHECK_EQ_INT(rx_buf[1], 1); // success

    // A zero time on air means CSMA gave up or the transmit timed out.
    serial_stub_reset();
    kiss_packet_transmitted(0);
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[1], 0);
}

static void test_data_frames(void)
{
    SUITE("data");
    reset_all();

    // A payload must reach the radio byte for byte, delimiters included.
    uint8_t payload[255];
    for (int i = 0; i < 255; i++) {
        payload[i] = (uint8_t)i;
    }

    serial_stub_reset();
    radio_stub_set_tx_active(false);
    kiss_frame_received(CMD_DATA, payload, sizeof(payload));

    CHECK_EQ_INT(radio_stub_tx_count(), 1);
    CHECK_EQ_INT(radio_stub_last_tx_len(), 255);
    CHECK_EQ_MEM(radio_stub_last_tx(), payload, 255);

    // An empty data frame is a protocol error.
    serial_stub_reset();
    radio_stub_reset();
    kiss_frame_received(CMD_DATA, payload, 0);
    uint8_t type;
    size_t body_len;
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], RSP_ERROR);
    CHECK_EQ_INT(rx_buf[1], ERR_INVALID_LENGTH);
    CHECK_EQ_INT(radio_stub_tx_count(), 0);

    // Transmitting while already busy must be refused, not silently queued.
    serial_stub_reset();
    radio_stub_reset();
    radio_stub_set_tx_active(true);
    kiss_frame_received(CMD_DATA, payload, 4);
    CHECK(serial_stub_next(&type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], RSP_ERROR);
    CHECK_EQ_INT(rx_buf[1], 0x07); // TX_BUSY
    CHECK_EQ_INT(radio_stub_tx_count(), 0);
}

static void test_csma_parameters(void)
{
    SUITE("csma");
    reset_all();

    CHECK_EQ_INT(radio_stub_persistence(), 63);
    CHECK_EQ_INT(radio_stub_tx_delay(), 50);
    CHECK_EQ_INT(radio_stub_slot_time(), 10);
    CHECK(radio_stub_full_duplex() == false);

    const uint8_t txdelay[] = { 32 };
    kiss_frame_received(CMD_TXDELAY, txdelay, 1);
    CHECK_EQ_INT(radio_stub_tx_delay(), 32);

    const uint8_t persistence[] = { 100 };
    kiss_frame_received(CMD_PERSISTENCE, persistence, 1);
    CHECK_EQ_INT(radio_stub_persistence(), 100);

    const uint8_t slottime[] = { 5 };
    kiss_frame_received(CMD_SLOTTIME, slottime, 1);
    CHECK_EQ_INT(radio_stub_slot_time(), 5);

    const uint8_t fd[] = { 1 };
    kiss_frame_received(CMD_FULLDUPLEX, fd, 1);
    CHECK(radio_stub_full_duplex() == true);

    // EXIT (0xFF) is a no-op for a dedicated modem and must stay silent.
    serial_stub_reset();
    kiss_frame_received(CMD_RETURN, NULL, 0);
    CHECK_EQ_INT(serial_stub_count(), 0);

    // Unknown commands are discarded silently, per the KISS specification.
    serial_stub_reset();
    kiss_frame_received(0x07, (const uint8_t*)"\x00", 1);
    CHECK_EQ_INT(serial_stub_count(), 0);

    // Anything on a non-zero port is ignored.
    serial_stub_reset();
    const uint8_t one = 0x42;
    kiss_frame_received(0x10 | CMD_DATA, &one, 1);
    CHECK_EQ_INT(serial_stub_count(), 0);
}

static void test_port_isolation(void)
{
    SUITE("ports");
    reset_all();

    // MeshCore only ever uses port 0. Frames addressed to another port must not
    // disturb the modem.
    serial_stub_reset();
    radio_stub_reset();

    uint8_t frame[] = { 0x01, 0x0B, 0x07 };
    kiss_frame_received(0x40, frame, 3);

    CHECK_EQ_INT(serial_stub_count(), 0);
    CHECK_EQ_INT(radio_stub_tx_count(), 0);
}

static void test_reboot(void)
{
    SUITE("reboot");
    reset_all();

    uint8_t type;
    size_t body_len;
    CHECK(hardware(REQ_REBOOT, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], RSP_OK);
    CHECK_EQ_INT(radio_stub_reboot_count(), 1);
}

static void test_mutex_is_balanced(void)
{
    SUITE("mutex");
    reset_all();

    // Two different locks are in play. A received data frame is emitted straight
// through serial_send_message, which the serial layer serialises with its own
// frame mutex. Only the SetHardware responses share hw_buf, so only they take
// the mutex in the modem. Both must be taken and released exactly once each.
    reset_all();

    const uint8_t payload[] = { 0x01, 0x02, 0x03 };

    kiss_packet_received(-90, 0, payload, sizeof(payload));

    // The data frame is free, the trailing RxMeta costs one take.
    CHECK_EQ_INT(freertos_stub_mutex_take_count(), 1);
    CHECK_EQ_INT(freertos_stub_mutex_depth(), 0);

    uint8_t type;
    size_t body_len;
    CHECK(hardware(REQ_PING, NULL, 0, &type, rx_buf, &body_len));
    CHECK_EQ_INT(rx_buf[0], 0x97); // PONG

    CHECK_EQ_INT(freertos_stub_mutex_take_count(), 2);
    CHECK_EQ_INT(freertos_stub_mutex_depth(), 0);
}

// -- full path through the real parser --------------------------------------

static void test_end_to_end_through_parser(void)
{
    SUITE("end-to-end");
    reset_all();

    // Build a request the same way a host would: encode with kiss_frame_encode,
    // then feed it a byte at a time through kiss_parser_feed, exactly as
    // serial_rx_task does on the target.
    uint8_t payload[8];
    payload[0] = REQ_GET_VERSION;

    uint8_t wire[64];
    const size_t wire_len = kiss_frame_encode(CMD_SETHARDWARE, payload, 1, wire, sizeof(wire));

    kiss_parser_t parser;
    kiss_parser_reset(&parser);

    serial_stub_reset();

    for (size_t i = 0; i < wire_len; i++) {
        if (kiss_parser_feed(&parser, wire[i])) {
            kiss_frame_received(parser.type, parser.data, parser.len);
        }
    }

    // Now read the modem's reply back through the same parser, which also
    // proves the encoder's escaping survives the round trip.
    uint8_t out[STUB_MAX_PAYLOAD];
    uint8_t type;
    size_t out_len;

    CHECK(serial_stub_next(&type, out, &out_len));

    const size_t reply_len = kiss_frame_encode(type, out, out_len, wire, sizeof(wire));
    kiss_parser_reset(&parser);
    bool got = false;

    for (size_t i = 0; i < reply_len && !got; i++) {
        got = kiss_parser_feed(&parser, wire[i]);
    }

    CHECK(got);
    CHECK_EQ_INT(parser.type, CMD_SETHARDWARE);
    CHECK_EQ_INT(parser.len, 3);
    CHECK_EQ_INT(parser.data[0], 0x91);
    CHECK_EQ_INT(parser.data[1], FIRMWARE_VERSION);
}

int test_kiss_modem(void)
{
    RUN(test_set_radio_accepts_and_applies);
    RUN(test_set_radio_rejects_bad_parameters);
    RUN(test_get_radio_round_trips);
    RUN(test_tx_power);
    RUN(test_simple_queries);
    RUN(test_random);
    RUN(test_unavailable_features);
    RUN(test_signal_report);
    RUN(test_tx_done);
    RUN(test_data_frames);
    RUN(test_csma_parameters);
    RUN(test_port_isolation);
    RUN(test_reboot);
    RUN(test_mutex_is_balanced);
    RUN(test_end_to_end_through_parser);

    return tu_report("kiss_modem");
}
