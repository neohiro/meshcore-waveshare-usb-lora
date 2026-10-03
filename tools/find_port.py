"""Pick the serial port the dongle is on.

The bring-up tool and the bot both need to know which COM port to talk to, and
hard-coding COM3 is what the README used to do. That is wrong the moment
anything else claims COM3, and it is wrong on a machine where the dongle lands
on COM5.

This is a separate script rather than a PowerShell one-liner for two reasons:
it can be unit tested without hardware, and it avoids embedding Python in a
PowerShell string, where quoting has repeatedly gone wrong.

The Waveshare dongle's USB-UART is a CH343, so its USB IDs are preferred over
any other serial port present. When several CH343s are attached, the caller has
to disambiguate, so the script says so instead of guessing.
"""

import argparse
import sys

# CH343 USB-UART, the converter on the Waveshare USB-TO-LoRa-HF dongle.
CH343 = (0x1A86, 0x55D3)

# Other converters seen on boards that pair a LoRa radio with a USB chip.
KNOWN = {
    (0x1A86, 0x55D3): "CH343",  # the dongle's actual pair
    (0x1A86, 0x55D4): "CH343",
    (0x1A86, 0x7523): "CH340",
    (0x0403, 0x6001): "FT232",
    (0x10C4, 0xEA60): "CP2102",
}


def describe(port):
    """A short human label for a port."""
    ids = ((port.vid or 0), (port.pid or 0))
    return KNOWN.get(ids, "")


def is_dongle(port):
    """True for a port whose USB IDs are the dongle's CH343.

    The pid is checked loosely: the CH343 family reports several pids depending
    on the variant, and they all sit in the same 0x55Dx range.
    """
    if port.vid != CH343[0]:
        return False
    return (port.pid or 0) == CH343[1] or (port.pid or 0) in (0x55D3, 0x55D4)


def choose(ports):
    """Pick the best port, or None.

    Returns (port, reason). The reason is what the caller prints, so a wrong
    guess is explained rather than merely reported.
    """
    if not ports:
        return None, "no serial ports found at all"

    dongles = [p for p in ports if is_dongle(p)]
    if len(dongles) == 1:
        return dongles[0], "the dongle's CH343"
    if len(dongles) > 1:
        names = ", ".join(p.device for p in dongles)
        return None, f"several CH343 ports are attached ({names}); pass -Port"

    # Nothing recognisable. A single port is still the obvious guess.
    if len(ports) == 1:
        return ports[0], "the only serial port present"

    return None, (
        f"no CH343 (the dongle's USB-UART) among the {len(ports)} attached "
        "ports; run with --list to see them, or pass -Port"
    )


def list_ports():
    """Every serial port, as pyserial sees them."""
    try:
        from serial.tools import list_ports as pyserial_ports
    except ImportError:
        sys.exit("This tool needs pyserial: pip install pyserial")

    return list(pyserial_ports.comports())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list every port and exit")
    args = parser.parse_args(argv)

    ports = list_ports()

    if args.list:
        if not ports:
            print("no serial ports found")
            return 1
        for port in ports:
            label = describe(port)
            suffix = f"  {label}" if label else ""
            ids = ""
            if port.vid and port.pid:
                ids = f"  {port.vid:04X}:{port.pid:04X}"
            print(f"{port.device}{ids}{suffix}")
        return 0

    port, reason = choose(ports)
    if port is None:
        print(f"could not pick a port: {reason}", file=sys.stderr)
        print("run with --list to see what is attached", file=sys.stderr)
        return 1

    label = describe(port)
    suffix = f" ({label})" if label else ""
    print(f"{port.device}{suffix}  -- {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
