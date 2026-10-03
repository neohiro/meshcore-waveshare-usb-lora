#ifndef DEVICE_ID_H__
#define DEVICE_ID_H__

#include <stdint.h>

/**
 * A non-zero value derived from the MCU's unique device ID.
 *
 * The protocol layer needs a per-device seed for GetRandom, and the CSMA back-off
 * needs one for its p-persistent jitter. Reading the silicon ID register is a
 * platform concern, so it lives here rather than in either of those callers.
 */
uint32_t device_id_seed(void);

#endif // DEVICE_ID_H__
