#!/usr/bin/env python3
"""
Flash the Waveshare USB-TO-LoRa-xF over the GD32F103's built-in USART ISP
bootloader.

IMPORTANT: on this dongle the KEY button does NOT reach the ROM ISP bootloader.

Measured on real hardware (GD32F103C8T6, CH343 on USART1):

  * Holding KEY while powering up lights the LEDs and puts the dongle into
    Waveshare's *application-level* firmware update mode, which answers any
    input with the four bytes 3F 3E 01 0A. That is a proprietary handshake used
    by Waveshare's "DTU Update Tool" Windows binary.
  * No ROM ISP bootloader answers an ISP sync byte at any of 115200/230400/
    57600/38400/9600 with even, odd or no parity.
  * `detect` therefore times out, and `sweep` reports that the vendor
    application is still alive.

So this tool is kept because it is correct, fully unit tested against AN2606,
and useful for checking any variant that does expose the ROM ISP (some
Waveshare LoRa dongles do). To flash this firmware on the USB-TO-LoRa-xF you
need an ST-Link: see the README. Archie3d's notes reach the same conclusion,
which is why his bootloader is installed over SWD.

Usage:

    python tools/gd32_isp.py sweep
    python tools/gd32_isp.py detect
    python tools/gd32_isp.py flash firmware/firmware.bin -y
    python tools/gd32_isp.py verify firmware/firmware.bin
    python tools/gd32_isp.py optionbytes

Read protection: the shipped vendor firmware leaves the chip read-protected, and
writing requires clearing that, which erases the chip.
"""

import argparse
import binascii
import contextlib
import struct
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("This tool needs pyserial: pip install pyserial")


# --- USART ISP protocol constants -------------------------------------------

STM32_INIT = 0x7F
ACK = 0x79
NACK = 0x1F

CMD_GET = 0x00
CMD_GET_VERSION = 0x01
CMD_GET_ID = 0x02
CMD_READ_MEMORY = 0x03
CMD_GO = 0x04
CMD_WRITE_MEMORY = 0x05
CMD_WRITE_UNPROTECT = 0x09
CMD_EXTENDED_ERASE = 0x44

# Serial settings the bootloader expects.
BAUD = 115200

# Line settings worth probing when the bootloader does not answer at the
# default. Some GigaDevice ISP builds use 8N1 where AN2606 specifies even
# parity, and the ROM bootloader measures the incoming sync byte so any of
# these can work depending on how the part was configured.
BAUD_CANDIDATES = (115200, 230400, 57600, 38400, 9600)
PARITY_CANDIDATES = ("even", "odd", "none")

# GD32F103C8T6: 64 KiB flash in 1 KiB pages.
PAGE_SIZE = 1024
FLASH_SIZE = 64 * 1024
FLASH_ORIGIN = 0x08000000

# Medium-density STM32F103, high-density STM32F103 and the GD32 clone id.
EXPECTED_IDS = {0x410, 0x414, 0x430}


def xor_checksum(data):
    """XOR of every byte, as used by the STM32F1-family bootloader."""
    value = 0
    for b in data:
        value ^= b
    return value


class ISPError(RuntimeError):
    pass


