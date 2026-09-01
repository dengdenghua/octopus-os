from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.bundled.narrative_studio import (
    API_PREFIX,
    NarrativeStudioPlugin,
)
from runtime.platform.plugins.bundled.narrative_studio.store import NarrativeStore
from runtime.platform.plugins.plugin_base import ModuleContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "narrative_studio"
)


def _load(
    tmp_path: Path,
    *,
    principal: str | None = None,
    echo_source_path: str = "",
    review_quorum: int = 2,
    approval_ratio: float = 0.67,
    context_max_chars: int = 20_000,
    context_max_items: int = 100,
) -> tuple[NarrativeStudioPlugin, TestClient, SkillRegistry]:
    app = FastAPI()
    if principal:

        @app.middleware("http")
        async def authenticated_principal(request: Request, call_next):
            request.state.principal = {"id": principal}
            return await call_next(request)

    registry = SkillRegistry()
    plugin = NarrativeStudioPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name="narrative_studio",
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            skill_registry=registry,
            config={
                "data_dir": str(tmp_path / "narrative"),
                "echo_source_path": echo_source_path,
                "review_quorum": review_quorum,
                "approval_ratio": approval_ratio,
                "context_max_chars": context_max_chars,
                "context_max_items": context_max_items,
            },
        )
    )
    return plugin, TestClient(app), registry


