#include "pa_config.h"

void pa_config_for(int8_t power_dbm, uint8_t* pa_duty_cycle, uint8_t* hp_max)
{
    if (power_dbm > PA_MAX_POWER_DBM) {
        power_dbm = PA_MAX_POWER_DBM;
    }

    if (power_dbm < PA_MIN_POWER_DBM) {
        power_dbm = PA_MIN_POWER_DBM;
    }

    if (power_dbm <= 14) {
        // Low power amplifier.
        *pa_duty_cycle = PA_LP_DUTY_CYCLE;
        *hp_max = PA_LP_HP_MAX;
    } else if (power_dbm <= 17) {
        // High power amplifier, +17 dBm.
        *pa_duty_cycle = 0x02;
        *hp_max = 0x03;
    } else if (power_dbm <= 20) {
        // High power amplifier, +20 dBm.
        *pa_duty_cycle = 0x03;
        *hp_max = 0x05;
    } else {
        // High power amplifier, +22 dBm.
        *pa_duty_cycle = 0x04;
        *hp_max = 0x07;
    }
}
