#ifndef PA_CONFIG_H__
#define PA_CONFIG_H__

#include <stdint.h>

/*
 * The SX1262's high power amplifier needs a (pa_duty_cycle, hp_max) pair chosen
 * to reach the requested output power; the two are not independent. These are the
 * combinations Semtech specifies, kept here as plain arithmetic so they can be
 * verified on the host rather than trusted to a table buried in the driver.
 *
 * At or below +14 dBm the low power amplifier is used, where both fields are 0.
 */

#define PA_LP_DUTY_CYCLE 0x02
#define PA_LP_HP_MAX     0x02

// Highest output power the part can deliver.
#define PA_MAX_POWER_DBM  22

// Lowest power the driver accepts before clamping.
#define PA_MIN_POWER_DBM (-17)

/**
 * Returns the PA settings for a requested power in dBm. Power is clamped to the
 * supported range, and the returned settings are monotonic in power, so a
 * higher request never yields a lower setting.
 */
void pa_config_for(int8_t power_dbm, uint8_t* pa_duty_cycle, uint8_t* hp_max);

#endif // PA_CONFIG_H__
