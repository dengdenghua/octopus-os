from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.execution.suckers import Skill, SkillRegistry  # noqa: E402
from runtime.memory.journal import InMemoryJournal, journal_context  # noqa: E402
from runtime.platform.models import (  # noqa: E402
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path  # noqa: E402
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager  # noqa: E402
from runtime.safety.evolution.candidate_registry import (  # noqa: E402
    CandidateRegistry,
    CandidateStatus,
)
from runtime.sensing.gateway.evolution_ops_router import (  # noqa: E402
    create_evolution_ops_router,
)
from runtime.sensing.gateway.evolution_router import create_evolution_router  # noqa: E402

TENANT_A = TenantScope("tenant-a", "ops-a")
TENANT_B = TenantScope("tenant-b", "ops-b")


def _identities() -> IdentityStore:
    identities = IdentityStore()
    identities.add(
        Identity(
            actor_id="ops-a",
            roles=("operator",),
            metadata={"tenant_id": "tenant-a"},
        ),
        api_key_plaintext="sk-ops-a",
    )
    identities.add(
        Identity(
            actor_id="ops-b",
            roles=("operator",),
            metadata={"tenant_id": "tenant-b"},
        ),
        api_key_plaintext="sk-ops-b",
    )
    identities.add(
        Identity(
            actor_id="global-admin",
            roles=("admin",),
            metadata={
                "tenant_id": "admin-tenant",
                "scopes": ["evolution:cross_tenant"],
            },
        ),
        api_key_plaintext="sk-global-admin",
    )
    identities.add(
        Identity(
            actor_id="plain-admin",
            roles=("admin",),
            metadata={"tenant_id": "admin-tenant"},
        ),
        api_key_plaintext="sk-plain-admin",
    )
    return identities


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _shadow_candidate(base_path: Path, scope: TenantScope) -> str:
    registry = CandidateRegistry(
        tenant_scoped_path(base_path, scope),
        tenant_scope=scope,
    )
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.system",
        patch={"op": "replace", "value": "Verify the tenant fixture."},
        proposer="tenant-test",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "security": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    return candidate.candidate_id


def test_candidate_api_is_partitioned_and_cross_tenant_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    base_path = data_dir / "evolution_candidates.jsonl"
    candidate_a = _shadow_candidate(base_path, TENANT_A)
    candidate_b = _shadow_candidate(base_path, TENANT_B)

    # Ownership participates in the durable identity domain, so identical
    # patches in separate tenant partitions cannot alias one lifecycle.
    assert candidate_a != candidate_b
    persisted_a = CandidateRegistry(tenant_scoped_path(base_path, TENANT_A)).get(candidate_a)
    assert persisted_a is not None
    assert persisted_a.tenant_id == "tenant-a"
    assert persisted_a.owner_actor_id == "ops-a"

    app = FastAPI()
    app.include_router(
        create_evolution_router(
            identity_store=_identities(),
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/evolution/candidates").status_code == 401

    rows_a = client.get(
        "/api/evolution/candidates",
        headers=_headers("sk-ops-a"),
    ).json()["candidates"]
    rows_b = client.get(
        "/api/evolution/candidates",
        headers=_headers("sk-ops-b"),
    ).json()["candidates"]
    assert [row["candidate_id"] for row in rows_a] == [candidate_a]
    assert [row["candidate_id"] for row in rows_b] == [candidate_b]
    assert all(row["tenant_id"] == "tenant-a" for row in rows_a)
    assert all(row["tenant_id"] == "tenant-b" for row in rows_b)

    hidden = client.get(
        f"/api/evolution/candidates/{candidate_b}/canary",
        headers=_headers("sk-ops-a"),
    )
    assert hidden.status_code == 404
    assert (
        client.get(
            "/api/evolution/candidates?cross_tenant=true",
            headers=_headers("sk-ops-a"),
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/evolution/candidates?cross_tenant=true",
            headers=_headers("sk-plain-admin"),
        ).status_code
        == 403
    )

    global_rows = client.get(
        "/api/evolution/candidates?cross_tenant=true",
        headers=_headers("sk-global-admin"),
    ).json()["candidates"]
    assert {row["candidate_id"] for row in global_rows} == {candidate_a, candidate_b}

    registered = client.post(
        f"/api/evolution/candidates/{candidate_a}/canary/register",
        headers=_headers("sk-ops-a"),
    )
    assert registered.status_code == 200
    assert registered.json()["canary"]["phase"] == "canary_5"
    assert registered.json()["canary"]["metadata"]["runtime_materialized"] is False

    outcome = client.post(
        f"/api/evolution/candidates/{candidate_a}/canary/outcome",
        headers=_headers("sk-ops-a"),
        json={"success": True},
    )
    assert outcome.status_code == 200
    assert outcome.json()["canary"]["sample_count"] == 1

    rolled_back = client.post(
        f"/api/evolution/candidates/{candidate_a}/rollback",
        headers=_headers("sk-ops-a"),
        json={"reason": "tenant operator rollback"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["candidate"]["status"] == "rolled_back"

    tenant_b_status = client.get(
        f"/api/evolution/candidates/{candidate_b}/canary",
        headers=_headers("sk-ops-b"),
    ).json()
    assert tenant_b_status["candidate"]["status"] == "shadow"
    assert tenant_b_status["canary"] is None


def test_control_plane_only_tenant_canary_cannot_claim_runtime_promotion(
    tmp_path: Path,
) -> None:
    registry = CandidateRegistry(
        tenant_scoped_path(tmp_path / "candidates.jsonl", TENANT_A),
        tenant_scope=TENANT_A,
    )
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.system",
        patch={"op": "replace", "value": "Tenant-only prompt."},
        proposer="tenant-test",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    manager = CandidateCanaryManager(
        registry,
        tmp_path / "tenant-canary",
        materialize_runtime=False,
    )
    manager.register(candidate.candidate_id)

    result: dict[str, object] = {}
    for _ in range(20 + 40 + 60):
        result = manager.record_outcome(candidate.candidate_id, True)

    assert result["canary"]["phase"] == "full"  # type: ignore[index]
    assert result["candidate"]["status"] == "canary"  # type: ignore[index]
    assert registry.get(candidate.candidate_id).metadata["promotion_blocked"] == (  # type: ignore[union-attr]
        "tenant_runtime_registry_not_partitioned"
    )
    assert manager.should_route(candidate.candidate_id) is False


def _write_trajectory(
    journal: InMemoryJournal,
    *,
    scope: TenantScope,
    tool_name: str,
    success: bool,
    output: str,
    task_id: TaskId | None = None,
) -> TaskId:
    resolved_task_id = task_id or TaskId(uuid4())
    call = ToolCall(caller="tenant-test", sucker_id=tool_name, args={"query": output})
    result = ExecutionResult(
        call_id=call.call_id,
        status="success" if success else "failed",
        output=output,
        error_type=None if success else "HttpContractError",
    )
    with journal_context(tenant_id=scope.tenant_id, owner_actor_id=scope.actor_id):
        journal.write_token_usage(
            str(resolved_task_id),
            iteration=1,
            input_tokens=10,
            output_tokens=5,
            model=f"model-{scope.tenant_id}",
        )
        journal.write_trajectory(
            Trajectory(
                task_id=resolved_task_id,
                arm_id=ArmId("tenant-test"),
                strategy_id="react_loop",
                steps=[
                    Step(
                        step_id=0,
                        node_id="n0",
                        action=call,
                        result=result,
                    )
                ],
                outcome=TrajectoryOutcome(success=success),
            )
        )
    return resolved_task_id


def test_dashboard_and_intel_projections_are_authenticated_and_tenant_scoped() -> None:
    journal = InMemoryJournal()
    _write_trajectory(
        journal,
        scope=TENANT_A,
        tool_name="tenant_a_tool",
        success=False,
        output="HTTP 404 /api/tenant-a endpoint missing capability github",
    )
    _write_trajectory(
        journal,
        scope=TENANT_B,
        tool_name="tenant_b_tool",
        success=False,
        output="HTTP 404 /api/tenant-b endpoint missing capability postgres",
    )

    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(
            journal=journal,
            registry=SkillRegistry(),
            identity_store=_identities(),
            require_auth=True,
        )
    )
    client = TestClient(app)

    protected_paths = (
        "/api/evolution/overview",
        "/api/evolution/story",
        "/api/evolution/skills/history",
        "/api/evolution/skills/performance",
        "/api/intel-evolution/models/proposals",
        "/api/intel-evolution/mcp/proposals",
        "/api/intel-evolution/protocols/drift",
    )
    assert all(client.get(path).status_code == 401 for path in protected_paths)

    headers_a = _headers("sk-ops-a")
    history_a = client.get("/api/evolution/skills/history", headers=headers_a).json()
    performance_a = client.get("/api/evolution/skills/performance", headers=headers_a).json()
    story_a = client.get("/api/evolution/story", headers=headers_a).json()
    models_a = client.get("/api/intel-evolution/models/proposals", headers=headers_a).json()
    mcp_a = client.get("/api/intel-evolution/mcp/proposals", headers=headers_a).json()
    drift_a = client.get("/api/intel-evolution/protocols/drift", headers=headers_a).json()

    assert {row["skill_name"] for row in history_a} == {"tenant_a_tool"}
    assert {row["name"] for row in performance_a} == {"tenant_a_tool"}
    assert story_a["observed_task_count"] == 1
    assert story_a["observations"][0]["tools"] == ["tenant_a_tool"]
    assert [row["model_label"] for row in models_a] == ["model-tenant-a"]
    assert {row["server_name"] for row in mcp_a} == {"github"}
    assert drift_a
    assert "tenant-b" not in str(drift_a)

    assert (
        client.get(
            "/api/evolution/story?cross_tenant=true",
            headers=headers_a,
        ).status_code
        == 403
    )
    global_story = client.get(
        "/api/evolution/story?cross_tenant=true",
        headers=_headers("sk-global-admin"),
    ).json()
    assert global_story["observed_task_count"] == 2
    assert {row["tools"][0] for row in global_story["observations"]} == {
        "tenant_a_tool",
        "tenant_b_tool",
    }


def test_forge_candidate_uses_same_scoped_control_plane_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    journal = InMemoryJournal()
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="source_value",
            description="Source a value",
            trusted_source="builtin://source_value",
            handler=lambda value="hello": {"content": value},
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="uppercase",
            description="Uppercase content",
            trusted_source="builtin://uppercase",
            handler=lambda content="": {"content": content.upper()},
        ),
        verify_tests=False,
    )

    task_id = TaskId(uuid4())
    for _ in range(3):
        first = ToolCall(
            caller="tenant-test",
            sucker_id="source_value",
            args={"value": "hello"},
        )
        second = ToolCall(
            caller="tenant-test",
            sucker_id="uppercase",
            args={"content": "hello"},
        )
        with journal_context(tenant_id=TENANT_A.tenant_id, owner_actor_id=TENANT_A.actor_id):
            journal.write_trajectory(
                Trajectory(
                    task_id=task_id,
                    arm_id=ArmId("tenant-test"),
                    strategy_id="react_loop",
                    steps=[
                        Step(
                            step_id=0,
                            node_id="n0",
                            action=first,
                            result=ExecutionResult(
                                call_id=first.call_id,
                                status="success",
                                output={"content": "hello"},
                            ),
                            args_template={"value": "hello"},
                        ),
                        Step(
                            step_id=1,
                            node_id="n1",
                            action=second,
                            result=ExecutionResult(
                                call_id=second.call_id,
                                status="success",
                                output={"content": "HELLO"},
                            ),
                            args_template={"content": "{n0.content}"},
                        ),
                    ],
                    outcome=TrajectoryOutcome(success=True),
                )
            )

    identities = _identities()
    app = FastAPI()
    app.include_router(
        create_evolution_ops_router(
            journal=journal,
            registry=registry,
            forged_skill_dir=tmp_path / "forged-skills",
            identity_store=identities,
            require_auth=True,
        )
    )
    app.include_router(
        create_evolution_router(
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    headers_a = _headers("sk-ops-a")

    forged = client.post(
        "/api/evolution/skills/forge-from-task",
        headers=headers_a,
        json={"task_id": str(task_id)},
    )
    assert forged.status_code == 200
    assert forged.json()["status"] == "governed"
    candidate_id = forged.json()["evolution_candidates"][0]["candidate_id"]

    listed = client.get("/api/evolution/candidates", headers=headers_a).json()["candidates"]
    assert [row["candidate_id"] for row in listed] == [candidate_id]
    assert listed[0]["tenant_id"] == "tenant-a"

    scoped_registry = CandidateRegistry(
        tenant_scoped_path(data_dir / "evolution_candidates.jsonl", TENANT_A),
        tenant_scope=TENANT_A,
    )
    assert scoped_registry.get(candidate_id).status == CandidateStatus.VALIDATED  # type: ignore[union-attr]
    # Structured shadow evidence is produced by the separate reviewer service;
    # the control-plane transition below represents that completed gate.
    scoped_registry.transition(candidate_id, CandidateStatus.SHADOW)

    canary = client.post(
        f"/api/evolution/candidates/{candidate_id}/canary/register",
        headers=headers_a,
    )
    assert canary.status_code == 200
    assert canary.json()["candidate"]["status"] == "canary"

    rolled_back = client.post(
        f"/api/evolution/candidates/{candidate_id}/rollback",
        headers=headers_a,
        json={"reason": "closed-loop test"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["candidate"]["status"] == "rolled_back"

