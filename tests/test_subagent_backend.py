"""Subagent multi-provider backend tests — dsh provider vocabulary port."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.execution.subagents import bridge
from runtime.execution.subagents.registry import (
    SubagentDefinition,
    SubagentRegistry,
    load_subagent_file,
)


def _definition(**overrides: object) -> SubagentDefinition:
    base: dict[str, object] = {
        "name": "code_reviewer",
        "description": "reviews code",
        "system_prompt": "You review code.",
    }
    base.update(overrides)
    return SubagentDefinition(**base)  # type: ignore[arg-type]


def test_frontmatter_backend_parsed(tmp_path: Path) -> None:
    path = tmp_path / "reviewer.md"
    path.write_text(
        "---\nname: reviewer\ndescription: reviews code\nbackend: codex-cli\n---\nBody",
        encoding="utf-8",
    )
    definition = load_subagent_file(path, scope="project")
    assert definition.backend == "codex-cli"
    assert definition.to_wire()["backend"] == "codex-cli"


def test_frontmatter_backend_absent() -> None:
    definition = _definition()
    assert definition.backend is None
    assert definition.to_wire()["backend"] is None


@pytest.mark.parametrize(
    "backend",
    [
        "claude-code",
        "codex-cli",
        "trae-cli",
        "codebuddy-cli",
        "kimi-cli",
        "opencode-cli",
        "local_claude_code",
    ],
)
def test_legacy_cli_backends_are_not_implicitly_dispatched(backend: str) -> None:
    """CLI presence alone must not create a hidden subagent provider."""

    result = bridge._dispatch_partner(_definition(backend=backend), "do it", 60)

    assert result is not None
    assert result["success"] is False
    assert result["backend"] == backend
    assert result["failure_kind"] == "legacy_cli_backend_removed"
    assert "model-provider plugin" in result["error"]


def test_unknown_backend_returns_a_structured_failure() -> None:
    result = bridge._dispatch_partner(_definition(backend="nope-cli"), "do it", 60)
    assert result is not None
    assert result["success"] is False
    assert result["failure_kind"] == "legacy_cli_backend_removed"


def test_registry_backend_failure_does_not_silently_change_engines() -> None:
    previous = bridge.get_subagent_registry()
    try:
        bridge.set_subagent_registry(SubagentRegistry([_definition(backend="codex-cli")]))
        result = bridge.call_subagent(agent_id="code_reviewer", prompt="check it")
    finally:
        bridge.set_subagent_registry(previous)

    assert result["success"] is False
    assert result["backend"] == "codex-cli"
    assert result["failure_kind"] == "legacy_cli_backend_removed"

