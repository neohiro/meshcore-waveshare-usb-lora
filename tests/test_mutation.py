"""The mutation helper must be impossible to fool.

Every other guard in this suite can be satisfied by a mutation that never
applied, which is the failure mode these tests exist to rule out. They are
therefore mostly about the helper refusing to lie:

* a target that is not present raises, rather than quietly changing nothing;
* an ambiguous target raises, rather than picking one occurrence;
* a replacement equal to the original raises, because it cannot prove anything;
* the file is read back after writing, so an external writer is detected;
* the context manager restores byte-for-byte, even when the body raises.
"""

import pathlib
import tempfile
import unittest

from tests import mutation
from tests.mutation import MutationError

SAMPLE = """line one
target line
line three
target line
"""


class MutationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "sample.txt"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()


class ApplyTests(MutationTestCase):
    def test_it_replaces_the_expected_number_of_occurrences(self):
        mutation.apply(self.path, "target line", "changed", expected=2)

        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("target line", text)
        self.assertEqual(2, text.count("changed"))

    def test_it_raises_when_the_target_is_absent(self):
        """The silent no-op that made two earlier proofs worthless."""
        with self.assertRaises(MutationError) as ctx:
            mutation.apply(self.path, "not in the file", "replacement")

        self.assertIn("found 0", str(ctx.exception))
        self.assertEqual(
            SAMPLE,
            self.path.read_text(encoding="utf-8"),
            "a failed mutation must leave the file alone",
        )

    def test_it_raises_on_an_ambiguous_target(self):
        with self.assertRaises(MutationError) as ctx:
            mutation.apply(self.path, "target line", "changed", expected=1)

        self.assertIn("expected 1", str(ctx.exception))
        self.assertEqual(SAMPLE, self.path.read_text(encoding="utf-8"))

    def test_it_raises_when_the_replacement_is_identical(self):
        with self.assertRaises(MutationError) as ctx:
            mutation.apply(self.path, "target line", "target line", expected=2)

        self.assertIn("identical", str(ctx.exception))

    def test_it_can_assert_a_hazard_is_absent(self):
        """expected=0 is a read-only assertion: it checks, and changes nothing."""
        mutation.apply(self.path, "target line", "changed", expected=2)
        before = self.path.read_bytes()

        mutation.apply(self.path, "target line", "x", expected=0)

        self.assertEqual(
            before,
            self.path.read_bytes(),
            "expected=0 must not modify the file",
        )

    def test_absence_check_raises_if_the_hazard_is_present(self):
        with self.assertRaises(MutationError):
            mutation.apply(self.path, "target line", "x", expected=0)

    def test_it_refuses_a_file_that_does_not_exist(self):
        with self.assertRaises(MutationError):
            mutation.apply(self.path.parent / "nope.txt", "a", "b")

    def test_the_error_names_the_file(self):
        with self.assertRaises(MutationError) as ctx:
            mutation.apply(self.path, "absent", "x")

        self.assertIn(self.path.name, str(ctx.exception))


class PreservedTests(MutationTestCase):
    def test_it_restores_the_original_bytes(self):
        before = self.path.read_bytes()

        with mutation.preserved(self.path) as mutate:
            mutate("target line", "changed", expected=2)
            self.assertIn("changed", self.path.read_text(encoding="utf-8"))

        self.assertEqual(before, self.path.read_bytes())

    def test_it_restores_when_the_body_raises(self):
        before = self.path.read_bytes()

        # One `with`: preserved() is entered second and so exits first, which is
        # exactly the guarantee under test -- the restore happens before
        # assertRaises sees the exception.
        with self.assertRaises(RuntimeError), mutation.preserved(self.path) as mutate:
            mutate("target line", "changed", expected=2)
            raise RuntimeError("the check failed, as intended")

        self.assertEqual(before, self.path.read_bytes())

    def test_a_failing_mutation_inside_the_block_still_restores(self):
        before = self.path.read_bytes()

        with self.assertRaises(MutationError), mutation.preserved(self.path) as mutate:
            mutate("target line", "changed", expected=2)
            mutate("not present", "x")

        self.assertEqual(before, self.path.read_bytes())

    def test_successive_mutations_accumulate(self):
        with mutation.preserved(self.path) as mutate:
            mutate("target line", "second form", expected=2)
            mutate("second form", "third form", expected=2)

            text = self.path.read_text(encoding="utf-8")
            self.assertEqual(2, text.count("third form"))

    def test_it_restores_a_file_with_no_trailing_newline(self):
        self.path.write_text("no newline here", encoding="utf-8")
        before = self.path.read_bytes()

        with mutation.preserved(self.path) as mutate:
            mutate("no newline", "still none")

        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.path.read_bytes().endswith(b"\n"))


class EndToEndGuardTests(unittest.TestCase):
    """Use the helper the way it is meant to be used, on a real guard.

    If this stops failing when the source is broken, either the guard or the
    helper has regressed.
    """

    def test_an_isr_that_touches_spi_is_caught(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        radio = root / "firmware" / "src" / "radio.c"
        if not radio.exists():
            raise unittest.SkipTest("firmware source not present")

        needle = "void exti0_isr(void) {\n"
        offending = (
            needle + "    sx126x_irq_mask_t probe;"
            " sx126x_get_irq_status(NULL, &probe);\n"
        )
        with mutation.preserved(radio) as mutate:
            mutate(needle, offending)
            import subprocess
            import sys

            result = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.test_isr_safety"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                0,
                result.returncode,
                "the ISR safety guard did not notice an ISR touching SPI",
            )
            self.assertIn(
                "exti0_isr",
                result.stderr + result.stdout,
                "the failure should name the offending handler",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
