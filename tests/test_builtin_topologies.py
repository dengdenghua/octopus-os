"""Tests for the built-in topology seeding."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.safety.organization.builtin_topologies import (
    BUILTIN_TOPOLOGIES,
    seed_builtin_topologies,
)
from runtime.safety.organization.forge import load_registry, save_registry
from runtime.safety.organization.topology import (
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)


def test_builtin_topologies_has_exactly_four() -> None:
    assert len(BUILTIN_TOPOLOGIES) == 4


def test_builtin_topology_invariants() -> None:
    """Each built-in has unique name, valid roles, non-empty system_prompt."""
    seen_names: set[str] = set()
    seen_fingerprints: set[str] = set()
    for topology in BUILTIN_TOPOLOGIES:
        # Unique name across the four.
        assert topology.name not in seen_names, f"duplicate name: {topology.name}"
        seen_names.add(topology.name)
        # Unique fingerprint.
        assert topology.fingerprint not in seen_fingerprints
        seen_fingerprints.add(topology.fingerprint)
        # At least one agent.
        assert len(topology.agents) >= 1
        # Every role is from the canonical Role enum, every spec has an
        # agent_id, and every spec carries a non-empty system_addendum
        # so the model knows what role it's playing.
        for role, spec in topology.agents.items():
            assert isinstance(role, Role)
            assert spec.agent_id, f"{topology.name}: empty agent_id"
            assert spec.system_addendum, f"{topology.name}/{role}: empty system prompt"
            assert len(spec.system_addendum) >= 50, (
                f"{topology.name}/{role}: system_addendum too short "
                f"({len(spec.system_addendum)} chars)"
            )


def test_research_swarm_synthesizer_requires_full_report_structure() -> None:
    research = next(t for t in BUILTIN_TOPOLOGIES if t.name == "research_swarm_v1")
    synth = research.agents[Role.SYNTHESIZER].system_addendum or ""
    assert "90天行动计划" in synth
    assert "5 条 bullet" in synth
    assert "完整最终报告" in synth
    assert "不得压缩成摘要" in synth
    assert "正文第一行必须原样输出" in synth


def test_seed_into_empty_dict_adds_all_four() -> None:
    registry: dict[str, TeamTopology] = {}
    added = seed_builtin_topologies(registry)
    assert added == 4
    assert len(registry) == 4
    # Every built-in is present by fingerprint.
    for topology in BUILTIN_TOPOLOGIES:
        assert topology.fingerprint in registry


def test_seed_is_idempotent_on_existing_fingerprint() -> None:
    """Pre-seeding one built-in means seed_builtin_topologies adds only 3."""
    first = BUILTIN_TOPOLOGIES[0]
    registry: dict[str, TeamTopology] = {first.fingerprint: first}
    added = seed_builtin_topologies(registry)
    assert added == 3
    assert len(registry) == 4
    # No duplicate of the pre-existing fingerprint.
    assert registry[first.fingerprint] is first


def test_seed_runs_twice_without_duplicating() -> None:
    registry: dict[str, TeamTopology] = {}
    seed_builtin_topologies(registry)
    second_added = seed_builtin_topologies(registry)
    assert second_added == 0
    assert len(registry) == 4


def test_load_registry_seeds_on_fresh_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty data_dir should load with the four built-ins seeded."""
    from runtime.platform import paths as paths_module
    from runtime.safety.organization import forge as forge_module

    fake = paths_module.AppPaths(tmp_path, tmp_path)
    monkeypatch.setattr(forge_module, "app_paths", lambda: fake)

    loaded = load_registry()
    assert len(loaded) >= 4
    builtin_fingerprints = {t.fingerprint for t in BUILTIN_TOPOLOGIES}
    assert builtin_fingerprints.issubset(loaded.keys())

    # The seed should also have been persisted to disk so a subsequent
    # load doesn't re-seed (and so the user can edit the file).
    persisted_path = tmp_path / "topologies.json"
    assert persisted_path.is_file()


def test_topology_round_trips_through_save_and_load(tmp_path: Path) -> None:
    target = tmp_path / "registry.json"
    original = TeamTopology(
        name="round-trip-fixture",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.PLANNER: AgentSpec(
                agent_id="alice",
                system_addendum="planner addendum",
                temperature=0.4,
            ),
            Role.GENERATOR: AgentSpec(agent_id="bob"),
        },
        task_bucket="rt",
        quality_threshold=0.7,
        max_iterations=2,
        metadata={"note": "fixture"},
    )
    save_registry({original.fingerprint: original}, path=target)

    reloaded = load_registry(path=target)
    assert original.fingerprint in reloaded
    same = reloaded[original.fingerprint]
    assert same.fingerprint == original.fingerprint
    assert same.name == original.name
    assert same.protocol == original.protocol
    assert same.task_bucket == original.task_bucket
    assert same.quality_threshold == original.quality_threshold
    assert same.max_iterations == original.max_iterations
    assert same.agents[Role.PLANNER].system_addendum == "planner addendum"
    assert same.agents[Role.PLANNER].temperature == 0.4
    assert same.agents[Role.GENERATOR].agent_id == "bob"


def test_load_registry_does_not_reseed_when_user_topology_present(
    tmp_path: Path,
) -> None:
    """If the user has already added their own topology, do NOT inject
    the four built-ins on top — that would surprise them with extras."""
    target = tmp_path / "registry.json"
    user_topology = TeamTopology(
        name="user-only",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="g")},
    )
    save_registry({user_topology.fingerprint: user_topology}, path=target)

    loaded = load_registry(path=target)
    assert len(loaded) == 1
    assert user_topology.fingerprint in loaded
    builtin_fingerprints = {t.fingerprint for t in BUILTIN_TOPOLOGIES}
    assert not builtin_fingerprints.intersection(loaded.keys())
