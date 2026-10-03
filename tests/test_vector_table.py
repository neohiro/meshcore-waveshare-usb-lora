"""The linked vector table must point at real handlers.

Every other test in this project runs the firmware's logic on the host, against
stubs for the USART, the radio and FreeRTOS. That is the right way to test
protocol behaviour, and it has one blind spot: it never sees the vector table
that the chip will actually boot through.

So the failure modes here are invisible everywhere else. A handler whose name
does not match the one in the vector table is silently dead. A vector pointing
at the weak catch-all means that interrupt never happens. Both leave a modem
that answers commands over serial and then never receives a single packet --
exactly the "looks alive but never communicates" outcome this project exists to
avoid.

These assertions read the built ELF and image, so they are skipped when the
firmware has not been built yet.
"""

import glob
import pathlib
import re
import shutil
import struct
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
IMAGE = FIRMWARE / "firmware.bin"
ELF = FIRMWARE / "firmware.elf"
LINKER = FIRMWARE / "gd32f103.ld"
PINOUT = FIRMWARE / "src" / "pinout.h"
NVIC_HEADER = (
    FIRMWARE
    / "libopencm3"
    / "include"
    / "libopencm3"
    / "stm32"
    / "f1"
    / "nvic.h"
)

# ARMv7-M vector table: the first entries are system exceptions, and external
# interrupts start at 0x40 indexed by IRQ number.
IRQ0_OFFSET = 0x40
EXTERNAL_IRQ_COUNT = 16

# The exceptions that must reach real code. If any of these lands on the weak
# catch-all handler, the chip cannot start or the RTOS cannot run.
REQUIRED_HANDLERS = {
    0x04: "reset_handler",
    0x2C: "sv_call_handler",
    0x38: "pend_sv_handler",
    0x3C: "sys_tick_handler",
}


def find_nm():
    """Locate the toolchain's nm, on Windows or POSIX."""
    found = shutil.which("arm-none-eabi-nm")
    if found:
        return found

    matches = sorted(
        glob.glob(
            str(ROOT / "toolchain" / "**" / "arm-none-eabi-nm*"), recursive=True
        )
    )
    if matches:
        return matches[0]

    raise unittest.SkipTest("arm-none-eabi-nm not found; build the firmware first")