def _project(client: TestClient, project_id: str = "story") -> dict:
    response = client.post(
        f"{API_PREFIX}/projects",
        json={"id": project_id, "title": project_id.title()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chapter(client: TestClient, project_id: str, chapter_id: str, ordinal: int) -> dict:
    response = client.post(
        f"{API_PREFIX}/projects/{project_id}/chapters",
        json={
            "id": chapter_id,
            "branch_id": "main",
            "ordinal": ordinal,
            "title": f"Chapter {ordinal}",
            "summary": f"Summary {ordinal}",
            "body": f"Body {ordinal}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_v1_project_is_atomically_migrated_and_future_fields_survive(tmp_path: Path) -> None:
    project_dir = tmp_path / "data" / "projects" / "legacy"
    project_dir.mkdir(parents=True)
    old = {
        "schema_version": "echo.narrative-studio.project.v1",
        "id": "legacy",
        "title": "Legacy",
        "default_branch_id": "main",
        "future_marker": {"preserve": True},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    (project_dir / "project.json").write_text(json.dumps(old), encoding="utf-8")

    store = NarrativeStore(tmp_path / "data")
    migrated = store.get_project("legacy")
    persisted = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

    assert migrated.schema_version == "echo.narrative-studio.project.v2"
    assert migrated.migrated_from == "echo.narrative-studio.project.v1"
    assert migrated.governance.review_quorum == 2
    assert persisted["schema_version"] == "echo.narrative-studio.project.v2"
    assert persisted["future_marker"] == {"preserve": True}
    assert not list(project_dir.glob("*.tmp"))


def test_structured_world_context_has_every_source_kind_and_hard_limits(
    tmp_path: Path,
) -> None:
    _plugin, client, _registry = _load(tmp_path, context_max_chars=20_000)
    _project(client, "context-story")
    _chapter(client, "context-story", "chapter-1", 1)
    _chapter(client, "context-story", "chapter-2", 2)

    pack = client.post(
        f"{API_PREFIX}/projects/context-story/world-packs",
        json={
            "id": "world",
            "name": "World",
            "summary": "World summary",
            "resources": [
                {
                    "category": "bible",
                    "relative_path": "bible/rules.md",
                    "sha256": "a" * 64,
                    "excerpt": "Ghosts are data, never spirits.",
                }
            ],
        },
    )
    assert pack.status_code == 201
    arc = client.post(
        f"{API_PREFIX}/projects/context-story/story-arcs",
        json={
            "id": "arc-1",
            "branch_id": "main",
            "name": "First arc",
            "summary": "A memory becomes evidence.",
            "beats": ["wake", "discover", "choose"],
        },
    )
    assert arc.status_code == 201
    for entity_id, kind, name in (
        ("lin", "character", "Lin"),
        ("zero", "concept", "Zero"),
    ):
        response = client.post(
            f"{API_PREFIX}/projects/context-story/entities",
            json={
                "id": entity_id,
                "kind": kind,
                "name": name,
                "summary": f"{name} summary",
            },
        )
        assert response.status_code == 201
    relationship = client.post(
        f"{API_PREFIX}/projects/context-story/relationships",
        json={
            "id": "lin-remembers-zero",
            "from_entity_id": "lin",
            "to_entity_id": "zero",
            "kind": "remembers",
        },
    )
    assert relationship.status_code == 201
    fact = client.post(
        f"{API_PREFIX}/projects/context-story/facts",
        json={"id": "fact-1", "subject": "Ghost", "predicate": "is", "object": "data"},
    )
    assert fact.status_code == 201
    foreshadow = client.post(
        f"{API_PREFIX}/projects/context-story/foreshadows",
        json={
            "id": "hand-memory",
            "branch_id": "main",
            "title": "The borrowed hand",
            "setup": "Her hand knows a stranger.",
            "intended_payoff": "Procedural memory identifies its owner.",
            "setup_chapter_id": "chapter-1",
        },
    )
    assert foreshadow.status_code == 201
    state = client.post(
        f"{API_PREFIX}/projects/context-story/state-changes",
        json={
            "id": "state-1",
            "branch_id": "main",
            "chapter_id": "chapter-1",
            "entity_id": "lin",
            "field": "trust",
            "before": 0,
            "after": 1,
        },
    )
    assert state.status_code == 201

    built = client.post(
        f"{API_PREFIX}/projects/context-story/context-packs",
        json={
            "id": "full-context",
            "branch_id": "main",
            "target_chapter_id": "chapter-2",
            "max_chars": 20_000,
            "max_items": 100,
        },
    )
    assert built.status_code == 201, built.text
    context = built.json()
    kinds = {source["kind"] for source in context["sources"]}
    assert {
        "world_resource",
        "fact",
        "entity",
        "relationship",
        "foreshadow",
        "previous_chapter",
        "branch_state",
    }.issubset(kinds)
    assert all(source["ref"] and source["content"] for source in context["sources"])
    assert context["total_chars"] == len(context["content"])
    assert context["estimated_tokens"] == (context["total_chars"] + 3) // 4

    # Reconfigure only the server hard caps; client requests cannot exceed them.
    assert _plugin.store is not None
    _plugin.store.context_max_chars = 256
    _plugin.store.context_max_items = 3
    limited = client.post(
        f"{API_PREFIX}/projects/context-story/context-packs",
        json={
            "id": "limited-context",
            "branch_id": "main",
            "target_chapter_id": "chapter-2",
            "max_chars": 20_000,
            "max_items": 100,
        },
    ).json()
    assert limited["max_chars"] == 256
    assert limited["max_items"] == 3
    assert limited["total_chars"] <= 256
    assert len(limited["sources"]) <= 3
    assert limited["omitted_count"] > 0
    assert limited["truncated"] is True


def test_project_isolation_and_fixed_candidate_pipeline_order(tmp_path: Path) -> None:
    _plugin, client, _registry = _load(tmp_path)
    _project(client, "alpha")
    _project(client, "beta")
    _chapter(client, "alpha", "chapter-a", 1)
    for project_id, entity_id in (("alpha", "entity-a"), ("beta", "entity-b")):
        assert (
            client.post(
                f"{API_PREFIX}/projects/{project_id}/entities",
                json={"id": entity_id, "kind": "character", "name": entity_id},
            ).status_code
            == 201
        )
    crossed = client.post(
        f"{API_PREFIX}/projects/alpha/relationships",
        json={
            "from_entity_id": "entity-a",
            "to_entity_id": "entity-b",
            "kind": "must-not-cross",
        },
    )
    assert crossed.status_code == 404

    created = client.post(
        f"{API_PREFIX}/projects/alpha/pipelines",
        json={"id": "pipeline-a", "branch_id": "main", "chapter_id": "chapter-a"},
    )
    assert created.status_code == 201
    run = created.json()
    assert [stage["id"] for stage in run["stages"]] == [
        "outline",
        "draft",
        "continuity",
        "style",
        "revision",
        "editorial",
    ]
    assert all(stage["status"] == "pending" for stage in run["stages"])
    skipped = client.put(
        f"{API_PREFIX}/projects/alpha/pipelines/pipeline-a/stages/draft",
        json={"output": "draft", "submitted_by": "agent"},
    )
    assert skipped.status_code == 409
    injected_canon = client.put(
        f"{API_PREFIX}/projects/alpha/pipelines/pipeline-a/stages/outline",
        json={
            "output": "outline",
            "submitted_by": "agent",
            "canon_status": "canonical",
        },
    )
    assert injected_canon.status_code == 422

    for index, stage in enumerate(
        ("outline", "draft", "continuity", "style", "revision", "editorial")
    ):
        response = client.put(
            f"{API_PREFIX}/projects/alpha/pipelines/pipeline-a/stages/{stage}",
            json={
                "output": f"candidate {stage}",
                "source_refs": ["chapter:chapter-a@r1"],
                "submitted_by": "agent",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["stages"][index]["canon_status"] == "candidate"
    assert payload["status"] == "complete"
    assert payload["current_stage"] is None


def test_review_governance_deduplicates_votes_and_commits_immutable_snapshot(
    tmp_path: Path,
) -> None:
    _plugin, client, _registry = _load(tmp_path, review_quorum=2, approval_ratio=0.67)
    _project(client, "governed")
    _chapter(client, "governed", "chapter-1", 1)
    review = client.post(
        f"{API_PREFIX}/projects/governed/reviews",
        json={
            "id": "review-1",
            "target_type": "chapter",
            "target_id": "chapter-1",
            "title": "Editorial review",
            "summary": "Ready after human approval.",
            "blocking": True,
            "requested_by": "editor",
        },
    )
    assert review.status_code == 201
    assert review.json()["actor_source"] == "client_asserted"

    alice = client.post(
        f"{API_PREFIX}/projects/governed/reviews/review-1/votes",
        json={"voter_id": "Alice", "decision": "approve"},
    )
    assert alice.status_code == 201
    duplicate = client.post(
        f"{API_PREFIX}/projects/governed/reviews/review-1/votes",
        json={"voter_id": "alice", "decision": "approve"},
    )
    assert duplicate.status_code == 409
    bob = client.post(
        f"{API_PREFIX}/projects/governed/reviews/review-1/votes",
        json={"voter_id": "Bob", "decision": "reject"},
    )
    assert bob.status_code == 201
    detail = client.get(f"{API_PREFIX}/projects/governed/reviews/review-1").json()
    assert detail["quorum_received"] == 2
    assert detail["quorum_required"] == 2
    assert detail["approval_ratio"] == 0.5
    assert detail["blockers"][0]["id"] == "review-1"

    unconfirmed = client.post(
        f"{API_PREFIX}/projects/governed/canon-commits",
        json={
            "review_request_id": "review-1",
            "confirm": False,
            "committed_by": "editor",
        },
    )
    assert unconfirmed.status_code == 400
    ratio_failed = client.post(
        f"{API_PREFIX}/projects/governed/canon-commits",
        json={
            "review_request_id": "review-1",
            "confirm": True,
            "committed_by": "editor",
        },
    )
    assert ratio_failed.status_code == 409

    assert (
        client.put(
            f"{API_PREFIX}/projects/governed/reviews/review-1/votes/{bob.json()['id']}",
            json={"decision": "approve", "rationale": "Fixed"},
        ).status_code
        == 200
    )
    blocked = client.post(
        f"{API_PREFIX}/projects/governed/canon-commits",
        json={
            "review_request_id": "review-1",
            "confirm": True,
            "committed_by": "editor",
        },
    )
    assert blocked.status_code == 409
    assert (
        client.put(
            f"{API_PREFIX}/projects/governed/reviews/review-1",
            json={"status": "resolved", "resolution": "All blocking notes addressed."},
        ).status_code
        == 200
    )
    committed = client.post(
        f"{API_PREFIX}/projects/governed/canon-commits",
        json={
            "review_request_id": "review-1",
            "confirm": True,
            "committed_by": "editor",
            "message": "Canon chapter one",
        },
    )
    assert committed.status_code == 201, committed.text
    commit = committed.json()
    assert commit["snapshot"]["body"] == "Body 1"
    assert len(commit["snapshot_sha256"]) == 64

    assert (
        client.put(
            f"{API_PREFIX}/projects/governed/chapters/chapter-1",
            json={"body": "A later candidate revision"},
        ).status_code
        == 200
    )
    saved_commit = client.get(f"{API_PREFIX}/projects/governed/canon-commits/{commit['id']}").json()
    assert saved_commit["snapshot"]["body"] == "Body 1"
    assert (
        client.put(
            f"{API_PREFIX}/projects/governed/canon-commits/{commit['id']}",
            json={"message": "mutate"},
        ).status_code
        == 405
    )


def test_authenticated_principal_overrides_actor_and_agents_have_no_canon_skill(
    tmp_path: Path,
) -> None:
    _plugin, client, registry = _load(
        tmp_path,
        principal="server-user",
        review_quorum=1,
        approval_ratio=1.0,
    )
    _project(client, "identity")
    _chapter(client, "identity", "chapter-1", 1)
    review = client.post(
        f"{API_PREFIX}/projects/identity/reviews",
        json={
            "id": "review-auth",
            "target_type": "chapter",
            "target_id": "chapter-1",
            "title": "Review",
            "summary": "Looks good",
            "requested_by": "spoofed-client",
        },
    ).json()
    assert review["requested_by"] == "server-user"
    assert review["actor_source"] == "authenticated_principal"
    vote = client.post(
        f"{API_PREFIX}/projects/identity/reviews/review-auth/votes",
        json={"voter_id": "spoofed-voter", "decision": "approve"},
    ).json()
    assert vote["voter_id"] == "server-user"
    assert vote["actor_source"] == "authenticated_principal"
    commit = client.post(
        f"{API_PREFIX}/projects/identity/canon-commits",
        json={
            "review_request_id": "review-auth",
            "confirm": True,
            "committed_by": "spoofed-committer",
        },
    ).json()
    assert commit["committed_by"] == "server-user"
    assert commit["actor_source"] == "authenticated_principal"

    expected = {
        "narrative_studio.build_context",
        "narrative_studio.pipeline_create",
        "narrative_studio.pipeline_stage_submit",
        "narrative_studio.review_candidate",
    }
    names = set(registry.all_names())
    assert expected.issubset(names)
    assert not any("canon" in name or "promote" in name for name in names)
    agent_review = registry.get("narrative_studio.review_candidate").handler(
        project_id="identity",
        target_type="chapter",
        target_id="chapter-1",
        title="Agent opinion",
        summary="Candidate-only critique",
        confirm=True,
        canon_status="canonical",
    )
    assert agent_review["ok"] is True
    assert agent_review["result"]["canon_status"] == "candidate"
    assert agent_review["result"]["actor_source"] == "agent_skill"


def test_echo_override_cannot_scan_outside_configured_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed-echo"
    outside = tmp_path / "private-documents"
    (allowed / "bible").mkdir(parents=True)
    (outside / "bible").mkdir(parents=True)
    (allowed / "bible" / "rules.md").write_text("allowed", encoding="utf-8")
    (outside / "bible" / "secret.md").write_text("secret", encoding="utf-8")
    _plugin, client, _registry = _load(tmp_path, echo_source_path=str(allowed))
    _project(client, "echo-security")

    denied = client.post(
        f"{API_PREFIX}/projects/echo-security/imports/echo",
        json={"source_path": str(outside)},
    )
    assert denied.status_code == 400
    assert "configured source root" in denied.json()["detail"]
    accepted = client.post(
        f"{API_PREFIX}/projects/echo-security/imports/echo",
        json={"source_path": str(allowed)},
    )
    assert accepted.status_code == 200
    assert accepted.json()["imported"] is True

    status = client.get(f"{API_PREFIX}/status").json()
    assert status["version"] == "0.2.0"
    assert "immutable_canon_commits" in status["capabilities"]


def test_native_workbench_alias_routes_share_v2_governance(tmp_path: Path) -> None:
    _plugin, client, _registry = _load(
        tmp_path,
        review_quorum=1,
        approval_ratio=1.0,
    )
    _project(client, "workbench")
    _chapter(client, "workbench", "chapter-1", 1)

    assert client.get(f"{API_PREFIX}/projects/workbench/arcs").status_code == 200
    created_run = client.post(
        f"{API_PREFIX}/projects/workbench/pipeline-runs",
        json={"id": "run-ui", "branch_id": "main", "chapter_id": "chapter-1"},
    )
    assert created_run.status_code == 201
    stage = client.post(
        f"{API_PREFIX}/projects/workbench/pipeline-runs/run-ui/stages/outline/submit",
        json={"output": "UI outline", "submitted_by": "editor", "source_refs": []},
    )
    assert stage.status_code == 200
    assert stage.json()["current_stage"] == "draft"

    review = client.post(
        f"{API_PREFIX}/projects/workbench/review-requests",
        json={
            "id": "review-ui",
            "target_type": "chapter",
            "target_id": "chapter-1",
            "title": "UI review",
            "summary": "Review from the native workbench",
            "requested_by": "editor",
        },
    )
    assert review.status_code == 201
    assert review.json()["quorum_required"] == 1
    voted = client.post(
        f"{API_PREFIX}/projects/workbench/review-requests/review-ui/votes",
        json={"voter_id": "editor", "decision": "approve", "rationale": "ready"},
    )
    assert voted.status_code == 201
    assert voted.json()["quorum_received"] == 1
    assert voted.json()["blockers"] == []
    committed = client.post(
        f"{API_PREFIX}/projects/workbench/review-requests/review-ui/commit",
        json={"actor": "editor", "rationale": "ship it", "confirm": True},
    )
    assert committed.status_code == 201, committed.text
    assert committed.json()["review_request_id"] == "review-ui"
    assert client.get(f"{API_PREFIX}/projects/workbench/review-requests").status_code == 200

