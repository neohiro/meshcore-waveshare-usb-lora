"""The firmware's RAM budget, checked before it ever reaches the chip.

Two numbers decide whether this firmware runs, and neither is visible to any
other test in the project:

1. The FreeRTOS heap. Every task and queue is carved out of
   `configTOTAL_HEAP_SIZE` at runtime. If the allocations exceed it,
   `configASSERT` fires inside pvPortMalloc -- on hardware, on first boot, with
   no useful context. All the host-side protocol tests would still pass.

2. The MSP headroom. On Cortex-M3 the tasks run on the PSP but every interrupt
   runs on the MSP, which grows down from the top of RAM. The static footprint
   therefore has to leave room under it.

The struct sizes are measured, not estimated: TCB_t and Queue_t are private to
tasks.c and queue.c, so the test compiles a probe that includes those
translation units and reads the array sizes back out of the object file. That
way a vendored FreeRTOS update cannot quietly invalidate the arithmetic.
"""

import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
SRC = FIRMWARE / "src"
RTOS = FIRMWARE / "rtos"
LINKER = FIRMWARE / "gd32f103.ld"
ELF = FIRMWARE / "firmware.elf"
CONFIG = SRC / "FreeRTOSConfig.h"
BIN = ROOT / "tools" / "bin"

# heap_4 rounds every allocation up to portBYTE_ALIGNMENT after adding one
# BlockLink_t header, so an allocation of n bytes costs round_up(n + hdr, 8).
# Measured below; the floor is what the alignment could not go below.
ALIGNMENT = 8

# Headroom MSP must keep for the ISRs: SysTick, PendSV, SVC, the radio's
# EXTI0 handler and the USART handler all run on it.
MIN_MSP_HEADROOM = 1024

# The heap must not be nearly full, or a later allocation has no room and the
# failure is a runtime assert rather than a build error.
MIN_HEAP_FREE_FRACTION = 0.20

PROBE_TCB = """
#include "tasks.c"
char probe_tcb[sizeof(TCB_t)];
char probe_stacktype[sizeof(StackType_t)];
"""

PROBE_QUEUE = """
#include "queue.c"
char probe_queue[sizeof(Queue_t)];
"""

PROBE_HEAP = """
#include "heap_4.c"
char probe_blocklink[sizeof(BlockLink_t)];
"""


def tool(name):
    """Locate an xPack toolchain binary, or skip."""
    import glob
    import shutil

    found = shutil.which(name)
    if found:
        return found

    pattern = str(ROOT / "toolchain" / "**" / (name + "*"))
    matches = sorted(glob.glob(pattern, recursive=True))
    if matches:
        return matches[0]

    raise unittest.SkipTest(f"{name} not found; build the firmware first")


