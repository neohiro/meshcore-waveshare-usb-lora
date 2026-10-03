#ifndef LORA_PARAMS_H__
#define LORA_PARAMS_H__

#include <stdint.h>

/**
 * Pure LoRa parameter arithmetic, kept out of the radio driver and the protocol
 * layer so it can be exercised on the host.
 *
 * The bandwidth and low-data-rate rules here are dictated by what MeshCore's
 * nodes do, since they are driven by RadioLib. Getting either wrong does not
 * break the link visibly: it just fails to decode.
 */

// Returned by lora_bw_from_hz for a bandwidth the SX126x cannot produce.
#define LORA_BW_INVALID 0xFF

// Maps a bandwidth in Hz to the SX126x enumerant, or LORA_BW_INVALID.
uint8_t lora_bw_from_hz(uint32_t hz);

// Maps an SX126x bandwidth enumerant to Hz, or 0 if the code is not valid.
uint32_t lora_hz_from_bw(uint8_t code);

/**
 * Whether the low data rate optimization must be on for this SF/BW pair.
 *
 * RadioLib enables it when the symbol duration reaches 16 ms, and MeshCore
 * nodes leave RadioLib's automatic mode on, so matching that is required for
 * interoperability. Note that this depends on the bandwidth as well as the
 * spreading factor: SF11 at 125 kHz needs it, SF11 at 250 kHz does not.
 */
uint8_t lora_ldro_for(uint8_t sf, uint8_t bw_code);

// The bandwidth in Hz the SX1262 is specified to use with its TCXO supplied on
// DIO3 at 1.8 V, as fitted to the Waveshare dongle.
#define LORA_TCXO_SUPPLY_MV 1800

#endif // LORA_PARAMS_H__
