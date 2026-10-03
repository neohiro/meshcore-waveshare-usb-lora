"""
Guards the bot configuration against the firmware it has to match.

Nothing else checks bot/config.toml, so a wrong frequency, a bandwidth the
SX1262 cannot produce, or a baud rate that differs from the firmware would all
fail silently: the modem would simply never hear the mesh.

The limits are read out of the firmware headers rather than restated here, so
this cannot drift away from the code it is checking.
"""

import pathlib
import re
import unittest

import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE_SRC = ROOT / "firmware" / "src"
CONFIG = ROOT / "bot" / "config.toml"


def header_define(name, header="radio.h"):
    """
    Reads a simple numeric #define from a firmware header. Tolerates parentheses
    around negative values, as in "#define PA_MIN_POWER_DBM (-17)".
    """
    text = (FIRMWARE_SRC / header).read_text(encoding="utf-8")
    m = re.search(
        rf"^\s*#define\s+{re.escape(name)}\s*\(?\s*(-?0x[0-9A-Fa-f]+|-?\d+)\s*\)?",
        text,
        re.MULTILINE,
    )
    if m is None:
        raise AssertionError(f"{name} not found in {header}")

    return int(m.group(1), 0)


def load_config():
    if not CONFIG.exists():
        raise AssertionError(f"missing {CONFIG}")

    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


# The bandwidths the SX126x can produce, in Hz. Must mirror lora_params.c.
VALID_BANDWIDTHS = {
    7000, 10400, 15600, 20300, 31250, 41700, 62500, 125000, 250000, 500000
}

# SX126x bandwidth enum -> Hz, so the firmware's initialiser can be compared with
# the config's kHz value.
ENUM_BANDWIDTH_HZ = {
    "SX126X_LORA_BW_007": 7000,
    "SX126X_LORA_BW_010": 10400,
    "SX126X_LORA_BW_015": 15600,
    "SX126X_LORA_BW_020": 20300,
    "SX126X_LORA_BW_031": 31250,
    "SX126X_LORA_BW_062": 62500,
    "SX126X_LORA_BW_125": 125000,
    "SX126X_LORA_BW_250": 250000,
    "SX126X_LORA_BW_500": 500000,
}

ENUM_SPREADING_FACTOR = {
    f"SX126X_LORA_SF{n}": n for n in range(5, 13)
}


def radio_c_text():
    path = FIRMWARE_SRC / "radio.c"
    if not path.exists():
        raise AssertionError(f"missing {path}")
    return path.read_text(encoding="utf-8")


def firmware_boot_config():
    """The frequency, power and modulation the radio comes up with.

    Read out of radio.c rather than restated, so this cannot drift from the code.
    A freshly flashed dongle applies these before it ever sees a SetRadio
    command, which is what lets `kissmon monitor` receive straight away without
    anyone running set-radio first.
    """
    text = radio_c_text()

    freq = re.search(r"#define\s+DEFAULT_FREQ\s+(\d+)", text)
    power = re.search(r"#define\s+DEFAULT_POWER\s+(-?\d+)", text)
    if freq is None or power is None:
        raise AssertionError("DEFAULT_FREQ / DEFAULT_POWER not found in radio.c")

    block = re.search(
        r"static\s+sx126x_mod_params_lora_t\s+lora_mod_params\s*=\s*\{(.*?)\}",
        text,
        re.S,
    )
    if block is None:
        raise AssertionError("could not find the lora_mod_params initialiser")

    body = block.group(1)
    sf = re.search(r"\.sf\s*=\s*(\w+)", body)
    bw = re.search(r"\.bw\s*=\s*(\w+)", body)
    cr = re.search(r"\.cr\s*=\s*(\w+)", body)
    if not (sf and bw and cr):
        raise AssertionError("lora_mod_params does not set sf, bw and cr")

    # SX126X_LORA_CR_4_8 is coding rate 4/8, and the config stores 4 and 8 too.
    cr_match = re.fullmatch(r"SX126X_LORA_CR_(\d+)_(\d+)", cr.group(1))
    if cr_match is None:
        raise AssertionError(f"unrecognised coding rate {cr.group(1)}")

    return {
        "frequency": int(freq.group(1)),
        "tx_power": int(power.group(1)),
        "spreading_factor": ENUM_SPREADING_FACTOR[sf.group(1)],
        "bandwidth": ENUM_BANDWIDTH_HZ[bw.group(1)],
        "coding_rate": int(cr_match.group(2)),
    }