class ISP:
    def __init__(self, port, timeout=5.0, log=print):
        self.log = log
        self.timeout = timeout
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
                write_timeout=timeout,
            )
        except serial.SerialException as exc:
            raise ISPError(f"cannot open {port}: {exc}") from exc

    def close(self):
        with contextlib.suppress(Exception):
            self.ser.close()

    # -- low level ----------------------------------------------------------

    def _read_exact(self, n):
        data = self.ser.read(n)
        if len(data) < n:
            raise ISPError(
                f"timeout waiting for {n} byte(s), got {len(data)}: "
                f"{binascii.hexlify(data).decode() or '<nothing>'}"
            )
        return data

    def _read_optional(self, n):
        """Read up to n bytes without raising if the device goes quiet."""
        out = b""
        deadline = time.time() + 0.5
        while len(out) < n and time.time() < deadline:
            chunk = self.ser.read(n - len(out))
            if not chunk:
                continue
            out += chunk
        return out

    def _cmd_ack(self, cmd, payload=b""):
        """Send a command and return its immediate ACK byte."""
        self.ser.write(bytes([STM32_INIT, cmd]) + payload)
        return self._read_exact(1)[0]

    def _wait_ready(self, timeout=30.0):
        """Poll GET until the bootloader ACKs again."""
        start = time.time()
        while True:
            self.ser.write(bytes([CMD_GET]))
            reply = self.ser.read(1)
            if reply and reply[0] == ACK:
                self.ser.reset_input_buffer()
                return
            if time.time() - start > timeout:
                raise ISPError("chip stayed busy")
            time.sleep(0.05)

    # -- protocol steps -----------------------------------------------------

    def sync(self, attempts=120):
        """
        Get the bootloader to acknowledge, retrying for roughly 30 seconds so
        there is time to plug the dongle in while holding KEY.
        """
        # Poll quickly: a blocking read on a silent port would otherwise make
        # each attempt take the full serial timeout.
        self.ser.timeout = 0.2
        try:
            for i in range(attempts):
                self.ser.write(bytes([STM32_INIT]))
                reply = self.ser.read(1)

                if reply and reply[0] in (ACK, NACK):
                    version = self._read_exact(2)
                    self.log(
                        f"  bootloader v{version[1]}.{version[0]} "
                        f"(after {i + 1} sync attempt(s))"
                    )
                    self.ser.reset_input_buffer()
                    return version

                time.sleep(0.05)
        finally:
            self.ser.timeout = self.timeout
            self.ser.write_timeout = self.timeout

        raise ISPError(
            "no response to ISP sync, so the chip is not in the ROM ISP "
            "bootloader.\n"
            "  On the USB-TO-LoRa-xF the KEY button opens Waveshare's own "
            "application-level\n"
            "  update mode instead, which this tool cannot speak. Use an "
            "ST-Link; see the\n"
            "  README. Run 'sweep' to confirm what the dongle is doing."
        )

    def get_id(self):
        if self._cmd_ack(CMD_GET_ID) != ACK:
            raise ISPError("GET_ID was not acknowledged")

        length = self._read_exact(1)[0]
        if length != 1:
            raise ISPError(f"unexpected GET_ID length byte 0x{length:02X}")

        ident = struct.unpack(">H", self._read_exact(2))[0]
        ack = self._read_exact(1)[0]
        if ack != ACK:
            raise ISPError(f"GET_ID trailer was 0x{ack:02X}")

        return ident

    def read_memory(self, address, length):
        """
        Read length bytes. On this family the byte after the length is a
        checksum placeholder, not a CRC.
        """
        payload = struct.pack(">I", address) + bytes([length - 1]) + b"\xff"
        if self._cmd_ack(CMD_READ_MEMORY, payload) != ACK:
            raise ISPError(f"READ_MEMORY at 0x{address:08X} was not acknowledged")

        data = self._read_exact(length)
        ack = self._read_exact(1)[0]
        if ack != ACK:
            raise ISPError(f"READ_MEMORY trailer was 0x{ack:02X}")

        return data

    def read_option_bytes(self):
        """Returns (number, value) from the option bytes at 0x1FFFF800."""
        n, value = struct.unpack(">HH", self.read_memory(0x1FFFF800, 4))
        return n, value

    def write_unprotect(self):
        """
        Clear read protection. On this family the chip mass-erases itself in
        response, so the vendor firmware does not survive.
        """
        self.log("  clearing read protection (this erases the chip)")
        if self._cmd_ack(CMD_WRITE_UNPROTECT) != ACK:
            raise ISPError("WRITE_UNPROTECT was not acknowledged")

        # The bootloader ACKs again once protection is off.
        self._read_optional(1)
        self._wait_ready(timeout=60)

    def mass_erase(self):
        self.log("  mass erasing flash")
        # 0xFFFF selects a mass erase; the trailing byte is its XOR checksum.
        body = b"\xff\xff"
        if self._cmd_ack(CMD_EXTENDED_ERASE, body + bytes([xor_checksum(body)])) != ACK:
            raise ISPError("EXTENDED_ERASE was not acknowledged")

        self._wait_ready(timeout=60)
        self.ser.reset_input_buffer()

    def write_page(self, address, data):
        """
        Program one page.

        AN2606's Write Memory sequence for a device without CRC is: ACK,
        command, address, length, checksum and data. The bootloader then
        acknowledges twice (once for the command block, once for the data),
        echoes back N, the command, the checksum and the address and data, and
        finally acknowledges once the page has been programmed.
        """
        if not 1 <= len(data) <= 256:
            raise ISPError(f"page must be 1..256 bytes, got {len(data)}")

        body = struct.pack(">I", address) + bytes([len(data) - 1]) + data
        payload = body + bytes([xor_checksum(body)])

        if self._cmd_ack(CMD_WRITE_MEMORY, payload) != ACK:
            raise ISPError(f"WRITE_MEMORY at 0x{address:08X} was not acknowledged")

        if self._read_exact(1)[0] != ACK:
            raise ISPError(
                f"WRITE_MEMORY at 0x{address:08X} rejected the data block"
            )

        # Echo is N, command, checksum, address, data.
        echo = self._read_exact(len(body) + 2)

        if echo[0] != len(data) - 1 or echo[3:7] != struct.pack(">I", address):
            raise ISPError(
                f"WRITE_MEMORY at 0x{address:08X} echoed an unexpected frame: "
                f"{binascii.hexlify(echo[:7]).decode()}"
            )

        ack = self._read_exact(1)[0]
        if ack != ACK:
            raise ISPError(
                f"WRITE_MEMORY at 0x{address:08X} failed (echo was "
                f"{binascii.hexlify(echo[:7]).decode()})"
            )

        # The final acknowledge means the page has been programmed, so there is
        # no busy poll to do here.

    def go(self, address=FLASH_ORIGIN):
        self._cmd_ack(CMD_GO, struct.pack(">I", address))