def load_symbols():
    """Map address (Thumb bit cleared) to the set of symbol names there."""
    nm = find_nm()

    try:
        output = subprocess.run(
            [nm, "--numeric-sort", str(ELF)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise unittest.SkipTest(f"could not run nm: {exc}") from exc

    symbols = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            address = int(parts[0], 16)
        except ValueError:
            continue
        symbols.setdefault(address & ~1, set()).add(parts[2])

    return symbols


def linker_region(name):
    """(origin, length) for a MEMORY region in the linker script."""
    match = re.search(
        rf"\b{name}\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+)\s*,\s*"
        r"LENGTH\s*=\s*(\d+)\s*K",
        LINKER.read_text(encoding="utf-8"),
    )
    if match is None:
        raise AssertionError(
            f"could not parse the {name} region from the linker script"
        )
    return int(match.group(1), 0), int(match.group(2)) * 1024


def di1_nvic_irq():
    """The NVIC interrupt number behind the radio's DIO1 line.

    This takes two hops on purpose. pinout.h names a libopencm3 NVIC constant
    such as NVIC_EXTI0_IRQ, but that constant is *not* the EXTI line number:
    libopencm3 numbers the shared interrupt lines, so EXTI0 is interrupt 6,
    EXTI1 is 7, and EXTI5_10 is 23. Reading the digits out of the constant's
    name gives the wrong vector slot, so the real number is taken from
    libopencm3's own header.
    """
    header = PINOUT.read_text(encoding="utf-8")
    match = re.search(r"#define\s+LORA_DIO1_IRQ\s+(NVIC_\w+_IRQ)", header)
    if match is None:
        raise AssertionError("could not find LORA_DIO1_IRQ in pinout.h")
    constant = match.group(1)

    numbers = {
        name: int(value)
        for name, value in re.findall(
            r"#define\s+(NVIC_\w+_IRQ)\s+(\d+)",
            NVIC_HEADER.read_text(encoding="utf-8"),
        )
    }
    if constant not in numbers:
        raise AssertionError(f"{constant} is not defined in {NVIC_HEADER.name}")

    return constant, numbers[constant]


class VectorTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.exists() or not ELF.exists():
            raise unittest.SkipTest("firmware not built yet")

        cls.data = IMAGE.read_bytes()
        cls.symbols = load_symbols()
        cls.dio1_constant, cls.dio1_irq = di1_nvic_irq()

    def vector(self, offset):
        (value,) = struct.unpack_from("<I", self.data, offset)
        return value

    def handler_names(self, offset):
        return self.symbols.get(self.vector(offset) & ~1, set())

    def describe(self, offset):
        names = sorted(self.handler_names(offset))
        return ", ".join(names) if names else "no symbol"

    def test_initial_stack_pointer_is_the_top_of_sram(self):
        # The stack grows down from here, so it must start at the very top of
        # RAM and be 8-byte aligned as the ARMv7-M ABI requires.
        ram_origin, ram_length = linker_region("ram")
        initial_sp = self.vector(0x00)

        self.assertEqual(ram_origin + ram_length, initial_sp)
        self.assertEqual(0, initial_sp % 8, "the stack pointer must be 8-byte aligned")

    def test_reset_reaches_the_reset_handler(self):
        self.assertIn("reset_handler", self.handler_names(0x04))

    def test_freertos_exceptions_reach_their_handlers(self):
        # SVC is how the RTOS starts a task, PendSV is where context switches
        # happen and SysTick drives the tick. Any of these landing on the weak
        # catch-all means the scheduler never runs.
        for offset, expected in REQUIRED_HANDLERS.items():
            with self.subTest(offset=f"0x{offset:02x}", expected=expected):
                self.assertIn(
                    expected,
                    self.handler_names(offset),
                    f"vector 0x{offset:02x} points at {self.describe(offset)}; "
                    "the interrupt would be swallowed by the catch-all handler",
                )

    def test_radio_interrupt_reaches_its_isr(self):
        """DIO1 must land on the ISR that drains the SX126x.

        pinout.h chooses which NVIC interrupt the radio's DIO1 uses; this
        asserts that the vector table for that interrupt is wired to the
        handler in radio.c. The two are easy to desynchronise by changing the
        pin, and nothing else in the project can see the difference.
        """
        offset = IRQ0_OFFSET + 4 * self.dio1_irq

        self.assertIn(
            "exti0_isr",
            self.handler_names(offset),
            f"DIO1 is on {self.dio1_constant} (IRQ {self.dio1_irq}, vector "
            f"0x{offset:02x}) but that vector points at "
            f"{self.describe(offset)}; radio interrupts would never be "
            "serviced and nothing would ever be received",
        )

    def test_thumb_bit_is_set_on_every_vector(self):
        offsets = list(REQUIRED_HANDLERS) + [
            IRQ0_OFFSET + 4 * n for n in range(EXTERNAL_IRQ_COUNT)
        ]

        for offset in offsets:
            with self.subTest(offset=f"0x{offset:02x}"):
                self.assertEqual(
                    1, self.vector(offset) & 1, "handlers are Thumb functions"
                )

    def test_unused_interrupts_share_one_catch_all_handler(self):
        """Every unused IRQ must point at the same weak handler.

        This is what proves the table was linked coherently: a misnamed handler
        would show up here as a stray address that is not the catch-all, which
        is the only way to notice an ISR the linker could not resolve.
        """
        used_offsets = set(REQUIRED_HANDLERS) | {IRQ0_OFFSET + 4 * self.dio1_irq}

        unused = {
            self.vector(offset) & ~1
            for offset in (
                IRQ0_OFFSET + 4 * n for n in range(EXTERNAL_IRQ_COUNT)
            )
            if offset not in used_offsets
        }

        self.assertEqual(
            1,
            len(unused),
            "unused IRQ vectors should all share the weak catch-all handler, "
            f"but they point at {len(unused)} different places",
        )

    def test_every_handler_lies_inside_the_image(self):
        """A handler beyond the flashed bytes would jump into erased flash."""
        rom_origin, _ = linker_region("rom")
        top = rom_origin + len(self.data)

        offsets = list(REQUIRED_HANDLERS) + [
            IRQ0_OFFSET + 4 * n for n in range(EXTERNAL_IRQ_COUNT)
        ]

        for offset in offsets:
            handler = self.vector(offset) & ~1
            with self.subTest(offset=f"0x{offset:02x}"):
                self.assertGreaterEqual(handler, rom_origin)
                self.assertLess(
                    handler, top, f"handler at 0x{handler:08x} is outside the image"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
