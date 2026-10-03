#!/usr/bin/env python3
"""
Minimal KISS client for bringing up the Waveshare USB-LoRa dongle.

Speaks the same dialect MeshCore's own KISS modem does (standard KA9Q/K3MC
framing plus the SetHardware 0x06 extension), so this doubles as a check that
the dongle will work with meshcore-go before the bot is configured.

Examples:

    python tools/kissmon.py info
    python tools/kissmon.py monitor
    python tools/kissmon.py setradio --freq 869618000 --bw 62500 --sf 8 --cr 8
    python tools/kissmon.py tx 48656c6c6f
    python tools/kissmon.py raw c000ff c0
"""

import argparse
import struct
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("This tool needs pyserial: pip install pyserial")


FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD

CMD_DATA = 0x00
CMD_TXDELAY = 0x01
CMD_PERSISTENCE = 0x02
CMD_SLOTTIME = 0x03
CMD_TXTAIL = 0x04
CMD_FULLDUPLEX = 0x05
CMD_SETHARDWARE = 0x06
CMD_RETURN = 0xFF

HW_CMD = {
    "get-random": 0x02,
    "set-radio": 0x09,
    "set-tx-power": 0x0A,
    "get-radio": 0x0B,
    "get-tx-power": 0x0C,
    "get-current-rssi": 0x0D,
    "is-channel-busy": 0x0E,
    "get-airtime": 0x0F,
    "get-noise-floor": 0x10,
    "get-version": 0x11,
    "get-stats": 0x12,
    "get-battery": 0x13,
    "get-mcu-temp": 0x14,
    "get-device-name": 0x16,
    "ping": 0x17,
    "get-signal-report": 0x1A,
}

HW_RESP = {
    0x81: "identity", 0x82: "random", 0x83: "verify", 0x84: "signature",
    0x85: "encrypted", 0x86: "decrypted", 0x87: "shared-secret", 0x88: "hash",
    0x8B: "radio", 0x8C: "tx-power", 0x8D: "current-rssi", 0x8E: "channel-busy",
    0x8F: "airtime", 0x90: "noise-floor", 0x91: "version", 0x92: "stats",
    0x93: "battery", 0x94: "mcu-temp", 0x95: "sensors", 0x96: "device-name",
    0x97: "pong", 0x99: "signal-report", 0xF0: "ok", 0xF1: "error",
    0xF8: "tx-done", 0xF9: "rx-meta",
}

ERR = {
    0x01: "invalid-length", 0x02: "invalid-param", 0x03: "no-callback",
    0x04: "mac-failed", 0x05: "unknown-cmd", 0x06: "encrypt-failed",
    0x07: "tx-busy",
}


def escape(payload):
    out = bytearray()
    for b in payload:
        if b == FEND:
            out += bytes((FESC, TFEND))
        elif b == FESC:
            out += bytes((FESC, TFESC))
        else:
            out.append(b)
    return bytes(out)


def encode(cmd, payload=b""):
    return bytes((FEND, cmd)) + escape(payload) + bytes((FEND,))


class Decoder:
    """Incremental KISS frame decoder."""

    def __init__(self):
        self.buf = bytearray()
        self.have_type = False
        self.type = 0
        self.escaping = False

    def _push(self, b):
        if self.have_type and len(self.buf) < 1024:
            self.buf.append(b)

    def feed(self, chunk):
        frames = []
        for b in chunk:
            if self.escaping:
                self.escaping = False
                if b == TFEND:
                    b = FEND
                elif b == TFESC:
                    b = FESC
                else:
                    # Invalid escape: abandon the rest of this frame.
                    self.buf.clear()
                    self.have_type = False
                    continue

                # An unescaped byte is always payload, never a delimiter.
                self._push(b)
                continue

            if b == FESC:
                self.escaping = True
                continue

            if b == FEND:
                if self.have_type:
                    frames.append((self.type, bytes(self.buf)))
                self.buf.clear()
                self.have_type = False
                continue

            if not self.have_type:
                self.type = b
                self.have_type = True
                continue

            self._push(b)
        return frames