def load_image(path):
    with open(path, "rb") as fh:
        raw = fh.read()

    if len(raw) > FLASH_SIZE:
        raise ISPError(
            f"{path} is {len(raw)} bytes but the flash holds only {FLASH_SIZE}"
        )

    remainder = len(raw) % PAGE_SIZE
    padded = raw if remainder == 0 else raw + b"\xff" * (PAGE_SIZE - remainder)
    return raw, padded


def open_isp(args, retries=True):
    isp = ISP(args.port, log=print)

    if retries:
        print("Waiting up to 30s for a ROM ISP bootloader...")
        print("(On the USB-TO-LoRa-xF this never succeeds; see the module docstring.)")

    isp.sync()
    ident = isp.get_id()
    print(f"device id : 0x{ident:03X}")

    if ident not in EXPECTED_IDS:
        raise ISPError(
            f"device id 0x{ident:03X} is not a recognised STM32F103/GD32F103 "
            f"id (expected one of "
            + ", ".join(f"0x{i:03X}" for i in sorted(EXPECTED_IDS))
            + ")"
        )

    return isp, ident


def cmd_detect(args):
    try:
        isp, _ = open_isp(args)
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        n, value = isp.read_option_bytes()
        print(f"option bytes : n={n} value=0x{value:04X}")
        print(f"read protection : {'on' if value & 0x0001 else 'off'}")
        print("OK: the chip is in ISP bootloader mode and reachable")
        return 0
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        isp.close()


def cmd_optionbytes(args):
    try:
        isp, _ = open_isp(args, retries=False)
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        n, value = isp.read_option_bytes()
        print(f"option bytes : n={n} value=0x{value:04X}")
        print(f"read protection : {'on' if value & 0x0001 else 'off'}")
        print(f"software watchdog : {'on' if value & 0x0080 else 'off'}")
        print(f"readout unprotected : {'yes' if value & 0x0002 else 'no'}")
        return 0
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        isp.close()


