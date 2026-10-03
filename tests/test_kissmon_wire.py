"""kissmon's wire encodings, checked against the real firmware.

Every command in `kissmon info` -- the one a new user runs first -- puts bytes on
the wire that nothing in this repository checked. The contract tests drive the
same SetHardware commands through meshcore-go's Go implementation, so a wrong
layout in kissmon -- a field in the wrong order, a width off by one, big-endian
where the firmware expects little -- would pass every test here and then quietly
do nothing, or something subtly wrong, when a user ran it.

So this pushes kissmon's *own* encoder into the *real* firmware, hosted by
tests/kiss-server, and reads the result back. If the two disagree about the wire
format, the modem reports the old values and this fails.

SetRadio is the motivating case, and the one whose failure looked exactly like a
firmware bug: SF10 is the byte 0x0A, and asking for it made the modem report
SF13 and CR10 until kiss-server's Windows text mode was found rewriting 0x0A as
0x0D 0x0A. Alongside it: signed transmit power, the random-length bounds, and an
airtime wider than 16 bits.
"""

import importlib.util
import pathlib
import queue
import re
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


class KissmonAgainstFirmwareTests(unittest.TestCase):
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

        # Killing the process ends the pipe, so the pump thread sees EOF and
        # returns; only then is it safe to close its end. Closing earlier raises
        # inside the thread, which surfaces as a ResourceWarning in the output of
        # an otherwise passing run.
        self.proc.wait(timeout=10)
        self.reader.join(timeout=5)

        if self.proc.stdout is not None:
            self.proc.stdout.close()

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
            code = self.error_code(cmd_in, body)
            if code is not None:
                self.fail(f"the modem rejected the command: error 0x{code:02X}")

    def error_code(self, cmd_in, body):
        """The error code from a rejection, or None.

        A rejection arrives as SetHardware with sub-command 0xF1 followed by the
        code, so the frame's own command byte is 0x06 like every other reply --
        looking for 0xF1 as the command finds nothing, which is why the first
        version of this harness sat waiting for a rejection that had already
        arrived.
        """
        if (
            cmd_in == self.kissmon.CMD_SETHARDWARE
            and len(body) >= 2
            and body[0] == ERROR
        ):
            return body[1]
        return None

    def ask_sub(self, sub, payload=b"", timeout=10.0):
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
            self.kissmon.encode(
                self.kissmon.CMD_SETHARDWARE, bytes((sub,)) + payload
            )
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
            code = self.error_code(cmd_in, body)
            if code is not None:
                self.fail(
                    f"the modem rejected query 0x{sub:02X}: error 0x{code:02X}"
                )

            matched = (
                cmd_in == self.kissmon.CMD_SETHARDWARE
                and body
                and body[0] in (sub, sub | 0x80)
            )
            if matched:
                return body[1:]

        raise AssertionError(f"no reply to sub-command 0x{sub:02X} within {timeout}s")

    def expect_error(self, sub, payload=b"", timeout=10.0):
        """Send a command that should be rejected and return the error code.

        The firmware validates its inputs, and a rejection is part of the
        contract with kissmon: it has to arrive as a 0xF1 carrying a code, not as
        silence and not as a plausible-looking reply.
        """
        self.proc.stdin.write(
            self.kissmon.encode(self.kissmon.CMD_SETHARDWARE, bytes((sub,)) + payload)
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
            code = self.error_code(cmd_in, body)
            if code is not None:
                return code

        raise AssertionError(
            f"sub-command 0x{sub:02X} should have been rejected, but nothing "
            f"came back"
        )

    def set_tx_power(self, dbm):
        """Exactly what kissmon's `set-tx-power` puts on the wire."""
        self.send_and_drain(
            self.kissmon.encode(
                self.kissmon.CMD_SETHARDWARE,
                bytes((self.kissmon.HW_CMD["set-tx-power"],))
                + struct.pack("b", dbm),
            )
        )
        return self.ask_sub(self.kissmon.HW_CMD["get-tx-power"])

    def test_set_tx_power_round_trips_including_negative_values(self):
        """The wire carries a signed byte, and the modem has to agree.

        struct.pack("b", ...) is signed on purpose: transmit power is routinely
        negative, and an unsigned read would turn -3 dBm into 253.
        """
        for dbm in (17, 0, -3, -17):
            power = self.set_tx_power(dbm)

            self.assertEqual(
                1,
                len(power),
                f"get-tx-power should carry one byte, got {power!r}",
            )
            self.assertEqual(
                dbm,
                struct.unpack("b", power)[0],
                f"the modem did not keep {dbm} dBm",
            )

    def test_get_random_returns_exactly_what_was_asked_for(self):
        """The lengths kissmon's `info` command sends, plus the maximum."""
        for length in (1, 8, 32, 64):
            payload = self.ask_sub(
                self.kissmon.HW_CMD["get-random"], bytes((length,))
            )

            self.assertEqual(
                length,
                len(payload),
                f"asked for {length} random bytes and got {len(payload)}",
            )

    def test_get_random_rejects_lengths_outside_one_to_64(self):
        """The firmware's own bounds, checked from the outside.

        It answers a bad length with an error rather than silence, and it has
        to: kissmon reports the error to the user, so an empty reply would leave
        `kissmon info` looking like a modem that had stopped answering.
        """
        for length in (0, 65, 255):
            code = self.expect_error(
                self.kissmon.HW_CMD["get-random"], bytes((length,))
            )

            self.assertEqual(
                0x02,
                code,
                f"length {length} is out of range, so expect INVALID_PARAM",
            )

    def test_get_airtime_replies_with_four_bytes(self):
        """A uint32, so a payload long enough to take over a minute still fits.

        255 bytes at SF8 on a 62.5 kHz channel is several minutes of air time,
        well past what a uint16 would hold, and the host stub always answers
        100 ms, so only the width is checked here. The decoding is checked
        separately, below, where the value can be made large on purpose.
        """
        for length in (1, 64, 200):
            payload = self.ask_sub(
                self.kissmon.HW_CMD["get-airtime"], bytes((length,))
            )

            self.assertEqual(
                4,
                len(payload),
                f"airtime for {length} bytes should be a uint32, got {payload!r}",
            )

    def test_airtime_over_65535_ms_is_not_wrapped(self):
        """A value a uint16 would have truncated, decoded the way kissmon does.

        Built by hand rather than asked of the modem, because the radio stub
        answers a fixed 100 ms and so cannot produce one. If kissmon ever unpacked
        two bytes here, this reports 3 ms instead of 267000 and nothing else in
        the suite would notice.
        """
        airtime_ms = 267_000
        reply = (
            bytes([0x8F])
            + struct.pack("<I", airtime_ms)
        )

        line = self.kissmon.describe(self.kissmon.CMD_SETHARDWARE, reply)

        self.assertIn(str(airtime_ms), line)
        self.assertNotIn("3704", line, "that is 267000 modulo 65536, i.e. wrapped")

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


class KissmonTablesMatchTheFirmwareTests(unittest.TestCase):
    """kissmon's lookup tables, compared with the firmware's own headers.

    Both tables are hand-maintained on two sides of the repo, and nothing
    connected them. Get one wrong and the symptom is silence rather than an
    error: a command sent under the wrong id is answered with 0xF1 unknown-cmd,
    or not answered at all, and a mislabelled error code prints a confident,
    wrong explanation for whatever actually went wrong.

    The comparison runs one way on purpose. kissmon exposes a subset of what the
    firmware can do, which is fine; what must not happen is kissmon offering a
    command under an id the firmware does not use, or renaming an error the
    firmware can send.
    """

    @classmethod
    def setUpClass(cls):
        cls.kissmon = load_kissmon()

        header = (ROOT / "firmware" / "src" / "kiss.h").read_text(encoding="utf-8")

        cls.commands = {
            name.lower().replace("_", "-"): int(value, 16)
            for name, value in re.findall(
                r"#define\s+HW_CMD_(\w+)\s+(0x[0-9A-Fa-f]+)", header
            )
        }
        cls.errors = {
            name.lower().replace("_", "-"): int(value, 16)
            for name, value in re.findall(
                r"#define\s+HW_ERR_(\w+)\s+(0x[0-9A-Fa-f]+)", header
            )
        }

    def test_the_headers_were_parsed(self):
        self.assertTrue(self.commands, "no HW_CMD_ constants were read from kiss.h")
        self.assertTrue(self.errors, "no HW_ERR_ constants were read from kiss.h")

    def test_every_command_kissmon_offers_uses_the_firmware_id(self):
        wrong = {
            name: (hex(value), hex(self.commands[slug]))
            for name, value in self.kissmon.HW_CMD.items()
            if (slug := name.lower().replace("_", "-")) in self.commands
            and self.commands[slug] != value
        }

        self.assertEqual(
            {},
            wrong,
            "kissmon would send these under the wrong command id, and the modem "
            "would answer unknown-cmd",
        )

    def test_every_command_kissmon_offers_exists_in_the_firmware(self):
        unknown = sorted(set(self.kissmon.HW_CMD) - set(self.commands))

        self.assertEqual(
            [],
            unknown,
            "kissmon offers commands the firmware does not define, so they would "
            "be answered with unknown-cmd",
        )

    def test_every_error_kissmon_names_matches_the_firmware_code(self):
        wrong = {
            name: (value, hex(self.errors[name]))
            for value, name in self.kissmon.ERR.items()
            if name in self.errors and self.errors[name] != value
        }

        self.assertEqual(
            {},
            wrong,
            "kissmon would report these errors under the wrong code, so the "
            "explanation it prints would be for a different problem",
        )

    def test_every_error_the_firmware_can_send_is_named(self):
        unnamed = sorted(
            slug
            for slug, value in self.errors.items()
            if value not in self.kissmon.ERR
        )

        self.assertEqual(
            [],
            unnamed,
            "the firmware can send these errors and kissmon has no name for "
            "them, so it would print a bare hex code",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
