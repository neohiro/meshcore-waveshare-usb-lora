"""Every native suite must actually be run.

tests/main.c registers its suites by calling each one and summing the returned
failure counts. That makes a dropped suite invisible: delete the
`test_lora_params()` line and the harness still prints ALL NATIVE TESTS PASSED
and exits 0, having quietly stopped testing a whole area.

This is the same vacuous-pass trap as a guard that inspects an empty set, one
level down. It is easy to hit, because a suite that misbehaves on one platform
is tempting to disconnect rather than fix.

The list of suites is derived from the test_*.c files on disk, so adding a
suite without wiring it into the harness fails here rather than in production.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
MAIN = TESTS / "main.c"

# Sanity floor, so this module cannot itself pass by discovering nothing.
MINIMUM_SUITES = 4

# If one of these ever stops being a separate suite this test will say so,
# which is the point: it is a checkpoint, not a wish list.
EXPECTED_SUITES = (
    "test_kiss_frame",
    "test_kiss_modem",
    "test_lora_params",
    "test_pa_config",
)


def main_text():
    if not MAIN.exists():
        raise AssertionError(f"missing {MAIN}")
    return MAIN.read_text(encoding="utf-8")


def suite_sources():
    """Suite names derived from the tests/test_*.c files actually present."""
    return sorted(path.stem for path in TESTS.glob("test_*.c"))


def defined_suite_functions():
    """Suite functions actually defined, as opposed to merely declared."""
    defined = set()
    for path in sorted(TESTS.glob("test_*.c")):
        text = path.read_text(encoding="utf-8")
        # A definition has a body; the header alone only declares it.
        defined.update(
            re.findall(r"^int\s+(test_\w+)\s*\(\s*void\s*\)\s*\{", text, re.M)
        )
    return defined


def invoked_suites():
    """Suite functions main.c calls, i.e. the ones that actually run."""
    text = re.sub(r"//.*", "", main_text())
    return set(re.findall(r"failures\s*\+=\s*(test_\w+)\s*\(", text))


class NativeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.sources = suite_sources()
        self.invoked = invoked_suites()

    def test_the_suite_list_is_not_empty(self):
        """Guards against this module passing while finding nothing."""
        self.assertGreaterEqual(
            len(self.sources),
            MINIMUM_SUITES,
            f"only found {len(self.sources)} test_*.c files in {TESTS}",
        )

    def test_the_expected_suites_are_still_there(self):
        missing = [name for name in EXPECTED_SUITES if name not in self.sources]
        self.assertEqual(
            [],
            missing,
            f"expected suites missing from {TESTS}: {missing}. If one was "
            "merged or removed on purpose, update EXPECTED_SUITES here so the "
            "gap is deliberate rather than silent.",
        )

    def test_every_suite_file_defines_its_suite_function(self):
        defined = defined_suite_functions()

        missing = [name for name in self.sources if name not in defined]
        self.assertEqual(
            [],
            missing,
            "these files exist but define no suite function, so they are dead "
            f"weight: {missing}",
        )

    def test_every_suite_is_invoked_by_the_harness(self):
        """The check that matters: a suite nobody calls is not being run."""
        not_run = sorted(name for name in self.sources if name not in self.invoked)

        self.assertEqual(
            [],
            not_run,
            f"{not_run} define test suites that tests/main.c never calls, so "
            "they are compiled and then ignored; the harness would still "
            "report success",
        )

    def test_no_suite_is_invoked_that_does_not_exist(self):
        unknown = sorted(self.invoked - set(self.sources))

        self.assertEqual(
            [],
            unknown,
            f"main.c calls suite functions with no test_*.c file: {unknown}",
        )

    def test_a_failing_suite_makes_the_harness_exit_nonzero(self):
        text = main_text()

        self.assertRegex(
            text,
            r"return\s+failures\s*==\s*0\s*\?\s*0\s*:\s*1",
            "the harness must exit non-zero when any suite fails, or CI cannot "
            "see the failure",
        )

    def test_failures_are_accumulated_rather_than_ignored(self):
        """A suite whose result is discarded would report success regardless."""
        text = re.sub(r"//.*", "", main_text())

        for name in sorted(self.invoked):
            with self.subTest(suite=name):
                self.assertRegex(
                    text,
                    rf"failures\s*\+=\s*{name}\s*\(",
                    f"{name} is called but its failure count is not accumulated",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
