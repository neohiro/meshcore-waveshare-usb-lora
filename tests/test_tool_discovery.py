"""A missing prerequisite must say how to install it.

The scripts in tools/ discover their toolchain by searching PATH and known
install locations. That search returns nothing when a tool is absent, and an
empty search result is easy to mishandle: the run either continues and fails
somewhere confusing, or reports success having done nothing.

So two things are asserted here, for every shipped script:

1. discovery never silently continues -- a missing prerequisite must throw;
2. the error tells the user what to run.

The second one is the subtle half. "python not found" is technically correct
and practically useless: the reader still has to know which package, and on a
machine they may not. `tools/test.ps1` gets this right -- it says the host
compiler is needed rather than the ARM cross compiler, explains why, and gives
the exact winget command. That is the standard every other message should meet.
"""

import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

SCRIPTS = ("build.ps1", "test.ps1", "flash-swd.ps1", "fetch-toolchain.ps1")

# Phrases that mark an error as being about something not being present.
MISSING = re.compile(r"\b(not found|missing|cannot be|could not find)\b", re.I)

# A message is actionable if it says how to obtain, install or re-obtain the
# thing it complained about.
ACTIONABLE = re.compile(
    r"(winget install"
    r"|pip install"
    r"|Run tools\\"
    r"|fetch-toolchain"
    r"|re-clone"
    r"|https?://)",
    re.I,
)


def script_text(name):
    path = TOOLS / name
    if not path.exists():
        raise unittest.SkipTest(f"{name} not present")
    return path.read_text(encoding="utf-8")


def strip_ps_comments(text):
    """Remove comment-based help blocks and whole-line `#` comments.

    Without this, prose in a script's .SYNOPSIS or .DESCRIPTION is audited as if it
    were an error message: a help block that happens to say "throw ... when it is
    missing" reads as an unactionable error, which is both a false failure and a
    way for a genuinely bad message to hide behind helpful documentation.
    """
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def throw_blocks(text):
    """The body of each throw, joining here-strings across their lines.

    Both here-string flavours have to be handled. Recognising only `@'` would
    split a `throw @"..."` down the middle, leaving a fragment that looks like a
    separate error message -- and the fragment no longer contains the advice, so
    the actionability check below would fail on a message that is in fact fine.
    """
    lines = strip_ps_comments(text).splitlines()
    blocks = []

    index = 0
    while index < len(lines):
        line = lines[index]
        if "throw" not in line:
            index += 1
            continue

        opener = "@'" if "@'" in line else ('@"' if '@"' in line else None)
        if opener is not None:  # here-string: runs to the closing delimiter
            closer = "'@" if opener == "@'" else '"@'
            body = [line]
            index += 1
            while index < len(lines) and lines[index].strip() != closer:
                body.append(lines[index])
                index += 1
            blocks.append("\n".join(body))
        else:
            blocks.append(line)
            index += 1

    return blocks


class PrerequisiteErrorsAreActionableTests(unittest.TestCase):
    def test_every_script_exists(self):
        missing = [name for name in SCRIPTS if not (TOOLS / name).exists()]
        self.assertEqual([], missing, f"missing scripts: {missing}")

    def test_every_discovered_tool_is_branched_on(self):
        """A discovery result must never be used without being checked.

        Too crude to demand a `throw` on sight: `fetch-toolchain.ps1` searches
        inside an unpacked archive and legitimately handles both outcomes with
        `if ($inner) { ... } else { ... }`. What must not happen is a discovered
        value being consumed as though it were certainly there.
        """
        assigned = re.compile(
            r"^\s*\$(?P<var>\w+)\s*=\s*(?:\(.*?\)\s*)?Get-(?:Command|ChildItem)",
            re.M,
        )

        for name in SCRIPTS:
            text = script_text(name)

            for match in assigned.finditer(text):
                var = match.group("var")

                # Used in any conditional, or negated somewhere. PowerShell
                # variables are $-prefixed, so allow the sigil to be present or
                # absent: the script may write $foo or $foo in an expression.
                branched = re.search(
                    rf"(if\s*\(\s*!?\s*-?not\s+\$?\s*{var}\b)"
                    rf"|(if\s*\(\s*\$?\s*{var}\b)"
                    rf"|(-not\s+\$?\s*{var}\b)",
                    text,
                )

                with self.subTest(script=name, variable=var):
                    self.assertIsNotNone(
                        branched,
                        f"{name} discovers ${var} but never checks it, so an "
                        "empty search result would be used as though it were "
                        "a real path",
                    )

    def test_tools_searched_on_path_must_fail_loudly(self):
        """Get-Command is looking for an executable: absent means stop.

        Unlike the archive case above, there is no sensible way to carry on
        without the program, so these must throw rather than warn.
        """
        for name in SCRIPTS:
            text = script_text(name)
            if "Get-Command" not in text:
                continue

            with self.subTest(script=name):
                self.assertIn(
                    "throw",
                    text,
                    f"{name} looks for a program with Get-Command but never "
                    "throws when it is absent",
                )

    def test_missing_thing_errors_explain_how_to_get_it(self):
        for name in SCRIPTS:
            for block in throw_blocks(script_text(name)):
                if not MISSING.search(block):
                    continue

                # A here-string may hold several messages; judge it as a whole.
                with self.subTest(script=name, block=block.splitlines()[0][:60]):
                    self.assertRegex(
                        block,
                        ACTIONABLE,
                        "this error is about something missing but does not "
                        "say how to obtain it:\n" + block.strip(),
                    )

    def test_no_error_uses_a_bare_not_found(self):
        """Guards the exact wording that started this: 'X not found' and stop."""
        bare = re.compile(r"['\"][^'\"]*\bnot found\b[^'\"]*['\"]", re.I)

        for name in SCRIPTS:
            for block in throw_blocks(script_text(name)):
                for quoted in bare.findall(block):
                    with self.subTest(script=name, message=quoted):
                        self.assertRegex(
                            quoted,
                            ACTIONABLE,
                            "this message names the problem but not the fix",
                        )


