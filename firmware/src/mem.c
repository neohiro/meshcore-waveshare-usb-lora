#include <stddef.h>
#include <stdint.h>

/*
 * The firmware links with -nostdlib, so the memory primitives GCC emits calls
 * to have to come from somewhere. These are the usual Cortex-M3 word-at-a-time
 * implementations; init.c clears SCB_CCR.UNALIGN_TRP so unaligned accesses are
 * safe on this part.
 */

void *memcpy(void *dest, const void *src, size_t n)
{
    uint8_t* d = (uint8_t*)dest;
    const uint8_t* s = (const uint8_t*)src;

    /* Align the destination to a word boundary before copying words. */
    while (n > 0 && ((uintptr_t)d & 3u) != 0) {
        *d++ = *s++;
        n--;
    }

    while (n >= 4) {
        *(uint32_t*)d = *(const uint32_t*)s;
        d += 4;
        s += 4;
        n -= 4;
    }

    while (n > 0) {
        *d++ = *s++;
        n--;
    }

    return dest;
}

void *memset(void *dest, int c, size_t n)
{
    uint8_t* d = (uint8_t*)dest;
    const uint8_t v = (uint8_t)c;

    while (n > 0 && ((uintptr_t)d & 3u) != 0) {
        *d++ = v;
        n--;
    }

    {
        const uint32_t word = (uint32_t)v * 0x01010101u;

        while (n >= 4) {
            *(uint32_t*)d = word;
            d += 4;
            n -= 4;
        }
    }

    while (n > 0) {
        *d++ = v;
        n--;
    }

    return dest;
}

void *memmove(void *dest, const void *src, size_t n)
{
    uint8_t* d = (uint8_t*)dest;
    const uint8_t* s = (const uint8_t*)src;

    if (d == s || n == 0) {
        return dest;
    }

    if (d < s) {
        return memcpy(dest, src, n);
    }

    /* Overlapping and copying downwards: work from the tail. */
    d += n;
    s += n;

    while (n > 0) {
        *--d = *--s;
        n--;
    }

    return dest;
}

/*
 * FreeRTOSConfig.h redirects memcpy to dbg_memcpy for instrumented builds, so
 * that entry point has to exist even though nothing calls it directly.
 */
void *dbg_memcpy(void *dest, const void *src, size_t n)
{
    return memcpy(dest, src, n);
}
