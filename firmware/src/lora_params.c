#include "lora_params.h"

// SX126x bandwidth enumerants. These values are register encodings, not the
// bandwidths themselves, and 7 is left unused by the silicon.
#define BW_007 0
#define BW_010 8
#define BW_015 1
#define BW_020 9
#define BW_031 2
#define BW_041 10
#define BW_062 3
#define BW_125 4
#define BW_250 5
#define BW_500 6

#define BW_RESERVED 7

// RadioLib enables low data rate optimization once a symbol takes 16 ms or
// more: float symbolLength = (float)(1 << sf) / (float) bandwidthKhz.
#define LDRO_SYMBOL_LIMIT_MS 16

uint8_t lora_bw_from_hz(uint32_t hz)
{
    switch (hz) {
    case 7000:   return BW_007;
    case 10400:  return BW_010;
    case 15600:  return BW_015;
    case 20300:  return BW_020;
    case 31250:  return BW_031;
    case 41700:  return BW_041;
    case 62500:  return BW_062;
    case 125000: return BW_125;
    case 250000: return BW_250;
    case 500000: return BW_500;
    default:     return LORA_BW_INVALID;
    }
}

uint32_t lora_hz_from_bw(uint8_t code)
{
    switch (code) {
    case BW_007: return 7000;
    case BW_010: return 10400;
    case BW_015: return 15600;
    case BW_020: return 20300;
    case BW_031: return 31250;
    case BW_041: return 41700;
    case BW_062: return 62500;
    case BW_125: return 125000;
    case BW_250: return 250000;
    case BW_500: return 500000;
    default:     return 0;
    }
}

uint8_t lora_ldro_for(uint8_t sf, uint8_t bw_code)
{
    const uint32_t hz = lora_hz_from_bw(bw_code);

    if (hz == 0 || sf < 5 || sf > 12) {
        return 0;
    }

    // Symbol duration in milliseconds: 2^SF / BW, arranged to stay inside a
    // 32-bit accumulator for SF12 at 7 kHz.
    const uint32_t symbol_ms = (uint32_t)(1u << sf) * 1000u / hz;

    return (symbol_ms >= LDRO_SYMBOL_LIMIT_MS) ? 1 : 0;
}