class Reader(threading.Thread):
    """Reads the port in the background and dispatches decoded frames."""

    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser = ser
        self.frames = []
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.stop = threading.Event()

    def run(self):
        dec = Decoder()
        while not self.stop.is_set():
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if not chunk:
                continue
            for frame in dec.feed(chunk):
                with self.lock:
                    self.frames.append(frame)
                self.event.set()

    def take(self):
        with self.lock:
            out = self.frames
            self.frames = []
        self.event.clear()
        return out


def describe(cmd, payload):
    """Turn a decoded frame into a human-readable line."""
    if cmd != CMD_SETHARDWARE or not payload:
        return f"cmd 0x{cmd:02X} len={len(payload)} {payload.hex()}"

    sub = payload[0]
    body = payload[1:]
    name = HW_RESP.get(sub, f"0x{sub:02X}")

    if sub == 0xF1 and body:
        return f"error {ERR.get(body[0], f'0x{body[0]:02X}')}"

    if sub == 0x8B and len(body) >= 10:
        freq, bw = struct.unpack("<II", body[:8])
        sf, cr = body[8], body[9]
        return f"radio  freq={freq} Hz  bw={bw} Hz  sf={sf}  cr=4/{cr}"

    if sub == 0x8C and body:
        return f"tx-power {struct.unpack('b', body[:1])[0]} dBm"

    if sub == 0x8D and body:
        return f"current-rssi {struct.unpack('b', body[:1])[0]} dBm"

    if sub == 0x8E and body:
        return f"channel-busy {'yes' if body[0] else 'no'}"

    if sub == 0x8F and len(body) >= 4:
        return f"airtime {struct.unpack('<I', body[:4])[0]} ms"

    if sub == 0x90 and len(body) >= 2:
        return f"noise-floor {struct.unpack('<h', body[:2])[0]} dBm"

    if sub == 0x91 and body:
        return f"version 0x{body[0]:02X} ({body[0] >> 4}.{body[0] & 0xF})"

    if sub == 0x92 and len(body) >= 12:
        rx, tx, err = struct.unpack("<III", body[:12])
        return f"stats rx={rx} tx={tx} errors={err}"

    if sub == 0x93 and len(body) >= 2:
        return f"battery {struct.unpack('<H', body[:2])[0]} mV"

    if sub == 0xF8 and body:
        return f"tx-done {'success' if body[0] else 'failed (csma gave up or timeout)'}"

    if sub == 0xF9 and len(body) >= 2:
        snr_q, rssi = struct.unpack("<bb", body[:2])
        return f"rx-meta snr={snr_q / 4:.2f} dB  rssi={rssi} dBm"

    if sub == 0x99 and body:
        return f"signal-report {'on' if body[0] else 'off'}"

    if sub == 0x96:
        return f"device-name {body.decode('utf-8', 'replace')}"

    return f"{name} {body.hex()}"


def open_port(args):
    try:
        return serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
            write_timeout=2,
        )
    except serial.SerialException as exc:
        sys.exit(f"cannot open {args.port}: {exc}")


def command(ser, cmd, payload=b"", label=None, wait=2.0):
    """Send one frame and print every frame that comes back."""
    print(f"> {label or f'cmd 0x{cmd:02X}'}  {payload.hex()}")
    ser.write(encode(cmd, payload))

    deadline = time.time() + wait
    got = []
    while time.time() < deadline:
        time.sleep(0.02)
        while ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            for cmd_in, body in Decoder().feed(chunk):
                print(f"< {describe(cmd_in, body)}")
                got.append((cmd_in, body))
        if got:
            # one round trip is enough for the simple queries
            break
    return got


