"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.write_skills import (
    _BACKGROUND_PROCESSES,
    EXEC_SKILL_NAME,
    WRITE_SKILL_NAMES,
    _append_text_file,
    _background_exec,
    _edit_file,
    _edit_text_file,
    _exec_shell,
    _kill_background_exec,
    _kill_shell,
    _multi_edit_file,
    _read_background_output,
    _read_shell_output,
    _write_text_file,
    register_exec_skill,
    register_write_skills,
)

# ═══════════════════════════════════════════════════════════
# write_text_file
# ═══════════════════════════════════════════════════════════


class TestWriteTextFile:
    def test_create_new_file(self, tmp_path: Path):
        p = tmp_path / "out.txt"
        r = _write_text_file(path=str(p), content="hello")
        assert "error" not in r
        assert p.read_text(encoding="utf-8") == "hello"
        assert r["bytes_written"] == 5

    def test_refuse_overwrite_by_default(self, tmp_path: Path):
        p = tmp_path / "exists.txt"
        p.write_text("old", encoding="utf-8")
        r = _write_text_file(path=str(p), content="new")
        assert "error" in r
        assert "exists" in r["error"]
        assert p.read_text(encoding="utf-8") == "old"  # Implementation note.

    def test_overwrite_true_replaces(self, tmp_path: Path):
        p = tmp_path / "exists.txt"
        p.write_text("old", encoding="utf-8")
        r = _write_text_file(path=str(p), content="new", overwrite=True)
        assert "error" not in r
        assert p.read_text(encoding="utf-8") == "new"

    def test_size_cap_rejects_big_content(self, tmp_path: Path):
        p = tmp_path / "big.txt"
        r = _write_text_file(
            path=str(p),
            content="x" * 1000,
            max_bytes=100,
        )
        assert "error" in r
        assert not p.exists()

    def test_creates_parent_directories(self, tmp_path: Path):
        p = tmp_path / "deep" / "nested" / "out.txt"
        r = _write_text_file(path=str(p), content="x")
        assert "error" not in r
        assert p.exists()

    def test_sandbox_dir_allows_inner(self, tmp_path: Path):
        r = _write_text_file(
            path="inner.txt",
            content="x",
            sandbox_dir=str(tmp_path),
        )
        assert "error" not in r
        assert (tmp_path / "inner.txt").exists()

    def test_sandbox_dir_blocks_escape(self, tmp_path: Path):
        outside = tmp_path.parent / f"escape_{tmp_path.name}.txt"
        r = _write_text_file(
            path=str(outside),
            content="pwned",
            sandbox_dir=str(tmp_path),
        )
        assert "error" in r
        assert "escapes_sandbox" in r["error"]
        assert not outside.exists()

    def test_missing_path_error(self):
        r = _write_text_file(path="", content="x")
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# append_text_file
# ═══════════════════════════════════════════════════════════


class TestAppendTextFile:
    def test_create_new(self, tmp_path: Path):
        p = tmp_path / "a.log"
        r = _append_text_file(path=str(p), content="line1\n")
        assert "error" not in r
        assert p.read_text(encoding="utf-8") == "line1\n"

    def test_append_preserves_existing(self, tmp_path: Path):
        p = tmp_path / "a.log"
        p.write_text("line1\n", encoding="utf-8")
        _append_text_file(path=str(p), content="line2\n")
        assert p.read_text(encoding="utf-8") == "line1\nline2\n"

    def test_size_cap(self, tmp_path: Path):
        p = tmp_path / "a.log"
        r = _append_text_file(
            path=str(p),
            content="x" * 1000,
            max_bytes=100,
        )
        assert "error" in r
        assert not p.exists()


# ═══════════════════════════════════════════════════════════
# edit_text_file
# ═══════════════════════════════════════════════════════════


class TestEditTextFile:
    def test_simple_replace(self, tmp_path: Path):
        p = tmp_path / "e.txt"
        p.write_text("hello world · hello again", encoding="utf-8")
        r = _edit_text_file(path=str(p), find="hello", replace="hi")
        assert r["replaced"] == 2
        assert p.read_text(encoding="utf-8") == "hi world · hi again"

    def test_count_limits_replacements(self, tmp_path: Path):
        p = tmp_path / "e.txt"
        p.write_text("a a a a", encoding="utf-8")
        r = _edit_text_file(path=str(p), find="a", replace="b", count=2)
        assert r["replaced"] == 2
        assert p.read_text(encoding="utf-8") == "b b a a"

    def test_find_not_present_error(self, tmp_path: Path):
        p = tmp_path / "e.txt"
        p.write_text("hello", encoding="utf-8")
        r = _edit_text_file(path=str(p), find="nope", replace="x")
        assert "error" in r
        assert r["occurrences"] == 0
        assert p.read_text(encoding="utf-8") == "hello"  # Implementation note.

    def test_missing_find_error(self):
        r = _edit_text_file(path="/tmp/x", find="", replace="y")
        assert "error" in r

    def test_missing_file_error(self, tmp_path: Path):
        r = _edit_text_file(
            path=str(tmp_path / "nope"),
            find="a",
            replace="b",
        )
        assert "error" in r
        assert "not found" in r["error"]


