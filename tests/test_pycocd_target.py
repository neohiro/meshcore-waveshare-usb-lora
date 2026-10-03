"""
Guards the pyOCD target used to flash this dongle.

The failure mode being defended against is silent and expensive: if the flash
geometry declared here is wrong, pyOCD programs with the wrong block size and
corrupts the flash rather than reporting an error. The GD32F103C8 has 64 KiB in
1 KiB pages, while pyOCD only ships stm32f103rc for the family at 256 KiB in 2 KiB
pages, so the numbers in tools/pyocd/gd32f103c8t6.py have to be right.

These checks also tie the target back to the firmware itself, so the two cannot
drift apart unnoticed.
"""

import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "tools" / "pyocd" / "gd32f103c8t6.py"
LINKER = ROOT / "firmware" / "gd32f103.ld"
IMAGE = ROOT / "firmware" / "firmware.bin"

# From the GD32F103xC datasheet: 64 KiB flash in 1 KiB pages, 20 KiB SRAM.
EXPECTED_FLASH_BASE = 0x08000000
EXPECTED_FLASH_LENGTH = 0x10000
EXPECTED_FLASH_BLOCKSIZE = 0x400
EXPECTED_RAM_BASE = 0x20000000
EXPECTED_RAM_LENGTH = 0x5000


def load_target():
    """Imports the target module without needing a probe or a session."""
    if not TARGET.exists():
        raise AssertionError(f"missing pyOCD target at {TARGET}")

    spec = importlib.util.spec_from_file_location("gd32f103c8t6_target", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def linker_regions():
    """Reads the ORIGIN and LENGTH of the rom and ram regions."""
    text = LINKER.read_text(encoding="utf-8")

    out = {}
    for name in ("rom", "ram"):
        m = re.search(
            rf"\b{name}\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,\s*"
            rf"LENGTH\s*=\s*(\d+)\s*K",
            text,
        )
        if m is None:
            raise AssertionError(
                f"could not parse the {name} region from the linker script"
            )

        out[name] = (int(m.group(1), 0), int(m.group(2)) * 1024)

    return out


class PyOCDTargetGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pyocd  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(
                "pyocd is not installed: pip install pyocd"
            ) from exc

        cls.module = load_target()
        regions = cls.module.GD32F103C8T6.MEMORY_MAP.regions

        # Selected by class because pyOCD's region "type" is an enum.
        cls.flash = next(r for r in regions if type(r).__name__ == "FlashRegion")
        cls.ram = next(r for r in regions if type(r).__name__ == "RamRegion")


class FlashGeometryTests(PyOCDTargetGeometryTests):
    def test_flash_base_and_size(self):
        self.assertEqual(self.flash.start, EXPECTED_FLASH_BASE)
        self.assertEqual(self.flash.length, EXPECTED_FLASH_LENGTH)

    def test_page_size_is_1k_not_2k(self):
        # The whole reason this target exists. stm32f103rc uses 2 KiB pages, and
        # programming a 1 KiB-page part with a 2 KiB block size corrupts flash.
        self.assertEqual(self.flash.attributes["blocksize"], EXPECTED_FLASH_BLOCKSIZE)

    def test_flash_is_boot_memory(self):
        # pyOCD erases via the boot ROM, which requires the alias.
        self.assertTrue(self.flash.attributes["is_boot_memory"])

    def test_a_flash_algorithm_is_present(self):
        algo = self.flash.algo

        self.assertTrue(algo, "no flash algorithm, so programming would not work")

        # Reused from the STM32F1 target, which the GD32F103's flash controller
        # is register compatible with.
        self.assertGreater(len(algo["instructions"]), 50)

    def test_ram_matches_the_part(self):
        self.assertEqual(self.ram.start, EXPECTED_RAM_BASE)
        self.assertEqual(self.ram.length, EXPECTED_RAM_LENGTH)


class TargetMatchesFirmwareTests(PyOCDTargetGeometryTests):
    def test_flash_base_matches_the_linker_script(self):
        rom, _ = linker_regions()["rom"]

        self.assertEqual(
            self.flash.start, rom,
            "the pyOCD target and the firmware's linker script disagree on the "
            "flash base, so the program step would not land anywhere",
        )

    def test_flash_length_matches_the_linker_script(self):
        # This used to be excused: the linker script carried upstream's 112K
        # budget, which is a different part from this 64K one. That let the
        # linker accept an image the chip cannot hold, so the script now
        # declares 64K and the two are held to each other.
        _, rom_length = linker_regions()["rom"]

        self.assertEqual(
            self.flash.length, rom_length,
            "the pyOCD target and the linker script disagree about flash size; "
            "the linker would accept an image the flasher cannot write",
        )

    def test_flash_length_matches_the_part(self):
        self.assertEqual(self.flash.length, EXPECTED_FLASH_LENGTH)

    def test_ram_length_matches_the_linker_script(self):
        _, ram_length = linker_regions()["ram"]

        self.assertEqual(
            self.ram.length, ram_length,
            "the pyOCD target's RAM size differs from the linker script's",
        )

    def test_firmware_image_fits_the_declared_flash(self):
        # The check that prevents a bricked part: an image larger than the
        # region pyOCD believes it has would overflow the real part.
        if not IMAGE.exists():
            self.skipTest("firmware.bin not built yet")

        size = IMAGE.stat().st_size

        self.assertLessEqual(
            size, self.flash.length,
            f"firmware.bin is {size} bytes, which exceeds the "
            f"{self.flash.length} byte flash region declared to pyOCD",
        )

    def test_firmware_image_starts_with_a_valid_vector_table(self):
        if not IMAGE.exists():
            self.skipTest("firmware.bin not built yet")

        data = IMAGE.read_bytes()

        self.assertGreaterEqual(len(data), 8)

        initial_sp = int.from_bytes(data[0:4], "little")
        reset_vector = int.from_bytes(data[4:8], "little")

        self.assertGreaterEqual(initial_sp, 0x20000000)
        self.assertLessEqual(initial_sp, 0x20000000 + self.ram.length)
        self.assertEqual(
            reset_vector & 1, 1,
            "the reset vector must have the Thumb bit set",
        )

        # The reset handler must point inside the flash region.
        handler = reset_vector & ~1
        self.assertGreaterEqual(handler, self.flash.start)
        self.assertLess(handler, self.flash.start + self.flash.length)


if __name__ == "__main__":
    unittest.main(verbosity=2)
