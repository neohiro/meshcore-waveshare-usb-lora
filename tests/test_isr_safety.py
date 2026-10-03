"""Interrupt handlers must not touch the SPI bus.

`sx126x_hal.c` drives the radio with libopencm3's single-byte `spi_send` and
`spi_read` in a loop, not `spi_transfer`, which masks interrupts for the duration
of a transfer. That is fine as long as nothing that runs in interrupt context
also uses SPI: a handler that started a transfer mid-command would interleave
two conversations on one bus and corrupt a radio register write.

The symptom of getting this wrong is the worst kind: the radio works, mostly,
until an interrupt happens to land in the wrong microsecond. It reads as a flaky
SX1262 rather than as a concurrency bug, and it will not reproduce on the host,
where none of this code runs.

So this asserts the invariant structurally: no handler in the firmware's own
sources may call the SPI primitives or the radio HAL. It does not attempt to
prove interrupt latency is acceptable -- that needs hardware -- only that the
bus cannot be re-entered.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware" / "src"

# Names that mark a function as running in interrupt context. re.M matters:
# without it `^` anchors to the start of the file and no handler is ever found.
ISR_NAME = re.compile(
    r"^(?:void|static\s+void)\s+\w*(?:isr|ISR|handler|Handler)\w*\s*\(",
    re.M,
)

# Anything that would put bytes on the SPI bus, or drive the radio directly.
FORBIDDEN = re.compile(
    r"\b(?:spi_send|spi_read|spi_transfer|spi_write|sx126x_hal_\w+|sx126x_\w+)\b"
    r"|\bLORA_SPI\b"
)

# The handlers this firmware installs itself.
EXPECTED = ("exti0_isr", "usart1_isr")


def handler_bodies():
    """(name, body, filename) for every interrupt handler in firmware/src.

    FreeRTOS tasks are excluded even though their names contain "isr"
    (`radio_isr_task`). A task runs in task context, where calling the radio
    driver is exactly what should happen; only a hardware-invoked handler is
    restricted.
    """
    found = []

    for path in sorted(SRC.glob("*.c")):
        text = path.read_text(encoding="utf-8")

        for match in ISR_NAME.finditer(text):
            name = re.search(
                r"(\w+)\s*\(", text[match.start() : match.end()]
            ).group(1)

            if name.endswith("_task") or name.endswith("Task"):
                continue

            open_brace = text.find("{", match.end())
            if open_brace == -1:
                continue

            depth = 0
            for index in range(open_brace, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        found.append((name, text[open_brace : index + 1], path.name))
                        break

    return found


class InterruptHandlerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.handlers = handler_bodies()

    def test_the_handlers_were_found(self):
        """Without this the check below would pass by inspecting nothing."""
        names = {name for name, _, _ in self.handlers}

        self.assertGreaterEqual(
            len(self.handlers), 2, f"only found handlers: {sorted(names)}"
        )
        for expected in EXPECTED:
            with self.subTest(handler=expected):
                self.assertIn(
                    expected,
                    names,
                    "the handler this firmware installs is missing, so the "
                    "safety check is not looking at the real interrupt path",
                )

    def test_no_handler_uses_the_spi_bus(self):
        offenders = []
        for name, body, filename in self.handlers:
            # Strip comments so prose about SPI is not mistaken for a call.
            code = re.sub(r"//.*", "", body)
            code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
            if FORBIDDEN.search(code):
                offenders.append(f"{filename}: {name}")

        self.assertEqual(
            [],
            offenders,
            "these interrupt handlers touch SPI or the radio HAL. A handler "
            "that starts a transfer mid-command interleaves two "
            "conversations on one bus and corrupts a register write, which "
            f"looks like a flaky radio rather than a concurrency bug: {offenders}",
        )

    def test_the_radio_isr_only_notifies(self):
        """exti0_isr must hand off to the task, not do the work itself.

        The SX126x SPI conversation needs BUSY polling and multi-byte transfers.
        Doing any of that in interrupt context would extend the interrupt across
        a whole radio transaction, which is what makes these bugs intermittent.
        """
        for name, body, _ in self.handlers:
            if name != "exti0_isr":
                continue

            code = re.sub(r"//.*", "", body)
            self.assertRegex(
                code,
                r"xTaskNotifyFromISR|portYIELD_FROM_ISR",
                "exti0_isr should notify the radio task and return",
            )
            self.assertNotRegex(
                code,
                r"sx126x_get_irq_status|sx126x_clear_irq_status|sx126x_set_dio_irq_params",
                "reading or clearing SX126x registers belongs in the task, not "
                "in the interrupt handler",
            )
            return

        self.fail("exti0_isr was not found, so this check inspected nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
