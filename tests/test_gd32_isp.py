"""
Unit tests for the GD32F103 USART ISP flasher.

These run against a scripted fake serial port, so they verify the exact bytes
that go on the wire against AN2606 without needing the hardware. The bugs these
guard against (a wrong mass-erase selector, a checksum computed over the wrong
slice, a miscounted echo) are all things that silently corrupt a flash, so they
are worth pinning.
"""

import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

# Importable without PYTHONPATH, so the documented command works from the
# project root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

import gd32_isp  # noqa: E402

ACK = gd32_isp.ACK
NACK = gd32_isp.NACK
FLASH_ORIGIN = gd32_isp.FLASH_ORIGIN


class FakeSerial:
    """Scripted stand-in for serial.Serial."""

    def __init__(self, script=None, silent=False):
        self.script = bytearray(script or b"")
        self.writes = bytearray()
        self.silent = silent
        self.timeout = 0
        self.write_timeout = 0
        self.closed = False
        # Highest read timeout observed, so a test can prove the flasher polls
        # with a short timeout instead of blocking on a silent port.
        self.max_timeout_at_read = 0.0

    # -- serial API ------------------------------------------------------

    def write(self, data):
        if self.silent:
            return len(data)
        self.writes += bytes(data)
        return len(data)

    def read(self, size=1):
        self.max_timeout_at_read = max(self.max_timeout_at_read, self.timeout)
        if self.silent:
            return b""
        take = min(size, len(self.script))
        out = bytes(self.script[:take])
        del self.script[:take]
        return out

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def write_echo(address, data):
    """The frame a bootloader echoes back after accepting a page."""
    return (
        bytes([len(data) - 1, 0x05])
        + bytes([0x00])  # checksum, value irrelevant to the flasher
        + struct.pack(">I", address)
        + bytes(data)
    )


def make_isp(script=None, silent=False):
    """An ISP bound to a fake port, with the serial.Serial call stubbed out."""
    fake = FakeSerial(script=script, silent=silent)

    def factory(**kwargs):
        self_config = dict(kwargs)
        fake.timeout = self_config.get("timeout", 0)
        fake.write_timeout = self_config.get("write_timeout", 0)
        return fake

    with mock.patch.object(gd32_isp.serial, "Serial", factory):
        isp = gd32_isp.ISP("COM99", log=lambda *a: None)

    return isp, fake


class ChecksumTests(unittest.TestCase):
    def test_xor_is_over_every_byte(self):
        # A single byte XORs to itself.
        self.assertEqual(gd32_isp.xor_checksum(b"\xff"), 0xFF)
        # 0xFF ^ 0xFF ^ 0x00 == 0x00, which is the mass-erase selector checksum.
        self.assertEqual(gd32_isp.xor_checksum(b"\xff\xff"), 0x00)
        self.assertEqual(gd32_isp.xor_checksum(b"\x01\x02\x03"), 0x00)
        self.assertEqual(gd32_isp.xor_checksum(b"\x01\x02\x04"), 0x07)


class SyncTests(unittest.TestCase):
    def test_sync_reads_version_after_ack(self):
        isp, fake = make_isp(script=bytes([ACK, 0x00, 0x31]))
        isp.ser.timeout = 0.2
        version = isp.sync(attempts=1)
        self.assertEqual(version, bytes([0x00, 0x31]))
        self.assertEqual(fake.writes[0], 0x7F)

    def test_sync_accepts_nack_and_still_reads_version(self):
        isp, _ = make_isp(script=bytes([NACK, 0x00, 0x31]))
        self.assertEqual(isp.sync(attempts=1), bytes([0x00, 0x31]))

    def test_sync_gives_up_on_a_silent_port(self):
        isp, _ = make_isp(silent=True)
        with self.assertRaises(gd32_isp.ISPError) as ctx:
            isp.sync(attempts=2)
        message = str(ctx.exception)
        self.assertIn("ROM ISP bootloader", message)
        # The message has to point at the real alternative, otherwise a user
        # just retries the same button press that cannot work.
        self.assertIn("ST-Link", message)

    def test_sync_polls_with_a_short_timeout(self):
        # A silent port with the default 5s read timeout would turn each retry
        # into a 5s stall, which is how this flasher originally hung.
        isp, fake = make_isp(silent=True)
        with self.assertRaises(gd32_isp.ISPError):
            isp.sync(attempts=2)
        self.assertLessEqual(fake.max_timeout_at_read, 1.0)


