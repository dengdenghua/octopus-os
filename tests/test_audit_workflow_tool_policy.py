from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.execution.misc.skill_policy import (
    audit_read_only_tool_denial,
    filter_audit_read_only_tool_specs,
    is_audit_read_only_context,
)
from runtime.execution.subagents.bridge import _inherit_parent_work_context
from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.execution.tool_engine.executor import ToolExecutor
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway.tool_bridge import _execute_tool_call
from runtime.sensing.model_router.models import ToolCall


@pytest.mark.parametrize(
    "context",
    [
        {"workflow_preset": "audit.review"},
        {"metadata": {"workflow_preset": "audit.deep"}},
        {"workflow_preset": "audit.ultracode"},
    ],
)
def test_audit_workflow_is_resolved_from_flat_or_nested_context(
    context: dict[str, object],
) -> None:
    assert is_audit_read_only_context(context) is True


def test_audit_catalog_keeps_read_search_verification_and_orchestration() -> None:
    specs = [
        SimpleNamespace(name=name)
        for name in (
            "read_file",
            "grep_text",
            "web_search",
            "run_tests",
            "lint_check",
            "run_orchestration",
            "edit_file",
            "exec_shell",
            "format_code",
        )
    ]

    filtered = filter_audit_read_only_tool_specs(
        specs,
        context={"workflow_preset": "audit.deep"},
    )

    assert {spec.name for spec in filtered} == {
        "read_file",
        "grep_text",
        "web_search",
        "run_tests",
        "lint_check",
        "run_orchestration",
    }


def test_audit_verification_rejects_fix_and_arbitrary_commands() -> None:
    context = {"workflow_preset": "audit.review"}

    assert audit_read_only_tool_denial("run_tests", {}, context=context) is None
    assert (
        audit_read_only_tool_denial(
            "run_tests",
            {"command": "python -m pytest tests/test_api.py"},
            context=context,
        )
        is None
    )
    assert (
        audit_read_only_tool_denial(
            "lint_check",
            {"command": "ruff check .", "fix": False},
            context=context,
        )
        is None
    )
    assert "fix=true is blocked" in str(
        audit_read_only_tool_denial(
            "lint_check",
            {"fix": True},
            context=context,
        )
    )
    assert "arbitrary commands are blocked" in str(
        audit_read_only_tool_denial(
            "run_tests",
            {"command": 'python -c \'open("x", "w").write("bad")\''},
            context=context,
        )
    )


def test_executor_hard_blocks_audit_writer_but_develop_executes_it() -> None:
    calls: list[str] = []
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="custom_writer",
            description="Mutate project state.",
            affinity=["write"],
            trusted_source="skill://test/custom_writer",
            handler=lambda: calls.append("write") or {"ok": True},
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace(executor=ToolExecutor(registry, TrustEngine()))
    call = ToolCall(id="write-1", name="custom_writer", input={})

    with session_scope(
        Session(
            thread_id="audit-thread",
            metadata={"workflow_preset": "audit.review"},
        )
    ):
        output, is_error = _execute_tool_call(stack, call)

    assert is_error is True
    assert "audit-read-only" in output
    assert "Switch the task to develop" in output
    assert calls == []

    with session_scope(
        Session(
            thread_id="develop-thread",
            metadata={"workflow_preset": "develop.iterate"},
        )
    ):
        output, is_error = _execute_tool_call(stack, call)

    assert is_error is False
    assert '"ok": true' in output.lower()
    assert calls == ["write"]


def test_executor_allows_focused_verification_during_audit() -> None:
    calls: list[str] = []
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="run_tests",
            description="Run focused tests.",
            affinity=["test", "quality"],
            trusted_source="skill://test/run_tests",
            handler=lambda **_kwargs: calls.append("test") or {"success": True},
        ),
        verify_tests=False,
    )
    stack = SimpleNamespace(executor=ToolExecutor(registry, TrustEngine()))

    with session_scope(
        Session(
            thread_id="audit-thread",
            metadata={"workflow_preset": "audit.deep"},
        )
    ):
        output, is_error = _execute_tool_call(
            stack,
            ToolCall(
                id="test-1",
                name="run_tests",
                input={"command": "python -m pytest tests/test_api.py"},
            ),
        )

    assert is_error is False
    assert '"success": true' in output.lower()
    assert calls == ["test"]


def test_subagent_inherits_authoritative_parent_work_policy() -> None:
    parent = Session(
        thread_id="parent",
        metadata={
            "workflow_preset": "audit.deep",
            "personal_mode": "research",
            "verification_policy": "strict",
        },
    )

    inherited = _inherit_parent_work_context(None, parent)
    assert inherited["workflow_preset"] == "audit.deep"
    assert inherited["personal_mode"] == "research"
    assert inherited["verification_policy"] == "strict"
    assert inherited["tool_allowlist_read_only"] is True

    attempted_widening = _inherit_parent_work_context(
        {"workflow_preset": "develop.iterate", "personal_mode": "build"},
        parent,
    )
    assert attempted_widening["workflow_preset"] == "audit.deep"
    assert attempted_widening["personal_mode"] == "research"
    assert attempted_widening["tool_allowlist_read_only"] is True

    develop_parent = Session(
        thread_id="next-turn",
        metadata={"workflow_preset": "develop.iterate", "personal_mode": "build"},
    )
    develop = _inherit_parent_work_context(None, develop_parent)
    assert develop["workflow_preset"] == "develop.iterate"
    assert develop["personal_mode"] == "build"
    assert "tool_allowlist_read_only" not in develop

