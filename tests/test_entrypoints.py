"""The two-command entry points, and the diagnostic they rely on.

`setup.ps1` and `run-bot.ps1` exist so that going from a fresh clone to a running
bot takes one command and then a second. That only helps if they are trustworthy,
so this checks three things:

1. `setup.ps1` behaves. Run with -DryRun it must exit 0, change nothing, and list
   its steps; -SkipTests must drop the test step. This is executed, not merely
   pattern-matched, because a script can look right and still fall over.
2. `setup.ps1` is honest about hardware. It has to succeed with no probe and no
   flashed modem, because that is the state a first-time user is in. Anything
   that makes a missing probe fatal would strand them.
3. `kissmon info` reports silence. It used to exit 0 after sending fifteen
   commands and receiving nothing, which made a dongle still running the
   Waveshare firmware look healthy -- and that is precisely the state setup.ps1
   needs to be able to detect.
"""

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "setup.ps1"
RUN_BOT = ROOT / "run-bot.ps1"
CI = ROOT / "tools" / "ci.ps1"
KISSMON = ROOT / "tools" / "kissmon.py"
README = ROOT / "README.md"

# The pinned bot release must not drift between the two entry points, or the
# acceptance test verifies something different from what actually runs.
PINNED_BOT = "v1.2.0"

# The name this project had before the rename. Kept so the consistency checks
# below have something to assert the absence of.
OLD_NAME = "meshcore-usb-lora"


def load_kissmon():
    spec = importlib.util.spec_from_file_location("kissmon_under_test", KISSMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSerial:
    """Just enough of pyserial for cmd_info's control flow."""

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


class FakeArgs:
    port = "COM_TEST"
    baud = 115200


class KissmonHonestyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kissmon = load_kissmon()

    def setUp(self):
        # cmd_info prints its transcript; capture it so the gate's output stays
        # readable and a real failure is not buried under "port COM_TEST @ ...".
        self._stdout = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self._stdout)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)

    def test_info_fails_when_the_modem_says_nothing(self):
        sent = []

        def silent_command(ser, cmd, payload=b"", label=None, wait=2.0):
            sent.append(cmd)
            return []

        self.kissmon.open_port = lambda args: FakeSerial()
        self.kissmon.command = silent_command

        result = self.kissmon.cmd_info(FakeArgs())

        self.assertEqual(
            1,
            result,
            "info must exit non-zero when nothing replied, otherwise a dongle "
            "still running the vendor firmware looks healthy",
        )
        self.assertTrue(sent, "no commands were sent, so this proves nothing")
        self.assertIn(
            "no replies",
            self._stdout.getvalue(),
            "the failure has to say the modem is silent, not just exit 1",
        )

    def test_info_succeeds_when_the_modem_answers(self):
        self.kissmon.open_port = lambda args: FakeSerial()
        self.kissmon.command = lambda *a, **k: [(0x99, b"\x01")]

        self.assertEqual(0, self.kissmon.cmd_info(FakeArgs()))

    def test_a_single_reply_is_enough(self):
        # One answer means the modem is alive; a later query timing out must not
        # turn a working modem into a reported failure.
        self.kissmon.open_port = lambda args: FakeSerial()
        answers = iter([[(0x99, b"\x01")]] + [[]] * 40)
        self.kissmon.command = lambda *a, **k: next(answers, [])

        self.assertEqual(0, self.kissmon.cmd_info(FakeArgs()))


def run_setup(*args):
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SETUP),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


class SetupDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SETUP.exists():
            raise unittest.SkipTest("setup.ps1 not present")
        cls.powerShell = True

    def setUp(self):
        if not self.powerShell:
            self.skipTest("setup.ps1 needs Windows PowerShell")

    def test_dry_run_succeeds_and_changes_nothing(self):
        before = (ROOT / "firmware" / "firmware.bin")
        stamp = before.stat().st_mtime if before.exists() else None

        result = run_setup("-DryRun")

        self.assertEqual(
            0,
            result.returncode,
            f"-DryRun must not fail:\n{result.stdout}\n{result.stderr}",
        )
        if stamp is not None:
            self.assertEqual(
                stamp,
                before.stat().st_mtime,
                "-DryRun rebuilt the firmware; it promised to change nothing",
            )

    def test_dry_run_lists_every_step(self):
        result = run_setup("-DryRun")

        for step in ("prerequisites", "toolchain", "build", "tests", "flash", "modem"):
            with self.subTest(step=step):
                self.assertIn(step, result.stdout, f"the plan omits the {step} step")

    def test_skip_tests_drops_the_test_step(self):
        result = run_setup("-DryRun", "-SkipTests")

        self.assertEqual(0, result.returncode)
        self.assertNotIn("tests ", result.stdout, "-SkipTests must drop the test step")
        self.assertIn("build", result.stdout, "the rest of the plan should remain")


class BoardSelectionTests(unittest.TestCase):
    """`-Board xtal` has to reach both the build and the gate.

    Getting this wrong is a hardware-facing failure with a misleading symptom.
    The TCXO build drives DIO3 as a TCXO supply, so flashing it to a board that
    already has a crystal gives a radio that never starts -- which looks like a
    firmware bug rather than the wrong build.

    It is also easy to get half right: setup.ps1 could pass -XtAL to build.ps1
    and forget ci.ps1, in which case the gate would rebuild the TCXO image last
    and quietly leave that on disk, undoing the selection the build just made.
    """

    def setUp(self):
        if not SETUP.exists():
            raise unittest.SkipTest("setup.ps1 not present")
        self.text = SETUP.read_text(encoding="utf-8")

    def test_the_board_parameter_is_validated(self):
        self.assertIn(
            "ValidateSet('tcxo', 'xtal')",
            self.text,
            "an unvalidated -Board would be silently ignored by the build",
        )

    def test_the_board_is_forwarded_to_the_build(self):
        self.assertRegex(
            self.text,
            r"if\s*\(\$Board\s*-eq\s*'xtal'\)\s*\{\s*\$buildCmd\s*\+=\s*'-XtAL'",
            "-Board xtal must add -XtAL to the build.ps1 arguments",
        )

    def test_the_board_is_forwarded_to_the_gate(self):
        self.assertRegex(
            self.text,
            r"if\s*\(\$Board\s*-eq\s*'xtal'\)\s*\{\s*\$ciCmd\s*\+=\s*'-XtAL'",
            "-Board xtal must also add -XtAL to ci.ps1, or the gate rebuilds "
            "the other variant last and leaves the wrong image on disk",
        )

    def test_the_dry_run_names_the_selected_variant(self):
        result = run_setup("-DryRun", "-Board", "xtal")

        self.assertEqual(0, result.returncode)
        self.assertIn(
            "xtal",
            result.stdout,
            "the plan should say which board variant it would build",
        )

    def test_the_default_is_tcxo(self):
        result = run_setup("-DryRun")

        self.assertIn(
            "tcxo",
            result.stdout,
            "the default board variant should be the TCXO one",
        )


