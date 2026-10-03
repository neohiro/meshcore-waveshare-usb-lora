#include "kiss.h"
#include "version.h"
#include "serial.h"
#include "radio.h"
#include "device_id.h"
#include "lora_params.h"

#include <FreeRTOS.h>
#include <semphr.h>

#include <string.h>

static uint8_t tx_delay = 50;      // 10 ms units
static uint8_t persistence = 63;
static uint8_t slot_time = 10;     // 10 ms units
static bool full_duplex = false;
static volatile bool signal_report = true;

// hw_buf is filled by both the KISS command handler (serial task) and the
// TxDone/RxMeta paths (radio task), so filling and sending has to be atomic
// with respect to both.
static uint8_t hw_buf[80];
static SemaphoreHandle_t hw_mutex = NULL;

static uint32_t rd_u32(const uint8_t* p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void push_csma(void)
{
    radio_set_csma_params(persistence, slot_time, tx_delay, full_duplex);
}

static void hw_send(uint8_t subcmd, const uint8_t* data, size_t size)
{
    if (size > sizeof(hw_buf) - 1) {
        return;
    }

    if (hw_mutex != NULL) {
        xSemaphoreTake(hw_mutex, portMAX_DELAY);
    }

    hw_buf[0] = subcmd;

    if (data != NULL && size > 0) {
        memcpy(&hw_buf[1], data, size);
    }

    serial_send_message(KISS_CMD_SETHARDWARE, hw_buf, size + 1);

    if (hw_mutex != NULL) {
        xSemaphoreGive(hw_mutex);
    }
}

static void hw_ok(void)
{
    hw_send(HW_RESP_OK, NULL, 0);
}

static void hw_error(uint8_t code)
{
    hw_send(HW_RESP_ERROR, &code, 1);
}

static void hw_no_callback(void)
{
    hw_error(HW_ERR_NO_CALLBACK);
}

static void handle_set_radio(const uint8_t* data, size_t size)
{
    // Freq (4) + BW (4) + SF (1) + CR (1), little endian.
    if (size < 10) {
        hw_error(HW_ERR_INVALID_LENGTH);
        return;
    }

    uint32_t freq = rd_u32(data);
    uint32_t bw_hz = rd_u32(data + 4);
    uint8_t sf = data[8];
    uint8_t cr = data[9];

    if (freq < 150000000 || freq > 960000000) {
        hw_error(HW_ERR_INVALID_PARAM);
        return;
    }

    if (sf < 5 || sf > 12) {
        hw_error(HW_ERR_INVALID_PARAM);
        return;
    }

    // The wire carries the coding rate denominator (5..8); the SX126x
    // enumerant is 1..4.
    if (cr < 5 || cr > 8) {
        hw_error(HW_ERR_INVALID_PARAM);
        return;
    }

uint8_t bw = lora_bw_from_hz(bw_hz);
    if (bw == LORA_BW_INVALID) {
        hw_error(HW_ERR_INVALID_PARAM);
        return;
    }

    radio_lora_params_t params;
    radio_get_lora_params(&params);

    params.spreading_factor = sf;
    params.bandwidth = bw;
    params.coding_rate = (uint8_t)(cr - 4);
    params.low_data_rate = 0;

    radio_set_lora_params(&params);
    radio_set_frequency(freq);

    hw_ok();
}

static void handle_get_radio(void)
{
    uint8_t out[10];

    radio_lora_params_t params;
    radio_get_lora_params(&params);

    uint32_t freq = radio_get_frequency();
    uint32_t bw_hz = lora_hz_from_bw(params.bandwidth);

    out[0] = (uint8_t)(freq & 0xFF);
    out[1] = (uint8_t)((freq >> 8) & 0xFF);
    out[2] = (uint8_t)((freq >> 16) & 0xFF);
    out[3] = (uint8_t)((freq >> 24) & 0xFF);
    out[4] = (uint8_t)(bw_hz & 0xFF);
    out[5] = (uint8_t)((bw_hz >> 8) & 0xFF);
    out[6] = (uint8_t)((bw_hz >> 16) & 0xFF);
    out[7] = (uint8_t)((bw_hz >> 24) & 0xFF);
    out[8] = params.spreading_factor;
    out[9] = (uint8_t)(params.coding_rate + 4);

    hw_send(HW_RESP_RADIO, out, sizeof(out));
}

static void process_sethardware(const uint8_t* data, size_t size)
{
    const uint8_t* payload = data + 1;
    const size_t payload_size = size - 1;

    switch (data[0]) {
    case HW_CMD_GET_RANDOM: {
        // The request is a single length byte, exactly as MeshCore's own modem
        // reads it: data[0] is how many bytes to return, not part of them.
        if (payload_size < 1) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        const uint8_t requested = payload[0];

        if (requested < 1 || requested > 64) {
            hw_error(HW_ERR_INVALID_PARAM);
            return;
        }

        // A deterministic per-device sequence is enough: the host only needs
        // unpredictable-looking bytes, and this avoids fiddling with the
        // SX126x analogue and RNG registers.
        uint8_t out[64];
        uint32_t state = device_id_seed();

        for (size_t i = 0; i < requested; i++) {
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            out[i] = (uint8_t)(state & 0xFF);
        }

        hw_send(HW_RESP_RANDOM, out, requested);
        break;
    }

    case HW_CMD_SET_RADIO:
        handle_set_radio(payload, payload_size);
        break;

    case HW_CMD_SET_TX_POWER: {
        if (payload_size < 1) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        int8_t power = (int8_t)payload[0];
        if (power < -17 || power > 22) {
            hw_error(HW_ERR_INVALID_PARAM);
            return;
        }

        radio_set_tx_power(power);
        hw_ok();
        break;
    }

    case HW_CMD_GET_TX_POWER: {
        int8_t power = radio_get_tx_power();
        hw_send(HW_RESP_TX_POWER, (const uint8_t*)&power, 1);
        break;
    }

    case HW_CMD_GET_RADIO:
        handle_get_radio();
        break;

    case HW_CMD_GET_CURRENT_RSSI: {
        int16_t rssi = radio_get_continuous_rssi();
        int8_t rssi_dbm = (rssi < -128) ? -128 : (rssi > 127 ? 127 : rssi);
        hw_send(HW_RESP_CURRENT_RSSI, (const uint8_t*)&rssi_dbm, 1);
        break;
    }

    case HW_CMD_IS_CHANNEL_BUSY: {
        uint8_t busy = radio_is_channel_busy() ? 0x01 : 0x00;
        hw_send(HW_RESP_CHANNEL_BUSY, &busy, 1);
        break;
    }

    case HW_CMD_GET_AIRTIME: {
        if (payload_size < 1) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        // A single length byte, so 0 is the only invalid value.
        const uint8_t len = payload[0];
        if (len == 0) {
            hw_error(HW_ERR_INVALID_PARAM);
            return;
        }

        uint32_t ms = radio_get_airtime(len);
        uint8_t out[4] = {
            (uint8_t)(ms & 0xFF),
            (uint8_t)((ms >> 8) & 0xFF),
            (uint8_t)((ms >> 16) & 0xFF),
            (uint8_t)((ms >> 24) & 0xFF)
        };
        hw_send(HW_RESP_AIRTIME, out, sizeof(out));
        break;
    }

    case HW_CMD_GET_NOISE_FLOOR: {
        // With the modem listening and no packet in progress, the
        // instantaneous RSSI is the noise floor.
        int16_t rssi = radio_get_continuous_rssi();
        uint8_t out[2] = { (uint8_t)(rssi & 0xFF), (uint8_t)((rssi >> 8) & 0xFF) };
        hw_send(HW_RESP_NOISE_FLOOR, out, sizeof(out));
        break;
    }

    case HW_CMD_GET_VERSION: {
        uint8_t out[2] = { FIRMWARE_VERSION, 0x00 };
        hw_send(HW_RESP_VERSION, out, sizeof(out));
        break;
    }

    case HW_CMD_GET_STATS: {
        uint32_t rx, tx, errs;
        radio_get_stats(&rx, &tx, &errs);

        uint8_t out[12] = {
            (uint8_t)(rx & 0xFF),        (uint8_t)((rx >> 8) & 0xFF),
            (uint8_t)((rx >> 16) & 0xFF), (uint8_t)((rx >> 24) & 0xFF),
            (uint8_t)(tx & 0xFF),        (uint8_t)((tx >> 8) & 0xFF),
            (uint8_t)((tx >> 16) & 0xFF), (uint8_t)((tx >> 24) & 0xFF),
            (uint8_t)(errs & 0xFF),      (uint8_t)((errs >> 8) & 0xFF),
            (uint8_t)((errs >> 16) & 0xFF), (uint8_t)((errs >> 24) & 0xFF)
        };
        hw_send(HW_RESP_STATS, out, sizeof(out));
        break;
    }

    case HW_CMD_GET_DEVICE_NAME:
        hw_send(HW_RESP_DEVICE_NAME, (const uint8_t*)KISS_DEVICE_NAME, strlen(KISS_DEVICE_NAME));
        break;

    case HW_CMD_PING:
        hw_send(HW_RESP_PONG, NULL, 0);
        break;

    case HW_CMD_REBOOT:
        hw_ok();
        radio_reboot();
        break;

    case HW_CMD_SET_SIGNAL_REPORT: {
        if (payload_size < 1) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        signal_report = payload[0] != 0;
        uint8_t status = signal_report ? 0x01 : 0x00;
        hw_send(HW_RESP_SIGNAL_REPORT, &status, 1);
        break;
    }

    case HW_CMD_GET_SIGNAL_REPORT: {
        uint8_t status = signal_report ? 0x01 : 0x00;
        hw_send(HW_RESP_SIGNAL_REPORT, &status, 1);
        break;
    }

    // USB powered dongle with no battery sense, no temperature sensor and no
    // crypto backend: report the feature as unavailable rather than inventing
    // a reading.
    case HW_CMD_GET_BATTERY:
    case HW_CMD_GET_MCU_TEMP:
    case HW_CMD_GET_SENSORS:
        hw_no_callback();
        break;

    default:
        // Includes the Ed25519/X25519/AES/SHA sub-commands: this is a radio
        // modem, all cryptography stays on the host.
        hw_error(HW_ERR_NO_CALLBACK);
        break;
    }
}

void kiss_frame_received(uint8_t type, const uint8_t* data, size_t size)
{
    const uint8_t port = type & KISS_MASK_PORT;
    const uint8_t cmd = type & KISS_MASK_CMD;

    // Single port TNC.
    if (port != 0) {
        return;
    }

    switch (cmd) {
    case KISS_CMD_DATA:
        if (size == 0 || size > KISS_MAX_PAYLOAD) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        if (radio_is_tx_active()) {
            hw_error(HW_ERR_TX_BUSY);
            return;
        }

        radio_set_tx(data, size);
        break;

    case KISS_CMD_TXDELAY:
        if (size >= 1) {
            tx_delay = data[0];
            push_csma();
        }
        break;

    case KISS_CMD_PERSISTENCE:
        if (size >= 1) {
            persistence = data[0];
            push_csma();
        }
        break;

    case KISS_CMD_SLOTTIME:
        if (size >= 1) {
            slot_time = data[0];
            push_csma();
        }
        break;

    case KISS_CMD_TXTAIL:
        // Accepted and ignored: the SX126x falls back to receive itself.
        if (size >= 1) {
            hw_ok();
        }
        break;

    case KISS_CMD_FULLDUPLEX:
        if (size >= 1) {
            full_duplex = data[0] != 0;
            push_csma();
        }
        break;

    case KISS_CMD_SETHARDWARE:
        if (size < 1) {
            hw_error(HW_ERR_INVALID_LENGTH);
            return;
        }

        process_sethardware(data, size);
        break;

    case KISS_CMD_RETURN:
        // Exit KISS mode is a no-op for a dedicated modem.
        break;

    default:
        // Per the KISS spec, unknown commands are silently discarded.
        break;
    }
}

//------------------------------------------------------------------------------

void kiss_init(void)
{
    // Set every host-visible default explicitly rather than relying on static
    // initialisation, so a re-init genuinely restores a known state.
    hw_mutex = xSemaphoreCreateMutex();

    tx_delay = 50;      // 500 ms before transmitting
    persistence = 63;  // p-persistent back-off parameter
    slot_time = 10;     // 100 ms between channel-busy retries
    full_duplex = false;
    signal_report = true;

    push_csma();
}

void kiss_packet_received(int8_t rssi_dbm, int8_t snr_quarter_db, const uint8_t* data, size_t size)
{
    if (size == 0 || size > KISS_MAX_PAYLOAD) {
        return;
    }

    // The data frame goes out first, then the signal report, so the host can
    // pair them (MeshCore's modem does the same).
    serial_send_message(KISS_CMD_DATA, data, size);

    if (signal_report) {
        uint8_t meta[2];

        // SNR is carried in quarter-dB steps, matching the SX126x register.
        meta[0] = (uint8_t)snr_quarter_db;
        meta[1] = (uint8_t)rssi_dbm;

        hw_send(HW_RESP_RX_META, meta, sizeof(meta));
    }
}

void kiss_packet_transmitted(uint32_t time_on_air)
{
    // 0x01 on success, 0x00 when CSMA gave up or the transmit timed out.
    uint8_t result = time_on_air > 0 ? 0x01 : 0x00;
    hw_send(HW_RESP_TX_DONE, &result, 1);
}