def cmd_flash(args):
    try:
        raw, padded = load_image(args.image)
    except (ISPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        isp, _ = open_isp(args)
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        n, value = isp.read_option_bytes()
        protected = bool(value & 0x0001)
        print(f"option bytes : n={n} value=0x{value:04X} (read protection "
              f"{'on' if protected else 'off'})")

        if protected or args.force_unprotect:
            print("")
            print("The chip is read-protected. Clearing that erases the "
                  "vendor firmware")
            print("and cannot be undone; you can always restore it from "
                  "Waveshare's")
            print("DTU update tool if you change your mind.")

            if not args.assume_yes:
                reply = input("Erase and continue? [y/N] ")
                if reply.strip().lower() != "y":
                    print("aborted")
                    return 1

            isp.write_unprotect()
            print("  protection cleared")
            print("Reconnecting to the bootloader")
            isp.close()
            isp = ISP(args.port, log=print)
            isp.sync()
            isp.get_id()

        pages = len(padded) // PAGE_SIZE
        print(f"writing {len(raw)} bytes in {pages} page(s) to 0x{FLASH_ORIGIN:08X}")

        for i in range(pages):
            chunk = padded[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
            isp.write_page(FLASH_ORIGIN + i * PAGE_SIZE, chunk)
            if args.verbose:
                print(f"  page {i + 1}/{pages}")

        isp.go()
        print("done. The dongle restarts into the new firmware.")
        return 0
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        isp.close()


def cmd_verify(args):
    try:
        raw, _ = load_image(args.image)
    except (ISPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        isp, _ = open_isp(args, retries=False)
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        bad = 0
        for offset in range(0, len(raw), PAGE_SIZE):
            want = raw[offset:offset + PAGE_SIZE]
            got = isp.read_memory(FLASH_ORIGIN + offset, len(want))
            if got != want:
                bad += 1
                print(f"  mismatch at 0x{FLASH_ORIGIN + offset:08X}")
            elif args.verbose:
                print(f"  ok 0x{FLASH_ORIGIN + offset:08X}")

        if bad:
            print(f"error: {bad} page(s) differ", file=sys.stderr)
            return 1

        print(f"verified: all {len(raw)} bytes match")
        return 0
    except ISPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        isp.close()


def cmd_sweep(args):
    """
    Try every plausible serial configuration looking for an ISP bootloader,
    then check whether the vendor application is still alive.

    This answers the question that matters when `detect` finds nothing: is the
    chip in the ROM bootloader at some other line setting, or is it still
    running Waveshare's firmware in its own update mode?
    """
    import serial

    configs = [
        (baud, parity)
        for baud in BAUD_CANDIDATES
        for parity in PARITY_CANDIDATES
    ]

    print(f"sweeping {len(configs)} serial configurations on {args.port}\n")

    hits = []

    for baud, parity in configs:
        try:
            probe = serial.Serial(
                port=args.port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=getattr(serial, f"PARITY_{parity.upper()}"),
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=0.5,
            )
        except serial.SerialException as exc:
            print(f"cannot open {args.port}: {exc}", file=sys.stderr)
            return 1

        try:
            probe.reset_input_buffer()
            probe.reset_output_buffer()

            # The bootloader looks for a single 0x7F to sync on.
            probe.write(bytes([STM32_INIT]))
            time.sleep(0.3)

            reply = probe.read(64)
        finally:
            probe.close()

        if reply:
            printable = reply.hex()
            hits.append((baud, parity, reply))
            print(f"  {baud:>7} {parity:<4} -> {printable}")
        elif args.verbose:
            print(f"  {baud:>7} {parity:<4} -> (silent)")

    print("")

    if hits:
        print(f"something answered on {len(hits)} configuration(s):")
        for baud, parity, reply in hits:
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in reply)
            print(f"  {baud} {parity}: {reply.hex()}  |{text}|")
    else:
        print("nothing answered an ISP sync byte at any configuration")

    # If the vendor application still answers AT commands, the chip never left
    # the application and is waiting in Waveshare's own update mode.
    print("")
    alive = at_probe(args.port, args.at_baud)
    if alive:
        print("the vendor application still answers AT commands, so the chip is "
              "NOT in the ROM ISP bootloader")
        print("KEY selects Waveshare's application-level update mode, which "
              "speaks a\nproprietary handshake. See the README for what that "
              "means for flashing.")
    else:
        print("no AT response either, so the application is not running. If the "
              "chip is\npowered and held in update mode, use an ST-Link; see "
              "the README.")

    return 0


def at_probe(port, baud, settle=0.6):
    """Try to get an AT response from Waveshare's stock firmware."""
    import serial

    try:
        probe = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.3,
            write_timeout=1,
        )
    except serial.SerialException:
        return None

    try:
        probe.reset_input_buffer()
        probe.write(b"+++\r\n")
        time.sleep(settle)
        probe.write(b"AT+VER\r\n")
        time.sleep(settle)
        reply = probe.read(128)

        if reply:
            # Leave the DTU's command mode cleanly.
            probe.write(b"AT+EXIT\r\n")
            return reply

        return b""
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Flash a Waveshare USB-TO-LoRa-xF over the GD32F103 USART ISP "
            "bootloader."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-p", "--port", default="COM3", help="serial port (default: COM3)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="check whether the chip is in ISP bootloader mode")
    sub.add_parser("optionbytes", help="read and decode the option bytes")

    sweep = sub.add_parser(
        "sweep",
        help="try every serial configuration, and report whether the vendor "
             "application is still alive",
    )
    sweep.add_argument("--at-baud", type=int, default=115200)

    flash = sub.add_parser("flash", help="write firmware.bin to the chip")
    flash.add_argument("image", help="path to firmware.bin")
    flash.add_argument("-y", "--assume-yes", action="store_true",
                       help="skip the erase confirmation")
    flash.add_argument("--force-unprotect", action="store_true",
                       help="clear read protection even when it reports as off")

    verify = sub.add_parser(
        "verify", help="read the chip back and compare it with the image"
    )
    verify.add_argument("image", help="path to firmware.bin")

    args = parser.parse_args()

    handlers = {
        "detect": cmd_detect,
        "optionbytes": cmd_optionbytes,
        "sweep": cmd_sweep,
        "flash": cmd_flash,
        "verify": cmd_verify,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
