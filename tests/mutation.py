"""Deliberate source mutations, for proving a check actually fails.

A guard is only worth having if you have seen it fail. Temporarily breaking
something and confirming the right test notices is the only way to tell a real
check from one that passes for the wrong reason -- and it is easy to get wrong,
because the mutation can silently not apply.

That is not hypothetical. Twice in this project's history a mutation was applied
from the shell, quietly matched nothing, and the guard went on reporting a pass:

    * a comparison of `pathlib.Path(...) == "revert"` is always False, so the
      "revert" branch never ran and the suite passed against an unmodified tree;
    * a regex aimed at the wrong file reported success because it simply had no
      match to make.

Both looked identical to a passing proof. So mutations belong behind a helper
that refuses to run unless it can show the file changed, and that restores the
original bytes exactly afterwards.

This lives in tests/ because it is a testing tool, not part of the firmware or
the setup path.
"""

import contextlib
import pathlib


class MutationError(AssertionError):
    """A mutation could not be applied, so any resulting result is worthless."""


def _read(path):
    path = pathlib.Path(path)
    if not path.exists():
        raise MutationError(f"cannot mutate a file that does not exist: {path}")
    return path.read_bytes()


def _write(path, data):
    path.write_bytes(data)
    # Read back rather than trusting the write: this is the check that turns a
    # silent no-op into a loud failure.
    written = path.read_bytes()
    if written != data:
        raise MutationError(
            f"{path} does not match what was written; something else is "
            "modifying the tree, so a mutation here cannot be trusted"
        )


def apply(path, old, new, *, expected=1):
    """Replace `old` with `new` in `path`, insisting it happened exactly once.

    `expected=0` asserts the text is *absent*, which is how a guard against a
    removed hazard is checked.
    """
    path = pathlib.Path(path)
    original = _read(path)
    text = original.decode("utf-8")

    found = text.count(old)
    if found != expected:
        raise MutationError(
            f"{path.name}: expected {expected} occurrence(s) of the mutation "
            f"target, found {found}. A mutation that does not apply makes the "
            "result meaningless, so this refuses to continue."
        )

    if expected == 0:
        return original

    mutated = text.replace(old, new, expected).encode("utf-8")
    if mutated == original:
        raise MutationError(
            f"{path.name}: the mutation produced an identical file. The target "
            "text must differ from its replacement for the test to mean "
            "anything."
        )

    _write(path, mutated)
    return original


@contextlib.contextmanager
def preserved(path):
    """Mutate `path` for the duration of the block, then restore it exactly.

    Yields a one-argument callable for applying mutations:

        with preserved(SOURCE) as mutate:
            mutate("old", "new")
            ...
        # original bytes are back here, verified

    Restoration is verified rather than assumed, and it happens even if the body
    raises. Byte-for-byte, because the whole point is that the tree afterwards is
    indistinguishable from the tree before.
    """
    path = pathlib.Path(path)
    original = _read(path)

    def mutate(old, new, *, expected=1):
        # Re-check against the current content, so a second mutation in the same
        # block sees the first one's result rather than the original file.
        path.write_bytes(_apply_to(path, original, old, new, expected))

    try:
        yield mutate
    finally:
        _write(path, original)


def _apply_to(path, original, old, new, expected):
    text = path.read_text(encoding="utf-8")
    found = text.count(old)

    if found != expected:
        raise MutationError(
            f"{path.name}: expected {expected} occurrence(s) of the mutation "
            f"target, found {found}; the mutation would not apply"
        )

    if expected == 0:
        return path.read_bytes()

    mutated = text.replace(old, new, expected).encode("utf-8")
    if mutated == original:
        raise MutationError(
            f"{path.name}: the mutation left the file unchanged, so it cannot "
            "prove anything"
        )

    return mutated
