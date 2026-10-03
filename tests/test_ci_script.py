"""
Guards the CI script itself.

An unpinned or per-run network dependency in a gate is a real failure mode: the
step hangs or errors when the network is slow or blocked, and in an automated
run that surfaces as an unrelated-looking tool failure. The contract step used
to run `go install ...@latest` on every invocation.

These checks are static, so they need neither a network nor Go.
"""

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CI = ROOT / "tools" / "ci.ps1"


def extract_invoke_step(text):
    """The source of Invoke-Step, brace-matched rather than guessed."""
    start = text.find("function Invoke-Step {")
    if start < 0:
        raise AssertionError("Invoke-Step not found in ci.ps1")

    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise AssertionError("could not find the end of Invoke-Step")


def ci_text():
    if not CI.exists():
        raise AssertionError(f"missing {CI}")

    return CI.read_text(encoding="utf-8")


def ci_code():
    """The script with comments stripped, so prose about moving refs is not
    mistaken for a moving ref."""
    out = []

    for line in ci_text().splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            out.append(stripped)

    return "\n".join(out)


class CIScriptTests(unittest.TestCase):
    def test_go_modules_are_pinned(self):
        text = ci_text()

        # The version is held in a variable, so resolve it and check the
        # resolved module reference rather than the raw literal.
        m = re.search(r"\$botVersion\s*=\s*'([^']+)'", text)

        self.assertIsNotNone(
            m,
            "the bot version is not held in a pinned variable, so this guard "
            "cannot verify it",
        )

        version = m.group(1)
        self.assertRegex(
            version, r"^v\d+\.\d+\.\d+$",
            f"bot version {version!r} is not a pinned semantic version",
        )

        # And the module must actually be built from that variable, so the pin
        # is not decorative.
        self.assertRegex(
            text,
            r"github\.com/meshcore-go/meshcore-bot@\$botVersion",
            "the bot module is not installed using the pinned version variable",
        )

    def test_no_moving_refs_anywhere(self):
        code = ci_code()

        # A moving ref in a gate means the thing under test can change under you.
        for moving in ("@latest", "@master", "@main", "@head"):
            self.assertNotIn(
                moving,
                code,
                f"ci.ps1 refers to {moving}, so results are not reproducible",
            )

    def test_the_bot_is_only_installed_when_absent(self):
        text = ci_text()

        # The existence check must come before the install, or the network
        # round-trip happens on every run.
        have = text.find("$haveBot")
        install = text.find("$go.Source install")

        self.assertNotEqual(have, -1, "no existence guard for the bot binary")
        self.assertNotEqual(install, -1, "the bot install is missing entirely")
        self.assertLess(
            have, install,
            "the bot install runs before the existence check, so every run hits "
            "the network",
        )

    def test_gobin_is_scoped_to_the_install(self):
        text = ci_text()

        # GOBIN must be cleared again, or it leaks into the later `go test`.
        self.assertIn("Remove-Item Env:\\GOBIN", text)

    def test_install_failure_is_visible(self):
        text = ci_text()

        # A missing bot means the acceptance test silently stops running, which
        # is a coverage gap. It must be announced, and optionally fatal.
        self.assertIn("WARNING", text)
        self.assertIn("-Strict", text)

    def test_every_step_is_reported(self):
        text = ci_text()

        steps = re.findall(r"Invoke-Step '([^']+)'", text)

        self.assertGreaterEqual(len(steps), 5, "expected at least five gated steps")
        self.assertIn("contract test", steps)
        self.assertIn("python tests", steps)


class ContractStepCannotPassOnSkipsTests(unittest.TestCase):
    """A skipped Go test must not be reported as a passing gate.

    Both Go suites skip themselves when their binary environment variable is
    unset -- `go test` still exits 0 and prints `ok`. That is not a cosmetic
    problem: the acceptance test that drives the real published bot against the
    real firmware is the entire reason the contract step exists, and a skipped
    run would leave this project's headline claim unverified while the gate
    reported success.

    A direct `go test ./...` outside this script still skips, which is the right
    behaviour for a developer. Inside CI the binaries are always built first, so
    a skip can only mean something is broken.
    """

    def setUp(self):
        self.text = ci_text()

    def test_go_is_run_verbosely(self):
        # Without -v there are no per-test lines, so a skip cannot be detected.
        self.assertRegex(
            self.text,
            r"go\.Source test -v -count=1",
            "the contract step must run `go test -v` so individual results, "
            "including skips, are visible",
        )

    def test_a_skipped_test_is_treated_as_failure(self):
        self.assertRegex(
            self.text,
            r"SKIP:",
            "the contract step must look for skipped tests",
        )
        self.assertIn(
            "these contract tests were skipped",
            self.text,
            "a skip must produce a message naming the skipped tests",
        )

    def test_the_skip_check_throws_rather_than_warns(self):
        """A warning is what the bot-install path already does, and it is not
        enough: the point is that a green run means the tests really ran."""
        block = ci_text()
        start = block.find("$skipped =")
        self.assertGreater(start, -1, "no skip-detection block found")

        tail = block[start : start + 1200]
        self.assertIn(
            "throw",
            tail,
            "the skip check must throw, so the step fails instead of warning",
        )

    def test_go_exit_code_is_still_checked(self):
        self.assertRegex(
            self.text,
            r"if \(\$goExit -ne 0\)\s*\{[^}]*throw",
            "a non-zero exit from `go test` must fail the step",
        )


