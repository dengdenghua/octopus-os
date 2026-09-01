"""Tests for the read-before-write enforcement layer in ToolExecutor.

Spec source: prior deep-analysis identified this as a P0 test blind
spot — grep ``_READ_TRACKING_KEY|read_before_write_required`` in
``tests/`` returned zero matches, meaning the enforcement could be
silently removed or bypassed without CI catching it.

The guard sits at executor.py:945-950 and refuses writes to *existing*
files the agent has not first read in the current turn. The goal is to
force the model to inspect current contents before mutating them — a
real safety property for code editing. Coverage pinned here:

  * writing an existing file without a prior read → refused
  * writing after read_file in the same turn → allowed
  * writing after exec_shell cat in the same turn → allowed
  * writing a NEW file (target.exists() is False) → allowed (no read needed)
  * tools outside the _READ_BEFORE_WRITE_TOOLS set → not checked
  * the read tracking key is turn-scoped (no cross-turn leakage)
  * a failed read (e.g. read_file_range on a missing file) does NOT grant
    write capability
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.builtins import _read_file
from runtime.execution.suckers.write_skills import _write_text_file
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine

# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def immunity() -> TrustEngine:
    return TrustEngine(trusted_sources=["skill://public/*"])


@pytest.fixture
def journal() -> InMemoryJournal:
    return InMemoryJournal()


@pytest.fixture
def budget() -> Budget:
    return Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=10_000, usd=1.0))


def _make_executor(immunity: TrustEngine, journal: InMemoryJournal) -> ToolExecutor:
    """An executor with read_file + write_text_file + exec_shell registered."""
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="read_file",
            description="read a file",
            affinity=["file", "read"],
            trusted_source="skill://public/read_file",
            handler=_read_file,
        )
    )
    registry.register(
        Skill(
            name="write_text_file",
            description="write a file",
            affinity=["file", "write"],
            trusted_source="skill://public/write_text_file",
            handler=_write_text_file,
        )
    )
    return ToolExecutor(registry=registry, immunity=immunity, journal=journal)


# ── Tests: existing file without prior read is refused ────────────


def test_write_existing_file_without_prior_read_is_refused(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """Writing an existing file the agent has not read this turn must fail."""
    target = tmp_path / "config.yaml"
    target.write_text("original: value\n", encoding="utf-8")

    executor = _make_executor(immunity, journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": "config.yaml", "content": "new: value\n", "overwrite": True},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert not step.success
    assert step.result.status == "failed"
    assert step.result.error_type == "read_before_write_required"
    # The original content is preserved
    assert target.read_text(encoding="utf-8") == "original: value\n"


# ── Tests: read in the same turn unlocks the write ────────────────


def test_write_after_read_file_in_same_turn_is_allowed(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """read_file in the same turn records the path and unlocks the write."""
    target = tmp_path / "config.yaml"
    target.write_text("original: value\n", encoding="utf-8")

    executor = _make_executor(immunity, journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )

    with session_scope(session):
        read_step = executor.execute_step(
            step_id=0,
            node_id="read",
            sucker_id=SkillId("read_file"),
            args={"path": "config.yaml"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        write_step = executor.execute_step(
            step_id=1,
            node_id="write",
            sucker_id=SkillId("write_text_file"),
            args={"path": "config.yaml", "content": "new: value\n", "overwrite": True},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert read_step.success
    assert write_step.success
    # Content was actually overwritten
    assert target.read_text(encoding="utf-8") == "new: value\n"


# ── Tests: writing a NEW file is allowed without a prior read ─────


def test_write_new_file_without_prior_read_is_allowed(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """A non-existent target has nothing to inspect first — write proceeds.

    ``_read_before_write_violation`` returns None when
    ``not target.exists()``, so creating a new file is never blocked.
    """
    target = tmp_path / "new_file.txt"
    assert not target.exists()

    executor = _make_executor(immunity, journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["new_file.txt"],
        }
    )

    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("write_text_file"),
            args={"path": "new_file.txt", "content": "fresh content\n"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert step.success
    assert target.read_text(encoding="utf-8") == "fresh content\n"


# ── Tests: exec_shell cat grants read tracking ────────────────────


def test_exec_shell_cat_grants_read_tracking(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """A successful ``cat file`` (argv form) grants read tracking for that file.

    The guard is about ensuring the model has inspected the current
    contents, not about forcing one particular UI tool. Native models
    often use ``cat``; the executor recognises only this narrow,
    successful, read-only form.
    """
    target = tmp_path / "config.yaml"
    target.write_text("original: value\n", encoding="utf-8")

    registry = SkillRegistry()

    # exec_shell handler that returns a successful cat argv — the form
    # _record_successful_read recognises.
    def _exec_shell_handler(command: str = "", **_kw):
        # The handler is expected to return {"argv": [...], "exit_code": 0}
        # for the read-tracking path to fire. We split the command on
        # whitespace to simulate a real shell parser for the simple case.
        argv = command.split() if command else []
        return {"argv": argv, "exit_code": 0}

    registry.register(
        Skill(
            name="exec_shell",
            description="run a shell command",
            affinity=["shell", "exec", "read"],
            trusted_source="skill://public/exec_shell",
            handler=_exec_shell_handler,
        )
    )
    registry.register(
        Skill(
            name="write_text_file",
            description="write a file",
            affinity=["file", "write"],
            trusted_source="skill://public/write_text_file",
            handler=_write_text_file,
        )
    )
    executor = ToolExecutor(registry=registry, immunity=immunity, journal=journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )

    with session_scope(session):
        cat_step = executor.execute_step(
            step_id=0,
            node_id="cat",
            sucker_id=SkillId("exec_shell"),
            args={"command": "cat config.yaml", "cwd": str(tmp_path)},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        write_step = executor.execute_step(
            step_id=1,
            node_id="write",
            sucker_id=SkillId("write_text_file"),
            args={"path": "config.yaml", "content": "updated\n", "overwrite": True},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert cat_step.success
    assert write_step.success, "cat should have granted read tracking"
    assert target.read_text(encoding="utf-8") == "updated\n"


# ── Tests: a failed read does NOT grant write capability ──────────


def test_failed_read_does_not_grant_write_capability(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """A read that errors (e.g. file not found) must not record the path.

    ``_record_successful_read`` checks ``output.get("error")`` and
    returns early; the write that follows must still be refused because
    the path was never recorded.
    """
    existing = tmp_path / "config.yaml"
    existing.write_text("keep me\n", encoding="utf-8")

    executor = _make_executor(immunity, journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )

    with session_scope(session):
        # Attempt to read a *different*, non-existent file
        read_step = executor.execute_step(
            step_id=0,
            node_id="read-missing",
            sucker_id=SkillId("read_file"),
            args={"path": "does_not_exist.txt"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        # The read itself returns an error in output (read_file on a
        # missing file sets status=success with an error dict — the
        # step is "executed" but the read failed). The key property is
        # that the missing file's path is NOT recorded in read tracking.
        assert isinstance(read_step.result.output, dict)
        assert read_step.result.output.get("error"), (
            "read_file on a missing file should report an error in output"
        )
        # Now attempt to write the existing file — must still be refused
        # because the failed read did not grant tracking for config.yaml.
        write_step = executor.execute_step(
            step_id=1,
            node_id="write",
            sucker_id=SkillId("write_text_file"),
            args={"path": "config.yaml", "content": "should be refused\n", "overwrite": True},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert not write_step.success
    assert write_step.result.error_type == "read_before_write_required"
    assert existing.read_text(encoding="utf-8") == "keep me\n"


# ── Tests: read tracking is turn-scoped ───────────────────────────


def test_read_tracking_does_not_leak_across_sessions(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """A read in session A must NOT unlock a write in session B.

    Each Session has its own metadata dict, so ``_READ_TRACKING_KEY``
    starts empty per session. This is the turn-scoping contract: the
    model must re-read in each new turn before writing.
    """
    target = tmp_path / "config.yaml"
    target.write_text("original\n", encoding="utf-8")

    executor = _make_executor(immunity, journal)

    # Session A: read the file
    session_a = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )
    with session_scope(session_a):
        read_step = executor.execute_step(
            step_id=0,
            node_id="read",
            sucker_id=SkillId("read_file"),
            args={"path": "config.yaml"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
    assert read_step.success

    # Session B: write without a prior read in THIS session
    session_b = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["config.yaml"],
        }
    )
    with session_scope(session_b):
        write_step = executor.execute_step(
            step_id=0,
            node_id="write",
            sucker_id=SkillId("write_text_file"),
            args={"path": "config.yaml", "content": "leaked\n", "overwrite": True},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert not write_step.success
    assert write_step.result.error_type == "read_before_write_required"
    assert target.read_text(encoding="utf-8") == "original\n"


# ── Tests: tools outside the guard set are not checked ────────────


def test_non_guarded_tool_is_not_checked(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
) -> None:
    """A tool not in _READ_BEFORE_WRITE_TOOLS skips the read guard entirely."""
    # Register a custom tool that writes but is NOT in the guard set
    registry = SkillRegistry()

    def _custom_write_handler(path: str = "", content: str = "", **_kw):
        Path(path).write_text(content, encoding="utf-8")
        return {"bytes_written": len(content)}

    registry.register(
        Skill(
            name="custom_writer",
            description="a writer not in the guard set",
            affinity=["file", "write"],
            trusted_source="skill://public/custom_writer",
            handler=_custom_write_handler,
        )
    )
    executor = ToolExecutor(registry=registry, immunity=immunity, journal=journal)

    target = tmp_path / "existing.txt"
    target.write_text("original\n", encoding="utf-8")

    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": ["existing.txt"],
        }
    )
    with session_scope(session):
        step = executor.execute_step(
            step_id=0,
            node_id="n0",
            sucker_id=SkillId("custom_writer"),
            args={"path": str(target), "content": "overwritten\n"},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    # No read_before_write_required — the guard only fires for the 4 named tools
    assert step.success
    assert step.result.error_type != "read_before_write_required"
    assert target.read_text(encoding="utf-8") == "overwritten\n"


@pytest.mark.parametrize(
    ("read_tool", "write_tool", "suffix"),
    [
        ("documents.extract_text", "documents.replace_text", ".docx"),
        ("spreadsheets.read_sheet", "spreadsheets.update_cells", ".xlsx"),
        ("presentations.extract_text", "presentations.replace_text", ".pptx"),
    ],
)
def test_office_edits_require_a_same_turn_native_read(
    tmp_path: Path,
    immunity: TrustEngine,
    journal: InMemoryJournal,
    budget: Budget,
    read_tool: str,
    write_tool: str,
    suffix: str,
) -> None:
    """Native Office readers unlock only their matching file for editing."""
    target = tmp_path / f"artifact{suffix}"
    target.write_bytes(b"original")

    registry = SkillRegistry()

    def _read_handler(path: str = "", **_kw):
        return {"path": path, "content": "current contents"}

    def _write_handler(path: str = "", **_kw):
        Path(path).write_bytes(b"updated")
        return {"path": path, "updated": True}

    registry.register(
        Skill(
            name=read_tool,
            description="read an Office artifact",
            affinity=["file", "read"],
            trusted_source=f"skill://public/{read_tool}",
            handler=_read_handler,
        )
    )
    registry.register(
        Skill(
            name=write_tool,
            description="edit an Office artifact",
            affinity=["file", "write", "edit"],
            trusted_source=f"skill://public/{write_tool}",
            handler=_write_handler,
        )
    )
    executor = ToolExecutor(registry=registry, immunity=immunity, journal=journal)
    session = Session(
        metadata={
            "mode": "code",
            "workspace_path": str(tmp_path),
            "allowed_write_paths": [target.name],
        }
    )

    with session_scope(session):
        blocked = executor.execute_step(
            step_id=0,
            node_id="blocked-write",
            sucker_id=SkillId(write_tool),
            args={"path": str(target)},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        read_step = executor.execute_step(
            step_id=1,
            node_id="native-read",
            sucker_id=SkillId(read_tool),
            args={"path": str(target)},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )
        allowed = executor.execute_step(
            step_id=2,
            node_id="allowed-write",
            sucker_id=SkillId(write_tool),
            args={"path": str(target)},
            caller="arms/code_arm",
            task_id=budget.task_id,
            arm_id=ArmId("code_arm"),
            budget=budget,
        )

    assert blocked.result.error_type == "read_before_write_required"
    assert read_step.success
    assert allowed.success
    assert target.read_bytes() == b"updated"

