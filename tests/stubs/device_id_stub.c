#include "device_id.h"

/*
 * Stand-in for the real silicon unique-ID read, which would fault on a host.
 * Fixed so GetRandom's output is reproducible across runs and the test can
 * assert that the stream is stable.
 */
uint32_t device_id_seed(void)
{
    return 0x12345678;
}