class PrerequisitesAreDocumentedTests(unittest.TestCase):
    """The README's prerequisite list must cover what the scripts actually use.

    A missing entry is a papercut rather than a bug: the scripts do throw with the
    exact winget command, so a user who hits it can recover. But the list is the
    first thing anyone reads, and it was missing two tools that the default
    `ci.ps1` run genuinely needs -- a host C compiler for the native tests and Go
    for the contract step. The compiler one bites hardest, because "make" is
    listed and it is easy to assume that covers building C.

    This keeps the list honest against the scripts, rather than trusting it.
    """

    # Tools the scripts look up with Get-Command, and the words that count as
    # documenting them.
    ALIASES = {
        "gcc": ("gcc", "c compiler", "mingw", "winlibs"),
        "make": ("make",),
        "go": ("go",),
        "python": ("python",),
        "pyocd": ("pyocd",),
        "ruff": ("ruff",),
    }

    # Looked up but not required: flash-swd.ps1 installs pyocd itself, and ci.ps1
    # skips the lint when ruff is absent. They still have to be documented
    # somewhere, because "ruff not installed, skipping" is the only clue a user
    # gets otherwise.
    OPTIONAL = {"pyocd", "ruff"}

    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def prerequisites_section(self):
        """The bullet list under '## Prerequisites'."""
        lines = self.readme.splitlines()
        start = None
        for index, line in enumerate(lines):
            if line.startswith("## Prerequisites"):
                start = index
                break
        if start is None:
            raise AssertionError("the README has no Prerequisites section")

        section = []
        for line in lines[start + 1 :]:
            if line.startswith("## "):
                break
            section.append(line)

        return "\n".join(section)

    def scripts_use_these_tools(self):
        used = set()
        for path in sorted(TOOLS.glob("*.ps1")):
            text = path.read_text(encoding="utf-8")
            used.update(re.findall(r"Get-Command\s+(\w+)", text))
        return used

    def test_the_section_exists_and_lists_something(self):
        section = self.prerequisites_section()
        self.assertIn("-", section, "the prerequisites section has no bullet list")

    def test_every_tool_the_scripts_need_is_listed(self):
        section = self.prerequisites_section().lower()

        undocumented = []
        for tool in sorted(self.scripts_use_these_tools()):
            words = self.ALIASES.get(tool.lower())
            if words is None or tool.lower() in self.OPTIONAL:
                continue
            if not any(word in section for word in words):
                undocumented.append(tool)

        self.assertEqual(
            [],
            undocumented,
            "these tools are looked up by the scripts but not listed under "
            f"## Prerequisites: {undocumented}",
        )

    def test_optional_tools_are_still_documented(self):
        readme = self.readme.lower()

        for tool in sorted(self.OPTIONAL):
            words = self.ALIASES[tool]
            with self.subTest(tool=tool):
                self.assertTrue(
                    any(word in readme for word in words),
                    f"{tool} is optional but undocumented, so the only warning "
                    "a user ever sees is the script skipping it",
                )

    def test_the_host_compiler_is_distinguished_from_the_arm_one(self):
        """The confusion that motivated adding the entry.

        The native tests need a compiler that runs on this machine; the firmware
        needs one that targets the GD32F103. Saying so prevents the most likely
        wrong conclusion, that the fetched ARM toolchain covers both.
        """
        section = self.prerequisites_section()

        self.assertRegex(
            section,
            r"(?i)host c compiler",
            "the prerequisites must name a host C compiler, not just 'a compiler'",
        )
        self.assertRegex(
            self.readme,
            r"(?i)host compiler and the arm cross compiler are different",
            "the README should say the host and ARM compilers are not "
            "interchangeable",
        )


