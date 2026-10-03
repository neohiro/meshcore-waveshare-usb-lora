"""
Keeps tools/flash-swd.ps1 in step with the pyOCD command line.

The script assembles a `pyocd flash` invocation by hand, so its flags can drift
away from what pyOCD actually accepts. Two defects slipped through once already:
a `--no-chip-erase` flag that does not exist at all, and reliance on pyOCD's
default erase mode, which is sector rather than chip, so a read-protected vendor
image could not be replaced.

Both are invisible until someone runs the script against real hardware, which is
the worst place to find out. These checks need no probe: they compare the flags
the script passes against pyOCD's own help output.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "flash-swd.ps1"


def script_code():
    if not SCRIPT.exists():
        raise AssertionError(f"missing {SCRIPT}")

    text = SCRIPT.read_text(encoding="utf-8")

    return "\n".join(
        line for line in (raw.split("#", 1)[0] for raw in text.splitlines())
        if line.strip()
    )


def pyocd_flash_help():
    """pyOCD's own help for the subcommand the script calls."""
    try:
        import pyocd  # noqa: F401
    except ImportError as exc:
        raise unittest.SkipTest("pyocd is not installed: pip install pyocd") from exc

    proc = subprocess.run(
        [sys.executable, "-m", "pyocd", "flash", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    return proc.stdout + proc.stderr


class FlashScriptCLIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = script_code()
        cls.help = pyocd_flash_help()

    def flags_passed_to_pyocd(self):
        # Only long options, and only those appended to the argument list.
        return sorted(set(re.findall(r"'(--[a-z0-9][a-z0-9-]*)'", self.code)))

    def test_every_flag_the_script_passes_exists(self):
        self.assertTrue(
            self.help.strip(),
            "could not read pyocd's help, so this guard would be vacuous",
        )

        for flag in self.flags_passed_to_pyocd():
            self.assertIn(
                flag,
                self.help,
                f"{flag} is not a pyocd option, so flash-swd.ps1 would be "
                "rejected by the command line parser",
            )

    def test_the_script_calls_flash(self):
        self.assertRegex(self.code, r"'flash'")

    def test_chip_erase_is_the_default(self):
        # pyOCD defaults to sector erase. This dongle ships read-protected, and
        # the flash algorithm needs a mass erase to clear that, so the default
        # here must ask for a chip erase explicitly.
        self.assertIn("chip", self.code)

        default_path = self.code.split("if ($SectorErase)")[-1]
        self.assertIn(
            "'chip'",
            default_path,
            "the default path must request a chip erase, not fall back to "
            "pyOCD's sector default",
        )

    def test_the_sector_opt_out_is_opt_in(self):
        self.assertIn(
            "$SectorErase",
            self.code,
            "keeping flash contents should require asking for it explicitly",
        )

    def test_a_read_protection_hint_is_given(self):
        # If pyOCD cannot clear read protection, the fallback is CubeProgrammer,
        # and the operator should be told rather than left guessing.
        self.assertIn("read", self.code.lower())
        self.assertIn("CubeProgrammer", self.code)

    def test_it_looks_for_a_probe_before_flashing(self):
        """pyOCD blocks forever when no probe is attached.

        It prints "Waiting for a debug probe to be connected..." and never
        returns, so an unplugged or unseated probe presents as a frozen script
        rather than a missing cable. This was measured: the unguarded version sat
        there for ten minutes before it was interrupted.
        """
        check = self.code.find("pyocd list")
        flash = self.code.find("& pyocd @pyocdArgs")

        self.assertNotEqual(
            -1,
            check,
            "the script must enumerate probes first; pyocd does not time out",
        )
        self.assertLess(
            check,
            flash,
            "the probe check has to happen before the flashing call, not after",
        )

    def test_the_no_probe_error_says_what_to_do(self):
        self.assertIn(
            "No SWD probe",
            self.code,
            "the failure must name the problem",
        )
        for pad in ("3V3", "GND", "SWDIO", "SWCLK"):
            with self.subTest(pad=pad):
                self.assertIn(
                    pad,
                    self.code,
                    f"the wiring instructions must mention {pad}",
                )


class NoProbeBehaviourTests(unittest.TestCase):
    """Run the flash script when nothing is plugged in, to prove it fails fast.

    Gated on pyocd actually reporting no probes, because if a probe *is*
    attached this script would erase the chip -- which is exactly what it is for,
    and emphatically not something a test should do to someone's hardware.
    """

    SCRIPT = ROOT / "tools" / "flash-swd.ps1"
    TIMEOUT = 120

    @classmethod
    def setUpClass(cls):
        if not cls.SCRIPT.exists():
            raise unittest.SkipTest("flash-swd.ps1 not present")
        if shutil.which("powershell") is None:
            raise unittest.SkipTest("PowerShell not available")

        try:
            import pyocd  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(
                "pyocd is not installed: pip install pyocd"
            ) from exc

        probes = subprocess.run(
            [sys.executable, "-m", "pyocd", "list"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        if re.search(r"DAP|STLink|ST-LINK|CMSIS", probes):
            raise unittest.SkipTest("a debug probe is attached; refusing to run")

    def test_it_fails_in_seconds_rather_than_hanging(self):
        start = time.time()
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.SCRIPT),
                "-Profile",
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=self.TIMEOUT,
        )
        elapsed = time.time() - start

        self.assertNotEqual(0, result.returncode, "a missing probe must fail")
        self.assertLess(
            elapsed,
            self.TIMEOUT / 2,
            f"took {elapsed:.0f}s; pyocd blocks indefinitely when no probe is "
            "attached, so the pre-flight check is what keeps this fast",
        )
        self.assertIn(
            "No SWD probe",
            result.stdout + result.stderr,
            "the failure must say no probe was found",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
