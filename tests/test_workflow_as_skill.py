"""Implementation note."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# The router these tests target (runtime.sensing.gateway.workflow_editor_router)
# has not been implemented yet. Skip the whole file until it lands.
pytest.importorskip("runtime.sensing.gateway.workflow_editor_router")


@pytest.fixture
def wf_dir(tmp_path: Path, monkeypatch) -> Path:
    """Implementation note."""
    d = tmp_path / "workflows"
    d.mkdir()
    monkeypatch.setenv("ECHO_WORKFLOWS_DIR", str(d))
    return d


class _StubRegistry:
    """Implementation note."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def register(self, skill: Any, verify_tests: bool = False) -> None:
        self.registered[skill.name] = skill

    def unregister(self, name: str) -> bool:
        return self.registered.pop(name, None) is not None

    def has(self, name: str) -> bool:
        return name in self.registered


class _StubStack:
    def __init__(self) -> None:
        self.registry = _StubRegistry()
        self.runtime = None  # Implementation note.


def _minimal_workflow(name: str = "demo") -> dict:
    """Implementation note."""
    return {
        "name": name,
        "description": "list current working dir",
        "definition": {
            "nodes": [
                {"id": "start-1", "type": "start", "data": {}},
                {"id": "tool-1", "type": "tool", "data": {"path": "."}},
                {"id": "end-1", "type": "end", "data": {}},
            ],
            "edges": [
                {"source": "start-1", "target": "tool-1"},
                {"source": "tool-1", "target": "end-1"},
            ],
        },
    }


# Implementation note.


def test_create_workflow_registers_skill(wf_dir: Path) -> None:
    from runtime.sensing.gateway.workflow_editor_router import (
        _register_workflow_skill,
        _save_workflow,
        _workflow_skill_name,
    )

    stack = _StubStack()
    data = {"id": "abc12345ef", **_minimal_workflow()}
    _save_workflow(data)
    _register_workflow_skill(data, stack)

    skill_name = _workflow_skill_name("abc12345ef")
    assert skill_name == "workflow_abc12345"
    assert stack.registry.has(skill_name), "workflow 未注册进 registry"
    skill = stack.registry.registered[skill_name]
    # Implementation note.
    assert "demo" in skill.description
    assert "list current working dir" in skill.description
    # Implementation note.
    assert "workflow" in skill.affinity


# Implementation note.


def test_update_workflow_overwrites_skill(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _register_workflow_skill,
        _save_workflow,
        _workflow_skill_name,
    )

    stack = _StubStack()
    data_v1 = {"id": "abc12345ef", **_minimal_workflow("v1-name")}
    data_v1["description"] = "version one"
    _save_workflow(data_v1)
    _register_workflow_skill(data_v1, stack)

    data_v2 = dict(data_v1)
    data_v2["description"] = "version TWO"
    _save_workflow(data_v2)
    _register_workflow_skill(data_v2, stack)

    skill = stack.registry.registered[_workflow_skill_name("abc12345ef")]
    assert "version TWO" in skill.description
    assert "version one" not in skill.description


# Implementation note.


def test_delete_workflow_unregisters_skill(wf_dir: Path) -> None:
    from runtime.sensing.gateway.workflow_editor_router import (
        _register_workflow_skill,
        _unregister_workflow_skill,
        _workflow_skill_name,
    )

    stack = _StubStack()
    data = {"id": "abc12345ef", **_minimal_workflow()}
    _register_workflow_skill(data, stack)
    assert stack.registry.has(_workflow_skill_name("abc12345ef"))

    _unregister_workflow_skill("abc12345ef", stack)
    assert not stack.registry.has(_workflow_skill_name("abc12345ef"))


def test_unregister_missing_workflow_is_noop(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _unregister_workflow_skill,
    )

    stack = _StubStack()
    _unregister_workflow_skill("nonexistent_id", stack)  # Implementation note.


# Implementation note.


def test_bootstrap_registers_all_disk_workflows(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _save_workflow,
        _workflow_skill_name,
        bootstrap_existing_workflows,
    )

    for i in range(3):
        data = {"id": f"wf_{i}_abcd1234", **_minimal_workflow(f"name-{i}")}
        _save_workflow(data)

    stack = _StubStack()
    n = bootstrap_existing_workflows(stack)
    assert n == 3, f"expected 3 bootstrapped · got {n}"
    for i in range(3):
        assert stack.registry.has(_workflow_skill_name(f"wf_{i}_abcd1234"))


def test_bootstrap_skips_corrupt_json(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _save_workflow,
        _workflow_skill_name,
        bootstrap_existing_workflows,
    )

    # Implementation note.
    good = {"id": "good_id_123456", **_minimal_workflow("good")}
    _save_workflow(good)
    # Implementation note.
    (wf_dir / "bad.json").write_text("{ truncated", encoding="utf-8")
    # Implementation note.
    (wf_dir / "noid.json").write_text(json.dumps({"name": "noid"}), encoding="utf-8")

    stack = _StubStack()
    n = bootstrap_existing_workflows(stack)
    assert n == 1, f"只有 good 应该成功 · 实际 {n}"
    assert stack.registry.has(_workflow_skill_name("good_id_123456"))


# Implementation note.


def test_skill_name_is_deterministic_and_safe(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import _workflow_skill_name

    assert _workflow_skill_name("abcdef1234567890") == "workflow_abcdef12"
    # Implementation note.
    assert _workflow_skill_name("abcdef1234") == "workflow_abcdef12"


# Implementation note.


def test_visual_to_task_graph_skips_start_end_nodes(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _visual_to_task_graph_obj,
    )

    wf = _minimal_workflow()
    graph = _visual_to_task_graph_obj(wf["definition"]["nodes"], wf["definition"]["edges"])
    # Implementation note.
    assert len(graph.nodes) == 1
    assert graph.nodes[0].node_id == "tool-1"
    assert graph.nodes[0].skill_ref is not None
    # Implementation note.
    assert len(graph.edges) == 0


def test_visual_to_task_graph_no_executable_raises(wf_dir: Path) -> None:
    """Implementation note."""
    from runtime.sensing.gateway.workflow_editor_router import (
        _visual_to_task_graph_obj,
    )

    nodes = [
        {"id": "s", "type": "start", "data": {}},
        {"id": "e", "type": "end", "data": {}},
    ]
    with pytest.raises(ValueError, match="no executable nodes"):
        _visual_to_task_graph_obj(nodes, [])


# Implementation note.


def test_skill_registry_unregister_removes_skill() -> None:
    """Implementation note."""
    from runtime.execution.suckers.registry import Skill, SkillRegistry

    reg = SkillRegistry()
    reg.register(
        Skill(
            name="demo_skill",
            description="",
            trusted_source="builtin://test",
            handler=lambda: None,
        ),
        verify_tests=False,
    )
    assert reg.has("demo_skill")
    assert reg.unregister("demo_skill") is True
    assert not reg.has("demo_skill")
    # Implementation note.
    assert reg.unregister("demo_skill") is False
    assert reg.unregister("never_existed") is False