class SetupStructureTests(unittest.TestCase):
    def setUp(self):
        if not SETUP.exists():
            raise unittest.SkipTest("setup.ps1 not present")
        self.text = SETUP.read_text(encoding="utf-8")

    def test_it_offers_the_options_the_readme_describes(self):
        for switch in ("-Board", "-Port", "-SkipTests", "-NoFlash", "-DryRun"):
            with self.subTest(switch=switch):
                self.assertIn(
                    switch.replace("-", "$"),
                    self.text,
                    f"{switch} is documented behaviour but not a parameter",
                )

    def test_missing_hardware_is_reported_not_fatal(self):
        """A first run has no probe and an unflashable modem. It must still finish."""
        self.assertIn("Add-Outstanding", self.text)
        self.assertRegex(
            self.text,
            r"no SWD probe detected",
            "a missing probe should be reported as remaining work, not thrown",
        )

    def test_it_ends_by_pointing_at_the_run_command(self):
        self.assertIn(
            "run-bot.ps1",
            self.text,
            "setup should tell the user the second command",
        )

    def test_it_reuses_the_existing_scripts(self):
        for helper in ("build.ps1", "ci.ps1", "flash-swd.ps1", "fetch-toolchain.ps1",
                       "find_port.py", "kissmon.py"):
            with self.subTest(helper=helper):
                self.assertIn(
                    helper,
                    self.text,
                    f"setup should reuse tools\\{helper} rather than reimplement it",
                )

    def test_it_does_not_reimplement_shell_discovery(self):
        """The duplication that first broke this script.

        setup.ps1 originally checked for `sh` on PATH, which fails on a normal
        Git for Windows install: sh lives in usr\\bin, and only Git\\cmd is on
        PATH. tools/build.ps1 already looks in the right place, so setup must not
        second-guess it.
        """
        code = re.sub(r"#.*", "", self.text)
        self.assertNotRegex(
            code,
            r"Get-Command\s+'sh'",
            "setup.ps1 must not look for sh on PATH; build.ps1 owns that check",
        )


class RunBotSurvivesAVainInstallTests(unittest.TestCase):
    """What happens when `go install` succeeds without producing a binary.

    The script used to pick a path and run it, so a GOBIN that was quietly
    ignored turned into a shell error naming neither the module nor the
    directory. Driven with a stub `go` that exits 0 and writes nothing, which is
    exactly the case that could not otherwise be reproduced on demand.
    """

    def setUp(self):
        if not RUN_BOT.exists():
            raise unittest.SkipTest("run-bot.ps1 not present")

        self.tmp = tempfile.mkdtemp(prefix="run-bot-vain-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # run-bot.ps1 resolves everything from $PSScriptRoot, so a copy in an
        # otherwise empty tree exercises the real script without touching the
        # repository's own tools/bin.
        shutil.copy2(RUN_BOT, os.path.join(self.tmp, "run-bot.ps1"))

        repo = self.tmp
        os.makedirs(os.path.join(repo, "firmware"))
        os.makedirs(os.path.join(repo, "bot"))
        os.makedirs(os.path.join(repo, "tools", "bin"), exist_ok=True)
        # Both of these are checked before the bot is installed, so they have to
        # be present for the run to reach the step under test. $Repo is
        # $PSScriptRoot, so they belong beside the copied script.
        pathlib.Path(repo, "firmware", "firmware.bin").write_bytes(b"")
        pathlib.Path(repo, "bot", "config.toml").write_text(
            "# not parsed by this test\n", encoding="utf-8"
        )

        stub_dir = os.path.join(self.tmp, "stub")
        os.makedirs(stub_dir, exist_ok=True)
        stub = os.path.join(stub_dir, "go.cmd")
        with open(stub, "w", encoding="utf-8") as handle:
            handle.write(
                "@echo off\r\n"
                "rem succeeds without writing anything\r\n"
                "exit /b 0\r\n"
            )

        self.repo = repo

    def run_bot(self):
        env = dict(os.environ)
        env["PATH"] = os.path.join(self.tmp, "stub") + os.pathsep + env["PATH"]

        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                os.path.join(self.tmp, "run-bot.ps1"),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=self.repo,
        )

    def test_a_vain_install_is_reported_not_attempted(self):
        result = self.run_bot()

        self.assertNotEqual(
            0,
            result.returncode,
            f"a `go install` that produced nothing must not report success:\n"
            f"{result.stdout}\n{result.stderr}",
        )

    def test_the_message_says_where_it_looked_and_what_to_do(self):
        result = self.run_bot()
        output = result.stdout + result.stderr

        self.assertIn(
            "meshcore-bot is not in",
            output,
            f"the failure should name the directory it searched:\n{output}",
        )
        self.assertIn(
            "go env GOBIN",
            output,
            f"the failure should say how to diagnose a GOBIN that was ignored:\n"
            f"{output}",
        )

    def test_it_does_not_try_to_execute_the_missing_binary(self):
        result = self.run_bot()
        output = result.stdout + result.stderr

        self.assertNotIn(
            "Press Ctrl-C to stop",
            output,
            "the script announced a running bot, so it got past the check it "
            f"should have stopped at:\n{output}",
        )


