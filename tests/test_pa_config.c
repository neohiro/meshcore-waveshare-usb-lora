#include "test_pa_config.h"
#include "test_util.h"

#include "pa_config.h"

#include <stdint.h>

typedef struct {
    int8_t power;
    uint8_t duty;
    uint8_t hp_max;
    const char* note;
} pa_case_t;

static void test_reference_combinations(void)
{
    SUITE("pa/reference");

    // The combinations Semtech specifies. Getting these wrong does not report an
    // error, it just transmits at the wrong power.
    static const pa_case_t cases[] = {
        { -17, 0x02, 0x02, "clamped low, low power amplifier" },
        {   0, 0x02, 0x02, "low power amplifier" },
        {  14, 0x02, 0x02, "top of the low power amplifier" },
        {  15, 0x02, 0x03, "just into the high power amplifier" },
        {  17, 0x02, 0x03, "high power amplifier, +17 dBm" },
        {  18, 0x03, 0x05, "step up" },
        {  20, 0x03, 0x05, "high power amplifier, +20 dBm" },
        {  21, 0x04, 0x07, "step up" },
        {  22, 0x04, 0x07, "high power amplifier, +22 dBm" }
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        uint8_t duty = 0;
        uint8_t hp = 0;

        pa_config_for(cases[i].power, &duty, &hp);

        tu_checks++;

        if (duty != cases[i].duty || hp != cases[i].hp_max) {
            tu_failures++;
            printf("  FAIL [%s] %d dBm: got duty=0x%02X hp_max=0x%02X, "
                   "want duty=0x%02X hp_max=0x%02X (%s)\n",
                   tu_suite, (int)cases[i].power, duty, hp,
                   cases[i].duty, cases[i].hp_max, cases[i].note);
        }
    }
}

static void test_clamps_out_of_range(void)
{
    SUITE("pa/clamps");

    uint8_t duty_hi = 0;
    uint8_t hp_hi = 0;
    uint8_t duty_lo = 0;
    uint8_t hp_lo = 0;

    pa_config_for(127, &duty_hi, &hp_hi);
    pa_config_for(PA_MAX_POWER_DBM, &duty_hi, &hp_hi);
    CHECK_EQ_INT(duty_hi, 0x04);
    CHECK_EQ_INT(hp_hi, 0x07);

    pa_config_for(-128, &duty_lo, &hp_lo);
    pa_config_for(PA_MIN_POWER_DBM, &duty_lo, &hp_lo);
    CHECK_EQ_INT(duty_lo, 0x02);
    CHECK_EQ_INT(hp_lo, 0x02);
}

static void test_monotonic(void)
{
    SUITE("pa/monotonic");

    // A higher requested power must never yield a lower amplifier setting, or
    // the radio would silently transmit more weakly than asked.
    uint8_t prev_duty = 0;
    uint8_t prev_hp = 0;

    for (int p = PA_MIN_POWER_DBM; p <= PA_MAX_POWER_DBM; p++) {
        uint8_t duty = 0;
        uint8_t hp = 0;

        pa_config_for((int8_t)p, &duty, &hp);

        tu_checks++;

        if (p > PA_MIN_POWER_DBM && (duty < prev_duty || (duty == prev_duty && hp < prev_hp))) {
            tu_failures++;
            printf("  FAIL [%s] %d dBm: settings went backwards "
                   "(duty=0x%02X hp_max=0x%02X after duty=0x%02X hp_max=0x%02X)\n",
                   tu_suite, p, duty, hp, prev_duty, prev_hp);
        }

        prev_duty = duty;
        prev_hp = hp;
    }

    // And the extremes must be the documented ones, so the sweep really did
    // reach both ends.
    CHECK_EQ_INT(prev_duty, 0x04);
    CHECK_EQ_INT(prev_hp, 0x07);
}

static void test_eu_duty_cycle_default(void)
{
    SUITE("pa/default");

    // The firmware boots at +17 dBm, which must stay inside the EU 868 duty
    // cycle budget of 1%.
    uint8_t duty = 0;
    uint8_t hp = 0;

    pa_config_for(17, &duty, &hp);
    CHECK_EQ_INT(duty, 0x02);
    CHECK_EQ_INT(hp, 0x03);
}

int test_pa_config(void)
{
    RUN(test_reference_combinations);
    RUN(test_clamps_out_of_range);
    RUN(test_monotonic);
    RUN(test_eu_duty_cycle_default);

    return tu_report("pa_config");
}
