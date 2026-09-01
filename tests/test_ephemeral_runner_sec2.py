"""SEC-2 regression: ephemeral sub-agent dispatch must apply the executor's gates.

``_execute_tool_in_subagent`` bypasses ``executor.execute_step``, so before this
fix it called ``skill.handler(**call.input)`` without the credential-file
denylist (``check_file_write``), the capability denylist, or the
``allow_sensitive`` / ``allow_private`` strip — an unsandboxed sub-agent could
write ``./.env`` / ``./id_rsa`` or run an operator-disabled tool. The path now
routes through ``gate_inner_dispatch`` + ``strip_model_controlled_overrides``.
"""

from __future__ import annotations

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.ephemeral_runner import _execute_tool_in_subagent
from runtime.platform.models.llm import ToolCall


def _registry_with(skill: Skill) -> SkillRegistry:
    r = SkillRegistry()
    r.register(skill)
    return r


def _call(name: str, tool_input: dict) -> ToolCall:
    # The production path passes a frozen ``llm.ToolCall`` whose ``input`` dict
    # may be mutated in place but whose attributes may NOT be rebound.
    return ToolCall(id="tu_test", name=name, input=tool_input)


def test_credential_write_blocked_on_ephemeral_path(tmp_path) -> None:
    called = {"hit": False}

    def _write(path, content="", sandbox_dir=None, **kw):
        called["hit"] = True
        return "wrote"

    reg = _registry_with(
        Skill(
            name="write_text_file",
            description="writes a file",
            affinity=["write"],
            trusted_source="skill://public/write_text_file",
            handler=_write,
        )
    )
    call = _call(
        "write_text_file",
        {
            "path": str(tmp_path / ".env"),
            "content": "SECRET=1",
            "sandbox_dir": str(tmp_path),
        },
    )

    out, is_error = _execute_tool_in_subagent(reg, call)

    assert is_error is True
    assert "blocked" in out.lower()
    assert "file_safety" in out
    assert called["hit"] is False  # handler must never run


def test_normal_write_still_succeeds(tmp_path) -> None:
    called = {"path": None}

    def _write(path, content="", sandbox_dir=None, **kw):
        called["path"] = path
        return "wrote"

    reg = _registry_with(
        Skill(
            name="write_text_file",
            description="writes a file",
            affinity=["write"],
            trusted_source="skill://public/write_text_file",
            handler=_write,
        )
    )
    target = str(tmp_path / "notes.txt")
    call = _call(
        "write_text_file",
        {"path": target, "content": "hello", "sandbox_dir": str(tmp_path)},
    )

    out, is_error = _execute_tool_in_subagent(reg, call)

    assert is_error is False
    assert called["path"] == target


def test_privilege_flags_stripped_on_ephemeral_path(tmp_path) -> None:
    captured: dict = {}

    def _reader(**kw):
        captured.update(kw)
        return "ok"

    reg = _registry_with(
        Skill(
            name="read_thing",
            description="reads something",
            affinity=["read"],
            trusted_source="skill://public/read_thing",
            handler=_reader,
        )
    )
    call = _call(
        "read_thing",
        {"q": "x", "allow_sensitive": True, "allow_private": True},
    )

    out, is_error = _execute_tool_in_subagent(reg, call)

    assert is_error is False
    assert "allow_sensitive" not in captured
    assert "allow_private" not in captured
    assert captured.get("q") == "x"