class BootDefaultsMatchConfigTests(unittest.TestCase):
    """The firmware's power-on radio settings must equal the bot's.

    The bot sends SetRadio when it connects, so a mismatch here would not stop
    the bot working -- it would leave the dongle unusable for anything else.
    Right after a flash, before any bot exists, radio.c's initialisers are the
    only thing in effect: someone running `kissmon monitor` to watch the mesh
    would be listening on the wrong frequency and see nothing, with no way to
    tell that from a broken antenna.
    """

    def setUp(self):
        self.boot = firmware_boot_config()
        self.config = load_config()

    def test_frequency_matches(self):
        config_hz = int(round(float(self.config["freq"]) * 1_000_000))
        self.assertEqual(
            self.boot["frequency"],
            config_hz,
            "the firmware boots at a different frequency from bot/config.toml",
        )

    def test_tx_power_matches(self):
        self.assertEqual(self.boot["tx_power"], self.config["tx"])

    def test_spreading_factor_matches(self):
        self.assertEqual(self.boot["spreading_factor"], self.config["sf"])

    def test_bandwidth_matches(self):
        config_hz = int(round(float(self.config["bw"]) * 1000))
        self.assertEqual(
            self.boot["bandwidth"],
            config_hz,
            "the firmware boots at a different bandwidth from bot/config.toml",
        )

    def test_coding_rate_matches(self):
        self.assertEqual(self.boot["coding_rate"], self.config["cr"])

    def test_the_boot_defaults_are_a_real_meshcore_preset(self):
        """Sanity: the pair has to be MeshCore EU/UK narrow, not just equal."""
        self.assertEqual(869_618_000, self.boot["frequency"])
        self.assertEqual(8, self.boot["spreading_factor"])
        self.assertEqual(62_500, self.boot["bandwidth"])
        self.assertEqual(8, self.boot["coding_rate"])

    def test_the_boot_config_is_actually_applied_before_any_command(self):
        """The defaults are only useful if the chip is told about them at boot."""
        text = radio_c_text()

        self.assertRegex(
            text,
            r"sx126x_set_rf_freq\s*\(\s*NULL\s*,\s*frequency\s*\)",
            "radio.c never applies the default frequency to the SX1262",
        )
        self.assertRegex(
            text,
            r"sx126x_set_lora_mod_params\s*\(\s*NULL\s*,\s*&lora_mod_params\s*\)",
            "radio.c never applies the default modulation to the SX1262",
        )


class ConfigMatchesFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_connection_is_serial(self):
        # The dongle runs the KISS modem, so the bot must use the serial scheme.
        # An "openhop" or "spi" scheme would talk a dialect it does not speak.
        conn = self.cfg.get("connection", "")
        self.assertTrue(
            conn.startswith("serial://"),
            f"connection is {conn!r}; the KISS modem needs serial://",
        )

    def test_baud_rate_matches_the_firmware(self):
        self.assertEqual(
            self.cfg.get("baudRate"),
            header_define("SERIAL_BAUD", "serial.h"),
            "config baud rate differs from the firmware's SERIAL_BAUD",
        )

    def test_frequency_is_inside_the_sx1262_range(self):
        # config.toml uses MHz; the firmware works in Hz.
        freq_mhz = self.cfg.get("freq")
        self.assertIsInstance(freq_mhz, float)
        freq_hz = round(freq_mhz * 1_000_000)
        self.assertGreaterEqual(freq_hz, 150_000_000)
        self.assertLessEqual(freq_hz, 960_000_000)

    def test_bandwidth_is_one_the_sx126x_can_produce(self):
        bw_khz = self.cfg.get("bw")
        self.assertIsInstance(bw_khz, float)
        bw_hz = round(bw_khz * 1000)
        self.assertIn(
            bw_hz, VALID_BANDWIDTHS,
            f"{bw_hz} Hz is not an SX126x bandwidth; the modem would ignore it",
        )

    def test_spreading_factor_and_coding_rate_are_in_range(self):
        sf = self.cfg.get("sf")
        cr = self.cfg.get("cr")

        self.assertGreaterEqual(sf, 5)
        self.assertLessEqual(sf, 12)
        # The wire carries the coding rate denominator, 5..8.
        self.assertGreaterEqual(cr, 5)
        self.assertLessEqual(cr, 8)

    def test_tx_power_is_within_the_amplifier_range(self):
        tx = self.cfg.get("tx")
        self.assertGreaterEqual(tx, header_define("PA_MIN_POWER_DBM", "pa_config.h"))
        self.assertLessEqual(tx, header_define("PA_MAX_POWER_DBM", "pa_config.h"))

    def test_duty_cycle_is_set_for_eu_868(self):
        # Above 868 MHz the EU band allows 1%. Leaving it unset makes the bot
        # transmit continuously enough to get it jammed or sanctioned.
        duty = self.cfg.get("dutyCycle")
        self.assertIsNotNone(duty, "dutyCycle is not set; EU868 requires a limit")
        self.assertGreater(duty, 0)
        self.assertLessEqual(duty, 1)

    def test_config_has_at_least_one_bot_with_a_trigger(self):
        bots = self.cfg.get("bot")
        self.assertTrue(bots, "no [[bot]] defined")
        self.assertTrue(
            any(b.get("trigger") for b in bots),
            "no bot has a trigger, so the bot would never answer anything",
        )

    def test_triggers_name_a_channel_and_a_match(self):
        for bot in self.cfg.get("bot", []):
            for trigger in bot.get("trigger", []):
                self.assertTrue(
                    trigger.get("channels"),
                    f"trigger in bot {bot.get('name')!r} has no channels",
                )
                self.assertTrue(
                    trigger.get("match"),
                    f"trigger in bot {bot.get('name')!r} has no match pattern",
                )


class SyncWordIsDocumentedTests(unittest.TestCase):
    def test_config_documents_the_sync_word_the_firmware_uses(self):
        # A comment drift here costs hours of on-air debugging: a wrong sync
        # word produces a modem that hears Meshtastic but never MeshCore.
        word = header_define("MESHCORE_SYNC_WORD")
        text = CONFIG.read_text(encoding="utf-8")

        self.assertIn(
            f"0x{word:02X}", text,
            f"config.toml does not mention the firmware's sync word 0x{word:02X}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
