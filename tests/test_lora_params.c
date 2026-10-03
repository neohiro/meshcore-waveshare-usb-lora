#include "test_lora_params.h"
#include "test_util.h"

#include "lora_params.h"
#include "radio.h"

#include <stdint.h>

typedef struct {
    uint32_t hz;
    uint8_t code;
} bw_case_t;

// Every bandwidth the SX126x can produce, with its register encoding. The codes
// are the silicon's enumerants, not sequential values.
static const bw_case_t bandwidths[] = {
    { 7000, 0 },
    { 10400, 8 },
    { 15600, 1 },
    { 20300, 9 },
    { 31250, 2 },
    { 41700, 10 },
    { 62500, 3 },
    { 125000, 4 },
    { 250000, 5 },
    { 500000, 6 }
};

static void test_bandwidth_round_trip(void)
{
    SUITE("bandwidth/round-trip");

    for (size_t i = 0; i < sizeof(bandwidths) / sizeof(bandwidths[0]); i++) {
        const uint8_t code = lora_bw_from_hz(bandwidths[i].hz);

        CHECK_EQ_INT(code, bandwidths[i].code);
        CHECK_EQ_INT(lora_hz_from_bw(code), bandwidths[i].hz);
    }
}

static void test_bandwidth_rejects_nonsense(void)
{
    SUITE("bandwidth/rejection");

    // Values near a real bandwidth must not be silently snapped to it.
    CHECK_EQ_INT(lora_bw_from_hz(0), LORA_BW_INVALID);
    CHECK_EQ_INT(lora_bw_from_hz(1), LORA_BW_INVALID);
    CHECK_EQ_INT(lora_bw_from_hz(62628), LORA_BW_INVALID);
    CHECK_EQ_INT(lora_bw_from_hz(124999), LORA_BW_INVALID);
    CHECK_EQ_INT(lora_bw_from_hz(1000000), LORA_BW_INVALID);

    // Code 7 is reserved by the silicon and must not resolve.
    CHECK_EQ_INT(lora_hz_from_bw(7), 0);
    CHECK_EQ_INT(lora_hz_from_bw(11), 0);
    CHECK_EQ_INT(lora_hz_from_bw(255), 0);
}

typedef struct {
    uint8_t sf;
    uint8_t bw_code;
    uint8_t expect_ldro;
    const char* note;
} ldro_case_t;

static void test_ldro_matches_radiolib(void)
{
    SUITE("ldro/radiolib-rule");

    // RadioLib enables LDRO when the symbol duration reaches 16 ms:
    //   symbolLength = (1 << sf) / bandwidthKhz
    //
    // The cases marked "old rule was wrong" are the ones an SF-only or
    // SF11-and-62.5k-only test gets backwards, which silently breaks the link
    // rather than raising an error.
    static const ldro_case_t cases[] = {
        { 7,  3, 0, "SF7/BW62.5, symbol 2.05 ms" },
        { 8,  3, 0, "SF8/BW62.5, symbol 4.10 ms - the EU/UK narrow preset" },
        { 9,  4, 0, "SF9/BW125, symbol 4.10 ms" },
        { 10, 4, 0, "SF10/BW125, symbol 8.19 ms" },
        { 11, 4, 1, "SF11/BW125, symbol 16.38 ms - old rule was wrong" },
        { 11, 5, 0, "SF11/BW250, symbol 8.19 ms" },
        { 12, 5, 1, "SF12/BW250, symbol 16.38 ms - old rule was wrong" },
        { 11, 3, 1, "SF11/BW62.5, symbol 32.8 ms" },
        { 12, 3, 1, "SF12/BW62.5, symbol 65.5 ms" },
        { 11, 10, 1, "SF11/BW41.7, symbol 49.1 ms - old rule was wrong" },
        { 12, 6, 0, "SF12/BW500, symbol 8.19 ms" },
        { 5,  6, 0, "SF5/BW500, symbol 0.064 ms" },
        { 12, 0, 1, "SF12/BW7k, symbol 585 ms" },
        { 7,  4, 0, "SF7/BW125, symbol 1.02 ms" }
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        tu_checks++;

        const uint8_t got = lora_ldro_for(cases[i].sf, cases[i].bw_code);

        if (got != cases[i].expect_ldro) {
            tu_failures++;
            printf("  FAIL [%s] SF%u/BW%u: ldro=%u expected %u (%s)\n",
                   tu_suite, (unsigned)cases[i].sf,
                   (unsigned)lora_hz_from_bw(cases[i].bw_code),
                   (unsigned)got, (unsigned)cases[i].expect_ldro, cases[i].note);
        }
    }
}

static void test_ldro_rejects_bad_input(void)
{
    SUITE("ldro/input-validation");

    // An unknown bandwidth cannot produce a symbol time, so LDRO stays off
    // rather than guessing.
    CHECK_EQ_INT(lora_ldro_for(12, 7), 0);
    CHECK_EQ_INT(lora_ldro_for(12, 200), 0);

    // Out of range spreading factors are not silently accepted either.
    CHECK_EQ_INT(lora_ldro_for(4, 3), 0);
    CHECK_EQ_INT(lora_ldro_for(13, 3), 0);
    CHECK_EQ_INT(lora_ldro_for(0, 3), 0);
}

static void test_sync_word_is_the_meshcore_one(void)
{
    SUITE("sync-word");

    // RadioLib's RADIOLIB_SX126X_SYNC_WORD_PRIVATE, which is what MeshCore
    // passes to radio.begin(). If this ever drifts the modem joins a different
    // network and stops hearing MeshCore entirely, so pin it.
    //
    // Not 0x2B (Meshtastic) and not 0x34 (LoRaWAN public).
    CHECK_EQ_INT(MESHCORE_SYNC_WORD, 0x12);
}

int test_lora_params(void)
{
    RUN(test_bandwidth_round_trip);
    RUN(test_bandwidth_rejects_nonsense);
    RUN(test_ldro_matches_radiolib);
    RUN(test_ldro_rejects_bad_input);
    RUN(test_sync_word_is_the_meshcore_one);

    return tu_report("lora_params");
}
