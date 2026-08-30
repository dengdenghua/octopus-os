"""Tenant ownership and execution isolation for persisted Cron tasks."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.cron_context import (
    CRON_CONTEXT_ENV,
    CronContextError,
    consume_cron_session_from_environment,
    cron_child_environment,
    cron_session_for_job,
)
from runtime.execution.cron_executor import (
    read_run_ledger,
    recover_interrupted_cron_jobs,
    run_due_cron_jobs,
)
from runtime.execution.suckers import cron_skills
from runtime.execution.suckers.cron_skills import (
    _cancel_scheduled_task,
    _list_scheduled_tasks,
    _schedule_task,
)
from runtime.platform.process.session import Session, current_session, session_scope
from runtime.safety.auth.identity import Identity, IdentityStore
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import (
    AUTHORITATIVE_SCOPE_CONTEXT_KEY,
    authoritative_scope_context,
)
from runtime.sensing.gateway.cron_router import create_cron_router

NOW = datetime(2026, 8, 26, 9, 30, 0).astimezone()
TENANT_A = TenantScope("tenant-a", "alice")
TENANT_B = TenantScope("tenant-b", "bob")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scoped_session(scope: TenantScope) -> Session:
    return Session(
        actor=scope.actor_id,
        metadata={
            "tenant_id": scope.tenant_id,
            "owner_actor_id": scope.actor_id,
            AUTHORITATIVE_SCOPE_CONTEXT_KEY: authoritative_scope_context(scope),
        },
    )


def _secured_client(path: Path) -> TestClient:
    identities = IdentityStore()
    identities.add(
        Identity(
            actor_id="alice",
            roles=("operator",),
            metadata={"tenant_id": "tenant-a"},
        ),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(
            actor_id="bob",
            roles=("operator",),
            metadata={"tenant_id": "tenant-b"},
        ),
        api_key_plaintext="sk-bob",
    )
    identities.add(
        Identity(
            actor_id="admin",
            roles=("admin",),
            metadata={"tenant_id": "tenant-admin"},
        ),
        api_key_plaintext="sk-admin",
    )
    app = FastAPI()
    app.include_router(
        create_cron_router(
            path,
            identity_store=identities,
            require_auth=True,
        )
    )
    return TestClient(app)


def _write_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs), encoding="utf-8")


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_router_principal_owns_scope_and_same_name_is_tenant_local(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    client = _secured_client(path)
    payload = {
        "name": "daily-report",
        "command": "echo alice",
        "cron_expression": "0 9 * * *",
        # Presentation input must never choose persistence authority.
        "tenant_id": "tenant-b",
        "owner_actor_id": "bob",
        "creator_actor": "bob",
    }
    created_a = client.post("/api/cron", json=payload, headers=_headers("sk-alice"))
    assert created_a.status_code == 200
    created_b = client.post(
        "/api/cron",
        json={**payload, "command": "echo bob"},
        headers=_headers("sk-bob"),
    )
    assert created_b.status_code == 200

    persisted = _read_jobs(path)
    assert len(persisted) == 2
    by_command = {job["command"]: job for job in persisted}
    assert (by_command["echo alice"]["tenant_id"], by_command["echo alice"]["owner_actor_id"]) == (
        "tenant-a",
        "alice",
    )
    assert (by_command["echo bob"]["tenant_id"], by_command["echo bob"]["owner_actor_id"]) == (
        "tenant-b",
        "bob",
    )

    alice_jobs = client.get("/api/cron", headers=_headers("sk-alice")).json()
    bob_jobs = client.get("/api/cron", headers=_headers("sk-bob")).json()
    assert [job["command"] for job in alice_jobs] == ["echo alice"]
    assert [job["command"] for job in bob_jobs] == ["echo bob"]
    assert all("tenant_id" not in job and "owner_actor_id" not in job for job in alice_jobs)

    deleted = client.delete("/api/cron/daily-report", headers=_headers("sk-alice"))
    assert deleted.status_code == 200
    assert client.get("/api/cron", headers=_headers("sk-alice")).json() == []
    assert [
        job["command"] for job in client.get("/api/cron", headers=_headers("sk-bob")).json()
    ] == ["echo bob"]


def test_router_hides_other_tenant_and_filters_run_history(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "alice-only",
                "command": "echo a",
                "cron_expression": "* * * * *",
                "tenant_id": "tenant-a",
                "owner_actor_id": "alice",
                "creator_actor": "alice",
            },
            {
                "name": "bob-only",
                "command": "echo b",
                "cron_expression": "* * * * *",
                "tenant_id": "tenant-b",
                "owner_actor_id": "bob",
                "creator_actor": "bob",
            },
        ],
    )
    ledger = tmp_path / "cron_runs.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                {
                    "run_id": "a",
                    "name": "alice-only",
                    "tenant_id": "tenant-a",
                    "owner_actor_id": "alice",
                    "creator_actor": "alice",
                    "status": "ok",
                },
                {
                    "run_id": "b",
                    "name": "bob-only",
                    "tenant_id": "tenant-b",
                    "owner_actor_id": "bob",
                    "creator_actor": "bob",
                    "status": "ok",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    client = _secured_client(path)

    assert client.delete("/api/cron/bob-only", headers=_headers("sk-alice")).status_code == 404
    alice_runs = client.get("/api/cron/runs", headers=_headers("sk-alice")).json()
    assert [record["name"] for record in alice_runs["runs"]] == ["alice-only"]
    assert "tenant_id" not in alice_runs["runs"][0]
    assert [
        job["name"] for job in client.get("/api/cron", headers=_headers("sk-admin")).json()
    ] == [
        "alice-only",
        "bob-only",
    ]


def test_legacy_creator_is_readable_only_by_matching_actor(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "old-alice",
                "command": "echo old",
                "cron_expression": "0 1 * * *",
                "creator_actor": "alice",
            },
            {
                "name": "old-local",
                "command": "echo local",
                "cron_expression": "0 2 * * *",
                "creator_actor": "*",
            },
        ],
    )
    secured = _secured_client(path)
    assert [
        job["name"] for job in secured.get("/api/cron", headers=_headers("sk-alice")).json()
    ] == ["old-alice"]
    assert secured.get("/api/cron", headers=_headers("sk-bob")).json() == []

    local_app = FastAPI()
    local_app.include_router(create_cron_router(path, require_auth=False))
    local = TestClient(local_app)
    assert [job["name"] for job in local.get("/api/cron").json()] == ["old-local"]


def test_model_skills_isolate_list_upsert_and_cancel(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "cron_jobs.json"
    monkeypatch.setattr(
        cron_skills,
        "app_paths",
        lambda: SimpleNamespace(cron_jobs_path=path),
    )
    with session_scope(_scoped_session(TENANT_A)):
        assert _schedule_task(
            prompt="alice prompt",
            cron_expression="0 9 * * *",
            name="same-name",
            tenant_id="tenant-b",  # ignored user/tool input
            owner_actor_id="bob",
        )["ok"]
    with session_scope(_scoped_session(TENANT_B)):
        assert _schedule_task(
            prompt="bob prompt",
            cron_expression="0 9 * * *",
            name="same-name",
        )["ok"]

    with session_scope(_scoped_session(TENANT_A)):
        listed_a = _list_scheduled_tasks()
        assert [task["prompt"] for task in listed_a["tasks"]] == ["alice prompt"]
        assert _cancel_scheduled_task(task_id="same-name")["ok"]
    with session_scope(_scoped_session(TENANT_B)):
        listed_b = _list_scheduled_tasks()
        assert [task["prompt"] for task in listed_b["tasks"]] == ["bob prompt"]

    persisted = _read_jobs(path)
    assert len(persisted) == 1
    assert persisted[0]["tenant_id"] == "tenant-b"
    assert persisted[0]["owner_actor_id"] == "bob"


def test_unscoped_skill_only_sees_legacy_unowned(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "cron_jobs.json"
    monkeypatch.setattr(cron_skills, "app_paths", lambda: SimpleNamespace(cron_jobs_path=path))
    _write_jobs(
        path,
        [
            {
                "name": "local",
                "command": "local prompt",
                "prompt": "local prompt",
                "cron_expression": "* * * * *",
                "creator_actor": "agent_self",
            },
            {
                "name": "tenant",
                "command": "tenant prompt",
                "prompt": "tenant prompt",
                "cron_expression": "* * * * *",
                "tenant_id": "tenant-a",
                "owner_actor_id": "alice",
                "creator_actor": "alice",
            },
        ],
    )
    listed = _list_scheduled_tasks()
    assert [task["task_id"] for task in listed["tasks"]] == ["local"]
    assert _cancel_scheduled_task(task_id="tenant")["error_type"] == "not_found"


def test_executor_requires_scope_but_global_scheduler_preserves_session(tmp_path: Path) -> None:
    path = tmp_path / "cron_jobs.json"
    jobs = [
        {
            "name": "legacy-local",
            "command": "local",
            "cron_expression": "* * * * *",
            "creator_actor": "*",
        },
        {
            "name": "alice-job",
            "command": "alice",
            "prompt": "alice",
            "cron_expression": "* * * * *",
            "tenant_id": "tenant-a",
            "owner_actor_id": "alice",
            "creator_actor": "alice",
        },
        {
            "name": "bob-job",
            "command": "bob",
            "prompt": "bob",
            "cron_expression": "* * * * *",
            "tenant_id": "tenant-b",
            "owner_actor_id": "bob",
            "creator_actor": "bob",
        },
    ]

    # No scope handles only the local/unowned compatibility row.
    _write_jobs(path, jobs)
    calls: list[str] = []
    unscoped = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        shell_runner=lambda command, _job: (calls.append(command) or "ok", "done"),
        prompt_runner=lambda prompt, _job: (calls.append(prompt) or "ok", "done"),
    )
    assert unscoped["fired"] == 1
    assert calls == ["local"]

    # An exact request scope can trigger only its own task.
    _write_jobs(path, jobs)
    calls.clear()
    scoped = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        scope=TENANT_A,
        shell_runner=lambda command, _job: (calls.append(command) or "ok", "done"),
        prompt_runner=lambda prompt, _job: (calls.append(prompt) or "ok", "done"),
    )
    assert scoped["fired"] == 1
    assert calls == ["alice"]

    # The trusted background scheduler can scan all tenants, but each runner
    # and delivery callback observes the job's own Session, never a global one.
    _write_jobs(path, jobs)
    observed: list[tuple[str | None, str | None, str | None, str]] = []
    delivered: list[tuple[str | None, str | None, str]] = []

    def prompt_runner(prompt: str, _job: dict[str, Any]) -> tuple[str, str]:
        session = current_session()
        assert session is not None
        observed.append(
            (
                session.actor,
                session.metadata.get("tenant_id"),
                session.metadata.get("owner_actor_id"),
                str(session.thread_id),
            )
        )
        return "ok", prompt

    def deliver(record: dict[str, Any]) -> None:
        session = current_session()
        assert session is not None
        delivered.append(
            (
                session.metadata.get("tenant_id"),
                session.metadata.get("owner_actor_id"),
                record["name"],
            )
        )

    global_result = run_due_cron_jobs(
        cron_path=path,
        now=NOW,
        allow_cross_tenant=True,
        shell_runner=lambda _command, _job: ("ok", "local"),
        prompt_runner=prompt_runner,
        deliver=deliver,
    )
    assert global_result["fired"] == 3
    assert [(row[0], row[1], row[2]) for row in observed] == [
        ("alice", "tenant-a", "alice"),
        ("bob", "tenant-b", "bob"),
    ]
    assert all("alice" not in row[3] and "tenant-a" not in row[3] for row in observed)
    assert delivered == [
        (None, None, "legacy-local"),
        ("tenant-a", "alice", "alice-job"),
        ("tenant-b", "bob", "bob-job"),
    ]
    ledger = read_run_ledger(tmp_path / "cron_runs.jsonl", limit=10)
    by_name = {record["name"]: record for record in ledger}
    assert by_name["alice-job"]["tenant_id"] == "tenant-a"
    assert by_name["bob-job"]["owner_actor_id"] == "bob"
    assert all(len(record["run_id"]) == 32 for record in ledger)


def test_scoped_restart_recovery_requires_global_authority_and_prevents_refire(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cron_jobs.json"
    started = datetime.now().astimezone() - timedelta(minutes=5)
    _write_jobs(
        path,
        [
            {
                "name": "scoped-crash",
                "command": "alice",
                "prompt": "alice",
                "cron_expression": "* * * * *",
                "tenant_id": "tenant-a",
                "owner_actor_id": "alice",
                "creator_actor": "alice",
                "started_at": started.isoformat(),
                "pid": 99999999,
            }
        ],
    )
    local_recovery = recover_interrupted_cron_jobs(path)
    assert local_recovery["interrupted"] == 0
    assert _read_jobs(path)[0]["started_at"]

    global_recovery = recover_interrupted_cron_jobs(path, allow_cross_tenant=True)
    assert global_recovery["interrupted"] == 1
    assert "started_at" not in _read_jobs(path)[0]
    result = run_due_cron_jobs(
        cron_path=path,
        now=datetime.now().astimezone(),
        allow_cross_tenant=True,
        prompt_runner=lambda _prompt, _job: pytest.fail("recovered job double-ran"),
    )
    assert result["fired"] == 0


def test_child_context_is_opaque_consumed_once_and_rejects_corruption() -> None:
    job = {
        "name": "private-job",
        "tenant_id": "tenant-secret",
        "owner_actor_id": "alice-secret",
        "creator_actor": "alice-secret",
    }
    session = cron_session_for_job(job, fired_at=NOW, run_id="a" * 32)
    env = cron_child_environment(session, base={})
    encoded = env[CRON_CONTEXT_ENV]
    assert "tenant-secret" not in encoded
    assert "alice-secret" not in encoded
    assert "tenant-secret" not in str(session.thread_id)
    assert "alice-secret" not in str(session.thread_id)

    child = consume_cron_session_from_environment(env)
    assert child is not None
    assert child.actor == "alice-secret"
    assert child.metadata["tenant_id"] == "tenant-secret"
    assert child.metadata["owner_actor_id"] == "alice-secret"
    assert AUTHORITATIVE_SCOPE_CONTEXT_KEY in child.metadata
    assert CRON_CONTEXT_ENV not in env
    assert consume_cron_session_from_environment(env) is None

    with pytest.raises(CronContextError):
        consume_cron_session_from_environment({CRON_CONTEXT_ENV: "%%%"})


def test_cli_child_binds_consumed_cron_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.cli as cli

    job = {
        "name": "scoped-child",
        "tenant_id": "tenant-a",
        "owner_actor_id": "alice",
        "creator_actor": "alice",
    }
    parent = cron_session_for_job(job, fired_at=NOW, run_id="b" * 32)
    encoded = cron_child_environment(parent, base={})[CRON_CONTEXT_ENV]
    monkeypatch.setenv(CRON_CONTEXT_ENV, encoded)
    captured: list[Session | None] = []

    def fake_dispatch(_argv: list[str] | None = None) -> int:
        captured.append(current_session())
        return 17

    monkeypatch.setattr(cli, "_main", fake_dispatch)
    assert cli.main(["status"]) == 17
    assert len(captured) == 1
    assert captured[0] is not None
    assert captured[0].actor == "alice"
    assert captured[0].metadata["tenant_id"] == "tenant-a"
    assert CRON_CONTEXT_ENV not in os.environ


def test_executor_settlement_preserves_concurrent_tenant_create_and_replacement(
    tmp_path: Path,
) -> None:
    """A running tick must not overwrite API changes with its old snapshot."""

    path = tmp_path / "cron_jobs.json"
    _write_jobs(
        path,
        [
            {
                "name": "running",
                "command": "old command",
                "cron_expression": "* * * * *",
                "tenant_id": "tenant-a",
                "owner_actor_id": "alice",
                "creator_actor": "alice",
            }
        ],
    )
    entered = threading.Event()
    release = threading.Event()
    result: dict[str, Any] = {}

    def slow_runner(_command: str, _job: dict[str, Any]) -> tuple[str, str]:
        entered.set()
        assert release.wait(5)
        return "ok", "old result"

    worker = threading.Thread(
        target=lambda: result.update(
            run_due_cron_jobs(
                cron_path=path,
                now=NOW,
                allow_cross_tenant=True,
                shell_runner=slow_runner,
            )
        ),
        daemon=True,
    )
    worker.start()
    assert entered.wait(3)

    client = _secured_client(path)
    # Replace Alice's running logical task, and concurrently add Bob's task.
    replace = client.post(
        "/api/cron",
        json={
            "name": "running",
            "command": "new command",
            "cron_expression": "0 10 * * *",
        },
        headers=_headers("sk-alice"),
    )
    create_b = client.post(
        "/api/cron",
        json={
            "name": "bob-new",
            "command": "bob command",
            "cron_expression": "0 11 * * *",
        },
        headers=_headers("sk-bob"),
    )
    assert replace.status_code == 200
    assert create_b.status_code == 200
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result["fired"] == 1

    persisted = {job["name"]: job for job in _read_jobs(path)}
    assert set(persisted) == {"running", "bob-new"}
    assert persisted["running"]["command"] == "new command"
    assert persisted["running"]["last_status"] == "created"
    assert persisted["running"].get("last_output") is None
    assert persisted["bob-new"]["tenant_id"] == "tenant-b"
    assert persisted["bob-new"].get("last_run") is None

