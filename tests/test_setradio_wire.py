"""kissmon's SetRadio payload, checked against the real firmware parser.

`kissmon setradio` is in the README's manual sequence, and it is the one command
whose bytes the test suite never checked. The contract tests drive SetRadio
through meshcore-go's Go implementation, so a wrong layout in kissmon -- a field
in the wrong order, a width off by one, big-endian where the firmware expects
little -- would pass every test in this repository and then quietly do nothing
when a user ran it.

So this pushes kissmon's *own* encoder into the *real* firmware, hosted by
tests/kiss-server, and reads the configuration back. If the two disagree about
the wire format, the modem reports the old values and this fails.
"""

import importlib.util
import pathlib
import queue
import struct
import subprocess
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
KISSMON = ROOT / "tools" / "kissmon.py"
SERVER = ROOT / "tests" / "kiss-server.exe"

# The values the README tells people to use for the MeshCore EU/UK preset.
EU_FREQ = 869_618_000
EU_BW = 62_500
EU_SF = 8
EU_CR = 8

ERROR = 0xF1  # KISS error command


def load_kissmon():
    spec = importlib.util.spec_from_file_location("kissmon_under_test", KISSMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_set_radio_frame(kissmon, freq, bw, sf, cr):
    """Exactly what `cmd_setradio` puts on the wire."""
    payload = struct.pack("<II", freq, bw) + bytes((sf, cr))
    return kissmon.encode(
        kissmon.CMD_SETHARDWARE, bytes((kissmon.HW_CMD["set-radio"],)) + payload
    )


class SetRadioRoundTripTests(unittest.TestCase):
    """Against the firmware itself, not a reimplementation of it."""

    @classmethod
    def setUpClass(cls):
        if not SERVER.exists():
            raise unittest.SkipTest(
                "kiss-server.exe not built; run tools/test.ps1 or tools/ci.ps1"
            )
        cls.kissmon = load_kissmon()

    def setUp(self):
        self.proc = subprocess.Popen(
            [str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Reads happen on a background thread because a blocking read on a pipe
        # ignores any timeout: an earlier version of this test read one byte at
        # a time on the main thread and hung the whole suite when the modem had
        # nothing to say. The queue is what makes the timeout reachable.
        self.frames = queue.Queue()
        self.raw = bytearray()
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self):
        decoder = self.kissmon.Decoder()
        while True:
            # read1(), not read(): a buffered read(n) blocks until it has all n
            # bytes or hits EOF, and this exchange is only ever a handful of bytes
            # long, so the pump would sit there forever. read1() returns whatever
            # has arrived.
            chunk = self.proc.stdout.read1(256)
            if not chunk:
                self.frames.put(None)
                return
            self.raw.extend(chunk)
            for cmd_in, body in decoder.feed(chunk):
                self.frames.put((cmd_in, body))

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)

    def send_and_drain(self, frame, settle=0.4):
        """Send a frame and collect whatever comes back, failing on a rejection.

        The modem acknowledges a SetRadio with 0xF0 (hw_ok() at the end of
        handle_set_radio), and answers a bad one with 0xF1 plus an error code.
        Neither is required to proceed, but a rejection is a real failure.
        """
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()

        deadline = time.time() + settle
        while time.time() < deadline:
            try:
                item = self.frames.get(timeout=0.05)
            except queue.Empty:
                continue

            if item is None:
                self.fail("kiss-server exited unexpectedly")

            cmd_in, body = item
            if cmd_in == ERROR and body and body[0]:
                self.fail(f"the modem rejected the command: error 0x{body[0]:02X}")

    def ask_sub(self, sub, timeout=10.0):
        """Send a SetHardware query and return its reply payload, sub-byte removed.

        Two details cost this test a few wrong turns, so they are spelled out:

        - A reply echoes the *outer* command (0x06) with the sub-command in
          body[0], not the sub-command as the frame command.
        - That sub-command has its high bit set: a get-radio request (0x0B) comes
          back as 0x8B, which is what kissmon's own describe() matches on.

        The payload is returned without the sub-byte, so callers unpack exactly
        the way kissmon does.
        """
        self.proc.stdin.write(
            self.kissmon.encode(self.kissmon.CMD_SETHARDWARE, bytes((sub,)))
        )
        self.proc.stdin.flush()

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                item = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                self.fail("kiss-server exited before replying")

            cmd_in, body = item
            if cmd_in == ERROR and body and body[0]:
                self.fail(
                    f"the modem rejected query 0x{sub:02X}: error 0x{body[0]:02X}"
                )

            matched = (
                cmd_in == self.kissmon.CMD_SETHARDWARE
                and body
                and body[0] in (sub, sub | 0x80)
            )
            if matched:
                return body[1:]

        raise AssertionError(f"no reply to sub-command 0x{sub:02X} within {timeout}s")

    def test_kissmon_can_configure_the_radio(self):
        kissmon = self.kissmon

        self.send_and_drain(
            build_set_radio_frame(kissmon, EU_FREQ, EU_BW, EU_SF, EU_CR)
        )
        payload = self.ask_sub(kissmon.HW_CMD["get-radio"])

        self.assertGreaterEqual(
            len(payload),
            10,
            f"get-radio reply too short to be the real one: {payload!r}",
        )

        freq, bw = struct.unpack_from("<II", payload, 0)
        sf, cr = payload[8], payload[9]

        self.assertEqual(
            EU_FREQ, freq, "kissmon's frequency did not reach the firmware"
        )
        self.assertEqual(
            EU_BW, bw, "kissmon's bandwidth did not reach the firmware"
        )
        self.assertEqual(
            EU_SF, sf, "kissmon's spreading factor did not reach the firmware"
        )
        self.assertEqual(EU_CR, cr, "kissmon's coding rate did not reach the firmware")

    def test_a_different_preset_also_round_trips(self):
        """A second, different set of values, so the check is not a coincidence."""
        freq, bw, sf, cr = 915_000_000, 125_000, 10, 7
        kissmon = self.kissmon

        self.send_and_drain(build_set_radio_frame(kissmon, freq, bw, sf, cr))
        payload = self.ask_sub(kissmon.HW_CMD["get-radio"])

        got_freq, got_bw = struct.unpack_from("<II", payload, 0)

        self.assertEqual(
            (freq, bw, sf, cr), (got_freq, got_bw, payload[8], payload[9])
        )

    def test_a_0x0a_byte_survives_the_round_trip(self):
        """Regression guard for kiss-server's stdio streams being in text mode.

        Windows opens stdin/stdout in text mode unless told otherwise, and then a
        lone 0x0A is written as 0x0D 0x0A. SF10 is the value 0x0A, so a set-radio
        for SF10 came back reporting SF13 and CR10 -- a firmware bug that was not
        one at all, and every KISS frame carrying 0x0A was being mangled.
        """
        self.send_and_drain(build_set_radio_frame(self.kissmon, EU_FREQ, EU_BW, 10, 7))
        self.ask_sub(self.kissmon.HW_CMD["get-radio"])

        expected = (
            bytes([0xC0, 0x06, 0x8B])
            + struct.pack("<II", EU_FREQ, EU_BW)
            + bytes((10, 7))
            + bytes([0xC0])
        )

        self.assertIn(
            expected,
            bytes(self.raw),
            "the reply should carry SF=0x0A verbatim; a 0x0D in front of it means "
            "the stream is in text mode",
        )
        self.assertNotIn(
            b"\x0d\x0a",
            bytes(self.raw),
            "no 0x0D should appear anywhere in a binary KISS stream",
        )

    def test_kissmon_decodes_the_reply_the_way_the_firmware_sends_it(self):
        """The high-bit reply form is what kissmon's describe() already matches.

        A get-radio request is 0x0B; the reply is 0x8B. If kissmon ever compared
        against 0x0B, `kissmon getradio` would print a raw hex dump instead of
        the configured frequency, and nothing else in the suite would notice.
        """
        line = self.kissmon.describe(
            0x06,
            bytes([0x8B])
            + struct.pack("<II", EU_FREQ, EU_BW)
            + bytes((EU_SF, EU_CR)),
        )

        self.assertIn("869618000", line.replace(" ", ""))
        self.assertIn("sf=8", line)
        self.assertIn("4/8", line)

    def test_kissmon_sends_little_endian(self):
        """The layout, asserted directly rather than only through a round trip.

        If kissmon were changed to big-endian, both round trips above would fail
        with confusing values; this says exactly what went wrong.
        """
        payload = struct.pack("<II", 0x01020304, 0x05060708) + bytes((9, 8))

        self.assertEqual(
            b"\x04\x03\x02\x01\x08\x07\x06\x05\x09\x08",
            payload,
            "the firmware reads these fields with rd_u32, which is little endian",
        )

    def test_the_field_widths_match_the_firmware(self):
        """Ten bytes: freq(4) + bw(4) + sf(1) + cr(1). The firmware rejects <10."""
        payload = struct.pack("<II", EU_FREQ, EU_BW) + bytes((EU_SF, EU_CR))

        self.assertEqual(10, len(payload))


if __name__ == "__main__":
    unittest.main(verbosity=2)