class PowerShellArgumentHazardsTests(unittest.TestCase):
    """Two PowerShell traps that both fail by passing the wrong tokens along.

    Neither shows up as a syntax error, and both have already cost real time in
    this repository:

    1. Splatting an array onto a backtick-continued line. `@args` there is not
       treated as a splat, so the `@` and the name arrive at the called script's
       parameter binder as ordinary arguments and it reports the misleading
       "Cannot process argument because the value of argument name is not
       valid". This bit ci.ps1 while building the firmware variants.

    2. Assigning to `$args`, which is PowerShell's automatic variable holding a
       script's or function's arguments. It works at script scope, which is why
       it survives review, and then silently means something else inside any
       function added later. flash-swd.ps1 used it to build the pyOCD command
       line -- the one command that erases the vendor firmware.
    """

    def scripts(self):
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(TOOLS.glob("*.ps1"))
        }

    def test_no_script_assigns_to_the_automatic_args_variable(self):
        offenders = [
            name
            for name, text in self.scripts().items()
            if re.search(r"^\s*\$args\s*(?:=|\+=)", text, re.M)
        ]

        self.assertEqual(
            [],
            offenders,
            "$args is PowerShell's automatic argument variable; assigning to it "
            "works at script scope and then means something else inside any "
            f"function: {offenders}",
        )

    def test_no_splat_appears_on_a_continued_native_call(self):
        offenders = []

        for name, text in self.scripts().items():
            lines = text.splitlines()

            for index, line in enumerate(lines[:-1]):
                if not line.rstrip().endswith("`"):
                    continue

                # Find where this continued statement began.
                start = index
                while start > 0 and lines[start - 1].rstrip().endswith("`"):
                    start -= 1

                # Only native/cmdlet invocations are affected.
                if not re.match(r"^\s*&\s", lines[start]):
                    continue

                splat = re.search(r"(?<![\w@])@(\w+)", lines[index + 1])
                if splat:
                    offenders.append(f"{name}:{start + 1} (@{splat.group(1)})")

        self.assertEqual(
            [],
            offenders,
            "a splat on a backtick-continued native call is passed through as "
            "literal tokens, so the called script fails with a misleading "
            f"parameter error: {offenders}",
        )

    def test_the_flash_script_builds_its_command_line_explicitly(self):
        text = self.scripts().get("flash-swd.ps1", "")

        self.assertRegex(
            text,
            r"\$pyocdArgs",
            "flash-swd.ps1 should name its pyOCD argument list explicitly",
        )
        self.assertRegex(
            text,
            r"& pyocd @pyocdArgs",
            "the invocation should splat that array on a line of its own",
        )


class ToolchainFetchTests(unittest.TestCase):
    """The one script that reaches the network has to survive a bad answer.

    This is the only part of the setup that a fresh clone depends on before
    anything else can work, and it failed exactly once in practice: SourceForge
    answered a request for make.zip with a 535 KB HTML mirror-selection page.
    Expand-Archive then reported "End of Central Directory record could not be
    found", which names neither the cause nor a way forward.

    So the download is verified before it is unpacked, and make is no longer
    fetched at all -- tools/build.ps1 already finds it on PATH or in the winget
    cache, and throws with the exact install command when it is missing.
    """

    SCRIPT = TOOLS / "fetch-toolchain.ps1"

    def setUp(self):
        if not self.SCRIPT.exists():
            raise unittest.SkipTest("fetch-toolchain.ps1 not present")
        self.text = self.SCRIPT.read_text(encoding="utf-8")

    def test_make_is_not_vendored(self):
        """Vendoring make duplicated what build.ps1 already resolves."""
        self.assertNotIn(
            "make.zip",
            self.text,
            "make should not be downloaded; build.ps1 finds it or explains how "
            "to install it",
        )
        # Check the URL, not the word: the script's comment-based help explains
        # why make is no longer fetched, and that explanation is worth keeping.
        self.assertNotIn(
            "downloads.sourceforge.net",
            self.text,
            "the SourceForge mirror redirect is what served HTML instead of a zip",
        )

    def test_only_the_arm_toolchain_is_fetched(self):
        self.assertIn("xpack-arm-none-eabi-gcc.zip", self.text)
        self.assertIn("dir  = 'arm-gcc'", self.text)

    def test_a_download_is_checked_before_it_is_unpacked(self):
        self.assertIn(
            "Test-ZipFile",
            self.text,
            "the archive must be verified before Expand-Archive, or a stray HTML "
            "page becomes an unexplained compression error",
        )

    def test_the_error_explains_itself(self):
        for phrase in ("not a zip archive", "URL:", "extract it to"):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    self.text,
                    "the failure must say what happened and what to do next",
                )


