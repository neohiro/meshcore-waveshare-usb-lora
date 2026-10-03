#include "device_id.h"

// GD32F103 keeps a 96-bit unique device ID at this address, the same place an
// STM32F103 keeps it.
#define GD32_UID_BASE 0x1FFFF7E8

uint32_t device_id_seed(void)
{
    const volatile uint32_t* uid = (const volatile uint32_t*)GD32_UID_BASE;

    uint32_t seed = uid[0] ^ uid[1] ^ uid[2];

    // A seed of zero would leave the xorshift state stuck, so never return one.
    if (seed == 0) {
        seed = 0xA5C39E71;
    }

    return seed;
}
