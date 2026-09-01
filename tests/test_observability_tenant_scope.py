from __future__ import annotations

import hashlib
import json
import queue
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.execution.suckers import SkillRegistry  # noqa: E402
from runtime.memory.journal import (  # noqa: E402
    BudgetEvent,
    InMemoryJournal,
    journal_context,
)
from runtime.platform.models import ArmId, CostEntry, TaskId  # noqa: E402
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.safety.auth.scope import TenantScope  # noqa: E402
from runtime.sensing.gateway._observability_auth import (  # noqa: E402
    _scoped_observability_journal,
)
from runtime.sensing.gateway._observability_progress_stream import (  # noqa: E402
    _replay_events_after,
    _sse_event_frame,
)
from runtime.sensing.gateway.observability_router import (  # noqa: E402
    create_observability_router,
)
from runtime.sensing.gateway.streaming_journal import StreamingJournal  # noqa: E402


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _identity_store() -> IdentityStore:
    store = IdentityStore()
    store.add(
        Identity(
            actor_id="alice",
            roles=("operator",),
            metadata={"tenant_id": "tenant-a"},
        ),
        api_key_plaintext="alice-token",
    )
    store.add(
        Identity(
            actor_id="bob",
            roles=("operator",),
            metadata={"tenant_id": "tenant-b"},
        ),
        api_key_plaintext="bob-token",
    )
    store.add(
        Identity(
            actor_id="admin-no-scope",
            roles=("admin",),
            metadata={"tenant_id": "tenant-admin"},
        ),
        api_key_plaintext="admin-no-scope-token",
    )
    store.add(
        Identity(
            actor_id="global-admin",
            roles=("admin",),
            metadata={
                "tenant_id": "tenant-admin",
                "scopes": ["global:admin"],
            },
        ),
        api_key_plaintext="global-admin-token",
    )
    return store


def _client(journal: StreamingJournal, *, require_auth: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=journal,
            registry=SkillRegistry(),
            identity_store=_identity_store() if require_auth else None,
            require_auth=require_auth,
        )
    )
    return TestClient(app)


def _write_task(
    journal: StreamingJournal,
    *,
    tenant_id: str,
    actor_id: str,
    strategy: str,
) -> TaskId:
    task_id = TaskId(uuid4())
    with journal_context(tenant_id=tenant_id, owner_actor_id=actor_id):
        journal.write_task_started(
            task_id,
            arm_id=ArmId("tenant-test"),
            actor=actor_id,
            total_nodes=1,
            strategy=strategy,
            task_type="tenant-isolation",
        )
        journal.write(
            BudgetEvent(
                task_id=task_id,
                arm_id=ArmId("tenant-test"),
                actor=actor_id,
                event_type="budget_commit",
                reason="model_actual",
                cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
            )
        )
    return task_id


def test_journal_progress_timeline_and_budget_are_tenant_scoped() -> None:
    journal = StreamingJournal(InMemoryJournal())
    alice_task = _write_task(
        journal,
        tenant_id="tenant-a",
        actor_id="alice",
        strategy="alice-only",
    )
    bob_task = _write_task(
        journal,
        tenant_id="tenant-b",
        actor_id="bob",
        strategy="bob-only",
    )
    # Legacy rows are not silently attached to an authenticated tenant.
    journal.write_task_started(TaskId(uuid4()), strategy="legacy-global")
    client = _client(journal)

    alice_journal = client.get("/api/journal", headers=_headers("alice-token"))
    assert alice_journal.status_code == 200
    assert alice_journal.json()["total"] == 2

    alice_progress = client.get("/api/progress", headers=_headers("alice-token")).json()
    assert [row["task_id"] for row in alice_progress["tasks"]] == [str(alice_task)]
    assert (
        client.get(
            "/api/progress",
            params={"task_id": str(bob_task)},
            headers=_headers("alice-token"),
        ).status_code
        == 404
    )

    alice_timeline = client.get(
        "/api/journal/timeline",
        headers=_headers("alice-token"),
    ).json()
    assert alice_timeline["task_ids"] == [str(alice_task)]
    assert "bob-only" not in json.dumps(alice_timeline)

    alice_budget = client.get(
        "/api/budget/summary",
        headers=_headers("alice-token"),
    ).json()
    assert alice_budget["task_count"] == 1
    assert alice_budget["tasks"][0]["task_id"] == str(alice_task)

    assert (
        client.get(
            "/api/journal?cross_tenant=true",
            headers=_headers("alice-token"),
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/journal?cross_tenant=true",
            headers=_headers("admin-no-scope-token"),
        ).status_code
        == 403
    )
    global_view = client.get(
        "/api/journal?cross_tenant=true",
        headers=_headers("global-admin-token"),
    )
    assert global_view.status_code == 200
    assert global_view.json()["total"] == 5


