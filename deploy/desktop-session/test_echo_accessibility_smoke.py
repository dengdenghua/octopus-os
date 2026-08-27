#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("echo-accessibility-smoke.py")
LOADER = importlib.machinery.SourceFileLoader("echo_accessibility_smoke", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MODULE)


class FakeAccessible:
    def __init__(self, name: str, pid: int, children: list["FakeAccessible"] | None = None):
        self.name = name
        self._pid = pid
        self._children = children or []

    @property
    def childCount(self) -> int:
        return len(self._children)

    def getChildAtIndex(self, index: int) -> "FakeAccessible":
        return self._children[index]

    def getApplication(self) -> "FakeAccessible":
        return self

    def get_process_id(self) -> int:
        return self._pid


def write_status(proc_root: Path, pid: int, parent: int) -> None:
    process_dir = proc_root / str(pid)
    process_dir.mkdir()
    (process_dir / "status").write_text(
        f"Name:\ttest\nPid:\t{pid}\nPPid:\t{parent}\n", encoding="utf-8"
    )


class AccessibilitySmokeTests(unittest.TestCase):
    def test_accepts_marker_from_descendant_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            write_status(proc_root, 100, 1)
            write_status(proc_root, 101, 100)
            desktop = FakeAccessible("desktop", 1, [FakeAccessible("fixed", 101)])
            self.assertTrue(
                MODULE.marker_belongs_to_process(desktop, "fixed", 100, proc_root)
            )

    def test_rejects_same_marker_from_unrelated_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            write_status(proc_root, 100, 1)
            write_status(proc_root, 201, 1)
            desktop = FakeAccessible("desktop", 1, [FakeAccessible("fixed", 201)])
            self.assertFalse(
                MODULE.marker_belongs_to_process(desktop, "fixed", 100, proc_root)
            )

    def test_requires_exact_marker_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            write_status(proc_root, 100, 1)
            desktop = FakeAccessible("desktop", 1, [FakeAccessible("private fixed text", 100)])
            self.assertFalse(
                MODULE.marker_belongs_to_process(desktop, "fixed", 100, proc_root)
            )

    def test_tree_walk_is_bounded_when_child_access_fails(self) -> None:
        class BrokenAccessible(FakeAccessible):
            @property
            def childCount(self) -> int:
                raise RuntimeError("gone")

        broken = BrokenAccessible("", 1)
        self.assertEqual(list(MODULE.walk_accessibles(broken)), [broken])


if __name__ == "__main__":
    unittest.main()
