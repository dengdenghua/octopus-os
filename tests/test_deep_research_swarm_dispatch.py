"""Regression: ``deep-research`` / ``deep-research-swarm`` skill should
dispatch to the real ``research_swarm_v1`` topology, not just hand the
SKILL.md instructions back to the caller.

Without this, the model reads a 7-phase ≥10-sub-agent instruction
document and tries to execute it inside a single ReAct loop — which it
can't finish, so the user gets a hand-written fake report instead.
This test pins the contract that the ``_try_real_research_swarm``
helper exists, validates input, and falls back gracefully when the
multi-agent infra isn't available.
"""

from __future__ import annotations

from typing import Any

from runtime.execution.suckers.market_skills import _try_real_research_swarm


def test_returns_none_on_empty_topic() -> None:
    assert _try_real_research_swarm({}) is None
    assert _try_real_research_swarm({"topic": ""}) is None
    assert _try_real_research_swarm({"topic": "   "}) is None
    assert _try_real_research_swarm({"topic": None}) is None  # type: ignore[arg-type]


def test_accepts_topic_under_alias_keys() -> None:
    """Real callers may pass the topic under any of these names; the
    helper has to resolve it from all of them or it'd silently fall
    back to the legacy instructions handler."""
    # We expect None back when the topology lookup fails for some
    # reason (e.g. no registry mock here), but the helper MUST get
    # past the topic-resolution branch — we can verify by patching
    # registry to None and checking that we don't bail at "no topic".

    # If alias resolution worked, the helper proceeds to load_registry,
    # which we intercept to confirm we got past "no topic".
    proceeded = {"called": False}

    def fake_load_registry():
        proceeded["called"] = True
        return {}  # empty registry → topology lookup fails → return None

    # Patch via the import path used inside the helper.
    import runtime.safety.organization.forge as forge

    monkey_orig = forge.load_registry

    try:
        forge.load_registry = fake_load_registry  # type: ignore[assignment]
        for key in ("topic", "query", "question", "user_request"):
            proceeded["called"] = False
            r = _try_real_research_swarm({key: "智能戒指调研"})
            assert proceeded["called"], f"alias {key} did not reach registry lookup"
            assert r is None, "empty registry should fall back via None"
    finally:
        forge.load_registry = monkey_orig  # type: ignore[assignment]


def test_falls_back_when_topology_missing() -> None:
    """Empty registry → None so caller falls back to legacy
    instructions handler. Pin: never raise on missing topology."""
    import runtime.safety.organization.forge as forge

    orig = forge.load_registry

    def fake_load_registry():
        return {}

    try:
        forge.load_registry = fake_load_registry  # type: ignore[assignment]
        result = _try_real_research_swarm({"topic": "test research"})
        assert result is None
    finally:
        forge.load_registry = orig  # type: ignore[assignment]


def test_returns_structured_result_on_success() -> None:
    """When TeamRunner succeeds, return a structured dict with the
    final report and per-role outputs — NOT the legacy
    instructions-only payload."""
    import runtime.safety.organization.forge as forge

    # Stub topology
    class _StubTopology:
        name = "research_swarm_v1"
        fingerprint = "abc"
        task_bucket = "research"

    class _StubRoleOutput:
        role = "synthesizer"
        agent_id = "general"
        output = "## Final Report\n\nSmart ring market is..."
        error = None
        duration_ms = 1234

    class _StubRunResult:
        topology_name = "research_swarm_v1"
        topology_fingerprint = "abc"
        task_bucket = "research"
        success = True
        final_output = "## Final Report\n\nSmart ring market is..."
        role_outputs = [_StubRoleOutput()]
        iterations = 1
        total_duration_ms = 5000.0
        error = None

    class _StubRunner:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def run(self, topology, task, *, context=None):  # noqa: ANN001
            return _StubRunResult()

    orig_load = forge.load_registry

    import runtime.safety.organization.team_runner as tr

    orig_runner = tr.TeamRunner

    try:
        forge.load_registry = lambda: {"abc": _StubTopology()}  # type: ignore[assignment]
        tr.TeamRunner = _StubRunner  # type: ignore[assignment, misc]
        result = _try_real_research_swarm({"topic": "smart ring market"})
        assert result is not None
        assert result["ok"] is True
        assert result["skill"] == "deep-research-swarm"
        assert result["topic"] == "smart ring market"
        assert "Final Report" in result["report"]
        assert result["instructions_mode"] is False
        assert len(result["roles"]) == 1
        assert result["roles"][0]["role"] == "synthesizer"
    finally:
        forge.load_registry = orig_load  # type: ignore[assignment]
        tr.TeamRunner = orig_runner  # type: ignore[assignment, misc]


def test_returns_error_envelope_on_runner_crash() -> None:
    """When TeamRunner.run raises, return a structured error so the
    model sees a clear failure rather than silently falling back to
    legacy instructions (which would let the bug return)."""
    import runtime.safety.organization.forge as forge
    import runtime.safety.organization.team_runner as tr

    class _StubTopology:
        name = "research_swarm_v1"

    class _BoomRunner:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def run(self, *_a: Any, **_kw: Any):
            raise RuntimeError("boom")

    orig_load = forge.load_registry
    orig_runner = tr.TeamRunner

    try:
        forge.load_registry = lambda: {"abc": _StubTopology()}  # type: ignore[assignment]
        tr.TeamRunner = _BoomRunner  # type: ignore[assignment, misc]
        result = _try_real_research_swarm({"topic": "x"})
        assert result is not None
        assert result["ok"] is False
        assert "boom" in result["error"]
        assert "fallback_hint" in result
    finally:
        forge.load_registry = orig_load  # type: ignore[assignment]
        tr.TeamRunner = orig_runner  # type: ignore[assignment, misc]