class BoardVariantFlagTests(unittest.TestCase):
    """`-XtAL` must decide which firmware.bin is left behind.

    ci.ps1 declares `[switch]$XtAL` and once ignored it entirely, so every run
    left the TCXO image on disk. For the XTAL board variant (SKU
    USB-TO-LoRa-xF-B) that means flashing the wrong firmware: the TCXO build
    drives DIO3 as a TCXO supply to a board that already has a crystal, and the
    symptom is a radio that never starts rather than an obvious "wrong build".

    Both variants are still built either way -- that is deliberate, since an
    instruction that no longer compiles is worse than none -- so the flag cannot
    be verified by counting builds. It is verified by which image survives.
    """

    def setUp(self):
        self.text = ci_text()
        self.rules = ci_code()

    def test_the_flag_is_declared(self):
        self.assertRegex(
            self.rules,
            r"\[switch\]\$XtAL",
            "ci.ps1 should still offer -XtAL",
        )

    def test_the_flag_is_actually_read(self):
        """The regression: declared in the param block, never used."""
        uses = list(re.finditer(r"\$XtAL", self.rules))

        declarations = [
            match
            for match in uses
            if self.rules[: match.start()].rstrip().endswith("[switch]")
        ]

        self.assertGreater(
            len(uses) - len(declarations),
            0,
            "-XtAL is declared but never read, so it silently does nothing; "
            "every run leaves the TCXO image regardless of the board",
        )

    def test_the_variant_left_in_place_is_reported(self):
        self.assertRegex(
            self.rules,
            r"firmware\.bin is the",
            "the script should say which variant it left in firmware.bin, so a "
            "wrong-board build is visible without comparing hashes",
        )

    def test_both_variants_are_still_built(self):
        for variant in ("XTAL", "TCXO"):
            with self.subTest(variant=variant):
                self.assertIn(
                    variant,
                    self.rules,
                    f"{variant} must still be built, or a broken instruction "
                    "goes unnoticed",
                )

    def test_the_readme_explains_the_flag(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # Flattened, so a phrase split across a line break still matches.
        flat = re.sub(r"\s+", " ", readme)

        self.assertRegex(
            flat,
            r"(?i)wrong firmware|wrong build",
            "the README must warn that the wrong variant presents as a dead "
            "radio rather than an obvious mistake",
        )


class NativeStderrHandlingTests(unittest.TestCase):
    """A passing step must not be able to fail because a tool wrote to stderr.

    The native tools in this script write to stderr routinely: Python's unittest
    prints its progress dots *and* its result summary there, and go test writes
    diagnostics. The script sets $ErrorActionPreference='Stop', so if a step ever
    redirects a native command's streams, PowerShell turns that ordinary stderr
    into a NativeCommandError and aborts a run that actually passed.

    This was not hypothetical: it aborted the first attempt at this test. The
    probes below run the real Invoke-Step, extracted from ci.ps1, so removing the
    guard fails the test rather than silently reintroducing the trap.
    """

    PROBE_TEMPLATE = """
$ErrorActionPreference = 'Stop'
$results = @()
$script:results = $results
%(function)s

$stderrWriter = 'python -c "import sys; sys.stderr.write(chr(46) * 3 + chr(10))"'

# 1. A passing native step whose stderr is captured. This is the trap.
try {
    Invoke-Step 'passing' { & cmd.exe /c $stderrWriter 2>&1 | Out-Null }
    Write-Output 'PASSING_SURVIVED'
} catch {
    Write-Output ('PASSING_ABORTED: ' + $_.Exception.Message)
}

# 2. A genuinely failing native step must still fail.
try {
    Invoke-Step 'failing' { & cmd.exe /c 'exit /b 3' }
    Write-Output 'FAILING_SWALLOWED'
} catch {
    Write-Output 'FAILING_DETECTED'
}

# 3. A throw inside a step must still propagate.
try {
    Invoke-Step 'throwing' { throw 'deliberate' }
    Write-Output 'THROW_SWALLOWED'
} catch {
    Write-Output 'THROW_PROPAGATED'
}

# 4. The caller's preference must be left as it was found.
Write-Output ('PREFERENCE: ' + $ErrorActionPreference)
"""

    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("powershell") or shutil.which("pwsh")
        if cls.shell is None:
            raise unittest.SkipTest("PowerShell not available")

        if not CI.exists():
            raise AssertionError(f"missing {CI}")

        cls.function = extract_invoke_step(ci_text())

    def run_probes(self):
        script = self.PROBE_TEMPLATE % {"function": self.function}

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "probes.ps1"
            path.write_text(script, encoding="utf-8")

            result = subprocess.run(
                [
                    self.shell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )

        return result.stdout

    def test_passing_step_survives_captured_stderr(self):
        out = self.run_probes()

        self.assertIn(
            "PASSING_SURVIVED",
            out,
            "a step that exited 0 was aborted because a native tool wrote to "
            "stderr and the streams were captured; see the NativeStderrHandling "
            "docstring. Output was:\n" + out,
        )
        self.assertNotIn("PASSING_ABORTED", out)

    def test_failing_step_is_still_detected(self):
        out = self.run_probes()

        self.assertIn("FAILING_DETECTED", out, f"a non-zero exit was swallowed:\n{out}")
        self.assertNotIn("FAILING_SWALLOWED", out)

    def test_throw_inside_a_step_still_propagates(self):
        out = self.run_probes()

        self.assertIn("THROW_PROPAGATED", out, f"a throw was swallowed:\n{out}")
        self.assertNotIn("THROW_SWALLOWED", out)

    def test_error_action_preference_is_restored(self):
        out = self.run_probes()

        self.assertIn(
            "PREFERENCE: Stop", out, f"preference leaked out of a step:\n{out}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
