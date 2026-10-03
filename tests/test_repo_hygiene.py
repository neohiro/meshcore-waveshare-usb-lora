"""Repository hygiene: one line ending style, one final newline, no mojibake.

These are the defects that never break a build but quietly rot a project: a
CRLF creeping into one edited function, a truncated final newline showing up as
``\\ No newline at end of file`` in every future diff, a stray BOM.

The policy is declared in ``.editorconfig`` and enforced here. Vendored trees
are exempt on purpose, and a test below proves that exemption is real rather
than accidental, so nobody can widen it to hide real damage.
"""

import os
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

CR = b"\r"
LF = b"\n"
BOM = b"\xef\xbb\xbf"
REPLACEMENT = "\ufffd"

TEXT_SUFFIXES = {".md", ".py", ".toml", ".ps1", ".c", ".h", ".go", ".ld"}
TEXT_NAMES = {".gitignore", ".editorconfig"}

# Kept byte-for-byte as upstream shipped them; see .editorconfig.
VENDORED = ("firmware/rtos/", "firmware/sx126x/", "firmware/libopencm3/")

# Build output and the downloaded toolchain are not sources; descending into
# the toolchain on every test would cost seconds for nothing.
# Note: tests/contract/ holds real Go sources and must NOT be pruned.
PRUNED_DIRS = {"toolchain", "obj", "obj_test", "bin", ".git", "node_modules"}

# Two trailing spaces are a hard line break in Markdown.
NO_TRAILING_WS = TEXT_SUFFIXES - {".md"}

WALK_CACHE = {}


def _walk(*, include_vendored: bool):
    """Yield (relative_posix_path, bytes) for maintainable text files.

    os.walk rather than rglob so PRUNED_DIRS are skipped without descending.
    """
    key = bool(include_vendored)
    if key in WALK_CACHE:
        yield from WALK_CACHE[key]
        return

    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        here = pathlib.Path(dirpath)

        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED_DIRS)

        for name in sorted(filenames):
            path = here / name
            if path.suffix not in TEXT_SUFFIXES and name not in TEXT_NAMES:
                continue

            rel = path.relative_to(ROOT).as_posix()
            if not include_vendored and rel.startswith(VENDORED):
                continue

            found.append((rel, path.read_bytes()))

    WALK_CACHE[key] = found
    yield from found


def owned_text_files():
    """Files this project maintains (vendored trees excluded)."""
    return _walk(include_vendored=False)


def owned_text_files_all():
    """Everything, vendored trees included, for auditing the exemption."""
    return _walk(include_vendored=True)


class TestTextEncoding(unittest.TestCase):
    def test_files_are_valid_utf8(self):
        offenders = []
        for rel, data in owned_text_files():
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                offenders.append(f"{rel}: {exc}")
        self.assertEqual([], offenders, "files must be valid UTF-8")

    def test_no_byte_order_mark(self):
        offenders = [
            rel for rel, data in owned_text_files() if data[:3] == BOM
        ]
        self.assertEqual(
            [], offenders, "a UTF-8 BOM breaks some preprocessors and diff tools"
        )

    def test_no_replacement_character(self):
        offenders = [
            rel
            for rel, data in owned_text_files()
            if REPLACEMENT in data.decode("utf-8", "replace")
        ]
        self.assertEqual(
            [], offenders, "U+FFFD means the text was decoded with the wrong codec"
        )


class TestLineEndings(unittest.TestCase):
    def test_no_crlf_line_endings(self):
        offenders = [
            f"{rel} ({data.count(CR)} CR)"
            for rel, data in owned_text_files()
            if CR in data
        ]
        self.assertEqual(
            [], offenders, "use LF endings (.editorconfig sets end_of_line = lf)"
        )

    def test_ends_with_exactly_one_newline(self):
        offenders = []
        for rel, data in owned_text_files():
            if not data:
                continue
            if not data.endswith(LF):
                offenders.append(f"{rel}: no trailing newline")
            elif data.endswith(LF * 2):
                offenders.append(f"{rel}: more than one trailing newline")
        self.assertEqual([], offenders, "files must end with exactly one newline")

    def test_no_trailing_whitespace(self):
        offenders = []
        for rel, data in owned_text_files():
            if pathlib.PurePosixPath(rel).suffix not in NO_TRAILING_WS:
                continue
            for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
                if line != line.rstrip():
                    offenders.append(f"{rel}:{number}")
        self.assertEqual(
            [], offenders, "trailing whitespace is invisible and always wrong"
        )


