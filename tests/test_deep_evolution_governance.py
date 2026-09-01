from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.memory.learning import deep_evolution
from runtime.memory.learning.turn_scoring import TurnScore
from runtime.safety.auth.scope import TenantScope


def test_legacy_direct_apply_is_disabled_before_router_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_provider_lookup():
        raise AssertionError("direct-apply rejection must not consult a provider")

    monkeypatch.setattr(deep_evolution, "get_provider", _unexpected_provider_lookup)

    result = deep_evolution.deep_evolve(
        agent_id="coder",
        dry_run=False,
        legacy_direct_apply=True,
    )

    assert result == {
        "ok": False,
        "error": "legacy_direct_apply_disabled",
        "reason": "direct self-modification must use the governed candidate pipeline",
        "applied": [],
        "candidates": [],
        "dry_run": False,
    }


def test_deep_evolution_source_has_no_direct_soul_mutation_escape() -> None:
    source = deep_evolution.deep_evolve.__code__

    assert "_update_soul" not in source.co_names
    assert "_revert_soul" not in source.co_names


@pytest.mark.parametrize("entrypoint", ["deep_reflect", "deep_evolve"])
def test_unsafe_agent_id_is_rejected_before_provider_or_filesystem(
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deep_evolution,
        "get_provider",
        lambda: (_ for _ in ()).throw(AssertionError("provider must not be consulted")),
    )

    result = getattr(deep_evolution, entrypoint)(agent_id="../../outside")

    assert result == {"ok": False, "error": "unsafe_agent_id"}


def test_candidate_registration_rejects_unsafe_agent_before_creating_store(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "candidates.jsonl"

    with pytest.raises(ValueError, match="unsafe agent id"):
        deep_evolution._record_deep_evolve_candidate(
            agent_id="../../outside",
            candidate={"kind": "add_lesson", "lesson": "escape"},
            judgment={"verdict": "apply"},
            holdout_passed=True,
            source_failures=[],
            registry_path=registry_path,
        )

    assert not registry_path.exists()


def test_cross_tenant_aggregate_cannot_authorize_deep_evolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deep_evolution,
        "get_provider",
        lambda: (_ for _ in ()).throw(AssertionError("provider must not be consulted")),
    )

    result = deep_evolution.deep_evolve(
        agent_id="coder",
        scope=TenantScope("ops", "admin", allow_cross_tenant=True),
    )

    assert result["ok"] is False
    assert result["error"] == "cross_tenant_evolution_forbidden"


def test_deep_reflect_reads_soul_from_configured_agent_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.agents import loader
    from runtime.memory.learning import turn_scoring

    agents_root = tmp_path / "configured-agents"
    soul = agents_root / "coder" / "agent-core" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("CONFIGURED SOUL SENTINEL", encoding="utf-8")
    monkeypatch.setattr(loader, "default_agents_root", lambda: agents_root)
    monkeypatch.setattr(
        deep_evolution,
        "get_provider",
        lambda: SimpleNamespace(get=lambda _name: object()),
    )
    monkeypatch.setattr(
        turn_scoring,
        "read_recent_scores",
        lambda *_a, **_kw: [
            TurnScore(
                ts="2026-08-26T00:00:00",
                agent_id="coder",
                score=1.0,
                reason="success",
                soul_hash="hash",
            )
        ],
    )
    monkeypatch.setattr(
        turn_scoring,
        "analyze_soul_impact",
        lambda *_a, **_kw: {"verdict": "stable"},
    )
    prompts: list[str] = []

    def _judge(**kwargs):
        prompts.append(kwargs["user"])
        return ({"overall_score": 90}, {"input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(deep_evolution._EVOLVE_CALLER, "call_json", _judge)

    result = deep_evolution.deep_reflect(agent_id="coder")

    assert result["ok"] is True
    assert "CONFIGURED SOUL SENTINEL" in prompts[0]