def compile_probe(source, name, tmp):
    """Compile a probe that exposes FreeRTOS' private struct sizes."""
    cc = tool("arm-none-eabi-gcc")
    path = tmp / f"{name}.c"
    path.write_text(source, encoding="utf-8")

    obj = tmp / f"{name}.o"
    result = subprocess.run(
        [
            cc,
            "-mcpu=cortex-m3",
            "-mthumb",
            "-Os",
            "-ffunction-sections",
            "-fdata-sections",
            "-nostdlib",
            "-DSTM32F1",
            "-DWITH_TCXO=1",
            f"-I{FIRMWARE}",
            f"-I{SRC}",
            f"-I{RTOS}",
            f"-I{FIRMWARE / 'libopencm3' / 'include'}",
            "-c",
            str(path),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not obj.exists():
        raise unittest.SkipTest(f"probe {name} did not compile:\n{result.stderr}")

    return obj


def probe_sizes(obj):
    """Read `char probe_x[N]` sizes out of an object file."""
    nm = tool("arm-none-eabi-nm")
    out = subprocess.run([nm, "-S", str(obj)], capture_output=True, text=True).stdout

    sizes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3].startswith("probe_"):
            sizes[parts[3]] = int(parts[1], 16)

    if not sizes:
        raise unittest.SkipTest(f"no probe symbols found in {obj.name}")

    return sizes


def config_int(name):
    """An integer from FreeRTOSConfig.h, evaluated for simple `n * 1024` forms."""
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(
        rf"#define\s+{name}\s+\(\s*\(\s*size_t\s*\)\s*(.+?)\)", text, re.S
    )
    if match is None:
        match = re.search(rf"#define\s+{name}\s+(.+)", text)

    if match is None:
        raise AssertionError(f"{name} not found in FreeRTOSConfig.h")

    expr = match.group(1).replace("(", "").replace(")", "").replace(";", "")
    expr = re.sub(r"\(\s*size_t\s*\)", "", expr)
    expr = re.sub(r"\(\s*unsigned\s+short\s*\)", "", expr)
    expr = expr.replace("size_t", "").replace("unsigned short", "").strip()

    if not re.fullmatch(r"[\d\s*+\-]+", expr):
        raise AssertionError(f"cannot evaluate {name} = {expr!r}")

    return eval(expr)  # noqa: S307 - a validated arithmetic expression only


def define_int(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"#define\s+{name}\s+\(?\s*(\d+)\s*\)?", text)
    if match is None:
        raise AssertionError(f"{name} not found in {path.name}")
    return int(match.group(1))


def task_depths():
    """Stack depths passed to xTaskCreate, in words, across the firmware."""
    depths = []
    for path in sorted(SRC.glob("*.c")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"xTaskCreate\([^;]*?,\s*(\d+)\s*,", text, re.S):
            depths.append(int(match.group(1)))
    return depths


def queue_requests():
    """(length, item_size) for each xQueueCreate in the firmware.

    The size argument is matched separately from the length: it is normally
    `sizeof(uint8_t)`, whose closing parenthesis would otherwise truncate a
    single combined pattern and quietly yield an item size of zero.
    """
    out = []
    for path in sorted(SRC.glob("*.c")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "xQueueCreate(" not in line:
                continue

            length_match = re.search(r"xQueueCreate\(\s*(\w+)", line)
            if length_match is None:
                continue
            length = define_int(SRC / "serial.h", length_match.group(1))

            size_match = re.search(r"sizeof\(\s*(\w+)\s*\)", line)
            if size_match is None:
                raise AssertionError(
                    f"{path.name}: cannot read the item size of "
                    f"xQueueCreate({length_match.group(1)}, ...): {line.strip()}"
                )
            item_size = {"uint8_t": 1, "char": 1, "int": 4}.get(size_match.group(1))
            if item_size is None:
                raise AssertionError(
                    f"{path.name}: unknown item type {size_match.group(1)}"
                )

            out.append((length, item_size))

    return out


def mutex_count():
    total = 0
    for path in sorted(SRC.glob("*.c")):
        text = path.read_text(encoding="utf-8")
        total += len(re.findall(r"xSemaphoreCreateMutex\s*\(", text))
    return total


def section_sizes():
    """.data and .bss sizes from the linked image."""
    size = tool("arm-none-eabi-size")
    out = subprocess.run(
        [size, "-A", str(ELF)], capture_output=True, text=True
    ).stdout

    sizes = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] in (".data", ".bss", ".text") and len(parts) >= 2:
            sizes[parts[0]] = int(parts[1])

    return sizes


def linker_ram_bytes():
    text = LINKER.read_text(encoding="utf-8")
    match = re.search(
        r"\bram\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*0x[0-9A-Fa-f]+\s*,\s*LENGTH\s*=\s*(\d+)K",
        text,
    )
    if match is None:
        raise AssertionError("could not parse the ram region")
    return int(match.group(1)) * 1024


def allocation_cost(requested, header):
    """What heap_4 actually consumes for a request of `requested` bytes."""
    padded = requested + header
    remainder = padded % ALIGNMENT
    return padded if remainder == 0 else padded + (ALIGNMENT - remainder)


def linked_function_names():
    """Function symbols that survive --gc-sections, i.e. those that ship."""
    nm = tool("arm-none-eabi-nm")
    out = subprocess.run([nm, str(ELF)], capture_output=True, text=True).stdout

    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in ("T", "t", "W", "w"):
            names.add(parts[2])

    return names


def stack_frames():
    """Function -> stack frame bytes, from the .su files the build leaves.

    The firmware Makefile passes -fstack-usage, so every compiled object has a
    sibling .su file: `file:line:col:function<TAB>bytes<TAB>qualifiers`.
    """
    obj_dir = FIRMWARE / "obj"
    frames = {}

    for path in sorted(obj_dir.glob("*.su")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[0].split(":")[-1]
            try:
                frames[name] = int(parts[1])
            except ValueError:
                continue

    return frames


class FreeRTOSHeapBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ELF.exists():
            raise unittest.SkipTest("firmware not built yet")

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(cls._tmp.name)

        cls.tcb = probe_sizes(compile_probe(PROBE_TCB, "probe_tcb", tmp))
        cls.queue = probe_sizes(compile_probe(PROBE_QUEUE, "probe_queue", tmp))
        cls.heap = probe_sizes(compile_probe(PROBE_HEAP, "probe_heap", tmp))

        # heap_4.c: xHeapStructSize = (sizeof(BlockLink_t) + (ALIGN - 1))
        # & ~(ALIGN - 1), i.e. sizeof(BlockLink_t) rounded up to the alignment.
        cls.header = allocation_cost(0, cls.heap["probe_blocklink"])

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_struct_sizes_were_measured(self):
        for label, table, key in (
            ("TCB", self.tcb, "probe_tcb"),
            ("Queue", self.queue, "probe_queue"),
            ("BlockLink", self.heap, "probe_blocklink"),
        ):
            with self.subTest(struct=label):
                self.assertIn(key, table)
                self.assertGreater(table[key], 0)

    def test_stack_type_is_32_bit(self):
        self.assertEqual(4, self.tcb["probe_stacktype"])

    def test_the_firmware_would_fit_the_freeertos_heap(self):
        header = self.header
        cost = 0
        breakdown = []

        idle = config_int("configMINIMAL_STACK_SIZE")
        for depth in task_depths() + [idle]:
            task = allocation_cost(self.tcb["probe_tcb"], header)
            stack = allocation_cost(depth * 4, header)
            cost += task + stack
            breakdown.append(f"task({depth} words) = {task + stack}")

        for length, item in queue_requests():
            one = allocation_cost(self.queue["probe_queue"] + length * item, header)
            cost += one
            breakdown.append(f"queue({length}x{item}) = {one}")

        for _ in range(mutex_count()):
            one = allocation_cost(self.queue["probe_queue"], header)
            cost += one
            breakdown.append(f"mutex = {one}")

        heap = config_int("configTOTAL_HEAP_SIZE")
        free = heap - cost

        self.assertGreater(
            free,
            heap * MIN_HEAP_FREE_FRACTION,
            f"the FreeRTOS heap needs {cost} of {heap} bytes, leaving only "
            f"{free} ({free / heap:.0%}). Overcommit surfaces as a "
            f"configASSERT on first boot, not as a build failure. Breakdown: "
            + ", ".join(breakdown),
        )

    def test_the_parsing_found_the_allocations(self):
        """Guards against this module passing by finding nothing."""
        self.assertGreaterEqual(len(task_depths()), 3, "expected 3 tasks")
        self.assertGreaterEqual(len(queue_requests()), 2, "expected 2 queues")
        self.assertGreaterEqual(mutex_count(), 1, "expected at least one mutex")


class TaskStackUsageTests(unittest.TestCase):
    """No linked function may claim a large share of a task's stack.

    GCC is asked for `-fstack-usage` by the firmware Makefile, which leaves a .su
    file per object in obj/. That makes the frames measurable here rather than on
    the hardware, where a stack overflow does not crash cleanly: on Cortex-M3 the
    task stacks live on the PSP, so overflowing one corrupts another task's data
    and the symptom is a protocol bug that looks random.

    Only functions that survive --gc-sections are considered. The .su files
    describe everything that was *compiled*, including SX126x features this
    firmware never calls, and a dead 288-byte frame is not a risk.

    This is a per-frame bound, not a whole-call-chain analysis. A real bound would
    need a call graph, and this codebase dispatches through function pointers
    (`handler->frame_received` and the sx126x callbacks) whose targets a static
    pass cannot resolve. The deepest chain therefore runs through an indirect
    call this cannot see, so treat the result as a floor on what is safe.
    """

    # A frame may take at most this share of the largest task stack. Large
    # enough for the deepest real frame, small enough to catch a local buffer:
    # the regression below was 536 bytes, 52% of the 1024 byte stack.
    MAX_SHARE = 0.25

    # serial_rx_task's own frame, once the 256-byte parser is file scope.
    RX_TASK_FRAME_LIMIT = 64

    @classmethod
    def setUpClass(cls):
        if not ELF.exists():
            raise unittest.SkipTest("firmware not built yet")

        linked = linked_function_names()
        cls.frames = {
            name: size
            for name, size in stack_frames().items()
            if name in linked
        }

        if not cls.frames:
            raise unittest.SkipTest("no linked .su frames; build the firmware first")

        depths = task_depths() + [config_int("configMINIMAL_STACK_SIZE")]
        cls.largest_task_bytes = max(depths) * 4

    def test_frames_were_measured(self):
        self.assertGreater(
            len(self.frames), 15, f"only measured {len(self.frames)} linked functions"
        )

    def test_no_frame_dominates_a_task_stack(self):
        limit = self.largest_task_bytes * self.MAX_SHARE
        offenders = sorted(
            ((name, size) for name, size in self.frames.items() if size > limit),
            key=lambda item: -item[1],
        )

        self.assertEqual(
            [],
            offenders,
            f"these functions need more than {limit:.0f} bytes, "
            f"{self.MAX_SHARE:.0%} of the largest task stack "
            f"({self.largest_task_bytes} bytes): {offenders}. A large local "
            "buffer should be file scope rather than a local",
        )

    def test_the_rx_parser_is_not_on_the_task_stack(self):
        """Guards the regression that started this.

        The reassembly parser embeds a KISS_MAX_FRAME_SIZE buffer and used to be a
        local of serial_rx_task, where it measured 536 bytes against a 1024 byte
        stack. It is file scope now, and this measures the consequence directly
        rather than trusting the declaration.
        """
        self.assertIn(
            "serial_rx_task",
            self.frames,
            "serial_rx_task was not measured, so this check would pass vacuously",
        )
        self.assertLessEqual(
            self.frames["serial_rx_task"],
            self.RX_TASK_FRAME_LIMIT,
            "serial_rx_task's frame grew back: the KISS parser is a local again",
        )


class MSPHeadroomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ELF.exists():
            raise unittest.SkipTest("firmware not built yet")
        cls.sizes = section_sizes()

    def test_static_ram_was_measured(self):
        self.assertIn(".data", self.sizes)
        self.assertIn(".bss", self.sizes)

    def test_interrupts_have_room_to_run(self):
        ram = linker_ram_bytes()
        static = self.sizes[".data"] + self.sizes[".bss"]
        headroom = ram - static

        self.assertGreater(
            headroom,
            MIN_MSP_HEADROOM,
            f"static RAM is {static} bytes of {ram}, leaving {headroom} bytes "
            "of MSP for the interrupts. The task stacks live on the PSP, but "
            "SysTick, PendSV, the radio's EXTI0 handler and the USART handler "
            "all run on the MSP, and an overflow there corrupts memory in ways "
            "that look like random protocol bugs",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