class TestTheGuardIsNotVacuous(unittest.TestCase):
    """These checks must fail if the file walk stops finding files.

    Every other test in this file asserts that no offender was found. That is
    trivially true of an empty set, so a walk broken by a typo or an
    over-broad PRUNED_DIRS entry would report a clean repository while checking
    nothing at all -- a guard that cannot fail is worse than no guard, because
    it looks like coverage.

    This has already happened once: `contract` was briefly added to
    PRUNED_DIRS, which would have skipped every Go source under tests/contract.
    Nothing complained, because nothing counted.
    """

    # Files this project owns, one per area the policy is supposed to cover.
    MUST_SEE = (
        "README.md",
        ".editorconfig",
        ".gitignore",
        "bot/config.toml",
        "firmware/gd32f103.ld",
        "firmware/src/kiss.c",
        "firmware/src/radio.c",
        "tools/ci.ps1",
        "tools/kissmon.py",
        "tests/contract/contract_test.go",
    )

    def test_the_walk_finds_the_files_it_exists_to_police(self):
        found = {rel for rel, _ in owned_text_files()}

        missing = [name for name in self.MUST_SEE if name not in found]
        self.assertEqual(
            [],
            missing,
            "the hygiene walk no longer sees these files, so the checks above "
            "are passing vacuously; PRUNED_DIRS or TEXT_SUFFIXES has probably "
            "been edited too widely",
        )

    def test_the_walk_finds_a_plausible_number_of_files(self):
        count = len(list(owned_text_files()))

        self.assertGreater(
            count,
            40,
            f"the walk only found {count} files, which is implausibly few for "
            "this repository and suggests it is walking the wrong tree",
        )

    def test_go_sources_are_not_pruned(self):
        """tests/contract is source, and it is easy to prune by accident."""
        found = {
            rel for rel, _ in owned_text_files() if rel.startswith("tests/contract/")
        }

        self.assertTrue(
            found,
            "no files found under tests/contract: Go sources must not be pruned",
        )

    def test_vendored_trees_are_excluded_from_the_owned_set(self):
        owned = {rel for rel, _ in owned_text_files()}
        everything = {rel for rel, _ in owned_text_files_all()}

        vendored = {rel for rel in everything if rel.startswith(VENDORED)}
        self.assertTrue(vendored, "expected the vendored trees to be found")
        self.assertEqual(
            set(),
            owned & vendored,
            "vendored files must not be linted by this module",
        )
        self.assertLess(
            len(owned),
            len(everything),
            "the vendored exemption appears to have no effect",
        )


class TestGeneratedArtefactsStayIgnored(unittest.TestCase):
    """Build output must never reach git.

    The build produces a 100 MB toolchain, a 15 MB bot binary and a pile of object
    files. A .gitignore pattern that looks right but does not match is the kind of
    thing nobody notices until someone commits 100 MB by accident, at which point
    the history is permanently carrying it.

    This asks git directly rather than reading .gitignore, because a pattern that
    reads plausibly and does not match is precisely the failure worth catching.

    Source files are checked in the other direction: the point is to ignore
    artefacts, not to blanket-ignore the project, which would quietly turn a
    broken pattern into a working one.
    """

    # Artefacts the build and setup produce, with a plausible path inside each.
    ARTEFACTS = (
        "toolchain/arm-gcc/bin/arm-none-eabi-gcc.exe",
        "firmware/obj/main.o",
        "firmware/firmware.bin",
        "firmware/firmware.elf",
        "firmware/firmware.map",
        "firmware/libfreertos.a",
        "firmware/libopencm3/lib/libopencm3_stm32f1.a",
        "tools/bin/meshcore-bot.exe",
        "tests/kiss-server.exe",
        "tests/test-firmware.exe",
    )

    # Things that must stay visible to git, or ignoring becomes too broad.
    SOURCES = (
        "README.md",
        "bot/config.toml",
        "firmware/src/radio.c",
        "tools/ci.ps1",
        "setup.ps1",
        "run-bot.ps1",
    )

    def git_check_ignore(self, path):
        """True when git ignores `path`, False when it does not, None if unknown."""
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", "--", path],
                cwd=ROOT,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")

    def test_every_generated_artefact_is_ignored(self):
        unknown, tracked = [], []

        for artefact in self.ARTEFACTS:
            ignored = self.git_check_ignore(artefact)
            if ignored is None:
                unknown.append(artefact)
            elif not ignored:
                tracked.append(artefact)

        if unknown:
            self.skipTest(f"git could not classify: {unknown}")

        self.assertEqual(
            [],
            tracked,
            "these build artefacts are not ignored, so a commit would carry "
            f"them: {tracked}",
        )

    def test_sources_are_not_swept_up_by_the_patterns(self):
        verdicts = {s: self.git_check_ignore(s) for s in self.SOURCES}
        if all(v is None for v in verdicts.values()):
            self.skipTest("git could not classify the source files")

        over_ignored = [s for s, ignored in verdicts.items() if ignored]

        self.assertEqual(
            [],
            over_ignored,
            "these are source files and must stay visible to git; a pattern is "
            f"too broad: {over_ignored}",
        )