class GetIdTests(unittest.TestCase):
    def test_request_frame_and_big_endian_id(self):
        isp, fake = make_isp(script=bytes([ACK, 0x01, 0x04, 0x10, ACK]))
        self.assertEqual(isp.get_id(), 0x0410)
        # 0x7F, GET_ID (0x02) and nothing else.
        self.assertEqual(bytes(fake.writes), bytes([0x7F, 0x02]))

    def test_unexpected_length_byte_is_rejected(self):
        isp, _ = make_isp(script=bytes([ACK, 0x02]))
        with self.assertRaises(gd32_isp.ISPError):
            isp.get_id()

    def test_bad_trailer_is_rejected(self):
        isp, _ = make_isp(script=bytes([ACK, 0x01, 0x04, 0x10, NACK]))
        with self.assertRaises(gd32_isp.ISPError):
            isp.get_id()


class ReadMemoryTests(unittest.TestCase):
    def test_request_layout_matches_an2606(self):
        payload = b"\xa5\x5a"
        isp, fake = make_isp(
            script=bytes([ACK]) + payload + bytes([ACK])
        )
        self.assertEqual(isp.read_memory(0x08001234, len(payload)), payload)

        self.assertEqual(
            bytes(fake.writes),
            bytes([0x7F, 0x03]) + struct.pack(">I", 0x08001234)
            + bytes([len(payload) - 1, 0xFF]),
        )

    def test_missing_trailer_ack_is_an_error(self):
        isp, _ = make_isp(script=bytes([ACK, 0xAA]))
        with self.assertRaises(gd32_isp.ISPError):
            isp.read_memory(0x08000000, 2)


class WritePageTests(unittest.TestCase):
    def test_frame_layout_and_checksum(self):
        data = bytes(range(8))
        isp, fake = make_isp(
            script=bytes([ACK, ACK]) + write_echo(FLASH_ORIGIN, data) + bytes([ACK])
        )
        isp.write_page(FLASH_ORIGIN, data)

        body = struct.pack(">I", FLASH_ORIGIN) + bytes([len(data) - 1]) + data
        self.assertEqual(
            bytes(fake.writes),
            bytes([0x7F, 0x05]) + body + bytes([gd32_isp.xor_checksum(body)]),
        )

    def test_reads_two_acks_and_a_correctly_sized_echo(self):
        # AN2606: ACK for the command block, ACK for the data block, then an
        # echo of N, command, checksum, address and data, then a final ACK.
        # Reading one byte short here desyncs every subsequent page.
        data = bytes(range(32))
        isp, fake = make_isp(
            script=bytes([ACK, ACK]) + write_echo(FLASH_ORIGIN, data) + bytes([ACK])
        )
        isp.write_page(FLASH_ORIGIN, data)

        self.assertEqual(fake.silent, False)
        self.assertEqual(len(fake.script), 0, "no unread response bytes left over")

    def test_missing_second_ack_is_an_error(self):
        isp, _ = make_isp(
            script=bytes([ACK, NACK]) + write_echo(FLASH_ORIGIN, b"\x01\x02")
        )
        with self.assertRaises(gd32_isp.ISPError) as ctx:
            isp.write_page(FLASH_ORIGIN, b"\x01\x02")
        self.assertIn("rejected the data block", str(ctx.exception))

    def test_echo_for_a_different_address_is_rejected(self):
        data = bytes(range(8))
        isp, _ = make_isp(
            script=bytes([ACK, ACK]) + write_echo(0x0800DEAD, data) + bytes([ACK])
        )
        with self.assertRaises(gd32_isp.ISPError) as ctx:
            isp.write_page(FLASH_ORIGIN, data)
        self.assertIn("unexpected frame", str(ctx.exception))

    def test_oversized_page_is_rejected_before_any_wire_traffic(self):
        isp, fake = make_isp()
        with self.assertRaises(gd32_isp.ISPError):
            isp.write_page(FLASH_ORIGIN, bytes(257))
        self.assertEqual(bytes(fake.writes), b"")

    def test_empty_page_is_rejected(self):
        isp, _ = make_isp()
        with self.assertRaises(gd32_isp.ISPError):
            isp.write_page(FLASH_ORIGIN, b"")

    def test_checksum_covers_address_length_and_payload(self):
        data = b"\x01\x02\x03"
        isp, fake = make_isp(
            script=bytes([ACK, ACK]) + write_echo(FLASH_ORIGIN, data) + bytes([ACK])
        )
        isp.write_page(FLASH_ORIGIN, data)

        frame = bytes(fake.writes)
        checksum = frame[-1]
        self.assertEqual(
            gd32_isp.xor_checksum(frame[2:-1]), checksum,
            "checksum must be the XOR of the address, length and payload",
        )
        # If the address were excluded the value would differ, so this also
        # pins that the address participates.
        self.assertNotEqual(gd32_isp.xor_checksum(frame[6:-1]), checksum)


