"""The counts quoted in the README must match reality.

Concrete numbers are worth having in a README: "60,000 assertions" tells a
reader whether this project is a weekend script or something with teeth. The
cost is that they go stale the moment a test is added, and a README that
overstates its own coverage is worse than one that says nothing.

This check failed on the day it was written, twice: the Python count was 70
when the suite held 83, and the contract count said 11 when there were 10. So
the numbers are parsed out of the prose and compared with what is actually
there.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TESTS = ROOT / "tests"
CONTRACT = TESTS / "contract"

# Kept in one sentence in the Tests section, so the wording is part of the
# contract these assertions rely on.
PHRASE = (
    "That is about {assertions} host-side protocol assertions, "
    "{contract} contract tests, and {python} Python tests."
)


def readme_text():
    if not README.exists():
        raise AssertionError(f"missing {README}")
    return README.read_text(encoding="utf-8")


def quoted(text, label):
    """The integer the README quotes for `label`, with separators removed.

    Matching runs against a whitespace-flattened copy of the prose, so the
    sentence may be wrapped across lines as the README happens to be edited.
    """
    flat = re.sub(r"\s+", " ", text)
    match = re.search(rf"(\d[\d,]*)\s+{label}\b", flat)
    if match is None:
        raise AssertionError(
            f"the README does not quote a number for {label!r}; expected a "
            f"sentence of the form: {PHRASE}"
        )
    return int(match.group(1).replace(",", ""))


def python_test_count():
    """How many tests unittest discovery actually finds."""
    import unittest as ut

    return ut.TestLoader().discover(str(TESTS)).countTestCases()


def contract_test_count():
    """How many Go test functions the contract suite defines."""
    functions = 0
    for path in sorted(CONTRACT.glob("*.go")):
        functions += len(
            re.findall(r"^func Test", path.read_text(encoding="utf-8"), re.M)
        )
    return functions


class ReadmeCountsTests(unittest.TestCase):
    def setUp(self):
        self.text = readme_text()

    def test_python_test_count_is_current(self):
        self.assertEqual(
            python_test_count(),
            quoted(self.text, "Python tests"),
            "the README quotes a stale Python test count",
        )

    def test_contract_test_count_is_current(self):
        self.assertEqual(
            contract_test_count(),
            quoted(self.text, "contract tests"),
            "the README quotes a stale contract test count",
        )

    def test_native_assertion_count_is_not_understated(self):
        """The native suite's exact total is only knowable from its runner.

        tools/test.ps1 builds and runs the host binaries and prints the totals,
        and it deletes them afterwards, so an exact comparison here would mean
        rebuilding the C suite inside a unit test. Native coverage only grows,
        so the invariant that matters is that the README never claims less than
        the suite already has.
        """
        claimed = quoted(self.text, "host-side protocol assertions")

        self.assertGreaterEqual(
            claimed,
            60_000,
            "the README understates the native assertions; run tools/test.ps1 "
            "for the current total",
        )

    def test_the_counts_sit_in_one_parseable_sentence(self):
        """Keep the sentence findable, so the checks above cannot rot.

        A future edit that reworded this line would otherwise make the three
        checks above fail with a confusing message, or worse, get deleted.
        """
        flat = re.sub(r"\s+", " ", self.text)
        self.assertIn(
            "contract tests, and",
            flat,
            "the Tests section should keep the counts in the sentence the "
            f"checks parse: {PHRASE}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