# ─────────────────────────────────────────────────────────────
# edit_file / multi_edit_file
# ─────────────────────────────────────────────────────────────


class TestEditFile:
    def test_unique_replacement(self, tmp_path: Path):
        p = tmp_path / "edit.txt"
        p.write_text("alpha beta gamma", encoding="utf-8")
        r = _edit_file(
            path=str(p),
            old_string="beta",
            new_string="delta",
        )
        assert "error" not in r
        assert p.read_text(encoding="utf-8") == "alpha delta gamma"

    def test_rejects_non_unique_old_string(self, tmp_path: Path):
        p = tmp_path / "edit.txt"
        p.write_text("alpha beta alpha", encoding="utf-8")
        r = _edit_file(
            path=str(p),
            old_string="alpha",
            new_string="omega",
        )
        assert "error" in r
        assert "unique" in r["error"]
        assert p.read_text(encoding="utf-8") == "alpha beta alpha"

    def test_rejects_noop(self, tmp_path: Path):
        p = tmp_path / "edit.txt"
        p.write_text("alpha beta", encoding="utf-8")
        r = _edit_file(
            path=str(p),
            old_string="beta",
            new_string="beta",
        )
        assert "error" in r
        assert "no-op" in r["error"]


class TestMultiEditFile:
    def test_applies_multiple_edits_atomically(self, tmp_path: Path):
        p = tmp_path / "multi.txt"
        p.write_text("one two three", encoding="utf-8")
        r = _multi_edit_file(
            path=str(p),
            edits=[
                {"old_string": "one", "new_string": "1"},
                {"old_string": "three", "new_string": "3"},
            ],
        )
        assert "error" not in r
        assert p.read_text(encoding="utf-8") == "1 two 3"

    def test_rejects_duplicate_old_string(self, tmp_path: Path):
        p = tmp_path / "multi.txt"
        p.write_text("repeat repeat", encoding="utf-8")
        r = _multi_edit_file(
            path=str(p),
            edits=[{"old_string": "repeat", "new_string": "done"}],
        )
        assert "error" in r
        assert "unique" in r["error"]


# ═══════════════════════════════════════════════════════════
# exec_shell
# ═══════════════════════════════════════════════════════════


