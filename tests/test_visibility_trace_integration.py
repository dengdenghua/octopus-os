"""Visibility trace integration tests: capability routing → skill catalog →
ContextVar lifecycle (the path react_loop PHASE 3 → PHASE 4.7 relies on)."""

from __future__ import annotations

from types import SimpleNamespace

from runtime.core.cerebrum._react_context_helpers import _format_skill_catalog
from runtime.core.cerebrum._visibility_trace import (
    active_trace,
    new_trace,
    reset_active_trace,
    set_active_trace,
)
from runtime.core.cerebrum.capability_router import activate_capabilities


def _fake_registry(skills: dict[str, dict]) -> SimpleNamespace:
    """Minimal registry stub: all_names / get / is_enabled."""

    def _get(name: str) -> SimpleNamespace:
        spec = skills.get(name, {})
        return SimpleNamespace(
            summary=spec.get("summary", ""),
            description=spec.get("description", ""),
            affinity=spec.get("affinity", []),
        )

    return SimpleNamespace(
        all_names=lambda: list(skills),
        is_enabled=lambda name: True,
        get=_get,
        has=lambda name: name in skills,
    )


def test_activation_records_each_capability_with_basis() -> None:
    """activate_capabilities must record every activated label + why."""
    activation = activate_capabilities(
        "帮我把报告写完并保存成 markdown 文件",
        user_context={"mode": "code"},
        registry=_fake_registry({}),
    )
    trace = activation.trace
    assert trace is not None and not trace.empty()
    decisions = trace.export()
    decision_points = {d["decision_point"] for d in decisions}
    # Capability routing decisions exist and carry a basis.
    assert "capability_router.activate" in decision_points
    assert all(d.get("conclusion") and d.get("basis") for d in decisions)


def test_skill_catalog_records_delegation_and_truncation() -> None:
    """_format_skill_catalog must record delegation visibility + catalog size."""
    token = set_active_trace(new_trace())
    try:
        skills = {
            f"skill_{i:03d}": {"summary": f"工具 {i}", "description": "用于测试"}
            for i in range(120)
        }
        _format_skill_catalog(
            _fake_registry(skills),
            max_skills=100,
            user_context={"mode": "code"},
            goal="写代码",
        )
    finally:
        reset_active_trace(token)

    trace = active_trace()
    assert trace is None  # reset restored the ContextVar slot
    # The decisions were recorded into the turn-level trace we set.
    # Re-open a fresh context to read them back via the recorded object:
    recorded = new_trace()
    # Re-run with the explicit trace to read back content.
    token2 = set_active_trace(recorded)
    try:
        _format_skill_catalog(
            _fake_registry(skills),
            max_skills=100,
            user_context={"mode": "code"},
            goal="写代码",
        )
    finally:
        reset_active_trace(token2)
    points = {d["decision_point"] for d in recorded.export()}
    assert "context.delegation_cap" in points
    assert "context.skill_catalog" in points
    catalog = [d for d in recorded.export() if d["decision_point"] == "context.skill_catalog"]
    assert catalog
    last = catalog[-1]
    assert last["details"]["total"] == 120
    assert last["details"]["truncated"] >= 20


def test_contextvar_lifecycle_never_leaks_across_turns() -> None:
    """PHASE 3 set + PHASE 4.7 reset must leave the slot clean."""
    assert active_trace() is None
    token = set_active_trace(new_trace())
    try:
        assert active_trace() is not None
    finally:
        reset_active_trace(token)
    assert active_trace() is None


def test_react_loop_emits_visibility_event() -> None:
    """End-to-end: turn-level trace must reach PHASE 4.7 as an event."""
    from runtime.core.cerebrum.react_loop import stream_react_loop
    from tests.test_react_loop import (
        _build_stack_with_executor,
        _drain,
        _intent,
        _ScriptedRouter,
    )

    router = _ScriptedRouter(
        [
            "Final Answer: 你好,我在。",
            "Final Answer: 你好,我在。",
            "Final Answer: 你好,我在。",
        ]
    )
    stack = _build_stack_with_executor(router)
    intent = _intent("把报告写完")
    intent.user_context["mode"] = "code"

    events, result = _drain(stream_react_loop(stack, intent, agent=None, max_iterations=3))
    assert result is not None and result.success

    visibility = [e for e in events if e.get("type") == "visibility"]
    assert visibility, "react_loop must emit a visibility snapshot event"
    snap = visibility[0]
    assert snap.get("steps") is not None
    # The turn-level trace captured at least capability routing / catalog
    # decisions during PHASE 3 assembly.
    points = {s.get("decision_point") for s in snap["steps"]}
    assert points, "visibility snapshot must contain decision points"
    # And the ContextVar slot is clean again after PHASE 4.7 reset.
    assert active_trace() is None