def test_scoped_view_filters_a_scope_ignoring_duck_backend() -> None:
    inner = InMemoryJournal()
    journal = StreamingJournal(inner)
    _write_task(
        journal,
        tenant_id="tenant-a",
        actor_id="alice",
        strategy="alice-visible",
    )
    _write_task(
        journal,
        tenant_id="tenant-b",
        actor_id="bob",
        strategy="bob-hidden",
    )

    class _ScopeIgnoringDuck:
        def read_all(self, **_kwargs: object):
            return inner.read_all()

        def read_by_type(self, event_type: str, **_kwargs: object):
            return inner.read_by_type(event_type)

        def subscribe(self, callback: object):
            return journal.subscribe(callback)  # type: ignore[arg-type]

    view = _scoped_observability_journal(
        _ScopeIgnoringDuck(),
        TenantScope(tenant_id="tenant-a", actor_id="alice"),
    )
    assert view.read_all()
    assert {event.tenant_id for event in view.read_all()} == {"tenant-a"}
    assert {event.owner_actor_id for event in view.read_by_type("task_started")} == {"alice"}


def test_sse_replay_and_live_subscription_do_not_cross_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = StreamingJournal(InMemoryJournal())
    alice_first = _write_task(
        journal,
        tenant_id="tenant-a",
        actor_id="alice",
        strategy="alice-first",
    )
    _write_task(
        journal,
        tenant_id="tenant-b",
        actor_id="bob",
        strategy="bob-hidden",
    )
    _write_task(
        journal,
        tenant_id="tenant-a",
        actor_id="alice",
        strategy="alice-second",
    )
    alice_scope = TenantScope(tenant_id="tenant-a", actor_id="alice")
    alice_view = _scoped_observability_journal(journal, alice_scope)
    alice_events = alice_view.read_all()
    cursor = next(event for event in alice_events if event.task_id == alice_first)

    replayed = _replay_events_after(alice_view, str(cursor.event_id))
    assert replayed
    assert {event.tenant_id for event in replayed} == {"tenant-a"}
    assert {event.owner_actor_id for event in replayed} == {"alice"}

    live: queue.Queue[object] = queue.Queue()
    unsubscribe = alice_view.subscribe(live.put_nowait)
    try:
        _write_task(
            journal,
            tenant_id="tenant-b",
            actor_id="bob",
            strategy="bob-live-hidden",
        )
        alice_live_task = _write_task(
            journal,
            tenant_id="tenant-a",
            actor_id="alice",
            strategy="alice-live",
        )
    finally:
        unsubscribe()
    live_events: list[object] = []
    while not live.empty():
        live_events.append(live.get_nowait())
    assert live_events
    assert {getattr(event, "tenant_id", None) for event in live_events} == {"tenant-a"}
    assert alice_live_task in {getattr(event, "task_id", None) for event in live_events}

    # Exercise the real HTTP auth/scope boundary with a finite replay generator.
    from runtime.sensing.gateway import _observability_progress_stream as stream_module

    def _finite_frames(
        scoped_journal: object,
        _q: object,
        last_event_id: str | None,
        *,
        event_name: str | None = None,
        event_filter: object = None,
        catch_up: int = 0,
    ):
        del catch_up
        for event in _replay_events_after(
            scoped_journal,
            last_event_id,
            event_filter=event_filter,
        ):
            yield _sse_event_frame(event, event_name=event_name)

    monkeypatch.setattr(stream_module, "_iter_sse_frames", _finite_frames)
    client = _client(journal)
    response = client.get(
        "/api/stream",
        headers={**_headers("alice-token"), "last-event-id": str(cursor.event_id)},
    )
    assert response.status_code == 200
    assert "bob-hidden" not in response.text
    replay_ids = {
        line.removeprefix("id: ") for line in response.text.splitlines() if line.startswith("id: ")
    }
    assert replay_ids
    assert replay_ids.issubset({str(event.event_id) for event in alice_view.read_all()})
    assert (
        client.get(
            "/api/stream?cross_tenant=true",
            headers=_headers("alice-token"),
        ).status_code
        == 403
    )