class RunBotTests(unittest.TestCase):
    def setUp(self):
        if not RUN_BOT.exists():
            raise unittest.SkipTest("run-bot.ps1 not present")
        self.text = RUN_BOT.read_text(encoding="utf-8")

    def test_the_bot_version_is_pinned(self):
        self.assertIn(
            PINNED_BOT,
            self.text,
            "the bot release must be pinned, or @latest can change what runs",
        )

    def test_the_pinned_version_matches_the_test_gate(self):
        """Drift here means the acceptance test verifies a different release."""
        ci_text = CI.read_text(encoding="utf-8")

        self.assertIn(
            PINNED_BOT,
            ci_text,
            f"ci.ps1 pins a different bot release than run-bot.ps1 ({PINNED_BOT})",
        )

    def test_it_uses_the_config_setup_writes(self):
        self.assertIn("bot\\config.toml", self.text)
        self.assertIn("meshcore-bot", self.text)

    def test_it_refuses_to_run_before_setup(self):
        self.assertIn(
            "setup.ps1",
            self.text,
            "run-bot should say to run setup first when the firmware is missing",
        )

    def test_the_install_is_gated_on_the_binary_being_absent(self):
        """A fresh clone has no bot binary, so this path is the normal one.

        Verified by hand: with tools/bin emptied, run-bot.ps1 installed the pinned
        v1.2.0 and started. It cannot be covered here because it needs the
        network, so the shape of the decision is asserted instead.
        """
        self.assertRegex(
            self.text,
            r"if\s*\(\s*-\s*not\s*\(\s*\(\s*Test-Path[^\)]*\)\s*-\s*or\s*"
            r"\(\s*Test-Path",
            "the install must be gated on both the .exe and extension-less names "
            "being absent, or Windows resolves to a missing binary",
        )
        self.assertRegex(
            self.text,
            r"New-Item\s+-ItemType\s+Directory\s+-Force\s+-Path\s+\$Bin",
            "tools/bin has to be created before go install writes into it",
        )

    def test_gobin_is_scoped_to_the_install(self):
        """GOBIN must not leak into the environment that runs the bot."""
        self.assertRegex(
            self.text,
            r"\$env:GOBIN\s*=\s*\$Bin",
            "GOBIN should point at tools/bin for the install",
        )
        self.assertIn(
            "Remove-Item Env:\\GOBIN",
            self.text,
            "GOBIN must be cleaned up afterwards, or it leaks into the bot's "
            "own subprocesses",
        )


class ReadmeQuickStartTests(unittest.TestCase):
    def setUp(self):
        self.flat = re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))

    def test_the_two_commands_are_the_documented_start(self):
        self.assertIn(".\\setup.ps1", self.flat)
        self.assertIn(".\\run-bot.ps1", self.flat)

    def test_the_run_command_is_marked_as_second(self):
        self.assertRegex(
            self.flat,
            r"(?i)then run.*run-bot\.ps1|second.*run-bot\.ps1|run-bot\.ps1.*second",
            "the README should present run-bot.ps1 as the second of two commands",
        )

    def test_the_setup_options_are_documented(self):
        for switch in ("-Board xtal", "-Port", "-SkipTests", "-DryRun"):
            with self.subTest(switch=switch):
                self.assertIn(
                    switch,
                    self.flat,
                    f"{switch} works but is undocumented, so nobody will find it",
                )


