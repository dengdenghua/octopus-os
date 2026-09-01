"""Tests for per-thread workspace isolation.

The manager is deliberately small — just enough to prove:

* Allocation is idempotent and yields a predictable path.
* ``resolve_cwd`` prefers an explicit caller-supplied cwd.
* ``discard`` refuses to delete anything outside its configured root.
* Slug sanitisation handles the shapes a realtime thread id can take.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.platform.runtime_policy.workspaces import WorkspaceManager


class TestAllocate:
    def test_creates_directory(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate("th-1")
        assert ws.is_dir()
        assert ws.parent == tmp_path.resolve()
        assert (ws / ".gitignore").is_file()

    def test_creates_standard_workspace_protocol(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate("th-1")

        for rel in (
            "upload",
            "output",
            "output/stages",
            "output/final",
            "deploy",
            "skills",
        ):
            assert (ws / rel).is_dir()

        manifest = json.loads((ws / "workspace.json").read_text(encoding="utf-8"))
        assert manifest["schema"] == "echo.workspace.v1"
        assert manifest["thread_id"] == "th-1"
        assert manifest["slug"] == "th-1"
        assert manifest["dirs"] == {
            "upload": "upload",
            "output": "output",
            "stages": "output/stages",
            "final": "output/final",
            "deploy": "deploy",
            "skills": "skills",
        }

    def test_is_idempotent(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        first = mgr.allocate("th-1")
        (first / "existing.txt").write_text("keep me", encoding="utf-8")
        second = mgr.allocate("th-1")
        assert first == second
        assert (second / "existing.txt").read_text(encoding="utf-8") == "keep me"

    def test_parallel_threads_get_distinct_dirs(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        a = mgr.allocate("th-a")
        b = mgr.allocate("th-b")
        assert a != b
        assert a.parent == b.parent == tmp_path.resolve()

    def test_layout_exposes_named_paths(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        layout = mgr.layout("th-layout")

        assert layout.root == tmp_path.resolve() / "th-layout"
        assert layout.upload == layout.root / "upload"
        assert layout.stages == layout.root / "output" / "stages"
        assert layout.final == layout.root / "output" / "final"
        assert layout.deploy == layout.root / "deploy"
        assert layout.skills == layout.root / "skills"
        assert layout.manifest == layout.root / "workspace.json"
        assert Path(layout.as_dict()["final"]) == layout.final

    def test_manifest_method_repairs_invalid_manifest(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate("th-manifest")
        (ws / "workspace.json").write_text("not json", encoding="utf-8")

        manifest = mgr.manifest("th-manifest")

        assert manifest["schema"] == "echo.workspace.v1"
        assert manifest["thread_id"] == "th-manifest"


class TestResolveCwd:
    def test_explicit_wins(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        cwd = mgr.resolve_cwd("th-x", "/tmp/fixed")
        assert cwd == "/tmp/fixed"
        # Allocation does not happen when caller pinned cwd.
        assert not (tmp_path / "th-x").exists()

    def test_whitespace_explicit_falls_back(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        cwd = mgr.resolve_cwd("th-y", "   ")
        # Resolved absolute path, so compare via Path equality not str ==
        assert Path(cwd) == (tmp_path.resolve() / "th-y")

    def test_none_allocates(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        cwd = mgr.resolve_cwd("th-z", None)
        assert Path(cwd).is_dir()


class TestDiscard:
    def test_removes_workspace(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate("th-rm")
        (ws / "a.txt").write_text("x", encoding="utf-8")
        assert mgr.discard("th-rm") is True
        assert not ws.exists()

    def test_missing_workspace_is_noop(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        assert mgr.discard("never-existed") is False

    def test_discard_after_slug_change_is_safe(self, tmp_path: Path) -> None:
        # Sanity: a thread id mapped to a slug of all special chars
        # still lands inside root, so discard works on it.
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate("!!!!")
        assert ws.exists()
        assert mgr.discard("!!!!") is True


class TestSlugSafety:
    @pytest.mark.parametrize(
        "thread_id, expected_suffix",
        [
            ("th-abc", "th-abc"),
            ("th.123", "th.123"),
            ("th/../evil", "th_.._evil"),
            ("", "thread"),
        ],
    )
    def test_slug(self, tmp_path: Path, thread_id: str, expected_suffix: str) -> None:
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.allocate(thread_id)
        assert ws.name == expected_suffix
        # The hardening that matters: the allocation path always lands
        # *inside* root, regardless of what slashes / dots were in the
        # raw thread id.
        assert tmp_path.resolve() == ws.parent

    def test_traversal_attempt_stays_inside_root(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(tmp_path)
        # The slug keeps ``..`` as a literal substring (we allow ``.``
        # for legible thread ids), but path JOIN doesn't act on it
        # because the slash got sanitised first. Workspace lives under
        # root, period.
        ws = mgr.allocate("../escape")
        assert tmp_path.resolve() == ws.parent
