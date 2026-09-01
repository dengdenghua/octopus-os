from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.execution.agents.loader import compose_runtime_soul
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import ParsedIntent
from runtime.platform.process.session import Session, session_scope
from runtime.protocol import TurnStatus
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import CandidateRegistry, CandidateStatus
from runtime.safety.evolution.runtime_deployment import CandidateRuntimeSelector
from runtime.safety.evolution.runtime_outcomes import (
    active_runtime_candidates,
    settle_runtime_candidate_outcomes,
)
from runtime.sensing.gateway.realtime_turn_lifecycle import _close_turn
from runtime.sensing.model_router import MockModelRouter


def _shadow_candidate(
    registry: CandidateRegistry,
    *,
    gene_type: str,
    scope: str,
    patch: dict,
) -> str:
    candidate = registry.propose(
        gene_type=gene_type,
        scope=scope,
        patch=patch,
        proposer="test",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "safety": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    return candidate.candidate_id


def _routing_keys(selector: CandidateRuntimeSelector, candidate_id: str) -> tuple[str, str]:
    selected = ""
    control = ""
    for index in range(10_000):
        key = f"thread-{index}"
        if selector.is_active(candidate_id, routing_key=key):
            selected = selected or key
        else:
            control = control or key
        if selected and control:
            return selected, control
    raise AssertionError("failed to find both deterministic canary cohorts")


def test_prompt_candidate_routes_stickily_then_promotes(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate_id = _shadow_candidate(
        registry,
        gene_type="prompt",
        scope="planner.prompt:recipe-a",
        patch={"op": "replace", "value": "Use the sealed verifier."},
    )
    manager = CandidateCanaryManager(registry, tmp_path / "canary")
    manager.register(candidate_id)
    selector = CandidateRuntimeSelector(registry, tmp_path / "canary")
    selected, control = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="sticky-turn")):
        assert (
            selector.prompt_addendum("planner.prompt:recipe-a", routing_key=selected)[1]
            == "Use the sealed verifier."
        )
    assert selector.prompt_addendum("planner.prompt:recipe-a", routing_key=control) == (None, "")
    assert selector.is_active(candidate_id, routing_key=selected) is True
    assert selector.is_active(candidate_id, routing_key=selected) is True

    for _ in range(20 + 40 + 60):
        manager.record_outcome(candidate_id, True)
    assert selector.is_active(candidate_id, routing_key=control) is True


def test_role_candidate_is_an_in_memory_overlay_not_a_file_mutation(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate_id = _shadow_candidate(
        registry,
        gene_type="role",
        scope="agent.eve.soul",
        patch={"op": "append_lesson", "tag": "verification", "value": "Run tests first."},
    )
    manager = CandidateCanaryManager(registry, tmp_path / "canary")
    manager.register(candidate_id)
    selector = CandidateRuntimeSelector(registry, tmp_path / "canary")
    selected, control = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="role-turn")):
        assert "Run tests first." in selector.apply_role("Base soul", "eve", routing_key=selected)
    assert selector.apply_role("Base soul", "eve", routing_key=control) == "Base soul"


def test_planner_consumes_promoted_prompt_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    skill_registry = SkillRegistry()
    skill_registry.register(
        Skill(
            name="noop",
            trusted_source="skill://test/noop",
            handler=lambda **_: {"ok": True},
        ),
        verify_tests=False,
    )
    router = MockModelRouter(response='{"reasoning":"ok","nodes":[{"skill":"noop","args":{}}]}')
    planner = LLMPlanner(
        router=router,
        registry=skill_registry,
        composer=ContextComposer(registry=skill_registry, journal=InMemoryJournal()),
    )
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    candidate_id = _shadow_candidate(
        registry,
        gene_type="prompt",
        scope=f"planner.prompt:{planner.recipe_hash()}",
        patch={"op": "replace", "value": "CANDIDATE_PROMPT_SENTINEL"},
    )
    manager = CandidateCanaryManager(registry, data_dir / "candidate_canary_states")
    manager.register(candidate_id)
    for _ in range(20 + 40 + 60):
        manager.record_outcome(candidate_id, True)

    planner.plan(
        ParsedIntent(
            raw="test",
            intent_type="task",
            normalized_goal="test",
            user_context={"conversation_id": "thread-any"},
        )
    )
    system_text = next(
        message.content for message in router.call_log[-1].messages if message.role == "system"
    )
    assert "CANDIDATE_PROMPT_SENTINEL" in system_text


def test_compose_runtime_soul_consumes_role_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    candidate_id = _shadow_candidate(
        registry,
        gene_type="role",
        scope="agent.eve.soul",
        patch={"op": "append_lesson", "tag": "qa", "value": "ROLE_SENTINEL"},
    )
    manager = CandidateCanaryManager(registry, data_dir / "candidate_canary_states")
    manager.register(candidate_id)
    selector = CandidateRuntimeSelector(registry, data_dir / "candidate_canary_states")
    selected, _ = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="compose-role-turn")):
        resolved = compose_runtime_soul(SimpleNamespace(agent_id="eve", soul="Base soul"))
    assert "ROLE_SENTINEL" in resolved