class ReadmeCreditsTests(unittest.TestCase):
    """The credits and the walkthrough are part of the deliverable.

    A port of other people's work owes them attribution, and MeshCore in
    particular is the project this exists to join -- so its own site, not just a
    source link, is what a reader should be pointed at. These are cheap checks
    against documentation that is easy to drop in a refactor.
    """

    def setUp(self):
        self.text = README.read_text(encoding="utf-8")
        self.flat = re.sub(r"\s+", " ", self.text)

    def credits_section(self):
        start = self.text.find("## Credits")
        self.assertNotEqual(-1, start, "the README has no Credits section")
        return self.text[start:]

    def test_meshcore_is_credited_via_its_website(self):
        self.assertIn(
            "meshcore.io",
            self.credits_section(),
            "MeshCore must be credited via meshcore.io, not only a source link",
        )

    def test_the_upstream_projects_are_credited(self):
        credits = self.credits_section()

        for project in (
            "Archie3d/waveshare-usb-lora-firmware",
            "meshcore-go/meshcore-bot",
            "FreeRTOS",
            "libopencm3",
        ):
            with self.subTest(project=project):
                self.assertIn(
                    project,
                    credits,
                    f"{project} is used directly and should be credited",
                )

    def test_the_hardware_dependency_is_documented(self):
        """The SWD probe is the thing people do not know they need."""
        self.assertIn(
            "What you have to supply in hardware",
            self.flat,
            "the hardware a user must supply deserves its own section",
        )
        for token in ("SWD", "probe"):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_the_step_by_step_walkthrough_covers_the_whole_job(self):
        self.assertIn(
            "## Step by step",
            self.text,
            "a numbered walkthrough should exist for build/flash/run",
        )

        flat = self.flat
        for step in ("fetch-toolchain", "build.ps1", "ci.ps1", "flash-swd", "info"):
            with self.subTest(step=step):
                self.assertIn(
                    step,
                    flat,
                    f"the walkthrough should mention {step}",
                )

    def test_the_project_is_named_consistently(self):
        self.assertIn("# meshcore-waveshare-usb-lora", self.text)
        self.assertNotIn(
            "meshcore-usb-lora/",
            self.flat,
            "the old project name is still in a path somewhere",
        )

    def test_no_source_file_still_carries_the_old_name(self):
        """A rename that is only partly done is worse than not renaming.

        Two exemptions, both deliberate:

        * vendored trees are not ours to rename, and firmware/libopencm3's nvic.h
          is a *generated* file that embeds the absolute path of whoever ran
          irq2nvc_h last -- it is rebuilt from irq.json on every clean build, so
          editing it would be undone immediately;
        * this file names the old string on purpose, to assert it is gone.
        """
        vendored = ("firmware/rtos/", "firmware/sx126x/", "firmware/libopencm3/",
                    "toolchain/")
        generated_suffixes = {".bin", ".elf", ".map", ".a", ".exe", ".o", ".su", ".d"}

        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()

            if rel.startswith(vendored) or rel == "tests/test_entrypoints.py":
                continue
            if path.suffix in generated_suffixes or "obj" in rel.split("/"):
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            if OLD_NAME in text:
                offenders.append(rel)

        self.assertEqual(
            [],
            offenders,
            f"these files still refer to the old project name ({OLD_NAME}): "
            f"{offenders}",
        )


class ReadmeLinkTests(unittest.TestCase):
    """Internal links must resolve to headings that exist.

    A link to a renamed section still renders as a link and still fails silently,
    and the one that mattered here was `#flashing`, pointing at a heading whose
    full title had grown a subtitle. Rather than fix them by hand each time,
    the anchors are derived the way GitHub derives them and compared.
    """

    @staticmethod
    def slugify(heading):
        slug = heading.strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        return re.sub(r"\s+", "-", slug)

    def test_every_internal_link_resolves(self):
        text = README.read_text(encoding="utf-8")

        anchors = {
            self.slugify(h) for h in re.findall(r"^#{2,6}\s+(.+)$", text, re.M)
        }
        links = set(re.findall(r"\]\(#([\w-]+)\)", text))

        self.assertTrue(links, "no internal links found; this check is not working")

        missing = sorted(link for link in links if link not in anchors)
        self.assertEqual(
            [],
            missing,
            "these internal links point at headings that do not exist, so a "
            f"reader following them lands nowhere: {missing}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
