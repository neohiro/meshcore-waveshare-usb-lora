#ifndef RADIO_H__
#define RADIO_H__

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/**
 * The LoRa sync word MeshCore uses on the SX126x family.
 *
 * MeshCore's variants pass RadioLib's RADIOLIB_SX126X_SYNC_WORD_PRIVATE to
 * radio.begin(). That constant's value is 0x12 (RadioLib even comments it as
 * "actually 0x1424"); it is NOT 0x2B (Meshtastic) and NOT 0x34 (LoRaWAN public).
 * Any other value puts this modem on a different network, where it can neither
 * receive MeshCore nodes nor be received by them.
 */
#define MESHCORE_SYNC_WORD 0x12

#define LORA_IRQ_PRIORITY   0xBF

typedef struct {
    void (*packet_received)(int8_t rssi_dbm, int8_t snr_quarter_db, const uint8_t* payload, size_t payload_size);
    void (*packet_transmitted)(uint32_t time_on_air);
} radio_handler_t;

void radio_init(radio_handler_t* handler);

typedef struct {
    uint8_t spreading_factor;
    uint8_t bandwidth;
    uint8_t coding_rate;
    uint8_t low_data_rate;
} radio_lora_params_t;

void radio_get_lora_params(radio_lora_params_t* params);
void radio_set_lora_params(const radio_lora_params_t* params);

uint32_t radio_get_frequency();
void radio_set_frequency(uint32_t f);

int16_t radio_get_continuous_rssi();

int8_t radio_get_tx_power();
void radio_set_tx_power(int8_t power);

bool radio_is_tx_active();
void radio_set_tx(const uint8_t* payload, size_t payload_size);

// LoRa packet parameters are fixed for MeshCore: explicit header, 16 symbol
// preamble, the private sync word, CRC on, IQ not inverted.
void radio_set_meshcore_packet_params(void);

uint8_t radio_get_standby();
void radio_set_standby(uint8_t mode);

/**
 * CSMA tuning, in the units KISS uses: times are 10 ms units.
 * persistence is the p-persistent back-off parameter (0..255).
 */
void radio_set_csma_params(uint8_t persistence, uint8_t slot_time, uint8_t tx_delay, bool full_duplex);

// Runs a channel-activity detection. Leaves the radio listening again.
bool radio_is_channel_busy(void);

// Estimated time on air in ms for a payload of payload_len bytes.
uint32_t radio_get_airtime(uint8_t payload_len);

/**
 * Signal report for the packet just received. rssi_dbm and signal_rssi_dbm are
 * in dBm; snr_quarter_db is SNR in quarter-dB steps as the SX126x reports it
 * (so divide by 4 for dB), which preserves the fractional part that the
 * bundled driver's integer-dB conversion throws away.
 */
void radio_get_packet_status(int8_t* rssi_dbm, int8_t* snr_quarter_db, int8_t* signal_rssi_dbm);

void radio_get_stats(uint32_t* rx_count, uint32_t* tx_count, uint32_t* rx_errors);

void radio_reboot(void);

#endif // RADIO_H__