def cmd_info(args):
    ser = open_port(args)
    replies = 0
    try:
        ser.reset_input_buffer()
        print(f"port {args.port} @ {args.baud} 8N1\n")

        for name in ("ping", "get-version", "get-device-name", "get-radio",
                     "get-tx-power", "get-current-rssi", "get-noise-floor",
                     "get-stats", "is-channel-busy"):
            replies += len(
                command(ser, CMD_SETHARDWARE, bytes((HW_CMD[name],)))
            )
            print("")

        for length in (1, 8, 32):
            replies += len(
                command(ser, CMD_SETHARDWARE, bytes((HW_CMD["get-random"], length)))
            )

        print("")
        for length in (1, 64, 200):
            replies += len(
                command(ser, CMD_SETHARDWARE, bytes((HW_CMD["get-airtime"], length)))
            )
    finally:
        ser.close()

    # Exiting 0 here when the dongle said nothing at all is the worst outcome for
    # a diagnostic: the port opened, so it looks like the modem is healthy, and
    # the real cause -- still running the Waveshare firmware, or a firmware that
    # was never flashed -- gets missed.
    if not replies:
        print(
            "\nno replies at all. The port opened, but nothing answered. Either the\n"
            "dongle still runs the Waveshare firmware (flash it first), or this is\n"
            "not a MeshCore KISS modem on this port."
        )
        return 1

    print(f"\n{replies} replies")
    return 0


def cmd_setradio(args):
    ser = open_port(args)
    try:
        ser.reset_input_buffer()

        payload = struct.pack("<II", args.freq, args.bw) + bytes((args.sf, args.cr))
        print(f"set-radio freq={args.freq} bw={args.bw} sf={args.sf} cr=4/{args.cr}")
        command(ser, CMD_SETHARDWARE, bytes((HW_CMD["set-radio"],)) + payload)

        power = struct.pack("b", args.tx)
        print(f"set-tx-power {args.tx} dBm")
        command(ser, CMD_SETHARDWARE, bytes((HW_CMD["set-tx-power"],)) + power)

        if args.full_duplex:
            print("full-duplex on (CSMA bypassed)")
            ser.write(encode(CMD_FULLDUPLEX, b"\x01"))
            time.sleep(0.2)
        else:
            print("half-duplex with CSMA (txdelay 500ms, persistence 63, "
                  "slottime 100ms)")
            ser.write(encode(CMD_TXDELAY, b"\x32"))
            ser.write(encode(CMD_PERSISTENCE, b"\x3f"))
            ser.write(encode(CMD_SLOTTIME, b"\x0a"))
            ser.write(encode(CMD_FULLDUPLEX, b"\x00"))
            time.sleep(0.2)

        print("")
        command(ser, CMD_SETHARDWARE, bytes((HW_CMD["get-radio"],)))
        command(ser, CMD_SETHARDWARE, bytes((HW_CMD["get-tx-power"],)))
    finally:
        ser.close()


def cmd_monitor(args):
    ser = open_port(args)
    reader = Reader(ser)
    reader.start()

    print(f"listening on {args.port} @ {args.baud} (ctrl-c to stop)")
    if args.signal_report:
        ser.write(encode(CMD_SETHARDWARE, bytes((0x19, 0x01))))
        print("signal report enabled")

    # The modem sends a data frame first and the matching RxMeta second, so a
    # packet is held until its signal report arrives (or a timeout expires).
    pending = None
    pending_at = 0.0
    dropped_signal = 0

    def flush():
        nonlocal pending, pending_at, dropped_signal
        if pending is not None:
            size, body = pending
            print(f"  {size} bytes (no signal report): {body.hex()}")
            pending = None
            pending_at = 0.0

    def show(size, body, signal):
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} {size} bytes  {signal}  {body.hex()}")

    try:
        while True:
            now = time.time()
            if pending is not None and now - pending_at > args.meta_timeout:
                flush()

            for cmd_in, body in reader.take():
                if cmd_in == CMD_DATA:
                    if pending is not None:
                        dropped_signal += 1
                        flush()
                    pending = (len(body), bytes(body))
                    pending_at = time.time()

                elif cmd_in == CMD_SETHARDWARE and body and body[0] == 0xF9:
                    if len(body) >= 3:
                        snr_q, rssi = struct.unpack("<bb", body[1:3])
                        signal = f"snr={snr_q / 4:+.2f} dB rssi={rssi:4d} dBm"
                    else:
                        signal = "malformed rx-meta"
                    if pending is not None:
                        size, data = pending
                        show(size, data, signal)
                        pending = None
                        pending_at = 0.0
                    else:
                        print(f"{time.strftime('%H:%M:%S')} rx-meta with no packet "
                              f"({signal}) - missed data frame")
                elif args.verbose:
                    print(f"{time.strftime('%H:%M:%S')} {describe(cmd_in, body)}")
    except KeyboardInterrupt:
        flush()
        print("")
    finally:
        reader.stop.set()
        ser.close()

    if dropped_signal:
        print(f"note: {dropped_signal} packet(s) arrived without a "
              "usable signal report")