def test_skill_candidate_is_visible_only_to_its_canary_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    skill_registry = SkillRegistry()
    skill_registry.register(
        Skill(
            name="first",
            trusted_source="skill://test/first",
            handler=lambda **_: {"first": True},
        ),
        verify_tests=False,
    )
    skill_registry.register(
        Skill(
            name="second",
            trusted_source="skill://test/second",
            handler=lambda **_: {"second": True},
        ),
        verify_tests=False,
    )
    candidate_id = _shadow_candidate(
        registry,
        gene_type="skill",
        scope="skill.governed-flow",
        patch={
            "op": "register_forged_skill",
            "name": "governed-flow",
            "description": "Run the verified two-step flow.",
            "underlying_sequence": ["first", "second"],
            "step_templates": [{}, {}],
        },
    )
    manager = CandidateCanaryManager(
        registry,
        data_dir / "candidate_canary_states",
        runtime_registry=skill_registry,
    )
    manager.register(candidate_id)
    selector = CandidateRuntimeSelector(registry, data_dir / "candidate_canary_states")
    selected, control = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected)):
        assert skill_registry.has("governed-flow") is True
    with session_scope(Session(thread_id=control)):
        assert skill_registry.has("governed-flow") is False

    manager.force_rollback(candidate_id, reason="regression")
    with session_scope(Session(thread_id=selected)):
        assert skill_registry.has("governed-flow") is False


def test_canary_rejects_candidate_without_runtime_consumer(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate_id = _shadow_candidate(
        registry,
        gene_type="routing",
        scope="router.coding",
        patch={"op": "replace", "engine": "echo"},
    )
    manager = CandidateCanaryManager(registry, tmp_path / "canary")

    with pytest.raises(ValueError, match="no runtime consumer"):
        manager.register(candidate_id)
    assert registry.get(candidate_id).status == CandidateStatus.SHADOW


def test_prompt_canary_records_one_real_outcome_per_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(
        registry,
        gene_type="prompt",
        scope="planner.prompt:recipe-a",
        patch={"op": "replace", "value": "Use the sealed verifier."},
    )
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    selector = CandidateRuntimeSelector(registry, state_dir)
    selected, _ = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="turn-one")):
        assert selector.prompt_addendum("planner.prompt:recipe-a", routing_key=selected)[0]
        assert selector.prompt_addendum("planner.prompt:recipe-a", routing_key=selected)[0]

    assert active_runtime_candidates("turn-one") == (candidate_id,)
    results = settle_runtime_candidate_outcomes(
        "turn-one",
        success=True,
        registry=registry,
        state_dir=state_dir,
    )
    assert results == [
        {
            "candidate_id": candidate_id,
            "recorded": True,
            "status": "canary",
            "phase": "canary_5",
        }
    ]
    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 1
    assert (
        settle_runtime_candidate_outcomes(
            "turn-one",
            success=True,
            registry=registry,
            state_dir=state_dir,
        )
        == []
    )


def test_ungradable_turn_does_not_poison_canary_rate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(
        registry,
        gene_type="role",
        scope="agent.eve.soul",
        patch={"op": "append_lesson", "value": "Verify first."},
    )
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    selector = CandidateRuntimeSelector(registry, state_dir)
    selected, _ = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="cancelled-turn")):
        assert "Verify first." in selector.apply_role("Base soul", "eve", routing_key=selected)

    result = settle_runtime_candidate_outcomes(
        "cancelled-turn",
        success=None,
        registry=registry,
        state_dir=state_dir,
    )
    assert result[0]["reason"] == "ungradable_turn"
    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 0


def test_governed_skill_records_activation_only_when_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    state_dir = data_dir / "candidate_canary_states"
    skill_registry = SkillRegistry()
    skill_registry.register(
        Skill(
            name="first",
            trusted_source="skill://test/first",
            handler=lambda **_: {"first": True},
        ),
        verify_tests=False,
    )
    candidate_id = _shadow_candidate(
        registry,
        gene_type="skill",
        scope="skill.governed-flow",
        patch={
            "op": "register_forged_skill",
            "name": "governed-flow",
            "underlying_sequence": ["first"],
        },
    )
    CandidateCanaryManager(
        registry,
        state_dir,
        runtime_registry=skill_registry,
    ).register(candidate_id)
    selector = CandidateRuntimeSelector(registry, state_dir)
    selected, _ = _routing_keys(selector, candidate_id)

    with session_scope(Session(thread_id=selected, turn_id="")):
        assert skill_registry.has("governed-flow") is True
        with pytest.raises(RuntimeError, match="activation could not be persisted"):
            skill_registry.get("governed-flow").handler()

    with session_scope(Session(thread_id=selected, turn_id="skill-turn")):
        assert skill_registry.has("governed-flow") is True
        assert active_runtime_candidates("skill-turn") == ()
        result = skill_registry.get("governed-flow").handler()
        assert result["success"] is True

    assert active_runtime_candidates("skill-turn") == (candidate_id,)


def test_realtime_close_turn_settles_candidate_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    state_dir = data_dir / "candidate_canary_states"
    candidate_id = _shadow_candidate(
        registry,
        gene_type="prompt",
        scope="planner.prompt:recipe-a",
        patch={"op": "replace", "value": "Verify first."},
    )
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    selector = CandidateRuntimeSelector(registry, state_dir)
    selected, _ = _routing_keys(selector, candidate_id)
    with session_scope(Session(thread_id=selected, turn_id="realtime-turn")):
        selector.prompt_addendum("planner.prompt:recipe-a", routing_key=selected)

    completed: list[tuple] = []
    log = SimpleNamespace(turn_completed=lambda *args, **kwargs: completed.append(args))
    turn = SimpleNamespace(
        id="realtime-turn",
        items=[],
        status=TurnStatus.COMPLETED,
    )
    _close_turn(log, "thread-a", turn)

    assert completed
    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 1

    with session_scope(Session(thread_id=selected, turn_id="blocked-turn")):
        selector.prompt_addendum("planner.prompt:recipe-a", routing_key=selected)
    blocked = SimpleNamespace(
        id="blocked-turn",
        items=[],
        status=TurnStatus.FAILED,
        error={"disposition": "blocked_on_user"},
    )
    _close_turn(log, "thread-a", blocked)

    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 1

