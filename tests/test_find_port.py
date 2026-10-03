"""Which serial port would the setup script pick?

Port detection decides what the user has to type and what the bot connects to,
so the selection logic is tested directly with fake ports. No hardware is
involved, which is the point: the alternative is finding out it chose the wrong
port only after plugging the dongle in.
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIND_PORT = ROOT / "tools" / "find_port.py"


def load():
    spec = importlib.util.spec_from_file_location("find_port", FIND_PORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePort:
    """Stands in for serial.tools.list_common_ports.CommonPort."""

    def __init__(self, device, vid=None, pid=None):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.description = "fake"

    def __repr__(self):
        return f"FakePort({self.device!r}, {self.vid:#06x}, {self.pid:#06x})"


fp = load()


class ChooseTests(unittest.TestCase):
    def test_the_dongle_is_preferred_over_other_ports(self):
        ports = [
            FakePort("COM1", 0x0403, 0x6001),  # some other FTDI device
            FakePort("COM7", 0x1A86, 0x55D3),  # the dongle
            FakePort("COM4", 0x10C4, 0xEA60),  # a CP2102
        ]

        chosen, reason = fp.choose(ports)

        self.assertEqual("COM7", chosen.device)
        self.assertIn("CH343", reason)

    def test_nothing_attached_is_reported_clearly(self):
        chosen, reason = fp.choose([])

        self.assertIsNone(chosen)
        self.assertIn("no serial ports", reason)

    def test_one_unknown_port_is_still_chosen(self):
        # Better to try a plausible port than to refuse when there is only one
        # candidate; the bring-up tool will say clearly if it does not answer.
        ports = [FakePort("COM5")]

        chosen, reason = fp.choose(ports)

        self.assertEqual("COM5", chosen.device)
        self.assertIn("only serial port", reason)

    def test_several_unknown_ports_refuse_rather_than_guess(self):
        ports = [FakePort("COM5"), FakePort("COM6")]

        chosen, reason = fp.choose(ports)

        self.assertIsNone(chosen)
        self.assertIn("pass -Port", reason)

    def test_several_dongles_refuse_rather_than_guess(self):
        # Guessing here would silently attach the bot to the wrong radio.
        ports = [
            FakePort("COM3", 0x1A86, 0x55D3),
            FakePort("COM9", 0x1A86, 0x55D3),
        ]

        chosen, reason = fp.choose(ports)

        self.assertIsNone(chosen)
        self.assertIn("COM3", reason, "the message should name the candidates")
        self.assertIn("COM9", reason)


class DongleRecognitionTests(unittest.TestCase):
    def test_the_known_ids_are_recognised(self):
        for vid, pid in ((0x1A86, 0x55D3), (0x1A86, 0x55D4)):
            with self.subTest(vid=vid, pid=pid):
                self.assertTrue(fp.is_dongle(FakePort("COM3", vid, pid)))

    def test_other_vendors_are_rejected(self):
        for vid, pid in (
            (0x0403, 0x6001),
            (0x10C4, 0xEA60),
            (0x1A86, 0x7523),
            (0x2341, 0x0043),
        ):
            with self.subTest(vid=vid, pid=pid):
                self.assertFalse(fp.is_dongle(FakePort("COM3", vid, pid)))

    def test_a_port_without_ids_is_not_treated_as_the_dongle(self):
        self.assertFalse(fp.is_dongle(FakePort("COM3")))

    def test_describe_names_known_converters(self):
        self.assertEqual("CH343", fp.describe(FakePort("COM3", 0x1A86, 0x55D3)))
        self.assertEqual("", fp.describe(FakePort("COM3")))


class NonVacuityTests(unittest.TestCase):
    def test_the_pure_logic_is_reachable(self):
        """Guards against this module silently testing an empty thing."""
        for name in ("choose", "is_dongle", "describe"):
            with self.subTest(helper=name):
                self.assertTrue(callable(getattr(fp, name, None)))

    def test_choose_actually_returns_a_port_for_a_dongle(self):
        chosen, _ = fp.choose([FakePort("COM3", 0x1A86, 0x55D3)])
        self.assertIsNotNone(chosen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