def cmd_tx(args):
    payload = bytes.fromhex(args.hex.replace(" ", ""))
    if not 1 <= len(payload) <= 255:
        sys.exit("payload must be 1..255 bytes")

    ser = open_port(args)
    try:
        ser.reset_input_buffer()
        print(f"tx {len(payload)} bytes: {payload.hex()}")
        ser.write(encode(CMD_DATA, payload))

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            time.sleep(0.02)
            while ser.in_waiting:
                for cmd_in, body in Decoder().feed(ser.read(ser.in_waiting)):
                    print(f"< {describe(cmd_in, body)}")
                    if cmd_in == CMD_SETHARDWARE and body and body[0] == 0xF8:
                        return 0
        print("timed out waiting for tx-done", file=sys.stderr)
        return 1
    finally:
        ser.close()


def cmd_raw(args):
    ser = open_port(args)
    try:
        ser.reset_input_buffer()
        for chunk in args.bytes:
            data = bytes.fromhex(chunk.replace(" ", ""))
            print(f"> {chunk}")
            ser.write(data)
            time.sleep(0.1)

        deadline = time.time() + 1.0
        dec = Decoder()
        while time.time() < deadline:
            time.sleep(0.02)
            waiting = ser.in_waiting
            if waiting:
                for cmd_in, body in dec.feed(ser.read(waiting)):
                    print(f"< {describe(cmd_in, body)}")
    finally:
        ser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--port", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "info", help="query every SetHardware command and print the replies")

    setradio = sub.add_parser(
        "setradio", help="configure frequency, bandwidth, SF, CR and power")
    setradio.add_argument("--freq", type=int, default=869618000)
    setradio.add_argument("--bw", type=int, default=62500)
    setradio.add_argument("--sf", type=int, default=8)
    setradio.add_argument(
        "--cr", type=int, default=8, help="coding rate denominator, 5..8")
    setradio.add_argument("--tx", type=int, default=17)
    setradio.add_argument("--full-duplex", action="store_true")

    monitor = sub.add_parser(
        "monitor", help="print received packets until interrupted")
    monitor.add_argument(
        "--no-signal-report", dest="signal_report",
        action="store_false", default=True)
    monitor.add_argument(
        "--meta-timeout", type=float, default=1.0,
        help="seconds to wait for a packet's signal report (default: 1.0)")

    tx = sub.add_parser("tx", help="transmit a raw payload given as hex")
    tx.add_argument("hex")
    tx.add_argument("--timeout", type=float, default=10.0)

    raw = sub.add_parser("raw", help="write literal bytes and print decoded frames")
    raw.add_argument("bytes", nargs="+")

    args = parser.parse_args()

    handlers = {
        "info": cmd_info,
        "setradio": cmd_setradio,
        "monitor": cmd_monitor,
        "tx": cmd_tx,
        "raw": cmd_raw,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