class EraseTests(unittest.TestCase):
    def test_mass_erase_selects_all_pages_with_a_zero_checksum(self):
        isp, fake = make_isp(script=bytes([ACK, ACK]))
        isp.mass_erase()
        # 0xFFFF is the mass-erase selector; its XOR checksum is 0x00. A GET
        # poll follows, so only the leading frame is checked here.
        self.assertEqual(
            bytes(fake.writes)[:5],
            bytes([0x7F, 0x44, 0xFF, 0xFF, 0x00]),
        )

    def test_write_unprotect_uses_the_right_command(self):
        isp, fake = make_isp(script=bytes([ACK, ACK, ACK]))
        isp.write_unprotect()
        self.assertEqual(bytes(fake.writes)[:2], bytes([0x7F, 0x09]))


class OptionByteTests(unittest.TestCase):
    def test_reads_n_and_value_in_order(self):
        isp, _ = make_isp(
            script=bytes([ACK]) + struct.pack(">HH", 255, 0x00A5) + bytes([ACK])
        )
        n, value = isp.read_option_bytes()
        self.assertEqual(n, 255)
        self.assertEqual(value, 0x00A5)
        # Option bytes live in system memory.
        self.assertEqual(struct.pack(">I", 0x1FFFF800), b"\x1f\xff\xf8\x00")


class GoTests(unittest.TestCase):
    def test_jumps_to_the_flash_origin(self):
        isp, fake = make_isp(script=bytes([ACK]))
        isp.go()
        self.assertEqual(
            bytes(fake.writes),
            bytes([0x7F, 0x04]) + struct.pack(">I", FLASH_ORIGIN),
        )


class ImageTests(unittest.TestCase):
    def test_pads_to_a_page_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fw.bin")
            with open(path, "wb") as fh:
                fh.write(b"\x01\x02\x03")

            raw, padded = gd32_isp.load_image(path)
            self.assertEqual(raw, b"\x01\x02\x03")
            self.assertEqual(len(padded), gd32_isp.PAGE_SIZE)
            self.assertEqual(padded[:3], b"\x01\x02\x03")
            self.assertEqual(set(padded[3:]), {0xFF})

    def test_rejects_an_image_larger_than_the_flash(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fw.bin")
            with open(path, "wb") as fh:
                fh.write(b"\x00" * (gd32_isp.FLASH_SIZE + 1))

            with self.assertRaises(gd32_isp.ISPError):
                gd32_isp.load_image(path)

    def test_page_exact_image_is_not_padded(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fw.bin")
            with open(path, "wb") as fh:
                fh.write(b"\xAB" * gd32_isp.PAGE_SIZE)

            raw, padded = gd32_isp.load_image(path)
            self.assertEqual(len(raw), gd32_isp.PAGE_SIZE)
            self.assertEqual(len(padded), gd32_isp.PAGE_SIZE)


class ExpectedIdsTests(unittest.TestCase):
    def test_gd32f103_medium_density_id_is_accepted(self):
        # 0x410 is what a GD32F103C8T6 reports; if this set ever loses it the
        # flasher would refuse to touch the user's dongle.
        self.assertIn(0x410, gd32_isp.EXPECTED_IDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
