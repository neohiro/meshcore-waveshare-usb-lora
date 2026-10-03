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


class ReadmeCommandsAreRealTests(unittest.TestCase):
    """Every kissmon invocation in the README must name a real subcommand.

    The manual sequence in the README is what a first-time user follows, and a
    command that does not exist fails with argparse's "invalid choice" before
    anything useful happens. Nothing connected the documented commands to
    kissmon's subparsers, so a rename on either side would have gone unnoticed.

    Only fenced code blocks are considered, and only invocations that name an
    interpreter. The README also mentions `kissmon` in prose ("kissmon info will
    work against it, so the bring-up steps below...") and lists the file in the
    layout table ("kissmon.py   bring-up client: info, monitor, setradio, tx"),
    where the next word describes the file instead of being a command. Scanning
    for the bare filename reported a command called "bring-up" from both.
    """

    SUBCOMMANDS = ("info", "setradio", "monitor", "tx", "raw")

    # python tools\kissmon.py ..., py -m tools.kissmon ..., and so on.
    INVOCATION = re.compile(r"(?:python|py)\s+\S*kissmon\.py((?:\s+\S+)*)")

    def code_blocks(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        return re.findall(r"^```.*?$(.*?)^```", text, re.S | re.M)

    def documented_commands(self):
        found = set()

        for block in self.code_blocks():
            for match in self.INVOCATION.finditer(block):
                arguments = match.group(1).split()
                # Skip past the options and their values to the subcommand.
                index = 0
                while index < len(arguments):
                    token = arguments[index]
                    if not token.startswith("-"):
                        break
                    takes_value = (
                        "=" not in token
                        and index + 1 < len(arguments)
                        and not arguments[index + 1].startswith("-")
                    )
                    # An option that takes a value consumes the next token.
                    index += 2 if takes_value else 1

                if index < len(arguments):
                    found.add(arguments[index])

        return found

    def test_the_scan_found_the_documented_commands(self):
        self.assertEqual(
            {"info", "monitor", "setradio"},
            self.documented_commands(),
            "the scan should find the three commands the README's code blocks "
            "run; if this fails the check below is not looking at anything",
        )

    def test_every_documented_command_exists(self):
        unknown = sorted(self.documented_commands() - set(self.SUBCOMMANDS))

        self.assertEqual(
            [],
            unknown,
            "the README tells the reader to run kissmon commands that do not "
            f"exist; kissmon offers {', '.join(self.SUBCOMMANDS)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
