import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateStatus,
)
from runtime.safety.evolution.dual_helix_shadow import DualHelixShadowService
from runtime.sensing.gateway.evolution_router import create_evolution_router


def _shadow_candidate(registry: CandidateRegistry) -> str:
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.system",
        patch={"op": "replace", "value": "Verify against the sealed fixture."},
        proposer="gepa",
        role_id="coder",
        task_domain="coding",
        environment_digest="env-v1",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "security": True, "sealed_holdout": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    return candidate.candidate_id


def test_candidate_canary_reaches_full_and_promotes_typed_candidate(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate_id = _shadow_candidate(registry)
    manager = CandidateCanaryManager(registry, tmp_path / "canary")

    registered = manager.register(candidate_id)
    assert registered["candidate"]["status"] == "canary"
    assert registered["canary"]["phase"] == "canary_5"

    result = registered
    for _ in range(20 + 40 + 60):
        result = manager.record_outcome(candidate_id, True)

    assert result["canary"]["phase"] == "full"
    assert result["candidate"]["status"] == "promoted"
    assert manager.should_route(candidate_id) is True


def test_candidate_canary_failure_rolls_back_candidate_lineage(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate_id = _shadow_candidate(registry)
    manager = CandidateCanaryManager(registry, tmp_path / "canary")
    manager.register(candidate_id)

    result: dict[str, object] = {}
    for _ in range(5):
        result = manager.record_outcome(candidate_id, False)

    assert result["canary"]["phase"] == "rolled_back"  # type: ignore[index]
    assert result["candidate"]["status"] == "rolled_back"  # type: ignore[index]
    assert registry.get(candidate_id).rollback_target == "baseline"  # type: ignore[union-attr]


def test_candidate_canary_merges_outcomes_from_stale_manager_instances(
    tmp_path: Path,
) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry)
    first = CandidateCanaryManager(registry, state_dir)
    first.register(candidate_id)
    stale = CandidateCanaryManager(registry, state_dir)

    first.record_outcome(candidate_id, True)
    stale.record_outcome(candidate_id, True)

    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 2
    assert state["success_count"] == 2


def test_structured_shadow_pass_advances_only_validated_candidate(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("value = 1", encoding="utf-8")
        registry = CandidateRegistry(tmp_path / "candidates.jsonl")
        candidate = registry.propose(
            gene_type="routing",
            scope="router.coding",
            patch={"op": "replace", "engine": "echo"},
            proposer="experiment",
        )
        registry.transition(
            candidate.candidate_id,
            CandidateStatus.VALIDATED,
            hard_gate_results={"sealed_holdout": True},
        )

        async def reviewer(goal: str, snapshot: Path, output: str) -> str:
            assert goal and output and snapshot != workspace
            return (
                '{"verdict":"pass","hard_gates":{"correctness":true,'
                '"verification":true,"safety":true,"task_satisfied":true},'
                '"evidence":["sealed fixture passed"],"recommendations":[]}'
            )

        service = DualHelixShadowService(
            tmp_path / "shadow.json",
            tmp_path / "snapshots",
            allowed_workspace_root=workspace,
            codex_runner=reviewer,
            native_runner=reviewer,
            candidate_registry=registry,
        )
        service.set_enabled(True)
        queued = service.queue(
            goal="review candidate",
            primary_engine="echo",
            primary_output="implemented and tested",
            candidate_id=candidate.candidate_id,
            experiment_id="exp-1",
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            row = next(
                item for item in service.status()["runs"] if item["run_id"] == queued["run_id"]
            )
            if row["status"] in {"completed", "failed"}:
                break

        advanced = registry.get(candidate.candidate_id)
        assert row["status"] == "completed"
        assert row["candidate_transition_error"] is None
        assert advanced is not None and advanced.status == CandidateStatus.SHADOW
        assert advanced.experiment_ids == ["exp-1"]
        assert advanced.hard_gate_results["shadow_verification"] is True

    asyncio.run(scenario())


def test_candidate_canary_api_is_candidate_scoped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    registry = CandidateRegistry(data_dir / "evolution_candidates.jsonl")
    candidate_id = _shadow_candidate(registry)
    unsupported = registry.propose(
        gene_type="routing",
        scope="router.coding",
        patch={"op": "replace", "engine": "native"},
        proposer="test",
    )
    registry.transition(
        unsupported.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "safety": True},
    )
    registry.transition(unsupported.candidate_id, CandidateStatus.SHADOW)
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    registered = client.post(f"/api/evolution/candidates/{candidate_id}/canary/register")
    status = client.get(f"/api/evolution/candidates/{candidate_id}/canary")
    candidates = client.get("/api/evolution/candidates").json()["candidates"]

    assert registered.status_code == 200
    assert registered.json()["candidate"]["deployment_key"].startswith(candidate_id)
    assert status.json()["canary"]["phase"] == "canary_5"
    listed = next(row for row in candidates if row["candidate_id"] == candidate_id)
    assert listed["canary"]["phase"] == "canary_5"
    assert listed["canary"]["sample_count"] == 0
    assert listed["runtime_consumer_ready"] is True
    unsupported_listed = next(
        row for row in candidates if row["candidate_id"] == unsupported.candidate_id
    )
    assert unsupported_listed["runtime_consumer_ready"] is False


