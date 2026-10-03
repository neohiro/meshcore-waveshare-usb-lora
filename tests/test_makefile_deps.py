"""The build must know which objects depend on which headers.

Without `-MMD -MP` and an `-include` of the resulting .d files, the firmware
Makefile's pattern rule can see only the .c file and the Makefile itself. Edit a
header and nothing rebuilds, so an incremental build links stale objects against
new ones and produces a binary that corresponds to no source state. That is the
worst kind of build bug because the build reports success and the firmware simply
misbehaves in a way nobody can trace back to a build.

CI does not catch it, because `tools/ci.ps1` passes `-Clean` and therefore always
rebuilds from scratch. Only a developer running an incremental build meets it,
which is exactly why it has to be asserted here.

The DEPS guard is deliberately specific. The first version of this fix wrapped the
object lists in `$(patsubst %.o, $(OUT_DIR)/%.d, ...)`, but those lists already
carry the obj/ prefix, so it asked for obj/obj/*.d. `-include` ignores files it
cannot find, so make fell back to having no dependencies at all and the build
silently kept the original bug.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "firmware" / "Makefile"
OBJ = ROOT / "firmware" / "obj"

FIRMWARE_SOURCES = ("main", "serial", "radio", "kiss", "kiss_frame")


def makefile_text():
    if not MAKEFILE.exists():
        raise AssertionError(f"missing {MAKEFILE}")
    return MAKEFILE.read_text(encoding="utf-8")


def code(text):
    """The Makefile with comments and recipe lines stripped."""
    out = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip() and not line.startswith("\t"):
            out.append(stripped)
    return "\n".join(out)


class DependencyTrackingTests(unittest.TestCase):
    def setUp(self):
        self.text = makefile_text()
        self.rules = code(self.text)

    def test_dependencies_are_requested_from_the_compiler(self):
        for flag in ("-MMD", "-MP"):
            with self.subTest(flag=flag):
                self.assertIn(
                    flag,
                    self.rules,
                    f"{flag} must be in CFLAGS or header edits will not "
                    "trigger a rebuild",
                )

    def test_the_generated_files_are_included(self):
        self.assertRegex(
            self.rules,
            r"-include\s+\$\(DEPS\)",
            "the .d files are useless unless make is told to read them",
        )

    def test_deps_paths_are_not_double_prefixed(self):
        """The obj/obj/*.d mistake, which fails silently."""
        self.assertNotRegex(
            self.rules,
            r"patsubst\s+%.o,\s*\$\(OUT_DIR\)",
            "the object lists already start with obj/, so adding OUT_DIR again "
            "yields obj/obj/*.d; -include ignores missing files and the build "
            "silently loses its dependencies",
        )

        self.assertRegex(
            self.rules,
            r"DEPS\s*=.*\$\(OBJS:\.o=\.d\)",
            "DEPS should swap the extension on the already-prefixed object list",
        )

    def test_deps_covers_every_object_list(self):
        for name in ("OBJS", "RTOS_OBJS", "SX126X_OBJS"):
            with self.subTest(list=name):
                self.assertRegex(
                    self.rules,
                    rf"DEPS\s*=.*\$\({name}:\.o=\.d\)",
                    f"{name} is not covered by DEPS, so its header changes would "
                    "be ignored",
                )


class DependencyFilesWereProducedTests(unittest.TestCase):
    """The flags only matter if the build actually emits the files."""

    def test_a_dependency_file_exists_per_firmware_source(self):
        missing = [n for n in FIRMWARE_SOURCES if not (OBJ / f"{n}.d").exists()]
        if missing:
            self.skipTest(f"dependency files absent ({missing}); build first")
        self.assertEqual([], missing)

    def test_a_dependency_file_records_the_headers_it_read(self):
        serial_d = OBJ / "serial.d"
        if not serial_d.exists():
            self.skipTest("obj/serial.d absent; build first")

        text = serial_d.read_text(encoding="utf-8")

        self.assertIn("src/serial.c", text, "no rule for the object itself")
        for header in ("src/kiss_frame.h", "src/pinout.h"):
            with self.subTest(header=header):
                self.assertIn(
                    header,
                    text,
                    f"{header} is included by serial.c but missing from its "
                    "dependency file, so editing it would not rebuild anything",
                )

    def test_the_dependency_file_names_the_object_it_belongs_to(self):
        serial_d = OBJ / "serial.d"
        if not serial_d.exists():
            self.skipTest("obj/serial.d absent; build first")

        first = serial_d.read_text(encoding="utf-8").splitlines()[0]

        self.assertRegex(
            first,
            r"^obj/serial\.o:",
            "the rule must target obj/serial.o, or make cannot attach the "
            f"prerequisites to it (got: {first!r})",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
