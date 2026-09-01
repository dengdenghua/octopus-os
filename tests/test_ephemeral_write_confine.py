"""Worktree write-confinement gate for ephemeral sub-agents.

Ephemeral runs call ``skill.handler`` directly, bypassing the executor's
sandbox-arg injector — so a write skill would run with ``sandbox_dir=None``
(no confinement, verified live to escape). The gate replicates the injector:
when the Session pins ``_locked_write_root`` it injects it as ``sandbox_dir``
for write skills, and blocks (fail-closed) a write skill that can't take one.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.execution.suckers.ephemeral_runner import (
    _ephemeral_write_confine_block,
)
from runtime.platform.process.session import Session, _current_session


class _Skill:
    def __init__(self, handler: Any, affinity: list[str] | None = None) -> None:
        self.handler = handler
        self.affinity = affinity or []


class _Call:
    def __init__(self, name: str, inp: dict[str, Any]) -> None:
        self.name = name
        self.input = inp


def _writer(path: str = "", content: str = "", sandbox_dir: str | None = None, **_k: Any):
    return None


def _unconfinable(path: str = "", **_k: Any):  # no sandbox_dir parameter
    return None


@pytest.fixture
def _session():
    def _set(meta: dict[str, Any]):
        return _current_session.set(Session(metadata=meta))

    tokens: list[Any] = []

    def _enter(meta: dict[str, Any]):
        tokens.append(_set(meta))

    yield _enter
    for t in reversed(tokens):
        _current_session.reset(t)


def test_no_lock_no_injection(_session):
    _session({})
    call = _Call("write_text_file", {"path": "a.txt"})
    assert _ephemeral_write_confine_block(call, _Skill(_writer)) is None
    assert "sandbox_dir" not in call.input


def test_lock_injects_sandbox_dir_for_write_skill(_session):
    _session({"_locked_write_root": "/wt"})
    call = _Call("write_text_file", {"path": "a.txt"})
    assert _ephemeral_write_confine_block(call, _Skill(_writer)) is None
    assert call.input["sandbox_dir"] == "/wt"


def test_lock_blocks_unconfinable_write_skill(_session):
    _session({"_locked_write_root": "/wt"})
    call = _Call("write_blob", {"path": "a"})
    block = _ephemeral_write_confine_block(call, _Skill(_unconfinable))
    assert block is not None
    assert "confined" in block


def test_lock_ignores_non_write_skill(_session):
    _session({"_locked_write_root": "/wt"})
    call = _Call("read_file", {"path": "a"})
    assert _ephemeral_write_confine_block(call, _Skill(_unconfinable)) is None
    assert "sandbox_dir" not in call.input


def test_lock_does_not_treat_blackboard_or_todo_writes_as_files(_session):
    _session({"_locked_write_root": "/wt"})
    for name, affinity in (
        ("bb_write", ["blackboard", "shared_state"]),
        ("todo_write", ["meta", "plan", "ui"]),
    ):
        call = _Call(name, {"key": "k", "value": "v"})
        assert _ephemeral_write_confine_block(call, _Skill(_unconfinable, affinity)) is None
        assert "sandbox_dir" not in call.input


def test_lock_scopes_read_and_discovery_tools_to_worktree(_session):
    _session({"_locked_write_root": "/wt"})

    def _reader(
        path: str = "",
        *,
        cwd: str | None = None,
        sandbox_dir: str | None = None,
    ):
        return None

    read_call = _Call("read_file", {"path": "evidence.json"})
    assert _ephemeral_write_confine_block(read_call, _Skill(_reader, ["file", "io"])) is None
    assert read_call.input["cwd"] == "/wt"
    assert read_call.input["sandbox_dir"] == "/wt"

    def _glob(
        pattern: str = "*",
        root: str = ".",
        *,
        sandbox_dir: str | None = None,
    ):
        return None

    glob_call = _Call("glob_files", {"pattern": "*.json", "root": "."})
    assert _ephemeral_write_confine_block(glob_call, _Skill(_glob, ["file", "io"])) is None
    assert glob_call.input["root"] == "/wt"
    assert glob_call.input["sandbox_dir"] == "/wt"


def test_lock_does_not_override_preset_sandbox_dir(_session):
    _session({"_locked_write_root": "/wt"})
    call = _Call("write_text_file", {"path": "a", "sandbox_dir": "/preset"})
    _ephemeral_write_confine_block(call, _Skill(_writer))
    assert call.input["sandbox_dir"] == "/preset"


def test_write_detected_by_affinity(_session):
    _session({"_locked_write_root": "/wt"})
    call = _Call("apply_change", {"path": "a"})
    # name has no write/edit token, but affinity marks it as a write
    assert _ephemeral_write_confine_block(call, _Skill(_writer, ["write"])) is None
    assert call.input["sandbox_dir"] == "/wt"


def test_lock_blocks_shell_tool_by_affinity(_session):
    """Audit F-02: a cwd nudge is not a sandbox for a command interpreter.
    Inside an isolated spawn, shell/exec tools are refused outright."""
    _session({"_locked_write_root": "/wt"})

    def _shell(command: str = "", *, cwd: str | None = None):
        return None

    call = _Call("exec_shell", {"command": "echo escape"})
    block = _ephemeral_write_confine_block(call, _Skill(_shell, ["shell", "exec", "dangerous"]))
    assert block is not None
    assert "shell/exec" in block
    # No confinement args may have been injected before the refusal.
    assert "sandbox_dir" not in call.input


def test_lock_blocks_exec_affinity_tools_without_shell_name(_session):
    _session({"_locked_write_root": "/wt"})

    def _runner(code: str = ""):
        return None

    call = _Call("run_python", {"code": "print(1)"})
    block = _ephemeral_write_confine_block(
        call, _Skill(_runner, ["python", "analysis", "exec", "dangerous"])
    )
    assert block is not None
    assert "shell/exec" in block


def test_lock_blocks_shell_tool_by_name_without_affinity(_session):
    _session({"_locked_write_root": "/wt"})

    def _bg(command: str = ""):
        return None

    call = _Call("background_exec", {"command": "sleep 1"})
    block = _ephemeral_write_confine_block(call, _Skill(_bg))
    assert block is not None
    assert "shell/exec" in block


def test_shell_tool_allowed_without_locked_root(_session):
    _session({})

    def _shell(command: str = "", *, cwd: str | None = None):
        return None

    call = _Call("exec_shell", {"command": "ls"})
    assert _ephemeral_write_confine_block(call, _Skill(_shell, ["shell", "exec"])) is None

