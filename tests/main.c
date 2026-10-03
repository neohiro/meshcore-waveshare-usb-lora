#include "test_kiss_frame.h"
#include "test_kiss_modem.h"
#include "test_lora_params.h"
#include "test_pa_config.h"

#include <stdio.h>

int main(void)
{
    // Unbuffered, so output written before a crash is not lost.
    setvbuf(stdout, NULL, _IONBF, 0);

    printf("meshcore-waveshare-usb-lora native tests\n");
    printf("-----------------------------\n\n");

    int failures = 0;

    failures += test_kiss_frame();
    printf("\n");
    failures += test_kiss_modem();
    printf("\n");
    failures += test_lora_params();
    printf("\n");
    failures += test_pa_config();

    printf("\n");
    if (failures == 0) {
        printf("ALL NATIVE TESTS PASSED\n");
    } else {
        printf("%d SUITE(S) FAILED\n", failures);
    }

    return failures == 0 ? 0 : 1;
}
