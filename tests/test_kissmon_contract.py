"""
Checks the bring-up tool against the firmware it is meant to talk to.

tools/kissmon.py is what gets run first, on a dongle that may or may not be
working. If it asks for a sub-command the modem does not implement, or cannot
name a response the modem actually sends, the output shows errors or bare hex
instead of a readable answer. That reads as "the hardware is dead" and sends
someone debugging the wrong thing entirely.

So this pins the two halves of that contract statically, against the firmware
headers rather than a device.
"""

import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
KISSMON = ROOT / "tools" / "kissmon.py"
KISS_H = ROOT / "firmware" / "src" / "kiss.h"


def defines(text, prefix):
    """All #define PREFIX_NAME 0xNN in a header."""
    found = {}
    pattern = rf"^#define\s+({prefix}[A-Z0-9_]+)\s+(0x[0-9A-Fa-f]+|\d+)\s*$"

    for name, value in re.findall(pattern, text, re.MULTILINE):
        found[name] = int(value, 0)

    return found


def load_kissmon():
    if not KISSMON.exists():
        raise AssertionError(f"missing {KISSMON}")

    spec = importlib.util.spec_from_file_location("kissmon_under_test", KISSMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class KissmonFirmwareContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kissmon = load_kissmon()

        if not KISS_H.exists():
            raise AssertionError(f"missing {KISS_H}")

        text = KISS_H.read_text(encoding="utf-8")

        cls.fw_cmd = defines(text, "HW_CMD_")
        cls.fw_resp = defines(text, "HW_RESP_")
        cls.fw_err = defines(text, "HW_ERR_")

    def request_side(self):
        return self.kissmon.HW_CMD

    def response_side(self):
        # The keys are the wire codes; the values are the display names.
        return set(self.kissmon.HW_RESP) | set(self.kissmon.ERR)

    def test_the_headers_were_parsed(self):
        # Otherwise every assertion below would pass vacuously.
        self.assertGreaterEqual(len(self.fw_cmd), 10)
        self.assertGreaterEqual(len(self.fw_resp), 8)

    def test_every_request_kissmon_sends_is_implemented(self):
        for name, value in sorted(self.request_side().items()):
            matching = [d for d, v in self.fw_cmd.items() if v == value]

            self.assertTrue(
                matching,
                f"kissmon can send {name!r} (0x{value:02X}) but the modem "
                "implements no sub-command with that value",
            )

    def test_request_names_line_up_with_the_firmware(self):
        # Guards against the tool drifting onto a valid code that means
        # something else entirely.
        for name in self.request_side():
            expected = "HW_CMD_" + name.upper().replace("-", "_")

            self.assertIn(
                expected,
                self.fw_cmd,
                f"kissmon's {name!r} does not correspond to {expected} in kiss.h",
            )

            self.assertEqual(
                self.request_side()[name],
                self.fw_cmd[expected],
                f"{name} and {expected} disagree on the sub-command value",
            )

    def test_every_response_the_modem_sends_is_named(self):
        named = self.response_side()

        for name, value in sorted(self.fw_resp.items()):
            self.assertIn(
                value,
                named,
                f"the modem can reply {name} (0x{value:02X}) but kissmon has no "
                "name for it, so it would print bare hex",
            )

    def test_every_error_the_modem_sends_is_named(self):
        named = self.response_side()

        for name, value in sorted(self.fw_err.items()):
            self.assertIn(
                value,
                named,
                f"the modem can reply {name} (0x{value:02X}) but kissmon has no "
                "name for it",
            )

    def test_info_covers_the_queries_the_modem_answers(self):
        # The bring-up command should exercise the diagnostic set.
        sent = set(self.request_side())

        for required in (
            "ping",
            "get-version",
            "get-device-name",
            "get-radio",
            "get-tx-power",
            "get-current-rssi",
            "get-noise-floor",
            "get-stats",
            "is-channel-busy",
        ):
            self.assertIn(
                required,
                sent,
                f"kissmon's info command no longer queries {required!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