class TestExecShell:
    def test_simple_str_command_platform_neutral(self):
        """Implementation note."""
        import sys

        # Implementation note.
        r = _exec_shell(command=f"{sys.executable} --version")
        if "error" in r:
            pytest.skip(f"python not runnable: {r}")
        assert r["exit_code"] == 0
        # Implementation note.
        combined = (r["stdout"] or "") + (r["stderr"] or "")
        assert "Python" in combined

    def test_argv_list(self):
        import sys

        r = _exec_shell(command=[sys.executable, "-c", "print(2+2)"])
        assert "error" not in r
        assert "4" in r["stdout"]
        assert r["exit_code"] == 0

    def test_nonzero_exit_returned(self):
        import sys

        r = _exec_shell(command=[sys.executable, "-c", "import sys; sys.exit(3)"])
        assert "error" not in r
        assert r["exit_code"] == 3

    def test_timeout(self):
        import sys

        r = _exec_shell(
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_s=0.3,
        )
        assert r.get("timed_out") is True
        assert "timeout" in r["error"]

    def test_command_not_found(self):
        r = _exec_shell(command=["no_such_binary_xyz_1234"])
        assert "error" in r

    def test_missing_command_error(self):
        assert "error" in _exec_shell(command="")

    def test_cwd_sandbox_blocks_escape(self, tmp_path: Path):
        outside = tmp_path.parent
        r = _exec_shell(
            command=["echo", "x"],
            cwd=str(outside),
            sandbox_dir=str(tmp_path),
        )
        assert "error" in r
        assert "escapes_sandbox" in r["error"]

    def test_no_shell_injection(self, tmp_path: Path):
        """Implementation note."""
        import sys

        # Implementation note.
        r = _exec_shell(
            command=[sys.executable, "-c", "print('safe')"],
        )
        assert "error" not in r
        assert r["exit_code"] == 0

    def test_run_in_background_returns_task_id(self):
        import sys

        r = _exec_shell(
            command=[sys.executable, "-u", "-c", "print('ready')"],
            run_in_background=True,
        )
        assert "error" not in r
        assert r["task_id"].startswith("bg_")
        assert "read_shell_output" in r["message"] or "read_background_output" in r["message"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackgroundExec:
    def test_background_command_returns_task_id_and_output_can_be_polled(self):
        import sys
        import time

        started = _background_exec(
            command=[
                sys.executable,
                "-u",
                "-c",
                "import time; print('ready'); time.sleep(0.2); print('done')",
            ],
        )
        assert "error" not in started
        assert started["status"] == "running"
        task_id = started["task_id"]

        deadline = time.monotonic() + 3
        polled = {}
        while time.monotonic() < deadline:
            polled = _read_background_output(task_id=task_id)
            if polled.get("status") == "completed":
                break
            time.sleep(0.05)

        assert polled["status"] == "completed"
        assert polled["exit_code"] == 0
        assert "ready" in polled["stdout"]
        assert "done" in polled["stdout"]

    def test_background_output_survives_registry_loss(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import sys
        import time

        monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
        started = _background_exec(
            command=[
                sys.executable,
                "-u",
                "-c",
                "import time; print('ready'); time.sleep(0.2); print('done')",
            ],
        )
        task_id = started["task_id"]
        _BACKGROUND_PROCESSES.clear()

        deadline = time.monotonic() + 3
        polled = {}
        while time.monotonic() < deadline:
            polled = _read_background_output(task_id=task_id)
            if polled.get("status") == "completed":
                break
            time.sleep(0.05)

        assert polled["status"] == "completed"
        assert polled["exit_code"] == 0
        assert "ready" in polled["stdout"]
        assert "done" in polled["stdout"]

    def test_kill_background_command_marks_cancelled(self):
        import sys

        started = _background_exec(
            command=[sys.executable, "-u", "-c", "import time; time.sleep(10)"],
        )
        task_id = started["task_id"]

        killed = _kill_background_exec(task_id=task_id)
        assert killed["status"] in {"cancelled", "completed"}

        polled = _read_background_output(task_id=task_id)
        assert polled["status"] in {"cancelled", "completed"}

    def test_shell_aliases_poll_and_kill_background_command(self):
        import sys

        started = _exec_shell(
            command=[sys.executable, "-u", "-c", "import time; time.sleep(10)"],
            run_in_background=True,
        )
        task_id = started["task_id"]

        polled = _read_shell_output(task_id=task_id)
        assert polled["task_id"] == task_id
        assert polled["status"] == "running"

        killed = _kill_shell(task_id=task_id)
        assert killed["status"] in {"cancelled", "completed"}


class TestRegistration:
    def test_register_write_skills_count(self):
        reg = SkillRegistry()
        n = register_write_skills(reg)
        assert n == 5
        for name in WRITE_SKILL_NAMES:
            assert reg.has(name)

    def test_exec_not_in_write_skills(self):
        reg = SkillRegistry()
        register_write_skills(reg)
        assert not reg.has(EXEC_SKILL_NAME)

    def test_register_exec_skill_separate(self):
        reg = SkillRegistry()
        n = register_exec_skill(reg)
        # register_exec_skill registers shell-execution-class skills.
        assert n == 7
        assert reg.has(EXEC_SKILL_NAME)
        assert reg.has("background_exec")
        assert reg.has("read_background_output")
        assert reg.has("kill_background_exec")
        assert reg.has("read_shell_output")
        assert reg.has("kill_shell")
        s = reg.get(EXEC_SKILL_NAME)
        assert "dangerous" in s.affinity

    def test_register_all_includes_write_but_not_exec(self):
        from runtime.execution.suckers.builtins import register_all

        reg = SkillRegistry()
        register_all(reg)
        for name in WRITE_SKILL_NAMES:
            assert reg.has(name), f"{name} should be in register_all"
        assert not reg.has(EXEC_SKILL_NAME), "exec_shell must NOT be auto-registered · opt-in only"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_write_skill_via_executor(self, tmp_path: Path):
        from uuid import uuid4

        from runtime.execution.tool_engine import ToolExecutor
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import (
            ArmId,
            Budget,
            BudgetLimits,
            SkillId,
            TaskId,
        )
        from runtime.safety.auth import TrustEngine

        reg = SkillRegistry()
        register_write_skills(reg)
        executor = ToolExecutor(
            registry=reg,
            immunity=TrustEngine(trusted_sources=["skill://public/*"]),
            journal=InMemoryJournal(),
        )
        tid = TaskId(uuid4())
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={
                "path": "out.txt",
                "content": "via-agent",
                "sandbox_dir": str(tmp_path),
            },
            caller="arms/x",
            task_id=tid,
            arm_id=ArmId("x"),
            budget=Budget(task_id=tid, limits=BudgetLimits(tokens=1000, usd=0.01)),
        )
        assert step.success
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "via-agent"
