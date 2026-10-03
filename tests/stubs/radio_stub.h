#ifndef RADIO_STUB_H
#define RADIO_STUB_H

/*
 * A fake SX1262 that records what the modem asked for. Lets the tests assert on
 * the translation from KISS SetHardware fields to radio parameters.
 */

#include <stdbool.h>
#include <stdint.h>

void radio_stub_reset(void);

/**
 * Complete a transmission the moment one is requested, reporting the given
 * time on air. Lets a host client's SendData return instead of waiting for a
 * TX_DONE that no real radio will ever send. 0 ms reports a failure, which is
 * how CSMA giving up is surfaced.
 */
void radio_stub_set_auto_tx_done(bool enabled);
void radio_stub_set_tx_airtime(uint32_t ms);

int radio_stub_tx_count(void);
const uint8_t* radio_stub_last_tx(void);
size_t radio_stub_last_tx_len(void);

void radio_stub_set_tx_active(bool active);
void radio_stub_set_channel_busy(bool busy);
void radio_stub_set_rssi(int16_t rssi);
void radio_stub_set_airtime(uint32_t ms);

int radio_stub_reboot_count(void);

uint32_t radio_stub_frequency(void);
int8_t radio_stub_tx_power(void);
uint8_t radio_stub_persistence(void);
uint8_t radio_stub_slot_time(void);
uint8_t radio_stub_tx_delay(void);
bool radio_stub_full_duplex(void);

#endif // RADIO_STUB_H
