"""
A pyOCD target for the GD32F103C8T6 on the Waveshare USB-TO-LoRa-HF dongle.

pyOCD ships only stm32f103rc for this family, which is the wrong part: 256 KiB
of flash in 2 KiB pages. This dongle has a GD32F103C8, so 64 KiB of flash in 1 KiB
pages. Programming with the wrong blocksize would corrupt the flash, so the
geometry is declared explicitly here rather than borrowed.

The flash algorithm itself is reused from the STM32F1 target: the GD32F103's
flash controller is register compatible, which is the whole basis on which it
clones the STM32F103.

Usage, with any SWD probe pyOCD supports (CMSIS-DAP, ST-Link):

    pyocd flash --target tools/pyocd/gd32f103c8t6.py firmware/firmware.bin

Wiring, from the board schematic and silkscreen (the pad block beside the USB
connector, labelled 3V3 / GND / SWDIO / SWCLK):

    probe 3V3   -> board 3V3
    probe GND   -> board GND
    probe SWDIO -> board SWDIO   (PA13)
    probe SWCLK -> board SWCLK   (PA14)
    probe NRST  -> board NRST    (optional, makes re-attaching easier)

"""

from pyocd.core.memory_map import FlashRegion, MemoryMap, RamRegion
from pyocd.coresight.coresight_target import CoreSightTarget
from pyocd.debug.svd.loader import SVDFile

# The STM32F1 flash algorithm and debug freeze registers. Reused rather than
# copied so this cannot drift from upstream.
from pyocd.target.builtin.target_STM32F103RC import (  # noqa: F401
    DBGMCU_CR,
    DBGMCU_VAL,
    FLASH_ALGO,
)


class GD32F103C8T6(CoreSightTarget):
    """GD32F103C8T6: 64 KiB flash in 1 KiB pages, 20 KiB SRAM."""

    VENDOR = "GigaDevice"

    MEMORY_MAP = MemoryMap(
        FlashRegion(
            start=0x08000000,
            length=0x10000,  # 64 KiB
            blocksize=0x400,  # 1 KiB pages
            is_boot_memory=True,
            algo=FLASH_ALGO,
        ),
        RamRegion(
            start=0x20000000,
            length=0x5000,  # 20 KiB
        ),
    )

    def __init__(self, session):
        super().__init__(session, self.MEMORY_MAP)
        self._svd_location = SVDFile.from_builtin("STM32F103xx.svd")

    def post_connect_hook(self):
        # Leave the core halted on connect, as the STM32F1 target does.
        self.write_memory(DBGMCU_CR, DBGMCU_VAL)
