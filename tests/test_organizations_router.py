"""Smoke tests for /api/organizations/* REST endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.safety.organization import (  # noqa: E402
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)
from runtime.safety.organization.forge import save_registry  # noqa: E402
from runtime.sensing.gateway.organizations_router import (  # noqa: E402
    create_organizations_router,
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    a = FastAPI()
    a.include_router(create_organizations_router())
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_list_topologies_empty(client: TestClient) -> None:
    """A fresh data_dir auto-seeds the four built-in topologies."""
    r = client.get("/api/organizations/topologies")
    assert r.status_code == 200
    body = r.json()
    # Built-ins are seeded on first boot so multi-agent dispatch works
    # out of the box. The count is the four shipped recipes.
    assert body["count"] == 4
    names = {t["name"] for t in body["topologies"]}
    assert {
        "research_swarm_v1",
        "code_review_team_v1",
        "refactor_pair_v1",
        "debug_team_v1",
    } == names


def test_list_topologies_after_save(client: TestClient, tmp_path: Path) -> None:
    t = TeamTopology(
        name="t",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="g")},
        task_bucket="b",
    )
    save_registry({t.fingerprint: t})
    r = client.get("/api/organizations/topologies")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["topologies"][0]["fingerprint"] == t.fingerprint


def test_get_topology_404(client: TestClient) -> None:
    r = client.get("/api/organizations/topologies/nope")
    assert r.status_code == 404


def test_list_proposals_empty(client: TestClient) -> None:
    r = client.get("/api/organizations/topology-proposals")
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "echo.topology_proposals.merged.v1"
    assert body["count"] == 0
    assert body["persisted_count"] == 0
    assert body["subagent_promotion_count"] == 0
    assert body["proposals"] == []
    assert body["subagent_promotion"]["proposal_count"] == 0


def test_list_proposals_returns_persisted_payload(
    client: TestClient,
    tmp_path: Path,
) -> None:
    payload = {
        "ts": 0,
        "proposals": [
            {
                "kind": "swap_agent",
                "base_topology": "abc",
                "bucket": "b",
                "detail": {"role": "generator", "new_agent": "bob"},
                "confidence": 0.7,
                "rationale": "test",
            },
        ],
    }
    (tmp_path / "data" / "topology_proposals.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    r = client.get("/api/organizations/topology-proposals")
    body = r.json()
    assert body["count"] == 1
    assert body["proposals"][0]["kind"] == "swap_agent"


def test_promote_proposal_invalid_index(client: TestClient) -> None:
    r = client.post("/api/organizations/topology-proposals/99/promote")
    assert r.status_code == 404


def test_promote_proposal_against_real_registry(
    client: TestClient,
    tmp_path: Path,
) -> None:
    base = TeamTopology(
        name="orig",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="alice")},
        task_bucket="b",
    )
    save_registry({base.fingerprint: base})
    proposals = {
        "ts": 0,
        "proposals": [
            {
                "kind": "swap_agent",
                "base_topology": base.fingerprint,
                "bucket": "b",
                "detail": {"role": "generator", "old_agent": "alice", "new_agent": "bob"},
                "confidence": 0.8,
                "rationale": "smoke",
            }
        ],
    }
    (tmp_path / "data" / "topology_proposals.json").write_text(
        json.dumps(proposals),
        encoding="utf-8",
    )
    r = client.post("/api/organizations/topology-proposals/0/promote")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["new_topology"]["agents"]["generator"]["agent_id"] == "bob"


def test_topology_performance_empty(client: TestClient) -> None:
    r = client.get("/api/organizations/topology-performance")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "runs": []}


def test_retire_topology_removes_entry(
    client: TestClient,
    tmp_path: Path,
) -> None:
    t = TeamTopology(
        name="doomed",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="g")},
    )
    save_registry({t.fingerprint: t})
    r = client.post(f"/api/organizations/topologies/{t.fingerprint}/retire")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retired"] == t.fingerprint
    assert body["remaining"] == 0


def test_retire_topology_404(client: TestClient) -> None:
    r = client.post("/api/organizations/topologies/nonexistent/retire")
    assert r.status_code == 404