class ToolchainFetchBehaviourTests(unittest.TestCase):
    """Run the fetch script against a planted non-archive.

    No network needed: the script skips the download when the file already
    exists, so planting an HTML page where the zip belongs exercises exactly the
    validation path that a mirror page used to fall through.
    """

    ARCHIVE_NAME = "xpack-arm-none-eabi-gcc.zip"

    def setUp(self):
        import shutil

        self.shutil = shutil
        if shutil.which("powershell") is None:
            raise unittest.SkipTest("PowerShell not available")

        self._tmp = tempfile.TemporaryDirectory()
        self.dest = pathlib.Path(self._tmp.name)

        (self.dest / self.ARCHIVE_NAME).write_bytes(
            b"<!doctype html><html><head><title>mirror</title></head></html>"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self):
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
"-ExecutionPolicy",
            "Bypass",
                "-File",
                str(TOOLS / "fetch-toolchain.ps1"),
                "-Dest",
                str(self.dest),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_it_rejects_an_html_page_instead_of_a_zip(self):
        result = self.run_script()
        output = result.stdout + result.stderr

        self.assertNotEqual(
            0,
            result.returncode,
            f"an HTML page was accepted as an archive:\n{output}",
        )
        self.assertIn(
            "not a zip archive",
            output,
            "the message must say the download was not an archive",
        )

    def test_it_does_not_leave_the_bad_file_behind(self):
        self.run_script()

        self.assertFalse(
            (self.dest / self.ARCHIVE_NAME).exists(),
            "the unusable download must be deleted, or the next run reuses it",
        )

    def test_it_does_not_create_a_half_extracted_directory(self):
        self.run_script()

        self.assertFalse(
            (self.dest / "arm-gcc").exists(),
            "nothing should be unpacked from a file that is not an archive",
        )


class ScriptsMustRunWithoutArgumentsTests(unittest.TestCase):
    """A script documented as `tools\\name.ps1` has to accept no arguments.

    Every one of these is invoked bare somewhere a reader will copy from: the
    README, setup.ps1's help, and build.ps1's own "run tools\\fetch-toolchain.ps1
    first" message. A non-switch parameter with no default makes that documented
    command fail at parameter binding, and PowerShell's report of it names the
    wrong thing entirely -- fetch-toolchain.ps1 said "Cannot bind argument to
    parameter 'Path' because it is an empty string", which never mentions that
    the caller left out a parameter the documentation told them to leave out.

    That is not hypothetical: it is how the first GitHub Actions run failed, on a
    runner with no toolchain present. It could not happen locally, because the
    toolchain was already there.
    """

    # [type]$Name optionally followed by "=", up to the next comma or newline.
    DECLARATION = re.compile(
        r"\[(string|int|long|switch)\]\s*\$(\w+)\s*(=|,|\r?\n|$)"
    )

    def test_no_non_switch_parameter_is_required(self):
        for name in SCRIPTS:
            text = script_text(name)

            param = re.search(r"^param\((.*?)^\)", text, re.S | re.M)
            self.assertIsNotNone(
                param, f"{name} should declare its parameters in a param() block"
            )

            for kind, param_name, terminator in self.DECLARATION.findall(
                param.group(1)
            ):
                if kind == "switch":
                    continue

                self.assertEqual(
                    "=",
                    terminator,
                    f"{name}'s ${param_name} has no default, so the documented "
                    f"bare invocation `tools\\{name}` cannot work",
                )

    def test_the_declarations_were_actually_found(self):
        """Guards the test above against matching nothing and passing vacuously."""
        for name in SCRIPTS:
            param = re.search(
                r"^param\((.*?)^\)", script_text(name), re.S | re.M
            )
            found = self.DECLARATION.findall(param.group(1))

            self.assertTrue(
                found,
                f"no parameter declarations were recognised in {name}, so the "
                f"check above would pass without looking at anything",
            )

    def test_the_documented_invocation_is_the_bare_one(self):
        """The fetch script's own help must not promise a form that fails."""
        text = script_text("fetch-toolchain.ps1")

        self.assertIn("powershell -File tools\\fetch-toolchain.ps1", text)
        self.assertNotIn(
            "fetch-toolchain.ps1 -Dest",
            text,
            "the example should not need a destination, because the default is right",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