class TestPolicyIsHonest(unittest.TestCase):
    def test_the_vendored_exemption_points_at_real_content(self):
        """The exemption must name trees that actually exist and hold files.

        This used to assert the vendored sources were CRLF, using line endings as
        a proxy for "this is upstream content we are not linting". That was a
        reasonable proxy until `.gitattributes` was added: normalising the whole
        repository to LF for Windows clones means a fresh clone has no CRLF left
        anywhere, so the check failed on a clean checkout while passing on the
        machine that had committed.

        What actually matters is not the line endings but that the exemption is
        not vacuous: the directories it names must still be populated. Verified by
        cloning, which is the only way this class of bug shows up at all.
        """
        # Only the two trees git actually tracks. firmware/libopencm3 is not
        # vendored at all: it is listed in .gitignore and cloned by
        # tools/build.ps1, so a fresh checkout has no such directory and
        # requiring one here would fail on exactly the clean clone this test
        # exists to protect.
        expected = {
            "firmware/rtos": ("firmware/rtos/tasks.c", "firmware/rtos/queue.c"),
            "firmware/sx126x": (
                "firmware/sx126x/sx126x.c",
                "firmware/sx126x/sx126x_hal.c",
            ),
        }

        present = {rel for rel, _ in owned_text_files_all()}

        for prefix, samples in expected.items():
            for sample in samples:
                with self.subTest(tree=prefix, file=sample):
                    self.assertTrue(
                        (ROOT / sample).exists(),
                        f"{prefix} is exempted from linting but {sample} is "
                        "missing; the exemption has stopped referring to "
                        "anything real",
                    )
                    self.assertIn(sample, present)

    def test_the_own_walker_excludes_those_trees(self):
        """...and the exemption is actually applied, not just declared."""
        owned = {rel for rel, _ in owned_text_files()}

        for prefix in ("firmware/rtos/", "firmware/sx126x/", "firmware/libopencm3/"):
            with self.subTest(tree=prefix):
                leaked = [rel for rel in owned if rel.startswith(prefix)]
                self.assertEqual(
                    [],
                    leaked,
                    f"{prefix} is vendored and must not be linted by this module",
                )

    def test_editorconfig_declares_the_enforced_policy(self):
        config = (ROOT / ".editorconfig").read_text(encoding="utf-8")
        for setting in (
            "charset = utf-8",
            "end_of_line = lf",
            "insert_final_newline = true",
            "trim_trailing_whitespace = true",
        ):
            self.assertIn(setting, config, f".editorconfig must declare '{setting}'")

    def test_editorconfig_exempts_every_vendored_tree(self):
        config = (ROOT / ".editorconfig").read_text(encoding="utf-8")
        for prefix in (
            "firmware/rtos/**",
            "firmware/sx126x/**",
            "firmware/libopencm3/**",
        ):
            self.assertIn(
                f"[{prefix}]", config, f".editorconfig must exempt {prefix}"
            )

        # ...and the walker's exemptions must match it exactly.
        for prefix in ("firmware/rtos/", "firmware/sx126x/", "firmware/libopencm3/"):
            self.assertIn(prefix, VENDORED, f"the file walker must exempt {prefix}")


if __name__ == "__main__":
    unittest.main()