def test_rollback_cannot_select_another_tenants_event(tmp_path: Path) -> None:
    journal = StreamingJournal(InMemoryJournal())
    target = tmp_path / "bob-state.txt"
    target.write_text("after\n", encoding="utf-8")
    bob_task = TaskId(uuid4())
    with journal_context(tenant_id="tenant-b", owner_actor_id="bob"):
        journal.write_file_op(
            task_id=bob_task,
            actor="bob",
            path=str(target),
            action="write",
            rollback={
                "reversible": True,
                "action": "write",
                "path": str(target),
                "content": "before\n",
                "expected_current_sha256": hashlib.sha256(b"after\n").hexdigest(),
            },
        )
    bob_event = next(event for event in journal.read_by_type("file_op"))
    client = _client(journal)

    hidden = client.post(
        "/api/files/rollback/apply",
        headers=_headers("alice-token"),
        json={"event_id": str(bob_event.event_id), "project_root": str(tmp_path)},
    )
    assert hidden.status_code == 200
    assert hidden.json()["matched_events"] == 0
    assert hidden.json()["applied"] == 0
    assert target.read_text(encoding="utf-8") == "after\n"

    own = client.post(
        "/api/files/rollback/apply",
        headers=_headers("bob-token"),
        json={"event_id": str(bob_event.event_id), "project_root": str(tmp_path)},
    )
    assert own.status_code == 200
    assert own.json()["matched_events"] == 1
    assert own.json()["applied"] == 1
    assert target.read_text(encoding="utf-8") == "before\n"
    audit = journal.read_by_type(
        "file_rollback",
        scope=TenantScope(tenant_id="tenant-b", actor_id="bob"),
    )
    assert len(audit) == 1
    assert audit[0].source_event_ids == [str(bob_event.event_id)]


@pytest.mark.parametrize(
    "path",
    [
        "/api/kg",
        "/api/blackboard",
        "/api/hemolymph/recent",
        "/api/regeneration/summary",
        "/api/evolution/status",
    ],
)
def test_process_global_panels_require_explicit_privileged_cross_tenant(
    path: str,
) -> None:
    journal = StreamingJournal(InMemoryJournal())
    client = _client(journal)

    assert client.get(path, headers=_headers("alice-token")).status_code == 403
    assert (
        client.get(
            path,
            params={"cross_tenant": "true"},
            headers=_headers("admin-no-scope-token"),
        ).status_code
        == 403
    )
    response = client.get(
        path,
        params={"cross_tenant": "true"},
        headers=_headers("global-admin-token"),
    )
    assert response.status_code == 200
    assert response.json()["global_control_plane"] is True

    # Explicit single-user development mode keeps the legacy local dashboard.
    dev = _client(StreamingJournal(InMemoryJournal()), require_auth=False).get(path)
    assert dev.status_code == 200
    assert dev.json()["global_control_plane"] is True

