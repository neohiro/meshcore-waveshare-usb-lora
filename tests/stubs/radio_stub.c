#include "radio.h"
#include "radio_stub.h"
#include "kiss.h"

#include <string.h>

static radio_lora_params_t lora_params;
static uint32_t frequency;
static int8_t tx_power;

static bool auto_tx_done;
static uint32_t auto_tx_done_ms = 100;

static uint8_t persistence;
static uint8_t slot_time;
static uint8_t tx_delay;
static bool full_duplex;

static uint8_t last_tx[256];
static size_t last_tx_len;
static int tx_count;

static bool tx_active;
static bool channel_busy;
static int16_t rssi = -120;
static uint32_t airtime = 100;

static int reboot_count;

void radio_stub_reset(void)
{
    memset(&lora_params, 0, sizeof(lora_params));
    memset(last_tx, 0, sizeof(last_tx));

    // Boot defaults, matching the real firmware: MeshCore EU/UK narrow.
    lora_params.spreading_factor = 8;
    lora_params.bandwidth = 3;    // SX126X_LORA_BW_062
    lora_params.coding_rate = 4;   // SX126X_LORA_CR_4_8
    frequency = 869618000;
    tx_power = 17;

    persistence = 63;
    slot_time = 10;
    tx_delay = 50;
    full_duplex = false;

    last_tx_len = 0;
    tx_count = 0;
    tx_active = false;
    channel_busy = false;
    rssi = -120;
    airtime = 100;
    reboot_count = 0;
    auto_tx_done = false;
    auto_tx_done_ms = 100;
}

void radio_init(radio_handler_t* h)
{
    (void)h;
}

void radio_get_lora_params(radio_lora_params_t* params)
{
    if (params != NULL) {
        *params = lora_params;
    }
}

void radio_set_lora_params(const radio_lora_params_t* params)
{
    if (params != NULL) {
        lora_params = *params;
    }
}

uint32_t radio_get_frequency(void)
{
    return frequency;
}

void radio_set_frequency(uint32_t f)
{
    frequency = f;
}

int16_t radio_get_continuous_rssi(void)
{
    return rssi;
}

int8_t radio_get_tx_power(void)
{
    return tx_power;
}

void radio_set_tx_power(int8_t power)
{
    tx_power = power;
}

void radio_set_meshcore_packet_params(void)
{
}

bool radio_is_tx_active(void)
{
    return tx_active;
}

void radio_set_tx(const uint8_t* payload, size_t payload_size)
{
    if (payload_size > sizeof(last_tx)) {
        payload_size = sizeof(last_tx);
    }

    if (payload != NULL && payload_size > 0) {
        memcpy(last_tx, payload, payload_size);
    }

    last_tx_len = payload_size;
    tx_count++;

    if (auto_tx_done) {
        tx_active = false;

        if (auto_tx_done_ms == 0) {
            kiss_packet_transmitted(0);
        } else {
            kiss_packet_transmitted(auto_tx_done_ms);
        }
    }
}

void radio_stub_set_auto_tx_done(bool enabled)
{
    auto_tx_done = enabled;
}

void radio_stub_set_tx_airtime(uint32_t ms)
{
    auto_tx_done_ms = ms;
}

uint8_t radio_get_standby(void)
{
    return 0;
}

void radio_set_standby(uint8_t mode)
{
    (void)mode;
}

void radio_set_csma_params(uint8_t p, uint8_t slot, uint8_t delay, bool fd)
{
    persistence = p;
    slot_time = slot;
    tx_delay = delay;
    full_duplex = fd;
}

bool radio_is_channel_busy(void)
{
    return channel_busy;
}

uint32_t radio_get_airtime(uint8_t payload_len)
{
    (void)payload_len;
    return airtime;
}

void radio_get_packet_status(int8_t* rssi_dbm, int8_t* snr_quarter_db, int8_t* signal_rssi_dbm)
{
    if (rssi_dbm != NULL) {
        *rssi_dbm = (int8_t)rssi;
    }
    if (snr_quarter_db != NULL) {
        *snr_quarter_db = 0;
    }
    if (signal_rssi_dbm != NULL) {
        *signal_rssi_dbm = (int8_t)rssi;
    }
}

void radio_get_stats(uint32_t* rx_count, uint32_t* tx_count, uint32_t* rx_errors)
{
    if (rx_count != NULL) {
        *rx_count = 7;
    }
    if (tx_count != NULL) {
        *tx_count = 3;
    }
    if (rx_errors != NULL) {
        *rx_errors = 1;
    }
}

void radio_reboot(void)
{
    reboot_count++;
}

// -- test-facing accessors ---------------------------------------------------

int radio_stub_tx_count(void)
{
    return tx_count;
}

const uint8_t* radio_stub_last_tx(void)
{
    return last_tx;
}

size_t radio_stub_last_tx_len(void)
{
    return last_tx_len;
}

void radio_stub_set_tx_active(bool active)
{
    tx_active = active;
}

void radio_stub_set_channel_busy(bool busy)
{
    channel_busy = busy;
}

void radio_stub_set_rssi(int16_t value)
{
    rssi = value;
}

void radio_stub_set_airtime(uint32_t ms)
{
    airtime = ms;
}

int radio_stub_reboot_count(void)
{
    return reboot_count;
}

uint32_t radio_stub_frequency(void)
{
    return frequency;
}

int8_t radio_stub_tx_power(void)
{
    return tx_power;
}

uint8_t radio_stub_persistence(void)
{
    return persistence;
}

uint8_t radio_stub_slot_time(void)
{
    return slot_time;
}

uint8_t radio_stub_tx_delay(void)
{
    return tx_delay;
}

bool radio_stub_full_duplex(void)
{
    return full_duplex;
}
